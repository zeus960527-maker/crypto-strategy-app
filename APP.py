import os
import base64
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from sqlalchemy import create_engine, text
from PIL import Image
from zoneinfo import ZoneInfo
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
import json
import hmac
import hashlib
import secrets
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from datetime import datetime, timedelta, timezone

# ---- 幣種 K 線模組（BTC / ETH / XAU 各自獨立）----
import btc_chart
import eth_chart
import xau_chart

COIN_MODULES = {"BTC": btc_chart, "ETH": eth_chart, "XAU": xau_chart}

# 搜尋別名 -> 幣種代碼
COIN_ALIASES = {"BTC": "BTC", "ETH": "ETH", "XAU": "XAU", "GOLD": "XAU", "黃金": "XAU", "黄金": "XAU"}
COIN_OPTIONS = list(COIN_MODULES.keys())


def resolve_coin(query):
    """把使用者輸入（BTC / ETH / XAU / GOLD / 黃金）對應成 COIN_MODULES 的 key，找不到回傳 None。"""
    q = (query or "").strip().upper()
    for alias, sym in COIN_ALIASES.items():
        if alias.upper() in q and sym in COIN_MODULES:
            return sym
    return None

try:
    from cryptography.fernet import Fernet, InvalidToken
except Exception:
    Fernet = None
    InvalidToken = Exception

TAIWAN_TZ = ZoneInfo('Asia/Taipei')

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
.stTextInput input, .stSelectbox input {{
    background-color: #1e2329 !important;
    color: #eaecef !important;
    border: 1px solid #2b313a !important;
    border-radius: 8px !important;
}}
.stTextInput input:focus, .stSelectbox input:focus {{
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

div[data-testid="stForm"] {{
    max-width: 480px;
    margin: 0 !important;
}}

@media(max-width: 768px) {{
    .block-container {{
        padding-left: 0.6rem !important;
        padding-right: 0.6rem !important;
        padding-top: 1.2rem !important;
        padding-bottom: 1.5rem !important;
    }}
    h1 {{
        font-size: 1.7rem !important;
        text-align: left;
        letter-spacing: 1px;
    }}
    div[data-testid="stForm"] {{
        max-width: 100% !important;
        background: rgba(20, 25, 35, 0.75) !important;
        border: 1px solid #2b313a !important;
        border-radius: 12px !important;
        padding: 10px !important;
        margin: 0 !important;
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
# 0.5 精美介面樣式（含幣種模組使用的 conf-card / sig-card 樣式）
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
    max-width: 480px;
    margin: 0 !important;
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
    min-height: 46px;
    height: auto !important;
    white-space: normal !important;
    word-break: break-word !important;
    padding: 8px 16px !important;
    border: none !important;
    border-radius: 10px !important;
    font-weight: 700 !important;
    letter-spacing: 1px;
    color: #1a1400 !important;
    background: linear-gradient(135deg, #ffd35a 0%, #f0b90b 55%, #d99a00 100%) !important;
    box-shadow: 0 6px 18px rgba(240,185,11,.28);
    transition: transform .15s ease, box-shadow .15s ease, filter .15s ease;
}
[data-testid="stFormSubmitButton"] button:hover, .stButton button:hover {
    transform: translateY(-1px); filter: brightness(1.06);
    box-shadow: 0 10px 24px rgba(240,185,11,.38);
}

[data-testid="stExpander"] summary span[data-testid="stIconMaterial"],
[data-testid="stPopoverButton"] span[data-testid="stIconMaterial"] {
    font-size: 0 !important;
    color: transparent !important;
    width: 0 !important;
    overflow: hidden !important;
}
[data-testid="stExpander"] summary p {
    margin: 0 !important;
    padding-left: 0 !important;
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

.account-card {
    background: rgba(22, 27, 36, 0.9);
    border: 1px solid rgba(240, 185, 11, 0.3);
    border-radius: 14px;
    padding: 16px 20px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.4);
    height: 100%;
    display: flex;
    flex-direction: column;
    justify-content: center;
}
.account-title { font-size: 12px; color: #8b94a3; letter-spacing: 1px; margin-bottom: 4px; }
.account-val { font-size: 20px; font-weight: 800; color: #f0b90b; font-family: 'JetBrains Mono', monospace; }
</style>
"""
st.markdown(UI_CSS, unsafe_allow_html=True)


# ============================================================
# 1. Session State 與標題
# ============================================================
st.session_state.setdefault("is_searched", False)
st.session_state.setdefault("search_query", "")
st.session_state.setdefault("ai_models", {})

st.title("加密貨幣 AI 策略系統")

st.markdown("""
<div class="hero-sub">
    <span class="chip live">LIVE 即時監控</span>
    <span class="chip">BTC / ETH / XAU 永續合約</span>
    <span class="chip">5m 進場 + 15m/1h/4h 高勝率共振</span>
    <span class="chip">OKX / Bitunix / BingX 多交易所自動下單</span>
</div>
""", unsafe_allow_html=True)

# ============================================================
# 1.5 帳號系統與多交易所自動下單 (OKX, Bitunix, BingX)
# ============================================================
st.session_state.setdefault("auth_user", None)
st.session_state.setdefault("auth_settings", {})

EXCHANGE_URLS = {
    "OKX": "https://www.okx.com",
    "Bitunix": "https://fapi.bitunix.com",
    "BingX": "https://open-api.bingx.com",
}

DEFAULT_TRADE_SETTINGS = {
    "auto_trade": False,
    "exchange": "OKX",
    "demo": False,
    "td_mode": "逐倉",
    "fund_amount": 100.0,
    "use_max_lever": True,
    "manual_lever": 10,
    "protect_sl": True,
    "trailing_stop": True,
    "ai_confidence_thresh": 45,
    "trade_style": "平衡",
    "backtest_days": 30,
    "enabled_at": "",
}

st.markdown("""
<style>
div[data-testid="stPopoverBody"] { min-width: min(520px, 94vw); }
.exchange-guide { font-size: 13px; line-height: 1.8; color: #d1d5db; background: rgba(255,255,255,0.03); padding: 12px; border-radius: 8px; margin-bottom: 12px; border-left: 3px solid #f0b90b; }
.exchange-guide b { color: #f0b90b; }
</style>
""", unsafe_allow_html=True)


class ExchangeError(Exception):
    pass


class OrderSkip(Exception):
    pass


def _acct_db_url():
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
def _acct_engine():
    url = _acct_db_url()
    if not url:
        raise RuntimeError("找不到 SUPABASE_DB_URL。")
    engine = create_engine(
        url, pool_size=2, max_overflow=2, pool_pre_ping=True,
        pool_recycle=300, connect_args={"connect_timeout": 5},
    )
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS app_users (
                username TEXT PRIMARY KEY,
                pw_salt TEXT NOT NULL,
                pw_hash TEXT NOT NULL,
                okx_key_enc TEXT NOT NULL DEFAULT '',
                okx_secret_enc TEXT NOT NULL DEFAULT '',
                okx_pass_enc TEXT NOT NULL DEFAULT '',
                bitunix_key_enc TEXT NOT NULL DEFAULT '',
                bitunix_secret_enc TEXT NOT NULL DEFAULT '',
                bingx_key_enc TEXT NOT NULL DEFAULT '',
                bingx_secret_enc TEXT NOT NULL DEFAULT '',
                settings_json TEXT NOT NULL DEFAULT '{}',
                fingerprint_token TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        for col_def in [
            ("bitunix_key_enc", "TEXT NOT NULL DEFAULT ''"),
            ("bitunix_secret_enc", "TEXT NOT NULL DEFAULT ''"),
            ("bingx_key_enc", "TEXT NOT NULL DEFAULT ''"),
            ("bingx_secret_enc", "TEXT NOT NULL DEFAULT ''"),
            ("fingerprint_token", "TEXT"),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE app_users ADD COLUMN IF NOT EXISTS {col_def[0]} {col_def[1]};"))
            except Exception:
                pass

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS exchange_order_log (
                id BIGSERIAL PRIMARY KEY,
                username TEXT NOT NULL,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                signal_time TIMESTAMP NOT NULL,
                direction TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                detail TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (username, exchange, symbol, timeframe, signal_time)
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS position_guard (
                id BIGSERIAL PRIMARY KEY,
                username TEXT NOT NULL,
                exchange TEXT NOT NULL,
                pair TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry DOUBLE PRECISION NOT NULL,
                init_sl DOUBLE PRECISION NOT NULL,
                cur_sl DOUBLE PRECISION NOT NULL,
                tp DOUBLE PRECISION,
                best DOUBLE PRECISION NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
    return engine


def _secret_key_raw():
    v = os.getenv("APP_SECRET_KEY", "").strip()
    if not v:
        try:
            v = str(st.secrets.get("APP_SECRET_KEY", "")).strip()
        except Exception:
            v = ""
    return v


def _fernet():
    if Fernet is None:
        raise RuntimeError("缺少 cryptography 套件。")
    raw = _secret_key_raw()
    if len(raw) < 16:
        raise RuntimeError("請先設定環境變數 APP_SECRET_KEY（至少 16 個字元）。")
    key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest())
    return Fernet(key)


def _enc(plain):
    if not plain:
        return ""
    return _fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def _dec(token):
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except Exception:
        return ""


def _hash_pw(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return salt.hex(), h.hex()


def _verify_pw(password, salt_hex, hash_hex):
    _, h = _hash_pw(password, bytes.fromhex(salt_hex))
    return hmac.compare_digest(h, hash_hex)


@st.cache_resource
def _login_fail_store():
    return {}


def _is_locked(u):
    rec = _login_fail_store().get(u)
    return bool(rec and rec[1] > time.time())


def _record_fail(u):
    store = _login_fail_store()
    if len(store) > 5000:
        store.clear()
    cnt, _ = store.get(u, (0, 0.0))
    cnt += 1
    store[u] = (0, time.time() + 300) if cnt >= 5 else (cnt, 0.0)


def _clear_fail(u):
    _login_fail_store().pop(u, None)


def _load_settings(raw_json):
    s = dict(DEFAULT_TRADE_SETTINGS)
    try:
        s.update(json.loads(raw_json or "{}"))
    except Exception:
        pass
    if "fund_ratio" in s and "fund_amount" not in s:
        s["fund_amount"] = 100.0
    if "td_mode" in s:
        if s["td_mode"] == "isolated":
            s["td_mode"] = "逐倉"
        elif s["td_mode"] == "cross":
            s["td_mode"] = "全倉"
    return s


def register_user(username, password):
    u = username.strip().lower()
    if not u or not password:
        return None, "請填寫帳號與密碼。"
    try:
        salt, h = _hash_pw(password)
        with _acct_engine().begin() as conn:
            row = conn.execute(
                text("""
                    INSERT INTO app_users
                        (username, pw_salt, pw_hash, okx_key_enc, okx_secret_enc, okx_pass_enc,
                         bitunix_key_enc, bitunix_secret_enc, bingx_key_enc, bingx_secret_enc, settings_json)
                    VALUES (:u, :salt, :h, '', '', '', '', '', '', '', :j)
                    ON CONFLICT (username) DO NOTHING
                    RETURNING username
                """),
                {"u": u, "salt": salt, "h": h, "j": json.dumps(DEFAULT_TRADE_SETTINGS)},
            ).first()
        if row is None:
            return None, "此系統帳號已被使用，請換一個。"
    except Exception as e:
        return None, f"註冊失敗：{e}"
    return u, ""


def login_user(username, password):
    u = username.strip().lower()
    if _is_locked(u):
        return None, None, "登入嘗試次數過多，請 5 分鐘後再試。"
    try:
        with _acct_engine().connect() as conn:
            row = conn.execute(
                text("SELECT pw_salt, pw_hash, settings_json FROM app_users WHERE username = :u"),
                {"u": u},
            ).first()
    except Exception:
        return None, None, "資料庫連線失敗。"
    if row is None or not _verify_pw(password, row[0], row[1]):
        _record_fail(u)
        return None, None, "帳號或密碼錯誤。"
    _clear_fail(u)

    return u, _load_settings(row[2]), ""


def check_fingerprint_login():
    if st.session_state.get("auth_user"):
        return True

    fp_token = st.query_params.get("fp_token")
    if fp_token:
        try:
            with _acct_engine().connect() as conn:
                row = conn.execute(
                    text("SELECT username, settings_json FROM app_users WHERE fingerprint_token = :t"),
                    {"t": fp_token},
                ).first()
            if row:
                st.session_state.auth_user = row[0]
                st.session_state.auth_settings = _load_settings(row[1])
                st.query_params.clear()
                return True
            else:
                st.error("❌ 指紋憑證與資料庫紀錄不符或已失效，請重新以帳密登入。")
        except Exception:
            pass
    return False


def save_user_settings(username, settings):
    with _acct_engine().begin() as conn:
        conn.execute(
            text("UPDATE app_users SET settings_json = :j, updated_at = now() WHERE username = :u"),
            {"j": json.dumps(settings), "u": username},
        )


def update_exchange_credentials(username, exchange, k="", s="", p=""):
    with _acct_engine().begin() as conn:
        curr = conn.execute(
            text("SELECT okx_key_enc, okx_secret_enc, okx_pass_enc, bitunix_key_enc, bitunix_secret_enc, bingx_key_enc, bingx_secret_enc FROM app_users WHERE username = :u"),
            {"u": username}
        ).first()

        ok_k, ok_s, ok_p = _dec(curr[0]), _dec(curr[1]), _dec(curr[2])
        bx_k, bx_s = _dec(curr[3]), _dec(curr[4])
        bg_k, bg_s = _dec(curr[5]), _dec(curr[6])

        if exchange == "OKX":
            ok_k = k.strip() if k.strip() else ok_k
            ok_s = s.strip() if s.strip() else ok_s
            ok_p = p.strip() if p.strip() else ok_p
        elif exchange == "Bitunix":
            bx_k = k.strip() if k.strip() else bx_k
            bx_s = s.strip() if s.strip() else bx_s
        elif exchange == "BingX":
            bg_k = k.strip() if k.strip() else bg_k
            bg_s = s.strip() if s.strip() else bg_s

        conn.execute(
            text("""
                UPDATE app_users
                SET okx_key_enc = :ok_k, okx_secret_enc = :ok_s, okx_pass_enc = :ok_p,
                    bitunix_key_enc = :bx_k, bitunix_secret_enc = :bx_s,
                    bingx_key_enc = :bg_k, bingx_secret_enc = :bg_s,
                    updated_at = now()
                WHERE username = :u
            """),
            {
                "ok_k": _enc(ok_k), "ok_s": _enc(ok_s), "ok_p": _enc(ok_p),
                "bx_k": _enc(bx_k), "bx_s": _enc(bx_s),
                "bg_k": _enc(bg_k), "bg_s": _enc(bg_s),
                "u": username
            }
        )


def _get_exchange_creds(username, exchange):
    with _acct_engine().connect() as conn:
        row = conn.execute(
            text("SELECT okx_key_enc, okx_secret_enc, okx_pass_enc, bitunix_key_enc, bitunix_secret_enc, bingx_key_enc, bingx_secret_enc FROM app_users WHERE username = :u"),
            {"u": username},
        ).first()
    if row is None:
        raise ExchangeError("找不到使用者帳號資料。")

    if exchange == "OKX":
        if not row[0]:
            raise ExchangeError("尚未設定 OKX API 金鑰，請至設定填寫。")
        return {"key": _dec(row[0]), "secret": _dec(row[1]), "passphrase": _dec(row[2])}
    elif exchange == "Bitunix":
        if not row[3]:
            raise ExchangeError("尚未設定 Bitunix API 金鑰，請至設定填寫。")
        return {"key": _dec(row[3]), "secret": _dec(row[4])}
    elif exchange == "BingX":
        if not row[5]:
            raise ExchangeError("尚未設定 BingX API 金鑰，請至設定填寫。")
        return {"key": _dec(row[5]), "secret": _dec(row[6])}
    else:
        raise ExchangeError(f"不支援的交易所：{exchange}")


def _okx_request(method, path, creds=None, params=None, body=None, demo=False):
    query = ("?" + urllib.parse.urlencode(params)) if params else ""
    req_path = path + query
    body_str = json.dumps(body, separators=(",", ":")) if body is not None else ""
    headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    if creds:
        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
        prehash = ts + method.upper() + req_path + body_str
        sign = base64.b64encode(
            hmac.new(creds["secret"].encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256).digest()
        ).decode("utf-8")
        headers.update({
            "OK-ACCESS-KEY": creds["key"],
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": creds["passphrase"],
        })
        if demo:
            headers["x-simulated-trading"] = "1"
    req = urllib.request.Request(
        EXCHANGE_URLS["OKX"] + req_path,
        data=body_str.encode("utf-8") if body_str else None,
        headers=headers,
        method=method.upper(),
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    code = str(payload.get("code", ""))
    data = payload.get("data") or []
    if code != "0":
        msg = payload.get("msg") or ""
        raise ExchangeError(f"OKX {code}: {msg}")
    return data


def _http_json(req):
    """送出請求並解析 JSON；HTTP 錯誤時把交易所回傳的內容一併帶出，方便除錯。"""
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8")[:300]
        except Exception:
            detail = ""
        raise ExchangeError(f"HTTP {e.code} {detail}")


def _bitunix_request(method, path, creds=None, params=None, body=None):
    params = {k: str(v) for k, v in (params or {}).items()}
    body_str = json.dumps(body, separators=(",", ":")) if body is not None else ""
    headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0", "language": "en-US"}
    if creds:
        timestamp = str(int(time.time() * 1000))
        nonce = secrets.token_hex(16)
        query_for_sign = "".join(k + params[k] for k in sorted(params))
        digest = hashlib.sha256(
            (nonce + timestamp + creds["key"] + query_for_sign + body_str).encode("utf-8")
        ).hexdigest()
        sign = hashlib.sha256((digest + creds["secret"]).encode("utf-8")).hexdigest()
        headers.update({"api-key": creds["key"], "timestamp": timestamp, "nonce": nonce, "sign": sign})
    query_str = urllib.parse.urlencode(params)
    req_url = f"{EXCHANGE_URLS['Bitunix']}{path}" + (f"?{query_str}" if query_str else "")
    req = urllib.request.Request(
        req_url,
        data=body_str.encode("utf-8") if body_str else None,
        headers=headers,
        method=method.upper(),
    )
    payload = _http_json(req)
    if str(payload.get("code")) not in ("0", "200", "SUCCESS"):
        raise ExchangeError(f"Bitunix Error {payload.get('code')}: {payload.get('msg') or payload}")
    data = payload.get("data")
    return data if data is not None else {}


def _bingx_request(method, path, creds=None, params=None, body=None):
    p = {k: str(v) for k, v in (params or {}).items()}
    if body:
        p.update({k: str(v) for k, v in body.items()})
    p["timestamp"] = str(int(time.time() * 1000))
    items = sorted(p.items())
    raw = "&".join(f"{k}={v}" for k, v in items)
    enc = "&".join(f"{k}={urllib.parse.quote(v, safe='')}" for k, v in items)
    headers = {"User-Agent": "Mozilla/5.0"}
    if creds:
        sig = hmac.new(creds["secret"].encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()
        enc += f"&signature={sig}"
        headers["X-BX-APIKEY"] = creds["key"]
    method = method.upper()
    req = urllib.request.Request(
        f"{EXCHANGE_URLS['BingX']}{path}?{enc}",
        data=b"" if method == "POST" else None,
        headers=headers,
        method=method,
    )
    payload = _http_json(req)
    if int(payload.get("code", -1)) != 0:
        raise ExchangeError(f"BingX Error {payload.get('code')}: {payload.get('msg')}")
    data = payload.get("data")
    return data if data is not None else {}


def _bitunix_symbol(pair):
    return pair.replace("-USDT-SWAP", "USDT")


def _bingx_symbol(pair):
    return pair.replace("-USDT-SWAP", "-USDT")


def get_exchange_balance(exchange, creds, demo):
    if exchange == "OKX":
        data = _okx_request("GET", "/api/v5/account/balance", creds, params={"ccy": "USDT"}, demo=demo)
        details = (data[0].get("details") or []) if data else []
        for d in details:
            if d.get("ccy") == "USDT":
                try:
                    return max(float(d.get("availBal") or d.get("availEq") or d.get("eq") or 0.0), 0.0)
                except (TypeError, ValueError):
                    pass
        return 0.0
    elif exchange == "Bitunix":
        data = _bitunix_request("GET", "/api/v1/futures/account", creds, params={"marginCoin": "USDT"})
        if isinstance(data, list):
            data = next((x for x in data if x.get("marginCoin") == "USDT"), data[0] if data else {})
        try:
            return max(float(data.get("available")), 0.0)
        except (TypeError, ValueError):
            raise ExchangeError(f"Bitunix 無法取得可用餘額：{data}")
    elif exchange == "BingX":
        data = _bingx_request("GET", "/openApi/swap/v2/user/balance", creds)
        bal = data.get("balance", {}) if isinstance(data, dict) else {}
        if isinstance(bal, list):
            bal = next((x for x in bal if x.get("asset") == "USDT"), bal[0] if bal else {})
        try:
            return max(float(bal.get("availableMargin") if bal.get("availableMargin") not in (None, "") else bal.get("balance")), 0.0)
        except (TypeError, ValueError):
            raise ExchangeError(f"BingX 無法取得可用餘額：{data}")
    return 0.0


def check_exchange_position(exchange, creds, demo, inst_id):
    if exchange == "OKX":
        data = _okx_request("GET", "/api/v5/account/positions", creds, params={"instType": "SWAP", "instId": inst_id}, demo=demo)
        for p in data:
            try:
                if abs(float(p.get("pos") or 0)) > 0:
                    return True
            except ValueError:
                pass
        return False
    elif exchange == "Bitunix":
        data = _bitunix_request("GET", "/api/v1/futures/position/get_pending_positions", creds,
                                params={"symbol": _bitunix_symbol(inst_id)})
        if isinstance(data, dict):
            data = data.get("positionList") or data.get("list") or ([data] if data else [])
        for p in data or []:
            try:
                if abs(float(p.get("qty") or 0)) > 0:
                    return True
            except (TypeError, ValueError):
                pass
        return False
    elif exchange == "BingX":
        data = _bingx_request("GET", "/openApi/swap/v2/user/positions", creds,
                              params={"symbol": _bingx_symbol(inst_id)})
        if isinstance(data, dict):
            data = data.get("positions") or []
        for p in data or []:
            try:
                if abs(float(p.get("positionAmt") or 0)) > 0:
                    return True
            except (TypeError, ValueError):
                pass
        return False
    return False


@st.cache_data(ttl=3600, show_spinner=False)
def get_exchange_instrument(exchange, inst_id):
    if exchange == "OKX":
        data = _okx_request("GET", "/api/v5/public/instruments", params={"instType": "SWAP", "instId": inst_id})
        if not data:
            raise ExchangeError(f"找不到合約規格：{inst_id}")
        return dict(data[0])
    elif exchange == "Bitunix":
        sym = _bitunix_symbol(inst_id)
        data = _bitunix_request("GET", "/api/v1/futures/market/trading_pairs", params={"symbols": sym})
        row = next((x for x in (data or []) if x.get("symbol") == sym), None)
        if not row:
            raise ExchangeError(f"Bitunix 找不到合約規格：{sym}")
        return {
            "ctVal": "1", "ctMult": "1",
            "minSz": str(row.get("minTradeVolume") or "0.001"),
            "lotSz": format(Decimal(1).scaleb(-int(row.get("basePrecision") or 3)), "f"),
            "tickSz": format(Decimal(1).scaleb(-int(row.get("quotePrecision") or 1)), "f"),
            "lever": int(float(row.get("maxLeverage") or 20)),
        }
    elif exchange == "BingX":
        sym = _bingx_symbol(inst_id)
        data = _bingx_request("GET", "/openApi/swap/v2/quote/contracts")
        row = next((x for x in (data or []) if x.get("symbol") == sym), None)
        if not row:
            raise ExchangeError(f"BingX 找不到合約規格：{sym}")
        return {
            "ctVal": "1", "ctMult": "1",
            "minSz": str(row.get("tradeMinQuantity") or "0.001"),
            "lotSz": format(Decimal(1).scaleb(-int(row.get("quantityPrecision") or 3)), "f"),
            "tickSz": format(Decimal(1).scaleb(-int(row.get("pricePrecision") or 1)), "f"),
            "lever": 10,
        }
    return {"ctVal": "1", "ctMult": "1", "minSz": "1", "lotSz": "1", "tickSz": "0.1", "lever": 10}


def okx_account_config(creds, demo):
    data = _okx_request("GET", "/api/v5/account/config", creds, demo=demo)
    return data[0] if data else {}


def _floor_step(x, step):
    return (x / step).to_integral_value(rounding=ROUND_DOWN) * step


def _fmt_px(price, tick):
    t = Decimal(str(tick))
    p = (Decimal(str(price)) / t).quantize(Decimal(1), rounding=ROUND_HALF_UP) * t
    return format(p.normalize(), "f")


def _calc_order(inst, s, avail, price, slots):
    max_lev = max(1, int(float(inst.get("lever") or 1)))
    lever = max_lev if s.get("use_max_lever") else max(1, min(int(s.get("manual_lever", 10)), max_lev))
    ct = Decimal(str(inst.get("ctVal") or "1")) * Decimal(str(inst.get("ctMult") or "1"))
    lot = Decimal(str(inst["lotSz"]))
    min_sz = Decimal(str(inst["minSz"]))

    slots = max(int(slots), 1)
    avail_d = Decimal(str(avail))
    share = avail_d * Decimal("0.98") / slots if slots > 1 else avail_d
    amount = min(Decimal(str(s.get("fund_amount", 100.0))), share)
    qty = _floor_step(amount * Decimal(lever) / (Decimal(str(price)) * ct), lot)
    if qty < min_sz:
        raise ExchangeError("可用資金或設定金額不足以下最小單位。")
    return lever, qty, amount


def place_exchange_order(exchange, creds, s, pair, direction, price, sl, tp, clord_seed, slots=1):
    demo = bool(s.get("demo"))
    is_long = direction == "LONG"
    cl_id = "ai" + hashlib.sha1(clord_seed.encode("utf-8")).hexdigest()[:24]

    if exchange == "OKX":
        cfg = okx_account_config(creds, demo)
        if str(cfg.get("acctLv")) == "1":
            raise ExchangeError("OKX 帳戶目前為現貨模式，請切換為合約模式。")
        pos_mode = cfg.get("posMode") or "net_mode"
        if check_exchange_position(exchange, creds, demo, pair):
            raise OrderSkip("該合約已有持倉，略過此訊號。")

        inst = get_exchange_instrument(exchange, pair)
        avail = get_exchange_balance(exchange, creds, demo)
        if avail <= 0:
            raise ExchangeError("可用 USDT 餘額為 0。")
        lever, contracts, _ = _calc_order(inst, s, avail, price, slots)

        td_mode_raw = "isolated" if s.get("td_mode", "逐倉") == "逐倉" else "cross"
        lev_body = {"instId": pair, "lever": str(lever), "mgnMode": td_mode_raw}
        if td_mode_raw == "isolated" and pos_mode == "long_short_mode":
            lev_body["posSide"] = "long" if is_long else "short"
        _okx_request("POST", "/api/v5/account/set-leverage", creds, body=lev_body, demo=demo)

        tick = inst.get("tickSz") or "0.1"
        sl_s, tp_s = _fmt_px(sl, tick), _fmt_px(tp, tick)
        body = {
            "instId": pair, "tdMode": td_mode_raw, "side": "buy" if is_long else "sell",
            "ordType": "market", "sz": format(contracts.normalize(), "f"),
            "clOrdId": cl_id,
            "attachAlgoOrds": [{
                "tpTriggerPx": tp_s, "tpOrdPx": "-1", "tpTriggerPxType": "last",
                "slTriggerPx": sl_s, "slOrdPx": "-1", "slTriggerPxType": "last",
            }],
        }
        if pos_mode == "long_short_mode":
            body["posSide"] = "long" if is_long else "short"
        data = _okx_request("POST", "/api/v5/trade/order", creds, body=body, demo=demo)
        ord_id = data[0].get("ordId", "") if data else ""
        return f"OKX ordId={ord_id} ｜ {contracts} 張 ｜ 槓桿 {lever}x"

    elif exchange == "Bitunix":
        symbol = _bitunix_symbol(pair)
        if check_exchange_position(exchange, creds, demo, pair):
            raise OrderSkip("該合約已有持倉，略過此訊號。")
        inst = get_exchange_instrument(exchange, pair)
        avail = get_exchange_balance(exchange, creds, demo)
        if avail <= 0:
            raise ExchangeError("可用 USDT 餘額為 0。")
        lever, qty, _ = _calc_order(inst, s, avail, price, slots)

        try:
            _bitunix_request("POST", "/api/v1/futures/account/change_margin_mode", creds, body={
                "symbol": symbol, "marginCoin": "USDT",
                "marginMode": "ISOLATION" if s.get("td_mode", "逐倉") == "逐倉" else "CROSS",
            })
        except Exception:
            pass
        _bitunix_request("POST", "/api/v1/futures/account/change_leverage", creds, body={
            "symbol": symbol, "marginCoin": "USDT", "leverage": lever,
        })

        tick = inst.get("tickSz") or "0.1"
        body = {
            "symbol": symbol,
            "qty": format(qty.normalize(), "f"),
            "side": "BUY" if is_long else "SELL",
            "tradeSide": "OPEN",
            "orderType": "MARKET",
            "clientId": cl_id,
            "tpPrice": _fmt_px(tp, tick), "tpStopType": "LAST_PRICE", "tpOrderType": "MARKET",
            "slPrice": _fmt_px(sl, tick), "slStopType": "LAST_PRICE", "slOrderType": "MARKET",
        }
        data = _bitunix_request("POST", "/api/v1/futures/trade/place_order", creds, body=body)
        oid = data.get("orderId", "") if isinstance(data, dict) else ""
        return f"Bitunix orderId={oid} ｜ 數量 {body['qty']} ｜ 槓桿 {lever}x"

    elif exchange == "BingX":
        symbol = _bingx_symbol(pair)
        if check_exchange_position(exchange, creds, demo, pair):
            raise OrderSkip("該合約已有持倉，略過此訊號。")
        inst = get_exchange_instrument(exchange, pair)
        avail = get_exchange_balance(exchange, creds, demo)
        if avail <= 0:
            raise ExchangeError("可用 USDT 餘額為 0。")
        lever, qty, _ = _calc_order(inst, s, avail, price, slots)

        try:
            _bingx_request("POST", "/openApi/swap/v2/trade/marginType", creds, body={
                "symbol": symbol,
                "marginType": "ISOLATED" if s.get("td_mode", "逐倉") == "逐倉" else "CROSSED",
            })
        except Exception:
            pass
        side_str = "LONG" if is_long else "SHORT"
        _bingx_request("POST", "/openApi/swap/v2/trade/leverage", creds, body={
            "symbol": symbol, "side": side_str, "leverage": lever,
        })

        tick = inst.get("tickSz") or "0.1"
        body = {
            "symbol": symbol,
            "side": "BUY" if is_long else "SELL",
            "positionSide": side_str,
            "type": "MARKET",
            "quantity": format(qty.normalize(), "f"),
            "clientOrderId": cl_id,
            "takeProfit": json.dumps({"type": "TAKE_PROFIT_MARKET", "stopPrice": float(_fmt_px(tp, tick)), "workingType": "MARK_PRICE"}, separators=(",", ":")),
            "stopLoss": json.dumps({"type": "STOP_MARKET", "stopPrice": float(_fmt_px(sl, tick)), "workingType": "MARK_PRICE"}, separators=(",", ":")),
        }
        data = _bingx_request("POST", "/openApi/swap/v2/trade/order", creds, body=body)
        oid = (data.get("order") or {}).get("orderId", "") if isinstance(data, dict) else ""
        return f"BingX orderId={oid} ｜ 數量 {body['quantity']} ｜ 槓桿 {lever}x"

    raise ExchangeError(f"未知的交易所：{exchange}")


def _claim_order(user, exchange, symbol, tf, sig_time, direction):
    with _acct_engine().begin() as conn:
        row = conn.execute(
            text("""
                INSERT INTO exchange_order_log (username, exchange, symbol, timeframe, signal_time, direction, status)
                VALUES (:u, :ex, :s, :t, :st, :d, 'pending')
                ON CONFLICT (username, exchange, symbol, timeframe, signal_time) DO NOTHING
                RETURNING id
            """),
            {"u": user, "ex": exchange, "s": symbol, "t": tf, "st": sig_time, "d": direction},
        ).first()
    return row[0] if row else None


def _finish_order(order_id, status, detail):
    try:
        with _acct_engine().begin() as conn:
            conn.execute(
                text("UPDATE exchange_order_log SET status = :st, detail = :d WHERE id = :i"),
                {"st": status, "d": detail[:480], "i": order_id},
            )
    except Exception:
        pass


def _render_order_log(user):
    try:
        with _acct_engine().connect() as conn:
            df = pd.read_sql(
                text("""
                    SELECT signal_time, exchange, symbol, timeframe, direction, status, detail
                    FROM exchange_order_log WHERE username = :u ORDER BY id DESC LIMIT 10
                """),
                con=conn, params={"u": user},
            )
    except Exception:
        return
    if df.empty:
        return
    df["signal_time"] = pd.to_datetime(df["signal_time"]).dt.strftime("%m/%d %H:%M")
    df = df.rename(columns={
        "signal_time": "訊號時間", "exchange": "交易所", "symbol": "幣種", "timeframe": "週期",
        "direction": "方向", "status": "狀態", "detail": "說明",
    })
    with st.expander("最近自動下單紀錄"):
        st.dataframe(df, width="stretch", hide_index=True)


# ======================================================================
#  持倉守護：止損保護 (protect_sl) / 移動止損 (trailing_stop)
#  - 下單成功後登記到 position_guard，由背景執行緒定期檢查並調整交易所上的止損單
#  - 開關每個週期都會重新讀取使用者設定，開／關立即對「已持有的倉位」生效
# ======================================================================
PROTECT_TRIGGER_R = 1.0     # 浮盈達 1R 時，止損保護把 SL 移到進場價（保本）
PROTECT_BUFFER_PCT = 0.0010  # 保本價再多留 0.10%，用來覆蓋手續費
TRAIL_ACTIVATE_R = 1.0      # 浮盈達 1R 後，移動止損開始啟動
TRAIL_DISTANCE_R = 1.0      # 移動止損與最佳價格保持 1R 的距離
MIN_SL_GAP_PCT = 0.0005     # 新 SL 與現價至少相距 0.05%，避免交易所因「觸發價已越過」而拒單
GUARD_INTERVAL_SEC = 10     # 守護檢查週期（秒）
GUARD_CLOSE_GRACE_SEC = 60  # 剛下單後這段時間內查不到持倉，不視為已平倉


def _guard_register(user, exchange, pair, direction, entry, sl, tp):
    with _acct_engine().begin() as conn:
        conn.execute(
            text("""
                INSERT INTO position_guard (username, exchange, pair, direction, entry, init_sl, cur_sl, tp, best)
                VALUES (:u, :ex, :p, :d, :e, :sl, :sl, :tp, :e)
            """),
            {"u": user, "ex": exchange, "p": pair, "d": direction,
             "e": float(entry), "sl": float(sl), "tp": float(tp)},
        )


def _guard_open_rows():
    with _acct_engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT g.id, g.username, g.exchange, g.pair, g.direction, g.entry, g.init_sl,
                   g.cur_sl, g.tp, g.best, g.created_at, u.settings_json
            FROM position_guard g JOIN app_users u ON u.username = g.username
            WHERE g.status = 'open'
        """)).mappings().all()
    return [dict(r) for r in rows]


def _guard_update(guard_id, **fields):
    if not fields:
        return
    allowed = {"entry", "cur_sl", "best", "status"}
    sets = ", ".join(f"{k} = :{k}" for k in fields if k in allowed)
    params = {k: v for k, v in fields.items() if k in allowed}
    params["i"] = guard_id
    with _acct_engine().begin() as conn:
        conn.execute(text(f"UPDATE position_guard SET {sets}, updated_at = now() WHERE id = :i"), params)


def _calc_guard_sl(is_long, entry, init_sl, best, protect, trailing):
    """依兩個開關計算「理想止損價」；沒有達到啟動條件時回傳 None。"""
    r = abs(entry - init_sl)
    if r <= 0:
        return None
    sign = 1 if is_long else -1
    gain_r = sign * (best - entry) / r
    cands = []
    if protect and gain_r >= PROTECT_TRIGGER_R:
        cands.append(entry + sign * entry * PROTECT_BUFFER_PCT)
    if trailing and gain_r >= TRAIL_ACTIVATE_R:
        cands.append(best - sign * r * TRAIL_DISTANCE_R)
    if not cands:
        return None
    return max(cands) if is_long else min(cands)


def get_open_position(exchange, creds, demo, pair):
    """回傳 {'is_long','entry','qty','id'}；查詢成功但沒有持倉時回傳 None。"""
    if exchange == "OKX":
        data = _okx_request("GET", "/api/v5/account/positions", creds,
                            params={"instType": "SWAP", "instId": pair}, demo=demo)
        for p in data:
            pos = float(p.get("pos") or 0)
            if abs(pos) > 0:
                ps = p.get("posSide")
                is_long = (ps == "long") if ps in ("long", "short") else pos > 0
                return {"is_long": is_long, "entry": float(p.get("avgPx") or 0), "qty": abs(pos), "id": None}
        return None
    elif exchange == "Bitunix":
        data = _bitunix_request("GET", "/api/v1/futures/position/get_pending_positions", creds,
                                params={"symbol": _bitunix_symbol(pair)})
        if isinstance(data, dict):
            data = data.get("positionList") or data.get("list") or ([data] if data else [])
        for p in data or []:
            qty = float(p.get("qty") or 0)
            if abs(qty) > 0:
                entry = float(p.get("avgOpenPrice") or p.get("entryPrice") or 0)
                return {"is_long": str(p.get("side", "")).upper() == "BUY", "entry": entry,
                        "qty": abs(qty), "id": p.get("positionId")}
        return None
    elif exchange == "BingX":
        data = _bingx_request("GET", "/openApi/swap/v2/user/positions", creds,
                              params={"symbol": _bingx_symbol(pair)})
        if isinstance(data, dict):
            data = data.get("positions") or []
        for p in data or []:
            amt = float(p.get("positionAmt") or 0)
            if abs(amt) > 0:
                is_long = str(p.get("positionSide", "")).upper() == "LONG" if p.get("positionSide") in ("LONG", "SHORT") else amt > 0
                return {"is_long": is_long, "entry": float(p.get("avgPrice") or 0),
                        "qty": abs(amt), "id": p.get("positionId")}
        return None
    raise ExchangeError(f"未知的交易所：{exchange}")


def get_last_price(exchange, creds, demo, pair):
    if exchange == "OKX":
        data = _okx_request("GET", "/api/v5/market/ticker", params={"instId": pair})
        return float(data[0]["last"])
    elif exchange == "Bitunix":
        data = _bitunix_request("GET", "/api/v1/futures/market/tickers", params={"symbols": _bitunix_symbol(pair)})
        row = data[0] if isinstance(data, list) and data else data
        return float(row.get("lastPrice") or row.get("last") or row.get("markPrice"))
    elif exchange == "BingX":
        data = _bingx_request("GET", "/openApi/swap/v2/quote/price", params={"symbol": _bingx_symbol(pair)})
        row = data[0] if isinstance(data, list) and data else data
        return float(row.get("price"))
    raise ExchangeError(f"未知的交易所：{exchange}")


def modify_stop_loss(exchange, creds, demo, pair, is_long, new_sl, tp, tick, pos):
    """把交易所上該持倉的止損單改成 new_sl（止盈維持不變）。"""
    sl_s = _fmt_px(new_sl, tick)

    if exchange == "OKX":
        algos = []
        for ord_type in ("oco", "conditional"):
            try:
                algos += _okx_request("GET", "/api/v5/trade/orders-algo-pending", creds,
                                      params={"ordType": ord_type, "instType": "SWAP", "instId": pair}, demo=demo)
            except ExchangeError:
                pass
        want = "long" if is_long else "short"
        done = 0
        for a in algos:
            if not a.get("slTriggerPx"):
                continue
            if a.get("posSide") in ("long", "short") and a.get("posSide") != want:
                continue
            _okx_request("POST", "/api/v5/trade/amend-algos", creds, demo=demo, body={
                "instId": pair, "algoId": a["algoId"],
                "newSlTriggerPx": sl_s, "newSlOrdPx": "-1", "newSlTriggerPxType": "last",
            })
            done += 1
        if not done:
            raise ExchangeError("OKX 找不到該持倉的止損單，無法調整。")

    elif exchange == "Bitunix":
        symbol = _bitunix_symbol(pair)
        body = {"symbol": symbol, "positionId": pos.get("id"),
                "slPrice": sl_s, "slStopType": "LAST_PRICE"}
        if tp:
            body.update({"tpPrice": _fmt_px(tp, tick), "tpStopType": "LAST_PRICE"})
        try:
            _bitunix_request("POST", "/api/v1/futures/tpsl/position/modify_order", creds, body=body)
        except ExchangeError:
            _bitunix_request("POST", "/api/v1/futures/tpsl/position/place_order", creds, body=body)

    elif exchange == "BingX":
        symbol = _bingx_symbol(pair)
        side_str = "LONG" if is_long else "SHORT"
        data = _bingx_request("GET", "/openApi/swap/v2/trade/openOrders", creds, params={"symbol": symbol})
        orders = data.get("orders", []) if isinstance(data, dict) else (data or [])
        old = [o for o in orders
               if str(o.get("type", "")).upper().startswith("STOP")
               and str(o.get("positionSide", "")).upper() == side_str]
        # 先掛新的、再撤舊的：中間不會出現沒有止損的空窗
        _bingx_request("POST", "/openApi/swap/v2/trade/order", creds, body={
            "symbol": symbol, "side": "SELL" if is_long else "BUY", "positionSide": side_str,
            "type": "STOP_MARKET", "quantity": format(Decimal(str(pos["qty"])).normalize(), "f"),
            "stopPrice": float(sl_s), "workingType": "MARK_PRICE",
        })
        for o in old:
            try:
                _bingx_request("DELETE", "/openApi/swap/v2/trade/order", creds,
                               params={"symbol": symbol, "orderId": o.get("orderId")})
            except Exception as e:
                _tw_log(f"BingX 撤銷舊止損單失敗 {o.get('orderId')}：{e}")
    else:
        raise ExchangeError(f"未知的交易所：{exchange}")


def _guard_tick(g, s, creds):
    """處理單一守護紀錄：回報價格、必要時上調止損。"""
    ex, pair = g["exchange"], g["pair"]
    demo = bool(s.get("demo")) and ex == "OKX"
    is_long = g["direction"] == "LONG"

    pos = get_open_position(ex, creds, demo, pair)
    if pos is None:
        age = (datetime.now(timezone.utc) - g["created_at"]).total_seconds()
        if age > GUARD_CLOSE_GRACE_SEC:
            _guard_update(g["id"], status="closed")
            _tw_log(f"{g['username']} {ex} {pair} 持倉已結束，停止守護")
        return
    if pos["is_long"] != is_long:
        return  # 目前持倉方向與紀錄不同，不是這張單

    entry = pos["entry"] or g["entry"]
    price = get_last_price(ex, creds, demo, pair)
    best = max(g["best"], price) if is_long else min(g["best"], price)

    target = _calc_guard_sl(
        is_long, entry, g["init_sl"], best,
        bool(s.get("protect_sl")), bool(s.get("trailing_stop")),
    )
    if target is None:
        if best != g["best"] or abs(entry - g["entry"]) > 0:
            _guard_update(g["id"], best=best, entry=entry)
        return

    inst = get_exchange_instrument(ex, pair)
    tick = float(inst.get("tickSz") or 0.1)
    r = abs(entry - g["init_sl"])

    gap = price * MIN_SL_GAP_PCT
    target = min(target, price - gap) if is_long else max(target, price + gap)

    cur = g["cur_sl"]
    step = max(tick, r * 0.1)
    improved = (target - cur >= step) if is_long else (cur - target >= step)
    if not improved:
        _guard_update(g["id"], best=best, entry=entry)
        return

    modify_stop_loss(ex, creds, demo, pair, is_long, target, g["tp"], tick, pos)
    new_sl = float(_fmt_px(target, tick))
    _guard_update(g["id"], best=best, entry=entry, cur_sl=new_sl)
    _tw_log(f"{g['username']} {ex} {pair} {g['direction']} 止損 {cur} → {new_sl}（現價 {price}）")


def _tw_log(msg):
    try:
        print(f"[trade-worker] {datetime.now(TAIWAN_TZ).strftime('%m-%d %H:%M:%S')} {msg}", flush=True)
    except Exception:
        pass


@st.cache_resource
def _start_background_trade_worker():
    SCAN_BARS = 3
    MAX_BAR_AGE_MIN = 15
    TF = "5m"

    def _collect_signals(user, s, enabled_at, now_tw, sig_cache):
        user_thresh = s.get("ai_confidence_thresh", 45)
        trade_style = s.get("trade_style", "平衡")
        try:
            bt_days = int(s.get("backtest_days", 30))
        except Exception:
            bt_days = 30

        found = []
        for sym, mod in COIN_MODULES.items():
            try:
                write_outbox = (user == btc_chart.OUTBOX_WRITER_USER)
                ck = (sym, user_thresh, trade_style, bt_days, write_outbox)
                if ck not in sig_cache:
                    sig_cache[ck] = mod.compute_signals(
                        TF, user_thresh, trade_style, days=bt_days, save_outbox=write_outbox
                    )
                df_tf = sig_cache[ck]
                if df_tf is None or df_tf.empty:
                    continue

                for _, r in df_tf.tail(SCAN_BARS).iloc[::-1].iterrows():
                    long_sig, short_sig = bool(r["Long_Signal"]), bool(r["Short_Signal"])
                    if long_sig == short_sig:
                        continue
                    bar_t = pd.Timestamp(r["open_time"]).to_pydatetime()
                    if (now_tw - bar_t).total_seconds() > MAX_BAR_AGE_MIN * 60:
                        continue
                    if enabled_at and bar_t + timedelta(minutes=5) <= enabled_at:
                        continue
                    sl, tp = r.get("Stop_Loss"), r.get("Take_Profit")
                    if pd.isna(sl) or pd.isna(tp):
                        continue
                    found.append({
                        "sym": sym, "pair": mod.PAIR, "bar_t": bar_t,
                        "direction": "LONG" if long_sig else "SHORT",
                        "price": float(r["close_price"]), "sl": float(sl), "tp": float(tp),
                    })
                    break
            except Exception as e:
                _tw_log(f"{user} {sym}: 掃描訊號失敗：{e}")
        return found

    def _execute_signals(user, exchange, creds, s, signals):
        claimed = []
        for sig in signals:
            try:
                oid = _claim_order(user, exchange, sig["sym"], TF, sig["bar_t"], sig["direction"])
            except Exception as e:
                _tw_log(f"{user} {sig['sym']}: 登記訂單失敗：{e}")
                continue
            if oid:
                claimed.append((oid, sig))

        total = len(claimed)
        for idx, (order_id, sig) in enumerate(claimed):
            slots_left = total - idx
            try:
                seed = f"{user}|{exchange}|{sig['sym']}|{TF}|{sig['bar_t'].isoformat()}"
                detail = place_exchange_order(
                    exchange, creds, s, sig["pair"], sig["direction"],
                    sig["price"], sig["sl"], sig["tp"], seed, slots=slots_left,
                )
                _finish_order(order_id, "success", detail)
                _tw_log(f"{user} {exchange} {sig['sym']} {sig['direction']} 成功：{detail}")
                try:
                    _guard_register(user, exchange, sig["pair"], sig["direction"],
                                    sig["price"], sig["sl"], sig["tp"])
                except Exception as e:
                    _tw_log(f"{user} {sig['sym']}: 登記持倉守護失敗（止損保護/移動止損不會生效）：{e}")
            except OrderSkip as e:
                _finish_order(order_id, "skipped", str(e))
                _tw_log(f"{user} {exchange} {sig['sym']} 略過：{e}")
            except Exception as e:
                _finish_order(order_id, "failed", str(e))
                _tw_log(f"{user} {exchange} {sig['sym']} 失敗：{e}")

    def _worker_loop():
        _tw_log("背景下單執行緒已啟動")
        while True:
            time.sleep(5)
            try:
                with _acct_engine().connect() as conn:
                    users = conn.execute(text("SELECT username, settings_json FROM app_users")).fetchall()

                now_tw = datetime.now(TAIWAN_TZ).replace(tzinfo=None)
                sig_cache = {}

                for row in users:
                    user = row[0]
                    try:
                        s = _load_settings(row[1])
                        if not s.get("auto_trade"):
                            continue

                        exchange = s.get("exchange", "OKX")
                        try:
                            creds = _get_exchange_creds(user, exchange)
                            if not creds.get("key") or not creds.get("secret"):
                                continue
                        except Exception:
                            continue

                        enabled_at = None
                        try:
                            if s.get("enabled_at"):
                                enabled_at = datetime.fromisoformat(str(s["enabled_at"]))
                        except Exception:
                            enabled_at = None

                        signals = _collect_signals(user, s, enabled_at, now_tw, sig_cache)
                        if signals:
                            _execute_signals(user, exchange, creds, s, signals)
                    except Exception as e:
                        _tw_log(f"{user}: 處理失敗：{e}")
            except Exception as e:
                _tw_log(f"worker 迴圈錯誤：{e}")

    def _guard_loop():
        _tw_log("持倉守護執行緒已啟動")
        while True:
            time.sleep(GUARD_INTERVAL_SEC)
            try:
                rows = _guard_open_rows()
                cred_cache = {}
                for g in rows:
                    try:
                        s = _load_settings(g["settings_json"])
                        if not (s.get("protect_sl") or s.get("trailing_stop")):
                            continue
                        ck = (g["username"], g["exchange"])
                        if ck not in cred_cache:
                            try:
                                cred_cache[ck] = _get_exchange_creds(g["username"], g["exchange"])
                            except Exception:
                                cred_cache[ck] = {}
                        creds = cred_cache[ck]
                        if not creds.get("key") or not creds.get("secret"):
                            continue
                        _guard_tick(g, s, creds)
                    except Exception as e:
                        _tw_log(f"{g['username']} {g['pair']}: 持倉守護失敗：{e}")
            except Exception as e:
                _tw_log(f"guard 迴圈錯誤：{e}")

    threading.Thread(target=_worker_loop, daemon=True, name="background-trade-worker").start()
    threading.Thread(target=_guard_loop, daemon=True, name="position-guard-worker").start()
    return True


_start_background_trade_worker()


def render_exchange_trade_panel(symbol, pair, tf, latest, latest_close):
    user = st.session_state.get("auth_user")
    if not user:
        return
    s = dict(DEFAULT_TRADE_SETTINGS)
    s.update(st.session_state.get("auth_settings") or {})

    exchange = s.get("exchange", "OKX")
    st.markdown("---")
    st.markdown(f"#### 🤖 {exchange} 自動下單")
    if not s["auto_trade"]:
        st.caption(f"{exchange} 自動下單：關閉。點擊上方設定可開啟與調整參數。")
        _render_order_log(user)
        return

    st.caption(f"🤖 背景 24 小時自動下單執行中 ({exchange})。")
    _render_order_log(user)


def render_auth_gate():
    st.markdown("### 🔐 請先登入或註冊")

    tab_login, tab_reg = st.tabs(["登入", "註冊"])
    with tab_login:
        with st.form("login_form"):
            l_user = st.text_input("系統帳號", key="login_user")
            l_pw = st.text_input("系統密碼", type="password", key="login_pw")

            c_btn1, c_btn2 = st.columns(2)
            submitted = c_btn1.form_submit_button("登入", width="stretch")

            with c_btn2:
                components.html("""
                <div style="margin-top: 2px;">
                    <button type="button" onclick="requestFingerprintLogin()" style="width:100%; height:46px; background:linear-gradient(135deg, #ffd35a, #f0b90b); color:#1a1400; font-weight:bold; border:none; border-radius:10px; cursor:pointer; box-shadow: 0 4px 14px rgba(240,185,11,.35); font-size:14px;">
                        👆 指紋快速登入
                    </button>
                </div>
                <script>
                    function requestFingerprintLogin() {
                        const android = (window.top && window.top.AndroidInterface) || 
                                        (window.parent && window.parent.AndroidInterface) || 
                                        window.AndroidInterface;
                                        
                        if (android && typeof android.requestFingerprintAuth === 'function') {
                            android.requestFingerprintAuth();
                        } else {
                            const savedToken = window.localStorage.getItem('okx_fingerprint_token') || 
                                               (window.parent && window.parent.localStorage.getItem('okx_fingerprint_token'));
                            if (savedToken) {
                                onFingerprintLoginSuccess(savedToken);
                            } else {
                                alert("💡 尚未偵測到 Android 容器指紋介面或尚未綁定。請先用帳密登入並至設定完成綁定。");
                            }
                        }
                    }
                    
                    function onFingerprintLoginSuccess(token) {
                        if (token) {
                            const targetWin = window.top || window.parent;
                            const url = new URL(targetWin.location.origin + targetWin.location.pathname);
                            url.searchParams.set('fp_token', token);
                            targetWin.location.href = url.toString();
                        } else {
                            alert("❌ 指紋驗證回傳的 Token 效期失效或為空，請重新登入綁定。");
                        }
                    }
                    
                    if (window.top) {
                        window.top.onFingerprintLoginSuccess = onFingerprintLoginSuccess;
                    }
                    window.onFingerprintLoginSuccess = onFingerprintLoginSuccess;
                </script>
                """, height=55)
            if submitted:
                u, settings, err = login_user(l_user, l_pw)
                if err:
                    st.error(err)
                else:
                    st.session_state.auth_user = u
                    st.session_state.auth_settings = settings
                    st.rerun()

    with tab_reg:
        with st.form("register_form"):
            r_user = st.text_input("系統帳號", key="reg_user")
            r_pw = st.text_input("系統密碼", type="password", key="reg_pw")
            st.caption("💡 註冊只需帳號與密碼！交易所 API 於登入後至設定填寫。")
            if st.form_submit_button("註冊並登入", width="stretch"):
                u, err = register_user(r_user, r_pw)
                if err:
                    st.error(err)
                else:
                    u_logged, settings_logged, _ = login_user(r_user, r_pw)
                    st.session_state.auth_user = u_logged
                    st.session_state.auth_settings = settings_logged
                    st.rerun()


def render_settings_popover(user):
    s = dict(DEFAULT_TRADE_SETTINGS)
    s.update(st.session_state.get("auth_settings") or {})

    curr_ex = s.get("exchange", "OKX")
    max_bal_limit = 10000.0
    try:
        creds_tmp = _get_exchange_creds(user, curr_ex)
        bal_tmp = get_exchange_balance(curr_ex, creds_tmp, s.get("demo", False))
        if bal_tmp > 0:
            max_bal_limit = float(bal_tmp)
    except Exception:
        pass

    with st.popover("⚙️ 系統與策略設定", help="選擇交易所、設定金鑰、智慧停損與 AI 參數"):
        st.markdown(f"**👤 帳號：{user}**")

        st.markdown("---")
        st.markdown("#### 👆 指紋 / 生物辨識登入設定")
        st.caption("綁定後，下次開啟 App 可直接使用手機指紋快速驗證登入。")

        if st.button("綁定當前裝置指紋快速登入", width="stretch", key="bind_fp_btn"):
            fp_val = secrets.token_hex(32)
            components.html(f"""
            <script>
                window.parent.localStorage.setItem('okx_fingerprint_token', '{fp_val}');
                const android = window.AndroidInterface || window.parent.AndroidInterface;
                if (android && typeof android.saveFingerprintToken === 'function') {{
                    android.saveFingerprintToken('{fp_val}');
                }}
            </script>
            """, height=1)

            try:
                with _acct_engine().begin() as conn:
                    conn.execute(
                        text("UPDATE app_users SET fingerprint_token = :t, updated_at = now() WHERE username = :u"),
                        {"t": fp_val, "u": user},
                    )
                st.success("✅ 指紋快速登入已成功綁定！")
            except Exception as e:
                st.error(f"寫入資料庫失敗：{e}")

        st.markdown("---")
        st.markdown("#### 🌐 選擇交易平台與 API 設定")

        selected_ex = st.selectbox(
            "選擇要串接的交易所",
            ["OKX", "Bitunix", "BingX"],
            index=["OKX", "Bitunix", "BingX"].index(s.get("exchange", "OKX"))
        )

        if selected_ex == "OKX":
            st.markdown("""
            <div class="exchange-guide">
            <b>💡 OKX API 申請步驟：</b><br>
            1. 登入 OKX 官網 → 點擊右上角頭像 → 進入「API」管理頁面。<br>
            2. 點擊「創建 V5 API」。<br>
            3. 設定 API 名稱並勾選權限：<b>讀取 (Read)</b> ＋ <b>交易 (Trade)</b>（<b>切勿勾選提現</b>）。<br>
            4. 填寫 Passphrase (密碼) 並完成安全驗證。
            </div>
            """, unsafe_allow_html=True)
        elif selected_ex == "Bitunix":
            st.markdown("""
            <div class="exchange-guide">
            <b>💡 Bitunix API 申請步驟：</b><br>
            1. 登入 Bitunix 帳戶 → 點擊個人中心 / 帳戶選單 → 進入「API 管理」。<br>
            2. 點擊「創建 API Key」。<br>
            3. 權限選擇「合約交易 (Futures Trading)」與「讀取權限」。<br>
            4. 複製您的 API Key 與 Secret Key 填入下方。
            </div>
            """, unsafe_allow_html=True)
        elif selected_ex == "BingX":
            st.markdown("""
            <div class="exchange-guide">
            <b>💡 BingX API 申請步驟：</b><br>
            1. 登入 BingX 官網 → 進入「個人中心」→ 點選「API 管理」。<br>
            2. 點擊「創建 API」。<br>
            3. 選擇標準合約/永續合約權限，並建議綁定您的信任 IP 位址提升安全性。<br>
            4. 取得 API Key 與 Secret Key 填入下方。
            </div>
            """, unsafe_allow_html=True)

        with st.form(f"api_keys_form_{selected_ex}"):
            k_input = st.text_input(f"{selected_ex} API Key", type="password", placeholder="留空則保持不變")
            s_input = st.text_input(f"{selected_ex} Secret Key", type="password", placeholder="留空則保持不變")
            p_input = ""
            if selected_ex == "OKX":
                p_input = st.text_input("OKX Passphrase", type="password", placeholder="留空則保持不變")

            if st.form_submit_button(f"儲存 {selected_ex} API 金鑰與設定", width="stretch"):
                if k_input.strip() or s_input.strip() or (selected_ex == "OKX" and p_input.strip()):
                    try:
                        update_exchange_credentials(user, selected_ex, k_input, s_input, p_input)
                        new_sets = dict(s)
                        new_sets["exchange"] = selected_ex
                        save_user_settings(user, new_sets)
                        st.session_state.auth_settings = new_sets
                        st.success(f"✅ [{selected_ex}] API 金鑰已成功更新並儲存！")
                    except Exception as e:
                        st.error(f"更新失敗：{e}")
                else:
                    st.warning("請至少輸入一項要更新的金鑰欄位。")

        st.markdown("---")
        st.markdown("#### 🤖 智慧自動下單與風險設定")
        with st.form("trade_settings_form"):
            auto = st.toggle("啟用自動下單", value=bool(s["auto_trade"]))
            demo = st.toggle("使用模擬盤 (僅 OKX 支援)", value=bool(s["demo"]))

            trailing = st.toggle(
                "🛡️ 啟用智慧移動止損 (Trailing Stop)", value=bool(s.get("trailing_stop", True)),
                help="浮盈達 1R（R＝進場價到原始止損的距離）後啟動，止損價會跟著最有利價格上移（多單）或下移（空單），"
                     "始終保持 1R 的距離，只會往有利方向移動。對已持有的倉位也會生效。",
            )

            td_current = s.get("td_mode", "逐倉")
            if td_current not in ["逐倉", "全倉"]:
                td_current = "逐倉"
            td = st.selectbox("保證金模式", ["逐倉", "全倉"], index=0 if td_current == "逐倉" else 1)

            curr_fund_amount = float(s.get("fund_amount", 100.0))
            fund_amount = st.number_input(
                "每次下單使用金額 (USDT)",
                min_value=1.0,
                max_value=max(max_bal_limit, curr_fund_amount, 1.0),
                value=min(curr_fund_amount, max_bal_limit),
                step=10.0,
                help="每次開倉使用的本金金額 (USDT)，系統將自動以您的帳戶可用餘額為上限。"
            )

            maxlev = st.toggle("使用最大槓桿", value=bool(s["use_max_lever"]))
            manual = st.number_input("自訂槓桿", 1, 125, int(s["manual_lever"]))
            protect = st.toggle(
                "止損保護", value=bool(s["protect_sl"]),
                help="浮盈達 1R 時，自動把止損移到進場價上方（空單為下方）0.1%（保本＋覆蓋手續費），只執行一次。"
                     "與移動止損同時開啟時，以較有利者為準。",
            )

            st.markdown("---")
            st.markdown("#### 🎛️ AI 策略與過濾參數")
            ai_thresh = st.slider("AI 信心分數過濾門檻 (%)", 30, 90, int(s.get("ai_confidence_thresh", 45)), 5)
            style = st.selectbox("交易風格偏好", ["保守 (高勝率)", "平衡 (預設)", "積極 (多頻次)"],
                                 index=0 if s.get("trade_style") == "保守 (高勝率)" else (1 if s.get("trade_style") == "平衡 (預設)" else 2))

            bt_days = st.selectbox("回測與分析歷史天數", [7, 14, 30, 60, 90],
                                   index=[7, 14, 30, 60, 90].index(int(s.get("backtest_days", 30))) if int(s.get("backtest_days", 30)) in [7, 14, 30, 60, 90] else 2)

            if st.form_submit_button("儲存系統與下單參數", width="stretch"):
                new = dict(s)
                new.update(
                    exchange=selected_ex, auto_trade=bool(auto), demo=bool(demo), td_mode=td, fund_amount=float(fund_amount),
                    use_max_lever=bool(maxlev), manual_lever=int(manual), protect_sl=bool(protect),
                    trailing_stop=bool(trailing), ai_confidence_thresh=int(ai_thresh),
                    trade_style=style, backtest_days=int(bt_days)
                )
                if auto and not s["auto_trade"]:
                    new["enabled_at"] = datetime.now(TAIWAN_TZ).replace(tzinfo=None).isoformat(timespec="seconds")
                save_user_settings(user, new)
                st.session_state.auth_settings = new
                st.success("✅ 設定參數已成功儲存！")

        if st.button("登入後直接回到首頁或登出", key="logout_btn", width="stretch"):
            components.html("""
            <script>
                window.parent.localStorage.removeItem('okx_fingerprint_token');
            </script>
            """, height=1)
            st.session_state.auth_user = None
            st.session_state.auth_settings = {}
            st.query_params.clear()
            st.rerun()


def render_homepage():
    st.markdown("""
    <div class="intro-card">
        <h2 style="color:#f0b90b;">🚀 關於加密貨幣 AI 策略系統 (多交易所專業版)</h2>
        <p>
        本系統完美結合 123法則、SMC、SNR 與宏觀基本面過濾，支援 <b>OKX</b>、<b>Bitunix</b> 與 <b>BingX</b> 自動下單！
        </p>
    </div>
    """, unsafe_allow_html=True)


# ============================================================
# 登入檢查
# ============================================================
if not check_fingerprint_login() and not st.session_state.get("auth_user"):
    render_auth_gate()
    st.stop()

# ============================================================
# 搜尋列與帳戶標頭區塊（含 15 秒自動重新整理可用餘額）
# ============================================================
current_user = st.session_state["auth_user"]
s_dict = dict(DEFAULT_TRADE_SETTINGS)
s_dict.update(st.session_state.get("auth_settings") or {})

c_left, c_right = st.columns([1.2, 2.8])

with c_left:
    with st.form("search_form"):
        input_val = st.selectbox(
            "🔍 請選擇或輸入商品名稱：",
            options=COIN_OPTIONS,
            index=None,
            placeholder="請選擇或輸入幣種",
        )
        
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        submitted = st.form_submit_button("送出查詢", width="stretch")

with c_right:
    @st.fragment(run_every=(30 if btc_chart.is_mobile_client() else 15))
    def render_live_account_header():
        curr_ex = s_dict.get("exchange", "OKX")
        balance_str = f"未設定 {curr_ex} API"
        try:
            creds_cache = _get_exchange_creds(current_user, curr_ex)
            bal_val = get_exchange_balance(curr_ex, creds_cache, s_dict.get("demo", False))
            balance_str = f"{bal_val:,.2f} USDT"
        except Exception:
            balance_str = f"尚無 {curr_ex} API"

        col_acc1, col_acc2, col_acc3 = st.columns(3)
        with col_acc1:
            st.markdown(f"""
            <div class="account-card">
                <div class="account-title">👤 系統帳戶</div>
                <div class="account-val" style="font-size:16px;">{current_user}</div>
            </div>
            """, unsafe_allow_html=True)
        with col_acc2:
            st.markdown(f"""
            <div class="account-card">
                <div class="account-title">💰 {curr_ex} 可用餘額</div>
                <div class="account-val">{balance_str}</div>
            </div>
            """, unsafe_allow_html=True)
        with col_acc3:
            st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
            render_settings_popover(current_user)

    render_live_account_header()

if submitted:
    q = (input_val or "").strip().upper()
    st.session_state.search_query = q
    st.session_state.is_searched = resolve_coin(q) is not None

st.markdown("---")

# ============================================================
# 內容區：需要顯示幣種資料時，呼叫該幣種的模組
# ============================================================
if st.session_state.is_searched:
    coin_module = COIN_MODULES[resolve_coin(st.session_state.search_query)]

    selected_tf = st.radio("時間軸週期", ["5m", "15m", "1h", "4h"], horizontal=True, key="timeframe_select")

    coin_module.render(selected_tf, trade_panel=render_exchange_trade_panel)

elif st.session_state.search_query:
    st.warning(f"找不到與「{st.session_state.search_query}」相關的圖表。")
    render_homepage()
else:
    render_homepage()


# ============================================================
# 未顯示的幣種也要執行：背景掃描訊號並寫入 strategy_signal_outbox
# ============================================================
@st.fragment(run_every=10)
def _scan_coin_signals(sym):
    mod = COIN_MODULES.get(sym)
    if mod is None:
        return
    s = dict(DEFAULT_TRADE_SETTINGS)
    s.update(st.session_state.get("auth_settings") or {})
    try:
        bt_days = int(s.get("backtest_days", 30))
    except Exception:
        bt_days = 30
    try:
        mod.compute_signals(
            "5m", s.get("ai_confidence_thresh", 45), s.get("trade_style", "平衡"),
            days=bt_days, save_outbox=True, apply_outbox_memory=False,
        )
    except Exception as e:
        _tw_log(f"{sym}: 背景掃描訊號失敗：{e}")


_shown_coin = None
if st.session_state.get("is_searched"):
    _shown_coin = resolve_coin(st.session_state.search_query)

if st.session_state.get("auth_user") == btc_chart.OUTBOX_WRITER_USER:
    for _sym in COIN_MODULES:
        if _sym != _shown_coin:
            _scan_coin_signals(_sym)
