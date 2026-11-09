# 自架掃毒、OCR 與資料自動帶入

## 本次範圍與目前狀態

這一版依需求只準備可部署程式，**沒有建立付費主機、沒有部署正式網站，也沒有把客戶文件交給外部 AI API**。主機與費用之後決定。

辨識採用自架的 Tesseract OCR、文件解析與欄位規則，不是雲端大型語言模型。適合有明確標示名稱、到期日、備註的文件；無法保證理解任意版型或任意檔案。

**正式合併前應先準備處理服務。** 新程式要求每個新附件先通過掃描；服務未設定、故障、病毒碼過期或掃描結果不明時，新附件會停止儲存。沒有附件的清單項目仍可建立，既有附件可繼續讀取、保留或刪除。既有附件不會自動重新掃毒。

## 客人操作

1. 開啟新增或編輯頁，選擇附件。
2. 網站先接收到私人暫存檔，交給自架服務掃毒。通過後才進行本機 OCR／解析；尚未寫入 Cloudinary。
3. 名稱、到期日、備註可自動帶入表單。已手動輸入或已儲存的欄位會保留；不明或衝突的日期會留白並提示核對。
4. 客人核對或修改後按儲存，才建立／更新項目並保存私人附件。關閉頁面不會留下正式附件。

技術上無法在伺服器接收到任何檔案位元之前掃毒；這個流程是在私人暫存區先掃描，通過後才做正式儲存與辨識。

## 格式與限制

| 格式 | 自動辨識方式 |
|---|---|
| PDF | 讀取文字；沒有可讀文字的頁面使用本機 OCR，最多前 10 頁 |
| PNG／JPG／JPEG／WEBP | 本機 Tesseract，英文與繁體中文模型 |
| DOCX | 解析文字，不執行 Word 或巨集 |
| XLSX | 解析儲存格、共用字串與日期格式，不計算或執行公式 |
| DOC／XLS | 使用 antiword／xls2csv 讀取可辨識內容 |
| UTF-8／UTF-16 TXT、CSV | 本機讀取文字 |

- 每個檔案最多 10 MB；不支援執行檔、一般壓縮檔或任意副檔名。
- 掃描通過後仍檢查實際檔案標頭，拒絕單純改副檔名的檔案。加密、掃描超限或可疑文件不會被當成安全掃描結果。
- 文件內容最多擷取 200,000 個字元；Office 壓縮內容另有檔數、解壓大小與 XML 安全限制。
- 表格最多檢查前 20 筆資料；目前每次確認儲存一個到期項目，多筆不同的到期資料需自行分項確認。
- 到期日需要明確的到期標籤；支援年在前的數字日期與民國年換算。只有簽發日或多個不同到期日時，不自行猜測。
- OCR 不是百分之百正確，掃毒也不是檔案無害的保證。客人儲存前需確認內容。

## 隱私與掃描順序

瀏覽器 → 現有 Django 網站私人暫存 → 你自己的私人處理服務 → 掃毒 → 本機辨識 → 表單預覽 → 使用者確認 → 原有私人附件儲存。

處理服務不連線資料庫、不持有 Cloudinary 或 Django SECRET_KEY，也不呼叫 OpenAI、VirusTotal 或其他外部文件分析 API。處理完成或失敗時刪除暫存目錄；沒有長期文件佇列或文件快取。原始文件文字及掃描工具輸出不寫入日誌。

解析工具使用獨立子程序，有記憶體、CPU、時間、輸出檔案大小限制；Linux seccomp 阻止它們建立網路連線。服務只接受驗證過的請求，序列處理檔案；不應公開未加密 HTTP 介面。

ClamAV 的更新程序仍需下載官方病毒碼，Docker 建置也需下載套件與 OCR 語言模型；這些下載不包含客戶文件。正式確認的附件仍沿用原有 **Cloudinary authenticated 私人儲存**，並非完全只存在本機硬碟。

瀏覽器取得的掃描憑證由網站簽署，綁定使用者、檔案 SHA-256、大小與格式，15 分鐘內有效。修改檔案、換帳號、憑證過期或停用 JavaScript 都不能略過掃描；模型欄位與私人儲存層也會驗證。憑證不包含文件文字。

## 處理服務：Docker

需要 Linux Docker 容器，建議至少 4 GB RAM。程式以非 root 帳號執行，初次啟動會下載病毒碼；下載成功前不接受文件處理。

```sh
docker build -t expiry-document-processor -f document_service/Dockerfile .
```

在主機上自行產生至少 32 字元的隨機 `DOCUMENT_PROCESSOR_TOKEN`，只存於主機秘密設定與 Render Environment，不貼入聊天或 GitHub。

可用 `document_service/compose.yml` 啟動本機測試或自有主機的服務：

```sh
docker compose --env-file /secure/path/processor.env -f document_service/compose.yml up -d --build
```

`processor.env` 只需包含 `DOCUMENT_PROCESSOR_TOKEN=你產生的隨機值`，檔案限制為管理者可讀。Compose 使用 4 GB 記憶體上限、唯讀根目錄、私人暫存空間與持久病毒碼 volume；連接埠只綁定 `127.0.0.1:10001`。不要使用會刪除病毒碼 volume 的清理指令來處理一般更新。

健康檢查 `/healthz` 必須回應 `200`、`ready=true`。病毒碼檔案不完整或每日病毒碼超過 48 小時未更新時，健康檢查失敗且掃描停止；應檢查 freshclam 下載與更新日誌。

若網站與自有主機不在同一台機器，需先建立你管理的 VPN／私人網路或 HTTPS 反向代理。不要把 Compose 的本機 HTTP 埠直接暴露到公開網路。

## 未來放到 Render 的方案

可以使用同一個 repository 的 **Private Service**，Dockerfile 為 `document_service/Dockerfile`、建置 context 為 repository 根目錄，Region 與現有網站同為 Oregon、Workspace 同為 My Workspace。處理服務只需 `DOCUMENT_PROCESSOR_TOKEN`，不要複製資料庫、寄信或 Cloudinary 密碼。

私人服務沒有公開網址。從 Render Connect／Internal 取得實際服務位址，再填到網站；不要猜主機名稱。若使用專案環境隔離，還需確認彼此允許連線。

依 2026/11/9 查閱的 [Render 價格](https://render.com/pricing)，4 GB、2 CPU 的服務約 **US$85／月**；病毒碼持久磁碟另計。這只是未來部署方案，目前未建立、未核准新增費用。也可以選擇符合條件的自有主機。

參考 [ClamAV 記憶體需求](https://docs.clamav.net/manual/Installing/Docker.html)及 [Render 私人網路](https://render.com/docs/private-network)。現有 512 MB 網站不適合直接執行完整病毒碼與 OCR。

## 網站接入

處理服務可用後，在**現有網站**的 Environment 設定：

```text
DOCUMENT_PROCESSOR_URL=http://實際內部主機:10000
DOCUMENT_PROCESSOR_TOKEN=與私人服務相同的隨機值
```

URL 填服務根位址，不加 `/process`；同機測試可用 `http://127.0.0.1:10001`，公開網域則必須 HTTPS。網站禁止轉址，避免把文件或驗證資訊交給其他目的地。

兩者設定完成、確認健康檢查與測試通過後，再檢視並核准本次 PR 的正式合併／部署。啟動命令已準備使用多執行緒處理，等待掃描時其他頁面仍可回應。

## 驗證

```sh
python -m pip install -r requirements.txt
python manage.py test --settings=config.test_settings
python manage.py makemigrations --check --dry-run --settings=config.test_settings
python -m pip check
docker run --rm --network none expiry-document-processor python -m unittest document_service.test_local_processing
```

測試使用測試資料庫、記憶體信箱及自行建立的文件，不會處理正式客戶資料。掃描連線層與服務錯誤處理有獨立測試；其他既有功能測試使用明確的假掃描器。

容器整合驗證會使用真正的 ClamAV 引擎、專用 EICAR 測試簽章與本機 Tesseract／Poppler，確認測試字串被阻擋、圖片與掃描 PDF 可辨識及暫存檔清除。這不等於已在正式主機驗證完整官方病毒碼；部署時仍需完成官方病毒碼下載，再用 EICAR 與自有非敏感文件驗證。

目前不需要新增資料庫 migration，也不修改到期提醒與忘記密碼的寄信設定。
