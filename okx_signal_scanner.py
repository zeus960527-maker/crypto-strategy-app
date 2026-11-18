"""Cloud scanner: 讀取 Supabase K線資料表 (與 dashboard_app.py 100% 同步) -> 寫入 strategy_signal_outbox"""
import os
import logging
from datetime import timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import psycopg2
import lightgbm as lgb

TZ = ZoneInfo('Asia/Taipei')
DB_URL = os.getenv('SUPABASE_DB_URL', '').strip()
TABLES = {
    'BTC': 'btc_contract_klines',
    'ETH': 'eth_contract_klines',
}
TIMEFRAMES = ['5m', '15m', '1h', '4h']
ATR_STOP_MULTIPLIER = 1.5
RISK_REWARD_RATIO = 2.5
AI_THRESHOLD = 0.58

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')


def connect_db():
    if not DB_URL:
        raise RuntimeError('缺少 SUPABASE_DB_URL 環境變數')
    return psycopg2.connect(DB_URL, connect_timeout=15, sslmode='require')


def ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute('''
        CREATE TABLE IF NOT EXISTS strategy_signal_outbox (
            id BIGSERIAL PRIMARY KEY,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            direction TEXT NOT NULL CHECK (direction IN ('LONG','SHORT')),
            candle_open_time TIMESTAMPTZ NOT NULL,
            signal_time_taipei TEXT NOT NULL,
            entry_price DOUBLE PRECISION NOT NULL,
            stop_loss DOUBLE PRECISION NOT NULL,
            take_profit DOUBLE PRECISION NOT NULL,
            atr DOUBLE PRECISION,
            ai_probability DOUBLE PRECISION,
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','sent')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            sent_at TIMESTAMPTZ,
            error_message TEXT,
            UNIQUE(symbol, timeframe, candle_open_time, direction)
        )
        ''')
    conn.commit()


def load_historical_data_from_supabase(conn, table_name, symbol_name):
    """與 dashboard_app.py 完全一致的資料讀取與時區轉換邏輯"""
    allowed_tables = {"btc_contract_klines", "eth_contract_klines"}
    if table_name not in allowed_tables:
        raise ValueError(f"不合法的資料表名稱: {table_name}")

    sql = f"""
        SELECT timeframe, open_time, open_price, high_price,
               low_price, close_price, volume
        FROM {table_name}
        ORDER BY open_time DESC
        LIMIT 300
    """
    df = pd.read_sql(sql, con=conn)
    if df.empty:
        return df

    df = df.sort_values("open_time").reset_index(drop=True)
    df["symbol"] = symbol_name

    if pd.api.types.is_numeric_dtype(df["open_time"]):
        sample = df["open_time"].dropna()
        sample_value = sample.iloc[0] if not sample.empty else 0
        unit = "ms" if sample_value > 10_000_000_000 else "s"
        df["open_time"] = pd.to_datetime(
            df["open_time"], unit=unit, errors="coerce", utc=True
        )
    else:
        df["open_time"] = pd.to_datetime(
            df["open_time"], errors="coerce", utc=True
        )

    df["open_time"] = (
        df["open_time"].dt.tz_convert("Asia/Taipei").dt.tz_localize(None)
    )

    for col in ["open_price", "high_price", "low_price", "close_price", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df.dropna(subset=[
        "open_time", "open_price", "high_price",
        "low_price", "close_price", "volume"
    ])


def calculate_trendline_channel(df, window=30, num_dev=1.8):
    df = df.copy()
    close = df["close_price"].astype(float).values
    n = len(close)

    mid = np.full(n, np.nan)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)

    for i in range(window - 1, n):
        y = close[i - window + 1:i + 1]
        x = np.arange(window)
        slope, intercept = np.polyfit(x, y, 1)
        center = slope * (window - 1) + intercept
        std = np.std(y - (slope * x + intercept))
        mid[i] = center
        upper[i] = center + num_dev * std
        lower[i] = center - num_dev * std

    df["Trend_Mid"] = mid
    df["Trend_Resistance"] = upper
    df["Trend_Support"] = lower
    return df


def calculate_indicators(df, tf):
    df = df.copy()
    df["EMA_21"] = df["close_price"].ewm(span=21, adjust=False).mean()
    df["EMA_50"] = df["close_price"].ewm(span=50, adjust=False).mean()
    df["EMA_100"] = df["close_price"].ewm(span=100, adjust=False).mean()

    win = 20 if tf in ["5m", "15m"] else 40
    dev = 1.5 if tf in ["5m", "15m"] else 2.0
    df = calculate_trendline_channel(df, win, dev)

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
    return df


def build_candidate_signals(df, tf):
    df = df.copy()
    if tf in ["5m", "15m"]:
        df["Base_Long"] = (
            (df["close_price"] > df["Trend_Resistance"])
            & df["RSI"].between(42, 85)
            & (df["volume"] > df["Vol_SMA20"] * 1.05)
            & (df["Body_Ratio"] > 0.3)
        )
        df["Base_Short"] = (
            (df["close_price"] < df["Trend_Support"])
            & df["RSI"].between(15, 58)
            & (df["volume"] > df["Vol_SMA20"] * 1.05)
            & (df["Body_Ratio"] > 0.3)
        )
    else:
        df["Base_Long"] = (
            (df["close_price"] > df["Trend_Resistance"])
            & df["RSI"].between(45, 80)
            & (df["volume"] > df["Vol_SMA20"])
        )
        df["Base_Short"] = (
            (df["close_price"] < df["Trend_Support"])
            & df["RSI"].between(20, 55)
            & (df["volume"] > df["Vol_SMA20"])
        )

    df["vol_ratio"] = df["volume"] / df["Vol_SMA20"].replace(0, np.nan)
    df["body_ratio"] = df["Body_Ratio"]
    df["atr_ratio"] = df["ATR"] / df["close_price"].replace(0, np.nan)
    df["ema_diff"] = (df["EMA_21"] - df["EMA_50"]) / df["close_price"].replace(0, np.nan)

    df["future_close_3"] = df["close_price"].shift(-3)
    df["future_return_3"] = df["future_close_3"] / df["close_price"] - 1
    df["side"] = np.where(df["Base_Long"], 1, 0)
    df["directional_return_3"] = df["future_return_3"] * np.where(df["side"] == 1, 1, -1)

    df["Trade_Result"] = "非訊號"
    candidate = df["Base_Long"] | df["Base_Short"]
    matured = candidate & df["future_close_3"].notna()
    pending = candidate & df["future_close_3"].isna()
    df.loc[pending, "Trade_Result"] = "觀察中"

    successful = matured & (df["directional_return_3"] > 0.003)
    long_win = successful & df["Base_Long"]
    short_win = successful & df["Base_Short"]
    df.loc[long_win | short_win, "Trade_Result"] = "成功"
    df.loc[matured & ~(long_win | short_win), "Trade_Result"] = "未達標"
    return df


def train_and_score(df):
    feature_cols = ["RSI", "vol_ratio", "body_ratio", "atr_ratio", "ema_diff", "side"]
    candidates = df[df["Base_Long"] | df["Base_Short"]].copy()
    train_df = candidates.dropna(subset=feature_cols + ["future_return_3"]).copy()

    model = None
    if len(train_df) >= 30 and train_df["Trade_Result"].nunique() >= 2:
        train_df["target"] = (train_df["Trade_Result"] == "成功").astype(int)
        if train_df["target"].nunique() >= 2:
            model = lgb.LGBMClassifier(
                n_estimators=60,
                learning_rate=0.04,
                max_depth=3,
                num_leaves=7,
                class_weight="balanced",
                random_state=42,
                verbose=-1,
            )
            model.fit(train_df[feature_cols], train_df["target"])

    df = df.copy()
    df["AI_Prob"] = np.nan
    if model is not None and not candidates.empty:
        usable = df.loc[candidates.index, feature_cols].replace([np.inf, -np.inf], np.nan)
        valid = usable.notna().all(axis=1)
        if valid.any():
            probabilities = model.predict_proba(usable.loc[valid])[:, 1]
            df.loc[usable.loc[valid].index, "AI_Prob"] = probabilities

    if model is None:
        df["AI_Prob"] = np.where(df["Base_Long"] | df["Base_Short"], 0.70, np.nan)
        df["Long_Signal"] = df["Base_Long"]
        df["Short_Signal"] = df["Base_Short"]
    else:
        df["Long_Signal"] = df["Base_Long"] & (df["AI_Prob"] >= AI_THRESHOLD)
        df["Short_Signal"] = df["Base_Short"] & (df["AI_Prob"] >= AI_THRESHOLD)

    return df


def enqueue_latest_closed_signal(conn, df_tf, symbol, timeframe):
    """與 dashboard_app.py 完全相同的最新已收盤 K 棒寫入條件"""
    if df_tf.empty:
        return False

    latest = df_tf.iloc[-1]
    timeframe_delta = {
        "5m": pd.Timedelta(minutes=5),
        "15m": pd.Timedelta(minutes=15),
        "1h": pd.Timedelta(hours=1),
        "4h": pd.Timedelta(hours=4),
    }.get(timeframe)
    if timeframe_delta is None:
        return False

    candle_open = pd.Timestamp(latest["open_time"])
    now_taipei = pd.Timestamp.now(tz="Asia/Taipei").tz_localize(None)
    if candle_open + timeframe_delta > now_taipei:
        return False

    is_long = bool(latest.get("Long_Signal", False))
    is_short = bool(latest.get("Short_Signal", False))
    if not (is_long or is_short) or (is_long and is_short):
        return False

    entry = float(latest["close_price"])
    atr = float(latest["ATR"]) if pd.notna(latest["ATR"]) else 0.0
    if entry <= 0 or atr <= 0:
        return False

    direction = "LONG" if is_long else "SHORT"
    if direction == "LONG":
        stop_loss = entry - atr * ATR_STOP_MULTIPLIER
        take_profit = entry + (entry - stop_loss) * RISK_REWARD_RATIO
    else:
        stop_loss = entry + atr * ATR_STOP_MULTIPLIER
        take_profit = entry - (stop_loss - entry) * RISK_REWARD_RATIO

    candle_time = candle_open.to_pydatetime().replace(tzinfo=TZ)
    signal_time_text = candle_time.strftime("%Y-%m-%d %H:%M:%S")
    ai_prob = float(latest["AI_Prob"]) if pd.notna(latest.get("AI_Prob")) else None

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO strategy_signal_outbox (
                symbol, timeframe, direction, candle_open_time,
                signal_time_taipei, entry_price, stop_loss, take_profit,
                atr, ai_probability, status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
            ON CONFLICT (symbol, timeframe, candle_open_time, direction)
            DO NOTHING
            RETURNING id
        """, (
            symbol, timeframe, direction, candle_time.astimezone(timezone.utc),
            signal_time_text, entry, stop_loss, take_profit, atr, ai_prob
        ))
        inserted = cur.fetchone() is not None
    conn.commit()

    if inserted:
        logging.info("新訊號已入列：%s %s %s @ %.4f", symbol, timeframe, direction, entry)
    else:
        logging.info("重複訊號略過：%s %s %s (%s)", symbol, timeframe, direction, signal_time_text)
    return inserted


def main():
    total = 0
    with connect_db() as conn:
        ensure_table(conn)
        for symbol, table_name in TABLES.items():
            df = load_historical_data_from_supabase(conn, table_name, symbol)
            if df.empty:
                logging.warning("%s 資料表為空，略過掃描", symbol)
                continue

            for tf in TIMEFRAMES:
                try:
                    df_tf = (
                        df[df["timeframe"] == tf]
                        .sort_values("open_time")
                        .copy()
                        .reset_index(drop=True)
                    )
                    if df_tf.empty:
                        continue

                    df_tf = calculate_indicators(df_tf, tf)
                    df_tf = build_candidate_signals(df_tf, tf)
                    df_tf = train_and_score(df_tf)

                    if enqueue_latest_closed_signal(conn, df_tf, symbol, tf):
                        total += 1
                except Exception:
                    logging.exception("掃描失敗：%s %s", symbol, tf)

    logging.info("掃描完成；新增訊號 %d 筆", total)


if __name__ == '__main__':
    main()
