# validator.py
# 腳本自我驗證共用函式庫
# 路徑（桌機版）：C:\Users\st99\OneDrive\For_check_result\Scripts\validator.py
#
# 使用方式：
#   import sys
#   sys.path.append(r"C:\Users\st99\OneDrive\For_check_result\Scripts")
#   from validator import validate_output, build_validation_block, VALIDATOR_VERSION

from datetime import datetime, date, timedelta
from typing import Optional

VALIDATOR_VERSION = "v1.0"


def _parse_date(date_str: str) -> Optional[date]:
    """
    將日期字串轉為 date 物件。
    支援格式：YYYY-MM-DD、YYYY-MM（月度數據，當作該月1號）
    回傳 None 表示無法解析。
    """
    if not date_str or date_str == "unknown":
        return None
    try:
        if len(date_str) == 7:  # YYYY-MM
            return datetime.strptime(date_str + "-01", "%Y-%m-%d").date()
        else:                   # YYYY-MM-DD
            return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return None


def validate_output(
    data_dates: dict,
    today: str,
    max_date_diff_days: int = 3,
    expected_keywords: Optional[list] = None,
    email_content: Optional[str] = None,
) -> dict:
    """
    驗證腳本產出的數據品質。

    參數：
        data_dates        dict，key 為指標名稱，value 為日期字串（YYYY-MM-DD 或 YYYY-MM）或 None（抓取失敗）
        today             今天日期，格式 YYYY-MM-DD
        max_date_diff_days  允許的最大數據落後天數（月度數據建議設 45）
        expected_keywords 預期出現在 email_content 裡的關鍵字清單（選填）
        email_content     email HTML 字串，僅用於關鍵字檢查（選填）

    回傳：
        驗證結果 dict，可直接傳入 build_validation_block()
    """
    today_date = _parse_date(today)
    anomalies = []

    # ── 1. 完整度：統計 None 的指標 ──────────────────────────────────────
    total = len(data_dates)
    missing = [k for k, v in data_dates.items() if v is None]
    missing_count = len(missing)

    if total == 0:
        completeness = "無數據"
        completeness_ok = False
        anomalies.append("data_dates 為空，無任何指標傳入")
    elif missing_count == 0:
        completeness = "100%"
        completeness_ok = True
    else:
        pct = int((total - missing_count) / total * 100)
        completeness = f"{pct}%（缺失：{', '.join(missing)}）"
        completeness_ok = False
        anomalies.append(f"以下指標抓取失敗：{', '.join(missing)}")

    # ── 2. 時效性：比對每個有日期的指標 ──────────────────────────────────
    timeliness_ok = True
    oldest_date = None
    oldest_key = None
    late_items = []

    for key, val in data_dates.items():
        if val is None:
            continue
        d = _parse_date(val)
        if d is None:
            anomalies.append(f"{key} 日期格式無法解析：{val}")
            timeliness_ok = False
            continue
        diff = (today_date - d).days
        if diff > max_date_diff_days:
            late_items.append(f"{key} 落後 {diff} 天（{val}）")
            timeliness_ok = False
        if oldest_date is None or d < oldest_date:
            oldest_date = d
            oldest_key = key

    # 組裝 data_date（最舊的那個）
    if oldest_date is None:
        data_date = "unknown"
    else:
        data_date = oldest_date.strftime("%Y-%m-%d")

    # 組裝 timeliness 說明
    if oldest_date and today_date:
        oldest_diff = (today_date - oldest_date).days
    else:
        oldest_diff = None

    if not data_dates or all(v is None for v in data_dates.values()):
        timeliness = "無可用數據"
        timeliness_ok = False
    elif late_items:
        timeliness = f"異常（{'; '.join(late_items)}）"
        anomalies.extend(late_items)
    else:
        timeliness = f"正常（最舊：{oldest_diff} 天，{oldest_key}）" if oldest_diff is not None else "正常"

    # ── 3. 關鍵字檢查（選填）────────────────────────────────────────────
    if expected_keywords and email_content:
        missing_kw = [kw for kw in expected_keywords if kw not in email_content]
        if missing_kw:
            anomalies.append(f"預期關鍵字未出現：{', '.join(missing_kw)}")

    # ── 4. 組裝回傳結果 ──────────────────────────────────────────────────
    anomaly_str = "；".join(anomalies) if anomalies else "無"

    return {
        "data_date": data_date,
        "timeliness": timeliness,
        "timeliness_ok": timeliness_ok,
        "completeness": completeness,
        "completeness_ok": completeness_ok,
        "anomaly": anomaly_str,
        "validator_version": VALIDATOR_VERSION,
    }


def build_validation_block(v: dict) -> str:
    """
    將 validate_output() 的回傳結果轉成 HTML 驗證結果區塊。
    插入位置：email 最下方，結果內容下方。
    """
    def field(label: str, value: str, ok: bool = True) -> str:
        color = "#888" if ok else "#c0392b"
        return f'  {label}：<span style="color:{color};">{value}</span><br>\n'

    return (
        '<div style="margin:20px 32px 0;padding:14px 18px;background:#f0f4f8;'
        'border-radius:6px;font-size:12px;color:#888;line-height:2;">\n'
        '  🔍 <b style="color:#999;">驗證結果</b><br>\n'
        + field("數據日期", v["data_date"])
        + field("數據時效", v["timeliness"], v["timeliness_ok"])
        + field("數據完整度", v["completeness"], v["completeness_ok"])
        + field("異常說明", v["anomaly"], v["anomaly"] == "無")
        + field("驗證器版本", v["validator_version"])
        + '</div>'
    )
