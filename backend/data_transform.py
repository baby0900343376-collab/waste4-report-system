"""
Module 3：資料清洗與驗證

把 Module 2 (Claude OCR) 判讀出來的原始結果，轉換成可以寫進資料庫、
最終匯出成申報 CSV 的乾淨資料，並決定這筆記錄要不要標記為 need_review。

- 民國轉西元：支援 115.5.30 / 115/05/30 / 115年5月30日 / 已經是 YYYY-MM-DD 等常見格式
- 品牌比對：AI 讀出來的手寫文字，跟官方品牌清單做模糊比對，找不到就落回「其他」，
  並記錄在 review_note 提醒內勤自己核對手寫品牌欄位
- 任何關鍵欄位（聯單編號／回收日期／項目／型態）缺漏，或 AI 信心分數過低，一律 need_review，
  絕不讓一筆资料在沒人看過的情況下直接進入可匯出狀態
"""

import re
from datetime import date

from constants import BRANDS, ITEM_TYPES, STATUS_NEED_REVIEW

_TICKET_NUMBER_PATTERN = re.compile(r"^[A-Za-z0-9]{6,}$")  # 依貴公司實際聯單編號規則調整
CONFIDENCE_THRESHOLD = 0.6

# 回收日期年份統一強制改成這一年，不管AI判讀出什麼年份都會被覆蓋成這個值
# （見下方 build_record_from_ocr 裡的用法）。跨年時記得手動改這個數字。
EXPECTED_RECYCLE_YEAR = 2026


def normalize_list_no(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = re.sub(r"\s+", "", raw).upper()
    return cleaned or None


def parse_date_to_iso(raw: str | None) -> str | None:
    """把民國年或西元年的各種常見寫法轉成 YYYY-MM-DD。無法判斷回傳 None。"""
    if not raw:
        return None
    raw = raw.strip()

    # 已經是 YYYY-MM-DD
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", raw)
    if m:
        y, mo, d = map(int, m.groups())
        return _safe_date(y, mo, d)

    # 民國：115.5.30 / 115/05/30 / 115-5-30
    m = re.match(r"^(\d{2,3})[./-](\d{1,2})[./-](\d{1,2})$", raw)
    if m:
        roc_y, mo, d = map(int, m.groups())
        return _safe_date(roc_y + 1911, mo, d)

    # 民國：115年5月30日
    m = re.match(r"^(\d{2,3})年(\d{1,2})月(\d{1,2})日?$", raw)
    if m:
        roc_y, mo, d = map(int, m.groups())
        return _safe_date(roc_y + 1911, mo, d)

    return None


def _safe_date(y: int, mo: int, d: int) -> str | None:
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def match_brand(item: str | None, brand_raw: str) -> tuple[str | None, bool]:
    """回傳 (matched_brand, is_fuzzy)。matched_brand 為 None 代表完全比對不出來。

    比對全程忽略英文大小寫（AI判讀出來的英文字母大小寫不穩定，例如讀出
    'whirlpool' 但官方清單存的是 'WHIRLPOOL 惠而浦'，這種只有大小寫不同的情況
    要當成完全相符處理，不能因為沒忽略大小寫而漏掉、退回成比對不到）。
    """
    if not item or item not in BRANDS or not brand_raw:
        return None, False

    candidates = BRANDS[item]
    cleaned = brand_raw.strip()
    if not cleaned:
        return None, False
    cleaned_lower = cleaned.lower()

    # 完全相符（整串，忽略大小寫）
    for b in candidates:
        if b.lower() == cleaned_lower:
            return b, False

    # 完全相符（只比對英文代號或只比對中文名稱那一半，忽略大小寫）——
    # 例如AI只讀出 "whirlpool" 或只讀出 "惠而浦"，這其實是精確比對，不是模糊猜測
    for b in candidates:
        eng, _, zh = b.partition(" ")
        if cleaned_lower == eng.lower() or cleaned == zh:
            return b, False

    # 子字串模糊比對，忽略大小寫（例如 AI 讀出「日立牌」，比對到「HITACHI 日立」）
    for b in candidates:
        b_lower = b.lower()
        brand_zh = b.split(" ", 1)[-1] if " " in b else b
        if cleaned_lower in b_lower or cleaned in brand_zh or brand_zh in cleaned:
            return b, True

    return None, False


def build_record_from_ocr(ocr_result: dict, driver_id: str, photo_path: str, barcode_list_no: str | None = None) -> dict:
    """把 OCR 原始結果轉成可寫入資料庫的完整記錄，並決定 status。

    barcode_list_no：如果司機端有掃到聯單上的條碼，這裡會是解碼出來的聯單編號字串。
    條碼是印刷編碼直接解碼出來的，比AI用眼睛「讀」聯單編號準確得多（不會有OCR認錯字的問題），
    所以只要有掃到，一律以條碼結果為準，完全不採用AI辨識出的聯單編號。
    """
    if barcode_list_no:
        list_no = normalize_list_no(barcode_list_no)
    else:
        list_no = normalize_list_no(ocr_result.get("list_no"))
    recycle_dt = parse_date_to_iso(ocr_result.get("recycle_date"))

    # 回收日期年份統一強制為 EXPECTED_RECYCLE_YEAR（目前業務情境下，所有聯單的回收日期
    # 都應該是同一年），不管AI判讀出哪個年份，一律覆蓋成這個值——避免民國年轉換抓錯
    # （例如115/114看錯）導致年份跑掉。每年記得把下面 EXPECTED_RECYCLE_YEAR 這個常數更新一次。
    year_corrected = False
    if recycle_dt:
        y, m, d = recycle_dt.split("-")
        if int(y) != EXPECTED_RECYCLE_YEAR:
            fixed = _safe_date(EXPECTED_RECYCLE_YEAR, int(m), int(d))
            if fixed:
                recycle_dt = fixed
                year_corrected = True

    item = ocr_result.get("item")
    item_type = ocr_result.get("type") if item in ITEM_TYPES else None
    confidence = float(ocr_result.get("confidence") or 0.0)

    brand_raw = ocr_result.get("brand_raw") or ""
    brand, is_fuzzy = match_brand(item, brand_raw)

    get_dt = date.today().isoformat()  # 收受日期預設為上傳當天

    reasons = []
    if not list_no or not _TICKET_NUMBER_PATTERN.match(list_no):
        reasons.append("聯單編號無法辨識或格式不符" if not barcode_list_no else "掃描到的條碼格式不符，請人工確認")
    if not recycle_dt:
        reasons.append("回收日期無法辨識")
    elif recycle_dt > get_dt:
        # 回收（撿貨）不可能晚於收受（入帳），這條規則申報網站會直接擋（「收受日期不可早於
        # 回收日期」）。以前這裡只標記提醒、不自動修正，但因為不確定是哪個數字讀錯，
        # 常常沒被即時處理掉，變成同一個問題一直在匯出時卡關。現在改成直接把回收日期
        # 「夾」到跟收受日期同一天（不會產生不合法的資料），同時還是留言提醒內勤這筆
        # 原本AI讀到的日期有異常，最好回頭核對照片，只是不會再卡住匯出流程。
        original = recycle_dt
        recycle_dt = get_dt
        reasons.append(f"回收日期原判讀為 {original}，晚於收受日期不合理，已自動改為 {get_dt}，請核對正確日期")
    if not item:
        reasons.append("回收項目無法辨識")
    if item and not item_type:
        reasons.append("回收型態無法辨識")
    if not brand:
        reasons.append(f"品牌「{brand_raw}」比對不到官方清單，需人工確認" if brand_raw else "品牌欄位無法辨識")
    elif is_fuzzy:
        reasons.append(f"品牌以模糊比對判斷為「{brand}」，建議核對手寫欄位")
    if confidence < CONFIDENCE_THRESHOLD:
        reasons.append(f"AI信心分數偏低（{confidence:.2f}）")
    note = ocr_result.get("confidence_note") or ""
    if note:
        reasons.append(f"AI備註：{note}")
    if year_corrected:
        reasons.append(f"回收日期年份已強制統一改為 {EXPECTED_RECYCLE_YEAR} 年（AI原判讀年份不同，可能是民國年轉換誤差），請核對")

    return {
        "driver_id": driver_id,
        "photo_path": photo_path,
        "list_no": list_no,
        "get_dt": get_dt,
        "recycle_dt": recycle_dt,
        "item": item,
        "type": item_type,
        "brand": brand or (brand_raw or None),
        "confidence": confidence,
        "status": STATUS_NEED_REVIEW,  # 一律先是「未匯出」，真正匯出後才會變成exported
        "review_note": "；".join(reasons) if reasons else None,
    }
