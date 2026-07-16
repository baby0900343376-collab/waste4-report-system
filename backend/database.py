"""
Module 1：資料庫（SQLite）

單一資料表 records，存放每一張聯單從「司機拍照上傳」到「內勤核對後匯出」的完整生命週期。
狀態機（簡化版）：need_review（尚未匯出，不管有沒有人看過或改過欄位） -> exported（已下載進CSV）。

正式上線若要換 PostgreSQL/MySQL，只需要把 DATABASE_URL 改掉、
並把下面 sqlite3 的部分換成 SQLAlchemy engine（目前先用標準函式庫 sqlite3，
不強迫使用者多裝一個 ORM 才能跑起原型）。
"""

import sqlite3
import os
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = os.getenv("DATABASE_URL", "waste4.db")
if DB_PATH.startswith("sqlite:///"):
    DB_PATH = DB_PATH.replace("sqlite:///", "", 1)


def init_db() -> None:
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                driver_id TEXT NOT NULL,
                photo_path TEXT NOT NULL,
                list_no TEXT,
                get_dt TEXT,
                recycle_dt TEXT,
                item TEXT,
                type TEXT,
                brand TEXT,
                confidence REAL,
                status TEXT NOT NULL DEFAULT 'need_review',
                review_note TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def insert_record(data: dict) -> int:
    with get_conn() as conn:
        ts = now_iso()
        cur = conn.execute(
            """
            INSERT INTO records
                (driver_id, photo_path, list_no, get_dt, recycle_dt, item, type, brand,
                 confidence, status, review_note, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                data.get("driver_id"),
                data.get("photo_path"),
                data.get("list_no"),
                data.get("get_dt"),
                data.get("recycle_dt"),
                data.get("item"),
                data.get("type"),
                data.get("brand"),
                data.get("confidence"),
                data.get("status", "need_review"),
                data.get("review_note"),
                ts,
                ts,
            ),
        )
        conn.commit()
        return cur.lastrowid


def list_records(status: str | None = None, driver_id: str | None = None) -> list[dict]:
    with get_conn() as conn:
        clauses = []
        params = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if driver_id:
            clauses.append("driver_id = ?")
            params.append(driver_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(f"SELECT * FROM records {where} ORDER BY id DESC", params).fetchall()
        return [dict(r) for r in rows]


def list_driver_ids() -> list[str]:
    """給後台「按人篩選」下拉選單用：抓資料庫裡目前實際出現過的司機代號，
    不用手動維護一份人員清單，誰上傳過就會出現在篩選選項裡。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT driver_id FROM records WHERE driver_id IS NOT NULL ORDER BY driver_id"
        ).fetchall()
        return [r["driver_id"] for r in rows]


def fix_invalid_recycle_dates() -> list[dict]:
    """一次性清理：找出所有「回收日期晚於收受日期」的舊資料（在這條驗證規則加入之前
    就已經存進去的），把回收日期夾到跟收受日期同一天，並標記review_note提醒核對。
    回傳被修正過的記錄清單，讓呼叫端知道改了哪些、方便告訴使用者。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM records WHERE recycle_dt IS NOT NULL AND get_dt IS NOT NULL "
            "AND recycle_dt > get_dt AND status != 'exported'"
        ).fetchall()
        fixed = []
        for row in rows:
            rec = dict(row)
            original = rec["recycle_dt"]
            new_note_part = f"回收日期原為 {original}，晚於收受日期不合理，已自動改為 {rec['get_dt']}，請核對正確日期"
            existing_note = rec.get("review_note") or ""
            combined_note = f"{existing_note}；{new_note_part}" if existing_note else new_note_part
            conn.execute(
                "UPDATE records SET recycle_dt = ?, review_note = ?, updated_at = ? WHERE id = ?",
                (rec["get_dt"], combined_note, now_iso(), rec["id"]),
            )
            fixed.append({"id": rec["id"], "list_no": rec["list_no"], "old_recycle_dt": original, "new_recycle_dt": rec["get_dt"]})
        conn.commit()
        return fixed


def get_record(record_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
        return dict(row) if row else None


def delete_record(record_id: int) -> bool:
    """刪除一筆記錄（不刪照片檔案，只刪資料庫紀錄，避免誤刪還沒備份的照片）。"""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM records WHERE id = ?", (record_id,))
        conn.commit()
        return cur.rowcount > 0


def update_record(record_id: int, fields: dict) -> dict | None:
    if not fields:
        return get_record(record_id)
    fields = dict(fields)
    fields["updated_at"] = now_iso()
    cols = ", ".join(f"{k} = ?" for k in fields.keys())
    values = list(fields.values()) + [record_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE records SET {cols} WHERE id = ?", values)
        conn.commit()
    return get_record(record_id)


def mark_exported(ids: list[int]) -> None:
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE records SET status = 'exported', updated_at = ? WHERE id IN ({placeholders})",
            [now_iso(), *ids],
        )
        conn.commit()
