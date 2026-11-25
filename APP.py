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
# 0. 頁面設定、圖片與背景與 Render/Streamlit 保活機制
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

div[data-testid="stForm"] {
    max-width: 480px;
    margin: 0 !important;
    background: var(--panel) !important;
    border: 1px solid var(--line) !important;
    border-radius: 16px !important;
    padding: 18px 20px 10px 20px !important;
    box-shadow: 0 10px 34px rgba(0,0,0,.35);
}
[data-testid="stFormSubmitButton"] button, .stButton button {
    min-height: 46px;
    height: auto !important;
    padding: 8px 16px !important;
    border: none !important;
    border-radius: 10px !important;
    font-weight: 700 !important;
    color: #1a1400 !important;
    background: linear-gradient(135deg, #ffd35a 0%, #f0b90b 55%, #d99a00 100%) !important;
    box-shadow: 0 6px 18px rgba(240,185,11,.28);
}
[data-testid="stMetric"] {
    background: var(--panel-2); border: 1px solid var(--line);
    border-radius: 12px; padding: 12px 14px;
}
[data-testid="stMetricValue"] { color: var(--gold) !important; font-weight: 700; }
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
# 1. Session State 與登入機制
# ============================================================
st.session_state.setdefault("is_searched", False)
st.session_state.setdefault("search_query", "")
st.session_state.setdefault("ai_models", {})
st.session_state.setdefault("auth_user", None)
st.session_state.setdefault("auth_settings", {})

st.title("加密貨幣 AI 策略系統 (專業增強版)")

st.markdown("""
<div class="hero-sub">
    <span class="chip live">LIVE 即時監控</span>
    <span class="chip">BTC / ETH 永續合約</span>
    <span class="chip">5m 進場 + 15m/1h/4h 高勝率共振</span>
</div>
""", unsafe_allow_html=True)

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
                remember_token TEXT,
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


def login_user(username, password, remember=False):
    u = username.strip().lower()
    try:
        with _acct_engine().connect() as conn:
            row = conn.execute(
                text("SELECT pw_salt, pw_hash, settings_json FROM app_users WHERE username = :u"),
                {"u": u},
            ).first()
    except Exception:
        return None, None, "資料庫連線失敗。"
    if row is None or not _verify_pw(password, row[0], row[1]):
        return None, None, "帳號或密碼錯誤。"

    token = None
    if remember:
        token = secrets.token_hex(32)
        try:
            with _acct_engine().begin() as conn:
                conn.execute(
                    text("UPDATE app_users SET remember_token = :t, updated_at = now() WHERE username = :u"),
                    {"t": token, "u": u},
                )
            st.query_params["remember_token"] = token
            components.html(f"""
            <script>
                window.parent.localStorage.setItem('okx_remember_token', '{token}');
            </script>
            """, height=0)
        except Exception:
            pass
    return u, _load_settings(row[2]), ""


def check_remembered_login():
    if st.session_state.get("auth_user"):
        return True
    
    components.html("""
    <script>
        const token = window.parent.localStorage.getItem('okx_remember_token');
        if (token && !window.parent.location.search.includes('remember_token')) {
            const url = new URL(window.parent.location.href);
            url.searchParams.set('remember_token', token);
            window.parent.location.href = url.toString();
        }
    </script>
    """, height=0)

    token = st.query_params.get("remember_token")
    if not token:
        return False
    try:
        with _acct_engine().connect() as conn:
            row = conn.execute(
                text("SELECT username, settings_json FROM app_users WHERE remember_token = :t"),
                {"t": token},
            ).first()
        if row:
            st.session_state.auth_user = row[0]
            st.session_state.auth_settings = _load_settings(row[1])
            return True
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


def render_auth_gate():
    st.markdown("### 🔐 請先登入或註冊")
    tab_login, tab_reg = st.tabs(["登入", "註冊"])
    
    with tab_login:
        with st.form("login_form"):
            l_user = st.text_input("系統帳號", key="login_user")
            l_pw = st.text_input("系統密碼", type="password", key="login_pw")
            l_remember = st.checkbox("保持登入", value=True, key="login_remember")
            
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
                        const android = window.AndroidInterface || window.parent.AndroidInterface;
                        if (android && typeof android.requestFingerprintAuth === 'function') {
                            android.requestFingerprintAuth();
                        } else {
                            alert("💡 請在 Android App 容器中使用指紋登入。");
                        }
                    }
                </script>
                """, height=55)

            if submitted:
                u, settings, err = login_user(l_user, l_pw, remember=l_remember)
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
            if st.form_submit_button("註冊並登入", use_container_width=True):
                u, err = register_user(r_user, r_pw)
                if err:
                    st.error(err)
                else:
                    u_logged, settings_logged, _ = login_user(r_user, r_pw, remember=True)
                    st.session_state.auth_user = u_logged
                    st.session_state.auth_settings = settings_logged
                    st.rerun()


def render_settings_popover(user):
    s = dict(DEFAULT_TRADE_SETTINGS)
    s.update(st.session_state.get("auth_settings") or {})

    with st.popover("⚙️ 系統與策略設定", help="設定交易所金鑰與指紋綁定"):
        st.markdown(f"**👤 帳號：{user}**")
        st.markdown("---")
        st.markdown("#### 👆 指紋 / 生物辨識登入設定")
        st.caption("點擊下方按鈕將當前登入憑證綁定至手機指紋：")
        
        components.html("""
        <button id="enable-bio" style="width:100%; padding:10px; background:linear-gradient(135deg, #ffd35a, #f0b90b); color:#1a1400; font-weight:bold; border:none; border-radius:8px; cursor:pointer;">
            綁定當前裝置指紋快速登入
        </button>
        <script>
            document.getElementById('enable-bio').onclick = function() {
                const token = window.parent.localStorage.getItem('okx_remember_token') || '';
                if (!token) {
                    alert("找不到登入憑證，請先重新登入後再綁定。");
                    return;
                }
                const android = window.AndroidInterface || window.parent.AndroidInterface;
                if (android && typeof android.saveFingerprintToken === 'function') {
                    android.saveFingerprintToken(token);
                } else {
                    alert("💡 提示：此功能需在 Android App 容器中執行才能生效。");
                }
            };
        </script>
        """, height=65)

        if st.button("登出系統", key="logout_btn", use_container_width=True):
            components.html("""
            <script>
                window.parent.localStorage.removeItem('okx_remember_token');
            </script>
            """, height=0)
            st.session_state.auth_user = None
            st.session_state.auth_settings = {}
            st.query_params.clear()
            st.rerun()


if not check_remembered_login():
    render_auth_gate()
    st.stop()


# ============================================================
# 主畫面與搜尋列
# ============================================================
current_user = st.session_state["auth_user"]
s_dict = dict(DEFAULT_TRADE_SETTINGS)
s_dict.update(st.session_state.get("auth_settings") or {})

c_left, c_right = st.columns([1.2, 2.8])

with c_left:
    with st.form("search_form"):
        input_val = st.text_input("🔍 請輸入加密貨幣名稱：", value=st.session_state.search_query, placeholder="BTC / ETH")
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
            st.markdown(f'<div class="account-card"><div class="account-title">👤 系統帳戶</div><div class="account-val" style="font-size:16px;">{current_user}</div></div>', unsafe_allow_html=True)
        with col_acc2:
            st.markdown(f'<div class="account-card"><div class="account-title">💰 OKX 可用餘額</div><div class="account-val">{balance_str}</div></div>', unsafe_allow_html=True)
        with col_acc3:
            st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
            render_settings_popover(current_user)

    render_live_account_header()

if submitted:
    q = input_val.strip().upper()
    st.session_state.search_query = q
    st.session_state.is_searched = ("BTC" in q or "ETH" in q)

st.markdown("---")

if st.session_state.is_searched:
    query = st.session_state.search_query.upper()
    st.info(f"目前正在檢視：{query} 交易對分析與 AI 訊號監控。")
else:
    st.markdown("""
    <div class="intro-card">
        <h2 style="color:#f0b90b;">🚀 關於加密貨幣 AI 策略系統 (專業版)</h2>
        <p>請於上方搜尋欄輸入 <b>BTC</b> 或 <b>ETH</b> 開始即時看盤、AI 多維度共振策略與自動下單監控。</p>
    </div>
    """, unsafe_allow_html=True)
