# 將祭典旅遊通知排程遷移至 GitHub Actions

這份版本不使用 Firebase Functions、Cloud Scheduler 或 Firebase 計費方案。合併到 `main` 並完成 GitHub Secrets 後，Actions 每天臺北時間 09:17 執行；也可手動啟動預覽，但手動預覽不會寄信。

## 郵件內容與資料限制

- 祭典日期必須由管理者依主辦部落／官方公告確認後，填入 `automation/festivals.json` 的 ISO 日期與公告網址。檔案目前是空陣列，尚未確認的日期不會被猜測，也不會觸發寄信。
- 提前 15–30 天寄出行程規劃；提前 1–7 天另寄氣象更新。中央氣象署鄉鎮預報只在可用預報期間查詢；一個月前無法取得該祭期的精準天氣，不會冒稱已查到預報。
- 中央氣象署 API 提供天氣；Google Places 提供附近住宿與餐飲搜尋結果、地圖／網站連結及服務商有提供時的營業時間。Google Places 的價格級距不是房價，實際房價、交通票價及餐價需依日期、人數與業者頁面確認。信件附訂房與交通查詢連結，不保證即時庫存或票價。
- 只納入經官方來源核實且明確開放參訪的活動；祭儀開放、拍攝、服儀及路線以主辦部落當年公告為準。

## 合併後的啟用步驟

1. 合併此變更到 `main`。排程只會在預設分支上的 workflow 存在時啟動。
2. 在 GitHub 儲存庫的 **Settings → Secrets and variables → Actions → New repository secret** 新增：
   - `TRIP_EMAIL_TO`：你要收信的信箱（請在 Secrets 欄位內輸入，不要寫進程式或公開檔案）
   - `RESEND_API_KEY`：Resend API 金鑰（勿貼在聊天或程式碼）
   - `MAIL_FROM`：已在 Resend 驗證的寄件人，例如 `祭典旅遊規劃 <travel@你的已驗證網域>`
   - `CWA_AUTHORIZATION`：中央氣象署開放資料會員授權碼
   - `GOOGLE_PLACES_API_KEY`：Google Places API 金鑰，並在 Google Cloud 啟用 Places API、設定金額上限及限制金鑰可用 API。
3. （可選）於 **Settings → Secrets and variables → Actions → Variables** 新增 `TRIP_ORIGIN`，例如 `臺北車站`；未設定時使用臺北市。
4. 在 `automation/festivals.json` 新增經核實的祭典資料，例如：

```json
[
  {
    "id": "stable-unique-id-2027",
    "name": "祭典名稱",
    "tribe": "族名",
    "startDate": "2027-07-01",
    "location": "縣市鄉鎮與活動地點",
    "town": "縣市鄉鎮",
    "active": true,
    "sourceUrl": "https://主辦單位官方公告網址",
    "accessNote": "依主辦單位公告填寫開放方式"
  }
]
```

5. GitHub 的 **Settings → Actions → General → Workflow permissions** 必須允許 workflow token 寫入 contents；workflow 只要求 `contents: write`，用於把去重狀態保存到 `automation-state` 分支。
6. 到 **Actions → 祭典旅遊規劃通知** 按 **Run workflow**。這只是預覽符合排程條件的祭典，不能用來測試寄信。確認 Secrets、資料來源及寄件網域後，等每日排程寄送，或另行安排一筆經同意的測試資料與測試收件信箱。

## 去重與故障復原

排程會先把待寄送狀態提交至 `automation-state` 分支，再呼叫 Resend，最後提交成功／失敗狀態。工作流程使用 concurrency 鎖，避免同時執行。穩定的雜湊識別碼搭配 Resend `Idempotency-Key` 避免重複寄送；Resend 的去重保留期為 24 小時。公開狀態分支只存活動代碼、日期、階段及寄送狀態，不存收件地址或 API 金鑰。若寄送失敗，下一次排程可重試。

GitHub 的排程可能延遲；公開儲存庫連續 60 天沒有活動時，GitHub 會自動停用排程，需到 Actions 重新啟用。

## 本機測試

```bash
python3 -m pip install -r automation/requirements.txt
python3 -m unittest discover -s tests -v
python3 automation/send_travel_emails.py preview --today 2027-06-05
```

預覽使用空的 `festivals.json` 時應顯示 `dueCount: 0`，不需任何 API 金鑰，也不會寄信。
