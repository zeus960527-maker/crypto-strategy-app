import os
import requests
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class DiscordWorker:
    def __init__(self, webhook_url: str = None):
        self.webhook_url = webhook_url or os.getenv("DISCORD_WEBHOOK_URL")

    def send_message(self, content: str, embed: dict = None) -> bool:
        """發送文字訊息或 Embed 區塊到 Discord 頻道"""
        if not self.webhook_url:
            logger.error("未設定 Discord Webhook URL，無法發送訊息。")
            return False

        payload = {}
        if content:
            payload["content"] = content
        if embed:
            payload["embeds"] = [embed]

        try:
            response = requests.post(self.webhook_url, json=payload, timeout=10)
            if response.status_code in [200, 204]:
                logger.info("Discord 訊息發送成功。")
                return True
            else:
                logger.error(f"發送 Discord 失敗，狀態碼: {response.status_code}, 回應: {response.text}")
                return False
        except Exception as e:
            logger.exception(f"發送 Discord 訊息發生例外錯誤: {e}")
            return False

    def send_signal_alert(self, symbol: str, indicator: str, action: str, price: float, details: str = ""):
        """封裝交易訊號的 Embed 格式發送"""
        color = 3066993 if "BUY" in action.upper() or "LONG" in action.upper() else 15158332
        
        embed = {
            "title": f"🚨 交易訊號通知: {symbol}",
            "color": color,
            "fields": [
                {"name": "標的", "value": symbol, "inline": True},
                {"name": "策略/指標", "value": indicator, "inline": True},
                {"name": "動作", "value": f"**{action}**", "inline": True},
                {"name": "觸發價格", "value": f"`{price}`", "inline": True},
                {"name": "詳細資訊", "value": details or "無額外說明", "inline": False}
            ],
            "footer": {"text": "Crypto Signal Bridge Bot"}
        }
        return self.send_message(content="", embed=embed)

if __name__ == "__main__":
    # 測試執行
    worker = DiscordWorker()
    worker.send_message("🤖 Crypto Signal Bridge Worker 已啟動測試！")
