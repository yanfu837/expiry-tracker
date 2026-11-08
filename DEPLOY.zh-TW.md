# 修正版部署說明

私人附件、刪檔重試與提醒防重複的最新設定，請另見 [PRIVACY-REMINDERS.zh-TW.md](PRIVACY-REMINDERS.zh-TW.md)。

這份程式碼以 GitHub `main` 的 `cb81a89` 為基礎。修正版完成本機驗證後，仍需要更新 GitHub 與現有 Render 服務，線上網站才會套用。

## 已修正

- 新增 `build.sh` 與 `start.sh`；啟動時先執行 Django migration，失敗時停止並留下錯誤日誌。
- 避免 Render 在缺少 `DATABASE_URL` 時默默使用會消失的本機 SQLite。
- 修正沒有附件、保留既有附件、清除附件時的表單錯誤；恢復 10 MB 限制。
- 新增註冊 Email、既有帳號 Email 設定與忘記密碼流程。
- 所有功能頁共用手機版版型、viewport、語言與表單樣式。
- 正式環境補上 Secure Cookie 與 HSTS，配合 Render 的 HTTPS 代理。
- 提醒信改用設定的寄件者、使用者 Email 與應用程式時區；寄信操作需要 POST。
- 將 requirements.txt 改成 UTF-8，套件版本維持不變。

## 先修復這次線上 500 的根因

已提供的 Render Traceback 明確顯示：

```text
django.db.utils.OperationalError: failed to resolve host '...':
[Errno -2] Name or service not known
```

錯誤發生在註冊表單查詢帳號是否存在時：PostgreSQL 主機名稱解析失敗，尚未建立資料庫連線。執行 migration 之前，需要先恢復這個連線。

1. 在 Render 後台開啟目前使用的 PostgreSQL，確認它仍存在且狀態可用。這份錯誤本身無法證明資料庫已到期或刪除。
2. 確認網站服務與 PostgreSQL 的 Region。Render 的內部資料庫主機名稱需要在支援的同區域私有網路中使用。
3. 從該資料庫的 Connect / Connections 重新複製 **Internal Database URL**，更新網站服務 Environment 的 `DATABASE_URL`。完整網址只填在 Render 設定，不要貼到聊天或 GitHub。
4. 若服務跨區域，先確認網路配置；使用資料庫提供的 **External Database URL** 時，必須確認外部存取政策及 TLS 設定。不要自行猜測或補寫主機網域。
5. 如果資料庫到期、刪除或停用，先確認恢復方式與備份。不要直接接上另一個空白資料庫，以免讓既有帳號與資料看似消失。
6. 儲存設定並重新部署；連線恢復後，再執行 migration 與功能驗證。

本 PR 修復程式問題並補上部署流程；修改程式碼不會自動更新現有 Render 的 `DATABASE_URL` 或修復資料庫服務狀態。

### 免費資料庫的期限

依目前 Render 規則，免費 PostgreSQL 在建立 30 天後到期，之後有 14 天可升級續用；超過寬限期會刪除資料庫與資料。免費方案不提供備份。若採用空白重建，必須重新建立帳號及到期項目；新資料庫不會自動帶回舊資料。

請在到期前安排資料匯出或改用適合長期保存資料的方案。以實際資料庫頁面的到期時間為準，參考 [Render 免費方案說明](https://render.com/docs/free#free-postgres)。

## 1. 將修正版放進目前的 GitHub 專案

檢視並合併修正 PR 後，再部署至目前的 Render 服務。若使用本機 Git，可以取得修正分支後檢視差異。

保留現有 `.env`、資料庫與上傳附件。修正版不包含你的密碼或正式資料。不要刪除現有 PostgreSQL 資料庫。

## 2. 在目前的 Render 服務設定部署命令

開啟 Render → 目前網站服務 → Settings，設定：

```text
Build Command: bash build.sh
Start Command: bash start.sh
```

不要建立另一個網站或另一個資料庫來取代現有資料。

本次檢查發現現有服務的 Build Command 已包含 `migrate`；線上錯誤發生在資料庫主機名稱解析。先修復連線，再套用部署命令。

## 3. 檢查 Environment

- 保留原來的 `SECRET_KEY`。
- `DEBUG` 設為 `False`。
- `DATABASE_URL` 指向目前 PostgreSQL 的 **Internal Database URL**。
- `ALLOWED_HOSTS` 可設為 `expiry-tracker-rrqh.onrender.com`；自訂網域另以逗號加入。
- 若有自訂網域，把它的 HTTPS 網址加入 `CSRF_TRUSTED_ORIGINS`。
- 如需台灣日期計算，將 `TIME_ZONE` 設為 `Asia/Taipei`。

真實密碼與連線網址只填在 Render 的 Environment，不要貼進 GitHub 或聊天。

若日誌顯示資料庫過期、無法連線或認證失敗，需先修正該資料庫狀態；migration 無法恢復已被刪除或到期的資料庫。

## 4. 設定 Email 與附件

忘記密碼與提醒信需要可用的 SMTP 設定。參考 README 的 Email 區段，設定 `EMAIL_HOST_USER`、`EMAIL_HOST_PASSWORD`、`DEFAULT_FROM_EMAIL` 等值。請不要在正式站使用範例的 console backend，否則信只會出現在 Logs。

保留原來的 Cloudinary 設定，避免既有附件網址失效；未設定 Cloudinary 時，Render 上會暫停接受新附件，仍可建立不含附件的項目。

舊帳號登入後，請到 **Email settings** 加入可用的 Email。從未設定 Email 且已忘記密碼的帳號，需要管理者協助。

## 5. 部署與確認

確認 GitHub 更新後，在 Render 部署最新版本。查看 Logs，確認 migration 成功且 Gunicorn 已啟動，再實測：

1. 註冊與登入。
2. 新增不含附件的項目、修改、刪除。
3. 上傳合法附件；超過 10 MB 應顯示提示。
4. 忘記密碼的真實信件是否送達。
5. 手機版頁面與既有帳號資料是否正常。

## 已知驗證範圍

本機另外重現了「未執行 migration → 註冊 500；執行後成功」，因此補上自動 migration 以避免後續部署問題。已提供的線上 Traceback 則確認本次線上 500 發生在 PostgreSQL 主機名稱解析，需先依前述步驟修復連線。

自動測試不連線正式 PostgreSQL、Cloudinary 或 SMTP，也不會修改正式資料。自動提醒的每日排程尚未建立；如需啟用，參考 README 的排程指令。
