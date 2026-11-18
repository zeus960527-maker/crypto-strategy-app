"""
BTC / ETH AI 交易訊號監控
- OKX USDT 永續合約 K 線
- 週期：5m、15m、1h、4h
- Supabase PostgreSQL 儲存
- Discord Webhook 通知
- 台灣時間（Asia/Taipei）顯示通知與程式日誌
- 使用 Streamlit 儀表板相同的趨勢通道、RSI、成交量、K 棒實體比例與 AI 門檻
- 只用已收盤 K 棒判斷訊號

安全提醒：
1. 請將 DATABASE_URL、DISCORD_WEBHOOK_URL 設為環境變數，不要寫死在程式碼。
2. 你先前貼出的資料庫密碼及 Discord Webhook Token 已曝光，請先更換。
3. AI 機率是模型估計值，不代表實際勝率，也不保證獲利。
"""

import os
import sys
import time
import logging
import threading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed

import ccxt
import numpy as np
import pandas as pd
import psycopg2
import requests
import lightgbm as lgb

from psycopg2 import sql


# ============================================================
# 1. 基本設定
# ============================================================

TAIWAN_TZ = ZoneInfo("Asia/Taipei")

DATABASE_URL = os.getenv("SUPABASE_DB_URL", "").strip()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

TIMEFRAMES = ["5m", "15m", "1h", "4h"]

TARGETS = [
    ("ETH/USDT:USDT", "eth_contract_klines", "ETH"),
    ("BTC/USDT:USDT", "btc_contract_klines", "BTC"),
]

CANDLE_LIMIT = 200
MAX_WORKERS = 8
LOOP_INTERVAL_SECONDS = 15

AI_THRESHOLD = 0.58
AI_MODEL_REFRESH_SECONDS = 6 * 60 * 60
REQUIRE_AI_MODEL = False

# 風險參數
ATR_STOP_MULTIPLIER = 1.5
RISK_REWARD_RATIO = 2.5

# 趨勢通道參數
SHORT_TF_WINDOW = 20
LONG_TF_WINDOW = 40

# AI 標籤：未來三根 K 棒的最高/最低價變動門檻
AI_FUTURE_CANDLES = 3
AI_MOVE_THRESHOLD = 0.012

# table_name 只允許使用下方固定白名單，避免 SQL identifier 被外部輸入控制
ALLOWED_TABLES = {
    "eth_contract_klines",
    "btc_contract_klines",
}

last_notified_timestamps = {}
ai_models = {}

state_lock = threading.Lock()
ai_lock = threading.Lock()


# ============================================================
# 2. 台灣時間與日誌
# ============================================================

def taiwan_now():
    """取得目前台灣時間，包含 UTC+8 時區資訊。"""
    return datetime.now(TAIWAN_TZ)


def taiwan_time_text():
    return taiwan_now().strftime("%Y-%m-%d %H:%M:%S")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)


class TaiwanTimeFormatter(logging.Formatter):
    """讓 logging 的時間戳固定顯示台灣時間。"""

    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, TAIWAN_TZ)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%Y-%m-%d %H:%M:%S")


for handler in logging.getLogger().handlers:
    handler.setFormatter(
        TaiwanTimeFormatter("%(asctime)s %(levelname)s %(message)s")
    )


# ============================================================
# 3. 環境變數與連線
# ============================================================

def validate_config():
    missing = []

    if not DATABASE_URL:
        missing.append("SUPABASE_DB_URL")

    if not DISCORD_WEBHOOK_URL:
        missing.append("DISCORD_WEBHOOK_URL")

    if missing:
        raise RuntimeError(
            "缺少環境變數：" + ", ".join(missing)
            + "。請先設定環境變數再啟動程式。"
        )


def get_db_connection():
    return psycopg2.connect(
        DATABASE_URL,
        connect_timeout=10,
        options="-c statement_timeout=15000",
        sslmode="require",
    )


def initialize_notification_table():
    """建立跨 GitHub Actions 執行保留的推播去重紀錄。"""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS signal_notification_log (
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    candle_open_time TIMESTAMPTZ NOT NULL,
                    direction TEXT NOT NULL,
                    notified_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (symbol, timeframe, candle_open_time, direction)
                )
            """)
        conn.commit()
    logging.info("Supabase 推播去重資料表已就緒")


def was_signal_notified(symbol, timeframe, candle_time, direction):
    """查詢這根已收盤 K 棒的同方向訊號是否曾成功推播。"""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 1 FROM signal_notification_log
                WHERE symbol = %s AND timeframe = %s
                  AND candle_open_time = %s AND direction = %s
                LIMIT 1
            """, (symbol, timeframe, candle_time, direction))
            return cur.fetchone() is not None


def record_signal_notified(symbol, timeframe, candle_time, direction):
    """Discord 推播成功後才寫入去重紀錄。"""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO signal_notification_log
                    (symbol, timeframe, candle_open_time, direction)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (symbol, timeframe, candle_open_time, direction)
                DO NOTHING
            """, (symbol, timeframe, candle_time, direction))
        conn.commit()


def create_exchange():
    return ccxt.okx({
        "enableRateLimit": True,
        "timeout": 15000,
        "options": {
            "defaultType": "swap",
        },
    })


# ============================================================
# 4. Discord 通知
# ============================================================

def send_discord_alert(
    symbol,
    timeframe,
    signal_type,
    price,
    stop_loss,
    take_profit,
    ai_probability=None,
):
    """推播 Discord；只有收到 HTTP 200/204 才回傳 True。"""

    if not DISCORD_WEBHOOK_URL:
        logging.error("未設定 Discord Webhook URL")
        return False

    is_long = signal_type.startswith("LONG")
    color = 0x0ECB81 if is_long else 0xF6465D
    emoji = "🟢" if is_long else "🔴"

    if ai_probability is None:
        ai_text = "模型資料不足或尚未建立"
    else:
        ai_text = f"{ai_probability * 100:.1f}%（模型估計值，非實際勝率）"

    current_time = taiwan_now().strftime("%Y-%m-%d %H:%M:%S %Z")

    payload = {
        "embeds": [{
            "title": f"{emoji} AI 智能交易訊號：{signal_type}",
            "color": color,
            "fields": [
                {
                    "name": "交易對 / 週期",
                    "value": f"{symbol}/USDT | {timeframe}",
                    "inline": True,
                },
                {
                    "name": "做單方向",
                    "value": signal_type,
                    "inline": True,
                },
                {
                    "name": "台灣時間",
                    "value": current_time,
                    "inline": True,
                },
                {
                    "name": "進場參考價",
                    "value": f"{price:,.4f} USDT",
                    "inline": True,
                },
                {
                    "name": "建議止損 SL",
                    "value": f"{stop_loss:,.4f} USDT",
                    "inline": True,
                },
                {
                    "name": "建議止盈 TP",
                    "value": f"{take_profit:,.4f} USDT",
                    "inline": True,
                },
                {
                    "name": "AI 模型評分",
                    "value": ai_text,
                    "inline": False,
                },
            ],
            "footer": {
                "text": "BTC / ETH AI 交易訊號監控｜時間採用台灣時間 UTC+8"
            },
        }]
    }

    for attempt in range(1, 4):
        try:
            response = requests.post(
                DISCORD_WEBHOOK_URL,
                json=payload,
                timeout=10,
            )

            if response.status_code in (200, 204):
                logging.info(
                    "Discord 推播成功：%s %s %s",
                    symbol,
                    timeframe,
                    signal_type,
                )
                return True

            logging.error(
                "Discord 推播失敗（第 %s 次），HTTP %s：%s",
                attempt,
                response.status_code,
                response.text[:500],
            )

            # 遇到限流時稍等再試
            if response.status_code == 429:
                try:
                    wait_seconds = float(
                        response.json().get("retry_after", 2)
                    )
                except (ValueError, TypeError):
                    wait_seconds = 2
                time.sleep(min(wait_seconds, 10))
            elif response.status_code >= 500:
                time.sleep(attempt * 2)
            else:
                # 400/401/403/404 等通常是 URL 或設定錯誤，重試也不會改善
                return False

        except requests.RequestException as exc:
            logging.error("Discord 連線錯誤（第 %s 次）：%s", attempt, exc)
            time.sleep(attempt * 2)

    return False


def test_discord():
    """啟動時測試 Webhook 是否有效。"""
    if not DISCORD_WEBHOOK_URL:
        logging.warning("沒有 Discord Webhook，略過測試")
        return False

    try:
        response = requests.post(
            DISCORD_WEBHOOK_URL,
            json={
                "content": (
                    "🟢 BTC / ETH AI 交易監控系統啟動測試成功！\n"
                    f"台灣時間：{taiwan_time_text()}"
                )
            },
            timeout=10,
        )

        if response.status_code in (200, 204):
            logging.info("Discord Webhook 測試成功")
            return True

        logging.error(
            "Discord Webhook 測試失敗：HTTP %s %s",
            response.status_code,
            response.text[:500],
        )
        return False

    except requests.RequestException as exc:
        logging.error("Discord Webhook 測試連線失敗：%s", exc)
        return False


# ============================================================
# 5. Supabase 資料表寫入
# ============================================================

def save_ohlcv_to_database(connection, table_name, dataframe, timeframe):
    if table_name not in ALLOWED_TABLES:
        raise ValueError(f"不允許的資料表名稱：{table_name}")

    insert_query = sql.SQL("""
        INSERT INTO {table}
        (
            timeframe,
            open_time,
            open_price,
            high_price,
            low_price,
            close_price,
            volume
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (timeframe, open_time)
        DO UPDATE SET
            high_price = GREATEST(
                {table}.high_price,
                EXCLUDED.high_price
            ),
            low_price = LEAST(
                {table}.low_price,
                EXCLUDED.low_price
            ),
            close_price = EXCLUDED.close_price,
            volume = EXCLUDED.volume
    """).format(table=sql.Identifier(table_name))

    rows = []

    for row in dataframe.itertuples(index=False):
        # open_time 是 UTC aware datetime；資料庫欄位建議使用 TIMESTAMPTZ
        rows.append((
            timeframe,
            row.open_time.to_pydatetime(),
            float(row.open_price),
            float(row.high_price),
            float(row.low_price),
            float(row.close_price),
            float(row.volume),
        ))

    with connection.cursor() as cursor:
        cursor.executemany(insert_query, rows)

    connection.commit()


# ============================================================
# 6. 技術指標
# ============================================================

def calculate_trendline_channel(df, window=30, num_dev=1.8):
    """計算線性回歸趨勢通道。"""
    result = df.copy()

    close = result["close_price"].to_numpy(dtype=float)
    count = len(close)

    upper = np.empty(count, dtype=float)
    lower = np.empty(count, dtype=float)

    x = np.arange(window, dtype=float)

    for i in range(count):
        if i < window:
            upper[i] = close[i]
            lower[i] = close[i]
            continue

        y = close[i - window + 1:i + 1]

        slope, intercept = np.polyfit(x, y, 1)
        fitted = slope * x + intercept

        middle = fitted[-1]
        deviation = np.std(y - fitted)

        upper[i] = middle + num_dev * deviation
        lower[i] = middle - num_dev * deviation

    result["Trend_Resistance"] = upper
    result["Trend_Support"] = lower

    return result


def calculate_indicators(df, timeframe):
    """計算 EMA、RSI、ATR、成交量均線及 K 棒實體比例。"""
    result = df.copy()

    result["EMA_21"] = result["close_price"].ewm(
        span=21, adjust=False
    ).mean()

    result["EMA_50"] = result["close_price"].ewm(
        span=50, adjust=False
    ).mean()

    window = (
        SHORT_TF_WINDOW
        if timeframe in ("5m", "15m")
        else LONG_TF_WINDOW
    )

    deviation = (
        1.5
        if timeframe in ("5m", "15m")
        else 2.0
    )

    result = calculate_trendline_channel(
        result,
        window=window,
        num_dev=deviation,
    )

    delta = result["close_price"].diff()

    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()

    rs = gain / loss.replace(0, np.nan)
    result["RSI"] = 100 - (100 / (1 + rs))

    # 持續上漲時 RSI 設為 100；完全沒有漲跌時設為 50
    result.loc[(loss == 0) & (gain > 0), "RSI"] = 100
    result.loc[(loss == 0) & (gain == 0), "RSI"] = 50

    high_low = result["high_price"] - result["low_price"]
    high_close = (
        result["high_price"] - result["close_price"].shift()
    ).abs()
    low_close = (
        result["low_price"] - result["close_price"].shift()
    ).abs()

    true_range = pd.concat(
        [high_low, high_close, low_close],
        axis=1,
    ).max(axis=1)

    result["ATR"] = true_range.rolling(14).mean()
    result["Vol_SMA20"] = result["volume"].rolling(20).mean()

    body_size = (
        result["close_price"] - result["open_price"]
    ).abs()

    candle_range = (
        result["high_price"] - result["low_price"]
    ).replace(0, np.nan)

    result["Body_Ratio"] = body_size / candle_range

    result["vol_ratio"] = (
        result["volume"] / result["Vol_SMA20"].replace(0, np.nan)
    )

    result["atr_ratio"] = (
        result["ATR"] / result["close_price"].replace(0, np.nan)
    )

    result["ema_diff"] = (
        result["EMA_21"] - result["EMA_50"]
    ) / result["close_price"].replace(0, np.nan)

    return result


# ============================================================
# 7. AI 模型：完全沿用儀表板的單一 AI 判斷方式
# ============================================================

FEATURE_COLUMNS = [
    "RSI",
    "vol_ratio",
    "Body_Ratio",
    "atr_ratio",
    "ema_diff",
]


def train_or_get_ai_model(df, key):
    """依照儀表板邏輯訓練單一模型：預測未來三根 K 棒最高價是否上漲超過 1.2%。

    注意：這是上漲目標模型，儀表板原始程式會將同一個 AI 分數用於多單與空單，
    這裡為了保持判斷方式一致，也採用相同做法；它不是空單獨立勝率。
    """
    now = time.time()

    with ai_lock:
        cached = ai_models.get(key)
        if cached and now - cached["trained_at"] < AI_MODEL_REFRESH_SECONDS:
            return cached["model"]

    if len(df) < 60:
        logging.info("[%s] AI 訓練資料不足：%s 根", key, len(df))
        return None

    try:
        work = df.copy()

        # 與儀表板相同：未來三根 K 棒最高價相對目前收盤價上漲 > 1.2%
        future_max_gain = (
            work["high_price"].shift(-3).rolling(3).max()
            / work["close_price"] - 1
        )
        work["target"] = (future_max_gain > 0.012).astype(int)

        # 排除沒有完整未來三根 K 棒的尾端資料，避免將未知結果誤標為 0。
        valid_future = future_max_gain.notna()
        train_df = work.loc[valid_future].dropna(
            subset=FEATURE_COLUMNS + ["target"]
        )

        if len(train_df) < 30 or train_df["target"].nunique() < 2:
            logging.info(
                "[%s] AI 模型暫不可訓練：有效資料=%s，類別數=%s",
                key, len(train_df), train_df["target"].nunique()
            )
            return None

        model = lgb.LGBMClassifier(
            n_estimators=40,
            learning_rate=0.05,
            max_depth=3,
            verbosity=-1,
            n_jobs=1,
            random_state=42,
        )
        model.fit(train_df[FEATURE_COLUMNS], train_df["target"].astype(int))

        with ai_lock:
            ai_models[key] = {"model": model, "trained_at": now}

        logging.info("[%s] 儀表板相同邏輯的 AI 模型訓練完成，樣本=%s", key, len(train_df))
        return model

    except Exception:
        logging.exception("[%s] AI 模型訓練失敗", key)
        return None


def predict_dashboard_ai_probability(model, feature_row):
    """重現儀表板的預設分數 0.70 與 predict_proba()[0][1] 行為。"""
    probability = 0.70
    if model is not None:
        try:
            feature_frame = pd.DataFrame([feature_row], columns=FEATURE_COLUMNS)
            probability = float(model.predict_proba(feature_frame)[0][1])
        except Exception:
            logging.exception("AI 機率預測失敗，沿用儀表板預設分數 70%%")
    return probability


# ============================================================
# 8. 訊號判斷與推播
# ============================================================

def check_signal_and_notify(symbol, timeframe, df):
    """使用上傳的 Streamlit 儀表板同一套 Base_Long/Base_Short + AI 門檻。"""
    if df.empty or len(df) < 100:
        logging.info("[%s %s] K 線資料不足：%s", symbol, timeframe, len(df))
        return

    df = calculate_indicators(df, timeframe)
    if df.empty:
        return

    latest = df.iloc[-1]
    required_values = [
        latest["RSI"], latest["ATR"], latest["Trend_Resistance"],
        latest["Trend_Support"], latest["Vol_SMA20"],
        latest["Body_Ratio"], latest["vol_ratio"], latest["atr_ratio"],
        latest["ema_diff"],
    ]
    if any(pd.isna(value) for value in required_values):
        logging.info("[%s %s] 指標尚未準備完成", symbol, timeframe)
        return

    close_price = float(latest["close_price"])
    atr = float(latest["ATR"])
    rsi = float(latest["RSI"])
    volume = float(latest["volume"])
    volume_sma = float(latest["Vol_SMA20"])
    body_ratio = float(latest["Body_Ratio"])
    resistance = float(latest["Trend_Resistance"])
    support = float(latest["Trend_Support"])

    if close_price <= 0 or atr <= 0 or volume_sma <= 0:
        logging.info("[%s %s] 價格、ATR 或成交量均線無效", symbol, timeframe)
        return

    # 以下門檻逐項對應上傳的 Streamlit 程式。
    if timeframe in ("5m", "15m"):
        base_long = (
            close_price > resistance
            and 42 <= rsi <= 85
            and volume > volume_sma * 1.05
            and body_ratio > 0.3
        )
        base_short = (
            close_price < support
            and 15 <= rsi <= 58
            and volume > volume_sma * 1.05
            and body_ratio > 0.3
        )
    else:  # 1h / 4h
        base_long = (
            close_price > resistance
            and 45 <= rsi <= 80
            and volume > volume_sma * 1.0
        )
        base_short = (
            close_price < support
            and 20 <= rsi <= 55
            and volume > volume_sma * 1.0
        )

    if not base_long and not base_short:
        logging.info(
            "[%s %s] WAIT｜收盤=%0.4f 上軌=%0.4f 下軌=%0.4f RSI=%.1f 量比=%.2f 實體比=%.2f",
            symbol, timeframe, close_price, resistance, support,
            rsi, volume / volume_sma, body_ratio,
        )
        return

    key = f"{symbol}_{timeframe}"
    latest_time = latest["open_time"]

    model = train_or_get_ai_model(df, key)
    feature_row = {
        "RSI": rsi,
        "vol_ratio": float(latest["vol_ratio"]),
        "Body_Ratio": body_ratio,
        "atr_ratio": float(latest["atr_ratio"]),
        "ema_diff": float(latest["ema_diff"]),
    }
    ai_probability = predict_dashboard_ai_probability(model, feature_row)

    # 儀表板原始程式：多、空訊號都使用同一個 AI 分數，門檻 >= 0.58。
    if ai_probability < AI_THRESHOLD:
        logging.info(
            "[%s %s] Base 條件成立，但 AI 過濾：%.1f%% < %.1f%%",
            symbol, timeframe, ai_probability * 100, AI_THRESHOLD * 100,
        )
        return

    if base_long and base_short:
        logging.warning("[%s %s] 多空條件同時成立，為避免矛盾訊號，本根不推播", symbol, timeframe)
        return

    direction = "LONG" if base_long else "SHORT"
    signal_type = "LONG (AI 智能多單)" if base_long else "SHORT (AI 智能空單)"

    with state_lock:
        if last_notified_timestamps.get(key) == latest_time:
            logging.info("[%s %s] 本次執行已推播過，略過", symbol, timeframe)
            return

    try:
        if was_signal_notified(symbol, timeframe, latest_time, direction):
            logging.info("[%s %s] 此 K 棒訊號已在先前執行推播，略過", symbol, timeframe)
            return
    except Exception:
        logging.exception("[%s %s] 查詢推播去重紀錄失敗，為避免重複推播，本次略過", symbol, timeframe)
        return

    if base_long:
        stop_loss = round(close_price - atr * ATR_STOP_MULTIPLIER, 4)
        risk = close_price - stop_loss
        take_profit = round(close_price + risk * RISK_REWARD_RATIO, 4)
    else:
        stop_loss = round(close_price + atr * ATR_STOP_MULTIPLIER, 4)
        risk = stop_loss - close_price
        take_profit = round(close_price - risk * RISK_REWARD_RATIO, 4)

    candle_time_taiwan = latest_time.astimezone(TAIWAN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    logging.info(
        "[訊號成立] %s %s %s｜K棒台灣時間=%s｜收盤=%.4f｜RSI=%.1f｜量比=%.2f｜AI分數=%.1f%%",
        symbol, timeframe, signal_type, candle_time_taiwan,
        close_price, rsi, volume / volume_sma, ai_probability * 100,
    )

    success = send_discord_alert(
        symbol=symbol,
        timeframe=timeframe,
        signal_type=signal_type,
        price=close_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        ai_probability=ai_probability,
    )

    # 只有 Discord 確認成功才記錄，推播失敗可在下一輪重試。
    if success:
        try:
            record_signal_notified(symbol, timeframe, latest_time, direction)
            with state_lock:
                last_notified_timestamps[key] = latest_time
        except Exception:
            logging.exception("[%s %s] Discord 已推播，但寫入去重紀錄失敗", symbol, timeframe)


# ============================================================
# 9. 抓取 OKX K 線、儲存並分析
# ============================================================

def fetch_and_process_symbol(symbol_name, table_name, symbol_short, timeframe):
    exchange = None
    connection = None

    try:
        exchange = create_exchange()

        ohlcv = exchange.fetch_ohlcv(
            symbol=symbol_name,
            timeframe=timeframe,
            limit=CANDLE_LIMIT,
        )

        if not ohlcv:
            logging.warning("[%s %s] OKX 沒有回傳 K 線", symbol_short, timeframe)
            return

        df = pd.DataFrame(
            ohlcv,
            columns=[
                "timestamp",
                "open_price",
                "high_price",
                "low_price",
                "close_price",
                "volume",
            ],
        )

        # CCXT timestamp 是 Unix 毫秒，先正確轉成 UTC aware datetime
        df["open_time"] = pd.to_datetime(
            df["timestamp"],
            unit="ms",
            utc=True,
        )

        df = df.drop(columns=["timestamp"])
        df = df.sort_values("open_time").drop_duplicates(
            subset=["open_time"],
            keep="last",
        ).reset_index(drop=True)

        numeric_columns = [
            "open_price",
            "high_price",
            "low_price",
            "close_price",
            "volume",
        ]

        for column in numeric_columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")

        df = df.dropna(subset=numeric_columns)

        if len(df) < 100:
            logging.warning("[%s %s] 有效 K 線不足：%s", symbol_short, timeframe, len(df))
            return

        connection = get_db_connection()

        save_ohlcv_to_database(
            connection,
            table_name,
            df,
            timeframe,
        )

        # OKX 最後一根通常是尚未收盤的 K 棒。
        # 只分析倒數第二根，避免盤中訊號反覆出現/消失。
        closed_df = df.iloc[:-1].copy()

        if len(closed_df) >= 100:
            check_signal_and_notify(
                symbol_short,
                timeframe,
                closed_df,
            )

        logging.info(
            "[%s %s] 更新完成，資料 %s 根，台灣現在時間 %s",
            symbol_short,
            timeframe,
            len(df),
            taiwan_time_text(),
        )

    except Exception:
        logging.exception("[%s %s] 抓取或處理失敗", symbol_short, timeframe)

        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass

    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

        if exchange is not None:
            try:
                exchange.close()
            except Exception:
                pass


# ============================================================
# 10. 多執行緒工作
# ============================================================

def run_all_tasks():
    tasks = []

    for symbol_name, table_name, symbol_short in TARGETS:
        for timeframe in TIMEFRAMES:
            tasks.append(
                (symbol_name, table_name, symbol_short, timeframe)
            )

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [
            executor.submit(
                fetch_and_process_symbol,
                symbol_name,
                table_name,
                symbol_short,
                timeframe,
            )
            for symbol_name, table_name, symbol_short, timeframe in tasks
        ]

        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                logging.exception("工作執行緒發生未處理錯誤")


# ============================================================
# 11. 主程式
# ============================================================

def main():
    validate_config()

    logging.info("====================================================")
    logging.info("BTC / ETH AI 智能交易訊號監控系統啟動")
    logging.info("時區：Asia/Taipei（台灣時間 UTC+8）")
    logging.info("目前台灣時間：%s", taiwan_time_text())
    logging.info("監控週期：%s", ", ".join(TIMEFRAMES))
    logging.info("策略：儀表板相同判斷；AI 門檻 %.0f%%", AI_THRESHOLD * 100)
    logging.info("執行環境：%s", "GitHub Actions 單次排程" if os.getenv("GITHUB_ACTIONS") == "true" else "本機常駐")
    logging.info("====================================================")

    initialize_notification_table()

    # GitHub Actions 每次啟動只執行一輪；避免單一工作長時間佔用 runner。
    # 本機執行則維持原本常駐監控模式。
    if os.getenv("GITHUB_ACTIONS", "").lower() == "true":
        logging.info("開始本次排程監控，台灣時間：%s", taiwan_time_text())
        run_all_tasks()
        logging.info("本次排程完成，台灣時間：%s", taiwan_time_text())
        return

    # 本機才預設送出啟動測試，避免 Actions 每五分鐘洗版。
    if os.getenv("DISCORD_STARTUP_TEST", "false").lower() == "true":
        test_discord()

    try:
        while True:
            cycle_start = time.monotonic()
            logging.info("開始新一輪監控，台灣時間：%s", taiwan_time_text())
            run_all_tasks()
            elapsed = time.monotonic() - cycle_start
            sleep_seconds = max(1.0, LOOP_INTERVAL_SECONDS - elapsed)
            logging.info("本輪完成，耗時 %.1f 秒；%.1f 秒後開始下一輪", elapsed, sleep_seconds)
            time.sleep(sleep_seconds)
    except KeyboardInterrupt:
        logging.info("收到手動停止指令，程式結束。")
        sys.exit(0)


if __name__ == "__main__":
    main()
