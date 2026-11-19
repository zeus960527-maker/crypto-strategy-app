import os
import base64
import subprocess
from pathlib import Path

import streamlit as st
import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
from PIL import Image
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import lightgbm as lgb
from zoneinfo import ZoneInfo

# 檢查是否在 Flask 伺服器環境下執行 (Render 部署)
is_flask_mode = os.getenv("RENDER", False) or __name__ != "__main__"

if is_flask_mode:
    from flask import Flask, jsonify
    flask_app = Flask(__name__)

TAIWAN_TZ = ZoneInfo('Asia/Taipei')
ATR_STOP_MULTIPLIER = 1.5
RISK_REWARD_RATIO = 2.5


# ============================================================
# 0. 頁面設定、圖片與背景（含手機 RWD 響應式 CSS）
# ============================================================
BASE_DIR = Path(__file__).resolve().parent


def load_image_safe(filename):
    try:
        return Image.open(BASE_DIR / filename)
    except Exception:
        return None


def get_base64_image(filename):
    try:
        with open(BASE_DIR / filename, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        return None


icon_img = load_image_safe("hacker_icon.png")
bg_base64 = get_base64_image("background.png")

if not is_flask_mode:
    st.set_page_config(
        page_title="加密貨幣 AI 策略系統",
        page_icon=icon_img if icon_img else "💻",
        layout="wide",
    )

bg_css = ""
if bg_base64:
    bg_css = f"""
    .stApp {{
        background-image: url("data:image/png;base64,{bg_base64}");
        background-size: cover;
        background-position: center;
        background-repeat: no-repeat;
        background-attachment: fixed;
    }}
    [data-testid="stAppViewContainer"] > .main {{
        background-color: rgba(11, 14, 17, 0.78);
    }}
    .block-container {{ background-color: transparent !important; }}
    """

if not is_flask_mode:
    st.markdown(f"""
    <style>
    {bg_css}
    .block-container {{
        padding-top: 3.5rem !important;
        padding-bottom: 2rem !important;
        max-width: 1500px;
    }}
    h1 {{
        color: #fff !important;
        font-size: 2.6rem !important;
        line-height: 1.4 !important;
        font-weight: 800 !important;
        margin-top: 1rem !important;
        margin-bottom: 1.5rem !important;
        padding: 8px 0 !important;
        position: relative;
        z-index: 10;
        text-shadow: 0 0 8px rgba(0,0,0,.9), 0 0 16px rgba(0,0,0,.7),
                     0 2px 4px rgba(0,0,0,.8) !important;
    }}
    h2, h3, h4 {{ color: #f0f3f6 !important; }}
    p, li, label {{ color: #e5e7eb; }}
    div[data-testid="stRadio"] > div {{ flex-direction: row; gap: 12px; flex-wrap: wrap; }}
    .stTextInput input {{
        background-color: #1e2329 !important;
        color: #eaecef !important;
        border: 1px solid #2b313a !important;
    }}
    .stTextInput input:focus {{
        border: 1px solid #f0b90b !important;
        box-shadow: none !important;
    }}
    [data-testid="stStatusWidget"] {{ visibility: hidden; display: none; }}
    .js-plotly-plot .plotly .nsewdrag,
    .js-plotly-plot .plotly .draglayer,
    .js-plotly-plot .plotly .plot-container {{ cursor: crosshair !important; }}
    .intro-card {{
        background: rgba(20,25,35,.94);
        border: 1px solid #343b48;
        border-radius: 12px;
        padding: 24px;
        margin: 18px 0;
    }}
    .intro-card p, .feature-card p {{ color: #d1d5db; line-height: 1.8; }}
    .feature-card {{
        background: rgba(30,35,41,.94);
        border-radius: 10px;
        padding: 18px;
        min-height: 175px;
        height: 100%;
    }}

    @media(max-width: 768px) {{
        .block-container {{
            padding-left: 0.75rem !important;
            padding-right: 0.75rem !important;
            padding-top: 1.5rem !important;
        }}
        h1 {{
            font-size: 1.8rem !important;
        }}
        .js-plotly-plot, .plot-container {{
            height: 450px !important;
        }}
    }}
    </style>
    """, unsafe_allow_html=True)


# ============================================================
# 2. Supabase 連線與核心邏輯
# ============================================================
def get_database_url():
    url = os.getenv("SUPABASE_DB_URL", "").strip()
    if not url:
        try:
            url = str(st.secrets.get("SUPABASE_DB_URL", "")).strip()
        except Exception:
            url = ""

    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url or None


def get_db_engine():
    url = get_database_url()
    if not url:
        raise RuntimeError(
            "找不到 SUPABASE_DB_URL，請在 Streamlit Secrets 或環境變數設定。"
        )
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={"connect_timeout": 10},
    )


def ensure_signal_outbox_table():
    engine = get_db_engine()
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
                UNIQUE(symbol, timeframe, candle_open_time, direction)
            )
        """))


def load_historical_data(table_name, symbol_name):
    allowed_tables = {"btc_contract_klines", "eth_contract_klines"}
    if table_name not in allowed_tables:
        return pd.DataFrame()

    try:
        engine = get_db_engine()
        sql = text(f"""
            SELECT timeframe, open_time, open_price, high_price,
                   low_price, close_price, volume
            FROM {table_name}
            ORDER BY open_time DESC
            LIMIT 300
        """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, con=conn)

        if df.empty:
            return df
        df = df.sort_values("open_time").reset_index(drop=True)

    except Exception:
        return pd.DataFrame()

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

    return df, win, dev


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
    return df


# ============================================================
# 3. 核心掃npm與推送到 Discord 邏輯 (供 Flask 路由調用)
# ============================================================
def run_scanner_logic():
    """執行技術分析掃描，若發現最新訊號則寫入 Supabase outbox，並由 webhook 派送"""
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    engine = get_db_engine()
    ensure_signal_outbox_table()

    pairs = [
        ("btc_contract_klines", "BTC"),
        ("eth_contract_klines", "ETH")
    ]
    timeframes = ["5m", "15m", "1h", "4h"]

    scanned_count = 0
    for table_name, symbol in pairs:
        for tf in timeframes:
            df = load_historical_data(table_name, symbol)
            if df.empty:
                continue
            df_tf = df[df["timeframe"] == tf].sort_values("open_time").copy().reset_index(drop=True)
            if df_tf.empty:
                continue

            df_tf, _, _ = calculate_indicators(df_tf, tf)
            df_tf = build_candidate_signals(df_tf, tf)

            if df_tf.empty:
                continue

            latest = df_tf.iloc[-1]
            is_long = bool(latest.get("Base_Long", False))
            is_short = bool(latest.get("Base_Short", False))

            if not (is_long or is_short) or (is_long and is_short):
                continue

            direction = "LONG" if is_long else "SHORT"
            entry = float(latest["close_price"])
            atr = float(latest["ATR"]) if pd.notna(latest["ATR"]) else 0.0
            if entry <= 0 or atr <= 0:
                continue

            if direction == "LONG":
                stop_loss = entry - atr * ATR_STOP_MULTIPLIER
                take_profit = entry + (entry - stop_loss) * RISK_REWARD_RATIO
            else:
                stop_loss = entry + atr * ATR_STOP_MULTIPLIER
                take_profit = entry - (stop_loss - entry) * RISK_REWARD_RATIO

            candle_time = pd.Timestamp(latest["open_time"]).to_pydatetime()
            if candle_time.tzinfo is None:
                candle_time = candle_time.replace(tzinfo=TAIWAN_TZ)
            else:
                candle_time = candle_time.astimezone(TAIWAN_TZ)

            signal_time_text = datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d %H:%M:%S")

            try:
                with engine.begin() as conn:
                    res = conn.execute(text("""
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
                        "timeframe": tf,
                        "direction": direction,
                        "candle_open_time": candle_time,
                        "signal_time_text": signal_time_text,
                        "entry_price": entry,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "atr": atr,
                    })
                    if res.rowcount > 0:
                        scanned_count += 1
            except Exception:
                pass

    # 讀取 pending 訊號發送到 Discord
    sent_count = 0
    if webhook_url:
        with engine.begin() as conn:
            rows = conn.execute(text("""
                SELECT id, symbol, timeframe, direction, signal_time_taipei,
                       entry_price, stop_loss, take_profit
                FROM strategy_signal_outbox
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT 10
            """)).fetchall()

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
                        "footer": {"text": f"OKX Strategy Scanner | {signal_time_text}"}
                    }]
                }
                res = requests.post(webhook_url, json=payload)
                if res.status_code in [200, 204]:
                    conn.execute(text("""
                        UPDATE strategy_signal_outbox
                        SET status = 'sent', sent_at = NOW()
                        WHERE id = :id
                    """), {"id": sig_id})
                    sent_count += 1

    return f"Scanner executed. New signals: {scanned_count}, Sent to Discord: {sent_count}"


# ============================================================
# 4. 如果在 Flask 模式 (Render Web Service) 下運行
# ============================================================
if is_flask_mode:
    @flask_app.route("/")
    def flask_home():
        return "Crypto AI Strategy Web Service is running 24/7! 🚀"

    @flask_app.route("/run-scanner", methods=["GET", "POST"])
    def flask_run_scanner():
        try:
            msg = run_scanner_logic()
            return jsonify({"status": "success", "message": msg}), 200
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    # 供 Gunicorn 載入的 app 實例
    app = flask_app


# ============================================================
# 5. 如果在 Streamlit 模式下運行 (Streamlit Cloud 介面)
# ============================================================
if not is_flask_mode:
    st.session_state.setdefault("is_searched", False)
    st.session_state.setdefault("search_query", "")

    st.title("加密貨幣 AI 策略系統")

    with st.form("search_form"):
        col1, col2 = st.columns([4, 1])
        with col1:
            input_val = st.text_input(
                "🔍 請輸入加密貨幣名稱（例如 BTC 或 ETH）：",
                value=st.session_state.search_query,
                placeholder="BTC / ETH",
            )
        with col2:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            submitted = st.form_submit_button("送出查詢", use_container_width=True)

    if submitted:
        q = input_val.strip().upper()
        st.session_state.search_query = q
        st.session_state.is_searched = ("BTC" in q or "ETH" in q)

    st.markdown("---")

    if st.session_state.is_searched:
        query = st.session_state.search_query.upper()
        if "ETH" in query:
            table_name, symbol_name, pair_name = "eth_contract_klines", "ETH", "ETH-USDT-SWAP"
        else:
            table_name, symbol_name, pair_name = "btc_contract_klines", "BTC", "BTC-USDT-SWAP"

        selected_tf = st.radio("時間週期", ["5m", "15m", "1h", "4h"], horizontal=True)

        # 載入畫面與繪圖邏輯
        df = load_historical_data(table_name, symbol_name)
        if df.empty:
            st.warning("目前無法取得資料。")
        else:
            df_tf = df[df["timeframe"] == selected_tf].sort_values("open_time").copy().reset_index(drop=True)
            if df_tf.empty:
                st.warning(f"{selected_tf} 週期目前尚無資料。")
            else:
                df_tf, win, dev = calculate_indicators(df_tf, selected_tf)
                df_tf = build_candidate_signals(df_tf, selected_tf)
                
                # 簡單畫出圖表展示
                st.subheader(f"{pair_name} - {selected_tf} 技術分析與訊號圖表")
                st.line_chart(df_tf.set_index("open_time")[["close_price", "EMA_21", "Trend_Resistance", "Trend_Support"]])
    else:
        st.info("💡 請在上方查詢欄輸入 BTC 或 ETH，並按下「送出查詢」開始使用。")
