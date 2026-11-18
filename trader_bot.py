from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import sys
import ccxt
import numpy as np
import pandas as pd
import psycopg2
import requests
import lightgbm as lgb
from sklearn.model_selection import train_test_split

# 📌 你的 Supabase 連線網址
SUPABASE_DB_URL = "postgresql://postgres.ygzeqvcyqtvhvjrxhzdv:%4010233960527Ken@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres"

# 📌 Discord Webhook URL
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1571457845772165170/MZt_7HpNi9VD_peLI8ttwP_EXlAD5gAePTNSPff65whlJ-IMdv9qbPRnVy22npwjftI7"

# 記錄上一次發送過信號的時間戳，避免同一根 K 棒重複推播
last_notified_timestamps = {}

# 全域 AI 模型快取
ai_models = {}

def get_db_connection():
    return psycopg2.connect(
        SUPABASE_DB_URL,
        connect_timeout=10,
        options="-c statement_timeout=15000"
    )

def send_discord_alert(symbol, tf, signal_type, price, sl, tp, ai_prob):
    """發送 Discord Webhook 即時通知，並附帶 AI 勝率預測評分"""
    if not DISCORD_WEBHOOK_URL or "你的_DISCORD_WEBHOOK_URL" in DISCORD_WEBHOOK_URL:
        print("[警告] 未設定 Discord Webhook URL，無法發送通知！")
        return

    color = 0x0ECB81 if "LONG" in signal_type else 0xF6465D
    emoji = "🟢" if "LONG" in signal_type else "🔴"
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    payload = {
        "embeds": [{
            "title": f"{emoji} 【AI 智能雙軌策略信號通知】",
            "color": color,
            "fields": [
                {"name": "交易對 / 週期", "value": f"{symbol}/USDT | 週期: {tf}", "inline": True},
                {"name": "做單方向", "value": f"**{signal_type}**", "inline": True},
                {"name": "目前時間", "value": current_time, "inline": True},
                {"name": "進場點位 (Entry)", "value": f"{price:,.2f} USDT", "inline": True},
                {"name": "建議止損 (SL)", "value": f"{sl:,.2f} USDT", "inline": True},
                {"name": "建議止盈 (TP)", "value": f"{tp:,.2f} USDT", "inline": True},
                {"name": "🤖 AI 模型勝率評分", "value": f"**{ai_prob * 100:.1f}%**  高勝率信號", "inline": False},
            ],
            "footer": {"text": "24/7 AI 自動化交易與策略監控系統"}
        }]
    }

    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
        if response.status_code not in [200, 204]:
            print(f"[Discord] 推送失敗，狀態碼: {response.status_code}, 回應: {response.text}")
        else:
            print(f"[Discord] 成功推送 {symbol} {tf} 訊號！(AI 勝率: {ai_prob*100:.1f}%)")
    except Exception as e:
        print(f"[Discord] 推送異常: {e}")

def calculate_trendline_channel(df, window=30, num_dev=1.8):
    """計算動態線性回歸趨勢線與通道"""
    close = df['close_price'].values
    n = len(close)
    upper = np.zeros(n)
    lower = np.zeros(n)
    
    for i in range(n):
        if i < window:
            upper[i] = close[i]
            lower[i] = close[i]
        else:
            y = close[i-window+1:i+1]
            x = np.arange(window)
            slope, intercept = np.polyfit(x, y, 1)
            mid = slope * (window - 1) + intercept
            std = np.std(y - (slope * x + intercept))
            upper[i] = mid + num_dev * std
            lower[i] = mid - num_dev * std
            
    df['Trend_Resistance'] = upper  
    df['Trend_Support'] = lower     
    return df

def train_or_get_ai_model(df_tf, key):
    """輕量機器學習模型訓練：根據歷史 K 線自動訓練 LightGBM 分類器"""
    if key in ai_models:
        return ai_models[key]
    
    if len(df_tf) < 60:
        return None
    
    try:
        df = df_tf.copy()
        df['vol_ratio'] = df['volume'] / df['volume'].rolling(20).mean()
        body_size = (df['close_price'] - df['open_price']).abs()
        candle_range = df['high_price'] - df['low_price']
        df['body_ratio'] = body_size / candle_range.replace(0, 1)
        df['atr_ratio'] = df['ATR'] / df['close_price']
        df['ema_diff'] = (df['EMA_21'] - df['EMA_50']) / df['close_price']
        
        future_max_gain = df['high_price'].shift(-3).rolling(3).max() / df['close_price'] - 1
        df['target'] = (future_max_gain > 0.012).astype(int)
        
        feature_cols = ['RSI', 'vol_ratio', 'body_ratio', 'atr_ratio', 'ema_diff']
        train_df = df.dropna(subset=feature_cols + ['target'])
        
        if len(train_df) < 30:
            return None
            
        X = train_df[feature_cols]
        y = train_df['target']
        
        if len(y.unique()) < 2:
            return None
            
        model = lgb.LGBMClassifier(n_estimators=50, learning_rate=0.05, max_depth=3, verbose=-1)
        model.fit(X, y)
        
        ai_models[key] = model
        return model
    except Exception as e:
        print(f"[AI 訓練錯誤] {e}")
        return None

def check_signal_and_notify(symbol, tf, df_tf):
    """檢測多空信號，並透過 AI 模型預測勝率進行智能過濾"""
    global last_notified_timestamps
    if df_tf.empty or len(df_tf) < 50:
        return

    df_tf['EMA_21'] = df_tf['close_price'].ewm(span=21, adjust=False).mean()
    df_tf['EMA_50'] = df_tf['close_price'].ewm(span=50, adjust=False).mean()

    win = 20 if tf in ['5m', '15m'] else 40
    dev = 1.5 if tf in ['5m', '15m'] else 2.0
    df_tf = calculate_trendline_channel(df_tf, window=win, num_dev=dev)

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

    df_tf['Vol_SMA20'] = df_tf['volume'].rolling(window=20).mean()
    body_size = (df_tf['close_price'] - df_tf['open_price']).abs()
    candle_range = df_tf['high_price'] - df_tf['low_price']
    df_tf['Body_Ratio'] = body_size / candle_range.replace(0, 1)

    if tf in ['5m', '15m']:
        long_cond = (
            (df_tf['close_price'] > df_tf['Trend_Resistance']) & 
            (df_tf['RSI'] >= 42) & (df_tf['RSI'] <= 85) &
            (df_tf['volume'] > df_tf['Vol_SMA20'] * 1.05) &
            (df_tf['Body_Ratio'] > 0.3)
        )
        short_cond = (
            (df_tf['close_price'] < df_tf['Trend_Support']) & 
            (df_tf['RSI'] >= 15) & (df_tf['RSI'] <= 58) &
            (df_tf['volume'] > df_tf['Vol_SMA20'] * 1.05) &
            (df_tf['Body_Ratio'] > 0.3)
        )
    else:
        long_cond = (
            (df_tf['close_price'] > df_tf['Trend_Resistance']) & 
            (df_tf['RSI'] >= 45) & (df_tf['RSI'] <= 80) &
            (df_tf['volume'] > df_tf['Vol_SMA20'] * 1.0)
        )
        short_cond = (
            (df_tf['close_price'] < df_tf['Trend_Support']) & 
            (df_tf['RSI'] >= 20) & (df_tf['RSI'] <= 55) &
            (df_tf['volume'] > df_tf['Vol_SMA20'] * 1.0)
        )

    df_tf['Long_Signal'] = long_cond
    df_tf['Short_Signal'] = short_cond

    latest = df_tf.iloc[-1]
    latest_time = latest['open_time']
    latest_close = float(latest['close_price'])
    latest_atr = float(latest['ATR']) if not pd.isna(latest['ATR']) else latest_close * 0.015

    key = f"{symbol}_{tf}"
    
    ai_model = train_or_get_ai_model(df_tf, key)

    # 觸發做多信號
    if latest['Long_Signal'] and last_notified_timestamps.get(key) != latest_time:
        ai_prob = 0.75
        if ai_model is not None:
            try:
                features = pd.DataFrame([{
                    'RSI': latest['RSI'],
                    'vol_ratio': latest['volume'] / latest['Vol_SMA20'] if latest['Vol_SMA20'] > 0 else 1.0,
                    'body_ratio': latest['Body_Ratio'],
                    'atr_ratio': latest_atr / latest_close,
                    'ema_diff': (latest['EMA_21'] - latest['EMA_50']) / latest_close
                }])
                ai_prob = ai_model.predict_proba(features)[0][1]
            except Exception:
                pass

        if ai_prob >= 0.58:
            last_notified_timestamps[key] = latest_time
            sl = round(latest_close - (latest_atr * 1.5), 2)
            risk = latest_close - sl
            tp = round(latest_close + (risk * 2.5), 2)
            send_discord_alert(symbol, tf, "LONG (AI智能多單)", latest_close, sl, tp, ai_prob)

    # 觸發做空信號
    elif latest['Short_Signal'] and last_notified_timestamps.get(key) != latest_time:
        ai_prob = 0.75
        if ai_model is not None:
            try:
                features = pd.DataFrame([{
                    'RSI': latest['RSI'],
                    'vol_ratio': latest['volume'] / latest['Vol_SMA20'] if latest['Vol_SMA20'] > 0 else 1.0,
                    'body_ratio': latest['Body_Ratio'],
                    'atr_ratio': latest_atr / latest_close,
                    'ema_diff': (latest['EMA_21'] - latest['EMA_50']) / latest_close
                }])
                ai_prob = ai_model.predict_proba(features)[0][1]
            except Exception:
                pass

        if ai_prob >= 0.58:
            last_notified_timestamps[key] = latest_time
            sl = round(latest_close + (latest_atr * 1.5), 2)
            risk = sl - latest_close
            tp = round(latest_close - (risk * 2.5), 2)
            send_discord_alert(symbol, tf, "SHORT (AI智能空單)", latest_close, sl, tp, ai_prob)

def fetch_and_process_symbol(symbol_name, table_name, tf):
    """抓取數據並執行 AI 監控"""
    exchange = ccxt.okx({'enableRateLimit': True})
    
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            params = {'symbol': symbol_name, 'timeframe': tf, 'limit': 150}
            ohlcv = exchange.fetch_ohlcv(**params)
            if not ohlcv:
                return
            
            batch_data = [(tf, candle[0], candle[1], candle[2], candle[3], candle[4], candle[5]) for candle in ohlcv]
            
            sql = f"""
                INSERT INTO {table_name} 
                (timeframe, open_time, open_price, high_price, low_price, close_price, volume)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (timeframe, open_time) DO UPDATE SET 
                high_price = GREATEST({table_name}.high_price, EXCLUDED.high_price),
                low_price = LEAST({table_name}.low_price, EXCLUDED.low_price),
                close_price = EXCLUDED.close_price,
                volume = EXCLUDED.volume;
            """
            cursor.executemany(sql, batch_data)
            connection.commit()
            
            df_tf = pd.DataFrame(batch_data, columns=['timeframe', 'open_time', 'open_price', 'high_price', 'low_price', 'close_price', 'volume'])
            df_tf['open_time'] = pd.to_datetime(df_tf['open_time'], unit='ms')
            
            symbol_short = "ETH" if "ETH" in symbol_name else "BTC"
            check_signal_and_notify(symbol_short, tf, df_tf)
            
    except Exception as e:
        connection.rollback()
        print(f"[{symbol_name}-{tf}] 處理錯誤: {e}")
    finally:
        connection.close()

def run_all_tasks():
    targets = [
        ("ETH/USDT:USDT", "eth_contract_klines"),
        ("BTC/USDT:USDT", "btc_contract_klines")
    ]
    timeframes = ["5m", "15m", "1h", "4h"]
    
    tasks = []
    for symbol_name, table_name in targets:
        for tf in timeframes:
            tasks.append((symbol_name, table_name, tf))
            
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(fetch_and_process_symbol, sym, tbl, tf) for sym, tbl, tf in tasks]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"執行緒錯誤: {e}")

if __name__ == "__main__":
    print("================================================================")
    print(" BTC & ETH 雙軌策略【AI 智能機器學習預測 + 全面捕捉推播系統】")
    print("================================================================")
    
    try:
        while True:
            start_time = time.time()
            run_all_tasks()
            elapsed = time.time() - start_time
            time.sleep(max(1.0, 3.0 - elapsed))
    except KeyboardInterrupt:
        print("程式已手動終止。")
        sys.exit(0)
