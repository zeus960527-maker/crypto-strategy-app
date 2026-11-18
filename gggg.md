# BTC / ETH AI 訊號與 Discord 雲端通知

## 架構
1. `dashboard_app.py`：原 Streamlit 儀表板。最新一根 K 棒符合 LONG/SHORT 訊號時，將進場價、ATR 止損、2.5R 止盈、台灣時間與 AI 分數寫入 Supabase `strategy_signal_outbox`。
2. `discord_worker.py`：GitHub Actions 執行的雲端通知 worker，讀取 pending 訊號並傳送 Discord。
3. `.github/workflows/discord-worker.yml`：每 5 分鐘執行一次；不需要家中電腦開機。

## GitHub Secrets
在 GitHub Repository -> Settings -> Secrets and variables -> Actions 新增：
- `SUPABASE_DB_URL`
- `DISCORD_WEBHOOK_URL`

不要把資料庫密碼或 Webhook 寫進程式碼。已曝光的密碼與 Webhook 請先輪替。

## Streamlit Secrets
在 Streamlit Cloud 的 App -> Settings -> Secrets 設定：
```toml
SUPABASE_DB_URL = "postgresql://..."
```

## 部署步驟
1. 將 `dashboard_app.py`、`discord_worker.py`、`requirements.txt` 放在 GitHub 專案根目錄。
2. 將 `.github/workflows/discord-worker.yml` 放到相同路徑。
3. 在 GitHub Actions Secrets 設定兩個秘密。
4. Streamlit Cloud 的主程式指向 `dashboard_app.py`。
5. 在 GitHub Actions 頁面手動執行 `Discord Signal Worker` 測試，再等待排程。

## 重要
- 通知由雲端 GitHub Actions 傳送，不依賴你的電腦開機。
- GitHub 排程不是即時服務；`*/5` 通常約每 5 分鐘檢查一次，但 GitHub 高負載時可能延遲。
- Dashboard 僅推送最新訊號，不會把歷史圖表中的舊訊號全部補發。
- Worker 的 `processing` 狀態若因 runner 突然中斷而卡住，需在 Supabase 將該筆狀態改回 `pending` 後重試。
- Discord 外部 API 與資料庫無法構成真正的 exactly-once 交易；極端情況下若 Discord 成功但資料庫回寫失敗，可能重複通知。
- 止損為 1.5 ATR，止盈為 2.5 倍風險距離，這是訊號參考值，不會自動下單。
