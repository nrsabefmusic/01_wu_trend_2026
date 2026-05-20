import os
import csv
import yaml
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from pathlib import Path
import google.generativeai as genai

# ── 載入設定 ──────────────────────────────────────────────
# 💡 修正：直接讀取根目錄的設定檔
CONFIG_PATH = Path("topic_config.yaml")
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
EMAIL_USER = os.environ["EMAIL_USER"]
EMAIL_PASS = os.environ["EMAIL_PASS"]
RECEIVER_EMAIL = os.environ["RECEIVER_EMAIL"]

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

TODAY = datetime.now().strftime("%Y-%m-%d")
# 💡 修正：直接生成在根目錄
HISTORY_FILE = Path("history.csv")
REPORT_FILE = Path("report.html")


# ── 抓取數據 ──────────────────────────────────────────────
def fetch_indicator_data(indicator: dict) -> dict:
    url = indicator["data_source"]["url"]
    method = indicator["data_source"]["access_method"]
    notes = indicator["data_source"].get("notes", "")
    max_delay = indicator["data_source"].get("max_delay_days", 30)

    result = {
        "metric": indicator["metric"],
        "url": url,
        "fetched_at": TODAY,
        "max_delay_days": max_delay,
        "content": "",
        "data_date": None,
        "delay_warning": False,
        "error": None,
    }

    try:
        if method == "scrape":
            from bs4 import BeautifulSoup
            resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"})
            soup = BeautifulSoup(resp.text, "html.parser")
            result["content"] = soup.get_text(separator=" ", strip=True)[:2000]

        elif method == "api" or method == "csv":
            resp = requests.get(url, timeout=15)
            result["content"] = resp.text[:2000]

        else:
            result["error"] = f"無法識別的存取方式：{method}"

    except Exception as e:
        result["error"] = str(e)
        result["content"] = f"抓取失敗：{str(e)}\n備註：{notes}"

    return result


# ── AI 分析 ──────────────────────────────────────────────
def analyze_with_ai(config: dict, fetch_results: list) -> dict:
    task = config["task"]
    indicators = config["indicators"]

    prompt = f"""
你是一個客觀的新聞言論驗證助理。

【原始言論】
{task['source_statement']}
發言人：{task.get('source_person', '不明')}
來源平台：{task.get('source_platform', '不明')}
發言日期：{task.get('source_date', '不明')}

【追蹤原因】
{task.get('tracking_reason', '')}

【今天日期】
{TODAY}

【需要驗證的指標與最新數據】
"""

    for i, indicator in enumerate(indicators):
        r = fetch_results[i]
        prompt += f"""
指標 {indicator['id']}：{indicator['metric']}
預測方向：{indicator['direction']}（up=上升 / down=下降 / stable=持平）
數據來源：{r['url']}
抓取時間：{r['fetched_at']}
原始內容摘要：
{r['content']}
---
"""

    prompt += """
請針對每個指標完成以下工作：
1. 從原始內容中判讀這筆數據的「數據日期」（即數據本身是哪一天發布或統計的最新收盤/指數日，格式 YYYY-MM-DD，若無法判斷填 unknown）
2. 說明目前數據顯示的趨勢
3. 判斷是否符合原始預測：符合 / 不符合 / 尚未明朗
4. 一句話結論

最後給出：
- 整體可信度評估（高 / 中 / 低 / 尚未明朗）
- 整體說明（2-3句）

請用以下 JSON 格式回答，不要有任何其他文字：
{
  "indicators": [
    {
      "id": 1,
      "data_date": "YYYY-MM-DD 或 unknown",
      "trend": "趨勢說明",
      "verdict": "符合 / 不符合 / 尚未明朗",
      "conclusion": "一句話結論"
    }
  ],
  "overall_credibility": "高 / 中 / 低 / 尚未明朗",
  "overall_summary": "整體說明"
}
"""

    response = model.generate_content(prompt)

    import json
    try:
        text_content = response.text.strip()
        if text_content.startswith("```json"):
            text_content = text_content.split("```json")[1].split("```")[0].strip()
        return json.loads(text_content)
    except Exception:
        return {
            "indicators": [{"id": i+1, "data_date": "unknown", "trend": "解析失敗", "verdict": "尚未明朗", "conclusion": ""} for i in range(len(indicators))],
            "overall_credibility": "尚未明朗",
            "overall_summary": response.text[:300]
        }


# ── 更新歷史紀錄 ──────────────────────────────────────────
def update_history(fetch_results: list, analysis: dict):
    file_exists = HISTORY_FILE.exists()

    with open(HISTORY_FILE, "a", newline="", encoding="utf-8") as f:
        fieldnames = ["run_date", "indicator_id", "metric", "data_date", "data_url",
                      "delay_warning", "verdict", "trend", "conclusion",
                      "overall_credibility"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)

        if not file_exists:
            writer.writeheader()

        for i, ind_result in enumerate(analysis.get("indicators", [])):
            r = fetch_results[i] if i < len(fetch_results) else {}

            delay_warning = False
            data_date_str = ind_result.get("data_date", "unknown")
            if data_date_str and data_date_str != "unknown":
                try:
                    data_date = datetime.strptime(data_date_str, "%Y-%m-%d")
                    max_delay = fetch_results[i].get("max_delay_days", 30)
                    if (datetime.now() - data_date).days > max_delay:
                        delay_warning = True
                except Exception:
                    pass

            writer.writerow({
                "run_date": TODAY,
                "indicator_id": ind_result.get("id", i+1),
                "metric": r.get("metric", ""),
                "data_date": data_date_str,
                "data_url": r.get("url", ""),
                "delay_warning": "⚠️" if delay_warning else "",
                "verdict": ind_result.get("verdict", ""),
                "trend": ind_result.get("trend", ""),
                "conclusion": ind_result.get("conclusion", ""),
                "overall_credibility": analysis.get("overall_credibility", ""),
            })


# ── 產生 HTML 報告 ────────────────────────────────────────
def generate_html_report(config: dict, fetch_results: list, analysis: dict):
    task = config["task"]
    indicators = config["indicators"]

    history = []
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                history.append(row)

    indicator_histories = {}
    for row in history:
        iid = str(row["indicator_id"])
        if iid not in indicator_histories:
            indicator_histories[iid] = []
        indicator_histories[iid].append(row)

    indicator_rows = ""
    for ind in analysis.get("indicators", []):
        iid = str(ind["id"])
        hist = indicator_histories.get(iid, [])

        rows_html = ""
        for h in reversed(hist):
            delay_badge = f'<span class="badge warning">{h["delay_warning"]}</span>' if h["delay_warning"] else ""
            verdict_class = {"符合": "good", "不符合": "bad", "尚未明朗": "neutral"}.get(h["verdict"], "neutral")
            rows_html += f"""
            <tr>
              <td>{h['run_date']}</td>
              <td>{h['data_date']} {delay_badge}</td>
              <td class="credibility {verdict_class}">{h['verdict']}</td>
              <td>{h['trend']}</td>
              <td>{h['conclusion']}</td>
            </tr>"""

        metric_name = next((x["metric"] for x in indicators if str(x["id"]) == iid), f"指標 {iid}")
        direction = next((x["direction"] for x in indicators if str(x["id"]) == iid), "")
        prediction = next((x["prediction"] for x in indicators if str(x["id"]) == iid), "")

        indicator_rows += f"""
        <div class="indicator-block">
          <h3>指標 {iid}：{metric_name}</h3>
          <p class="prediction"><strong>原始預測：</strong>{prediction} （預期方向：{direction}）</p>
          <table>
            <thead>
              <tr>
                <th style="width: 15%">驗證日期</th>
                <th style="width: 15%">數據實際日期</th>
                <th style="width: 15%">驗證結果</th>
                <th style="width: 35%">當前趨勢</th>
                <th style="width: 20%">結論</th>
              </tr>
            </thead>
            <tbody>{rows_html}</tbody>
          </table>
        </div>"""

    credibility_class = {"高": "good", "中": "neutral", "低": "bad", "尚未明朗": "neutral"}.get(
        analysis.get("overall_credibility", ""), "neutral")

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{task['name']} 驗證報告</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 960px; margin: 0 auto; padding: 24px; color: #1a1a1a; background: #f8f8f8; }}
    h1 {{ font-size: 1.6rem; border-bottom: 3px solid #2563eb; padding-bottom: 8px; }}
    h2 {{ font-size: 1.2rem; color: #374151; margin-top: 32px; }}
    h3 {{ font-size: 1rem; color: #1d4ed8; margin-bottom: 4px; }}
    .meta {{ background: #fff; border-radius: 8px; padding: 16px; margin: 16px 0; border-left: 4px solid #2563eb; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
    .meta p {{ margin: 6px 0; font-size: 0.9rem; color: #4b5563; line-height: 1.5; }}
    .credibility {{ display: inline-block; padding: 4px 12px; border-radius: 99px; font-weight: bold; font-size: 0.9rem; text-align: center; }}
    .good {{ background: #dcfce7; color: #166534; }}
    .bad {{ background: #fee2e2; color: #991b1b; }}
    .neutral {{ background: #fef9c3; color: #854d0e; }}
    .indicator-block {{ background: #fff; border-radius: 8px; padding: 20px; margin: 20px 0; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
    .prediction {{ font-size: 0.85rem; color: #6b7280; margin-bottom: 16px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; margin-top: 8px; }}
    th {{ background: #f1f5f9; text-align: left; padding: 10px 12px; border-bottom: 2px solid #e2e8f0; }}
    td {{ padding: 10px 12px; border-bottom: 1px solid #f1f5f9; vertical-align: top; line-height: 1.4; }}
    tr:last-child td {{ border-bottom: none; }}
    .badge.warning {{ background: #fee2e2; color: #991b1b; padding: 2px 6px; border-radius: 4px; font-size: 0.75rem; font-weight: bold; }}
    .updated {{ font-size: 0.8rem; color: #9ca3af; text-align: right; margin-top: 24px; }}
  </style>
</head>
<body>
  <h1>🔍 {task['name']} 趨勢驗證報告</h1>

  <div class="meta">
    <p><strong>原始言論：</strong>{task['source_statement']}</p>
    <p><strong>發言人：</strong>{task.get('source_person', '不明')} ／ <strong>來源：</strong>{task.get('source_platform', '不明')} ／ <strong>發言日期：</strong>{task.get('source_date', '不明')}</p>
    <p><strong>追蹤背景：</strong>{task.get('tracking_reason', '')}</p>
    <p><strong>追蹤起迄：</strong>{task['created']} ～ {task.get('tracking_until', 'indefinite')}</p>
  </div>

  <h2>📊 最新整體進度評估</h2>
  <div style="background: #fff; padding: 16px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
    <p>名言言論整體可信度：<span class="credibility {credibility_class}">{analysis.get('overall_credibility', '尚未明朗')}</span></p>
    <p style="color: #374151; font-size: 0.95rem; line-height: 1.5; margin-top: 8px;">{analysis.get('overall_summary', '')}</p>
  </div>

  <h2>📈 各分項指標歷史紀錄 (按時間倒序)</h2>
  {indicator_rows}

  <p class="updated">最後驗證時間：{TODAY}</p>
</body>
</html>"""

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ report.html 已更新")


# ── 寄送 Email ────────────────────────────────────────────
def send_email(config: dict, analysis: dict, fetch_results: list):
    task = config["task"]
    indicators = config["indicators"]
    report_url = task.get("report_url", "")

    delay_warnings = []
    for i, ind in enumerate(analysis.get("indicators", [])):
        data_date_str = ind.get("data_date", "unknown")
        if data_date_str and data_date_str != "unknown":
            try:
                data_date = datetime.strptime(data_date_str, "%Y-%m-%d")
                max_delay = fetch_results[i].get("max_delay_days", 30) if i < len(fetch_results) else 30
                if (datetime.now() - data_date).days > max_delay:
                    metric = next((x["metric"] for x in indicators if str(x["id"]) == str(ind["id"])), "")
                    delay_warnings.append(f"⚠️ {metric}：數據日期為 {data_date_str}，已超過 {max_delay} 天未更新。")
            except Exception:
                pass

    body = f"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 追蹤背景說明
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{task.get('tracking_reason', '')}

原始言論：{task['source_statement']}
發言人：{task.get('source_person', '不明')} ({task.get('source_platform', '不明')})

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 最新波段驗證結果（報告日期：{TODAY}）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

    for ind in analysis.get("indicators", []):
        iid = str(ind["id"])
        metric = next((x["metric"] for x in indicators if str(x["id"]) == iid), f"指標 {iid}")
        body += f"""
【{metric}】
- 數據基準日：{ind.get('data_date', 'unknown')}
- 走勢與數據：{ind.get('trend', '')}
- 驗證判定：{ind.get('verdict', '')}
- 結論：{ind.get('conclusion', '')}
"""

    if delay_warnings:
        body += f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ 數據源遲延警示
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        for w in delay_warnings:
            body += f"{w}\n"

    body += f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔎 OSINT 偵探整體評估
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【整體可信度：{analysis.get('overall_credibility', '尚未明朗')}】
{analysis.get('overall_summary', '')}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 線上互動與歷史趨勢看板
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
點擊查看最新累積數據：{report_url}
"""

    subject = f"{config['output']['subject_prefix']} - {TODAY}"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_USER
    msg["To"] = RECEIVER_EMAIL
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_USER, EMAIL_PASS)
        server.sendmail(EMAIL_USER, RECEIVER_EMAIL, msg.as_string())
    print("✅ 驗證簡報 Email 已成功發送")


def main():
    print(f"🔍 自動化 OSINT 驗證啟動：{config['task']['name']}")
    fetch_results = []
    for indicator in config["indicators"]:
        print(f"  ↳ 正在調取開源數據：{indicator['metric']}")
        result = fetch_indicator_data(indicator)
        fetch_results.append(result)

    print("🤖 提交 Gemini 模型進行數據交叉比對與文本語意分析...")
    analysis = analyze_with_ai(config, fetch_results)

    print("📝 更新本地數據歷史庫 (history.csv)...")
    update_history(fetch_results, analysis)

    print("🌐 更新大盤看板與 HTML 趨勢報告...")
    generate_html_report(config, fetch_results, analysis)

    print("📧 派發自動化分析報告至指定信箱...")
    send_email(config, analysis, fetch_results)

    print("✅ 任務執行完畢，等待下次排程。")


if __name__ == "__main__":
    main()
