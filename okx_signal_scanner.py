import os
import requests
import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
from datetime import datetime
from zoneinfo import ZoneInfo

TAIWAN_TZ = ZoneInfo('Asia/Taipei')
ATR_STOP_MULTIPLIER = 1.5
RISK_REWARD_RATIO = 2.5

def get_database_url():
    url = os.getenv("SUPABASE_DB_URL", "").strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url or None

def ensure_signal_outbox_table(engine):
    """確保 strategy_signal_outbox 資料表存在"""
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS strategy_signal_outbox (
                id BIGSERIAL PRIMARY KEY,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                direction TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
                candle_open_time TIMESTAMPTZ NOT NULL,
                signal_time_taipei TEXT NOT NULL,
                entry_price DOUBLE PRECISION NOT NULL,
                stop_loss DOUBLE PRECISION NOT NULL,
                take_profit DOUBLE PRECISION NOT NULL,
                atr DOUBLE PRECISION,
                ai_probability DOUBLE PRECISION,
                status TEXT NOT NULL DEFAULT 'pending' 
                    CHECK (status IN ('pending', 'processing', 'sent')),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                sent_at TIMESTAMPTZ,
                error_message TEXT,
                CONSTRAINT unique_signal_outbox UNIQUE(symbol, timeframe, candle_open_time, direction)
            )
        """))

def calculate_indicators_and_signals(df, tf):
    df = df.copy()
    df["EMA_21"] = df["close_price"].ewm(span=21, adjust=False).mean()
    df["EMA_50"] = df["close_price"].ewm(span=50, adjust=False).mean()
    df["EMA_100"] = df["close_price"].ewm(span=100, adjust=False).mean()

    # 趨勢通道計算
    window = 20 if tf in ["5m", "15m"] else 40
    num_dev = 1.5 if tf in ["5m", "15m"] else 2.0
    
    close = df["close_price"].astype(float).values
    n = len(close)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    for i in range(window - 1, n):
        y = close[i - window + 1:i + 1]
        x = np.arange(window)
        slope, intercept = np.polyfit(x, y, 1)
        center = slope * (window - 1) + intercept
        std = np.std(y - (slope * x + intercept))
        upper[i] = center + num_dev * std
        lower[i] = center - num_dev * std

    df["Trend_Resistance"] = upper
    df["Trend_Support"] = lower

    delta = df["close_price"].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["RSI"] = (100 - 100 / (1 + rs)).fillna(50)

    high_low = df["high_price"] - df["low_price"]
    high_close = (df["high_price"] - df["close_price"].shift()).abs()
    low_close = (df["low_price"] - df["close_price"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["ATR"] = tr.rolling(14).mean()
    df["Vol_SMA20"] = df["volume"].rolling(20).mean()

    body = (df["close_price"] - df["open_price"]).abs()
    candle_range = df["high_price"] - df["low_price"]
    df["Body_Ratio"] = (body / candle_range.replace(0, np.nan)).fillna(0)

    # 策略進場條件
    if tf in ["5m", "15m"]:
        df["Long_Signal"] = (
            (df["close_price"] > df["Trend_Resistance"])
            & df["RSI"].between(42, 85)
            & (df["volume"] > df["Vol_SMA20"] * 1.05)
            & (df["Body_Ratio"] > 0.3)
        )
        df["Short_Signal"] = (
            (df["close_price"] < df["Trend_Support"])
            & df["RSI"].between(15, 58)
            & (df["volume"] > df["Vol_SMA20"] * 1.05)
            & (df["Body_Ratio"] > 0.3)
        )
    else:
        df["Long_Signal"] = (
            (df["close_price"] > df["Trend_Resistance"])
            & df["RSI"].between(45, 80)
            & (df["volume"] > df["Vol_SMA20"])
        )
        df["Short_Signal"] = (
            (df["close_price"] < df["Trend_Support"])
            & df["RSI"].between(20, 55)
            & (df["volume"] > df["Vol_SMA20"])
        )

    return df

def scan_and_import_historical_signals(engine, table_name, symbol, timeframe):
    """從歷史 K 線中找出訊號，並批次匯入佇列 (ON CONFLICT DO NOTHING)"""
    try:
        query = text(f"""
            SELECT timeframe, open_time, open_price, high_price,
                   low_price, close_price, volume
            FROM {table_name}
            WHERE timeframe = :tf
            ORDER BY open_time ASC
        """)
        with engine.connect() as conn:
            df = pd.read_sql(query, con=conn, params={"tf": timeframe})

        if df.empty:
            return

        df["open_time"] = pd.to_datetime(df["open_time"], errors="coerce", utc=True)
        df["open_time"] = df["open_time"].dt.tz_convert(TAIWAN_TZ).dt.tz_localize(None)

        for col in ["open_price", "high_price", "low_price", "close_price", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df = calculate_indicators_and_signals(df, timeframe)
        signal_rows = df[df["Long_Signal"] | df["Short_Signal"]]
        if signal_rows.empty:
            return

        imported_count = 0
        with engine.begin() as conn:
            for _, row in signal_rows.iterrows():
                is_long = bool(row["Long_Signal"])
                direction = "LONG" if is_long else "SHORT"
                
                entry = float(row["close_price"])
                atr = float(row["ATR"]) if pd.notna(row["ATR"]) else 0.0
                if entry <= 0 or atr <= 0:
                    continue

                if direction == "LONG":
                    stop_loss = entry - atr * ATR_STOP_MULTIPLIER
                    take_profit = entry + (entry - stop_loss) * RISK_REWARD_RATIO
                else:
                    stop_loss = entry + atr * ATR_STOP_MULTIPLIER
                    take_profit = entry - (stop_loss - entry) * RISK_REWARD_RATIO

                candle_time = pd.Timestamp(row["open_time"]).to_pydatetime()
                if candle_time.tzinfo is None:
                    candle_time = candle_time.replace(tzinfo=TAIWAN_TZ)
                else:
                    candle_time = candle_time.astimezone(TAIWAN_TZ)

                current_taipei_time = datetime.now(TAIWAN_TZ)
                signal_time_text = current_taipei_time.strftime("%Y-%m-%d %H:%M:%S")

                result = conn.execute(text("""
                    INSERT INTO strategy_signal_outbox (
                        symbol, timeframe, direction, candle_open_time,
                        signal_time_taipei, entry_price, stop_loss, take_profit,
                        atr, ai_probability, status
                    )
                    VALUES (
                        :symbol, :timeframe, :direction, :candle_open_time,
                        :signal_time_text, :entry_price, :stop_loss, :take_profit,
                        :atr, 0.70, 'pending'
                    )
                    ON CONFLICT (symbol, timeframe, candle_open_time, direction)
                    DO NOTHING
                """), {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "direction": direction,
                    "candle_open_time": candle_time,
                    "signal_time_text": signal_time_text,
                    "entry_price": entry,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "atr": atr,
                })
                if result.rowcount > 0:
                    imported_count += 1

        if imported_count > 0:
            print(f"[{symbol} {timeframe}] 成功匯入 {imported_count} 筆新訊號至佇列！")

    except Exception as e:
        print(f"[{symbol} {timeframe}] 歷史訊號掃描匯入發生錯誤: {e}")

def process_outbox_and_notify(engine, webhook_url):
    """讀取佇列中 pending 的訊號，發送到 Discord 並更新為 sent"""
    with engine.begin() as conn:
        result = conn.execute(text("""
            SELECT id, symbol, timeframe, direction, signal_time_taipei,
                   entry_price, stop_loss, take_profit
            FROM strategy_signal_outbox
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT 10
        """))
        rows = result.fetchall()

        for row in rows:
            sig_id, symbol, tf, direction, sig_time, entry, sl, tp = row
            emoji = "🟢" if direction == "LONG" else "🔴"
            
            payload = {
                "content": f"🚨 **加密貨幣 AI 策略新訊號** 🚨",
                "embeds": [{
                    "title": f"{emoji} {symbol} ({tf}) - {direction}",
                    "color": 3066993 if direction == "LONG" else 15158332,
                    "fields": [
                        {"name": "⏰ 訊號時間 (TAIPEI)", "value": sig_time, "inline": False},
                        {"name": "💰 參考進場價", "value": f"`{entry:,.2f}`", "inline": True},
                        {"name": "🛡️ 建議止損價 (SL)", "value": f"`{sl:,.2f}`", "inline": True},
                        {"name": "🎯 建議止盈價 (TP)", "value": f"`{tp:,.2f}`", "inline": True},
                    ],
                    "footer": {"text": "OKX AI Strategy Scanner Bot"}
                }]
            }

            res = requests.post(webhook_url, json=payload)
            if res.status_code in [200, 204]:
                conn.execute(text("""
                    UPDATE strategy_signal_outbox
                    SET status = 'sent', sent_at = NOW()
                    WHERE id = :id
                """), {"id": sig_id})
                print(f"✅ 訊號 ID {sig_id} ({symbol} {tf} {direction}) 已成功發送到 Discord！")
            else:
                print(f"⚠️ 訊號 ID {sig_id} 發送失敗，HTTP Status: {res.status_code}")

def main():
    db_url = get_database_url()
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

    if not db_url:
        print("❌ 錯誤：未設定 SUPABASE_DB_URL")
        return
    if not webhook_url:
        print("❌ 錯誤：未設定 DISCORD_WEBHOOK_URL")
        return

    engine = create_engine(db_url)
    ensure_signal_outbox_table(engine)
    print("🚀 OKX 策略訊號掃描與派送系統啟動（涵蓋 BTC 與 ETH）...")

    pairs = [
        ("btc_contract_klines", "BTC"),
        ("eth_contract_klines", "ETH")
    ]
    timeframes = ["5m", "15m", "1h", "4h"]

    for table_name, symbol in pairs:
        for tf in timeframes:
            scan_and_import_historical_signals(engine, table_name, symbol, tf)

    process_outbox_and_notify(engine, webhook_url)
    print("✨ 本次 BTC 與 ETH 掃描與派送任務執行完畢。")

if __name__ == "__main__":
    main()
