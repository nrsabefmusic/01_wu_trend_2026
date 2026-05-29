import os
import csv
import io
import base64
import yaml
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from pathlib import Path
from google import genai
import yfinance as yf
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# ── 載入設定 ──────────────────────────────────────────────
CONFIG_PATH = Path("topic_config.yaml")
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
EMAIL_USER = os.environ["EMAIL_USER"]
EMAIL_PASS = os.environ["EMAIL_PASS"]
RECEIVER_EMAIL = os.environ["RECEIVER_EMAIL"]

TODAY = datetime.now().strftime("%Y-%m-%d")
HISTORY_FILE = Path("history.csv")
REPORT_FILE = Path("report.html")

# ── Gemini 自動偵測可用模型 ────────────────────────────────
def get_workable_model(client):
    import time
    priority = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
    for model in priority:
        try:
            response = client.models.generate_content(model=model, contents="hi")
            if response and response.text:
                print(f"  ↳ 可用模型：{model}")
                return model
        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                print(f"  ↳ {model} 額度不足，5秒後嘗試下一個...")
                time.sleep(5)
                continue
            else:
                print(f"  ↳ {model} 發生錯誤：{e}")
                continue
    return None

client = genai.Client(api_key=GEMINI_API_KEY)
GEMINI_MODEL = get_workable_model(client)


# ── 抓取各指標數據 ─────────────────────────────────────────

def fetch_yfinance(ticker_symbol: str, metric_name: str) -> dict:
    """用 yfinance 抓股價/ETF，回傳標準格式"""
    result = {
        "metric": metric_name,
        "fetched_at": TODAY,
        "content": "",
        "data_date": None,
        "value": None,
        "error": None,
    }
    try:
        ticker = yf.Ticker(ticker_symbol)
        # auto_adjust=False 避免調整後價格造成 NaN；period="1mo" 確保有足夠資料
        hist = ticker.history(period="1mo", auto_adjust=False)
        if hist.empty:
            result["error"] = f"{ticker_symbol} 無歷史數據"
            result["content"] = result["error"]
            return result

        # 取最後一筆有效收盤價
        close_series = hist["Close"].dropna()
        if close_series.empty:
            result["error"] = f"{ticker_symbol} Close 欄位全為 NaN"
            result["content"] = result["error"]
            return result

        latest_close = float(close_series.iloc[-1])
        latest_date = close_series.index[-1].strftime("%Y-%m-%d")
        result["data_date"] = latest_date
        result["value"] = latest_close
        result["content"] = (
            f"商品代號: {ticker_symbol}, "
            f"數據基準日: {latest_date}, "
            f"收盤價: {latest_close:.2f}"
        )
    except Exception as e:
        result["error"] = str(e)
        result["content"] = f"抓取例外錯誤: {str(e)}"
    return result


def fetch_taipei_housing_index() -> dict:
    """
    抓取台北市住宅價格季指數（全市）
    來源：台北市政府地政局 open data CSV，免帳號
    URL：https://data.taipei/api/dataset/954911b5.../download
    格式：宅價格季指數類別, 期別(114Q4), 季指數, 季指數變動率, ...
    """
    metric_name = "台北市住宅價格季指數（全市）"
    result = {
        "metric": metric_name,
        "fetched_at": TODAY,
        "content": "",
        "data_date": None,
        "value": None,
        "error": None,
    }
    url = (
        "https://data.taipei/api/dataset/954911b5-896d-4ae1-9ebe-87c4ba8a191e"
        "/resource/3210976b-f578-483c-8853-3ceec3796877/download"
    )
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        # 解析 CSV，找「全市」最新一筆
        content = resp.content.decode("utf-8-sig")  # 去除 BOM
        reader = csv.DictReader(io.StringIO(content))
        latest_row = None
        for row in reader:
            category = row.get("宅價格季指數類別", "").strip()
            if category == "全市":
                latest_row = row  # CSV 是時間正序，最後一筆就是最新
        if latest_row is None:
            result["error"] = "找不到「全市」資料列"
            result["content"] = result["error"]
            return result

        period = latest_row.get("期別", "").strip()       # e.g. "114Q4"
        index_val = latest_row.get("季指數", "").strip()  # e.g. "126.88"
        change = latest_row.get("季指數變動率", "").strip()
        price_total = latest_row.get("標準住宅總價（新台幣萬元）", "").strip()

        # 期別轉換：114Q4 → 2025Q4 → 約 2025-12-31
        try:
            roc_year = int(period[:3])
            quarter = int(period[4])
            ad_year = roc_year + 1911
            quarter_end_month = quarter * 3
            data_date = f"{ad_year}-{quarter_end_month:02d}-01"
        except Exception:
            data_date = "unknown"

        result["data_date"] = data_date
        result["value"] = float(index_val) if index_val else None
        result["content"] = (
            f"台北市住宅價格季指數（全市）"
            f"期別: {period}（西元{ad_year}年第{quarter}季）, "
            f"季指數: {index_val}, "
            f"季變動率: {change}, "
            f"標準住宅總價: {price_total} 萬元"
        )
    except Exception as e:
        result["error"] = str(e)
        result["content"] = f"抓取例外錯誤: {str(e)}"
    return result


def fetch_all_indicators(config: dict) -> list:
    """依照 topic_config.yaml 的指標清單抓取數據"""
    results = []
    for indicator in config["indicators"]:
        metric = indicator["metric"]
        print(f"  ↳ 正在調取：{metric}")

        if "0050" in metric:
            r = fetch_yfinance("0050.TW", metric)
        elif "2330" in metric or "台積電" in metric:
            r = fetch_yfinance("2330.TW", metric)
        elif "TLT" in metric:
            r = fetch_yfinance("TLT", metric)
        elif "房價" in metric or "住宅" in metric:
            r = fetch_taipei_housing_index()
            r["metric"] = metric  # 保留設定檔中的名稱
        else:
            # 預設走原有的 requests 爬蟲
            url = indicator["data_source"]["url"]
            r = {
                "metric": metric,
                "fetched_at": TODAY,
                "content": f"未知指標類型，無法自動對應抓取方式。URL: {url}",
                "data_date": None,
                "value": None,
                "error": "未知指標類型",
            }
        r["max_delay_days"] = indicator["data_source"].get("max_delay_days", 120)
        results.append(r)
    return results


# ── AI 分析 ──────────────────────────────────────────────
def analyze_with_ai(config: dict, fetch_results: list) -> dict:
    if not GEMINI_MODEL:
        return {
            "indicators": [
                {"id": i+1, "data_date": r.get("data_date") or "unknown",
                 "trend": "Gemini 無可用模型", "verdict": "尚未明朗", "conclusion": ""}
                for i, r in enumerate(fetch_results)
            ],
            "overall_credibility": "尚未明朗",
            "overall_summary": "Gemini API 無可用模型，無法分析。"
        }

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
原始內容摘要：
{r['content']}
---
"""

    prompt += """
請針對每個指標完成以下工作：
1. 從原始內容中判讀這筆數據的「數據日期」（格式 YYYY-MM-DD，若無法判斷填 unknown）
2. 說明目前數據顯示的趨勢（請具體說明數值）
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

    import json
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        text_content = response.text.strip()
        if text_content.startswith("```"):
            text_content = text_content.split("```")[1]
            if text_content.startswith("json"):
                text_content = text_content[4:]
            text_content = text_content.strip()
        return json.loads(text_content)
    except Exception as e:
        return {
            "indicators": [
                {"id": i+1, "data_date": "unknown",
                 "trend": "解析失敗", "verdict": "尚未明朗", "conclusion": ""}
                for i in range(len(indicators))
            ],
            "overall_credibility": "尚未明朗",
            "overall_summary": f"AI 分析失敗：{str(e)}"
        }


# ── 更新歷史紀錄 ──────────────────────────────────────────
def update_history(fetch_results: list, analysis: dict):
    file_exists = HISTORY_FILE.exists()
    fieldnames = ["run_date", "indicator_id", "metric", "data_date",
                  "delay_warning", "verdict", "trend", "conclusion",
                  "overall_credibility", "value"]

    with open(HISTORY_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()

        for i, ind_result in enumerate(analysis.get("indicators", [])):
            r = fetch_results[i] if i < len(fetch_results) else {}
            data_date_str = ind_result.get("data_date", "unknown")
            delay_warning = False
            if data_date_str and data_date_str != "unknown":
                try:
                    data_date = datetime.strptime(data_date_str, "%Y-%m-%d")
                    max_delay = r.get("max_delay_days", 120)
                    if (datetime.now() - data_date).days > max_delay:
                        delay_warning = True
                except Exception:
                    pass

            writer.writerow({
                "run_date": TODAY,
                "indicator_id": ind_result.get("id", i+1),
                "metric": r.get("metric", ""),
                "data_date": data_date_str,
                "delay_warning": "⚠️" if delay_warning else "",
                "verdict": ind_result.get("verdict", ""),
                "trend": ind_result.get("trend", ""),
                "conclusion": ind_result.get("conclusion", ""),
                "overall_credibility": analysis.get("overall_credibility", ""),
                "value": r.get("value", ""),
            })


# ── 產生折線圖（base64 內嵌）────────────────────────────────
def generate_charts_base64(config: dict, history: list) -> dict:
    """
    為每個有數值的指標產生折線圖，回傳 {indicator_id: base64_png_string}
    圖表標題全用英文，避免 GitHub Actions 環境中文字型問題
    """
    indicators = config["indicators"]
    charts = {}

    # 英文縮寫對照
    label_map = {
        "0050": "0050.TW Price",
        "2330": "2330.TW Price",
        "TLT": "TLT Price",
        "房價": "Taipei Housing Index",
        "住宅": "Taipei Housing Index",
    }

    for ind in indicators:
        iid = str(ind["id"])
        metric = ind["metric"]

        # 決定圖表標籤
        chart_label = metric
        for key, label in label_map.items():
            if key in metric:
                chart_label = label
                break

        # 從 history 撈這個指標的數值
        rows = [r for r in history if str(r.get("indicator_id")) == iid
                and r.get("value") and r.get("run_date")]
        if len(rows) < 2:
            continue  # 資料不足，不畫圖

        try:
            dates = [datetime.strptime(r["run_date"], "%Y-%m-%d") for r in rows]
            values = [float(r["value"]) for r in rows]

            fig, ax = plt.subplots(figsize=(8, 3))
            ax.plot(dates, values, marker="o", markersize=3,
                    linewidth=1.5, color="#2563eb")
            ax.set_title(chart_label, fontsize=11, pad=8)
            ax.set_xlabel("Date")
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
            ax.xaxis.set_major_locator(mdates.AutoDateLocator())
            fig.autofmt_xdate()
            ax.grid(True, alpha=0.3)
            plt.tight_layout()

            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
            plt.close(fig)
            buf.seek(0)
            charts[iid] = base64.b64encode(buf.read()).decode("utf-8")
        except Exception as e:
            print(f"  ↳ 指標 {iid} 折線圖產生失敗：{e}")

    return charts


# ── 產生 HTML 報告 ────────────────────────────────────────
def generate_html_report(config: dict, fetch_results: list,
                          analysis: dict, charts: dict) -> str:
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

    # 英文縮寫對照說明（圖表下方補充）
    chart_legend = {
        "0050": "0050.TW Price = 元大台灣50 收盤價（TWD）",
        "2330": "2330.TW Price = 台積電 收盤價（TWD）",
        "TLT": "TLT Price = 長天期美債 ETF 收盤價（USD）",
        "房價": "Taipei Housing Index = 台北市全市住宅價格季指數",
        "住宅": "Taipei Housing Index = 台北市全市住宅價格季指數",
    }

    indicator_rows = ""
    for ind in analysis.get("indicators", []):
        iid = str(ind["id"])
        hist = indicator_histories.get(iid, [])
        metric_name = next((x["metric"] for x in indicators
                            if str(x["id"]) == iid), f"指標 {iid}")
        direction = next((x["direction"] for x in indicators
                          if str(x["id"]) == iid), "")
        prediction = next((x.get("prediction", "") for x in indicators
                           if str(x["id"]) == iid), "")

        # 歷史表格（最新在上）
        rows_html = ""
        for h in reversed(hist[-30:]):
            delay_badge = (f'<span class="badge warning">{h["delay_warning"]}</span>'
                           if h.get("delay_warning") else "")
            verdict_class = {"符合": "good", "不符合": "bad",
                             "尚未明朗": "neutral"}.get(h.get("verdict", ""), "neutral")
            rows_html += f"""
            <tr>
              <td>{h.get('run_date','')}</td>
              <td>{h.get('data_date','')} {delay_badge}</td>
              <td class="credibility {verdict_class}">{h.get('verdict','')}</td>
              <td>{h.get('trend','')}</td>
              <td>{h.get('conclusion','')}</td>
            </tr>"""

        # 折線圖
        chart_html = ""
        if iid in charts:
            legend_text = ""
            for key, text in chart_legend.items():
                if key in metric_name:
                    legend_text = f'<p class="chart-legend">📊 {text}</p>'
                    break
            chart_html = f"""
            <div class="chart-wrap">
              <img src="data:image/png;base64,{charts[iid]}"
                   alt="{metric_name} 趨勢圖" style="max-width:100%;border-radius:4px;">
              {legend_text}
            </div>"""

        indicator_rows += f"""
        <div class="indicator-block">
          <h3>指標 {iid}：{metric_name}</h3>
          <p class="prediction"><strong>原始預測：</strong>{prediction}
            （預期方向：{direction}）</p>
          {chart_html}
          <table>
            <thead>
              <tr>
                <th style="width:12%">驗證日期</th>
                <th style="width:14%">數據實際日期</th>
                <th style="width:12%">驗證結果</th>
                <th style="width:38%">當前趨勢</th>
                <th style="width:24%">結論</th>
              </tr>
            </thead>
            <tbody>{rows_html or '<tr><td colspan="5" style="text-align:center;color:#9ca3af;">尚無歷史紀錄</td></tr>'}</tbody>
          </table>
        </div>"""

    credibility_class = {"高": "good", "中": "neutral", "低": "bad",
                         "尚未明朗": "neutral"}.get(
        analysis.get("overall_credibility", ""), "neutral")

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{task['name']} 驗證報告</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           max-width: 960px; margin: 0 auto; padding: 24px;
           color: #1a1a1a; background: #f8f8f8; }}
    h1 {{ font-size: 1.6rem; border-bottom: 3px solid #2563eb; padding-bottom: 8px; }}
    h2 {{ font-size: 1.2rem; color: #374151; margin-top: 32px; }}
    h3 {{ font-size: 1rem; color: #1d4ed8; margin-bottom: 4px; }}
    .meta {{ background: #fff; border-radius: 8px; padding: 16px; margin: 16px 0;
             border-left: 4px solid #2563eb;
             box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
    .meta p {{ margin: 6px 0; font-size: 0.9rem; color: #4b5563; line-height: 1.5; }}
    .credibility {{ display: inline-block; padding: 4px 12px; border-radius: 99px;
                    font-weight: bold; font-size: 0.9rem; text-align: center; }}
    .good {{ background: #dcfce7; color: #166534; }}
    .bad  {{ background: #fee2e2; color: #991b1b; }}
    .neutral {{ background: #fef9c3; color: #854d0e; }}
    .indicator-block {{ background: #fff; border-radius: 8px; padding: 20px;
                        margin: 20px 0; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
    .prediction {{ font-size: 0.85rem; color: #6b7280; margin-bottom: 12px; }}
    .chart-wrap {{ margin: 12px 0 16px; }}
    .chart-legend {{ font-size: 0.78rem; color: #6b7280; margin: 4px 0 0; }}
    table {{ width: 100%; border-collapse: collapse;
             font-size: 0.85rem; margin-top: 8px; }}
    th {{ background: #f1f5f9; text-align: left; padding: 10px 12px;
          border-bottom: 2px solid #e2e8f0; }}
    td {{ padding: 10px 12px; border-bottom: 1px solid #f1f5f9;
          vertical-align: top; line-height: 1.4; }}
    tr:last-child td {{ border-bottom: none; }}
    .badge.warning {{ background: #fee2e2; color: #991b1b; padding: 2px 6px;
                      border-radius: 4px; font-size: 0.75rem; font-weight: bold; }}
    .updated {{ font-size: 0.8rem; color: #9ca3af;
                text-align: right; margin-top: 24px; }}
  </style>
</head>
<body>
  <div style="background:#f3f4f6;border-radius:6px;padding:10px 16px;
              margin-bottom:16px;font-size:0.78rem;color:#6b7280;line-height:2;">
    <strong style="color:#9ca3af;letter-spacing:0.05em;">任務資訊</strong><br>
    Repo：01_wu_trend_2026　
    資料夾：/（根目錄）　
    腳本：01_verify.py　
    設定檔：topic_config.yaml
  </div>

  <h1>🔍 {task['name']} 趨勢驗證報告</h1>

  <div class="meta">
    <p><strong>原始言論：</strong>{task['source_statement']}</p>
    <p><strong>發言人：</strong>{task.get('source_person','不明')} ／
       <strong>來源：</strong>{task.get('source_platform','不明')} ／
       <strong>發言日期：</strong>{task.get('source_date','不明')}</p>
    <p><strong>追蹤背景：</strong>{task.get('tracking_reason','')}</p>
    <p><strong>追蹤起迄：</strong>{task['created']} ～
       {task.get('tracking_until','indefinite')}</p>
  </div>

  <h2>📊 最新整體進度評估</h2>
  <div style="background:#fff;padding:16px;border-radius:8px;
              box-shadow:0 1px 3px rgba(0,0,0,0.05);">
    <p>言論整體可信度：
      <span class="credibility {credibility_class}">
        {analysis.get('overall_credibility','尚未明朗')}
      </span>
    </p>
    <p style="color:#374151;font-size:0.95rem;line-height:1.5;margin-top:8px;">
      {analysis.get('overall_summary','')}
    </p>
  </div>

  <h2>📈 各分項指標歷史紀錄（最近 30 筆，最新在上）</h2>
  {indicator_rows}

  <p class="updated">最後驗證時間：{TODAY}</p>
</body>
</html>"""

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print("✅ report.html 已更新")
    return html


# ── 寄送 HTML Email ───────────────────────────────────────
def send_email(config: dict, html_content: str, analysis: dict,
               fetch_results: list):
    task = config["task"]
    indicators = config["indicators"]

    # 檢查數據遲延
    delay_warnings = []
    for i, ind in enumerate(analysis.get("indicators", [])):
        data_date_str = ind.get("data_date", "unknown")
        if data_date_str and data_date_str != "unknown":
            try:
                data_date = datetime.strptime(data_date_str, "%Y-%m-%d")
                max_delay = fetch_results[i].get("max_delay_days", 120) \
                            if i < len(fetch_results) else 120
                if (datetime.now() - data_date).days > max_delay:
                    metric = next((x["metric"] for x in indicators
                                   if str(x["id"]) == str(ind["id"])), "")
                    delay_warnings.append(
                        f"⚠️ {metric}：數據日期 {data_date_str}，"
                        f"已超過 {max_delay} 天未更新")
            except Exception:
                pass

    # 在 HTML 最上方插入遲延警示橫幅
    if delay_warnings:
        banner = (
            '<div style="background:#fee2e2;color:#991b1b;padding:12px 16px;'
            'border-radius:8px;margin-bottom:16px;font-size:0.9rem;">'
            + "<br>".join(delay_warnings) + "</div>"
        )
        html_content = html_content.replace("<body>", f"<body>{banner}", 1)

    subject = f"{config['output']['subject_prefix']} - {TODAY}"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_USER
    msg["To"] = RECEIVER_EMAIL

    # 純文字備援（給不支援 HTML 的信箱用）
    plain_body = (
        f"[{task['name']}] 驗證報告 {TODAY}\n\n"
        f"整體可信度：{analysis.get('overall_credibility','尚未明朗')}\n"
        f"{analysis.get('overall_summary','')}\n\n"
        + "\n".join(
            f"【指標 {ind.get('id')}】{ind.get('verdict','')}：{ind.get('conclusion','')}"
            for ind in analysis.get("indicators", [])
        )
    )
    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_USER, EMAIL_PASS)
        server.sendmail(EMAIL_USER, RECEIVER_EMAIL, msg.as_string())
    print("✅ HTML 驗證報告 Email 已成功發送")


# ── 主程式 ────────────────────────────────────────────────
def main():
    print(f"🔍 自動化 OSINT 驗證啟動：{config['task']['name']}")

    print("📡 調取各指標數據...")
    fetch_results = fetch_all_indicators(config)

    print("🤖 Gemini AI 分析中...")
    analysis = analyze_with_ai(config, fetch_results)

    print("📝 更新 history.csv...")
    update_history(fetch_results, analysis)

    print("📊 產生折線圖...")
    history = []
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            history = list(csv.DictReader(f))
    charts = generate_charts_base64(config, history)

    print("🌐 產生 HTML 報告...")
    html_content = generate_html_report(config, fetch_results, analysis, charts)

    print("📧 寄送 HTML Email...")
    send_email(config, html_content, analysis, fetch_results)

    print("✅ 任務完成。")


if __name__ == "__main__":
    main()
