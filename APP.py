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

COIN_MODULES = {"BTC": btc_chart, "ETH": eth_chart}

# XAU（黃金）模組：需要 xau_chart.py（介面與 eth_chart.py 相同）
try:
    import xau_chart
    COIN_MODULES["XAU"] = xau_chart
except Exception:
    xau_chart = None

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


def _bitunix_request(method, path, creds=None, params=None, body=None):
    timestamp = str(int(time.time() * 1000))
    nonce = secrets.token_hex(8)
    query_str = urllib.parse.urlencode(params) if params else ""
    body_str = json.dumps(body, separators=(",", ":")) if body is not None else ""

    raw_sign_str = timestamp + creds["key"] + nonce + body_str
    sign = hmac.new(creds["secret"].encode("utf-8"), raw_sign_str.encode("utf-8"), hashlib.sha256).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "api-key": creds["key"],
        "timestamp": timestamp,
        "nonce": nonce,
        "sign": sign,
        "User-Agent": "Mozilla/5.0"
    }
    req_url = f"{EXCHANGE_URLS['Bitunix']}{path}" + (f"?{query_str}" if query_str else "")
    req = urllib.request.Request(
        req_url,
        data=body_str.encode("utf-8") if body_str else None,
        headers=headers,
        method=method.upper(),
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if str(payload.get("code")) not in ("0", "200", "SUCCESS"):
        raise ExchangeError(f"Bitunix Error: {payload.get('msg') or payload}")
    return payload.get("data") or {}


def _bingx_request(method, path, creds=None, params=None, body=None):
    timestamp = str(int(time.time() * 1000))
    if params is None:
        params = {}
    params["timestamp"] = timestamp
    params["apiKey"] = creds["key"]

    query_str = urllib.parse.urlencode(sorted(params.items()))
    signature = hmac.new(creds["secret"].encode("utf-8"), query_str.encode("utf-8"), hashlib.sha256).hexdigest()
    query_str += f"&signature={signature}"

    headers = {"X-BX-APIKEY": creds["key"], "User-Agent": "Mozilla/5.0"}
    body_str = json.dumps(body, separators=(",", ":")) if body is not None else None

    req_url = f"{EXCHANGE_URLS['BingX']}{path}?{query_str}"
    req = urllib.request.Request(
        req_url,
        data=body_str.encode("utf-8") if body_str else None,
        headers=headers,
        method=method.upper(),
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    code = int(payload.get("code", -1))
    if code != 0:
        raise ExchangeError(f"BingX Error {code}: {payload.get('msg')}")
    return payload.get("data") or {}


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
        data = _bitunix_request("GET", "/api/v1/account/balance", creds)
        try:
            return float(data.get("usdt_available") or data.get("available") or 1000.0)
        except Exception:
            return 1000.0
    elif exchange == "BingX":
        data = _bingx_request("GET", "/openApi/swap/v1/user/balance", creds)
        try:
            balance = data.get("balance", {})
            return float(balance.get("balance") or balance.get("equity") or 1000.0)
        except Exception:
            return 1000.0
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
    return False


@st.cache_data(ttl=3600, show_spinner=False)
def get_exchange_instrument(exchange, inst_id):
    if exchange == "OKX":
        data = _okx_request("GET", "/api/v5/public/instruments", params={"instType": "SWAP", "instId": inst_id})
        if not data:
            raise ExchangeError(f"找不到合約規格：{inst_id}")
        return dict(data[0])
    elif exchange == "Bitunix":
        return {"ctVal": "1", "minSz": "0.001", "lotSz": "0.001", "tickSz": "0.1", "lever": 20}
    elif exchange == "BingX":
        return {"ctVal": "1", "minSz": "0.001", "lotSz": "0.001", "tickSz": "0.1", "lever": 20}
    return {"ctVal": "1", "minSz": "1", "lotSz": "1", "tickSz": "0.1", "lever": 10}


def okx_account_config(creds, demo):
    data = _okx_request("GET", "/api/v5/account/config", creds, demo=demo)
    return data[0] if data else {}


def _floor_step(x, step):
    return (x / step).to_integral_value(rounding=ROUND_DOWN) * step


def _fmt_px(price, tick):
    t = Decimal(str(tick))
    p = (Decimal(str(price)) / t).quantize(Decimal(1), rounding=ROUND_HALF_UP) * t
    return format(p.normalize(), "f")


def place_exchange_order(exchange, creds, s, pair, direction, price, sl, tp, clord_seed):
    demo = bool(s.get("demo"))
    is_long = direction == "LONG"

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

        max_lev = max(1, int(float(inst.get("lever") or 1)))
        lever = max_lev if s.get("use_max_lever") else max(1, min(int(s.get("manual_lever", 10)), max_lev))

        ct = Decimal(str(inst["ctVal"])) * Decimal(str(inst.get("ctMult") or "1"))
        lot = Decimal(str(inst["lotSz"]))
        min_sz = Decimal(str(inst["minSz"]))

        target_amount = min(Decimal(str(s.get("fund_amount", 100.0))), Decimal(str(avail)))
        budget = target_amount * Decimal(lever)

        contracts = _floor_step(budget / (Decimal(str(price)) * ct), lot)
        if contracts < min_sz:
            raise ExchangeError("可用資金或設定金額不足以下最小單位。")

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
            "clOrdId": "ai" + hashlib.sha1(clord_seed.encode("utf-8")).hexdigest()[:24],
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
        avail = get_exchange_balance(exchange, creds, demo)
        lever = 10 if s.get("use_max_lever") else int(s.get("manual_lever", 10))
        target_amount = min(float(s.get("fund_amount", 100.0)), float(avail))
        notional = target_amount * lever
        sz = round(notional / price, 4)

        body = {
            "symbol": pair.replace("-USDT-SWAP", "USDT"),
            "side": "OPEN_LONG" if is_long else "OPEN_SHORT",
            "type": "MARKET",
            "volume": str(sz),
            "leverage": str(lever)
        }
        _bitunix_request("POST", "/api/v1/trade/order", creds, body=body)
        return f"Bitunix 委託成功 ｜ 數量 {sz} ｜ 槓桿 {lever}x"

    elif exchange == "BingX":
        avail = get_exchange_balance(exchange, creds, demo)
        lever = 10 if s.get("use_max_lever") else int(s.get("manual_lever", 10))
        target_amount = min(float(s.get("fund_amount", 100.0)), float(avail))
        notional = target_amount * lever
        sz = round(notional / price, 4)

        body = {
            "symbol": pair.replace("-USDT-SWAP", "-USDT"),
            "side": "BUY" if is_long else "SELL",
            "positionSide": "LONG" if is_long else "SHORT",
            "type": "MARKET",
            "quantity": str(sz)
        }
        _bingx_request("POST", "/openApi/swap/v1/trade/order", creds, body=body)
        return f"BingX 委託成功 ｜ 數量 {sz} ｜ 槓桿 {lever}x"

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


# ------------------------------------------------------------
# 獨立背景自動下單執行緒（24小時不間斷背景監控）
# ------------------------------------------------------------
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

    def _worker_loop():
        _tw_log("背景下單執行緒已啟動")
        tick = 0
        while True:
            time.sleep(30)
            tick += 1
            try:
                with _acct_engine().connect() as conn:
                    users = conn.execute(text("SELECT username, settings_json FROM app_users")).fetchall()

                active = 0
                now_tw = datetime.now(TAIWAN_TZ).replace(tzinfo=None)
                sig_cache = {}

                for row in users:
                    user = row[0]
                    s = _load_settings(row[1])
                    if not s.get("auto_trade"):
                        continue
                    active += 1

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

                    user_thresh = s.get("ai_confidence_thresh", 45)
                    trade_style = s.get("trade_style", "平衡")
                    try:
                        bt_days = int(s.get("backtest_days", 30))
                    except Exception:
                        bt_days = 30

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
                                direction = "LONG" if long_sig else "SHORT"
                                sl, tp = r.get("Stop_Loss"), r.get("Take_Profit")
                                if pd.isna(sl) or pd.isna(tp):
                                    continue

                                order_id = _claim_order(user, exchange, sym, TF, bar_t, direction)
                                if not order_id:
                                    continue
                                try:
                                    seed = f"{user}|{exchange}|{sym}|{TF}|{bar_t.isoformat()}"
                                    detail = place_exchange_order(
                                        exchange, creds, s, mod.PAIR, direction,
                                        float(r["close_price"]), float(sl), float(tp), seed,
                                    )
                                    _finish_order(order_id, "success", detail)
                                except OrderSkip as e:
                                    _finish_order(order_id, "skipped", str(e))
                                except Exception as e:
                                    _finish_order(order_id, "failed", str(e))
                                break
                        except Exception:
                            pass
            except Exception:
                pass

    t = threading.Thread(target=_worker_loop, daemon=True, name="background-trade-worker")
    t.start()
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

            trailing = st.toggle("🛡️ 啟用智慧移動止損 (Trailing Stop)", value=bool(s.get("trailing_stop", True)))

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
            protect = st.toggle("止損保護", value=bool(s["protect_sl"]))

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
        # 支援下拉選單與自訂打字的欄位 (預設為空/None)
        input_val = st.selectbox(
            "🔍 請選擇或輸入商品名稱：",
            options=COIN_OPTIONS,
            index=None,
            placeholder="請選擇或輸入 " + "、".join(COIN_OPTIONS) + "...",
        )
        
        # 若使用者直接用打字或選單填寫，同步對應
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
@st.fragment(run_every=30)
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
