# 首頁 Gemini 行程管家設定

此 Worker 讓 GitHub Pages 首頁安全地呼叫 Gemini。Gemini 金鑰只放在 Cloudflare Worker 的 Secret，不會寫進網站原始碼。Worker 初步限制每個 IP 每分鐘約 5 次；此限制為近似值，且依 Cloudflare 地區分開計算。

## A. 建立 Gemini API 金鑰

1. 開啟 [Google AI Studio API Keys](https://aistudio.google.com/apikey)。
2. 建立或選取 API 金鑰。不要把金鑰貼到 GitHub、網站程式或聊天訊息。

## B. 在 Cloudflare 建立 Worker 並連到 GitHub

1. 登入 [Cloudflare Dashboard](https://dash.cloudflare.com/)。
2. 左側選 **Workers & Pages**，按 **Create application**，選 **Connect to Git** / **Import a repository**。
3. 授權 GitHub 後選 **aziza80110/Indigenous-Fest**。
4. Worker 名稱填 `indigenous-fest-gemini-planner`；專案根目錄（Root directory）填 `cloudflare/gemini-planner`。
5. 保留 Wrangler 設定自動偵測，確認讀到此資料夾的 `wrangler.toml`。如需 Build command，留空；如需 Deploy command，填 `npx wrangler deploy`，再按 **Deploy**。
6. 部署成功後，記下 Cloudflare 顯示的 Worker 網址，格式類似 `https://indigenous-fest-gemini-planner.<你的子網域>.workers.dev`。

## C. 將金鑰存進 Worker

1. 在 Cloudflare 打開剛建立的 Worker。
2. 進入 **Settings → Variables and Secrets**（部分介面顯示 **Settings → Variables**）。
3. 在 **Secrets** 按 **Add**，名稱填 `GEMINI_API_KEY`，值貼上 Google AI Studio 金鑰，按 **Deploy** 或 **Save and deploy**。
4. 確認 Worker 的 **Settings → Bindings** 有 `PLAN_RATE_LIMITER`；它由 `wrangler.toml` 設定。

## D. 將首頁指向 Worker

1. 在 GitHub 專案開啟 `planner-config.js`，按鉛筆編輯。
2. 把 `YOUR-SUBDOMAIN` 換成 Cloudflare 網址中的帳戶子網域，保留完整格式。例如 Worker 網址為 `https://indigenous-fest-gemini-planner.example-account.workers.dev`，設定值應為 `https://indigenous-fest-gemini-planner.example-account.workers.dev/api/plan`。
3. 按 **Commit changes**，等 GitHub Pages 部署完成。
4. 開首頁 → **AI 行程管家**，送一份短行程確認畫面有回覆。

## 注意

- 若未完成 C、D，首頁會提示尚未設定 Worker，或顯示連線錯誤。
- CORS 只接受 `https://aziza80110.github.io`。CORS 不等於完整存取控制。
- 請到 Google AI Studio 檢查 API 配額與可能費用。不要公開或分享 API 金鑰。
- 這個 AI 行程是草案，沒有即時搜尋或核實日期、價錢、營業時間、開放規範。使用前仍要查官方公告與業者資訊。
