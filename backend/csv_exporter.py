"""
Module 4：CSV 匯出

欄位順序、欄位名稱對照環境部「廢四機聯單匯入範例說明」官方範例檔案核對過，
直接可以用網站的「多筆聯單帶入」上傳，不需要再手動調整欄位。

日期格式：只留 YYYY-MM-DD，不含時間。官方範例檔案原本是 'YYYY-MM-DD 00:00:00'，
使用者已確認實際申報網站接受純日期格式，這裡照使用者確認的格式輸出。

編碼用 utf-8-sig（也就是 UTF-8 with BOM），這是 Windows Excel 開啟中文
CSV 不會亂碼的關鍵，官方範例檔案本身也是這個編碼。
"""

import csv
import io

CSV_COLUMNS = ["聯單編號", "收受日期", "回收日期", "回收項目", "回收型態", "品牌"]


def _fmt_date(iso_date: str) -> str:
    """YYYY-MM-DD，原樣輸出，不附加時間部分。"""
    return iso_date


def records_to_csv_bytes(records: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for r in records:
        writer.writerow([
            r["list_no"],
            _fmt_date(r["get_dt"]),
            _fmt_date(r["recycle_dt"]),
            r["item"],
            r["type"],
            r["brand"],
        ])
    return buf.getvalue().encode("utf-8-sig")

