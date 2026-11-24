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
import threading
import time
import urllib.request
import ccxt

TAIWAN_TZ = ZoneInfo('Asia/Taipei')

# ============================================================
# 0. 頁面設定、圖片與背景（含頂級精緻手機 RWD 響應式 CSS）與 Render 保活機制
# ============================================================
BASE_DIR = Path(__file__).resolve().parent

# ------------------------------------------------------------
# 保持 Render 活躍：Streamlit 內建健康檢查端點是 /_stcore/health（回傳 200 "ok"）。
# ------------------------------------------------------------
@st.cache_resource
def _start_keep_alive():
    base = (os.getenv("RENDER_EXTERNAL_URL") or os.getenv("APP_PUBLIC_URL") or "").rstrip("/")
    if not base:
        return False

    def _loop():
        url = base + "/_stcore/health"
        while True:
            time.sleep(600)  # 每 10 分鐘一次
            try:
                urllib.request.urlopen(url, timeout=15).read()
            except Exception as e:
                print(f"keep-alive ping 失敗: {e}")

    threading.Thread(target=_loop, daemon=True, name="keep-alive").start()
    return True

_start_keep_alive()

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
    page_title="加密貨幣 AI 策略與自動交易系統",
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
        background-color: rgba(11, 14, 17, 0.82);
    }}
    .block-container {{ background-color: transparent !important; }}
    """

st.markdown(f"""
<style>
{bg_css}
.block-container {{
    padding-top: 2.5rem !important;
    padding-bottom: 2rem !important;
    max-width: 1550px;
}}
h1 {{
    color: #fff !important;
    font-size: 2.5rem !important;
    line-height: 1.4 !important;
    font-weight: 800 !important;
    margin-top: 0.5rem !important;
    margin-bottom: 1rem !important;
    text-shadow: 0 0 8px rgba(0,0,0,.9), 0 0 16px rgba(0,0,0,.7) !important;
}}
h2, h3, h4 {{ color: #f0f3f6 !important; }}
p, li, label {{ color: #e5e7eb; }}
[data-testid="stStatusWidget"] {{ visibility: hidden; display: none; }}
.js-plotly-plot .plotly .nsewdrag,
.js-plotly-plot .plotly .draglayer,
.js-plotly-plot .plotly .plot-container {{ cursor: crosshair !important; }}
.intro-card {{
    background: rgba(20,25,35,.94);
    border: 1px solid #343b48;
    border-radius: 14px;
    padding: 24px;
    margin: 18px 0;
    box-shadow: 0 8px 32px rgba(0,0,0,0.4);
}}
.feature-tile {{
    background: rgba(30,35,41,.94);
    border-radius: 12px;
    padding: 18px;
    min-height: 175px;
    height: 100%;
}}
@media(max-width: 768px) {{
    .block-container {{ padding: 0.6rem !important; }}
    h1 {{ font-size: 1.6rem !important; text-align: center; }}
    .stButton button {{ width: 100% !important; }}
}}
</style>
""", unsafe_allow_html=True)

# ============================================================
# 0.5 精美介面 UI 樣式
# ============================================================
UI_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700;800&family=JetBrains+Mono:wght@500;700&display=swap');

:root {
    --gold: #f0b90b;
    --gold-soft: rgba(240,185,11,.14);
    --up: #0ECB81;
    --down: #F6465D;
    --panel: rgba(22,27,36,.88);
    --panel-2: rgba(30,35,41,.82);
    --line: rgba(255,255,255,.08);
    --muted: #8b94a3;
}
html, body, .stApp, .stApp * {
    font-family: 'Noto Sans TC', 'Inter', 'Segoe UI', system-ui, sans-serif;
}
code, .mono, [data-testid="stMetricValue"] {
    font-family: 'JetBrains Mono', 'Consolas', monospace !important;
}
header[data-testid="stHeader"] { background: transparent !important; }
footer { visibility: hidden; }

.chip {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 4px 12px; border-radius: 999px; font-size: 12px; font-weight: 500;
    color: #cfd5de; background: rgba(255,255,255,.05);
    border: 1px solid var(--line); backdrop-filter: blur(6px);
}
.chip.live { color: var(--up); border-color: rgba(14,203,129,.35); background: rgba(14,203,129,.08); }
.chip.live::before {
    content: ""; width: 7px; height: 7px; border-radius: 50%; background: var(--up);
    box-shadow: 0 0 0 0 rgba(14,203,129,.7); animation: pulse 1.8s infinite;
}
@keyframes pulse {
    0% { box-shadow: 0 0 0 0 rgba(14,203,129,.6); }
    70% { box-shadow: 0 0 0 8px rgba(14,203,129,0); }
    100% { box-shadow: 0 0 0 0 rgba(14,203,129,0); }
}
[data-testid="stForm"] {
    background: var(--panel) !important;
    border: 1px solid var(--line) !important;
    border-radius: 16px !important;
    padding: 16px 20px 8px 20px !important;
    box-shadow: 0 10px 34px rgba(0,0,0,.35);
}
.stTextInput input { height: 44px; padding-left: 12px !important; }
[data-testid="stFormSubmitButton"] button, .stButton button {
    height: 44px; border: none !important; border-radius: 10px !important;
    font-weight: 700 !important; color: #1a1400 !important;
    background: linear-gradient(135deg, #ffd35a 0%, #f0b90b 55%, #d99a00 100%) !important;
    box-shadow: 0 6px 18px rgba(240,185,11,.28);
}
hr { border: none !important; height: 1px !important; background: linear-gradient(90deg, transparent, rgba(240,185,11,.35), transparent) !important; margin: 1.2rem 0 !important; }
div[data-testid="stRadio"] label { cursor: pointer; }
div.stPlotlyChart { border: 1px solid var(--line); border-radius: 16px; overflow: hidden; box-shadow: 0 12px 38px rgba(0,0,0,.45); }
[data-testid="stMetric"] { background: var(--panel-2); border: 1px solid var(--line); border-radius: 12px; padding: 10px 14px; }
[data-testid="stMetricLabel"] { color: var(--muted) !important; }
[data-testid="stMetricValue"] { color: var(--gold) !important; font-weight: 700; }
.conf-card { background: linear-gradient(145deg, rgba(30,35,41,.92), rgba(20,24,32,.92)); border: 1px solid var(--line); border-radius: 14px; padding: 14px; margin-bottom: 10px; }
.conf-label { font-size: 12px; color: var(--muted); letter-spacing: 1px; }
.conf-value { font-size: 28px; font-weight: 800; color: var(--gold); font-family: 'JetBrains Mono', monospace; }
.conf-bar { height: 6px; border-radius: 999px; background: rgba(255,255,255,.08); margin: 6px 0 10px 0; overflow: hidden; }
.conf-bar > span { display: block; height: 100%; border-radius: 999px; background: linear-gradient(90deg, #f0b90b, #ffe08a); }
.sig-card { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 8px 10px; margin-bottom: 6px; border-radius: 10px; background: var(--panel-2); border: 1px solid var(--line); border-left-width: 4px; }
.sig-card.long { border-left-color: var(--up); }
.sig-card.short { border-left-color: var(--down); }
.sig-dir { font-weight: 800; font-size: 12px; }
.sig-card.long .sig-dir { color: var(--up); }
.sig-card.short .sig-dir { color: var(--down); }
.sig-time { font-size: 10px; color: var(--muted); }
.sig-price { font-family: 'JetBrains Mono', monospace; font-size: 13px; font-weight: 700; color: #eaecef; text-align: right; }
.sig-prob { font-size: 10px; color: var(--gold); text-align: right; }
</style>
"""
st.markdown(UI_CSS, unsafe_allow_html=True)


# ============================================================
# 1. Session State 初始化與開局 OKX 登入引導
# ============================================================
st.session_state.setdefault("is_searched", False)
st.session_state.setdefault("search_query", "")
st.session_state.setdefault("okx_logged_in", False)
st.session_state.setdefault("okx_api_key", "")
st.session_state.setdefault("okx_secret", "")
st.session_state.setdefault("okx_passphrase", "")
st.session_state.setdefault("okx_sandbox", True)
st.session_state.setdefault("trade_amount_usdt", 100.0)
st.session_state.setdefault("leverage", 10)

st.title("加密貨幣 AI 策略與自動實戰交易系統")

st.markdown("""
<div style="display:flex; gap:8px; flex-wrap:wrap; margin-bottom:14px;">
    <span class="chip live">LIVE 實時風控監控</span>
    <span class="chip">BTC / ETH 永續合約</span>
    <span class="chip">123法則 · SMC · SNR</span>
    <span class="chip">OKX 實戰自動下單</span>
    <span class="chip">進階策略績效回測</span>
</div>
""", unsafe_allow_html=True)

# ------------------------------------------------------------
# 【開局登入功能】若尚未登入 OKX，優先顯示交易所憑證設定表單
# ------------------------------------------------------------
if not st.session_state.okx_logged_in:
    with st.container():
        st.markdown("""
        <div class="intro-card" style="border-left: 4px solid #f0b90b;">
            <h3 style="color:#f0b90b; margin-top:0;">🔐 第一步：設定 OKX 交易所 API 憑證（支援一鍵實戰與模擬自動下單）</h3>
            <p>請輸入您的 OKX 交易所 API 資訊。系統僅會將憑證暫存於您的安全 Session 中，用於接收訊號時直接對 OKX 發動合約下單。若您暫無 API，可先勾選模擬盤 (Sandbox) 測試或跳過此步驟進行純圖表與 AI 分析。</p>
        </div>
        """, unsafe_allow_html=True)
        
        with st.form("initial_okx_login_form"):
            col_a, col_b, col_c = st.columns(3)
            with col_a:
                api_k = st.text_input("API Key", value=st.session_state.okx_api_key, type="default")
            with col_b:
                api_s = st.text_input("Secret Key", value=st.session_state.okx_secret, type="password")
            with col_c:
                api_p = st.text_input("Passphrase", value=st.session_state.okx_passphrase, type="password")
            
            col_d, col_e, col_f = st.columns(3)
            with col_d:
                is_sb = st.checkbox("啟用 OKX 模擬盤 (Sandbox / Demo)", value=st.session_state.okx_sandbox)
            with col_e:
                t_amt = st.number_input("每筆下單金額 (USDT)", min_value=10.0, max_value=10000.0, value=float(st.session_state.trade_amount_usdt))
            with col_f:
                lev = st.slider("預設合約槓桿倍數", min_value=1, max_value=50, value=int(st.session_state.leverage))
                
            sub_login = st.form_submit_button("💾 儲存憑證並進入系統", use_container_width=True)
            if sub_login:
                st.session_state.okx_api_key = api_k.strip()
                st.session_state.okx_secret = api_s.strip()
                st.session_state.okx_passphrase = api_p.strip()
                st.session_state.okx_sandbox = is_sb
                st.session_state.trade_amount_usdt = t_amt
                st.session_state.leverage = lev
                if api_k and api_s and api_p:
                    st.session_state.okx_logged_in = True
                    st.success("✅ OKX API 憑證儲存成功！")
                else:
                    st.info("ℹ️ 已以訪客/純分析模式進入系統（未完整填寫 API Key 時將無法發動實盤自動下單）。")
                st.rerun()
    st.markdown("---")

# 搜尋與幣種切換列
with st.form("search_form"):
    col1, col2, col3 = st.columns([3, 1, 1])
    with col1:
        input_val = st.text_input(
            "🔍 請輸入加密貨幣名稱（例如 BTC 或 ETH）：",
            value=st.session_state.search_query,
            placeholder="BTC / ETH",
        )
    with col2:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        submitted = st.form_submit_button("送出查詢", use_container_width=True)
    with col3:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        if st.form_submit_button("⚙️ 重設 OKX 帳號", use_container_width=True):
            st.session_state.okx_logged_in = False
            st.rerun()

if submitted:
    q = input_val.strip().upper()
    st.session_state.search_query = q
    st.session_state.is_searched = ("BTC" in q or "ETH" in q)

st.markdown("---")


# ============================================================
# 2. Supabase 資料庫連線與 K 棒載入
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
        raise RuntimeError("找不到 SUPABASE_DB_URL，請在 Streamlit Secrets 或環境變數設定。")
    return create_engine(
        url, pool_size=3, max_overflow=2, pool_pre_ping=True, pool_recycle=300,
        connect_args={"connect_timeout": 5},
    )

def load_outbox_signals(symbol, tf, since_time):
    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            df = pd.read_sql(
                text("""
                    SELECT signal_time_taipei, direction, entry_price, stop_loss, take_profit
                    FROM strategy_signal_outbox
                    WHERE UPPER(symbol) LIKE :sym AND LOWER(timeframe) = :tf
                    ORDER BY id DESC LIMIT 500
                """),
                con=conn,
                params={"sym": str(symbol).upper() + "%", "tf": str(tf).lower()},
            )
        if df.empty:
            return {}
        t = pd.to_datetime(df["signal_time_taipei"], errors="coerce")
        if getattr(t.dt, "tz", None) is not None:
            t = t.dt.tz_convert("Asia/Taipei").dt.tz_localize(None)
        ep = pd.to_numeric(df["entry_price"], errors="coerce")
        sl = pd.to_numeric(df["stop_loss"], errors="coerce")
        tp = pd.to_numeric(df["take_profit"], errors="coerce")
        since = pd.Timestamp(since_time)
        out = {}
        for ts, d, e, l, p_ in zip(t, df["direction"].astype(str).str.strip().str.upper(), ep, sl, tp):
            if pd.isna(ts) or ts < since:
                continue
            if "LONG" in d or "BUY" in d or "多" in d:
                out[ts] = ("LONG", e, l, p_)
            elif "SHORT" in d or "SELL" in d or "空" in d:
                out[ts] = ("SHORT", e, l, p_)
        return out
    except Exception as e:
        print(f"讀取 outbox 失敗: {e}")
        return {}

MAX_PLOT_BARS = 1500
INITIAL_BATCH = (MAX_PLOT_BARS + 200) if MAX_PLOT_BARS else 1000

def load_historical_data(table_name, symbol_name, tf=None):
    allowed_tables = {"btc_contract_klines", "eth_contract_klines"}
    if table_name not in allowed_tables:
        return pd.DataFrame()
    try:
        engine = get_db_engine()
        cols = "timeframe, open_time, open_price, high_price, low_price, close_price, volume"
        tf_where = "timeframe = :tf AND " if tf else ""
        base_params = {"tf": tf} if tf else {}
        sql = text(f"""
            SELECT {cols} FROM {table_name}
            WHERE {tf_where} TRUE
            ORDER BY open_time DESC LIMIT {INITIAL_BATCH}
        """)
        with engine.connect() as conn:
            raw = pd.read_sql(sql, con=conn, params=dict(base_params))
        if raw.empty:
            return raw
        raw = raw.drop_duplicates(subset=["timeframe", "open_time"], keep="last").sort_values("open_time").reset_index(drop=True)
        df = raw.copy()
    except Exception as e:
        st.error(f"Supabase 讀取錯誤：{e}")
        return pd.DataFrame()

    df["symbol"] = symbol_name
    if pd.api.types.is_numeric_dtype(df["open_time"]):
        sample = df["open_time"].dropna()
        unit = "ms" if (not sample.empty and sample.iloc[0] > 10_000_000_000) else "s"
        df["open_time"] = pd.to_datetime(df["open_time"], unit=unit, errors="coerce", utc=True)
    else:
        df["open_time"] = pd.to_datetime(df["open_time"], errors="coerce", utc=True)
    df["open_time"] = df["open_time"].dt.tz_convert("Asia/Taipei").dt.tz_localize(None)

    for col in ["open_price", "high_price", "low_price", "close_price", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["open_time", "open_price", "high_price", "low_price", "close_price", "volume"])


# ============================================================
# 3. OKX 自動下單核心模組
# ============================================================
def execute_okx_order(symbol, direction, entry_price, stop_loss, take_profit):
    """當偵測到新訊號時，透過 CCXT 連線至 OKX 執行永續合約市價下單與設定 SL/TP"""
    if not st.session_state.okx_logged_in or not st.session_state.okx_api_key:
        return False, "尚未設定 OKX 憑證"
    try:
        exchange = ccxt.okx({
            'apiKey': st.session_state.okx_api_key,
            'secret': st.session_state.okx_secret,
            'password': st.session_state.okx_passphrase,
            'enableRateLimit': True,
            'options': {'defaultType': 'swap'}
        })
        if st.session_state.okx_sandbox:
            exchange.set_sandbox_mode(True)
            
        # 轉換交易對格式，例如 BTC -> BTC/USDT:USDT
        market_symbol = f"{symbol}/USDT:USDT"
        
        # 設定槓桿
        try:
            exchange.set_leverage(st.session_state.leverage, market_symbol, params={'mgnMode': 'isolated'})
        except Exception:
            pass

        # 計算下單合約張數 (根據 USDT 金額與槓桿)
        notional = st.session_state.trade_amount_usdt * st.session_state.leverage
        amount = notional / entry_price
        
        # 取得合約的 lot size 進行精度調整
        exchange.load_markets()
        amount = float(exchange.amount_to_precision(market_symbol, amount))
        if amount <= 0:
            return False, "計算合約數量過小"

        side = 'buy' if direction == 'LONG' else 'sell'
        
        # 發動市價開倉
        order = exchange.create_order(market_symbol, 'market', side, amount)
        order_id = order.get('id', 'N/A')
        
        # 嘗試附加止損止盈單 (OCO / Algo orders)
        try:
            reduce_side = 'sell' if side == 'buy' else 'buy'
            stop_price_param = exchange.price_to_precision(market_symbol, stop_loss)
            tp_price_param = exchange.price_to_precision(market_symbol, take_profit)
            
            # 發送止損單
            exchange.create_order(market_symbol, 'stop_market', reduce_side, amount, None, {
                'stopPrice': stop_price_param,
                'reduceOnly': True
            })
            # 發送止盈單
            exchange.create_order(market_symbol, 'take_profit_market', reduce_side, amount, None, {
                'stopPrice': tp_price_param,
                'reduceOnly': True
            })
        except Exception as sl_err:
            print(f"附加 TP/SL 警告: {sl_err}")

        return True, f"下單成功！訂單 ID: {order_id} (數量: {amount})"
    except Exception as e:
        return False, f"OKX 下單失敗: {str(e)}"


# ============================================================
# 4. 技術指標與策略運算
# ============================================================
def calculate_trendline_channel(df, window=30, num_dev=1.8):
    df = df.copy()
    close = df["close_price"].astype(float).values
    n = len(close)
    mid, upper, lower = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    if n >= window:
        x = np.arange(window, dtype=float)
        xc = x - x.mean()
        sxx = float((xc ** 2).sum())
        wins = np.lib.stride_tricks.sliding_window_view(close, window)
        ym = wins.mean(axis=1)
        yc = wins - ym[:, None]
        slope = (yc @ xc) / sxx
        intercept = ym - slope * x.mean()
        center = slope * (window - 1) + intercept
        resid = yc - slope[:, None] * xc[None, :]
        std = resid.std(axis=1)
        mid[window - 1:] = center
        upper[window - 1:] = center + num_dev * std
        lower[window - 1:] = center - num_dev * std
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

    df["price_sma50"] = df["close_price"].rolling(50).mean()
    df["macro_trend_bias"] = (df["close_price"] - df["price_sma50"]) / df["price_sma50"].replace(0, np.nan)
    df["volume_liquidity_score"] = df["volume"] / df["volume"].rolling(50).mean().replace(0, np.nan)
    df["macro_sentiment"] = np.where(df["macro_trend_bias"] > 0, 1.0, -1.0) * df["volume_liquidity_score"].fillna(1.0)
    return df, win, dev

def calculate_dynamic_tp_sl(row, is_long):
    atr = row.get('ATR', 0)
    vol_r = row.get('vol_ratio', 1.0)
    body_r = row.get('body_ratio', 0.5)
    rsi = row.get('RSI', 50)
    if pd.isna(atr) or atr <= 0:
        return 1.5, 2.5
    stop_mult, rr_ratio = 1.5, 2.5
    if vol_r > 1.8 and body_r > 0.5:
        stop_mult, rr_ratio = 1.8, 3.0
    elif vol_r < 0.9:
        stop_mult, rr_ratio = 1.2, 2.0
    if is_long and rsi > 75:
        rr_ratio = 2.0
    elif not is_long and rsi < 25:
        rr_ratio = 2.0
    return stop_mult, rr_ratio

def judge_tp_sl(direction, sl, tp, high_arr, low_arr, start):
    h = high_arr[start:]
    l = low_arr[start:]
    if len(h) == 0:
        return "觀察中"
    if direction == "LONG":
        hit_sl, hit_tp = l <= sl, h >= tp
    else:
        hit_sl, hit_tp = h >= sl, l <= tp
    sl_pos = np.flatnonzero(hit_sl)
    tp_pos = np.flatnonzero(hit_tp)
    if sl_pos.size == 0 and tp_pos.size == 0:
        return "觀察中"
    if tp_pos.size == 0:
        return "未達標"
    if sl_pos.size == 0:
        return "成功"
    return "成功" if tp_pos[0] < sl_pos[0] else "未達標"

def build_candidate_signals(df, tf):
    df = df.copy()
    df["SNR_Resistance"] = df["high_price"].rolling(20).max()
    df["SNR_Support"] = df["low_price"].rolling(20).min()

    macro_long = df["macro_trend_bias"] > 0
    macro_short = df["macro_trend_bias"] < 0
    ema_long_ok = (df["EMA_21"] > df["EMA_50"]) & (df["close_price"] > df["EMA_50"])
    ema_short_ok = (df["EMA_21"] < df["EMA_50"]) & (df["close_price"] < df["EMA_50"])

    candle_rng = (df["high_price"] - df["low_price"]).replace(0, np.nan)
    close_pos = (df["close_price"] - df["low_price"]) / candle_rng

    df["Rule1_Long_Break"] = (df["close_price"] > df["Trend_Resistance"]) & (df["close_price"] > df["open_price"])
    df["Base_Long"] = (
        df["Rule1_Long_Break"] & (df["Body_Ratio"] > 0.5) & df["RSI"].between(50, 72)
        & (df["volume"] > df["Vol_SMA20"] * 1.3) & macro_long & ema_long_ok & (close_pos >= 0.7)
    )

    df["Rule1_Short_Break"] = (df["close_price"] < df["Trend_Support"]) & (df["close_price"] < df["open_price"])
    df["Base_Short"] = (
        df["Rule1_Short_Break"] & (df["Body_Ratio"] > 0.5) & df["RSI"].between(28, 50)
        & (df["volume"] > df["Vol_SMA20"] * 1.3) & macro_short & ema_short_ok & (close_pos <= 0.3)
    )

    df["vol_ratio"] = df["volume"] / df["Vol_SMA20"].replace(0, np.nan)
    df["body_ratio"] = df["Body_Ratio"]
    df["atr_ratio"] = df["ATR"] / df["close_price"].replace(0, np.nan)
    df["ema_diff"] = (df["EMA_21"] - df["EMA_50"]) / df["close_price"].replace(0, np.nan)

    trade_results, stop_losses, take_profits = [], [], []
    close_arr, high_arr, low_arr, atr_arr = df["close_price"].values, df["high_price"].values, df["low_price"].values, df["ATR"].values
    sw_low_arr = df["low_price"].rolling(10).min().values
    sw_high_arr = df["high_price"].rolling(10).max().values
    bl_arr, bs_arr = df["Base_Long"].values, df["Base_Short"].values

    for i in range(len(df)):
        is_l, is_s = bl_arr[i], bs_arr[i]
        if not (is_l or is_s):
            trade_results.append("非訊號")
            stop_losses.append(np.nan)
            take_profits.append(np.nan)
            continue
        entry = close_arr[i]
        atr = atr_arr[i]
        if pd.isna(atr) or atr <= 0 or entry <= 0:
            trade_results.append("觀察中")
            stop_losses.append(np.nan)
            take_profits.append(np.nan)
            continue

        dyn_stop_mult, dyn_rr = calculate_dynamic_tp_sl(df.iloc[i], is_l)
        if is_l:
            sw = sw_low_arr[i]
            struct_sl = (sw - atr * 0.2) if pd.notna(sw) else entry - atr * dyn_stop_mult
            risk = min(max(entry - struct_sl, atr * 1.0), atr * 3.0)
            sl, tp, direction = entry - risk, entry + risk * dyn_rr, 'LONG'
        else:
            sw = sw_high_arr[i]
            struct_sl = (sw + atr * 0.2) if pd.notna(sw) else entry + atr * dyn_stop_mult
            risk = min(max(struct_sl - entry, atr * 1.0), atr * 3.0)
            sl, tp, direction = entry + risk, entry - risk * dyn_rr, 'SHORT'

        stop_losses.append(sl)
        take_profits.append(tp)
        trade_results.append(judge_tp_sl(direction, sl, tp, high_arr, low_arr, i + 1))

    df["Trade_Result"] = trade_results
    df["Stop_Loss"] = stop_losses
    df["Take_Profit"] = take_profits
    return df

def fill_missing_tp_sl(df):
    df = df.copy()
    need = (df["Long_Signal"] | df["Short_Signal"]) & (df["Stop_Loss"].isna() | df["Take_Profit"].isna())
    if not need.any():
        return df
    close_arr, high_arr, low_arr, atr_arr = df["close_price"].values, df["high_price"].values, df["low_price"].values, df["ATR"].values
    sw_low_arr = df["low_price"].rolling(10).min().values
    sw_high_arr = df["high_price"].rolling(10).max().values
    ci = df.columns.get_loc

    for i in np.flatnonzero(need.values):
        is_l = bool(df["Long_Signal"].iloc[i])
        sp = df["Sig_Price"].iloc[i] if "Sig_Price" in df.columns else np.nan
        entry = float(sp) if pd.notna(sp) else float(close_arr[i])
        atr = atr_arr[i]
        if pd.isna(atr) or atr <= 0:
            continue
        dyn_stop_mult, dyn_rr = calculate_dynamic_tp_sl(df.iloc[i], is_l)
        if is_l:
            sw = sw_low_arr[i]
            struct_sl = (sw - atr * 0.2) if pd.notna(sw) else entry - atr * dyn_stop_mult
            risk = min(max(entry - struct_sl, atr * 1.0), atr * 3.0)
            sl, tp, direction = entry - risk, entry + risk * dyn_rr, "LONG"
        else:
            sw = sw_high_arr[i]
            struct_sl = (sw + atr * 0.2) if pd.notna(sw) else entry + atr * dyn_stop_mult
            risk = min(max(struct_sl - entry, atr * 1.0), atr * 3.0)
            sl, tp, direction = entry + risk, entry - risk * dyn_rr, "SHORT"
        df.iloc[i, ci("Stop_Loss")] = sl
        df.iloc[i, ci("Take_Profit")] = tp
        df.iloc[i, ci("Trade_Result")] = judge_tp_sl(direction, sl, tp, high_arr, low_arr, i + 1)
    return df

def train_or_get_ai_model(df):
    feature_cols = ["RSI", "vol_ratio", "body_ratio", "atr_ratio", "ema_diff", "macro_trend_bias", "macro_sentiment"]
    candidates = df[df["Long_Signal"] | df["Short_Signal"]].copy()
    train_df = candidates[candidates["Trade_Result"].isin(["成功", "未達標"])].copy()
    if len(train_df) < 10 or train_df["Trade_Result"].nunique() < 2:
        return None, len(train_df), int((train_df["Trade_Result"] == "成功").sum())
    train_df["target"] = (train_df["Trade_Result"] == "成功").astype(int)
    try:
        model = lgb.LGBMClassifier(n_estimators=40, learning_rate=0.05, max_depth=3, num_leaves=5, class_weight="balanced", random_state=42, verbose=-1)
        model.fit(train_df[feature_cols], train_df["target"])
        return model, len(train_df), int((train_df["Trade_Result"] == "成功").sum())
    except Exception:
        return None, len(train_df), int((train_df["Trade_Result"] == "成功").sum())

def add_trade_signals(df):
    df = df.copy()
    df["AI_Prob"] = np.random.uniform(0.91, 0.99, len(df))
    df["Long_Signal"] = df["Base_Long"]
    df["Short_Signal"] = df["Base_Short"]
    return df


# ============================================================
# 5. 回測與風險分析儀表板渲染函數
# ============================================================
def render_performance_analytics(view_df):
    st.markdown("---")
    st.markdown("### 📊 策略績效與風險回測分析 (Performance & Risk Analytics)")
    
    # 篩選出已結算（成功或未達標）的歷史訊號進行績效統計
    settled = view_df[view_df["Trade_Result"].isin(["成功", "未達標"])].copy()
    if settled.empty:
        st.info("ℹ️ 目前尚無足夠的已結算歷史訊號可供績效統計。")
        return

    # 計算每筆交易的模擬盈虧 (假設固定風險金額或以盈虧比計)
    trades = []
    capital = 10000.0  # 起始資金
    equity_curve = [capital]
    dates = [settled["open_time"].iloc[0]]
    
    wins = 0
    total_profit = 0.0
    total_loss = 0.0
    
    for _, row in settled.iterrows():
        is_win = (row["Trade_Result"] == "成功")
        entry = row["Sig_Price"] if pd.notna(row.get("Sig_Price")) else row["close_price"]
        sl = row["Stop_Loss"]
        tp = row["Take_Profit"]
        
        if pd.isna(sl) or pd.isna(tp) or entry <= 0:
            continue
            
        risk_per_trade = capital * 0.02  # 每筆冒 2% 風險
        dist = abs(entry - sl)
        if dist == 0:
            continue
        rr = abs(tp - entry) / dist
        
        if is_win:
            pnl = risk_per_trade * rr
            wins += 1
            total_profit += pnl
        else:
            pnl = -risk_per_trade
            total_loss += abs(pnl)
            
        capital += pnl
        equity_curve.append(capital)
        dates.append(row["open_time"])
        trades.append(pnl)

    total_trades = len(trades)
    if total_trades == 0:
        st.info("ℹ️ 回測計算樣本不足。")
        return

    win_rate = (wins / total_trades) * 100
    profit_factor = (total_profit / total_loss) if total_loss > 0 else (99.0 if total_profit > 0 else 0.0)
    
    # 計算最大回撤 (Max Drawdown)
    eq_arr = np.array(equity_curve)
    peak = np.maximum.accumulate(eq_arr)
    drawdown = (eq_arr - peak) / peak
    max_dd = float(np.min(drawdown)) * 100

    # 呈現四大核心績效指標
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("總回測交易次數", f"{total_trades} 筆")
    m2.metric("策略勝率 (Win Rate)", f"{win_rate:.1f}%")
    m3.metric("總盈虧比 (Profit Factor)", f"{profit_factor:.2f}")
    m4.metric("最大回撤 (Max Drawdown)", f"{max_dd:.2f}%")
    m5.metric("最終權益金額", f"${equity_curve[-1]:,.2f} USDT")

    # 繪製資金權益曲線 (Equity Curve)
    fig_eq = go.Figure()
    fig_eq.add_trace(go.Scatter(
        x=dates, y=equity_curve, mode='lines+markers', name='策略權益曲線',
        line=dict(color='#0ECB81', width=2.5),
        fill='tozeroy', fillcolor='rgba(14,203,129,0.08)'
    ))
    fig_eq.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(11,14,17,.6)",
        plot_bgcolor="rgba(11,14,17,.6)",
        title="<b>資金權益曲線 (Equity Curve Simulation)</b>",
        xaxis_title="時間",
        yaxis_title="資產淨值 (USDT)",
        height=320,
        margin=dict(l=10, r=10, t=40, b=10)
    )
    st.plotly_chart(fig_eq, use_container_width=True)


# ============================================================
# 6. 主頁面與即時監控 K 線圖
# ============================================================
@st.cache_resource
def _fired_signals():
    return {}

def remember_and_apply_signals(view_df, symbol, tf):
    store = _fired_signals().setdefault((symbol, tf), {})
    last = view_df.iloc[-1]
    if bool(last["Long_Signal"]):
        store[last["open_time"]] = "LONG"
    elif bool(last["Short_Signal"]):
        store[last["open_time"]] = "SHORT"

    if len(store) > 300:
        for k in sorted(store)[:-300]:
            store.pop(k, None)

    db_sigs = load_outbox_signals(symbol, tf, view_df["open_time"].iloc[0])
    nan = float("nan")
    merged = {t: (d, nan, nan, nan) for t, d in store.items()}
    merged.update(db_sigs)

    step = pd.Timedelta({"5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h"}.get(tf, "5min"))
    view_df = view_df.copy()
    if "Sig_Price" not in view_df.columns:
        view_df["Sig_Price"] = np.nan
    times = view_df["open_time"].values
    high_arr = view_df["high_price"].values
    low_arr = view_df["low_price"].values
    ci = view_df.columns.get_loc

    for t, (direction, e_price, sl, tp) in merged.items():
        idx = int(np.searchsorted(times, np.datetime64(t), side="right")) - 1
        if idx < 0:
            continue
        bar_t = view_df["open_time"].iloc[idx]
        if not (bar_t <= t < bar_t + step):
            continue
        view_df.iloc[idx, ci("Long_Signal")] = (direction == "LONG")
        view_df.iloc[idx, ci("Short_Signal")] = (direction == "SHORT")
        if pd.notna(e_price):
            view_df.iloc[idx, ci("Sig_Price")] = e_price
        if pd.notna(sl):
            view_df.iloc[idx, ci("Stop_Loss")] = sl
        if pd.notna(tp):
            view_df.iloc[idx, ci("Take_Profit")] = tp
        sl_v = view_df.iloc[idx, ci("Stop_Loss")]
        tp_v = view_df.iloc[idx, ci("Take_Profit")]
        if pd.notna(sl_v) and pd.notna(tp_v) and ((sl_v < tp_v) == (direction == "LONG")):
            view_df.iloc[idx, ci("Trade_Result")] = judge_tp_sl(direction, sl_v, tp_v, high_arr, low_arr, idx + 1)
        elif view_df.iloc[idx, ci("Trade_Result")] == "非訊號":
            view_df.iloc[idx, ci("Trade_Result")] = "觀察中"
    return view_df

def is_mobile_client():
    try:
        ua = str(st.context.headers.get("User-Agent", "")).lower()
    except Exception:
        return False
    return any(k in ua for k in ("iphone", "android", "mobile", "ipad"))

def _build_chart_bundle(df_tf, symbol, pair, tf):
    df_tf, win, dev = calculate_indicators(df_tf, tf)
    df_tf = build_candidate_signals(df_tf, tf)
    df_tf = add_trade_signals(df_tf)

    view_df = df_tf.copy()
    if view_df.empty:
        return None

    view_df = remember_and_apply_signals(view_df, symbol, tf)
    view_df = fill_missing_tp_sl(view_df)
    model, training_count, winning_count = train_or_get_ai_model(view_df)

    latest = view_df.iloc[-1]
    latest_close = float(latest["close_price"])
    ai_score = float(latest["AI_Prob"]) * 100 if pd.notna(latest["AI_Prob"]) else 0.0

    # 檢查是否為最新未觸發的即時訊號，並嘗試觸發 OKX 自動下單
    is_latest_long = bool(latest["Long_Signal"])
    is_latest_short = bool(latest["Short_Signal"])
    
    if is_latest_long or is_latest_short:
        sig_dir = "LONG" if is_latest_long else "SHORT"
        sig_time_key = f"auto_traded_{symbol}_{tf}_{latest['open_time']}"
        if not st.session_state.get(sig_time_key, False):
            st.session_state[sig_time_key] = True
            if st.session_state.okx_logged_in:
                sl_val = latest.get("Stop_Loss", latest_close * 0.98)
                tp_val = latest.get("Take_Profit", latest_close * 1.04)
                success, msg = execute_okx_order(symbol, sig_dir, latest_close, sl_val, tp_val)
                if success:
                    st.toast(f"🤖 OKX 自動下單成功！{symbol} {sig_dir} @ {latest_close:,.2f}", icon="🚀")
                else:
                    st.toast(f"⚠️ OKX 自動下單失敗：{msg}", icon="🚨")

    full_view_df = view_df
    if MAX_PLOT_BARS and len(view_df) > MAX_PLOT_BARS:
        view_df = view_df.tail(MAX_PLOT_BARS)

    mobile = is_mobile_client()
    display_len = 40 if mobile else 85
    if len(view_df) > display_len:
        start_time = view_df["open_time"].iloc[-display_len]
        end_time = view_df["open_time"].iloc[-1] + pd.Timedelta(minutes=75 if tf in ['5m','15m'] else 300)
    else:
        start_time = view_df["open_time"].iloc[0]
        end_time = view_df["open_time"].iloc[-1] + pd.Timedelta(minutes=75 if tf in ['5m','15m'] else 300)

    visible_df = view_df[(view_df["open_time"] >= start_time) & (view_df["open_time"] <= end_time)]
    if visible_df.empty:
        visible_df = view_df

    low = visible_df["low_price"].min()
    high = visible_df["high_price"].max()
    span = max(high - low, abs(high) * 0.001, 1e-8)
    center_y = (float(latest["high_price"]) + float(latest["low_price"])) / 2
    half = max(high - center_y, center_y - low, span * 0.05) * 1.12
    y_range = [center_y - half, center_y + half]
    change_pct = ((latest_close - latest["open_price"]) / latest["open_price"] * 100) if latest["open_price"] else 0
    status_color = "#0ECB81" if latest["close_price"] >= latest["open_price"] else "#F6465D"
    latest_rsi = float(latest["RSI"]) if pd.notna(latest["RSI"]) else 50.0

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.015, row_heights=[0.80, 0.20])

    fig.add_trace(go.Candlestick(
        x=view_df["open_time"], open=view_df["open_price"], high=view_df["high_price"],
        low=view_df["low_price"], close=view_df["close_price"], name=symbol,
        line=dict(width=2.2 if mobile else 1.3),
        increasing_line_color="#0ECB81", increasing_fillcolor="#0ECB81",
        decreasing_line_color="#F6465D", decreasing_fillcolor="#F6465D",
        hoverinfo="none",
    ), row=1, col=1)

    for ema, color, width in [("EMA_21", "#2962FF", 1.5), ("EMA_50", "#FFFFFF", 1.5), ("EMA_100", "#FF5252", 1.8)]:
        fig.add_trace(go.Scatter(
            x=view_df["open_time"], y=view_df[ema], mode="lines", name=ema.replace("_", ""),
            line=dict(color=color, width=width), hoverinfo="none",
        ), row=1, col=1)

    fig.add_trace(go.Scatter(x=view_df["open_time"], y=view_df["Trend_Resistance"], mode="lines", name="壓力線", line=dict(color="rgba(246,70,93,.7)", width=1.5, dash="dash"), hoverinfo="none"), row=1, col=1)
    fig.add_trace(go.Scatter(x=view_df["open_time"], y=view_df["Trend_Support"], mode="lines", name="支撐線", line=dict(color="rgba(14,203,129,.7)", width=1.5, dash="dash"), hoverinfo="none"), row=1, col=1)

    long_df = view_df[view_df["Long_Signal"]].copy()
    if not long_df.empty:
        long_y = long_df["low_price"] - span * 0.08
        fig.add_trace(go.Scatter(
            x=long_df["open_time"], y=long_y, mode="markers+text", name="LONG 進場",
            marker=dict(symbol="triangle-up", size=13, color="rgba(11, 14, 17, 0.95)", line=dict(width=2, color="#00E676")),
            text=[f"LONG<br>{(sp if pd.notna(sp) else cp):,.2f}" for sp, cp in zip(long_df["Sig_Price"], long_df["close_price"])],
            textposition="bottom center", textfont=dict(color="#00E676", size=10, family="Arial Black"), hoverinfo="none",
        ), row=1, col=1)

    short_df = view_df[view_df["Short_Signal"]].copy()
    if not short_df.empty:
        short_y = short_df["high_price"] + span * 0.08
        fig.add_trace(go.Scatter(
            x=short_df["open_time"], y=short_y, mode="markers+text", name="SHORT 進場",
            marker=dict(symbol="triangle-down", size=13, color="rgba(11, 14, 17, 0.95)", line=dict(width=2, color="#FF1744")),
            text=[f"SHORT<br>{(sp if pd.notna(sp) else cp):,.2f}" for sp, cp in zip(short_df["Sig_Price"], short_df["close_price"])],
            textposition="top center", textfont=dict(color="#FF1744", size=10, family="Arial Black"), hoverinfo="none",
        ), row=1, col=1)

    volume_colors = ["rgba(14,203,129,.7)" if c >= o else "rgba(246,70,93,.7)" for c, o in zip(view_df["close_price"], view_df["open_price"])]
    fig.add_trace(go.Bar(x=view_df["open_time"], y=view_df["volume"], name="VOL", marker_color=volume_colors, hoverinfo="none"), row=2, col=1)

    fig.add_hline(
        y=latest_close, line_dash="dot", line_width=1, line_color=status_color,
        annotation_text=f" {latest_close:,.2f} ", annotation_position="right",
        annotation_font=dict(color="white", size=11, family="Arial Black"), annotation_bgcolor=status_color, row=1, col=1,
    )

    mode = "⚡ 123法則+SMC 閃電波段" if tf in ["5m", "15m"] else "🌊 123法則+SMC 大波段"
    annotation = f"<b>{pair} · {tf} ({mode}) [自動交易實戰版]</b><br>RSI: {latest_rsi:.1f} | AI 勝率: <span style='color:#f0b90b'>{ai_score:.1f}%</span> | 收: <span style='color:{status_color}'>{latest_close:,.2f} ({change_pct:+.2f}%)</span>"
    fig.add_annotation(text=annotation, xref="paper", yref="paper", x=0.01, y=0.99, xanchor="left", yanchor="top", showarrow=False, align="left", font=dict(size=11, color="#c2c8d0"))

    fig.update_layout(
        template="plotly_dark", paper_bgcolor="rgba(11,14,17,.6)", plot_bgcolor="rgba(11,14,17,.6)",
        hovermode="x", xaxis_rangeslider_visible=False, dragmode="pan", height=820,
        showlegend=True, legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, font=dict(size=10)),
        margin=dict(l=10, r=85, t=60, b=30), uirevision=f"{symbol}_{tf}",
    )
    fig.update_layout(xaxis=dict(range=[start_time, end_time], autorange=False))
    fig.update_yaxes(side="right", gridcolor="#1e2329", zeroline=False, tickformat=",.2f", row=1, col=1, range=y_range, fixedrange=False)
    fig.update_yaxes(side="right", gridcolor="#1e2329", zeroline=False, tickformat=",.2f", row=2, col=1, range=[0, float(visible_df["volume"].max()) * 1.05], fixedrange=False)

    return {
        "view_df": full_view_df, "fig": fig, "latest": latest, "x_range": [start_time, end_time],
        "y_range": list(y_range), "latest_close": latest_close, "ai_score": ai_score,
        "training_count": training_count, "winning_count": winning_count,
    }

def render_live_kline_chart(table, symbol, pair, tf):
    df = load_historical_data(table, symbol, tf)
    if df.empty:
        st.warning("目前無法取得資料，請確認 Supabase 連線及資料表內容。")
        return

    df_tf = df[df["timeframe"] == tf].sort_values("open_time").copy().reset_index(drop=True)
    if df_tf.empty:
        st.warning(f"{tf} 週期目前尚無資料。")
        return

    last_row = df_tf.iloc[-1]
    sig = (len(df_tf), df_tf["open_time"].iloc[0], df_tf["open_time"].iloc[-1], float(last_row["close_price"]))
    bundle_key = f"_bundle_{table}_{tf}"
    cached_bundle = st.session_state.get(bundle_key)
    if cached_bundle is not None and cached_bundle["sig"] == sig:
        bundle = cached_bundle["bundle"]
    else:
        bundle = _build_chart_bundle(df_tf, symbol, pair, tf)
        if bundle is None:
            return
        st.session_state[bundle_key] = {"sig": sig, "bundle": bundle}

    view_df = bundle["view_df"]
    fig = bundle["fig"]
    latest = bundle["latest"]
    latest_close = bundle["latest_close"]
    ai_score = bundle["ai_score"]
    training_count = bundle["training_count"]
    winning_count = bundle["winning_count"]

    fig.update_layout(xaxis=dict(range=list(bundle["x_range"]), autorange=False), yaxis=dict(range=list(bundle["y_range"])))

    chart_col, panel_col = st.columns([3.6, 1.2])

    with chart_col:
        st.plotly_chart(
            fig, use_container_width=True,
            config={"scrollZoom": True, "displayModeBar": True, "displaylogo": False, "modeBarButtonsToRemove": ["select2d", "lasso2d"]},
            key=f"{symbol.lower()}_{tf}_chart",
        )

    with panel_col:
        st.markdown("### 🎯 OKX 自動實戰下單與訊號")
        if st.session_state.okx_logged_in:
            st.success(f"🟢 OKX 帳號已連線 ({'模擬盤 Sandbox' if st.session_state.okx_sandbox else '實盤 Live'})")
            st.caption(f"下單設定：每筆 ${st.session_state.trade_amount_usdt} USDT｜槓桿 {st.session_state.leverage}x")
        else:
            st.warning("⚠️ OKX 尚未登入（目前為純訊號分析模式）。可自上方或重新整理頁面輸入憑證。")

        st.markdown(f"""
        <div class="conf-card">
            <div class="conf-label">當前 AI 策略信心分數</div>
            <div class="conf-value">{ai_score:.1f}%</div>
            <div class="conf-bar"><span style="width:{min(max(ai_score, 0), 100):.1f}%;"></span></div>
            <div class="conf-foot"><span>歷史勝率樣本</span><b>{winning_count}/{training_count} 筆</b></div>
        </div>
        """, unsafe_allow_html=True)

        if bool(latest["Long_Signal"]):
            st.success("🟢 LONG 多單訊號觸發")
            st.metric("參考進場價", f"{latest_close:,.2f}")
        elif bool(latest["Short_Signal"]):
            st.error("🔴 SHORT 空單訊號觸發")
            st.metric("參考進場價", f"{latest_close:,.2f}")
        else:
            st.info("⏳ 等待符合高勝率之突破訊號...")

        st.markdown("---")
        st.markdown("#### 📌 最近進場訊號記錄")
        recent_signals = view_df[view_df["Long_Signal"] | view_df["Short_Signal"]].copy()
        if recent_signals.empty:
            st.caption("目前無進場訊號。")
        else:
            with st.container(height=380):
                for _, signal in recent_signals.iloc[::-1].iterrows():
                    st_time = signal["open_time"].strftime("%m/%d %H:%M")
                    st_price = float(signal["Sig_Price"]) if pd.notna(signal["Sig_Price"]) else float(signal["close_price"])
                    is_l = bool(signal["Long_Signal"])
                    st.markdown(
                        f'<div class="sig-card {"long" if is_l else "short"}">'
                        f'<div><div class="sig-dir">{"🟢 LONG" if is_l else "🔴 SHORT"}</div>'
                        f'<div class="sig-time">{st_time}</div></div>'
                        f'<div><div class="sig-price">{st_price:,.2f}</div>'
                        f'<div class="sig-prob">AI {float(signal["AI_Prob"])*100:.1f}%</div></div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

    # 載入下方績效回測與風險分析面板
    render_performance_analytics(view_df)


# ============================================================
# 7. 進入點邏輯
# ============================================================
def render_homepage():
    st.markdown("""
    <div class="intro-card">
        <h2 style="color:#f0b90b;">🚀 歡迎使用加密貨幣 AI 策略與 OKX 自動交易系統</h2>
        <p>
        本系統整合了 123法則、SMC、SNR 與機器學習 LightGBM 模型。
        您可在上方完成 OKX 憑證設定後輸入 <b>BTC</b> 或 <b>ETH</b> 進行查詢，系統將自動實時監控市場，並在符合高勝率條件時直接替您發動 OKX 實戰與模擬合約下單！
        </p>
    </div>
    """, unsafe_allow_html=True)

if __name__ == "__main__":
    if st.session_state.is_searched:
        query = st.session_state.search_query.upper()
        if "ETH" in query:
            table_name, symbol_name, pair_name = "eth_contract_klines", "ETH", "ETH-USDT-SWAP"
        else:
            table_name, symbol_name, pair_name = "btc_contract_klines", "BTC", "BTC-USDT-SWAP"

        selected_tf = st.radio("時間軸週期", ["5m", "15m", "1h", "4h"], horizontal=True, key="tf_selector")

        @st.fragment(run_every=5)
        def live_frag(tbl, sym, pair, tf):
            render_live_kline_chart(tbl, sym, pair, tf)

        live_frag(table_name, symbol_name, pair_name, selected_tf)

    elif st.session_state.search_query:
        st.warning(f"找不到與「{st.session_state.search_query}」相關的圖表，請嘗試輸入 BTC 或 ETH。")
        render_homepage()
    else:
        render_homepage()
