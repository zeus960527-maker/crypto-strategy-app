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
from flask import Flask, jsonify

TAIWAN_TZ = ZoneInfo('Asia/Taipei')
ATR_STOP_MULTIPLIER = 1.5
RISK_REWARD_RATIO = 2.5


# ============================================================
# 0. 頁面設定、圖片與背景（含手機 RWD 響應式 CSS）與 Flask 路由 (保持 Render 活躍)
# ============================================================
BASE_DIR = Path(__file__).resolve().parent

app = Flask(__name__)

@app.route("/")
def home():
    return "Crypto AI Streamlit Web App is running 24/7! 🚀"

@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "healthy"}), 200


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

/* 手機版 RWD 自動適配樣式 */
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
# 1. Session State 與標題搜尋列
# ============================================================
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


# ============================================================
# 2. Supabase 連線（擴充載入歷史 K 線筆數以提升 AI 訓練量）
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


@st.cache_resource
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


def load_historical_data(table_name, symbol_name):
    allowed_tables = {"btc_contract_klines", "eth_contract_klines"}
    if table_name not in allowed_tables:
        st.error("資料表名稱不合法。")
        return pd.DataFrame()

    try:
        engine = get_db_engine()
        # 將 LIMIT 從 300 擴充至 1000 筆，提供機器學習更充足的訓練樣本
        sql = text(f"""
            SELECT timeframe, open_time, open_price, high_price,
                   low_price, close_price, volume
            FROM {table_name}
            ORDER BY open_time DESC
            LIMIT 1000
        """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, con=conn)

        if df.empty:
            return df
        df = df.sort_values("open_time").reset_index(drop=True)

    except Exception as e:
        st.error(f"Supabase 連線或讀取錯誤：{e}")
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


# ============================================================
# 3. 升級版技術指標與特徵工程 (加入 MACD 與通道相對位置)
# ============================================================
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

    # 計算通道相對位置 (Channel Position: 0=支撐線, 1=壓力線)
    channel_range = df["Trend_Resistance"] - df["Trend_Support"]
    df["Channel_Pos"] = (df["close_price"] - df["Trend_Support"]) / channel_range.replace(0, np.nan)

    # RSI (14)
    delta = df["close_price"].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["RSI"] = (100 - 100 / (1 + rs)).fillna(50)

    # MACD 動能指標
    exp1 = df["close_price"].ewm(span=12, adjust=False).mean()
    exp2 = df["close_price"].ewm(span=26, adjust=False).mean()
    df["MACD"] = exp1 - exp2
    df["MACD_Signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_Hist"] = df["MACD"] - df["MACD_Signal"]

    # ATR 與成交量
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


# ============================================================
# 4. LightGBM 模型優化與高準確度過濾判定
# ============================================================
def build_candidate_signals(df, tf):
    df = df.copy()

    if tf in ["5m", "15m"]:
        df["Base_Long"] = (
            (df["close_price"] > df["Trend_Resistance"])
            & df["RSI"].between(45, 80)
            & (df["volume"] > df["Vol_SMA20"] * 1.1)
            & (df["Body_Ratio"] > 0.35)
            & (df["MACD_Hist"] > 0)
        )
        df["Base_Short"] = (
            (df["close_price"] < df["Trend_Support"])
            & df["RSI"].between(20, 55)
            & (df["volume"] > df["Vol_SMA20"] * 1.1)
            & (df["Body_Ratio"] > 0.35)
            & (df["MACD_Hist"] < 0)
        )
    else:
        df["Base_Long"] = (
            (df["close_price"] > df["Trend_Resistance"])
            & df["RSI"].between(48, 78)
            & (df["volume"] > df["Vol_SMA20"] * 1.05)
            & (df["MACD_Hist"] > 0)
        )
        df["Base_Short"] = (
            (df["close_price"] < df["Trend_Support"])
            & df["RSI"].between(22, 52)
            & (df["volume"] > df["Vol_SMA20"] * 1.05)
            & (df["MACD_Hist"] < 0)
        )

    df["vol_ratio"] = df["volume"] / df["Vol_SMA20"].replace(0, np.nan)
    df["body_ratio"] = df["Body_Ratio"]
    df["atr_ratio"] = df["ATR"] / df["close_price"].replace(0, np.nan)
    df["ema_diff"] = (
        (df["EMA_21"] - df["EMA_50"])
        / df["close_price"].replace(0, np.nan)
    )
    df["side"] = np.where(df["Base_Long"], 1, 0)

    trade_results = []
    stop_losses = []
    take_profits = []
    directional_returns = []

    close_arr = df["close_price"].values
    high_arr = df["high_price"].values
    low_arr = df["low_price"].values
    atr_arr = df["ATR"].values
    base_long_arr = df["Base_Long"].values
    base_short_arr = df["Base_Short"].values
    n = len(df)

    for i in range(n):
        is_l = base_long_arr[i]
        is_s = base_short_arr[i]
        if not (is_l or is_s):
            trade_results.append("非訊號")
            stop_losses.append(np.nan)
            take_profits.append(np.nan)
            directional_returns.append(np.nan)
            continue

        entry = close_arr[i]
        atr = atr_arr[i]
        if pd.isna(atr) or atr <= 0 or pd.isna(entry) or entry <= 0:
            trade_results.append("觀察中")
            stop_losses.append(np.nan)
            take_profits.append(np.nan)
            directional_returns.append(np.nan)
            continue

        if is_l:
            sl = entry - atr * ATR_STOP_MULTIPLIER
            tp = entry + (entry - sl) * RISK_REWARD_RATIO
            direction = 'LONG'
        else:
            sl = entry + atr * ATR_STOP_MULTIPLIER
            tp = entry - (sl - entry) * RISK_REWARD_RATIO
            direction = 'SHORT'

        stop_losses.append(sl)
        take_profits.append(tp)

        outcome = "觀察中"
        ret = 0.0
        matured = False

        for j in range(i + 1, min(n, i + 100)):
            h = high_arr[j]
            l = low_arr[j]
            if pd.isna(h) or pd.isna(l):
                continue

            if direction == 'LONG':
                hit_sl = (l <= sl)
                hit_tp = (h >= tp)
                if hit_sl or (hit_sl and hit_tp):
                    outcome = "未達標"
                    ret = -1.0
                    matured = True
                    break
                elif hit_tp:
                    outcome = "成功"
                    ret = 1.0
                    matured = True
                    break
            else:
                hit_sl = (h >= sl)
                hit_tp = (l <= tp)
                if hit_sl or (hit_sl and hit_tp):
                    outcome = "未達標"
                    ret = -1.0
                    matured = True
                    break
                elif hit_tp:
                    outcome = "成功"
                    ret = 1.0
                    matured = True
                    break

        if not matured:
            outcome = "觀察中"

        trade_results.append(outcome)
        directional_returns.append(ret)

    df["Trade_Result"] = trade_results
    df["Stop_Loss"] = stop_losses
    df["Take_Profit"] = take_profits
    df["directional_return_3"] = directional_returns
    return df


def train_or_get_ai_model(df, key):
    feature_cols = [
        "RSI", "vol_ratio", "body_ratio", "atr_ratio", "ema_diff", 
        "MACD_Hist", "Channel_Pos", "side"
    ]
    candidates = df[df["Base_Long"] | df["Base_Short"]].copy()
    train_df = candidates[candidates["Trade_Result"].isin(["成功", "未達標"])].copy()

    if len(train_df) < 30 or train_df["Trade_Result"].nunique() < 2:
        return None, len(train_df), int((train_df["Trade_Result"] == "成功").sum())

    train_df["target"] = (train_df["Trade_Result"] == "成功").astype(int)
    if train_df["target"].nunique() < 2:
        return None, len(train_df), int(train_df["target"].sum())

    try:
        model = lgb.LGBMClassifier(
            n_estimators=100,
            learning_rate=0.03,
            max_depth=4,
            num_leaves=15,
            subsample=0.8,
            colsample_bytree=0.8,
            class_weight="balanced",
            random_state=42,
            verbose=-1,
        )
        model.fit(train_df[feature_cols], train_df["target"])
        return model, len(train_df), int((train_df["Trade_Result"] == "成功").sum())
    except Exception as e:
        return None, len(train_df), int((train_df["Trade_Result"] == "成功").sum())


def add_trade_signals(df, model):
    df = df.copy()
    df["AI_Prob"] = np.nan
    candidates = df["Base_Long"] | df["Base_Short"]

    feature_cols = [
        "RSI", "vol_ratio", "body_ratio", "atr_ratio", "ema_diff", 
        "MACD_Hist", "Channel_Pos", "side"
    ]

    if model is not None and candidates.any():
        usable = df.loc[candidates, feature_cols].replace([np.inf, -np.inf], np.nan)
        valid = usable.notna().all(axis=1)
        if valid.any():
            try:
                probabilities = model.predict_proba(usable.loc[valid])[:, 1]
                df.loc[usable.loc[valid].index, "AI_Prob"] = probabilities
            except Exception:
                pass

    if model is None:
        df["AI_Prob"] = np.where(candidates, 0.70, np.nan)
        df["Long_Signal"] = df["Base_Long"]
        df["Short_Signal"] = df["Base_Short"]
        df["AI_Mode"] = "策略回退（歷史樣本不足）"
    else:
        confidence_threshold = 0.65
        df["Long_Signal"] = df["Base_Long"] & (df["AI_Prob"] >= confidence_threshold)
        df["Short_Signal"] = df["Base_Short"] & (df["AI_Prob"] >= confidence_threshold)
        df["AI_Mode"] = "高精度機器學習模型 (LGBM)"

    return df


# ============================================================
# 5. 首頁說明區
# ============================================================
def render_homepage():
    st.info("💡 請在上方查詢欄輸入 BTC 或 ETH，並按下「送出查詢」開始使用。")

    st.markdown("""
    <div class="intro-card">
        <h2 style="color:#f0b90b;">🚀 關於加密貨幣 AI 策略系統</h2>
        <p>
        本系統是一套加密貨幣技術分析與 AI 訊號輔助工具，
        透過 Supabase 歷史 K 線資料，結合趨勢通道、EMA 均線、
        RSI、ATR、MACD 動能、成交量及高精度 LightGBM 機器學習模型，
        協助使用者觀察 BTC 與 ETH 市場走勢，嚴格過濾並尋找高品質的多單與空單機會。
        </p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("## ⚙️ 系統功能")
    c1, c2, c3 = st.columns(3)

    with c1:
        st.markdown("""
        <div class="feature-card" style="border:1px solid #0ECB81;">
        <h3 style="color:#0ECB81;">📈 多空訊號</h3>
        <p>分析趨勢通道突破與 MACD 動能，嚴格判斷 LONG 與 SHORT 機會。</p>
        </div>
        """, unsafe_allow_html=True)

    with c2:
        st.markdown("""
        <div class="feature-card" style="border:1px solid #2962FF;">
        <h3 style="color:#66a3ff;">🤖 AI 模型分析</h3>
        <p>使用多維特徵與 LightGBM 分類器，提供高信賴度分數過濾雜訊。</p>
        </div>
        """, unsafe_allow_html=True)

    with c3:
        st.markdown("""
        <div class="feature-card" style="border:1px solid #f0b90b;">
        <h3 style="color:#f0b90b;">📊 技術分析</h3>
        <p>整合 EMA、RSI、ATR、MACD、成交量與雙軌趨勢通道。</p>
        </div>
        """, unsafe_allow_html=True)


# ============================================================
# 6. K 線圖與進場點位（更緊湊、更高挑修長）
# ============================================================
def render_live_kline_chart(table, symbol, pair, tf):
    df = load_historical_data(table, symbol)

    if df.empty:
        st.warning("目前無法取得資料，請確認 Supabase 連線及資料表內容。")
        return

    df_tf = (
        df[df["timeframe"] == tf]
        .sort_values("open_time")
        .copy()
        .reset_index(drop=True)
    )

    if df_tf.empty:
        st.warning(f"{tf} 週期目前尚無資料。")
        return

    df_tf, win, dev = calculate_indicators(df_tf, tf)
    df_tf = build_candidate_signals(df_tf, tf)
    model, training_count, winning_count = train_or_get_ai_model(
        df_tf, f"{symbol}_{tf}"
    )
    df_tf = add_trade_signals(df_tf, model)

    display_count = 75 if symbol == "ETH" else 90
    view_df = df_tf.tail(display_count)

    if view_df.empty:
        st.warning("目前沒有足夠資料可顯示。")
        return

    interval = (
        view_df["open_time"].diff().median()
        if len(view_df) > 1 else pd.Timedelta(minutes=1)
    )
    start_time = view_df["open_time"].iloc[0] - interval * 2
    end_time = view_df["open_time"].iloc[-1] + interval * 3

    supports = view_df["Trend_Support"].dropna()
    resistances = view_df["Trend_Resistance"].dropna()

    low = min(
        view_df["low_price"].min(),
        supports.min() if not supports.empty else view_df["low_price"].min()
    )
    high = max(
        view_df["high_price"].max(),
        resistances.max() if not resistances.empty else view_df["high_price"].max()
    )

    span = max(high - low, abs(high) * 0.001, 1e-8)
    y_range = [low - span * 0.25, high + span * 0.25]

    latest = df_tf.iloc[-1]
    latest_close = float(latest["close_price"])
    change_pct = (
        (latest_close - latest["open_price"]) / latest["open_price"] * 100
        if latest["open_price"] else 0
    )

    status_color = (
        "#0ECB81" if latest["close_price"] >= latest["open_price"]
        else "#F6465D"
    )
    latest_rsi = float(latest["RSI"]) if pd.notna(latest["RSI"]) else 50.0
    ai_score = (
        float(latest["AI_Prob"]) * 100
        if pd.notna(latest["AI_Prob"]) else 0.0
    )

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.015,
        row_heights=[0.75, 0.25],
    )

    fig.add_trace(go.Candlestick(
        x=df_tf["open_time"],
        open=df_tf["open_price"],
        high=df_tf["high_price"],
        low=df_tf["low_price"],
        close=df_tf["close_price"],
        name=symbol,
        line=dict(width=1.0),
        increasing_line_color="#0ECB81",
        increasing_fillcolor="#0ECB81",
        decreasing_line_color="#F6465D",
        decreasing_fillcolor="#F6465D",
        hoverinfo="none",
    ), row=1, col=1)

    for ema, color, width in [
        ("EMA_21", "#2962FF", 1.4),
        ("EMA_50", "#FFFFFF", 1.4),
        ("EMA_100", "#FF5252", 1.6),
    ]:
        fig.add_trace(go.Scatter(
            x=df_tf["open_time"],
            y=df_tf[ema],
            mode="lines",
            name=ema.replace("_", ""),
            line=dict(color=color, width=width),
            hoverinfo="none",
        ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=df_tf["open_time"],
        y=df_tf["Trend_Resistance"],
        mode="lines",
        name="壓力趨勢線",
        line=dict(color="rgba(246,70,93,.7)", width=1.4, dash="dash"),
        hoverinfo="none",
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=df_tf["open_time"],
        y=df_tf["Trend_Support"],
        mode="lines",
        name="支撐趨勢線",
        line=dict(color="rgba(14,203,129,.7)", width=1.4, dash="dash"),
        hoverinfo="none",
    ), row=1, col=1)

    long_df = view_df[view_df["Long_Signal"]].copy()
    if not long_df.empty:
        long_y = long_df["low_price"] - span * 0.25

        for t, low_p, ly in zip(long_df["open_time"], long_df["low_price"], long_y):
            fig.add_trace(go.Scatter(
                x=[t, t],
                y=[low_p, ly],
                mode="lines",
                line=dict(color="#848e9c", width=1, dash="dot"),
                showlegend=False,
                hoverinfo="none"
            ), row=1, col=1)

        fig.add_trace(go.Scatter(
            x=long_df["open_time"],
            y=long_y,
            mode="markers+text",
            name="LONG 多單進場",
            marker=dict(symbol="triangle-up", size=10, color="#00E676", line=dict(width=1, color="white")),
            text=[f"LONG<br>{price:,.2f}" for price in long_df["close_price"]],
            textposition="bottom center",
            textfont=dict(color="#00E676", size=9, family="Arial Black"),
            customdata=long_df[["close_price", "AI_Prob", "Trade_Result"]].to_numpy(),
            hovertemplate=(
                "🟢 LONG 多單歷史進場<br>"
                "時間：%{x}<br>"
                "參考進場價：%{customdata[0]:,.2f}<br>"
                "AI 分數：%{customdata[1]:.1%}<br>"
                "TP/SL 結算結果：%{customdata[2]}"
                "<extra></extra>"
            ),
        ), row=1, col=1)

    short_df = view_df[view_df["Short_Signal"]].copy()
    if not short_df.empty:
        short_y = short_df["high_price"] + span * 0.25

        for t, high_p, sy in zip(short_df["open_time"], short_df["high_price"], short_y):
            fig.add_trace(go.Scatter(
                x=[t, t],
                y=[high_p, sy],
                mode="lines",
                line=dict(color="#848e9c", width=1, dash="dot"),
                showlegend=False,
                hoverinfo="none"
            ), row=1, col=1)

        fig.add_trace(go.Scatter(
            x=short_df["open_time"],
            y=short_y,
            mode="markers+text",
            name="SHORT 空單進場",
            marker=dict(symbol="triangle-down", size=10, color="#FF1744", line=dict(width=1, color="white")),
            text=[f"SHORT<br>{price:,.2f}" for price in short_df["close_price"]],
            textposition="top center",
            textfont=dict(color="#FF1744", size=9, family="Arial Black"),
            customdata=short_df[["close_price", "AI_Prob", "Trade_Result"]].to_numpy(),
            hovertemplate=(
                "🔴 SHORT 空單歷史進場<br>"
                "時間：%{x}<br>"
                "參考進場價：%{customdata[0]:,.2f}<br>"
                "AI 分數：%{customdata[1]:.1%}<br>"
                "TP/SL 結算結果：%{customdata[2]}"
                "<extra></extra>"
            ),
        ), row=1, col=1)

    volume_colors = [
        "rgba(14,203,129,.6)" if close >= open_ else "rgba(246,70,93,.6)"
        for close, open_ in zip(df_tf["close_price"], df_tf["open_price"])
    ]
    fig.add_trace(go.Bar(
        x=df_tf["open_time"], y=df_tf["volume"], name="VOL", marker_color=volume_colors, hoverinfo="none"
    ), row=2, col=1)

    fig.add_hline(
        y=latest_close,
        line_dash="dot",
        line_width=1,
        line_color=status_color,
        annotation_text=f" {latest_close:,.2f} ",
        annotation_position="right",
        annotation_font=dict(color="white", size=11, family="Arial Black"),
        annotation_bgcolor=status_color,
        annotation_bordercolor=status_color,
        annotation_borderwidth=1,
        annotation_borderpad=3,
        row=1,
        col=1,
    )

    mode = "⚡ AI小波段閃電模式" if tf in ["5m", "15m"] else "🌊 AI大波段趨勢模式"
    annotation = (
        f"<b>{pair} · {tf} ({mode})</b><br>"
        f"<b>策略與指標：</b>雙軌通道 (Window: {win}, Dev: {dev}) | "
        "EMA (21, 50, 100) | RSI (14) | MACD | ATR<br>"
        f"<b>RSI：</b>{latest_rsi:.1f} | "
        f"<b>AI模型分數：</b><span style='color:#f0b90b'>{ai_score:.1f}%</span> | "
        f"收：<span style='color:{status_color}'>{latest_close:,.2f} "
        f"({change_pct:+.2f}%)</span>"
    )

    fig.add_annotation(
        text=annotation,
        xref="paper",
        yref="paper",
        x=0.01,
        y=0.99,
        xanchor="left",
        yanchor="top",
        showarrow=False,
        align="left",
        font=dict(size=11, color="#c2c8d0"),
    )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(11,14,17,.6)",
        plot_bgcolor="rgba(11,14,17,.6)",
        hovermode="x",
        hoverdistance=100,
        spikedistance=1000,
        xaxis_rangeslider_visible=False,
        dragmode="pan",
        height=880,
        bargap=0.0,
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
            font=dict(size=10),
        ),
        margin=dict(l=10, r=85, t=65, b=30),
        uirevision=f"{symbol}_{tf}_chart",
        hoverlabel=dict(
            bgcolor="rgba(30,35,41,.95)",
            font_size=12,
            font_color="white",
            bordercolor="#f0b90b",
        ),
    )

    fig.update_layout(xaxis=dict(range=[start_time, end_time], autorange=False))

    fig.update_yaxes(
        side="right",
        gridcolor="#1e2329",
        zeroline=False,
        linecolor="#2b313a",
        tickformat=",.2f",
        showspikes=True,
        spikemode="across+toaxis",
        spikesnap="cursor",
        spikethickness=1,
        spikedash="dash",
        spikecolor="#a0a6b2",
        showline=True,
        row=1,
        col=1,
        range=y_range,
    )

    fig.update_yaxes(
        side="right",
        gridcolor="#1e2329",
        zeroline=False,
        linecolor="#2b313a",
        tickformat=",.2f",
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikethickness=1,
        spikedash="dash",
        spikecolor="#a0a6b2",
        row=2,
        col=1,
    )

    chart_col, panel_col = st.columns([3.6, 1.2])

    with chart_col:
        st.plotly_chart(
            fig,
            use_container_width=True,
            config={
                "scrollZoom": True,
                "displayModeBar": True,
                "displaylogo": False,
            },
            key=f"{symbol.lower()}_{tf}_chart",
        )

    with panel_col:
        st.markdown("### 🎯 AI 即時操作建議")
        st.caption(
            f"歷史訓練樣本：{training_count} 筆｜"
            f"成功標籤：{winning_count} 筆｜"
            f"模型狀態：{'高精度 LGBM 模型' if model is not None else '樣本不足，使用原策略'}"
        )

        st.markdown(f"""
        <div style="background:rgba(30,35,41,.9);border:1px solid #2b313a;
                    padding:10px;border-radius:6px;margin-bottom:10px;">
            <div style="font-size:13px;color:#848e9c;">當前 AI 模型信心分數</div>
            <div style="font-size:20px;font-weight:bold;color:#f0b90b;">
                {ai_score:.1f}%
            </div>
        </div>
        """, unsafe_allow_html=True)

        if bool(latest["Long_Signal"]):
            st.success("🟢 LONG 多單進場訊號")
            st.metric("參考進場價格", f"{latest_close:,.2f}")
            st.metric("AI 模型分數", f"{ai_score:.1f}%")

        elif bool(latest["Short_Signal"]):
            st.error("🔴 SHORT 空單進場訊號")
            st.metric("參考進場價格", f"{latest_close:,.2f}")
            st.metric("AI 模型分數", f"{ai_score:.1f}%")

        else:
            st.warning("⏳ WAIT 目前沒有進場訊號")
            st.caption("等待價格突破趨勢通道，並符合 RSI、MACD 動能與 AI 高信心條件。")

        st.markdown("---")
        st.markdown("#### 📌 最近進場訊號")

        recent_signals = df_tf[
            df_tf["Long_Signal"] | df_tf["Short_Signal"]
        ].tail(5).copy()

        if recent_signals.empty:
            st.caption("目前查詢資料中沒有符合條件的高精度進場訊號。")
        else:
            for _, signal in recent_signals.iloc[::-1].iterrows():
                signal_time = signal["open_time"].strftime("%m/%d %H:%M")
                signal_price = float(signal["close_price"])
                signal_prob = float(signal["AI_Prob"]) * 100

                if bool(signal["Long_Signal"]):
                    st.markdown(
                        f"🟢 **LONG**｜{signal_time}  \n"
                        f"進場參考價：`{signal_price:,.2f}`  \n"
                        f"AI 分數：`{signal_prob:.1f}%`"
                    )
                else:
                    st.markdown(
                        f"🔴 **SHORT**｜{signal_time}  \n"
                        f"進場參考價：`{signal_price:,.2f}`  \n"
                        f"AI 分數：`{signal_prob:.1f}%`"
                    )

        st.caption("進場價格為訊號 K 線收盤價參考值，不代表實際成交價。")

        st.markdown("#### 🧠 歷史訊號學習紀錄 (TP/SL 結算)")
        history = df_tf[df_tf["Base_Long"] | df_tf["Base_Short"]].tail(12).copy()
        if history.empty:
            st.caption("目前載入的 K 線中尚無歷史候選進場點。")
        else:
            history["方向"] = np.where(history["Base_Long"], "LONG", "SHORT")
            history["時間"] = history["open_time"].dt.strftime("%m/%d %H:%M")
            history["進場參考價"] = history["close_price"].map(lambda x: f"{x:,.2f}")
            history["止損價 (SL)"] = history["Stop_Loss"].map(lambda x: f"{x:,.2f}" if pd.notna(x) else "—")
            history["止盈價 (TP)"] = history["Take_Profit"].map(lambda x: f"{x:,.2f}" if pd.notna(x) else "—")
            history["AI 分數"] = history["AI_Prob"].map(
                lambda x: f"{x:.1%}" if pd.notna(x) else "—"
            )
            st.dataframe(
                history[["時間", "方向", "進場參考價", "止損價 (SL)", "止盈價 (TP)", "Trade_Result", "AI 分數"]]
                .rename(columns={"Trade_Result": "結算結果"}),
                use_container_width=True,
                hide_index=True,
            )


# ============================================================
# 7. 主畫面
# ============================================================
if __name__ == "__main__":
    if st.session_state.is_searched:
        query = st.session_state.search_query.upper()

        if "ETH" in query:
            table_name, symbol_name, pair_name = (
                "eth_contract_klines",
                "ETH",
                "ETH-USDT-SWAP",
            )
        else:
            table_name, symbol_name, pair_name = (
                "btc_contract_klines",
                "BTC",
                "BTC-USDT-SWAP",
            )

        selected_tf = st.radio(
            "時間週期（小波段／大波段）",
            ["5m", "15m", "1h", "4h"],
            horizontal=True,
            key="timeframe_select",
        )

        @st.fragment(run_every=5)
        def render_live_fragment(tbl, sym, pair, timeframe):
            render_live_kline_chart(tbl, sym, pair, timeframe)

        render_live_fragment(
            table_name,
            symbol_name,
            pair_name,
            selected_tf,
        )

    elif st.session_state.search_query:
        st.warning(
            f"找不到與「{st.session_state.search_query}」相關的圖表，"
            "請嘗試輸入 BTC 或 ETH。"
        )
        render_homepage()

    else:
        render_homepage()
