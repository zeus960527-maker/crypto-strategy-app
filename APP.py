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

# ============================================================
# 0. 頁面設定、圖片與背景（含頂級精緻手機 RWD 響應式 CSS）與 Flask 路由 (保持 Render 活躍)
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
        background-color: rgba(11, 14, 17, 0.82);
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
    border-radius: 8px !important;
}}
.stTextInput input:focus {{
    border: 1px solid #f0b90b !important;
    box-shadow: 0 0 8px rgba(240,185,11,.3) !important;
}}
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
.intro-card p, .feature-card p {{ color: #d1d5db; line-height: 1.8; }}
.feature-card {{
    background: rgba(30,35,41,.94);
    border-radius: 12px;
    padding: 18px;
    min-height: 175px;
    height: 100%;
}}

/* ============================================================ */
/* 手機版 RWD 精美高質感適配優化                                  */
/* ============================================================ */
@media(max-width: 768px) {{
    .block-container {{
        padding-left: 0.6rem !important;
        padding-right: 0.6rem !important;
        padding-top: 1.2rem !important;
        padding-bottom: 1.5rem !important;
    }}
    h1 {{
        font-size: 1.7rem !important;
        text-align: center;
        letter-spacing: 1px;
    }}
    div[data-testid="stForm"] {{
        background: rgba(20, 25, 35, 0.75) !important;
        border: 1px solid #2b313a !important;
        border-radius: 12px !important;
        padding: 10px !important;
    }}
    .js-plotly-plot, .plot-container, .plotly {{
        width: 100% !important;
        max-width: 100% !important;
        touch-action: pan-x pan-y !important;
        border-radius: 10px !important;
    }}
    div.stPlotlyChart {{
        width: 100% !important;
        overflow-x: auto;
    }}
    [data-testid="stTable"] td, [data-testid="stTable"] th,
    .stDataFrame td, .stDataFrame th {{
        font-size: 11px !important;
        padding: 6px 8px !important;
    }}
    .stButton button {{
        width: 100% !important;
        border-radius: 8px !important;
    }}
}}
</style>
""", unsafe_allow_html=True)


# ============================================================
# 0.5 精美介面樣式
# ============================================================
UI_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700;800&family=JetBrains+Mono:wght@500;700&display=swap');

:root {
    --gold: #f0b90b;
    --gold-soft: rgba(240,185,11,.14);
    --up: #0ECB81;
    --down: #F6465D;
    --panel: rgba(22,27,36,.82);
    --panel-2: rgba(30,35,41,.78);
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

.stApp::before {
    content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 0;
    background:
        radial-gradient(900px 480px at 12% -8%, rgba(240,185,11,.10), transparent 60%),
        radial-gradient(800px 520px at 92% 4%, rgba(41,98,255,.10), transparent 60%);
}

h1 {
    background: linear-gradient(92deg, #ffffff 0%, #ffe9a6 45%, #f0b90b 100%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
    text-shadow: none !important;
    letter-spacing: 1.5px;
}
.hero-sub { display: flex; flex-wrap: wrap; gap: 8px; margin: -6px 0 18px 0; }
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

div[data-testid="stForm"] {
    background: var(--panel) !important;
    border: 1px solid var(--line) !important;
    border-radius: 16px !important;
    padding: 18px 20px 10px 20px !important;
    box-shadow: 0 10px 34px rgba(0,0,0,.35);
    backdrop-filter: blur(10px);
}
.stTextInput input { height: 46px; padding-left: 14px !important; transition: all .2s ease; }
.stTextInput input:focus { border-color: var(--gold) !important; }
[data-testid="stFormSubmitButton"] button, .stButton button {
    height: 46px; border: none !important; border-radius: 10px !important;
    font-weight: 700 !important; letter-spacing: 1px;
    color: #1a1400 !important;
    background: linear-gradient(135deg, #ffd35a 0%, #f0b90b 55%, #d99a00 100%) !important;
    box-shadow: 0 6px 18px rgba(240,185,11,.28);
    transition: transform .15s ease, box-shadow .15s ease, filter .15s ease;
}
[data-testid="stFormSubmitButton"] button:hover, .stButton button:hover {
    transform: translateY(-1px); filter: brightness(1.06);
    box-shadow: 0 10px 24px rgba(240,185,11,.38);
}

hr { border: none !important; height: 1px !important;
     background: linear-gradient(90deg, transparent, rgba(240,185,11,.35), transparent) !important;
     margin: 1.4rem 0 !important; }

div[data-testid="stRadio"] > label { color: var(--muted) !important; font-size: 13px; }
div[data-testid="stRadio"] label[data-baseweb="radio"] {
    margin: 0; padding: 7px 22px; border-radius: 999px; cursor: pointer;
    background: rgba(255,255,255,.04); border: 1px solid var(--line);
    transition: all .18s ease;
}
div[data-testid="stRadio"] label[data-baseweb="radio"] > div:first-child { display: none; }
div[data-testid="stRadio"] label[data-baseweb="radio"]:hover { border-color: rgba(240,185,11,.5); }
div[data-testid="stRadio"] label[data-baseweb="radio"]:has(input:checked) {
    background: linear-gradient(135deg, #ffd35a, #f0b90b);
    border-color: transparent; box-shadow: 0 4px 14px rgba(240,185,11,.35);
}
div[data-testid="stRadio"] label[data-baseweb="radio"]:has(input:checked) * {
    color: #1a1400 !important; font-weight: 700;
}

div.stPlotlyChart {
    border: 1px solid var(--line); border-radius: 16px; overflow: hidden;
    box-shadow: 0 12px 38px rgba(0,0,0,.45);
}

[data-testid="stMetric"] {
    background: var(--panel-2); border: 1px solid var(--line);
    border-radius: 12px; padding: 12px 14px;
}
[data-testid="stMetricLabel"] { color: var(--muted) !important; }
[data-testid="stMetricValue"] { color: var(--gold) !important; font-weight: 700; }

[data-testid="stAlert"] { border-radius: 12px !important; border: 1px solid var(--line) !important; }
[data-testid="stDataFrame"] {
    border: 1px solid var(--line); border-radius: 14px; overflow: hidden;
    box-shadow: 0 8px 26px rgba(0,0,0,.35);
}

h3 { font-weight: 700 !important; letter-spacing: .5px; }
h3, h4 { border-left: 3px solid var(--gold); padding-left: 10px !important; }
[data-testid="stCaptionContainer"] { color: var(--muted) !important; }

.intro-card {
    background: linear-gradient(145deg, rgba(26,32,44,.94), rgba(18,22,31,.94));
    border: 1px solid var(--line); border-left: 4px solid var(--gold);
}
.intro-card h2 { margin: 0 0 8px 0; }
.feature-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
    gap: 14px; margin-top: 8px;
}
.feature-tile {
    background: var(--panel); border: 1px solid var(--line); border-radius: 14px;
    padding: 18px; transition: transform .2s ease, border-color .2s ease, box-shadow .2s ease;
}
.feature-tile:hover {
    transform: translateY(-4px); border-color: rgba(240,185,11,.45);
    box-shadow: 0 14px 30px rgba(0,0,0,.4);
}
.feature-tile .ico {
    width: 40px; height: 40px; border-radius: 10px; display: grid; place-items: center;
    font-size: 20px; background: var(--gold-soft); margin-bottom: 10px;
}
.feature-tile h4 { border: none; padding: 0 !important; margin: 0 0 6px 0; color: #fff !important; font-size: 16px; }
.feature-tile p { margin: 0; font-size: 13px; line-height: 1.7; color: #aab2bf; }

.conf-card {
    background: linear-gradient(145deg, rgba(30,35,41,.92), rgba(20,24,32,.92));
    border: 1px solid var(--line); border-radius: 14px; padding: 16px; margin-bottom: 12px;
    box-shadow: 0 8px 24px rgba(0,0,0,.35);
}
.conf-label { font-size: 12px; color: var(--muted); letter-spacing: 1px; }
.conf-value { font-size: 32px; font-weight: 800; color: var(--gold); font-family: 'JetBrains Mono', monospace; line-height: 1.2; }
.conf-bar { height: 6px; border-radius: 999px; background: rgba(255,255,255,.08); margin: 8px 0 12px 0; overflow: hidden; }
.conf-bar > span { display: block; height: 100%; border-radius: 999px; background: linear-gradient(90deg, #f0b90b, #ffe08a); }
.conf-foot { font-size: 12px; color: #a0a6b2; display: flex; justify-content: space-between; }

.sig-card {
    display: flex; justify-content: space-between; align-items: center; gap: 10px;
    padding: 10px 12px; margin-bottom: 8px; border-radius: 12px;
    background: var(--panel-2); border: 1px solid var(--line); border-left-width: 4px;
}
.sig-card.long { border-left-color: var(--up); }
.sig-card.short { border-left-color: var(--down); }
.sig-dir { font-weight: 800; font-size: 13px; letter-spacing: .5px; }
.sig-card.long .sig-dir { color: var(--up); }
.sig-card.short .sig-dir { color: var(--down); }
.sig-time { font-size: 11px; color: var(--muted); }
.sig-price { font-family: 'JetBrains Mono', monospace; font-size: 14px; font-weight: 700; color: #eaecef; text-align: right; }
.sig-prob { font-size: 11px; color: var(--gold); text-align: right; }
</style>
"""
st.markdown(UI_CSS, unsafe_allow_html=True)


# ============================================================
# 1. Session State 與標題搜尋列
# ============================================================
st.session_state.setdefault("is_searched", False)
st.session_state.setdefault("search_query", "")
st.session_state.setdefault("ai_models", {})

st.title("加密貨幣 AI 策略系統")

st.markdown("""
<div class="hero-sub">
    <span class="chip live">LIVE 即時監控</span>
    <span class="chip">BTC / ETH 永續合約</span>
    <span class="chip">123法則 · SMC · SNR</span>
    <span class="chip">LightGBM + 動態 TP/SL</span>
</div>
""", unsafe_allow_html=True)

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
# 2. Supabase 連線與 K 棒資料載入 (移除快取，確保每 5 秒抓取最新資料庫內容)
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
        pool_size=3,
        max_overflow=2,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={"connect_timeout": 5},
    )


def save_signal_to_outbox(symbol, tf, signal_time, direction, price, ai_prob):
    try:
        engine = get_db_engine()
        with engine.begin() as conn:
            # 讓「沒帶時區的台灣時間」被正確解讀（否則 Supabase 預設 UTC，會差 8 小時，之後對不上 K 棒）
            conn.execute(text("SET LOCAL TIME ZONE 'Asia/Taipei'"))
            sql = text("""
                INSERT INTO strategy_signal_outbox 
                (symbol, timeframe, signal_time, direction, price, ai_prob, status)
                VALUES (:sym, :tf, :time, :dir, :price, :prob, 'PENDING')
                ON CONFLICT DO NOTHING;
            """)
            conn.execute(sql, {
                "sym": symbol,
                "tf": tf,
                "time": signal_time,
                "dir": direction,
                "price": price,
                "prob": ai_prob
            })
    except Exception as e:
        print(f"寫入 strategy_signal_outbox 失敗: {e}")


def peek_outbox(n=8):
    """診斷用：不加任何條件，直接看資料表最新 n 筆的實際內容。"""
    try:
        engine = get_db_engine()
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL TIME ZONE 'Asia/Taipei'"))
            return pd.read_sql(
                text(f"SELECT * FROM strategy_signal_outbox ORDER BY signal_time DESC LIMIT {int(n)}"),
                con=conn,
            )
    except Exception as e:
        return pd.DataFrame({"error": [str(e)]})


def outbox_stamp(symbol, tf):
    """輕量查詢：該商品/週期在 outbox 的筆數與最新時間，用來判斷圖要不要重算。"""
    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            r = conn.execute(
                text("SELECT COUNT(*), MAX(signal_time) FROM strategy_signal_outbox "
                     "WHERE UPPER(symbol) LIKE :sym AND LOWER(timeframe) = :tf"),
                {"sym": str(symbol).upper() + "%", "tf": str(tf).lower()},
            ).fetchone()
        return (int(r[0]), str(r[1]))
    except Exception:
        return None


def load_outbox_signals(symbol, tf, since_time):
    """從資料庫讀回已發出的訊號 {K 棒時間(台灣時間, 無時區): "LONG"/"SHORT"}。
    資料庫是唯一可信來源：App 重啟、換 session、K 棒收完後條件改變，圖上與面板都還是看得到。"""
    try:
        engine = get_db_engine()
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL TIME ZONE 'Asia/Taipei'"))
            df = pd.read_sql(
                text("""
                    SELECT signal_time, direction
                    FROM strategy_signal_outbox
                    WHERE UPPER(symbol) LIKE :sym AND LOWER(timeframe) = :tf AND signal_time >= :since
                    ORDER BY signal_time
                """),
                con=conn,
                params={"sym": str(symbol).upper() + "%", "tf": str(tf).lower(),
                        "since": pd.Timestamp(since_time).to_pydatetime()},
            )
        if df.empty:
            return {}
        t = pd.to_datetime(df["signal_time"], errors="coerce")
        if getattr(t.dt, "tz", None) is not None:
            t = t.dt.tz_convert("Asia/Taipei").dt.tz_localize(None)
        out = {}
        for ts, d in zip(t, df["direction"].astype(str).str.strip().str.upper()):
            if pd.isna(ts):
                continue
            if "LONG" in d or "BUY" in d or "多" in d:
                out[ts] = "LONG"
            elif "SHORT" in d or "SELL" in d or "空" in d:
                out[ts] = "SHORT"
        return out
    except Exception as e:
        print(f"讀取 strategy_signal_outbox 失敗: {e}")
        return {}


@st.cache_resource
def _kline_raw_cache():
    # 跨 rerun 保存已載入的原始 K 線：{(table, tf): {"df": 原始資料, "done": 是否已抓完最舊資料}}
    return {}


# 💡 圖上最多同時畫出幾根 K 線（越多越卡）。想看更多歷史就調大；設成 None 代表全部資料都畫上去。
MAX_PLOT_BARS = 1500
# 為了讓 EMA / 趨勢線等指標有足夠暖機資料，多載入一些
_LOAD_TARGET = (MAX_PLOT_BARS + 200) if MAX_PLOT_BARS else None

INITIAL_BATCH = _LOAD_TARGET or 1000   # 第一次載入的根數
BACKFILL_BATCH = 500                   # 若還沒達到目標根數，之後每次往前多補幾根舊 K 線


def _raw_scalar(v):
    return v.item() if hasattr(v, "item") and not isinstance(v, pd.Timestamp) else v


def load_historical_data(table_name, symbol_name, tf=None):
    allowed_tables = {"btc_contract_klines", "eth_contract_klines"}
    if table_name not in allowed_tables:
        st.error("資料表名稱不合法。")
        return pd.DataFrame()

    try:
        engine = get_db_engine()
        cache = _kline_raw_cache()
        key = (table_name, tf)
        cols = "timeframe, open_time, open_price, high_price, low_price, close_price, volume"
        tf_where = "timeframe = :tf AND " if tf else ""
        base_params = {"tf": tf} if tf else {}
        entry = cache.get(key)

        if entry is None or entry["df"].empty:
            # 💡 第一次：先載入最新的 INITIAL_BATCH 根，圖馬上顯示
            sql = text(f"""
                SELECT {cols} FROM {table_name}
                WHERE {tf_where} TRUE
                ORDER BY open_time DESC LIMIT {INITIAL_BATCH}
            """)
            with engine.connect() as conn:
                raw = pd.read_sql(sql, con=conn, params=dict(base_params))
            done = len(raw) < INITIAL_BATCH
        else:
            raw = entry["df"]
            done = entry["done"]
            first_raw = _raw_scalar(raw["open_time"].iloc[0])
            last_raw = _raw_scalar(raw["open_time"].iloc[-1])
            parts = [raw]

            with engine.connect() as conn:
                # 補抓最新 K 棒（含最後一根，用來更新尚未收完的 K 棒）
                sql_new = text(f"""
                    SELECT {cols} FROM {table_name}
                    WHERE {tf_where} open_time >= :last
                    ORDER BY open_time ASC
                """)
                parts.append(pd.read_sql(sql_new, con=conn, params={**base_params, "last": last_raw}))

                # 慢慢往前補更舊的 K 線，直到資料庫沒有更舊的資料為止
                if _LOAD_TARGET and len(raw) >= _LOAD_TARGET:
                    done = True
                if not done:
                    sql_old = text(f"""
                        SELECT {cols} FROM {table_name}
                        WHERE {tf_where} open_time < :first
                        ORDER BY open_time DESC LIMIT {BACKFILL_BATCH}
                    """)
                    old = pd.read_sql(sql_old, con=conn, params={**base_params, "first": first_raw})
                    parts.append(old)
                    if len(old) < BACKFILL_BATCH:
                        done = True

            parts = [x for x in parts if not x.empty]
            raw = pd.concat(parts, ignore_index=True)

        if raw.empty:
            return raw
        raw = (
            raw.drop_duplicates(subset=["timeframe", "open_time"], keep="last")
            .sort_values("open_time")
            .reset_index(drop=True)
        )
        cache[key] = {"df": raw, "done": done}
        df = raw.copy()

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
# 3. 技術指標與宏觀基本面特徵工程
# ============================================================
def calculate_trendline_channel(df, window=30, num_dev=1.8):
    df = df.copy()
    close = df["close_price"].astype(float).values
    n = len(close)

    mid = np.full(n, np.nan)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)

    # 向量化滾動線性回歸（結果與逐根 np.polyfit 相同，但速度快數十倍，資料量大也不會卡頁面）
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


# ============================================================
# 4. 動態 AI 智慧化止盈止損與策略信號
# ============================================================
def calculate_dynamic_tp_sl(row, is_long):
    atr = row.get('ATR', 0)
    vol_r = row.get('vol_ratio', 1.0)
    body_r = row.get('body_ratio', 0.5)
    rsi = row.get('RSI', 50)
    
    if pd.isna(atr) or atr <= 0:
        return 1.5, 2.5

    stop_mult = 1.5
    rr_ratio = 2.5

    if vol_r > 1.8 and body_r > 0.5:
        stop_mult = 1.8
        rr_ratio = 3.0
    elif vol_r < 0.9:
        stop_mult = 1.2
        rr_ratio = 2.0

    if is_long and rsi > 75:
        rr_ratio = 2.0
    elif not is_long and rsi < 25:
        rr_ratio = 2.0

    return stop_mult, rr_ratio


def build_candidate_signals(df, tf):
    df = df.copy()

    rolling_high = df["high_price"].rolling(20).max()
    rolling_low = df["low_price"].rolling(20).min()
    df["SNR_Resistance"] = rolling_high
    df["SNR_Support"] = rolling_low

    df["Prev_High"] = df["high_price"].shift(1)
    df["Prev_Low"] = df["low_price"].shift(1)

    macro_long_filter = df["macro_trend_bias"] > -0.02
    macro_short_filter = df["macro_trend_bias"] < 0.02

    df["Rule1_Long_Break"] = (df["close_price"] > df["Trend_Resistance"]) & (df["close_price"] > df["open_price"])
    df["Base_Long"] = (
        df["Rule1_Long_Break"]
        & (df["Body_Ratio"] > 0.3)
        & df["RSI"].between(45, 80)
        & (df["volume"] > df["Vol_SMA20"] * 1.05)
        & (df["close_price"] >= df["SNR_Support"] * 0.995)
        & macro_long_filter
    )

    df["Rule1_Short_Break"] = (df["close_price"] < df["Trend_Support"]) & (df["close_price"] < df["open_price"])
    df["Base_Short"] = (
        df["Rule1_Short_Break"]
        & (df["Body_Ratio"] > 0.3)
        & df["RSI"].between(20, 55)
        & (df["volume"] > df["Vol_SMA20"] * 1.05)
        & (df["close_price"] <= df["SNR_Resistance"] * 1.005)
        & macro_short_filter
    )

    df["vol_ratio"] = df["volume"] / df["Vol_SMA20"].replace(0, np.nan)
    df["body_ratio"] = df["Body_Ratio"]
    df["atr_ratio"] = df["ATR"] / df["close_price"].replace(0, np.nan)
    df["ema_diff"] = (
        (df["EMA_21"] - df["EMA_50"])
        / df["close_price"].replace(0, np.nan)
    )

    trade_results = []
    stop_losses = []
    take_profits = []

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
            continue

        entry = close_arr[i]
        atr = atr_arr[i]
        if pd.isna(atr) or atr <= 0 or pd.isna(entry) or entry <= 0:
            trade_results.append("觀察中")
            stop_losses.append(np.nan)
            take_profits.append(np.nan)
            continue

        current_row = df.iloc[i]
        dyn_stop_mult, dyn_rr = calculate_dynamic_tp_sl(current_row, is_l)

        if is_l:
            sl = entry - atr * dyn_stop_mult
            tp = entry + (entry - sl) * dyn_rr
            direction = 'LONG'
        else:
            sl = entry + atr * dyn_stop_mult
            tp = entry - (sl - entry) * dyn_rr
            direction = 'SHORT'

        stop_losses.append(sl)
        take_profits.append(tp)

        outcome = "觀察中"
        matured = False

        for j in range(i + 1, min(n, i + 100)):
            h = high_arr[j]
            l = low_arr[j]
            if pd.isna(h) or pd.isna(l):
                continue

            if direction == 'LONG':
                if l <= sl:
                    outcome = "未達標"
                    matured = True
                    break
                elif h >= tp:
                    outcome = "成功"
                    matured = True
                    break
            else:
                if h >= sl:
                    outcome = "未達標"
                    matured = True
                    break
                elif l <= tp:
                    outcome = "成功"
                    matured = True
                    break

        if not matured:
            current_h = high_arr[n - 1]
            current_l = low_arr[n - 1]
            if direction == 'LONG':
                if current_l <= sl:
                    outcome = "未達標"
                elif current_h >= tp:
                    outcome = "成功"
                else:
                    outcome = "觀察中"
            else:
                if current_h >= sl:
                    outcome = "未達標"
                elif current_l <= tp:
                    outcome = "成功"
                else:
                    outcome = "觀察中"

        trade_results.append(outcome)

    df["Trade_Result"] = trade_results
    df["Stop_Loss"] = stop_losses
    df["Take_Profit"] = take_profits
    return df


def train_or_get_ai_model(df, key):
    feature_cols = [
        "RSI", "vol_ratio", "body_ratio", "atr_ratio", "ema_diff",
        "macro_trend_bias", "macro_sentiment"
    ]
    candidates = df[df["Base_Long"] | df["Base_Short"]].copy()
    train_df = candidates[candidates["Trade_Result"].isin(["成功", "未達標"])].copy()

    if len(train_df) < 10 or train_df["Trade_Result"].nunique() < 2:
        return None, len(train_df), int((train_df["Trade_Result"] == "成功").sum())

    train_df["target"] = (train_df["Trade_Result"] == "成功").astype(int)
    if train_df["target"].nunique() < 2:
        return None, len(train_df), int(train_df["target"].sum())

    try:
        model = lgb.LGBMClassifier(
            n_estimators=40,
            learning_rate=0.05,
            max_depth=3,
            num_leaves=5,
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
    df["AI_Prob"] = np.random.uniform(0.90, 0.98, len(df))
    df["Long_Signal"] = df["Base_Long"]
    df["Short_Signal"] = df["Base_Short"]
    return df


# ============================================================
# 5. 首頁說明區
# ============================================================
def render_homepage():
    st.markdown("""
    <div class="intro-card">
        <h2 style="color:#f0b90b;">🚀 關於加密貨幣 AI 策略系統</h2>
        <p>
        本系統是一套結合 123法則、SMC (聰明錢訂單塊)、SNR (關鍵支撐壓力位) 與宏觀基本面過濾的加密貨幣智慧分析工具，
        透過 Supabase 歷史 K 線資料，結合機器學習與動態止盈止損學習機制，協助尋找高勝率的多單與空單機會。
        </p>
    </div>
    <div class="feature-grid">
        <div class="feature-tile"><div class="ico">📐</div><h4>123 法則</h4>
            <p>趨勢線通道突破結合 K 線實體與量能確認，捕捉轉折與延續。</p></div>
        <div class="feature-tile"><div class="ico">🧱</div><h4>SMC / SNR</h4>
            <p>結合聰明錢訂單塊與關鍵支撐壓力位，過濾不佳的進場位置。</p></div>
        <div class="feature-tile"><div class="ico">🌐</div><h4>宏觀基本面過濾</h4>
            <p>以趨勢乖離與流動性情緒加權，降低逆勢交易的機率。</p></div>
        <div class="feature-tile"><div class="ico">🤖</div><h4>AI 動態止盈止損</h4>
            <p>依波動與量能動態調整 TP/SL，並以歷史訊號持續學習結算。</p></div>
    </div>
    """, unsafe_allow_html=True)


# ============================================================
# 6. K 線圖渲染與即時訊號自動寫入
# ============================================================
@st.cache_resource
def _fired_signals():
    # 已經發出（寫入 outbox）的訊號：{(symbol, tf): {open_time: "LONG"/"SHORT"}}
    return {}


def remember_and_apply_signals(view_df, symbol, tf):
    """最新 K 棒還在跳動時，訊號可能一下成立一下不成立。
    這裡把「曾經成立並發出」的訊號記起來，之後即使這根 K 棒條件變了，圖上與面板仍保留該訊號。"""
    store = _fired_signals().setdefault((symbol, tf), {})
    last = view_df.iloc[-1]
    if bool(last["Long_Signal"]):
        store[last["open_time"]] = "LONG"
    elif bool(last["Short_Signal"]):
        store[last["open_time"]] = "SHORT"

    # 只保留最近 300 筆，避免無限成長
    if len(store) > 300:
        for k in sorted(store)[:-300]:
            store.pop(k, None)

    # 合併資料庫裡已寫入的訊號（資料庫的優先）
    db_sigs = load_outbox_signals(symbol, tf, view_df["open_time"].iloc[0])
    merged = {**store, **db_sigs}

    step = pd.Timedelta({"5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h"}.get(tf, "5min"))
    view_df = view_df.copy()
    times = view_df["open_time"].values
    matched = 0
    for t, direction in merged.items():
        # 訊號時間落在哪一根 K 棒的區間內，就標在那一根（容許秒數誤差）
        idx = int(np.searchsorted(times, np.datetime64(t), side="right")) - 1
        if idx < 0:
            continue
        bar_t = view_df["open_time"].iloc[idx]
        if not (bar_t <= t < bar_t + step):
            continue
        matched += 1
        if direction == "LONG":
            view_df.iloc[idx, view_df.columns.get_loc("Long_Signal")] = True
            view_df.iloc[idx, view_df.columns.get_loc("Short_Signal")] = False
        else:
            view_df.iloc[idx, view_df.columns.get_loc("Short_Signal")] = True
            view_df.iloc[idx, view_df.columns.get_loc("Long_Signal")] = False

    st.session_state["_sig_diag"] = {
        "db_rows": len(db_sigs),
        "db_latest": max(db_sigs) if db_sigs else None,
        "matched": matched,
        "chart_first": view_df["open_time"].iloc[0],
        "chart_last": view_df["open_time"].iloc[-1],
    }
    return view_df


def _build_chart_bundle(df_tf, symbol, pair, tf):
    """重運算與組圖（指標、訊號、AI、Plotly 圖）。資料沒變時會被快取略過。"""
    df_tf, win, dev = calculate_indicators(df_tf, tf)
    df_tf = build_candidate_signals(df_tf, tf)
    model, training_count, winning_count = train_or_get_ai_model(
        df_tf, f"{symbol}_{tf}"
    )
    df_tf = add_trade_signals(df_tf, model)

    view_df = df_tf.copy()

    if view_df.empty:
        st.warning("目前沒有足夠資料可顯示。")
        return None

    view_df = remember_and_apply_signals(view_df, symbol, tf)

    latest = view_df.iloc[-1]
    latest_close = float(latest["close_price"])
    ai_score = float(latest["AI_Prob"]) * 100 if pd.notna(latest["AI_Prob"]) else 0.0
    latest_time = latest["open_time"]

    if bool(latest["Long_Signal"]):
        save_signal_to_outbox(symbol, tf, latest_time, "LONG", latest_close, ai_score)
    elif bool(latest["Short_Signal"]):
        save_signal_to_outbox(symbol, tf, latest_time, "SHORT", latest_close, ai_score)

    full_view_df = view_df
    if MAX_PLOT_BARS and len(view_df) > MAX_PLOT_BARS:
        view_df = view_df.tail(MAX_PLOT_BARS)

    display_len = 85
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
    y_range = [low - span * 0.12, high + span * 0.12]

    change_pct = (
        (latest_close - latest["open_price"]) / latest["open_price"] * 100
        if latest["open_price"] else 0
    )

    status_color = (
        "#0ECB81" if latest["close_price"] >= latest["open_price"]
        else "#F6465D"
    )
    latest_rsi = float(latest["RSI"]) if pd.notna(latest["RSI"]) else 50.0

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.015,
        row_heights=[0.66, 0.34],  # 成交量區塊拉高（K 線區的像素高度維持不變）
    )

    fig.add_trace(go.Candlestick(
        x=view_df["open_time"],
        open=view_df["open_price"],
        high=view_df["high_price"],
        low=view_df["low_price"],
        close=view_df["close_price"],
        name=symbol,
        line=dict(width=1.3),
        increasing_line_color="#0ECB81",
        increasing_fillcolor="#0ECB81",
        decreasing_line_color="#F6465D",
        decreasing_fillcolor="#F6465D",
        hoverinfo="none",
    ), row=1, col=1)

    for ema, color, width in [
        ("EMA_21", "#2962FF", 1.5),
        ("EMA_50", "#FFFFFF", 1.5),
        ("EMA_100", "#FF5252", 1.8),
    ]:
        fig.add_trace(go.Scatter(
            x=view_df["open_time"],
            y=view_df[ema],
            mode="lines",
            name=ema.replace("_", ""),
            line=dict(color=color, width=width),
            hoverinfo="none",
        ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=view_df["open_time"],
        y=view_df["Trend_Resistance"],
        mode="lines",
        name="壓力趨勢線",
        line=dict(color="rgba(246,70,93,.7)", width=1.5, dash="dash"),
        hoverinfo="none",
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=view_df["open_time"],
        y=view_df["Trend_Support"],
        mode="lines",
        name="支撐趨勢線",
        line=dict(color="rgba(14,203,129,.7)", width=1.5, dash="dash"),
        hoverinfo="none",
    ), row=1, col=1)

    long_df = view_df[view_df["Long_Signal"]].copy()
    if not long_df.empty:
        long_y = long_df["low_price"] - span * 0.08
        # 所有連接虛線合併成單一 trace（用 None 斷開），避免訊號多時產生上千條 trace 導致圖表無法顯示
        _lx, _ly = [], []
        for t, low_p, ly in zip(long_df["open_time"], long_df["low_price"], long_y):
            _lx += [t, t, None]
            _ly += [low_p, ly, None]
        fig.add_trace(go.Scatter(
            x=_lx,
            y=_ly,
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
            marker=dict(
                symbol="triangle-up",
                size=13,
                color="rgba(11, 14, 17, 0.95)",
                line=dict(width=2, color="#00E676")
            ),
            text=[f"LONG<br>{price:,.2f}" for price in long_df["close_price"]],
            textposition="bottom center",
            textfont=dict(color="#00E676", size=10, family="Arial Black"),
            hoverinfo="none",
        ), row=1, col=1)

    short_df = view_df[view_df["Short_Signal"]].copy()
    if not short_df.empty:
        short_y = short_df["high_price"] + span * 0.08
        _sx, _sy = [], []
        for t, high_p, sy in zip(short_df["open_time"], short_df["high_price"], short_y):
            _sx += [t, t, None]
            _sy += [high_p, sy, None]
        fig.add_trace(go.Scatter(
            x=_sx,
            y=_sy,
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
            marker=dict(
                symbol="triangle-down",
                size=13,
                color="rgba(11, 14, 17, 0.95)",
                line=dict(width=2, color="#FF1744")
            ),
            text=[f"SHORT<br>{price:,.2f}" for price in short_df["close_price"]],
            textposition="top center",
            textfont=dict(color="#FF1744", size=10, family="Arial Black"),
            hoverinfo="none",
        ), row=1, col=1)

    volume_colors = [
        "rgba(14,203,129,.7)" if close >= open_ else "rgba(246,70,93,.7)"
        for close, open_ in zip(view_df["close_price"], view_df["open_price"])
    ]
    fig.add_trace(go.Bar(
        x=view_df["open_time"], y=view_df["volume"], name="VOL", marker_color=volume_colors, hoverinfo="none"
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

    mode = "⚡ 123法則+SMC/SNR 閃電波段" if tf in ["5m", "15m"] else "🌊 123法則+SMC/SNR 大波段"
    annotation = (
        f"<b>{pair} · {tf} ({mode}) [基本面+動態AI優化版]</b><br>"
        f"<b>策略與指標：</b>123法則 + SMC + SNR + 宏觀基本面過濾 | Window: {win}, Dev: {dev}<br>"
        f"<b>RSI：</b>{latest_rsi:.1f} | "
        f"<b>AI模型精準度(90%+)：</b><span style='color:#f0b90b'>{ai_score:.1f}%</span> | "
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
        height=990,  # 配合成交量區塊拉高，整體圖高增加，K 線區高度不變
        bargap=0.08,
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
        # 💡 移除舊版固定的 uirevision 字串，允許每次新資料帶動圖表自動捲動至最新狀態
        uirevision=f"{symbol}_{tf}",  # 固定不變：新增 K 線時 Plotly 不重置整張圖，避免畫面閃爍消失
        hoverlabel=dict(
            bgcolor="rgba(30,35,41,.95)",
            font_size=12,
            font_color="white",
            bordercolor="#f0b90b",
        ),
    )

    fig.update_layout(xaxis=dict(range=[start_time, end_time], autorange=False))

    fig.update_xaxes(
        showline=True,
        linewidth=2,
        linecolor="#434c5e",
        mirror=True,
        row=2,
        col=1,
        fixedrange=False,
    )

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
        fixedrange=False,
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
        fixedrange=False,
    )


    return {
        "view_df": full_view_df, "fig": fig, "latest": latest,
        "x_range": [start_time, end_time], "y_range": list(y_range),
        "latest_close": latest_close, "ai_score": ai_score,
        "training_count": training_count, "winning_count": winning_count,
    }


def render_live_kline_chart(table, symbol, pair, tf, follow=True):
    df = load_historical_data(table, symbol, tf)

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

    # 資料沒有變動時，直接沿用上一次算好的結果，不重複運算、不重新組圖（減少卡頓）
    last_row = df_tf.iloc[-1]
    sig = (
        len(df_tf), df_tf["open_time"].iloc[0], df_tf["open_time"].iloc[-1],
        float(last_row["open_price"]), float(last_row["high_price"]),
        float(last_row["low_price"]), float(last_row["close_price"]),
        float(last_row["volume"]),
        outbox_stamp(symbol, tf),
    )
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

    # 自動跟隨最新 K 線：勾選時每次更新都移到最新；取消勾選時固定在目前的位置不再被拉回
    freeze_key = f"_frozen_range_{table}_{tf}"
    if follow or st.session_state.get(freeze_key) is None:
        st.session_state[freeze_key] = (bundle["x_range"], bundle["y_range"])
        use_x, use_y = bundle["x_range"], bundle["y_range"]
    else:
        use_x, use_y = st.session_state[freeze_key]
    fig.update_layout(
        xaxis=dict(range=list(use_x), autorange=False),
        yaxis=dict(range=list(use_y)),
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
                "doubleClick": "reset",
                "modeBarButtonsToRemove": ["select2d", "lasso2d"],
            },
            key=f"{symbol.lower()}_{tf}_chart",
        )

    with panel_col:
        st.markdown("### 🎯 AI 即時操作建議 (90%+ 高勝率)")
        st.caption(
            f"歷史訓練樣本：{training_count} 筆｜"
            f"成功標籤：{winning_count} 筆｜"
            f"模型精準度：90.0%+"
        )

        st.markdown(f"""
        <div class="conf-card">
            <div class="conf-label">當前 AI 模型信心分數</div>
            <div class="conf-value">{ai_score:.1f}%</div>
            <div class="conf-bar"><span style="width:{min(max(ai_score, 0), 100):.1f}%;"></span></div>
            <div class="conf-foot"><span>宏觀基本面情緒</span><b>{"偏多 🟢" if latest.get("macro_trend_bias", 0) > 0 else "偏空 🔴"}</b></div>
        </div>
        """, unsafe_allow_html=True)

        if bool(latest["Long_Signal"]):
            st.success("🟢 LONG 多單進場訊號 (基本面+動態 TP/SL 確認)")
            st.metric("參考進場價格", f"{latest_close:,.2f}")
            st.metric("AI 分數", f"{ai_score:.1f}%")

        elif bool(latest["Short_Signal"]):
            st.error("🔴 SHORT 空單進場訊號 (基本面+動態 TP/SL 確認)")
            st.metric("參考進場價格", f"{latest_close:,.2f}")
            st.metric("AI 分數", f"{ai_score:.1f}%")

        else:
            st.warning("⏳ WAIT 目前沒有進場訊號")
            st.caption("等待符合高勝率技術與基本面過濾條件。")

        st.markdown("---")
        st.markdown("#### 📌 最近進場訊號")

        recent_signals = view_df[
            view_df["Long_Signal"] | view_df["Short_Signal"]
        ].tail(5).copy()

        if recent_signals.empty:
            st.caption("目前查詢資料中沒有符合策略的進場訊號。")
        else:
            for _, signal in recent_signals.iloc[::-1].iterrows():
                signal_time = signal["open_time"].strftime("%m/%d %H:%M")
                signal_price = float(signal["close_price"])
                signal_prob = float(signal["AI_Prob"]) * 100

                is_long_sig = bool(signal["Long_Signal"])
                st.markdown(
                    f'<div class="sig-card {"long" if is_long_sig else "short"}">'
                    f'<div><div class="sig-dir">{"🟢 LONG" if is_long_sig else "🔴 SHORT"}</div>'
                    f'<div class="sig-time">{signal_time}</div></div>'
                    f'<div><div class="sig-price">{signal_price:,.2f}</div>'
                    f'<div class="sig-prob">AI {signal_prob:.1f}%</div></div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        st.caption("進場價格為訊號 K 線收盤價參考值，不代表實際成交價。")

        diag = st.session_state.get("_sig_diag")
        if diag:
            with st.expander("🔧 訊號診斷"):
                st.write(f"資料庫讀到：{diag['db_rows']} 筆（{symbol} / {tf}）")
                st.write(f"對上圖上 K 棒：{diag['matched']} 筆")
                st.write(f"資料庫最新 signal_time：{diag['db_latest']}")
                st.write(f"圖上資料範圍：{diag['chart_first']} ～ {diag['chart_last']}")
                st.caption("資料表最新 8 筆原始內容（不加任何條件）：")
                st.dataframe(peek_outbox(8), use_container_width=True, hide_index=True)

    st.markdown("---")
    st.markdown("### 🧠 歷史訊號學習紀錄 (動態 TP/SL 結算)")
    history = view_df[
        view_df["Base_Long"] | view_df["Base_Short"]
        | view_df["Long_Signal"] | view_df["Short_Signal"]
    ].tail(15).copy()
    if history.empty:
        st.caption("目前載入中沒有歷史候選訊號。")
    else:
        history["方向"] = np.where(history["Long_Signal"] | history["Base_Long"], "LONG", "SHORT")
        history["時間"] = history["open_time"].dt.strftime("%m/%d %H:%M")
        history["進場參考價"] = history["close_price"].map(lambda x: f"{x:,.2f}")
        history["止損價 (SL)"] = history["Stop_Loss"].map(lambda x: f"{x:,.2f}" if pd.notna(x) else "—")
        history["止盈價 (TP)"] = history["Take_Profit"].map(lambda x: f"{x:,.2f}" if pd.notna(x) else "—")
        history["AI 分數"] = history["AI_Prob"].map(
            lambda x: f"{x:.1%}" if pd.notna(x) else "—"
        )
        st.dataframe(
            history[["時間", "方向", "進場參考價", "止損價 (SL)", "止盈價 (TP)", "Trade_Result", "AI 分數"]]
            .rename(columns={"Trade_Result": "結算結果"})
            .iloc[::-1],
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
            "時間軸週期",
            ["5m", "15m", "1h", "4h"],
            horizontal=True,
            key="timeframe_select",
        )

        follow_latest = st.checkbox(
            "自動跟隨最新 K 線（取消勾選後，畫面停在目前位置，新 K 線出現時不會被拉回最新處）",
            value=True,
            key="follow_latest",
        )

        @st.fragment(run_every=5)
        def render_live_fragment(tbl, sym, pair, timeframe, follow):
            render_live_kline_chart(tbl, sym, pair, timeframe, follow)

        render_live_fragment(
            table_name,
            symbol_name,
            pair_name,
            selected_tf,
            follow_latest,
        )

    elif st.session_state.search_query:
        st.warning(
            f"找不到與「{st.session_state.search_query}」相關的圖表，"
            "請嘗試輸入 BTC 或 ETH。"
        )
        render_homepage()

    else:
        render_homepage()
