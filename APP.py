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


# ============================================================
# 0. 頁面設定、圖片與背景
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

st.set_page_config(
    page_title="加密貨幣AI策略系統",
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
div[data-testid="stRadio"] > div {{ flex-direction: row; gap: 12px; }}
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
@media(max-width:768px) {{
    .block-container {{ padding-top: 2rem !important; }}
    h1 {{ font-size: 2rem !important; }}
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
# 2. Supabase 連線
# 請在 Streamlit Secrets 或環境變數設定 SUPABASE_DB_URL。
# 不要將資料庫密碼直接寫入程式。
# ============================================================
def get_database_url():
    url = os.getenv("postgresql://postgres.ygzeqvcyqtvhvjrxhzdv:%4010233960527Ken@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres", "").strip()
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
        url, pool_pre_ping=True, pool_recycle=1800,
        connect_args={"connect_timeout": 10},
    )


def load_historical_data(table_name, symbol_name):
    if table_name not in {"btc_contract_klines", "eth_contract_klines"}:
        st.error("資料表名稱不合法。")
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
# 3. 技術指標
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


# ============================================================
# 4. LightGBM 模型（保留原始模型目標與參數）
# ============================================================
def train_or_get_ai_model(df_tf, key):
    if key in st.session_state.ai_models:
        return st.session_state.ai_models[key]
    if len(df_tf) < 60:
        return None

    try:
        df = df_tf.copy()
        df["vol_ratio"] = df["volume"] / df["volume"].rolling(20).mean()
        body = (df["close_price"] - df["open_price"]).abs()
        candle_range = df["high_price"] - df["low_price"]
        df["body_ratio"] = body / candle_range.replace(0, np.nan)
        df["atr_ratio"] = df["ATR"] / df["close_price"]
        df["ema_diff"] = (df["EMA_21"] - df["EMA_50"]) / df["close_price"]

        # 沿用原程式：未來價格上漲幅度是否超過 1.2%
        future_max_gain = (
            df["high_price"].shift(-3).rolling(3).max()
            / df["close_price"] - 1
        )
        df["target"] = (future_max_gain > 0.012).astype(int)

        features = ["RSI", "vol_ratio", "body_ratio", "atr_ratio", "ema_diff"]
        train_df = df.dropna(subset=features + ["target"])
        if len(train_df) < 30 or train_df["target"].nunique() < 2:
            return None

        model = lgb.LGBMClassifier(
            n_estimators=40, learning_rate=0.05, max_depth=3, verbose=-1
        )
        model.fit(train_df[features], train_df["target"])
        st.session_state.ai_models[key] = model
        return model
    except Exception as e:
        st.warning(f"AI 模型訓練失敗：{e}")
        return None


# ============================================================
# 5. 原策略訊號判斷
# ============================================================
def add_trade_signals(df, tf, model):
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

    features = ["RSI", "vol_ratio", "body_ratio", "atr_ratio", "ema_diff"]
    probabilities, longs, shorts = [], [], []

    for _, row in df.iterrows():
        is_long = bool(row["Base_Long"])
        is_short = bool(row["Base_Short"])
        prob = 0.70  # 沿用原始程式的預設分數

        if (is_long or is_short) and model is not None:
            try:
                vol_sma = row["Vol_SMA20"]
                close = row["close_price"]
                feature_row = pd.DataFrame([{
                    "RSI": row["RSI"],
                    "vol_ratio": (
                        row["volume"] / vol_sma
                        if pd.notna(vol_sma) and vol_sma > 0 else 1.0
                    ),
                    "body_ratio": row["Body_Ratio"],
                    "atr_ratio": (
                        row["ATR"] / close
                        if pd.notna(row["ATR"]) and close else 0.015
                    ),
                    "ema_diff": (
                        (row["EMA_21"] - row["EMA_50"]) / close
                        if close else 0
                    ),
                }], columns=features)
                prob = float(model.predict_proba(feature_row)[0][1])
            except Exception:
                pass

        probabilities.append(prob)
        longs.append(is_long and prob >= 0.58)
        shorts.append(is_short and prob >= 0.58)

    df["AI_Prob"] = probabilities
    df["Long_Signal"] = longs
    df["Short_Signal"] = shorts
    return df


# ============================================================
# 6. 首頁說明區（紅色框位置）
# ============================================================
def render_homepage():
    st.info("💡 請在上方查詢欄輸入 BTC 或 ETH，並按下「送出查詢」開始使用。")

    st.markdown("""
    <div class="intro-card">
        <h2 style="color:#f0b90b;">🚀 關於加密貨幣 AI 策略系統</h2>
        <p>
        本系統是一套加密貨幣技術分析與 AI 訊號輔助工具，
        透過 Supabase 歷史 K 線資料，結合趨勢通道、EMA 均線、
        RSI、ATR、成交量及 LightGBM 機器學習模型，
        協助使用者觀察 BTC 與 ETH 市場走勢，尋找可能的多單與空單機會。
        </p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("## ⚙️ 系統功能")
    c1, c2, c3 = st.columns(3)

    with c1:
        st.markdown("""
        <div class="feature-card" style="border:1px solid #0ECB81;">
        <h3 style="color:#0ECB81;">📈 多空訊號</h3>
        <p>分析趨勢通道突破情況，判斷可能的 LONG 多單與 SHORT 空單機會。</p>
        </div>
        """, unsafe_allow_html=True)

    with c2:
        st.markdown("""
        <div class="feature-card" style="border:1px solid #2962FF;">
        <h3 style="color:#66a3ff;">🤖 AI 模型分析</h3>
        <p>使用 LightGBM 分析技術指標，提供 AI 模型信心分數，輔助觀察市場。</p>
        </div>
        """, unsafe_allow_html=True)

    with c3:
        st.markdown("""
        <div class="feature-card" style="border:1px solid #f0b90b;">
        <h3 style="color:#f0b90b;">📊 技術分析</h3>
        <p>整合 EMA 21、50、100、RSI、ATR、成交量與趨勢通道，觀察市場變化。</p>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("## 📖 使用說明")
    with st.container(border=True):
        st.markdown("### ① 輸入加密貨幣名稱")
        st.write("在頁面最上方搜尋欄輸入 BTC 或 ETH。")
        st.markdown("### ② 送出查詢")
        st.write("按下「送出查詢」，系統會載入該幣種的歷史 K 線資料。")
        st.markdown("### ③ 選擇時間週期")
        st.write("""
        - **5m：** 5 分鐘 K 線，觀察短線變化。
        - **15m：** 15 分鐘 K 線，觀察短線趨勢。
        - **1h：** 1 小時 K 線，觀察中期趨勢。
        - **4h：** 4 小時 K 線，觀察較大波段。
        """)
        st.markdown("### ④ 查看 AI 分析結果")
        st.write("""
        - 🟢 **AI 多單（LONG）：** 符合多單訊號條件。
        - 🔴 **AI 空單（SHORT）：** 符合空單訊號條件。
        - 🟡 **AI 觀望（WAIT）：** 暫時沒有符合條件的訊號。
        """)

    st.markdown("---")
    st.markdown("## 🧠 AI 策略判斷方式")
    strategy_df = pd.DataFrame({
        "技術指標": [
            "趨勢通道", "RSI（14）", "成交量",
            "K 線實體比例", "EMA（21、50、100）",
            "ATR（14）", "LightGBM"
        ],
        "用途": [
            "判斷價格是否突破壓力或支撐",
            "判斷市場動能",
            "確認成交量是否放大",
            "評估 K 線實體強度",
            "觀察短中期趨勢",
            "衡量市場波動程度",
            "提供模型預測分數"
        ]
    })
    st.dataframe(strategy_df, use_container_width=True, hide_index=True)

    with st.expander("查看不同週期的訊號條件"):
        st.markdown("""
        **5m、15m 小波段**

        - 多單：收盤價高於趨勢通道上軌、RSI 42～85、
          成交量大於 20 根均量的 1.05 倍、K 線實體比例大於 0.3。
        - 空單：收盤價低於趨勢通道下軌、RSI 15～58、
          成交量大於 20 根均量的 1.05 倍、K 線實體比例大於 0.3。

        **1h、4h 大波段**

        - 多單：收盤價高於趨勢通道上軌、RSI 45～80、
          成交量大於 20 根均量。
        - 空單：收盤價低於趨勢通道下軌、RSI 20～55、
          成交量大於 20 根均量。

        **AI 條件：** 模型分數達到 58% 才顯示訊號。
        """)

    st.warning("""
    ⚠️ **風險提醒**

    本系統提供技術分析與 AI 模型輔助訊號，不保證交易獲利。
    AI 模型分數不是實際勝率。市場可能因突發事件快速變動，
    請自行評估風險並設定適當止損，不要只依賴單一指標或模型分數。
    """)
    st.caption("加密貨幣 AI 策略系統｜BTC / ETH｜5m / 15m / 1h / 4h")


# ============================================================
# 7. K 線圖與右側 AI 建議面板
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
        st.warning("此週期目前尚無資料。")
        return

    df_tf, win, dev = calculate_indicators(df_tf, tf)
    model = train_or_get_ai_model(df_tf, f"{symbol}_{tf}")
    df_tf = add_trade_signals(df_tf, tf, model)

    display_count = 90 if symbol == "ETH" else 120
    view_df = df_tf.tail(display_count)
    if view_df.empty:
        st.warning("目前沒有足夠資料可顯示。")
        return

    interval = (
        view_df["open_time"].diff().median()
        if len(view_df) > 1 else pd.Timedelta(minutes=1)
    )
    start_time = view_df["open_time"].iloc[0] - interval * 5
    end_time = view_df["open_time"].iloc[-1] + interval * 10

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
    y_range = [low - span * 0.03, high + span * 0.03]

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
    ai_score = float(latest["AI_Prob"]) * 100

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        vertical_spacing=0.015, row_heights=[0.75, 0.25]
    )

    fig.add_trace(go.Candlestick(
        x=df_tf["open_time"],
        open=df_tf["open_price"],
        high=df_tf["high_price"],
        low=df_tf["low_price"],
        close=df_tf["close_price"],
        name=symbol,
        line=dict(width=1.5),
        increasing_line_color="#0ECB81",
        increasing_fillcolor="#0ECB81",
        decreasing_line_color="#F6465D",
        decreasing_fillcolor="#F6465D",
        hoverinfo="none"
    ), row=1, col=1)

    for ema, color, width in [
        ("EMA_21", "#2962FF", 1.2),
        ("EMA_50", "#FFFFFF", 1.2),
        ("EMA_100", "#FF5252", 1.4),
    ]:
        fig.add_trace(go.Scatter(
            x=df_tf["open_time"], y=df_tf[ema],
            mode="lines", name=ema.replace("_", ""),
            line=dict(color=color, width=width), hoverinfo="none"
        ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=df_tf["open_time"], y=df_tf["Trend_Resistance"],
        mode="lines", name="壓力趨勢線",
        line=dict(color="rgba(246,70,93,.7)", width=1.5, dash="dash"),
        hoverinfo="none"
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=df_tf["open_time"], y=df_tf["Trend_Support"],
        mode="lines", name="支撐趨勢線",
        line=dict(color="rgba(14,203,129,.7)", width=1.5, dash="dash"),
        fill="tonexty", fillcolor="rgba(126,87,194,.02)",
        hoverinfo="none"
    ), row=1, col=1)

    long_df = df_tf[df_tf["Long_Signal"]]
    if not long_df.empty:
        fig.add_trace(go.Scatter(
            x=long_df["open_time"], y=long_df["low_price"] * 0.995,
            mode="markers+text", name="AI 多點",
            marker=dict(
                symbol="triangle-up", size=10, color="#0ECB81",
                line=dict(width=1, color="white")
            ),
            text=["🟢 AI多"] * len(long_df),
            textposition="bottom center",
            textfont=dict(color="#0ECB81", size=11, family="Arial Black"),
            hoverinfo="none"
        ), row=1, col=1)

    short_df = df_tf[df_tf["Short_Signal"]]
    if not short_df.empty:
        fig.add_trace(go.Scatter(
            x=short_df["open_time"], y=short_df["high_price"] * 1.005,
            mode="markers+text", name="AI 空點",
            marker=dict(
                symbol="triangle-down", size=10, color="#F6465D",
                line=dict(width=1, color="white")
            ),
            text=["🔴 AI空"] * len(short_df),
            textposition="top center",
            textfont=dict(color="#F6465D", size=11, family="Arial Black"),
            hoverinfo="none"
        ), row=1, col=1)

    volume_colors = [
        "rgba(14,203,129,.5)" if close >= open_
        else "rgba(246,70,93,.5)"
        for close, open_ in zip(df_tf["close_price"], df_tf["open_price"])
    ]
    fig.add_trace(go.Bar(
        x=df_tf["open_time"], y=df_tf["volume"],
        name="VOL", marker_color=volume_colors, hoverinfo="none"
    ), row=2, col=1)

    fig.add_hline(
        y=latest_close, line_dash="dot", line_width=1,
        line_color=status_color,
        annotation_text=f" {latest_close:,.2f} ",
        annotation_position="right",
        annotation_font=dict(color="white", size=11, family="Arial Black"),
        annotation_bgcolor=status_color,
        annotation_bordercolor=status_color,
        annotation_borderwidth=1,
        annotation_borderpad=3,
        row=1, col=1
    )

    mode = "⚡ AI小波段閃電模式" if tf in ["5m", "15m"] else "🌊 AI大波段趨勢模式"
    annotation = (
        f"<b>{pair} · {tf} ({mode})</b><br>"
        f"<b>策略與指標：</b>雙軌通道 (Window: {win}, Dev: {dev}) | "
        "EMA (21, 50, 100) | RSI (14) | ATR (14)<br>"
        f"<b>RSI：</b>{latest_rsi:.1f} | "
        f"<b>AI模型信心分數：</b><span style='color:#f0b90b'>{ai_score:.1f}%</span> | "
        f"收：<span style='color:{status_color}'>{latest_close:,.2f} "
        f"({change_pct:+.2f}%)</span>"
    )
    fig.add_annotation(
        text=annotation, xref="paper", yref="paper",
        x=0.01, y=0.99, xanchor="left", yanchor="top",
        showarrow=False, align="left",
        font=dict(size=11, color="#c2c8d0")
    )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(11,14,17,.6)",
        plot_bgcolor="rgba(11,14,17,.6)",
        hovermode="x", hoverdistance=100, spikedistance=1000,
        xaxis_rangeslider_visible=False, dragmode="pan",
        height=820, bargap=0.15, showlegend=False,
        margin=dict(l=10, r=85, t=25, b=30),
        uirevision=f"{symbol}_{tf}_chart",
        hoverlabel=dict(
            bgcolor="rgba(30,35,41,.95)", font_size=12,
            font_color="white", bordercolor="#f0b90b"
        )
    )
    fig.update_layout(xaxis=dict(
        range=[start_time, end_time], autorange=False
    ))
    fig.update_xaxes(
        showticklabels=True, gridcolor="#1e2329",
        zeroline=False, linecolor="#2b313a",
        showspikes=True, spikemode="across", spikesnap="cursor",
        spikethickness=1, spikedash="dash", spikecolor="#a0a6b2"
    )
    fig.update_yaxes(
        side="right", gridcolor="#1e2329", zeroline=False,
        linecolor="#2b313a", tickformat=",.2f",
        showspikes=True, spikemode="across+toaxis",
        spikesnap="cursor", spikethickness=1,
        spikedash="dash", spikecolor="#a0a6b2",
        showline=True, row=1, col=1, range=y_range
    )
    fig.update_yaxes(
        side="right", gridcolor="#1e2329", zeroline=False,
        linecolor="#2b313a", tickformat=",.2f",
        showspikes=True, spikemode="across",
        spikesnap="cursor", spikethickness=1,
        spikedash="dash", spikecolor="#a0a6b2",
        row=2, col=1
    )

    chart_col, panel_col = st.columns([3.6, 1.2])
    with chart_col:
        st.plotly_chart(
            fig, use_container_width=True,
            config={"scrollZoom": True, "displayModeBar": True, "displaylogo": False},
            key=f"{symbol.lower()}_{tf}_chart"
        )

    with panel_col:
        st.markdown("### 🎯 AI 即時操作建議")
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
            st.markdown(f"""
            <div style="background:rgba(30,35,41,.9);border:1px solid #0ECB81;
                        padding:12px;border-radius:6px;border-left:5px solid #0ECB81;">
                <div style="color:#0ECB81;font-weight:bold;">🟢 AI 智能多單 (LONG)</div>
                <div style="font-size:12px;">參考價格：{latest_close:,.2f}</div>
            </div>
            """, unsafe_allow_html=True)
        elif bool(latest["Short_Signal"]):
            st.markdown(f"""
            <div style="background:rgba(30,35,41,.9);border:1px solid #F6465D;
                        padding:12px;border-radius:6px;border-left:5px solid #F6465D;">
                <div style="color:#F6465D;font-weight:bold;">🔴 AI 智能空單 (SHORT)</div>
                <div style="font-size:12px;">參考價格：{latest_close:,.2f}</div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown("""
            <div style="background:rgba(30,35,41,.9);border:1px solid #2b313a;
                        padding:15px;border-radius:6px;text-align:center;
                        border-left:5px solid #f0b90b;">
                <div style="color:#f0b90b;font-weight:bold;">⏳ AI 觀望中 (WAIT)</div>
                <div style="font-size:11px;color:#848e9c;margin-top:4px;">
                    等待符合策略條件的市場機會
                </div>
            </div>
            """, unsafe_allow_html=True)

        st.caption("AI 模型分數不是實際交易勝率。")


# ============================================================
# 8. 主畫面
# ============================================================
if st.session_state.is_searched:
    query = st.session_state.search_query.upper()
    if "ETH" in query:
        table_name, symbol_name, pair_name = (
            "eth_contract_klines", "ETH", "ETH-USDT-SWAP"
        )
    else:
        table_name, symbol_name, pair_name = (
            "btc_contract_klines", "BTC", "BTC-USDT-SWAP"
        )

    selected_tf = st.radio(
        "時間週期（小波段／大波段）",
        ["5m", "15m", "1h", "4h"],
        horizontal=True,
        key="timeframe_select"
    )

    # Streamlit fragment 每 5 秒更新一次顯示。
    # 若安裝的 Streamlit 太舊而不支援 fragment，請升級 Streamlit。
    @st.fragment(run_every=5)
    def render_live_fragment(tbl, sym, pair, timeframe):
        render_live_kline_chart(tbl, sym, pair, timeframe)

    render_live_fragment(
        table_name, symbol_name, pair_name, selected_tf
    )

elif st.session_state.search_query:
    st.warning(
        f"找不到與「{st.session_state.search_query}」相關的圖表，"
        "請嘗試輸入 BTC 或 ETH。"
    )
    render_homepage()
else:
    render_homepage()
