"""Cloud scanner: OKX BTC/ETH perpetual, 5m/15m/1h/4h -> Supabase outbox."""
import os
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import ccxt
import numpy as np
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
import lightgbm as lgb

TZ = ZoneInfo('Asia/Taipei')
DB_URL = os.getenv('SUPABASE_DB_URL', '').strip()
SYMBOLS = {'BTC': 'BTC/USDT:USDT', 'ETH': 'ETH/USDT:USDT'}
TIMEFRAMES = ['5m', '15m', '1h', '4h']
LIMIT = int(os.getenv('OKX_CANDLE_LIMIT', '300'))
ATR_STOP_MULTIPLIER = 1.5
RISK_REWARD_RATIO = 2.5
AI_THRESHOLD = 0.58
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')


def connect_db():
    if not DB_URL:
        raise RuntimeError('缺少 SUPABASE_DB_URL')
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


def fetch_candles(exchange, symbol, timeframe):
    raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=LIMIT)
    if not raw:
        return pd.DataFrame()
    df = pd.DataFrame(raw, columns=['timestamp','open_price','high_price','low_price','close_price','volume'])
    df['open_time'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True).dt.tz_convert(TZ)
    df = df.drop(columns=['timestamp'])
    for col in ['open_price','high_price','low_price','close_price','volume']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    # OKX 通常會回傳尚未收盤的最後一根 K 棒，這裡明確排除。
    duration = pd.Timedelta(timeframe)
    now = pd.Timestamp.now(tz='UTC')
    df = df[df['open_time'].dt.tz_convert('UTC') + duration <= now]
    return df.dropna().sort_values('open_time').reset_index(drop=True)


def calculate_trendline_channel(df, window, num_dev):
    df = df.copy(); close = df['close_price'].astype(float).values; n = len(close)
    mid = np.full(n, np.nan); upper = np.full(n, np.nan); lower = np.full(n, np.nan)
    x = np.arange(window)
    for i in range(window - 1, n):
        y = close[i-window+1:i+1]
        slope, intercept = np.polyfit(x, y, 1)
        center = slope * (window - 1) + intercept
        std = np.std(y - (slope*x + intercept))
        mid[i] = center; upper[i] = center + num_dev*std; lower[i] = center - num_dev*std
    df['Trend_Mid'] = mid; df['Trend_Resistance'] = upper; df['Trend_Support'] = lower
    return df


def calculate_indicators(df, tf):
    df = df.copy()
    df['EMA_21'] = df.close_price.ewm(span=21, adjust=False).mean()
    df['EMA_50'] = df.close_price.ewm(span=50, adjust=False).mean()
    df['EMA_100'] = df.close_price.ewm(span=100, adjust=False).mean()
    win = 20 if tf in ('5m','15m') else 40
    dev = 1.5 if tf in ('5m','15m') else 2.0
    df = calculate_trendline_channel(df, win, dev)
    delta = df.close_price.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df['RSI'] = (100 - 100/(1+rs)).fillna(50)
    tr = pd.concat([
        df.high_price-df.low_price,
        (df.high_price-df.close_price.shift()).abs(),
        (df.low_price-df.close_price.shift()).abs()
    ], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()
    df['Vol_SMA20'] = df.volume.rolling(20).mean()
    body = (df.close_price-df.open_price).abs()
    candle_range = df.high_price-df.low_price
    df['Body_Ratio'] = (body/candle_range.replace(0,np.nan)).fillna(0)
    return df


def build_candidates(df, tf):
    df = df.copy()
    if tf in ('5m','15m'):
        df['Base_Long'] = ((df.close_price > df.Trend_Resistance) & df.RSI.between(42,85) & (df.volume > df.Vol_SMA20*1.05) & (df.Body_Ratio > .3))
        df['Base_Short'] = ((df.close_price < df.Trend_Support) & df.RSI.between(15,58) & (df.volume > df.Vol_SMA20*1.05) & (df.Body_Ratio > .3))
    else:
        df['Base_Long'] = ((df.close_price > df.Trend_Resistance) & df.RSI.between(45,80) & (df.volume > df.Vol_SMA20))
        df['Base_Short'] = ((df.close_price < df.Trend_Support) & df.RSI.between(20,55) & (df.volume > df.Vol_SMA20))
    df['vol_ratio'] = df.volume / df.Vol_SMA20.replace(0,np.nan)
    df['body_ratio'] = df.Body_Ratio
    df['atr_ratio'] = df.ATR / df.close_price.replace(0,np.nan)
    df['ema_diff'] = (df.EMA_21-df.EMA_50) / df.close_price.replace(0,np.nan)
    df['future_return_3'] = df.close_price.shift(-3)/df.close_price-1
    df['side'] = np.where(df.Base_Long,1,0)
    df['directional_return_3'] = df.future_return_3*np.where(df.side==1,1,-1)
    candidates = df.Base_Long | df.Base_Short
    matured = candidates & df.future_return_3.notna()
    df['target'] = np.where(matured & (df.directional_return_3 > .003),1,0)
    return df


def score_latest(df):
    feature_cols = ['RSI','vol_ratio','body_ratio','atr_ratio','ema_diff','side']
    candidates = df[df.Base_Long | df.Base_Short].copy()
    train = candidates.dropna(subset=feature_cols+['future_return_3'])
    model = None
    if len(train) >= 30 and train.target.nunique() >= 2:
        model = lgb.LGBMClassifier(n_estimators=60, learning_rate=.04, max_depth=3,
                                   num_leaves=7, class_weight='balanced', random_state=42, verbose=-1)
        model.fit(train[feature_cols], train.target.astype(int))
    latest = df.iloc[-1].copy()
    latest['AI_Prob'] = np.nan
    if bool(latest.Base_Long or latest.Base_Short):
        if model is None:
            latest['AI_Prob'] = .70  # 與 Dashboard 相同：樣本不足時使用策略回退分數
        else:
            x = pd.DataFrame([{c: latest[c] for c in feature_cols}]).replace([np.inf,-np.inf],np.nan)
            if x.notna().all(axis=1).iloc[0]:
                latest['AI_Prob'] = float(model.predict_proba(x)[0,1])
    if model is None:
        latest['Long_Signal'] = bool(latest.Base_Long)
        latest['Short_Signal'] = bool(latest.Base_Short)
    else:
        p = latest['AI_Prob']
        latest['Long_Signal'] = bool(latest.Base_Long and pd.notna(p) and p >= AI_THRESHOLD)
        latest['Short_Signal'] = bool(latest.Base_Short and pd.notna(p) and p >= AI_THRESHOLD)
    return latest


def enqueue_signal(conn, latest, asset, tf):
    is_long = bool(latest.Long_Signal); is_short = bool(latest.Short_Signal)
    if is_long == is_short:  # both false or both true
        return False
    entry = float(latest.close_price); atr = float(latest.ATR) if pd.notna(latest.ATR) else 0.0
    if entry <= 0 or atr <= 0:
        return False
    direction = 'LONG' if is_long else 'SHORT'
    if is_long:
        sl = entry - atr*ATR_STOP_MULTIPLIER; tp = entry + (entry-sl)*RISK_REWARD_RATIO
    else:
        sl = entry + atr*ATR_STOP_MULTIPLIER; tp = entry - (sl-entry)*RISK_REWARD_RATIO
    candle_time = pd.Timestamp(latest.open_time).to_pydatetime()
    if candle_time.tzinfo is None:
        candle_time = candle_time.replace(tzinfo=TZ)
    candle_time = candle_time.astimezone(timezone.utc)
    taipei_text = candle_time.astimezone(TZ).strftime('%Y-%m-%d %H:%M:%S')
    prob = float(latest.AI_Prob) if pd.notna(latest.AI_Prob) else None
    with conn.cursor() as cur:
        cur.execute('''
            INSERT INTO strategy_signal_outbox
            (symbol,timeframe,direction,candle_open_time,signal_time_taipei,entry_price,stop_loss,take_profit,atr,ai_probability,status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending')
            ON CONFLICT (symbol,timeframe,candle_open_time,direction) DO NOTHING
            RETURNING id
        ''',(asset,tf,direction,candle_time,taipei_text,entry,sl,tp,atr,prob))
        inserted = cur.fetchone() is not None
    conn.commit()
    if inserted:
        logging.info('新訊號已入列：%s %s %s @ %.4f',asset,tf,direction,entry)
    else:
        logging.info('重複訊號略過：%s %s %s %s',asset,tf,direction,taipei_text)
    return inserted


def main():
    exchange = ccxt.okx({'enableRateLimit': True, 'options': {'defaultType':'swap'}})
    exchange.load_markets()
    total = 0
    with connect_db() as conn:
        ensure_table(conn)
        for asset, market_symbol in SYMBOLS.items():
            for tf in TIMEFRAMES:
                try:
                    candles = fetch_candles(exchange, market_symbol, tf)
                    if len(candles) < 120:
                        logging.warning('%s %s K 線不足：%d',asset,tf,len(candles)); continue
                    analyzed = build_candidates(calculate_indicators(candles,tf),tf)
                    latest = score_latest(analyzed)
                    if enqueue_signal(conn,latest,asset,tf):
                        total += 1
                except Exception:
                    logging.exception('掃描失敗：%s %s',asset,tf)
    logging.info('掃描完成；新增訊號 %d 筆',total)

if __name__ == '__main__':
    main()
