# 廢四機聯單申報系統

司機手機拍照上傳 → Claude AI 辨識 → 資料清洗轉換 → 內勤核對後台 → 匯出環境部格式 CSV。

跟你參考的那份範例功能架構相同（拍照上傳／AI辨識／人工核對／CSV匯出四個模組），但這份是重新寫的：
- OCR 用 **Claude API**（`claude-sonnet-4-6`）而不是 Gemini
- CSV 欄位是照**官方「廢四機聯單匯入範例說明」範例檔案**核對過的實際格式
  （聯單編號,收受日期,回收日期,回收項目,回收型態,品牌，日期含 `00:00:00`），不是通用命名的預設欄位
- 回收項目／型態／品牌下拉選單，用的是官方範例檔案裡的完整清單（`constants.py`），
  內勤修正欄位時不會選到系統不認得的選項

## 專案結構

```
waste4_report_system/
└── backend/
    ├── main.py              # FastAPI 主程式（API + 網頁路由）
    ├── database.py          # Module 1：資料庫（SQLite）
    ├── claude_ocr.py        # Module 2：Claude AI 影像辨識
    ├── data_transform.py    # Module 3：民國轉西元、品牌比對、驗證
    ├── csv_exporter.py      # Module 4：CSV 匯出（utf-8-sig，官方格式）
    ├── constants.py         # Module 0：回收項目／型態／品牌主檔清單
    ├── requirements.txt
    ├── .env.example         # 複製為 .env 並填入金鑰
    └── static/
        ├── uploads/         # 上傳的聯單照片存放處
        └── pages/
            ├── upload.html  # 司機端：手機拍照上傳
            └── admin.html   # 內勤端：核對／修正／匯出

```

## 安裝與啟動

```bash
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# 打開 .env，填入 GEMINI_API_KEY（或 ANTHROPIC_API_KEY，看你用哪個版本）

python generate_cert.py   # 產生本機用的自簽HTTPS憑證，見下方說明
uvicorn main:app --reload --host 0.0.0.0 --port 8000 --ssl-keyfile key.pem --ssl-certfile cert.pem
```

Windows 用戶可以直接雙擊 `start_server.bat`，會自動處理憑證產生、啟用 venv、開瀏覽器這幾步。

啟動後：

| 用途 | 網址 |
|---|---|
| 司機拍照上傳頁面 | `https://<你的主機位址>:8000/upload` |
| 內勤核對後台 | `https://<你的主機位址>:8000/admin` |
| API 文件（Swagger） | `https://<你的主機位址>:8000/docs` |

本機測試時，手機要連得到 `/upload`，手機與電腦要在同一個 Wi-Fi，網址用電腦的區域網路 IP
（例如 `192.168.1.5`）取代 `localhost`。要讓外部司機（不同網路）也能上傳，需要部署到有固定網址、
真正的 HTTPS 憑證（例如 Let's Encrypt）的雲端主機，不要一直用 `generate_cert.py` 產生的自簽憑證。

### 為什麼一定要 HTTPS

「連續拍照模式」（見下方）用的是瀏覽器的 `getUserMedia` 相機權限API，這個API規定一定要在
`https://` 或 `localhost` 底下才能用，`http://192.168.x.x` 這種本機區網IP會被手機瀏覽器直接
擋掉鏡頭權限。`generate_cert.py` 產生的是自簽憑證，手機第一次連線會跳出「不安全」警告，
這是正常的，按「進階」→「繼續前往」接受一次即可（每台裝置只需要接受一次）。

## 完整流程

0. **拍照（連續拍照模式）**：`/upload` 頁面預設用「連續拍照模式」——相機直接嵌在網頁裡即時顯示，
   按快門就拍、鏡頭不關閉、馬上可以拍下一張，全程不跳出手機相機App、也不會把照片存進手機相簿。
   這個模式需要 HTTPS（見上方說明）。如果相機打不開（沒設定HTTPS、瀏覽器不支援、或使用者拒絕權限），
   會顯示原因並提供「單張拍照」（原本 `capture="environment"` 那種，會跳出相機App）跟
   「從相簿選多張」兩種備用方式，不會讓司機卡住無法上傳。
1. **司機上傳**：`POST /api/upload`（圖片 + `driver_id`），照片存檔後呼叫 Claude 辨識，
   結果經過 `data_transform.py` 清洗驗證後寫入資料庫，回傳這筆記錄目前的狀態。
2. **AI 辨識失敗也不會讓上傳失敗**：額度用完、金鑰錯誤、逾時、AI回傳非法JSON，
   `claude_ocr.py` 都會攔截並回傳一個 confidence=0 的空結果，這筆記錄仍會寫入資料庫，
   狀態自動是 `need_review`，內勤補打全部欄位即可，司機端不會看到上傳失敗。
3. **內勤查詢**：`GET /api/records?status=need_review`，後台三個分頁（需核對／已確認待匯出／已匯出）
   對應三種狀態，也可以看「全部」。
4. **人工修正**：後台表格可以直接改欄位，項目／型態／品牌都是下拉選單（鎖定在官方清單內），
   按「儲存並確認」會呼叫 `PUT /api/records/{id}`，狀態改成 `confirmed`。
5. **CSV 匯出**：後台勾選要匯出的記錄，按「匯出選取項目為 CSV」呼叫 `POST /api/export`，
   下載的檔案編碼是 `utf-8-sig`（Excel 開啟中文不會亂碼），欄位格式跟官方範例檔案一致，
   可以直接用申報網站的「多筆聯單帶入」上傳。
6. **匯出後自動標記 `exported`**，避免同一批資料被重複匯出、重複申報。匯出前也會檢查
   每筆記錄的六個必要欄位都不是空的，缺欄位會擋下來並告訴你是哪一筆、缺什麼。

## 各模組設計重點

- **`claude_ocr.py`**：prompt 明確要求「看不清楚就填 null，不要用猜的」，並要求 AI 自評
  `confidence`（0~1）。任何關鍵欄位是 null，或信心分數低於 0.6，`data_transform.py` 就會把
  這筆標記成 `need_review`，理由寫進 `review_note`，內勤打開後台一眼就知道要核對什麼、為什麼。
- **`data_transform.py`**：
  - 民國轉西元支援 `115.6.13`、`115/06/13`、`115年6月13日` 三種常見手寫/列印格式
  - 品牌比對：AI 讀出的手寫文字先做精確比對，比對不到再做子字串模糊比對
    （例如讀出「日立」會比對到「HITACHI 日立」），模糊比對成功一樣會標記 `need_review`
    提醒人工核對手寫欄位，不會悄悄相信 AI 的猜測
  - 聯單編號格式規則目前是「至少6碼英數字」的寬鬆判斷（`_TICKET_NUMBER_PATTERN`），
    請依你們實際聯單編號規則調整
- **`csv_exporter.py`**：欄位、順序、日期格式都是照官方範例檔案核對過的，這是這份重寫版
  跟原本參考範例最大的差異——原本的欄位是通用預設命名，這份是真的核對過官方格式。

## 尚待你確認/客製化的部分

- **登入權限**：目前 `driver_id` 是自由輸入的文字欄位，`/admin` 後台也沒有登入機制，
  正式上線前建議加上帳號密碼或至少一個簡單的存取金鑰，避免不相關的人上傳或看到後台。
- **部署**：
  - 圖片目前存在本機磁碟（`static/uploads/`），量大建議改存雲端物件儲存（S3 等）
  - 資料庫目前是 SQLite，量大或多人同時寫入建議改 PostgreSQL（改 `.env` 的 `DATABASE_URL`，
    程式碼要把 `database.py` 换成 SQLAlchemy engine，目前先用標準函式庫把原型跑起來）
  - 務必加 HTTPS
- **`_TICKET_NUMBER_PATTERN`**（`data_transform.py`）：依你們實際聯單編號規則調整，
  例如固定長度、固定開頭字母等，目前是寬鬆的「6碼以上英數字」。
- **Claude API 費用**：每上傳一張照片會呼叫一次 API，正式量大使用前建議留意
  [Anthropic 的用量與計費](https://console.anthropic.com)。

## 開源／貢獻須知

- **絕對不要把 `.env`、`cert.pem`、`key.pem`、`*.db` 這幾種檔案提交進版本控制**——已經在
  `.gitignore` 裡排除了，但如果你手動 `git add -f` 硬加，或用別的方式（例如壓縮整個資料夾上傳）
  還是可能不小心把金鑰、憑證私鑰、實際上傳過的聯單資料外流出去，發布前務必再檢查一次。
- 這個專案含有實際聯單資料格式與環境部官方欄位對照，如果貴公司或機構的實際欄位規則
  （例如聯單編號格式）跟這份程式碼裡假設的不一樣，記得先在自己的分支調整過再用。
- 歡迎修改、擴充、發 PR。

## License

[MIT](LICENSE) — 可自由使用、修改、散布，包含商業用途，但不提供任何保固。

