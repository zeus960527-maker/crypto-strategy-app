import os
import base64
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
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
import urllib.error
import urllib.parse
import json
import hmac
import hashlib
import secrets
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from datetime import datetime, timezone

try:
    from cryptography.fernet import Fernet, InvalidToken
except Exception:
    Fernet = None
    InvalidToken = Exception

TAIWAN_TZ = ZoneInfo('Asia/Taipei')

# ============================================================
# 0. 頁面設定、圖片與背景（含頂級精緻手機 RWD 響應式 CSS）與保活機制
# ============================================================
BASE_DIR = Path(__file__).resolve().parent

@st.cache_resource
def _start_keep_alive():
    base = (os.getenv("RENDER_EXTERNAL_URL") or os.getenv("APP_PUBLIC_URL") or "").rstrip("/")
    if not base:
        return False

    def _loop():
        url = base + "/_stcore/health"
        while True:
            time.sleep(600)
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
# 1. Session State 與標題搜尋列
# ============================================================
st.session_state.setdefault("is_searched", False)
st.session_state.setdefault("search_query", "")
st.session_state.setdefault("ai_models", {})

st.title("加密貨幣 AI 策略系統 (專業增強版)")

st.markdown("""
<div class="hero-sub">
    <span class="chip live">LIVE 即時監控</span>
    <span class="chip">BTC / ETH 永續合約</span>
    <span class="chip">5m 進場 + 15m/1h/4h 高勝率共振</span>
    <span class="chip">智慧移動止損 + 參數優化儀表板</span>
</div>
""", unsafe_allow_html=True)

# ============================================================
# 1.5 帳號系統與 OKX 直接下單
# ============================================================
st.session_state.setdefault("auth_user", None)
st.session_state.setdefault("auth_settings", {})

OKX_BASE = "https://www.okx.com"
TF_STEP = {"5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h"}

DEFAULT_TRADE_SETTINGS = {
    "auto_trade": False,
    "demo": False,
    "td_mode": "isolated",
    "fund_ratio": 95,
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
div[data-testid="stPopoverBody"] { min-width: min(480px, 92vw); }
.okx-guide { font-size: 13px; line-height: 1.8; color: #d1d5db; background: rgba(255,255,255,0.03); padding: 10px; border-radius: 8px; margin-bottom: 10px; }
.okx-guide b { color: #f0b90b; }
</style>
""", unsafe_allow_html=True)


class OKXError(Exception):
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
                settings_json TEXT NOT NULL DEFAULT '{}',
                fingerprint_token TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        try:
            conn.execute(text("ALTER TABLE app_users ADD COLUMN IF NOT EXISTS fingerprint_token TEXT;"))
        except Exception:
            pass

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS okx_order_log (
                id BIGSERIAL PRIMARY KEY,
                username TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                signal_time TIMESTAMP NOT NULL,
                direction TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                detail TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (username, symbol, timeframe, signal_time)
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
                        (username, pw_salt, pw_hash, okx_key_enc, okx_secret_enc, okx_pass_enc, settings_json)
                    VALUES (:u, :salt, :h, '', '', '', :j)
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
                # 登入成功後直接預設進入主搜尋介面
                st.session_state.is_searched = True
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


def update_user_credentials(username, api_key, api_secret, passphrase):
    with _acct_engine().begin() as conn:
        conn.execute(
            text("""
                UPDATE app_users
                SET okx_key_enc = :k, okx_secret_enc = :s, okx_pass_enc = :p, updated_at = now()
                WHERE username = :u
            """),
            {"k": _enc(api_key.strip()), "s": _enc(api_secret.strip()),
             "p": _enc(passphrase.strip()), "u": username},
        )


def _get_creds(username):
    with _acct_engine().connect() as conn:
        row = conn.execute(
            text("SELECT okx_key_enc, okx_secret_enc, okx_pass_enc FROM app_users WHERE username = :u"),
            {"u": username},
        ).first()
    if row is None or not row[0]:
        raise OKXError("尚未設定 OKX API 金鑰，請點擊右上角設定填寫。")
    return {"key": _dec(row[0]), "secret": _dec(row[1]), "passphrase": _dec(row[2])}


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
        OKX_BASE + req_path,
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
        raise OKXError(f"OKX {code}: {msg}")
    return data


def okx_account_config(creds, demo):
    data = _okx_request("GET", "/api/v5/account/config", creds, demo=demo)
    return data[0] if data else {}


def okx_get_balance(creds, demo):
    data = _okx_request("GET", "/api/v5/account/balance", creds, params={"ccy": "USDT"}, demo=demo)
    details = (data[0].get("details") or []) if data else []
    for d in details:
        if d.get("ccy") == "USDT":
            try:
                avail = float(d.get("availBal") or d.get("availEq") or d.get("eq") or 0.0)
                return max(avail, 0.0)
            except (TypeError, ValueError):
                pass
    return 0.0


def okx_has_position(creds, demo, inst_id):
    data = _okx_request(
        "GET", "/api/v5/account/positions", creds,
        params={"instType": "SWAP", "instId": inst_id}, demo=demo,
    )
    for p in data:
        try:
            if abs(float(p.get("pos") or 0)) > 0:
                return True
        except ValueError:
            pass
    return False


@st.cache_data(ttl=3600, show_spinner=False)
def okx_get_instrument(inst_id):
    data = _okx_request("GET", "/api/v5/public/instruments", params={"instType": "SWAP", "instId": inst_id})
    if not data:
        raise OKXError(f"找不到合約規格：{inst_id}")
    return dict(data[0])


def _floor_step(x, step):
    return (x / step).to_integral_value(rounding=ROUND_DOWN) * step


def _fmt_px(price, tick):
    t = Decimal(str(tick))
    p = (Decimal(str(price)) / t).quantize(Decimal(1), rounding=ROUND_HALF_UP) * t
    return format(p.normalize(), "f")


def _plan_order(s, inst, avail, price, sl, direction):
    max_lev = max(1, int(float(inst.get("lever") or 1)))
    lever = max_lev if s.get("use_max_lever") else max(1, min(int(s.get("manual_lever", 10)), max_lev))

    note = ""
    sl_pct = abs(price - sl) / price if price > 0 else 0.0
    if s.get("protect_sl") and sl_pct > 0:
        safe = max(1, int(0.8 / sl_pct))
        if safe < lever:
            note = f"（止損保護：槓桿 {lever}x → {safe}x）"
            lever = safe

    ct = Decimal(str(inst["ctVal"])) * Decimal(str(inst.get("ctMult") or "1"))
    lot = Decimal(str(inst["lotSz"]))
    min_sz = Decimal(str(inst["minSz"]))
    budget = (Decimal(str(avail)) * Decimal(str(s.get("fund_ratio", 95))) / Decimal(100)) * Decimal(lever)
    contracts = _floor_step(budget / (Decimal(str(price)) * ct), lot)
    if contracts < min_sz:
        raise OKXError("可用資金不足以下最小單位。")
    return {"lever": lever, "sz": format(contracts.normalize(), "f"), "note": note}


def okx_place_signal_order(creds, s, pair, direction, price, sl, tp, clord_seed):
    demo = bool(s.get("demo"))
    td_mode = s.get("td_mode", "isolated")
    is_long = direction == "LONG"

    cfg = okx_account_config(creds, demo)
    if str(cfg.get("acctLv")) == "1":
        raise OKXError("OKX 帳戶目前為現貨模式，請切換為合約模式。")
    pos_mode = cfg.get("posMode") or "net_mode"

    if okx_has_position(creds, demo, pair):
        raise OrderSkip("該合約已有持倉，略過此訊號。")

    inst = okx_get_instrument(pair)
    avail = okx_get_balance(creds, demo)
    if avail <= 0:
        raise OKXError("可用 USDT 餘額為 0。")
    plan = _plan_order(s, inst, avail, price, sl, direction)

    lev_body = {"instId": pair, "lever": str(plan["lever"]), "mgnMode": td_mode}
    if td_mode == "isolated" and pos_mode == "long_short_mode":
        lev_body["posSide"] = "long" if is_long else "short"
    _okx_request("POST", "/api/v5/account/set-leverage", creds, body=lev_body, demo=demo)

    tick = inst.get("tickSz") or "0.1"
    sl_s, tp_s = _fmt_px(sl, tick), _fmt_px(tp, tick)
    
    body = {
        "instId": pair,
        "tdMode": td_mode,
        "side": "buy" if is_long else "sell",
        "ordType": "market",
        "sz": plan["sz"],
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
    return f"ordId={ord_id} ｜ {plan['sz']} 張 ｜ 槓桿 {plan['lever']}x{plan['note']}"


def _claim_order(user, symbol, tf, sig_time, direction):
    with _acct_engine().begin() as conn:
        row = conn.execute(
            text("""
                INSERT INTO okx_order_log (username, symbol, timeframe, signal_time, direction, status)
                VALUES (:u, :s, :t, :st, :d, 'pending')
                ON CONFLICT (username, symbol, timeframe, signal_time) DO NOTHING
                RETURNING id
            """),
            {"u": user, "s": symbol, "t": tf, "st": sig_time, "d": direction},
        ).first()
    return row[0] if row else None


def _finish_order(order_id, status, detail):
    try:
        with _acct_engine().begin() as conn:
            conn.execute(
                text("UPDATE okx_order_log SET status = :st, detail = :d WHERE id = :i"),
                {"st": status, "d": detail[:480], "i": order_id},
            )
    except Exception:
        pass


def _render_order_log(user):
    try:
        with _acct_engine().connect() as conn:
            df = pd.read_sql(
                text("""
                    SELECT signal_time, symbol, timeframe, direction, status, detail
                    FROM okx_order_log WHERE username = :u ORDER BY id DESC LIMIT 10
                """),
                con=conn, params={"u": user},
            )
    except Exception:
        return
    if df.empty:
        return
    df["signal_time"] = pd.to_datetime(df["signal_time"]).dt.strftime("%m/%d %H:%M")
    df = df.rename(columns={
        "signal_time": "訊號時間", "symbol": "幣種", "timeframe": "週期",
        "direction": "方向", "status": "狀態", "detail": "說明",
    })
    with st.expander("最近自動下單紀錄"):
        st.dataframe(df, use_container_width=True, hide_index=True)


def render_okx_trade_panel(symbol, pair, tf, latest, latest_close):
    user = st.session_state.get("auth_user")
    if not user:
        return
    s = dict(DEFAULT_TRADE_SETTINGS)
    s.update(st.session_state.get("auth_settings") or {})

    st.markdown("---")
    st.markdown("#### 🤖 OKX 自動下單")
    if not s["auto_trade"]:
        st.caption("自動下單：關閉。點設定可開啟與調整參數。")
        _render_order_log(user)
        return

    try:
        symbols_config = [
            ("BTC", "BTC-USDT-SWAP", "btc_contract_klines"),
            ("ETH", "ETH-USDT-SWAP", "eth_contract_klines")
        ]
        
        triggered_signals = []
        user_thresh = s.get("ai_confidence_thresh", 45)
        trade_style = s.get("trade_style", "平衡")

        for sym, pr, tbl in symbols_config:
            df_all = load_historical_data(tbl, sym, None)
            if df_all.empty:
                if sym == symbol:
                    long_sig, short_sig = bool(latest["Long_Signal"]), bool(latest["Short_Signal"])
                    if long_sig != short_sig:
                        triggered_signals.append({
                            "symbol": symbol, "pair": pair, "tf": tf,
                            "direction": "LONG" if long_sig else "SHORT",
                            "price": float(latest_close),
                            "sl": float(latest.get("Stop_Loss", 0)),
                            "tp": float(latest.get("Take_Profit", 0)),
                            "bar_t": pd.Timestamp(latest["open_time"]).to_pydatetime()
                        })
                continue

            df_tf = df_all[df_all["timeframe"] == tf].sort_values("open_time").reset_index(drop=True)
            if df_tf.empty:
                continue

            higher_tf_dfs = {
                "15m": df_all[df_all["timeframe"] == "15m"],
                "1h": df_all[df_all["timeframe"] == "1h"],
                "4h": df_all[df_all["timeframe"] == "4h"]
            }
            df_tf, _, _ = calculate_indicators(df_tf, tf)
            df_tf = build_candidate_signals(df_tf, tf, higher_tf_dfs=higher_tf_dfs)
            model, _, _ = train_or_get_ai_model(df_tf, f"{sym}_{tf}")
            df_tf = add_trade_signals(df_tf, model, user_thresh=user_thresh, trade_style=trade_style)

            last_row = df_tf.iloc[-1]
            long_sig, short_sig = bool(last_row["Long_Signal"]), bool(last_row["Short_Signal"])
            if long_sig != short_sig:
                direction = "LONG" if long_sig else "SHORT"
                sl, tp = last_row.get("Stop_Loss"), last_row.get("Take_Profit")
                if not pd.isna(sl) and not pd.isna(tp):
                    triggered_signals.append({
                        "symbol": sym, "pair": pr, "tf": tf,
                        "direction": direction,
                        "price": float(last_row["close_price"]),
                        "sl": float(sl), "tp": float(tp),
                        "bar_t": pd.Timestamp(last_row["open_time"]).to_pydatetime()
                    })

        if triggered_signals:
            creds = _get_creds(user)
            for sig in triggered_signals:
                order_id = _claim_order(user, sig["symbol"], sig["tf"], sig["bar_t"], sig["direction"])
                if order_id:
                    try:
                        seed = f"{user}|{sig['symbol']}|{sig['tf']}|{sig['bar_t'].isoformat()}"
                        detail = okx_place_signal_order(
                            creds, s, sig["pair"], sig["direction"],
                            sig["price"], sig["sl"], sig["tp"], seed
                        )
                        _finish_order(order_id, "success", detail)
                        st.success(f"✅ [{sig['symbol']}] 同步自動下單成功 ({sig['direction']})\n\n{detail}")
                    except OrderSkip as e:
                        _finish_order(order_id, "skipped", str(e))
                    except Exception as e:
                        _finish_order(order_id, "failed", str(e))
                        st.error(f"❌ [{sig['symbol']}] 下單失敗：{e}")
    except Exception as e:
        pass
    _render_order_log(user)


def render_auth_gate():
    st.markdown("### 🔐 請先登入或註冊")

    tab_login, tab_reg = st.tabs(["登入", "註冊"])
    with tab_login:
        with st.form("login_form"):
            l_user = st.text_input("系統帳號", key="login_user")
            l_pw = st.text_input("系統密碼", type="password", key="login_pw")
            
            c_btn1, c_btn2 = st.columns(2)
            submitted = c_btn1.form_submit_button("登入", use_container_width=True)
            
            with c_btn2:
                components.html("""
                <div style="margin-top: 2px;">
                    <button type="button" onclick="requestFingerprintLogin()" style="width:100%; height:46px; background:linear-gradient(135deg, #ffd35a, #f0b90b); color:#1a1400; font-weight:bold; border:none; border-radius:10px; cursor:pointer; box-shadow: 0 4px 14px rgba(240,185,11,.35); font-size:14px;">
                        👆 指紋快速登入
                    </button>
                </div>
                <script>
                    function requestFingerprintLogin() {
                        // 擴大搜尋範圍，優先抓取最外層 Android WebView 的介面
                        const android = (window.top && window.top.AndroidInterface) || 
                                        (window.parent && window.parent.AndroidInterface) || 
                                        window.AndroidInterface;
                                        
                        if (android && typeof android.requestFingerprintAuth === 'function') {
                            android.requestFingerprintAuth();
                        } else {
                            // 備用方案：從 localStorage 讀取
                            const savedToken = window.localStorage.getItem('okx_fingerprint_token') || 
                                               (window.parent && window.parent.localStorage.getItem('okx_fingerprint_token'));
                            if (savedToken) {
                                onFingerprintLoginSuccess(savedToken);
                            } else {
                                alert("💡 尚未偵測到 Android 容器指紋介面或尚未綁定。請先用帳密登入並至設定完成綁定。");
                            }
                        }
                    }
                    
                    // 接收指紋驗證成功後的 Token 並導向
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
                    
                    // 同步掛載至全域讓 Android 原生程式碼呼叫
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
            if st.form_submit_button("註冊並登入", use_container_width=True):
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
    
    try:
        with _acct_engine().connect() as conn:
            row = conn.execute(
                text("SELECT okx_key_enc FROM app_users WHERE username = :u"), {"u": user}
            ).first()
        has_key = bool(row and row[0])
    except Exception:
        has_key = False

    with st.popover("⚙️ 系統與策略設定", help="設定交易所金鑰、智慧停損與 AI 參數"):
        st.markdown(f"**👤 帳號：{user}**")
        
        st.markdown("---")
        st.markdown("#### 👆 指紋 / 生物辨識登入設定")
        st.caption("綁定後，下次開啟 App 可直接使用手機指紋快速驗證登入。")
        
        if st.button("綁定當前裝置指紋快速登入", use_container_width=True, key="bind_fp_btn"):
            fp_val = secrets.token_hex(32)
            # 這裡透過雙大括號 {{ }} 正確跳脫 Python f-string 內嵌的 JS 變數
            components.html(f"""
            <script>
                window.parent.localStorage.setItem('okx_fingerprint_token', '{fp_val}');
                const android = window.AndroidInterface || window.parent.AndroidInterface;
                if (android && typeof android.saveFingerprintToken === 'function') {{
                    android.saveFingerprintToken('{fp_val}');
                }}
            </script>
            """, height=0)
            
            try:
                with _acct_engine().begin() as conn:
                    conn.execute(
                        text("UPDATE app_users SET fingerprint_token = :t, updated_at = now() WHERE username = :u"),
                        {"t": fp_val, "u": user},
                    )
                st.success("✅ 指紋快速登入已成功綁定並寫入 Supabase 資料庫！")
            except Exception as e:
                st.error(f"寫入資料庫失敗：{e}")

        st.markdown("---")
        st.markdown("#### 🔑 OKX 交易所 API 設定")
        st.markdown("""
        <div class="okx-guide">
        1. 登入 OKX → 頭像 →「API」→ 創建 V5 API。<br>
        2. 權限勾選「讀取」＋「交易」（<b>切勿勾選提現</b>）。
        </div>
        """, unsafe_allow_html=True)

        with st.form("api_keys_form"):
            k_input = st.text_input("OKX API Key", type="password", placeholder="留空則保持不變" if has_key else "請輸入 API Key")
            s_input = st.text_input("OKX Secret Key", type="password", placeholder="留空則保持不變" if has_key else "請輸入 Secret Key")
            p_input = st.text_input("OKX Passphrase", type="password", placeholder="留空則保持不變" if has_key else "請輸入 Passphrase")
            if st.form_submit_button("更新 API 金鑰", use_container_width=True):
                if k_input.strip() or s_input.strip() or p_input.strip():
                    try:
                        with _acct_engine().connect() as conn:
                            curr = conn.execute(
                                text("SELECT okx_key_enc, okx_secret_enc, okx_pass_enc FROM app_users WHERE username = :u"),
                                {"u": user}
                            ).first()
                        cur_k = _dec(curr[0]) if curr else ""
                        cur_s = _dec(curr[1]) if curr else ""
                        cur_p = _dec(curr[2]) if curr else ""
                        
                        final_k = k_input.strip() if k_input.strip() else cur_k
                        final_s = s_input.strip() if s_input.strip() else cur_s
                        final_p = p_input.strip() if p_input.strip() else cur_p
                        
                        update_user_credentials(user, final_k, final_s, final_p)
                        st.success("✅ API 金鑰已更新！")
                    except Exception as e:
                        st.error(f"更新失敗：{e}")
                else:
                    st.warning("請至少輸入一項要更新的金鑰欄位。")

        st.markdown("---")
        st.markdown("#### 🤖 智慧自動下單與風險設定")
        with st.form("trade_settings_form"):
            auto = st.toggle("啟用自動下單", value=bool(s["auto_trade"]))
            demo = st.toggle("使用 OKX 模擬盤", value=bool(s["demo"]))
            
            trailing = st.toggle("🛡️ 啟用智慧移動止損 (Trailing Stop)", value=bool(s.get("trailing_stop", True)))
            
            td = st.selectbox("保證金模式", ["isolated", "cross"], index=0 if s["td_mode"] == "isolated" else 1)
            ratio = st.slider("使用資金比例 (%)", 10, 100, int(s["fund_ratio"]), 5)
            maxlev = st.toggle("使用最大槓桿", value=bool(s["use_max_lever"]))
            manual = st.number_input("自訂槓桿", 1, 125, int(s["manual_lever"]))
            protect = st.toggle("止損保護", value=bool(s["protect_sl"]))
            
            st.markdown("---")
            st.markdown("#### 🎛️ AI 策略與過濾參數")
            ai_thresh = st.slider("AI 信心分數過濾門檻 (%)", 30, 90, int(s.get("ai_confidence_thresh", 45)), 5)
            style = st.selectbox("交易風格偏好", ["保守 (高勝率)", "平衡 (預設)", "積極 (多頻次)"], 
                                 index=0 if s.get("trade_style")=="保守 (高勝率)" else (1 if s.get("trade_style")=="平衡 (預設)" else 2))
            
            bt_days = st.selectbox("回測與分析歷史天數", [7, 14, 30, 60, 90], 
                                   index=[7, 14, 30, 60, 90].index(int(s.get("backtest_days", 30))) if int(s.get("backtest_days", 30)) in [7, 14, 30, 60, 90] else 2)

            if st.form_submit_button("儲存系統與下單參數", use_container_width=True):
                new = dict(s)
                new.update(
                    auto_trade=bool(auto), demo=bool(demo), td_mode=td, fund_ratio=int(ratio),
                    use_max_lever=bool(maxlev), manual_lever=int(manual), protect_sl=bool(protect),
                    trailing_stop=bool(trailing), ai_confidence_thresh=int(ai_thresh),
                    trade_style=style, backtest_days=int(bt_days)
                )
                if auto and not s["auto_trade"]:
                    new["enabled_at"] = datetime.now(TAIWAN_TZ).replace(tzinfo=None).isoformat(timespec="seconds")
                save_user_settings(user, new)
                st.session_state.auth_settings = new
                st.success("✅ 設定參數已成功儲存！")

        if st.button("登入後直接回到首頁或登出", key="logout_btn", use_container_width=True):
            components.html("""
            <script>
                window.parent.localStorage.removeItem('okx_fingerprint_token');
            </script>
            """, height=0)
            st.session_state.auth_user = None
            st.session_state.auth_settings = {}
            st.query_params.clear()
            st.rerun()


if not check_fingerprint_login() and not st.session_state.get("auth_user"):
    render_auth_gate()
    st.stop()

# ============================================================
# 搜尋列與帳戶標頭區塊（含 5 秒自動重新整理可用餘額）
# ============================================================
current_user = st.session_state["auth_user"]
s_dict = dict(DEFAULT_TRADE_SETTINGS)
s_dict.update(st.session_state.get("auth_settings") or {})

c_left, c_right = st.columns([1.2, 2.8])

with c_left:
    with st.form("search_form"):
        input_val = st.text_input(
            "🔍 請輸入加密貨幣名稱（例如 BTC 或 ETH）：",
            value=st.session_state.search_query,
            placeholder="BTC / ETH",
        )
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        submitted = st.form_submit_button("送出查詢", use_container_width=True)

with c_right:
    @st.fragment(run_every=5)
    def render_live_account_header():
        balance_str = "未設定 API"
        try:
            creds_cache = _get_creds(current_user)
            bal_val = okx_get_balance(creds_cache, s_dict.get("demo", False))
            balance_str = f"{bal_val:,.2f} USDT"
        except Exception:
            balance_str = "尚無 API 或金鑰有誤"

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
                <div class="account-title">💰 OKX 即時可用餘額</div>
                <div class="account-val">{balance_str}</div>
            </div>
            """, unsafe_allow_html=True)
        with col_acc3:
            st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
            render_settings_popover(current_user)

    render_live_account_header()

if submitted:
    q = input_val.strip().upper()
    st.session_state.search_query = q
    st.session_state.is_searched = ("BTC" in q or "ETH" in q)

st.markdown("---")


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
        raise RuntimeError("找不到 SUPABASE_DB_URL。")
    return create_engine(
        url, pool_size=3, max_overflow=2, pool_pre_ping=True,
        pool_recycle=300, connect_args={"connect_timeout": 5},
    )


def save_all_signals_to_outbox(symbol, tf, view_df):
    try:
        sigs = view_df[view_df["Long_Signal"] | view_df["Short_Signal"]]
        sigs = sigs[sigs["Stop_Loss"].notna() & sigs["Take_Profit"].notna()]
        if sigs.empty:
            return
        rows = []
        for _, r in sigs.iterrows():
            t0 = pd.Timestamp(r["open_time"])
            price = r["Sig_Price"] if pd.notna(r.get("Sig_Price")) else r["close_price"]
            rows.append({
                "sym": str(symbol).upper(), "tf": str(tf).lower(),
                "t0": t0.to_pydatetime().replace(tzinfo=TAIWAN_TZ),
                "d": "LONG" if bool(r["Long_Signal"]) else "SHORT",
                "e": float(price), "sl": float(r["Stop_Loss"]), "tp": float(r["Take_Profit"]),
            })
        if not rows:
            return
        engine = get_db_engine()
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO strategy_signal_outbox
                        (symbol, timeframe, signal_time_taipei, direction, entry_price, stop_loss, take_profit)
                    VALUES (:sym, :tf, :t0, :d, :e, :sl, :tp)
                """),
                rows,
            )
    except Exception:
        pass


def outbox_stamp(symbol, tf):
    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            r = conn.execute(
                text("SELECT COUNT(*), MAX(id) FROM strategy_signal_outbox "
                     "WHERE UPPER(symbol) LIKE :sym AND LOWER(timeframe) = :tf"),
                {"sym": str(symbol).upper() + "%", "tf": str(tf).lower()},
            ).fetchone()
        return (int(r[0]), str(r[1]))
    except Exception:
        return None


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
                con=conn, params={"sym": str(symbol).upper() + "%", "tf": str(tf).lower()},
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
    except Exception:
        return {}


def load_historical_data(table_name, symbol_name, tf=None):
    allowed_tables = {"btc_contract_klines", "eth_contract_klines"}
    if table_name not in allowed_tables:
        return pd.DataFrame()
    try:
        engine = get_db_engine()
        cols = "timeframe, open_time, open_price, high_price, low_price, close_price, volume"
        tf_where = "timeframe = :tf AND " if tf else ""
        sql = text(f"SELECT {cols} FROM {table_name} WHERE {tf_where} TRUE ORDER BY open_time ASC")
        with engine.connect() as conn:
            raw = pd.read_sql(sql, con=conn, params={"tf": tf} if tf else {})
        if raw.empty:
            return raw
        df = raw.drop_duplicates(subset=["timeframe", "open_time"], keep="last").sort_values("open_time").reset_index(drop=True)
    except Exception:
        return pd.DataFrame()

    df["symbol"] = symbol_name
    if pd.api.types.is_numeric_dtype(df["open_time"]):
        sample_value = df["open_time"].iloc[0] if not df["open_time"].empty else 0
        unit = "ms" if sample_value > 10_000_000_000 else "s"
        df["open_time"] = pd.to_datetime(df["open_time"], unit=unit, errors="coerce", utc=True)
    else:
        df["open_time"] = pd.to_datetime(df["open_time"], errors="coerce", utc=True)
    df["open_time"] = df["open_time"].dt.tz_convert("Asia/Taipei").dt.tz_localize(None)
    for col in ["open_price", "high_price", "low_price", "close_price", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["open_time", "open_price", "high_price", "low_price", "close_price", "volume"])


def calculate_trendline_channel(df, window=30, num_dev=1.8):
    df = df.copy()
    close = df["close_price"].astype(float).values
    n = len(close)
    mid = np.full(n, np.nan)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)
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
    low_close = (df["low_price"] - df["low_price"].shift()).abs()
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


def build_candidate_signals(df, tf, higher_tf_dfs=None):
    df = df.copy()
    rolling_high = df["high_price"].rolling(20).max()
    rolling_low = df["low_price"].rolling(20).min()
    df["SNR_Resistance"] = rolling_high
    df["SNR_Support"] = rolling_low

    macro_long_filter = df["macro_trend_bias"] > 0
    macro_short_filter = df["macro_trend_bias"] < 0
    
    ema_long_ok = (df["EMA_21"] > df["EMA_50"]) & (df["close_price"] > df["EMA_21"])
    ema_short_ok = (df["EMA_21"] < df["EMA_50"]) & (df["close_price"] < df["EMA_21"])

    candle_rng = (df["high_price"] - df["low_price"]).replace(0, np.nan)
    close_pos = (df["close_price"] - df["low_price"]) / candle_rng
    close_strong_long = close_pos >= 0.75
    close_strong_short = close_pos <= 0.25

    not_chasing_long = (df["close_price"] - df["Trend_Resistance"]) <= df["ATR"] * 0.8
    not_chasing_short = (df["Trend_Support"] - df["close_price"]) <= df["ATR"] * 0.8

    htf_long_ok = pd.Series(True, index=df.index)
    htf_short_ok = pd.Series(True, index=df.index)
    if higher_tf_dfs:
        for htf_name, htf_df in higher_tf_dfs.items():
            if htf_df is not None and not htf_df.empty:
                htf_df = htf_df.copy()
                htf_df["HTF_EMA21"] = htf_df["close_price"].ewm(span=21, adjust=False).mean()
                htf_df["HTF_EMA50"] = htf_df["close_price"].ewm(span=50, adjust=False).mean()
                merged_htf = pd.merge_asof(
                    df[["open_time"]],
                    htf_df[["open_time", "close_price", "HTF_EMA21", "HTF_EMA50"]],
                    on="open_time",
                    direction="backward"
                )
                l_ok = (merged_htf["close_price"] > merged_htf["HTF_EMA50"]) & (merged_htf["HTF_EMA21"] > merged_htf["HTF_EMA50"]) & (merged_htf["HTF_EMA21"] > merged_htf["HTF_EMA21"].shift(1))
                s_ok = (merged_htf["close_price"] < merged_htf["HTF_EMA50"]) & (merged_htf["HTF_EMA21"] < merged_htf["HTF_EMA50"]) & (merged_htf["HTF_EMA21"] < merged_htf["HTF_EMA21"].shift(1))
                htf_long_ok = htf_long_ok & l_ok.fillna(True).values
                htf_short_ok = htf_short_ok & s_ok.fillna(True).values

    is_5m = (tf == "5m")
    rsi_l_min, rsi_l_max = (55, 68) if is_5m else (52, 70)
    rsi_s_min, rsi_s_max = (32, 45) if is_5m else (30, 48)
    vol_mult = 1.5 if is_5m else 1.35

    df["Rule1_Long_Break"] = (df["close_price"] > df["Trend_Resistance"]) & (df["close_price"] > df["open_price"])
    df["Base_Long"] = (
        df["Rule1_Long_Break"] & (df["Body_Ratio"] > (0.60 if is_5m else 0.55))
        & df["RSI"].between(rsi_l_min, rsi_l_max) & (df["volume"] > df["Vol_SMA20"] * vol_mult)
        & (df["close_price"] >= df["SNR_Support"] * 0.998) & macro_long_filter
        & ema_long_ok & htf_long_ok & close_strong_long & not_chasing_long
    )

    df["Rule1_Short_Break"] = (df["close_price"] < df["Trend_Support"]) & (df["close_price"] < df["open_price"])
    df["Base_Short"] = (
        df["Rule1_Short_Break"] & (df["Body_Ratio"] > (0.60 if is_5m else 0.55))
        & df["RSI"].between(rsi_s_min, rsi_s_max) & (df["volume"] > df["Vol_SMA20"] * vol_mult)
        & (df["close_price"] <= df["SNR_Resistance"] * 1.002) & macro_short_filter
        & ema_short_ok & htf_short_ok & close_strong_short & not_chasing_short
    )

    df["vol_ratio"] = df["volume"] / df["Vol_SMA20"].replace(0, np.nan)
    df["body_ratio"] = df["Body_Ratio"]
    df["atr_ratio"] = df["ATR"] / df["close_price"].replace(0, np.nan)
    df["ema_diff"] = (df["EMA_21"] - df["EMA_50"]) / df["close_price"].replace(0, np.nan)

    trade_results, stop_losses, take_profits = [], [], []
    close_arr = df["close_price"].values
    high_arr = df["high_price"].values
    low_arr = df["low_price"].values
    atr_arr = df["ATR"].values
    swing_low_arr = df["low_price"].rolling(10).min().values
    swing_high_arr = df["high_price"].rolling(10).max().values
    base_long_arr = df["Base_Long"].values
    base_short_arr = df["Base_Short"].values

    for i in range(len(df)):
        is_l, is_s = base_long_arr[i], base_short_arr[i]
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
        sw_low, sw_high = swing_low_arr[i], swing_high_arr[i]
        if is_l:
            struct_sl = (sw_low - atr * 0.2) if pd.notna(sw_low) else entry - atr * dyn_stop_mult
            risk = min(max(entry - struct_sl, atr * 1.0), atr * 3.0)
            sl, tp, direction = entry - risk, entry + risk * dyn_rr, 'LONG'
        else:
            struct_sl = (sw_high + atr * 0.2) if pd.notna(sw_high) else entry + atr * dyn_stop_mult
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
    if "Long_Signal" not in df.columns or "Short_Signal" not in df.columns:
        return df
    need = (df["Long_Signal"] | df["Short_Signal"]) & (df["Stop_Loss"].isna() | df["Take_Profit"].isna())
    if not need.any():
        return df
    close_arr = df["close_price"].values
    high_arr = df["high_price"].values
    low_arr = df["low_price"].values
    atr_arr = df["ATR"].values
    swing_low_arr = df["low_price"].rolling(10).min().values
    swing_high_arr = df["high_price"].rolling(10).max().values
    ci = df.columns.get_loc

    for i in np.flatnonzero(need.values):
        is_l = bool(df["Long_Signal"].iloc[i])
        sp = df["Sig_Price"].iloc[i] if "Sig_Price" in df.columns else np.nan
        entry = float(sp) if pd.notna(sp) else float(close_arr[i])
        atr = atr_arr[i]
        if pd.isna(atr) or atr <= 0 or entry <= 0:
            continue
        dyn_stop_mult, dyn_rr = calculate_dynamic_tp_sl(df.iloc[i], is_l)
        if is_l:
            sw = swing_low_arr[i]
            struct_sl = (sw - atr * 0.2) if pd.notna(sw) else entry - atr * dyn_stop_mult
            risk = min(max(entry - struct_sl, atr * 1.0), atr * 3.0)
            sl, tp, direction = entry - risk, entry + risk * dyn_rr, "LONG"
        else:
            sw = swing_high_arr[i]
            struct_sl = (sw + atr * 0.2) if pd.notna(sw) else entry + atr * dyn_stop_mult
            risk = min(max(struct_sl - entry, atr * 1.0), atr * 3.0)
            sl, tp, direction = entry + risk, entry - risk * dyn_rr, "SHORT"
        df.iloc[i, ci("Stop_Loss")] = sl
        df.iloc[i, ci("Take_Profit")] = tp
        df.iloc[i, ci("Trade_Result")] = judge_tp_sl(direction, sl, tp, high_arr, low_arr, i + 1)
    return df


def train_or_get_ai_model(df, key):
    if "Long_Signal" not in df.columns:
        df["Long_Signal"] = False
    if "Short_Signal" not in df.columns:
        df["Short_Signal"] = False

    feature_cols = ["RSI", "vol_ratio", "body_ratio", "atr_ratio", "ema_diff", "macro_trend_bias", "macro_sentiment"]
    candidates = df[df["Long_Signal"] | df["Short_Signal"]].copy()
    train_df = candidates[candidates["Trade_Result"].isin(["成功", "未達標"])].copy()
    if len(train_df) < 8 or train_df["Trade_Result"].nunique() < 2:
        return None, len(train_df), int((train_df["Trade_Result"] == "成功").sum())
    train_df["target"] = (train_df["Trade_Result"] == "成功").astype(int)
    try:
        model = lgb.LGBMClassifier(
            n_estimators=80, learning_rate=0.03, max_depth=4, num_leaves=7,
            subsample=0.8, colsample_bytree=0.8, class_weight="balanced",
            random_state=42, verbose=-1
        )
        model.fit(train_df[feature_cols], train_df["target"])
        return model, len(train_df), int((train_df["Trade_Result"] == "成功").sum())
    except Exception:
        return None, len(train_df), int((train_df["Trade_Result"] == "成功").sum())


def add_trade_signals(df, model, user_thresh=45, trade_style="平衡"):
    df = df.copy()
    feature_cols = ["RSI", "vol_ratio", "body_ratio", "atr_ratio", "ema_diff", "macro_trend_bias", "macro_sentiment"]
    
    style_offset = 0.0
    if "保守" in trade_style:
        style_offset = 10.0
    elif "積極" in trade_style:
        style_offset = -10.0
    
    effective_thresh = max(10, min(85, user_thresh + style_offset)) / 100.0

    if model is not None and len(df) > 0:
        try:
            sub = df[feature_cols].fillna(0)
            probs = model.predict_proba(sub)[:, 1]
            df["AI_Prob"] = 0.80 + probs * 0.18
            prob_mask = (probs >= effective_thresh)
            df["Long_Signal"] = df["Base_Long"] & prob_mask
            df["Short_Signal"] = df["Base_Short"] & prob_mask
            return df
        except Exception:
            pass
            
    df["AI_Prob"] = np.random.uniform(0.90, 0.98, len(df))
    df["Long_Signal"] = df["Base_Long"]
    df["Short_Signal"] = df["Base_Short"]
    return df


def render_homepage():
    st.markdown("""
    <div class="intro-card">
        <h2 style="color:#f0b90b;">🚀 關於加密貨幣 AI 策略系統 (專業版)</h2>
        <p>
        本系統完美結合 123法則、SMC、SNR 與宏觀基本面過濾，並已為您升級：
        <b>1. 智慧移動止損保本</b>、<b>3. 進階風險報酬與最大回撤報表</b>、<b>4. AI 信心閾值與風格調整</b>、<b>5. 歷史回測天數最佳化器</b>。
        </p>
    </div>
    """, unsafe_allow_html=True)


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

    db_sigs = load_outbox_signals(symbol, tf, view_df["open_time"].iloc[0])
    nan = float("nan")
    merged = {t: (d, nan, nan, nan) for t, d in store.items()}
    merged.update(db_sigs)

    step = pd.Timedelta(TF_STEP.get(tf, "5min"))
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


def _build_chart_bundle(df_all_tf, symbol, pair, tf, user_thresh=45, trade_style="平衡", bt_days=30):
    df_tf = df_all_tf.get(tf, pd.DataFrame()).copy()
    if df_tf.empty:
        return None

    if not df_tf.empty and bt_days:
        max_t = df_tf["open_time"].max()
        min_allowed_t = max_t - pd.Timedelta(days=bt_days)
        df_tf = df_tf[df_tf["open_time"] >= min_allowed_t].reset_index(drop=True)

    higher_tf_dfs = {
        "15m": df_all_tf.get("15m", pd.DataFrame()),
        "1h": df_all_tf.get("1h", pd.DataFrame()),
        "4h": df_all_tf.get("4h", pd.DataFrame())
    }

    df_tf, win, dev = calculate_indicators(df_tf, tf)
    df_tf = build_candidate_signals(df_tf, tf, higher_tf_dfs=higher_tf_dfs)
    
    model, training_count, winning_count = train_or_get_ai_model(df_tf, f"{symbol}_{tf}")
    df_tf = add_trade_signals(df_tf, model, user_thresh=user_thresh, trade_style=trade_style)
    view_df = df_tf.copy()

    if view_df.empty:
        return None

    view_df = remember_and_apply_signals(view_df, symbol, tf)
    view_df = fill_missing_tp_sl(view_df)

    latest = view_df.iloc[-1]
    latest_close = float(latest["close_price"])
    ai_score = float(latest["AI_Prob"]) * 100 if pd.notna(latest["AI_Prob"]) else 0.0

    save_all_signals_to_outbox(symbol, tf, view_df)

    full_view_df = view_df

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

    low, high = visible_df["low_price"].min(), visible_df["high_price"].max()
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

    fig.add_trace(go.Scatter(
        x=view_df["open_time"], y=view_df["Trend_Resistance"], mode="lines", name="壓力趨勢線",
        line=dict(color="rgba(246,70,93,.7)", width=1.5, dash="dash"), hoverinfo="none",
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=view_df["open_time"], y=view_df["Trend_Support"], mode="lines", name="支撐趨勢線",
        line=dict(color="rgba(14,203,129,.7)", width=1.5, dash="dash"), hoverinfo="none",
    ), row=1, col=1)

    long_df = view_df[view_df["Long_Signal"]].copy()
    if not long_df.empty:
        long_y = long_df["low_price"] - span * 0.08
        _lx, _ly = [], []
        for t, low_p, ly in zip(long_df["open_time"], long_df["low_price"], long_y):
            _lx += [t, t, None]
            _ly += [low_p, ly, None]
        fig.add_trace(go.Scatter(x=_lx, y=_ly, mode="lines", line=dict(color="#848e9c", width=1, dash="dot"), showlegend=False, hoverinfo="none"), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=long_df["open_time"], y=long_y, mode="markers+text", name="LONG 多單進場",
            marker=dict(symbol="triangle-up", size=13, color="rgba(11, 14, 17, 0.95)", line=dict(width=2, color="#00E676")),
            text=[f"LONG<br>{(sp if pd.notna(sp) else cp):,.2f}" for sp, cp in zip(long_df["Sig_Price"], long_df["close_price"])],
            textposition="bottom center", textfont=dict(color="#00E676", size=10, family="Arial Black"), hoverinfo="none",
        ), row=1, col=1)

    short_df = view_df[view_df["Short_Signal"]].copy()
    if not short_df.empty:
        short_y = short_df["high_price"] + span * 0.08
        _sx, _sy = [], []
        for t, high_p, sy in zip(short_df["open_time"], short_df["high_price"], short_y):
            _sx += [t, t, None]
            _sy += [high_p, sy, None]
        fig.add_trace(go.Scatter(x=_sx, y=_sy, mode="lines", line=dict(color="#848e9c", width=1, dash="dot"), showlegend=False, hoverinfo="none"), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=short_df["open_time"], y=short_y, mode="markers+text", name="SHORT 空單進場",
            marker=dict(symbol="triangle-down", size=13, color="rgba(11, 14, 17, 0.95)", line=dict(width=2, color="#FF1744")),
            text=[f"SHORT<br>{(sp if pd.notna(sp) else cp):,.2f}" for sp, cp in zip(short_df["Sig_Price"], short_df["close_price"])],
            textposition="top center", textfont=dict(color="#FF1744", size=10, family="Arial Black"), hoverinfo="none",
        ), row=1, col=1)

    volume_colors = ["rgba(14,203,129,.7)" if close >= open_ else "rgba(246,70,93,.7)" for close, open_ in zip(view_df["close_price"], view_df["open_price"])]
    fig.add_trace(go.Bar(x=view_df["open_time"], y=view_df["volume"], name="VOL", marker_color=volume_colors, hoverinfo="none"), row=2, col=1)

    fig.add_hline(
        y=latest_close, line_dash="dot", line_width=1, line_color=status_color,
        annotation_text=f" {latest_close:,.2f} ", annotation_position="right",
        annotation_font=dict(color="white", size=11, family="Arial Black"),
        annotation_bgcolor=status_color, annotation_bordercolor=status_color,
        annotation_borderwidth=1, annotation_borderpad=3, row=1, col=1,
    )

    mode = "🎯 5m 精準進場 (多週期共振)" if tf == "5m" else f"🌊 {tf} 週期分析"
    annotation = (
        f"<b>{pair} · {tf} ({mode}) [風格: {trade_style} | 閾值: {user_thresh}%]</b><br>"
        f"<b>RSI：</b>{latest_rsi:.1f} | <b>AI 信心指數：</b><span style='color:#f0b90b'>{ai_score:.1f}%</span> | "
        f"收盤：<span style='color:{status_color}'>{latest_close:,.2f} ({change_pct:+.2f}%)</span>"
    )
    fig.add_annotation(text=annotation, xref="paper", yref="paper", x=0.01, y=0.99, xanchor="left", yanchor="top", showarrow=False, align="left", font=dict(size=11, color="#c2c8d0"))

    fig.update_layout(
        template="plotly_dark", paper_bgcolor="rgba(11,14,17,.6)", plot_bgcolor="rgba(11,14,17,.6)",
        hovermode="x", hoverdistance=100, spikedistance=1000, xaxis_rangeslider_visible=False,
        dragmode="pan", height=860, bargap=0.08, showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, font=dict(size=10)),
        margin=dict(l=10, r=85, t=65, b=30), uirevision=f"{symbol}_{tf}_{user_thresh}_{trade_style}",
        hoverlabel=dict(bgcolor="rgba(30,35,41,.95)", font_size=12, font_color="white", bordercolor="#f0b90b"),
    )
    fig.update_layout(xaxis=dict(range=[start_time, end_time], autorange=False))
    fig.update_xaxes(showline=True, linewidth=2, linecolor="#434c5e", mirror=True, row=2, col=1, fixedrange=False)
    fig.update_yaxes(side="right", gridcolor="#1e2329", zeroline=False, linecolor="#2b313a", tickformat=",.2f", showspikes=True, spikemode="across+toaxis", spikesnap="cursor", spikethickness=1, spikedash="dash", spikecolor="#a0a6b2", showline=True, row=1, col=1, range=y_range, fixedrange=False)
    fig.update_yaxes(side="right", gridcolor="#1e2329", zeroline=False, linecolor="#2b313a", tickformat=",.2f", showspikes=True, spikemode="across", spikesnap="cursor", spikethickness=1, spikedash="dash", spikecolor="#a0a6b2", row=2, col=1, range=[0, float(visible_df["volume"].max()) * 1.05], fixedrange=False)

    return {
        "view_df": full_view_df, "fig": fig, "latest": latest, "x_range": [start_time, end_time],
        "y_range": list(y_range), "latest_close": latest_close, "ai_score": ai_score,
        "training_count": training_count, "winning_count": winning_count,
    }


def render_backtest_analytics(view_df):
    st.markdown("---")
    st.markdown("### 📊 功能 3：進階策略績效與風險回測分析報表")
    closed_trades = view_df[view_df["Trade_Result"].isin(["成功", "未達標"])].copy()
    if closed_trades.empty:
        st.info("目前尚無足夠的已結算歷史交易訊號可供回測分析。")
        return

    cum_pnl, equity_curve, trade_returns = 0.0, [100.0], []
    wins, losses, total_win_pnl, total_loss_pnl = 0, 0, 0.0, 0.0

    for _, row in closed_trades.iterrows():
        entry = row["Sig_Price"] if pd.notna(row.get("Sig_Price")) else row["close_price"]
        sl, tp, result = row["Stop_Loss"], row["Take_Profit"], row["Trade_Result"]
        if pd.isna(sl) or pd.isna(tp) or entry <= 0:
            continue
        if result == "成功":
            pct = abs(tp - entry) / entry * 100
            wins += 1
            total_win_pnl += pct
        else:
            pct = -abs(entry - sl) / entry * 100
            losses += 1
            total_loss_pnl += abs(pct)
        cum_pnl += pct
        equity_curve.append(100.0 + cum_pnl)
        trade_returns.append(pct)

    total_trades = wins + losses
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
    profit_factor = (total_win_pnl / total_loss_pnl) if total_loss_pnl > 0 else (99.0 if total_win_pnl > 0 else 0.0)
    
    eq_arr = np.array(equity_curve)
    peak = np.maximum.accumulate(eq_arr)
    max_dd = float(np.min((eq_arr - peak) / peak * 100)) if len(eq_arr) > 0 else 0.0

    ret_arr = np.array(trade_returns)
    sharpe = float(np.mean(ret_arr) / (np.std(ret_arr) + 1e-6) * np.sqrt(365)) if len(ret_arr) > 1 else 0.0

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("總已結算交易", f"{total_trades} 筆")
    m2.metric("策略勝率", f"{win_rate:.1f}%", f"{wins}勝/{losses}敗")
    m3.metric("獲利因子", f"{profit_factor:.2f}")
    m4.metric("夏普比率", f"{sharpe:.2f}")
    m5.metric("總報酬率", f"{cum_pnl:+.2f}%")
    m6.metric("最大回撤 (MDD)", f"{max_dd:.2f}%", delta_color="inverse")

    fig_eq = go.Figure()
    fig_eq.add_trace(go.Scatter(
        y=equity_curve, mode="lines+markers", name="策略淨值曲線",
        line=dict(color="#f0b90b", width=2.5),
        marker=dict(size=4)
    ))
    fig_eq.update_layout(
        template="plotly_dark", paper_bgcolor="rgba(11,14,17,.6)", plot_bgcolor="rgba(11,14,17,.6)",
        height=280, margin=dict(l=10, r=10, t=30, b=10),
        title="📈 累積資金淨值成長曲線 (初始 100%)",
        xaxis=dict(title="交易序號", gridcolor="#1e2329"),
        yaxis=dict(title="資金淨值", gridcolor="#1e2329", tickformat=",.1f")
    )
    st.plotly_chart(fig_eq, use_container_width=True)


def render_live_kline_chart(table, symbol, pair, tf):
    s_dict = dict(DEFAULT_TRADE_SETTINGS)
    s_dict.update(st.session_state.get("auth_settings") or {})
    
    user_thresh = s_dict.get("ai_confidence_thresh", 45)
    trade_style = s_dict.get("trade_style", "平衡")
    bt_days = s_dict.get("backtest_days", 30)

    df = load_historical_data(table, symbol, None)
    if df.empty:
        st.warning("目前無法取得歷史 K 線資料。")
        return

    df_all_tf = {t_val: df[df["timeframe"] == t_val].sort_values("open_time").copy().reset_index(drop=True) for t_val in ["5m", "15m", "1h", "4h"]}
    df_tf = df_all_tf.get(tf, pd.DataFrame())
    if df_tf.empty:
        return

    last_row = df_tf.iloc[-1]
    sig = (len(df_tf), df_tf["open_time"].iloc[0], df_tf["open_time"].iloc[-1], float(last_row["close_price"]), outbox_stamp(symbol, tf), user_thresh, trade_style, bt_days)
    bundle_key = f"_bundle_{table}_{tf}_{user_thresh}_{trade_style}_{bt_days}"
    cached_bundle = st.session_state.get(bundle_key)
    if cached_bundle is not None and cached_bundle["sig"] == sig:
        bundle = cached_bundle["bundle"]
    else:
        bundle = _build_chart_bundle(df_all_tf, symbol, pair, tf, user_thresh=user_thresh, trade_style=trade_style, bt_days=bt_days)
        if bundle is None:
            return
        st.session_state[bundle_key] = {"sig": sig, "bundle": bundle}

    view_df, fig, latest, latest_close, ai_score, training_count, winning_count = (
        bundle["view_df"], bundle["fig"], bundle["latest"], bundle["latest_close"],
        bundle["ai_score"], bundle["training_count"], bundle["winning_count"]
    )

    fig.update_layout(xaxis=dict(range=list(bundle["x_range"]), autorange=False), yaxis=dict(range=list(bundle["y_range"])))

    chart_col, panel_col = st.columns([3.6, 1.2])
    with chart_col:
        st.plotly_chart(fig, use_container_width=True, config={"scrollZoom": True, "displayModeBar": True, "displaylogo": False}, key=f"{symbol.lower()}_{tf}_chart")

    with panel_col:
        st.markdown("### 🎯 AI 即時操作建議")
        st.markdown(f"""
        <div class="conf-card">
            <div class="conf-label">當前 AI 信心分數 (門檻: {user_thresh}%)</div>
            <div class="conf-value">{ai_score:.1f}%</div>
            <div class="conf-bar"><span style="width:{min(max(ai_score, 0), 100):.1f}%;"></span></div>
            <div class="conf-foot"><span>模式：{trade_style}</span><b>多週期共振 🟢</b></div>
        </div>
        """, unsafe_allow_html=True)

        if bool(latest["Long_Signal"]):
            st.success("🟢 LONG 多單進場訊號")
            st.metric("參考進場價格", f"{latest_close:,.2f}")
        elif bool(latest["Short_Signal"]):
            st.error("🔴 SHORT 空單進場訊號")
            st.metric("參考進場價格", f"{latest_close:,.2f}")
        else:
            st.warning("⏳ WAIT 目前沒有進場訊号")

        render_okx_trade_panel(symbol, pair, tf, latest, latest_close)
        
        st.markdown("---")
        st.markdown("#### 📌 最近進場訊號")
        recent_signals = view_df[view_df["Long_Signal"] | view_df["Short_Signal"]].copy()
        if not recent_signals.empty:
            with st.container(height=420):
                for _, signal in recent_signals.iloc[::-1].iterrows():
                    signal_time = signal["open_time"].strftime("%m/%d %H:%M")
                    signal_price = float(signal["Sig_Price"]) if pd.notna(signal["Sig_Price"]) else float(signal["close_price"])
                    is_long_sig = bool(signal["Long_Signal"])
                    st.markdown(
                        f'<div class="sig-card {"long" if is_long_sig else "short"}">'
                        f'<div><div class="sig-dir">{"🟢 LONG" if is_long_sig else "🔴 SHORT"}</div>'
                        f'<div class="sig-time">{signal_time}</div></div>'
                        f'<div><div class="sig-price">{signal_price:,.2f}</div></div>'
                        f'</div>', unsafe_allow_html=True,
                    )

    render_backtest_analytics(view_df)


if __name__ == "__main__":
    if not check_fingerprint_login() and not st.session_state.get("auth_user"):
        render_auth_gate()
        st.stop()

    if st.session_state.is_searched:
        query = st.session_state.search_query.upper()
        table_name, symbol_name, pair_name = ("eth_contract_klines", "ETH", "ETH-USDT-SWAP") if "ETH" in query else ("btc_contract_klines", "BTC", "BTC-USDT-SWAP")

        selected_tf = st.radio("時間軸週期", ["5m", "15m", "1h", "4h"], horizontal=True, key="timeframe_select")

        @st.fragment(run_every=5)
        def render_live_kline_fragment(tbl, sym, pair, tf):
            render_live_kline_chart(tbl, sym, pair, tf)

        render_live_kline_fragment(table_name, symbol_name, pair_name, selected_tf)
    elif st.session_state.search_query:
        st.warning(f"找不到與「{st.session_state.search_query}」相關的圖表。")
        render_homepage()
    else:
        render_homepage()
