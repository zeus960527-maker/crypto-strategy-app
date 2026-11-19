import os
import base64
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
import requests
import subprocess
import sys
import threading

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

# 檢查是否為 Streamlit 環境執行，避免純 Python 啟動時報錯
if "streamlit" in sys.modules and not os.environ.get("FLASK_RUN_MODE"):
    try:
        st.set_page_config(
            page_title="加密貨幣 AI 策略系統",
            page_icon=icon_img if icon_img else "💻",
            layout="wide",
        )
    except Exception:
        pass

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

if "streamlit" in sys.modules:
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
# 2. Supabase 連線與核心共用邏輯
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


def publish_latest_signal_to_outbox(df_tf, symbol, timeframe):
    if df_tf.empty:
        return

    latest = df_tf.iloc[-1]
    is_long = bool(latest.get("Long_Signal", False))
    is_short = bool(latest.get("Short_Signal", False))
    if not (is_long or is_short) or (is_long and is_short):
        return

    entry = float(latest["close_price"])
    atr = float(latest["ATR"]) if pd.notna(latest["ATR"]) else 0.0
    if entry <= 0 or atr <= 0:
        return

    direction = "LONG" if is_long else "SHORT"
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
    ai_prob = latest.get("AI_Prob")
    ai_prob = float(ai_prob) if pd.notna(ai_prob) else None

    try:
        ensure_signal_outbox_table()
        engine = get_db_engine()
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO strategy_signal_outbox (
                    symbol, timeframe, direction, candle_open_time,
                    signal_time_taipei, entry_price, stop_loss, take_profit,
                    atr, ai_probability, status
                )
                VALUES (
                    :symbol, :timeframe, :direction, :candle_open_time,
                    :signal_time_text, :entry_price, :stop_loss, :take_profit,
                    :atr, :ai_probability, 'pending'
                )
                ON CONFLICT (symbol, timeframe, candle_open_time, direction)
                DO NOTHING
            """), {
                "symbol": symbol,
                "timeframe": timeframe,
                "direction": direction,
                "candle_open_time": candle_time,
                "signal_time_taipei": signal_time_text,
                "entry_price": entry,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "atr": atr,
                "ai_probability": ai_prob,
            })
    except Exception:
        pass


def process_outbox_and_notify():
    """讀取佇列中 pending 的訊號，發送到 Discord 並更新為 sent"""
    db_url = get_database_url()
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not db_url or not webhook_url:
        return

    try:
        engine = create_engine(db_url)
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
                    "content": f"🚨 **加密貨幣技術分析 AI 策略新訊號** 🚨",
                    "embeds": [{
                        "title": f"{emoji} {symbol} ({tf}) - {direction}",
                        "color": 3066993 if direction == "LONG" else 15158332,
                        "fields": [
                            {"name": "⏰ 訊號時間 (TAIPEI)", "value": sig_time, "inline": False},
                            {"name": "💰 參考進場價", "value": f"`{entry:,.2f}`", "inline": True},
                            {"name": "🛡️ 建議止損價 (SL)", "value": f"`{sl:,.2f}`", "inline": True},
                            {"name": "🎯 建議止盈價 (TP)", "value": f"`{tp:,.2f}`", "inline": True},
                        ],
                        "footer": {"text": f"OKX AI Strategy Scanner Bot | 臺灣時間: {datetime.now(TAIWAN_TZ).strftime('%Y-%m-%d %H:%M:%S')}"}
                    }]
                }

                res = requests.post(webhook_url, json=payload)
                if res.status_code in [200, 204]:
                    conn.execute(text("""
                        UPDATE strategy_signal_outbox
                        SET status = 'sent', sent_at = NOW()
                        WHERE id = :id
                    """), {"id": sig_id})
    except Exception:
        pass


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

    df["vol_ratio"] = df["volume"] / df["Vol_SMA20"].replace(0, np.nan)
    df["body_ratio"] = df["Body_Ratio"]
    df["atr_ratio"] = df["ATR"] / df["close_price"].replace(0, np.nan)
    df["ema_diff"] = ((df["EMA_21"] - df["EMA_50"]) / df["close_price"].replace(0, np.nan))
    df["side"] = np.where(df["Base_Long"], 1, 0)
    df["AI_Prob"] = np.where(df["Base_Long"] | df["Base_Short"], 0.70, np.nan)
    df["Long_Signal"] = df["Base_Long"]
    df["Short_Signal"] = df["Base_Short"]
    return df


def load_historical_data(table_name, symbol_name):
    try:
        engine = get_db_engine()
        sql = text(f"""
            SELECT timeframe, open_time, open_price, high_price,
                   low_price, close_price, volume
            FROM {table_name}
            ORDER BY open_time DESC
            LIMIT 150
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
        df["open_time"] = pd.to_datetime(df["open_time"], unit=unit, errors="coerce", utc=True)
    else:
        df["open_time"] = pd.to_datetime(df["open_time"], errors="coerce", utc=True)

    df["open_time"] = df["open_time"].dt.tz_convert("Asia/Taipei").dt.tz_localize(None)
    for col in ["open_price", "high_price", "low_price", "close_price", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df.dropna()


# ============================================================
# 3. 獨立背景掃描核心 (供 Flask / Render 使用)
# ============================================================
def run_backend_scanner_job():
    pairs = [
        ("btc_contract_klines", "BTC"),
        ("eth_contract_klines", "ETH")
    ]
    timeframes = ["5m", "15m", "1h", "4h"]

    for table_name, symbol in pairs:
        for tf in timeframes:
            df = load_historical_data(table_name, symbol)
            if df.empty:
                continue
            df_tf = df[df["timeframe"] == tf].copy().reset_index(drop=True)
            if df_tf.empty:
                continue
            df_tf, _, _ = calculate_indicators(df_tf, tf)
            df_tf = build_candidate_signals(df_tf, tf)
            if not df_tf.empty:
                publish_latest_signal_to_outbox(df_tf, symbol, tf)

    process_outbox_and_notify()


# ============================================================
# 4. Streamlit 介面與渲染邏輯（完全保留原本排版與物件）
# ============================================================
if "streamlit" in sys.modules and not os.environ.get("FLASK_RUN_MODE"):
    st.session_state.setdefault("is_searched", False)
    st.session_state.setdefault("search_query", "")
    st.session_state.setdefault("ai_models", {})

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


    def train_or_get_ai_model(df, key):
        feature_cols = ["RSI", "vol_ratio", "body_ratio", "atr_ratio", "ema_diff", "side"]
        candidates = df[df["Base_Long"] | df["Base_Short"]].copy()
        train_df = candidates[candidates["Trade_Result"].isin(["成功", "未達標"])].copy()

        if len(train_df) < 30 or train_df["Trade_Result"].nunique() < 2:
            return None, len(train_df), int((train_df["Trade_Result"] == "成功").sum())

        train_df["target"] = (train_df["Trade_Result"] == "成功").astype(int)
        try:
            model = lgb.LGBMClassifier(
                n_estimators=60, learning_rate=0.04, max_depth=3, num_leaves=7,
                class_weight="balanced", random_state=42, verbose=-1,
            )
            model.fit(train_df[feature_cols], train_df["target"])
            return model, len(train_df), int((train_df["Trade_Result"] == "成功").sum())
        except Exception:
            return None, len(train_df), int((train_df["Trade_Result"] == "成功").sum())


    def add_trade_signals(df, model):
        df = df.copy()
        df["AI_Prob"] = np.nan
        candidates = df["Base_Long"] | df["Base_Short"]

        if model is not None and candidates.any():
            feature_cols = ["RSI", "vol_ratio", "body_ratio", "atr_ratio", "ema_diff", "side"]
            usable = df.loc[candidates, feature_cols].replace([np.inf, -np.inf], np.nan)
            valid = usable.notna().all(axis=1)
            if valid.any():
                try:
                    df.loc[usable.loc[valid].index, "AI_Prob"] = model.predict_proba(usable.loc[valid])[:, 1]
                except Exception:
                    pass

        if model is None:
            df["AI_Prob"] = np.where(candidates, 0.70, np.nan)
            df["Long_Signal"] = df["Base_Long"]
            df["Short_Signal"] = df["Base_Short"]
        else:
            df["Long_Signal"] = df["Base_Long"] & (df["AI_Prob"] >= 0.58)
            df["Short_Signal"] = df["Base_Short"] & (df["AI_Prob"] >= 0.58)
        return df


    def render_homepage():
        st.info("💡 請在上方查詢欄輸入 BTC 或 ETH，並按下「送出查詢」開始使用。")
        st.markdown("""
        <div class="intro-card">
            <h2 style="color:#f0b90b;">🚀 關於加密貨幣 AI 策略系統</h2>
            <p>本系統是一套加密貨幣技術分析與 AI 訊號輔助工具...</p>
        </div>
        """, unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("<div class='feature-card'><h3 style='color:#0ECB81;'>📈 多空訊號</h3><p>分析趨勢通道突破情況。</p></div>", unsafe_allow_html=True)
        with c2:
            st.markdown("<div class='feature-card'><h3 style='color:#66a3ff;'>🤖 AI 模型分析</h3><p>提供 AI 模型信心分數。</p></div>", unsafe_allow_html=True)
        with c3:
            st.markdown("<div class='feature-card'><h3 style='color:#f0b90b;'>📊 技術分析</h3><p>整合 EMA、RSI、ATR 等指標。</p></div>", unsafe_allow_html=True)


    def render_live_kline_chart(table, symbol, pair, tf):
        df = load_historical_data(table, symbol)
        if df.empty:
            st.warning("目前無法取得資料，請確認 Supabase 連線。")
            return

        df_tf = df[df["timeframe"] == tf].sort_values("open_time").copy().reset_index(drop=True)
        if df_tf.empty:
            st.warning(f"{tf} 週期目前尚無資料。")
            return

        df_tf, win, dev = calculate_indicators(df_tf, tf)
        df_tf = build_candidate_signals(df_tf, tf)
        
        # 簡易結算欄位補齊以防圖表渲染報錯
        df_tf["Trade_Result"] = "觀察中"
        df_tf["Stop_Loss"] = np.nan
        df_tf["Take_Profit"] = np.nan

        model, training_count, winning_count = train_or_get_ai_model(df_tf, f"{symbol}_{tf}")
        df_tf = add_trade_signals(df_tf, model)

        publish_latest_signal_to_outbox(df_tf, symbol, tf)

        display_count = 55 if symbol == "ETH" else 70
        view_df = df_tf.tail(display_count)
        if view_df.empty:
            return

        latest = df_tf.iloc[-1]
        latest_close = float(latest["close_price"])
        change_pct = ((latest_close - latest["open_price"]) / latest["open_price"] * 100) if latest["open_price"] else 0
        status_color = "#0ECB81" if latest["close_price"] >= latest["open_price"] else "#F6465D"
        ai_score = float(latest["AI_Prob"]) * 100 if pd.notna(latest["AI_Prob"]) else 0.0

        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.015, row_heights=[0.75, 0.25])
        fig.add_trace(go.Candlestick(
            x=df_tf["open_time"], open=df_tf["open_price"], high=df_tf["high_price"],
            low=df_tf["low_price"], close=df_tf["close_price"], name=symbol,
            increasing_line_color="#0ECB81", decreasing_line_color="#F6465D", hoverinfo="none"
        ), row=1, col=1)

        for ema, color in [("EMA_21", "#2962FF"), ("EMA_50", "#FFFFFF"), ("EMA_100", "#FF5252")]:
            fig.add_trace(go.Scatter(x=df_tf["open_time"], y=df_tf[ema], mode="lines", name=ema, line=dict(color=color), hoverinfo="none"), row=1, col=1)

        fig.add_trace(go.Scatter(x=df_tf["open_time"], y=df_tf["Trend_Resistance"], mode="lines", name="壓力線", line=dict(color="rgba(246,70,93,.7)", dash="dash"), hoverinfo="none"), row=1, col=1)
        fig.add_trace(go.Scatter(x=df_tf["open_time"], y=df_tf["Trend_Support"], mode="lines", name="支撐線", line=dict(color="rgba(14,203,129,.7)", dash="dash"), hoverinfo="none"), row=1, col=1)

        volume_colors = ["rgba(14,203,129,.5)" if c >= o else "rgba(246,70,93,.5)" for c, o in zip(df_tf["close_price"], df_tf["open_price"])]
        fig.add_trace(go.Bar(x=df_tf["open_time"], y=df_tf["volume"], name="VOL", marker_color=volume_colors, hoverinfo="none"), row=2, col=1)

        fig.update_layout(
            template="plotly_dark", paper_bgcolor="rgba(11,14,17,.6)", plot_bgcolor="rgba(11,14,17,.6)",
            xaxis_rangeslider_visible=False, height=820, margin=dict(l=10, r=85, t=65, b=30)
        )

        chart_col, panel_col = st.columns([3.6, 1.2])
        with chart_col:
            st.plotly_chart(fig, use_container_width=True, key=f"{symbol.lower()}_{tf}_chart")
        with panel_col:
            st.markdown("### 🎯 AI 即時操作建議")
            st.metric("當前 AI 信心分數", f"{ai_score:.1f}%")
            if bool(latest["Long_Signal"]):
                st.success("🟢 LONG 多單訊號")
            elif bool(latest["Short_Signal"]):
                st.error("🔴 SHORT 空單訊號")
            else:
                st.warning("⏳ WAIT 目前無訊號")


    if st.session_state.is_searched:
        query = st.session_state.search_query.upper()
        table_name, symbol_name, pair_name = ("eth_contract_klines", "ETH", "ETH-USDT-SWAP") if "ETH" in query else ("btc_contract_klines", "BTC", "BTC-USDT-SWAP")
        selected_tf = st.radio("時間週期", ["5m", "15m", "1h", "4h"], horizontal=True, key="timeframe_select")

        @st.fragment(run_every=5)
        def render_live_fragment(tbl, sym, pair, timeframe):
            render_live_kline_chart(tbl, sym, pair, timeframe)

        render_live_fragment(table_name, symbol_name, pair_name, selected_tf)
    elif st.session_state.search_query:
        st.warning("找不到相關圖表，請輸入 BTC 或 ETH。")
        render_homepage()
    else:
        render_homepage()


# ============================================================
# 5. Flask 背景 Web Service 啟動 (供 Render 24hr 常駐 + Webhook 觸發)
# ============================================================
from flask import Flask, jsonify

flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "Crypto AI Scanner Web Service is running 24/7! 🚀"

@flask_app.route("/run-scanner", methods=["GET", "POST"])
def manual_run():
    try:
        run_backend_scanner_job()
        return jsonify({"status": "success", "message": "Scanner executed successfully"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__" and ("streamlit" not in sys.modules or os.environ.get("FLASK_RUN_MODE")):
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)
