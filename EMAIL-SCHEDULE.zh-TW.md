# 自動 Email 與免費每日排程

## 運作方式

`.github/workflows/expiry-reminders.yml` 使用公開專案的標準 GitHub Actions runner，每天 **台灣時間 09:00** 執行（UTC `0 1 * * *`）。它向網站送出短效、由 GitHub 簽署的身分憑證；網站只接受指定專案、擁有者、main 分支與這個工作流程，然後查詢原有資料庫並寄信。

GitHub 不需要 PostgreSQL、SMTP、Cloudinary 密碼，也不會取得清單內容、客戶 Email 或 PDF。HTTP 回應與工作流程日誌僅顯示數量。每次請求最多寄一個帳號的摘要，接著繼續下一批；正常重跑略過已成功的「項目＋到期日＋30／14／7／1 天」紀錄。寄送失敗會保留、退避重試，其他帳號仍可繼續。

這個方案**不建立付費 Render Cron Job**。依 [GitHub 計費說明](https://docs.github.com/en/billing/concepts/product-billing/github-actions)，公開儲存庫使用標準 runner 免費。GitHub 排程可能延遲或略過；公開專案 60 天沒有活動時，GitHub 會停用排程，需要到 Actions 重新啟用。參考 [schedule 事件說明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)。

## 1. Render 的排程信任設定

將程式合併到 main 後，在目前網站服務的 Environment 加入以下**非機密設定**，保留既有資料庫與附件設定：

```text
TIME_ZONE=Asia/Taipei
REMINDER_OIDC_AUDIENCE=https://expiry-tracker-rrqh.onrender.com/internal/reminders/run/
REMINDER_OIDC_REPOSITORY_ID=1372462294
REMINDER_OIDC_OWNER_ID=93774500
REMINDER_OIDC_WORKFLOW_REF=yanfu837/expiry-tracker/.github/workflows/expiry-reminders.yml@refs/heads/main
```

這些 ID 限定此專案，重新命名或移轉專案時需重新檢查。Fork 不會自動取得觸發權限。GitHub 的舊版與新版不可變 subject 格式皆支援，但兩者都必須符合指定的專案 ID、擁有者 ID、分支與工作流程。

## 2. Render 的寄信設定

開啟 [網站的 Environment](https://dashboard.render.com/web/srv-dal1kf6k1f9s73dch6ug/env)。帳密只填在這裡，勿貼到聊天、程式碼或 GitHub。

若使用 Gmail：

```text
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
EMAIL_HOST_USER=你的完整 Gmail 地址
EMAIL_HOST_PASSWORD=Google 應用程式密碼
DEFAULT_FROM_EMAIL=與上述 Gmail 相同的寄件地址
```

Google 帳號需先開啟兩步驟驗證，再到 [應用程式密碼](https://myaccount.google.com/apppasswords) 建立專用密碼；不要填一般登入密碼。部分公司或學校帳號不允許應用程式密碼，應改用管理者提供的 SMTP。

其他 SMTP 業者請依其指定主機、連接埠、TLS/SSL 與授權寄件者填寫。`EMAIL_USE_TLS` 和 `EMAIL_USE_SSL` 不可同時為 `True`。若使用 SSL 465，通常設 TLS=False、SSL=True；實際以業者文件為準。

儲存時選 **Save, rebuild, and deploy**，完成部署後設定才會生效。沒有寄信帳密時，工作流程會清楚回報尚未啟用，且不寫入寄送成功紀錄。

## 3. 驗證與啟用

1. 合併前執行 `python manage.py test --settings=config.test_settings`；測試只使用假信箱與測試資料庫。
2. 部署完成後，開啟 [GitHub Actions](https://github.com/yanfu837/expiry-tracker/actions/workflows/expiry-reminders.yml)，選 **Daily expiry reminders → Run workflow**，保留 `dry_run` 勾選。應看到日期、符合提醒門檻的數量及 `email_configured=True`；這一步不寄信、不建立提醒紀錄。
3. 使用自己可收信的測試帳號，新增到期日為台灣當天加 1、7、14、30 天的測試項目。若正式清單已有符合門檻的項目，真正執行時也會寄送那些提醒。
4. 再次執行工作流程，**取消** `dry_run`，確認收件匣或垃圾郵件夾。正常情況同一帳號會收到一封摘要，並包含四個提醒。
5. 當天再執行一次，已成功項目應顯示 `sent=0`。完成後刪除自己的測試項目。

工作流程在 main 上存在且 GitHub Actions 未被停用時，每天會自動執行。可在 Actions 查看結果；如失敗，檢查 Render 的 SMTP 設定、寄信業者限制與 GitHub 的工作流程失敗通知設定。要暫停時，在 Actions 選 **Disable workflow**；要恢復則選 **Enable workflow**。

## 限制與維護

- 提醒寄至項目所屬**帳號的 Email**。每個帳號獨立寄送，不附 PDF 或儲存來源連結。
- 提醒依台灣日期在恰好 30／14／7／1 天時建立；若當天排程完全未執行，可能漏掉該次提醒。免費 GitHub 排程不保證準時或必定執行。
- SMTP 已接受信件後、寫回成功紀錄前若發生程序中斷，重試仍可能重複；SMTP 與資料庫之間沒有原子交易。
- 待重試信件在之後的正常執行再次嘗試。可以用手動 Run workflow 提前重試，仍遵守退避時間及已成功紀錄。
- 每個工作流程最多處理 200 批、20 分鐘，超過時會失敗並保留待寄紀錄。清單量增加時應評估專用背景寄信服務。
- 網站、資料庫與 SMTP 必須持續可用。目前免費 PostgreSQL 會在 **2026/12/8 21:34（台灣時間）** 到期；到期前需處理續用，排程本身無法延長資料庫期限。

管理命令仍可使用：

```sh
python manage.py send_expiry_reminders --dry-run
python manage.py send_expiry_reminders
python manage.py cleanup_documents
```

附件刪檔佇列在刪除後與網站啟動時處理；需要時可另執行 cleanup_documents。免費排程端點只允許觸發提醒，不能讀取客戶項目或操作附件。
