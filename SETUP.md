# GitHub Pages 一次性設定與專案佈署步驟

## 步驟一：開啟 GitHub Pages 看板功能
1. 登入你的 GitHub 帳號並進入此專案的 Repository 頁面。
2. 點選右上方選單的 **Settings** (齒輪圖示)。
3. 在左側側邊欄中找到並點擊 **Pages**。
4. 在 **Build and deployment** 下方的 Source，選擇 **Deploy from a branch**。
5. 在 Branch 選項中，將分支切換為 **main**，並將資料夾保持為 **/ (root)**。
6. 點擊 **Save**。

> 💡 設定成功數分鐘後，你便可以透過以下網址查看公開的數據驗證網頁：
> `https://[你的GitHub帳號].github.io/[Repo名稱]/01_wu_trend_2026/report.html`

## 步驟二：開啟 GitHub Actions 的自動寫入權限
因為腳本在 Actions 容器中運行後需要自動將 `history.csv` 與 `report.html` 推回程式庫，我們必須開啟 Git 寫入權限：
1. 一樣在 Repository 的 **Settings** 頁面中，點選左側選單的 **Actions** -> **General**。
2. 滾動至最下方，找到 **Workflow permissions** 區塊。
3. 將預設的 Read permissions 修改為 **Read and write permissions**。
4. 點擊 **Save**。

## 步驟三：設定敏感資訊與祕鑰 (Secrets)
請至 **Settings** -> **Secrets and variables** -> **Actions**，點擊 **New repository secret** 分別加入以下三個密鑰，確保腳本能正常發信與調用 AI：
*   `GEMINI_API_KEY`：填入你的 Google Gemini API Key。
*   `EMAIL_USER`：填入發信用的 Gmail 帳號。
*   `EMAIL_PASS`：填入該 Gmail 的「應用程式密碼」（App Password，請勿直接填登入密碼）。
*   `RECEIVER_EMAIL`：填入你希望接收驗證報告的收件信箱。

## 步驟四：佈署檔案結構並執行首波測試
1. 在專案根目錄下建立名為 `.github/workflows` 的資料夾，並將 `01_verify.yml` 放進去。
2. 在專案根目錄下建立名為 `01_wu_trend_2026` 的資料夾。
3. 將 `topic_config.yaml`、`01_verify.py` 與本說明文件 `SETUP.md` 放入 `01_wu_trend_2026` 資料夾中。
4. 將所有變更 Push 回 GitHub 遠端程式庫。
5. 點選 Repo 上方的 **Actions** 標籤，點擊左側的 `01 wu_trend_2026 - 驗證任務`，並在右側點選 **Run workflow** 進行手動首航測試！
