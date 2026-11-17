import time
import requests
import pandas as pd
import numpy as np
from sqlalchemy import create_engine

# ==================== 參數設定 ====================
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1571457845772165170/MZt_7HpNi9VD_peLI8ttwP_EXlAD5gAePTNSPff65whlJ-IMdv9qbPRnVy22npwjftI7"  # 👈 請換成你的 Discord Webhook 網址

DB_USER = "postgres.ygzeqvcyqtvhvjrxhzdv"
DB_PASSWORD = "%4010233960527Ken"
DB_HOST = "aws-0-ap-northeast-1.pooler.supabase.com"
DB_PORT = "5432"
DB_NAME = "postgres"

def get_engine():
    db_url = f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    return create_engine(db_url)

def calculate_trendline_channel(df, window=20, num_dev=1.6):
    """計算動態線性回歸趨勢線與通道"""
    close = df['close_price'].values
    n = len(close)
    mid = np.zeros(n)
    upper = np.zeros(n)
    lower = np.zeros(n)
    
    for i in range(n):
        if i < window:
            mid[i] = close[i]
            upper[i] = close[i]
            lower[i] = close[i]
        else:
            y = close[i-window+1:i+1]
            x = np.arange(window)
            slope, intercept = np.polyfit(x, y, 1)
            mid[i] = slope * (window - 1) + intercept
            std = np.std(y - (slope * x + intercept))
            upper[i] = mid[i] + num_dev * std
            lower[i] = mid[i] - num_dev * std
            
    df['Trend_Resistance'] = upper  
    df['Trend_Support'] = lower     
    return df

def send_discord_alert(title, message, color_hex):
    """透過 Webhook 發送美觀的 Embed 訊息到 Discord"""
    if "YOUR_DISCORD_WEBHOOK_URL" in DISCORD_WEBHOOK_URL:
        print("【提示】請先填入你的 Discord Webhook 網址！")
        return

    # 將 Hex 顏色轉成十進位整數
    color_int = int(color_hex.lstrip('#'), 16)

    payload = {
        "embeds": [{
            "title": title,
            "description": message,
            "color": color_int,
            "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        }]
    }
    
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload)
        if response.status_code == 204:
            print("  -> Discord 信號發送成功！")
        else:
            print(f"  -> Discord 發送失敗，狀態碼: {response.status_code}, 回應: {response.text}")
    except Exception as e:
        print(f"  -> Discord 連線異常: {e}")

def check_market_signals():
    """從 Supabase 讀取資料並檢查是否產生最新交易信號"""
    print("--- 正在檢查市場策略信號 ---")
    try:
        engine = get_engine()
        query = "SELECT timeframe, open_time, open_price, high_price, low_price, close_price, volume FROM eth_contract_klines ORDER BY open_time DESC LIMIT 300"
        df = pd.read_sql(query, con=engine)
        if df.empty:
            return

        df = df.sort_values('open_time').reset_index(drop=True)

        # 這裡以 5m 小波段為例進行信號檢測
        df_tf = df[df['timeframe'] == '5m'].copy()
        if len(df_tf) < 50:
            return

        # 計算指標
        df_tf['EMA_10'] = df_tf['close_price'].ewm(span=10, adjust=False).mean()
        df_tf['EMA_21'] = df_tf['close_price'].ewm(span=21, adjust=False).mean()
        df_tf = calculate_trendline_channel(df_tf, window=20, num_dev=1.6)

        delta = df_tf['close_price'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df_tf['RSI'] = 100 - (100 / (1 + rs))

        high_low = df_tf['high_price'] - df_tf['low_price']
        high_close = (df_tf['high_price'] - df_tf['close_price'].shift()).abs()
        low_close = (df_tf['low_price'] - df_tf['close_price'].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df_tf['ATR'] = tr.rolling(window=14).mean()

        # 判斷多空信號
        df_tf['Long_Signal'] = (
            (df_tf['close_price'] > df_tf['Trend_Resistance']) & 
            (df_tf['EMA_10'] > df_tf['EMA_21']) &
            (df_tf['RSI'] >= 45) & (df_tf['RSI'] <= 85)
        )
        df_tf['Short_Signal'] = (
            (df_tf['close_price'] < df_tf['Trend_Support']) & 
            (df_tf['EMA_10'] < df_tf['EMA_21']) &
            (df_tf['RSI'] >= 15) & (df_tf['RSI'] <= 55)
        )

        latest = df_tf.iloc[-1]
        latest_close = latest['close_price']
        latest_atr = latest['ATR'] if not pd.isna(latest['ATR']) else latest_close * 0.015
        time_str = str(latest['open_time'])

        # 為了避免每一秒重複推播同一根 K 棒，我們可以用 Global 變數或記錄最後發送時間
        # 這裡示範即時判斷當前最新 K 棒是否符合條件
        if latest['Long_Signal']:
            sl = latest_close - (latest_atr * 1.5)
            tp = latest_close + (latest_atr * 1.5 * 2.5)
            msg = (
                f"**幣種:** ETH-USDT-SWAP (5m 小波段)\n"
                f"**方向:** 🟢 **做多 (LONG)**\n"
                f"**進場價:** `{latest_close:,.2f}`\n"
                f"**ATR 止損:** `{sl:,.2f}`\n"
                f"**ATR 止盈:** `{tp:,.2f}`\n"
                f"**時間:** {time_str}"
            )
            send_discord_alert("⚡ 策略買入信號觸發", msg, "#0ECB81")

        elif latest['Short_Signal']:
            sl = latest_close + (latest_atr * 1.5)
            tp = latest_close - (latest_atr * 1.5 * 2.5)
            msg = (
                f"**幣種:** ETH-USDT-SWAP (5m 小波段)\n"
                f"**方向:** 🔴 **做空 (SHORT)**\n"
                f"**進場價:** `{latest_close:,.2f}`\n"
                f"**ATR 止損:** `{sl:,.2f}`\n"
                f"**ATR 止盈:** `{tp:,.2f}`\n"
                f"**時間:** {time_str}"
            )
            send_discord_alert("⚡ 策略賣出信號觸發", msg, "#F6465D")
        else:
            print(f"  -> 目前 5m 無新信號 | 最新價: {latest_close:,.2f}")

    except Exception as e:
        print(f"檢查信號發生錯誤: {e}")

if __name__ == "__main__":
    print("==========================================")
    print(" ETH 策略 Discord 自動推播機器人已啟動")
    print("==========================================")
    
    while True:
        check_market_signals()
        # 每 30 秒檢查一次資料庫是否有符合信號的新 K 棒
        time.sleep(30)
