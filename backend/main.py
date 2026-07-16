"""
主程式：FastAPI，串起 Module 1~4，並提供司機端上傳頁面與內勤後台頁面。

流程：
  司機手機拍照 --POST /api/upload--> 存檔+寫入DB(狀態: AI辨識中) --立刻回應司機「上傳成功」
                                    └─ 背景任務：Module2 AI辨識 -> Module3 清洗驗證 -> 更新DB
  內勤電腦    --GET /api/records?status=need_review--> 看待核對清單
              --PUT /api/records/{id}--> 修正欄位
              --POST /api/export--> Module4 產生CSV，該批記錄標記為 exported

為什麼上傳要跟AI辨識分開兩段：AI辨識（呼叫Gemini/Claude）本身要幾秒鐘，如果讓司機的上傳
請求等AI辨識做完才回應，司機端會覺得「上傳」很慢——但其實慢的是AI，不是網路傳輸。
拆成「先存檔立刻回應」+「背景慢慢做AI辨識」之後，司機端幾乎是按了就跳下一張，
大量連續上傳的情境（例如一次處理幾百張）會快很多。內勤在後台看到的資料會在AI辨識完成後
自動出現（後台本來就有輪詢機制，見 admin.html），不需要額外操作。
"""

import os
import uuid
import asyncio
from pathlib import Path
from datetime import date

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import io

import database
import gemini_ocr as ocr_module  # 換回 Claude 只要改成: import claude_ocr as ocr_module
import data_transform
import csv_exporter
from constants import ITEM_TYPES, BRANDS, ALL_STATUSES, STATUS_EXPORTED

BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
PAGES_DIR = BASE_DIR / "static" / "pages"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="廢四機聯單申報系統")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

database.init_db()


# ---------- 網頁路由 ----------

@app.get("/upload", response_class=HTMLResponse)
def upload_page():
    return FileResponse(PAGES_DIR / "upload.html")


@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    return FileResponse(PAGES_DIR / "admin.html")


@app.get("/")
def root():
    return {"service": "廢四機聯單申報系統", "pages": ["/upload", "/admin"], "docs": "/docs"}


# ---------- API：主檔選項（給前端畫下拉選單用） ----------

@app.get("/api/options")
def get_options():
    return {"item_types": ITEM_TYPES, "brands": BRANDS}


# ---------- API：司機上傳 ----------

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # 15MB


def _run_ocr_and_update(record_id: int, content: bytes, filename: str, barcode_list_no: str | None):
    """背景任務：實際呼叫AI辨識、清洗驗證，完成後更新資料庫。這一步慢（幾秒鐘），
    所以不能放在 /api/upload 的主流程裡，不然司機端每上傳一張都要多等好幾秒。"""
    existing = database.get_record(record_id)
    if not existing:
        return  # 記錄被刪了（例如內勤手動刪除），沒必要再處理

    ocr_result = ocr_module.run_ocr(content, filename)
    record_data = data_transform.build_record_from_ocr(
        ocr_result, existing["driver_id"], existing["photo_path"], barcode_list_no=barcode_list_no
    )
    # driver_id / photo_path 不需要更新（本來就對），只更新AI辨識相關欄位
    update_fields = {
        k: v for k, v in record_data.items()
        if k not in ("driver_id", "photo_path", "get_dt")
    }
    database.update_record(record_id, update_fields)


@app.post("/api/upload")
async def upload_photo(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    driver_id: str = Form(...),
    scanned_list_no: str | None = Form(default=None),
):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"不支援的檔案格式：{ext or '(無副檔名)'}")

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "檔案太大，請壓縮後再上傳（上限 15MB）")
    if not driver_id or not driver_id.strip():
        raise HTTPException(400, "缺少司機代號")

    # 存檔（檔名用 uuid 避免撞名/覆蓋）
    saved_name = f"{uuid.uuid4().hex}{ext}"
    saved_path = UPLOAD_DIR / saved_name
    saved_path.write_bytes(content)

    barcode_list_no = data_transform.normalize_list_no(scanned_list_no) if scanned_list_no else None

    # 先寫一筆「AI辨識中」的暫時記錄，立刻回應司機端——這一步只有存檔+寫DB，很快。
    # 真正花時間的AI辨識丟到背景任務，不擋司機端的上傳流程。
    # 如果司機端有掃到條碼，聯單編號直接用條碼結果（比AI辨識準），不用等背景任務。
    placeholder = {
        "driver_id": driver_id.strip(),
        "photo_path": f"/static/uploads/{saved_name}",
        "list_no": barcode_list_no,
        "get_dt": date.today().isoformat(),
        "recycle_dt": None,
        "item": None,
        "type": None,
        "brand": None,
        "confidence": None,
        "status": "need_review",
        "review_note": "AI辨識處理中，請稍後在後台重新整理查看結果",
    }
    record_id = database.insert_record(placeholder)

    background_tasks.add_task(_run_ocr_and_update, record_id, content, file.filename or saved_name, barcode_list_no)

    saved = database.get_record(record_id)
    return {"ok": True, "record": saved}



# ---------- API：內勤查詢／修正／匯出 ----------

@app.get("/api/records")
def api_list_records(status: str | None = Query(default=None), driver_id: str | None = Query(default=None)):
    if status and status not in ALL_STATUSES:
        raise HTTPException(400, f"status 必須是 {ALL_STATUSES} 其中之一")
    return {"records": database.list_records(status, driver_id)}


@app.get("/api/drivers")
def api_list_drivers():
    """給後台「按人篩選」下拉選單用。"""
    return {"drivers": database.list_driver_ids()}


@app.post("/api/maintenance/fix-invalid-dates")
def api_fix_invalid_dates():
    """一次性清理舊資料：把資料庫裡所有「回收日期晚於收受日期」但還沒匯出的記錄，
    回收日期自動改成跟收受日期同一天。這是在加上這條驗證規則之前就已經存在的舊資料
    才需要用到；規則加上之後，新資料在寫入的當下就已經不會產生這種情況了
    （見 data_transform.py），這支只是補救措施。"""
    fixed = database.fix_invalid_recycle_dates()
    return {"fixed_count": len(fixed), "fixed": fixed}


@app.get("/api/records/{record_id}")
def api_get_record(record_id: int):
    rec = database.get_record(record_id)
    if not rec:
        raise HTTPException(404, "找不到這筆記錄")
    return rec


class RecordUpdate(BaseModel):
    list_no: str | None = None
    get_dt: str | None = None
    recycle_dt: str | None = None
    item: str | None = None
    type: str | None = None
    brand: str | None = None
    status: str | None = None
    review_note: str | None = None


@app.put("/api/records/{record_id}")
def api_update_record(record_id: int, body: RecordUpdate):
    existing = database.get_record(record_id)
    if not existing:
        raise HTTPException(404, "找不到這筆記錄")

    fields = {k: v for k, v in body.model_dump().items() if v is not None}

    if "item" in fields and fields["item"] not in ITEM_TYPES:
        raise HTTPException(400, f"item 必須是 {list(ITEM_TYPES.keys())} 其中之一")
    check_item = fields.get("item", existing["item"])
    if "type" in fields and check_item in ITEM_TYPES and fields["type"] not in ITEM_TYPES[check_item]:
        raise HTTPException(400, f"type 跟 item「{check_item}」不匹配")
    if "status" in fields and fields["status"] not in ALL_STATUSES:
        raise HTTPException(400, f"status 必須是 {ALL_STATUSES} 其中之一")

    # 回收日期不得晚於收受日期——不管這次改的是哪一個欄位，都要用「改完之後」的兩個值一起檢查，
    # 不能只看有沒有被送進來的那個欄位（例如只改收受日期，也可能反而讓原本合法的回收日期變不合法）
    check_get_dt = fields.get("get_dt", existing["get_dt"])
    check_recycle_dt = fields.get("recycle_dt", existing["recycle_dt"])
    if check_get_dt and check_recycle_dt and check_recycle_dt > check_get_dt:
        raise HTTPException(400, f"回收日期（{check_recycle_dt}）不得晚於收受日期（{check_get_dt}）")

    updated = database.update_record(record_id, fields)
    return updated


@app.delete("/api/records/{record_id}")
def api_delete_record(record_id: int, force: bool = Query(default=False)):
    existing = database.get_record(record_id)
    if not existing:
        raise HTTPException(404, "找不到這筆記錄")
    if existing["status"] == STATUS_EXPORTED and not force:
        raise HTTPException(
            400,
            "這筆記錄已經匯出過（已申報），刪除前請再次確認——這會刪掉這筆的存查紀錄。"
            "確定要刪，請加上 ?force=true 再送一次。",
        )
    database.delete_record(record_id)
    return {"ok": True, "deleted_id": record_id}


class ExportRequest(BaseModel):
    ids: list[int]


async def _delayed_delete_exported(ids: list[int], delay_seconds: int = 60):
    """匯出完成後，過一段時間自動把這批記錄（連同聯單照片）刪掉。
    用意是CSV已經匯出、資料已經在申報網站送出了，本機不用再留著佔空間。
    等60秒才刪（不是匯出當下立刻刪）是為了留一點緩衝時間，萬一匯出的CSV有問題
    需要回頭核對，還來得及在這60秒內處理；正式環境如果不需要這個緩衝，把delay_seconds改成0即可。"""
    await asyncio.sleep(delay_seconds)
    for rid in ids:
        rec = database.get_record(rid)
        if not rec:
            continue  # 可能已經被手動刪除了，跳過
        if rec["status"] != STATUS_EXPORTED:
            continue  # 匯出後又被改動、狀態不是exported了，保險起見不要自動刪
        photo_path = rec.get("photo_path")
        if photo_path:
            # photo_path 存的是 /static/uploads/xxx.jpg 這種URL路徑，轉成實際檔案路徑
            local_path = BASE_DIR / photo_path.lstrip("/")
            try:
                if local_path.exists():
                    local_path.unlink()
            except OSError:
                pass  # 檔案刪不掉（例如被占用）不影響資料庫記錄照樣刪除
        database.delete_record(rid)


@app.post("/api/export")
def api_export(body: ExportRequest, background_tasks: BackgroundTasks):
    if not body.ids:
        raise HTTPException(400, "請至少選擇一筆記錄")

    records = []
    for rid in body.ids:
        rec = database.get_record(rid)
        if not rec:
            raise HTTPException(404, f"找不到記錄 id={rid}")
        missing = [f for f in ("list_no", "get_dt", "recycle_dt", "item", "type", "brand") if not rec.get(f)]
        if missing:
            raise HTTPException(
                400,
                f"記錄 id={rid}（聯單編號：{rec.get('list_no') or '未知'}）缺少欄位 {missing}，"
                f"請先在後台補齊再匯出",
            )
        # 收受日期不可早於回收日期（=回收日期不得晚於收受日期）——這條規則是後來才加的驗證，
        # 舊資料可能是在還沒有這個檢查之前就存進去的，所以匯出前一定要再檢查一次，
        # 不然申報網站會直接把整批CSV退回來，錯誤訊息還只會列出第一筆有問題的。
        if rec["recycle_dt"] > rec["get_dt"]:
            raise HTTPException(
                400,
                f"記錄 id={rid}（聯單編號：{rec.get('list_no')}）回收日期（{rec['recycle_dt']}）"
                f"晚於收受日期（{rec['get_dt']}），申報網站會拒收，請先回後台修正再匯出",
            )
        records.append(rec)

    csv_bytes = csv_exporter.records_to_csv_bytes(records)
    database.mark_exported(body.ids)

    # 匯出完成，排程1分鐘後自動清掉這批記錄跟照片檔案（見上方函式說明）
    background_tasks.add_task(_delayed_delete_exported, body.ids, 60)

    filename = f"waste4_export_{len(records)}records.csv"
    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
