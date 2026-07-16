"""
Module 2（Gemini版）：Gemini AI 影像辨識

跟 claude_ocr.py 是同一個角色的替代方案，函式簽名、回傳格式完全相同：
    run_ocr(image_bytes: bytes, filename: str) -> dict

要切換成這個模組，main.py 只要把
    import claude_ocr as ocr_module
改成
    import gemini_ocr as ocr_module
（或直接把下面這個函式改名貼進 claude_ocr.py，看你想留哪個檔名）其他程式碼完全不用動，
因為 data_transform.py 只依賴這個 dict 的欄位名稱，不管是哪一家 AI 產生的。

用的是 Gemini REST API（不依賴 google-generativeai 這個 SDK，減少一個依賴），
並用 response_mime_type: application/json 強制 Gemini 回傳合法 JSON，
比純靠 prompt 要求「只回傳JSON」更可靠。
"""

import base64
import json
import os

import httpx

from constants import VALID_ITEMS, ITEM_TYPES, BRANDS

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
GEMINI_ENDPOINT = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
)


def _build_brand_reference() -> str:
    """把四個類別的官方品牌清單整理成prompt要用的文字區塊。
    這是這版OCR準確度改進的核心：以前是讓AI自由把看到的品牌文字寫出來（brand_raw），
    寫完之後才由 data_transform.py 的程式碼去跟官方清單做字串比對——
    這樣任何 AI 轉寫時的大小寫、拼法差異，都要靠事後的比對邏輯去猜，容易漏接
    （之前National/Westinghouse/whirlpool那幾次「品牌不存在」都是這樣來的）。
    現在直接把完整清單放進prompt，讓AI自己「看著清單選」，同一次推論裡完成
    視覺辨識+比對，輸出的就會是清單裡本來就存在的精確字串，準確率高很多，
    也讓後面的比對程式碼變成單純的安全網，而不是主要防線。
    """
    lines = []
    for category, brands in BRANDS.items():
        lines.append(f"【{category}】" + "、".join(brands))
    return "\n".join(lines)


BRAND_REFERENCE = _build_brand_reference()

OCR_PROMPT = f"""你是廢四機回收聯單的資料判讀助手。你會收到一張「廢四機回收聯單」的照片。

請仔細判讀勾選欄位（項目/型態的checkbox是否打勾）與手寫欄位，只回傳一個JSON物件。

JSON格式：
{{
  "list_no": "聯單編號，例如 AOB0005907325，去除多餘空白。看不清楚填 null",
  "recycle_date": "回收日期，換算成西元年份，格式 YYYY-MM-DD。聯單上印的是民國年，民國年+1911=西元年。看不清楚或無法判斷填 null",
  "item": "項目，必須是這四個字串之一：電視機、電冰箱、洗衣機、冷、暖氣機。如果看不出勾選哪一個，填 null，不要用猜的",
  "type": "型態，必須是該項目對應的三個型態選項之一（見下方對照表）。看不出來填 null",
  "brand_raw": "品牌欄位判讀結果，見下方「品牌判讀規則」，非常重要",
  "confidence": "0到1之間的數字，代表你對這次判讀整體的信心程度。任何一個關鍵欄位是用猜的，信心就不該超過0.5",
  "confidence_note": "簡短說明有沒有辨識不確定或有疑慮的地方，沒有就填空字串"
}}

項目與型態對應：
電視機: CRT電視 / 液晶電視 / 內投影電視
電冰箱: 單門冰箱 / 雙門冰箱 / 多門冰箱
洗衣機: 單槽洗衣機 / 雙槽洗衣機 / 滾筒洗衣機
冷、暖氣機: 窗型冷氣 / 分離式冷氣 / 其他冷氣

品牌判讀規則（非常重要，請仔細照做）：
先判斷這張聯單勾選的是哪個項目類別，然後只看該類別底下的官方品牌清單（見下方），
把照片上手寫或印刷的品牌文字，跟該清單逐一比對，找出最接近、最合理的那一個。
brand_raw 欄位「必須」輸出清單裡的字串，一字不差照抄（包含大小寫、空格、中英文都要一致），
不要自己改寫、不要只寫英文或只寫中文、不要用你覺得對的方式重新拼寫。
如果比對過後真的找不到合理對應的品牌（清單裡沒有這個牌子、或字跡完全無法辨識），
brand_raw 填空字串，不要硬選一個最接近的湊數。

官方品牌清單（依項目類別分組）：
{BRAND_REFERENCE}

看不清楚寧可填 null 或空字串，不要用猜的，這份資料會直接用於政府申報。"""

# 用 responseSchema 限制 Gemini 的輸出結構，比純文字prompt要求更可靠
RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "list_no": {"type": "STRING", "nullable": True},
        "recycle_date": {"type": "STRING", "nullable": True},
        "item": {"type": "STRING", "nullable": True},
        "type": {"type": "STRING", "nullable": True},
        "brand_raw": {"type": "STRING"},
        "confidence": {"type": "NUMBER"},
        "confidence_note": {"type": "STRING"},
    },
    "required": ["brand_raw", "confidence", "confidence_note"],
}


def _empty_result(note: str) -> dict:
    return {
        "list_no": None,
        "recycle_date": None,
        "item": None,
        "type": None,
        "brand_raw": "",
        "confidence": 0.0,
        "confidence_note": note,
    }


def _guess_mime_type(filename: str) -> str:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else "jpeg"
    return {
        "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "png": "image/png", "webp": "image/webp", "heic": "image/heic",
    }.get(ext, "image/jpeg")


MAX_DIMENSION = 2048  # 長邊超過這個像素才縮小。上傳已經跟AI辨識拆開背景處理，不影響感受速度，
# 可以放寬解析度換取更清楚的細節，尤其是手寫品牌欄位那種小字
JPEG_QUALITY = 85


def _preprocess_image(image_bytes: bytes) -> tuple[bytes, str]:
    """把手機拍的大圖縮小、轉成 JPEG 再傳給AI，明顯縮短上傳與AI處理時間。
    縮圖失敗（例如 Pillow 沒裝、檔案格式讀不了）就直接用原圖，不讓上傳流程中斷。"""
    try:
        from PIL import Image
        import io

        img = Image.open(io.BytesIO(image_bytes))
        img = img.convert("RGB")  # 統一轉RGB：HEIC/PNG的alpha通道存成JPEG會出錯

        w, h = img.size
        if max(w, h) > MAX_DIMENSION:
            scale = MAX_DIMENSION / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        return buf.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001 — 縮圖只是優化，失敗就退回原圖，不影響辨識流程
        return image_bytes, None


def run_ocr(image_bytes: bytes, filename: str) -> dict:
    """讀一張聯單照片，回傳判讀結果 dict。永遠不拋例外給呼叫端。"""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return _empty_result("伺服器未設定 GEMINI_API_KEY，無法進行AI辨識，請人工輸入全部欄位")

    processed_bytes, forced_mime = _preprocess_image(image_bytes)
    image_b64 = base64.standard_b64encode(processed_bytes).decode("utf-8")
    mime_type = forced_mime or _guess_mime_type(filename)

    payload = {
        "contents": [{
            "parts": [
                {"text": OCR_PROMPT},
                {"inline_data": {"mime_type": mime_type, "data": image_b64}},
            ]
        }],
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": RESPONSE_SCHEMA,
            "temperature": 0.1,
        },
    }

    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(GEMINI_ENDPOINT, params={"key": api_key}, json=payload)
        resp.raise_for_status()
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
    except httpx.HTTPStatusError as e:
        return _empty_result(f"AI辨識失敗（Gemini API錯誤 {e.response.status_code}，可能是額度用完或金鑰錯誤）")
    except httpx.HTTPError as e:
        return _empty_result(f"AI辨識失敗（無法連線到Gemini API）：{e}")
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        return _empty_result(f"AI辨識失敗（Gemini回傳格式異常，無法解析）：{e}")
    except Exception as e:  # noqa: BLE001 — 故意攔截所有例外，確保上傳流程不會中斷
        return _empty_result(f"AI辨識發生未預期錯誤：{e}")

    # 驗證AI回傳的 item/type 是不是官方認可的選項，不是的話視為未判讀成功
    item = parsed.get("item")
    item_type = parsed.get("type")
    if item not in VALID_ITEMS:
        item = None
        item_type = None
    elif item_type not in ITEM_TYPES.get(item, []):
        item_type = None

    return {
        "list_no": (parsed.get("list_no") or None),
        "recycle_date": (parsed.get("recycle_date") or None),
        "item": item,
        "type": item_type,
        "brand_raw": parsed.get("brand_raw") or "",
        "confidence": float(parsed.get("confidence") or 0.0),
        "confidence_note": parsed.get("confidence_note") or "",
    }
