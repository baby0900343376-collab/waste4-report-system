"""
Module 2：Claude AI 影像辨識

讀一張「廢四機回收聯單」照片，回傳聯單編號／回收日期／回收項目／回收型態／品牌／信心分數。

設計原則（跟司機端上傳流程有關，很重要）：
- Prompt 明確要求「看不清楚就填 null，不要用猜的」，寧可交給後台人工核對，也不要塞錯資料進政府系統。
- 這個函式「不會」對外拋出讓上傳失敗的例外——任何失敗（額度用完、金鑰錯誤、逾時、AI回傳的不是合法JSON）
  都會被這裡攔下來，回傳一個 confidence=0、status 建議 need_review 的結果，
  讓 main.py 的上傳流程永遠能把這筆記錄寫進資料庫，不會讓司機端看到上傳失敗。
"""

import base64
import json
import os
import re

import anthropic
from constants import VALID_ITEMS, ITEM_TYPES, BRANDS

MODEL = "claude-sonnet-4-6"


def _build_brand_reference() -> str:
    """把四個類別的官方品牌清單整理成prompt要用的文字區塊，見下方 SYSTEM_PROMPT 組合時的說明。"""
    lines = []
    for category, brands in BRANDS.items():
        lines.append(f"【{category}】" + "、".join(brands))
    return "\n".join(lines)


BRAND_REFERENCE = _build_brand_reference()

SYSTEM_PROMPT = f"""你是廢四機回收聯單的資料判讀助手。你會收到一張「廢四機回收聯單」的照片。

請仔細判讀勾選欄位（項目/型態的checkbox是否打勾）與手寫欄位，只回傳一個JSON物件，不要任何其他文字、不要markdown code fence。

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

只回傳JSON，不要其他任何文字。看不清楚寧可填 null 或空字串，不要用猜的，這份資料會直接用於政府申報。"""


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


def _guess_media_type(filename: str) -> str:
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
        img = img.convert("RGB")

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
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return _empty_result("伺服器未設定 ANTHROPIC_API_KEY，無法進行AI辨識，請人工輸入全部欄位")

    try:
        client = anthropic.Anthropic(api_key=api_key)
        processed_bytes, forced_type = _preprocess_image(image_bytes)
        b64 = base64.standard_b64encode(processed_bytes).decode("utf-8")
        media_type = forced_type or _guess_media_type(filename)

        response = client.messages.create(
            model=MODEL,
            max_tokens=500,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                    {"type": "text", "text": "請判讀這張聯單並回傳JSON。"},
                ],
            }],
        )


        text = "".join(block.text for block in response.content if block.type == "text").strip()
        text = re.sub(r"^```(json)?", "", text.strip())
        text = re.sub(r"```$", "", text.strip()).strip()
        parsed = json.loads(text)

    except anthropic.APIError as e:
        return _empty_result(f"AI辨識失敗（API錯誤，可能是額度用完或金鑰錯誤）：{e}")
    except json.JSONDecodeError:
        return _empty_result("AI辨識失敗（回傳內容不是合法JSON），請人工輸入")
    except Exception as e:  # noqa: BLE001 — 這裡故意攔截所有例外，見上方模組說明
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
