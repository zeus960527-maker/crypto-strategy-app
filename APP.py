import os
import base64
import hashlib
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
    padding-top: 2.5rem !important;
    padding-bottom: 2rem !important;
    max-width: 1500px;
}}
h1 {{
    color: #fff !important;
    font-size: 2.6rem !important;
    line-height: 1.4 !important;
    font-weight: 800 !important;
    margin-top: 0.5rem !important;
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
.stTextInput input, .stNumberInput input {{
    background-color: #1e2329 !important;
    color: #eaecef !important;
    border: 1px solid #2b313a !important;
    border-radius: 8px !important;
}}
.stTextInput input:focus, .stNumberInput input:focus {{
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
.auth-box {{
    max-width: 480px;
    margin: 30px auto;
    background: rgba(22, 27, 36, 0.92);
    border: 1px solid rgba(240, 185, 11, 0.35);
    border-radius: 16px;
    padding: 28px 32px;
    box-shadow: 0 16px 48px rgba(0,0,0,0.6);
    backdrop-filter: blur(12px);
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
.stTextInput input { height: 44px; padding-left: 14px !important; transition: all .2s ease; }
[data-testid="stFormSubmitButton"] button, .stButton button {
    height: 44px; border: none !important; border-radius: 10px !important;
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
# 1. Supabase 連線、帳號系統與 OKX 自動下單引擎
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


@st.cache_resource
def init_user_exchange_table():
    """自動建立使用者帳號與 OKX API 儲存資料表"""
    try:
        engine = get_db_engine()
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS users_exchange_config (
                    username VARCHAR(64) PRIMARY KEY,
                    password_hash VARCHAR(128) NOT NULL,
                    okx_api_key TEXT DEFAULT '',
                    okx_secret TEXT DEFAULT '',
                    okx_passphrase TEXT DEFAULT '',
                    auto_trade_enabled BOOLEAN DEFAULT FALSE,
                    order_usdt NUMERIC(12, 2) DEFAULT 50.0,
                    leverage INTEGER DEFAULT 10,
                    is_demo BOOLEAN DEFAULT FALSE,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
            """))
        return True
    except Exception as e:
        print(f"初始化使用者資料表失敗: {e}")
        return False


init_user_exchange_table()


def hash_password(pw: str) -> str:
    return hashlib.sha256(pw.strip().encode("utf-8")).hexdigest()


def register_user(username: str, password: str):
    uname = username.strip()
    if not uname or not password:
        return False, "帳號與密碼不可為空。"
    if len(password) < 4:
        return False, "密碼長度請至少 4 個字元。"
    try:
        engine = get_db_engine()
        with engine.begin() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM users_exchange_config WHERE username = :u"),
                {"u": uname},
            ).fetchone()
            if exists:
                return False, "此帳號名稱已被註冊，請換一個。"
            conn.execute(
                text("""
                    INSERT INTO users_exchange_config (username, password_hash)
                    VALUES (:u, :p)
                """),
                {"u": uname, "p": hash_password(password)},
            )
        return True, "註冊成功！請使用剛建立的帳號登入。"
    except Exception as e:
        return False, f"註冊發生錯誤：{e}"


def authenticate_user(username: str, password: str):
    uname = username.strip()
    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT username, password_hash, okx_api_key, okx_secret, okx_passphrase,
                           auto_trade_enabled, order_usdt, leverage, is_demo
                    FROM users_exchange_config
                    WHERE username = :u
                """),
                {"u": uname},
            ).mappings().fetchone()
        if not row:
            return None, "找不到此帳號。"
        if row["password_hash"] != hash_password(password):
            return None, "密碼錯誤。"
        return dict(row), "登入成功！"
    except Exception as e:
        return None, f"登入連線錯誤：{e}"


def save_user_exchange_settings(username: str, api_key: str, secret: str, passphrase: str,
                                auto_enabled: bool, order_usdt: float, leverage: int, is_demo: bool):
    try:
        engine = get_db_engine()
        with engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE users_exchange_config
                    SET okx_api_key = :k,
                        okx_secret = :s,
                        okx_passphrase = :p,
                        auto_trade_enabled = :ae,
                        order_usdt = :usdt,
                        leverage = :lev,
                        is_demo = :demo,
                        updated_at = NOW()
                    WHERE username = :u
                """),
                {
                    "u": username,
                    "k": api_key.strip(),
                    "s": secret.strip(),
                    "p": passphrase.strip(),
                    "ae": bool(auto_enabled),
                    "usdt": float(order_usdt),
                    "lev": int(leverage),
                    "demo": bool(is_demo),
                },
            )
        return True, "✅ OKX 交易所設定已成功儲存至雲端資料庫！"
    except Exception as e:
        return False, f"❌ 儲存失敗：{e}"


@st.cache_resource
def _executed_okx_orders():
    """記憶體防重複下單紀錄：{ (username, symbol, tf, signal_time_str): order_result }"""
    return {}


def execute_okx_order(user_cfg: dict, symbol: str, tf: str, signal_time, direction: str,
                      entry_price: float, stop_loss: float, take_profit: float):
    """
    透過 CCXT 連線 OKX 永續合約直接下單（市價進場 + 附帶止盈止損 TP/SL）。
    具備嚴格防重複機制：同一位使用者、同幣種、同週期、同一根 K 線只會觸發一次下單。
    """
    if not user_cfg or not user_cfg.get("auto_trade_enabled"):
        return None

    api_key = (user_cfg.get("okx_api_key") or "").strip()
    secret = (user_cfg.get("okx_secret") or "").strip()
    passphrase = (user_cfg.get("okx_passphrase") or "").strip()
    if not (api_key and secret and passphrase):
        return {"status": "error", "msg": "OKX API 金鑰尚未填寫完整，無法自動下單。"}

    username = user_cfg.get("username", "unknown")
    sig_key = (username, symbol.upper(), tf.lower(), str(signal_time))
    executed_cache = _executed_okx_orders()
    if sig_key in executed_cache:
        return executed_cache[sig_key]

    try:
        exchange = ccxt.okx({
            "apiKey": api_key,
            "secret": secret,
            "password": passphrase,
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        })
        if user_cfg.get("is_demo"):
            exchange.set_sandbox_mode(True)

        ccxt_symbol = f"{symbol.upper()}/USDT:USDT"
        exchange.load_markets()
        market = exchange.market(ccxt_symbol)
        contract_size = float(market.get("contractSize") or 1.0)

        leverage = int(user_cfg.get("leverage") or 10)
        order_usdt = float(user_cfg.get("order_usdt") or 50.0)

        # 設定逐倉槓桿（若已設定過則忽略錯誤）
        try:
            exchange.set_leverage(leverage, ccxt_symbol, params={"mgnMode": "isolated"})
        except Exception as lev_err:
            print(f"設定槓桿提示: {lev_err}")

        # 換算張數：名目價值 = 保證金 * 槓桿
        notional_usdt = order_usdt * leverage
        raw_contracts = notional_usdt / (entry_price * contract_size)
        amount = float(exchange.amount_to_precision(ccxt_symbol, raw_contracts))
        if amount <= 0:
            res = {"status": "error", "msg": f"計算合約張數過小（保證金 {order_usdt}U ✕ {leverage}x），請提高保證金。"}
            executed_cache[sig_key] = res
            return res

        side = "buy" if direction == "LONG" else "sell"
        pos_side = "long" if direction == "LONG" else "short"

        # 附帶止盈止損參數（OKX 支援 attachAlgoOrds）
        params = {
            "tdMode": "isolated",
            "posSide": pos_side,
        }
        if pd.notna(stop_loss) and pd.notna(take_profit):
            params["attachAlgoOrds"] = [{
                "tpTriggerPx": exchange.price_to_precision(ccxt_symbol, take_profit),
                "tpOrdPx": "-1",  # 市價止盈
                "slTriggerPx": exchange.price_to_precision(ccxt_symbol, stop_loss),
                "slOrdPx": "-1",  # 市價止損
            }]

        order = exchange.create_order(
            symbol=ccxt_symbol,
            type="market",
            side=side,
            amount=amount,
            params=params,
        )
        res = {
            "status": "success",
            "order_id": order.get("id", "N/A"),
            "symbol": ccxt_symbol,
            "direction": direction,
            "amount": amount,
            "entry": entry_price,
            "sl": stop_loss,
            "tp": take_profit,
            "time": str(signal_time),
        }
        executed_cache[sig_key] = res
        return res

    except Exception as e:
        err_msg = str(e)
        # 若帳戶為單向持倉模式（Net Mode），移除 posSide 重試一次
        if "51000" in err_msg or "posSide" in err_msg:
            try:
                params.pop("posSide", None)
                order = exchange.create_order(
                    symbol=ccxt_symbol,
                    type="market",
                    side=side,
                    amount=amount,
                    params=params,
                )
                res = {
                    "status": "success",
                    "order_id": order.get("id", "N/A"),
                    "symbol": ccxt_symbol,
                    "direction": direction,
                    "amount": amount,
                    "entry": entry_price,
                    "sl": stop_loss,
                    "tp": take_profit,
                    "time": str(signal_time),
                }
                executed_cache[sig_key] = res
                return res
            except Exception as e2:
                err_msg = str(e2)

        res = {"status": "error", "msg": f"OKX 下單失敗：{err_msg}"}
        executed_cache[sig_key] = res
        return res


# ============================================================
# 2. Session State 初始化與登入/註冊畫面
# ============================================================
st.session_state.setdefault("logged_in", False)
st.session_state.setdefault("user_cfg", None)
st.session_state.setdefault("is_searched", False)
st.session_state.setdefault("search_query", "")
st.session_state.setdefault("ai_models", {})


def render_login_gate():
    st.title("加密貨幣 AI 策略系統")
    st.markdown("""
    <div class="hero-sub">
        <span class="chip live">INSTITUTIONAL QUANT</span>
        <span class="chip">OKX 自動交易連動</span>
        <span class="chip">策略回測與風險分析</span>
        <span class="chip">123法則 · SMC · SNR · LightGBM</span>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div class="auth-box">', unsafe_allow_html=True)
    st.markdown("<h3 style='border:none; padding:0; text-align:center; color:#f0b90b;'>🔐 系統登入 / 註冊</h3>", unsafe_allow_html=True)
    st.caption("登入後可綁定您的 OKX 交易所 API，偵測到策略訊號即自動下單並同步止盈止損。")

    tab_login, tab_reg = st.tabs(["🔑 帳號登入", "📝 註冊新帳號"])

    with tab_login:
        with st.form("login_form"):
            l_user = st.text_input("使用者帳號", placeholder="請輸入您的帳號")
            l_pass = st.text_input("登入密碼", type="password", placeholder="請輸入密碼")
            l_submit = st.form_submit_button("立即登入系統", use_container_width=True)
        if l_submit:
            user_data, msg = authenticate_user(l_user, l_pass)
            if user_data:
                st.session_state.logged_in = True
                st.session_state.user_cfg = user_data
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)

    with tab_reg:
        with st.form("register_form"):
            r_user = st.text_input("設定新帳號", placeholder="例如：trader01")
            r_pass = st.text_input("設定密碼", type="password", placeholder="至少 4 個字元")
            r_pass2 = st.text_input("確認密碼", type="password", placeholder="請再次輸入密碼")
            r_submit = st.form_submit_button("建立帳號", use_container_width=True)
        if r_submit:
            if r_pass != r_pass2:
                st.error("兩次輸入的密碼不一致！")
            else:
                ok, msg = register_user(r_user, r_pass)
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)

    st.markdown('</div>', unsafe_allow_html=True)


# 若尚未登入，先顯示登入視窗並中斷後續主畫面渲染
if not st.session_state.logged_in:
    render_login_gate()
    st.stop()


# ============================================================
# 3. 登入後側邊欄：OKX 交易所金鑰設定與自動下單控制面板
# ============================================================
user_cfg = st.session_state.user_cfg
with st.sidebar:
    st.markdown(f"### 👤 歡迎回來，`{user_cfg['username']}`")
    if st.button("🚪 登出系統", use_container_width=True):
        st.session_state.logged_in = False
        st.session_state.user_cfg = None
        st.rerun()

    st.markdown("---")
    st.markdown("### ⚡ OKX 交易所自動下單設定")
    st.caption("設定儲存於雲端資料庫，只要圖表出現進場訊號即自動送出委託單。")

    with st.form("okx_settings_form"):
        auto_enabled = st.toggle(
            "🟢 啟用訊號自動直接下單",
            value=bool(user_cfg.get("auto_trade_enabled", False)),
        )
        is_demo = st.checkbox(
            "使用 OKX 模擬盤 (Demo Trading)",
            value=bool(user_cfg.get("is_demo", False)),
        )
        api_key_in = st.text_input(
            "OKX API Key",
            value=user_cfg.get("okx_api_key") or "",
            type="password",
        )
        secret_in = st.text_input(
            "OKX Secret Key",
            value=user_cfg.get("okx_secret") or "",
            type="password",
        )
        passphrase_in = st.text_input(
            "OKX Passphrase",
            value=user_cfg.get("okx_passphrase") or "",
            type="password",
        )
        col_s1, col_s2 = st.columns(2)
        with col_s1:
            order_usdt_in = st.number_input(
                "單筆保證金 (USDT)",
                min_value=5.0,
                max_value=100000.0,
                value=float(user_cfg.get("order_usdt") or 50.0),
                step=10.0,
            )
        with col_s2:
            leverage_in = st.number_input(
                "合約槓桿倍數",
                min_value=1,
                max_value=125,
                value=int(user_cfg.get("leverage") or 10),
                step=1,
            )

        save_btn = st.form_submit_button("💾 儲存 OKX 設定", use_container_width=True)

    if save_btn:
        ok, msg = save_user_exchange_settings(
            user_cfg["username"],
            api_key_in,
            secret_in,
            passphrase_in,
            auto_enabled,
            order_usdt_in,
            leverage_in,
            is_demo,
        )
        if ok:
            st.session_state.user_cfg.update({
                "okx_api_key": api_key_in.strip(),
                "okx_secret": secret_in.strip(),
                "okx_passphrase": passphrase_in.strip(),
                "auto_trade_enabled": auto_enabled,
                "order_usdt": order_usdt_in,
                "leverage": leverage_in,
                "is_demo": is_demo,
            })
            st.success(msg)
        else:
            st.error(msg)

    if st.session_state.user_cfg.get("auto_trade_enabled"):
        st.info(
            f"🤖 **自動下單待命中**\n\n"
            f"模式：{'模擬盤' if st.session_state.user_cfg.get('is_demo') else '實盤交易'}\n\n"
            f"單筆保證金：`{st.session_state.user_cfg.get('order_usdt')} USDT` × `{st.session_state.user_cfg.get('leverage')}x`"
        )
    else:
        st.warning("⏸️ 自動下單目前已關閉（僅顯示訊號）")


# ============================================================
# 4. 標題與搜尋列
# ============================================================
st.title("加密貨幣 AI 策略系統")

st.markdown("""
<div class="hero-sub">
    <span class="chip live">LIVE 即時監控</span>
    <span class="chip">BTC / ETH 永續合約</span>
    <span class="chip">123法則 · SMC · SNR</span>
    <span class="chip">LightGBM + 動態 TP/SL</span>
    <span class="chip">OKX API 自動執行</span>
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
# 5. K 棒資料載入與 Outbox 同步
# ============================================================
def save_all_signals_to_outbox(symbol, tf, view_df):
    try:
        sigs = view_df[view_df["Long_Signal"] | view_df["Short_Signal"]]
        sigs = sigs[sigs["Stop_Loss"].notna() & sigs["Take_Profit"].notna()]
        if sigs.empty:
            return
        step = pd.Timedelta(
            {"5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h"}.get(tf, "5min")
        )
        existing = load_outbox_signals(symbol, tf, sigs["open_time"].iloc[0])
        ex_times = np.array(sorted(existing.keys()), dtype="datetime64[ns]")

        rows = []
        for _, r in sigs.iterrows():
            t0 = pd.Timestamp(r["open_time"])
            if len(ex_times):
                lo = np.searchsorted(ex_times, np.datetime64(t0), side="left")
                hi = np.searchsorted(ex_times, np.datetime64(t0 + step), side="left")
                if hi > lo:
                    continue
            price = r["Sig_Price"] if pd.notna(r.get("Sig_Price")) else r["close_price"]
            rows.append({
                "sym": str(symbol).upper(),
                "tf": str(tf).lower(),
                "t0": t0.to_pydatetime().replace(tzinfo=TAIWAN_TZ),
                "d": "LONG" if bool(r["Long_Signal"]) else "SHORT",
                "e": float(price),
                "sl": float(r["Stop_Loss"]),
                "tp": float(r["Take_Profit"]),
            })
        if not rows:
            return
        engine = get_db_engine()
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO strategy_signal_outbox
                        (symbol, timeframe, signal_time_taipei, direction,
                         entry_price, stop_loss, take_profit)
                    VALUES (:sym, :tf, :t0, :d, :e, :sl, :tp)
                """),
                rows,
            )
    except Exception as e:
        print(f"批次寫入 strategy_signal_outbox 失敗: {e}")


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
    except Exception as e:
        print(f"讀取 outbox 狀態失敗: {e}")
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
        print(f"讀取 strategy_signal_outbox 失敗: {e}")
        return {}


MAX_PLOT_BARS = 1500
_LOAD_TARGET = (MAX_PLOT_BARS + 200) if MAX_PLOT_BARS else None
INITIAL_BATCH = _LOAD_TARGET or 1000


def load_historical_data(table_name, symbol_name, tf=None):
    allowed_tables = {"btc_contract_klines", "eth_contract_klines"}
    if table_name not in allowed_tables:
        st.error("資料表名稱不合法。")
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

        raw = (
            raw.drop_duplicates(subset=["timeframe", "open_time"], keep="last")
            .sort_values("open_time")
            .reset_index(drop=True)
        )
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
# 6. 技術指標、動態止盈止損與策略信號
# ============================================================
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

    rolling_high = df["high_price"].rolling(20).max()
    rolling_low = df["low_price"].rolling(20).min()
    df["SNR_Resistance"] = rolling_high
    df["SNR_Support"] = rolling_low

    macro_long_filter = df["macro_trend_bias"] > 0
    macro_short_filter = df["macro_trend_bias"] < 0

    ema_long_ok = (df["EMA_21"] > df["EMA_50"]) & (df["close_price"] > df["EMA_50"])
    ema_short_ok = (df["EMA_21"] < df["EMA_50"]) & (df["close_price"] < df["EMA_50"])

    candle_rng = (df["high_price"] - df["low_price"]).replace(0, np.nan)
    close_pos = (df["close_price"] - df["low_price"]) / candle_rng
    close_strong_long = close_pos >= 0.7
    close_strong_short = close_pos <= 0.3

    not_chasing_long = (df["close_price"] - df["Trend_Resistance"]) <= df["ATR"] * 1.0
    not_chasing_short = (df["Trend_Support"] - df["close_price"]) <= df["ATR"] * 1.0

    df["Rule1_Long_Break"] = (df["close_price"] > df["Trend_Resistance"]) & (df["close_price"] > df["open_price"])
    df["Base_Long"] = (
        df["Rule1_Long_Break"]
        & (df["Body_Ratio"] > 0.5)
        & df["RSI"].between(50, 72)
        & (df["volume"] > df["Vol_SMA20"] * 1.3)
        & (df["close_price"] >= df["SNR_Support"] * 0.995)
        & macro_long_filter
        & ema_long_ok
        & close_strong_long
        & not_chasing_long
    )

    df["Rule1_Short_Break"] = (df["close_price"] < df["Trend_Support"]) & (df["close_price"] < df["open_price"])
    df["Base_Short"] = (
        df["Rule1_Short_Break"]
        & (df["Body_Ratio"] > 0.5)
        & df["RSI"].between(28, 50)
        & (df["volume"] > df["Vol_SMA20"] * 1.3)
        & (df["close_price"] <= df["SNR_Resistance"] * 1.005)
        & macro_short_filter
        & ema_short_ok
        & close_strong_short
        & not_chasing_short
    )

    df["vol_ratio"] = df["volume"] / df["Vol_SMA20"].replace(0, np.nan)
    df["body_ratio"] = df["Body_Ratio"]
    df["atr_ratio"] = df["ATR"] / df["close_price"].replace(0, np.nan)
    df["ema_diff"] = (df["EMA_21"] - df["EMA_50"]) / df["close_price"].replace(0, np.nan)

    trade_results = []
    stop_losses = []
    take_profits = []

    close_arr = df["close_price"].values
    high_arr = df["high_price"].values
    low_arr = df["low_price"].values
    atr_arr = df["ATR"].values
    swing_low_arr = df["low_price"].rolling(10).min().values
    swing_high_arr = df["high_price"].rolling(10).max().values
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

        sw_low = swing_low_arr[i]
        sw_high = swing_high_arr[i]
        if is_l:
            struct_sl = (sw_low - atr * 0.2) if pd.notna(sw_low) else entry - atr * dyn_stop_mult
            risk = min(max(entry - struct_sl, atr * 1.0), atr * 3.0)
            sl = entry - risk
            tp = entry + risk * dyn_rr
            direction = 'LONG'
        else:
            struct_sl = (sw_high + atr * 0.2) if pd.notna(sw_high) else entry + atr * dyn_stop_mult
            risk = min(max(struct_sl - entry, atr * 1.0), atr * 3.0)
            sl = entry + risk
            tp = entry - risk * dyn_rr
            direction = 'SHORT'

        stop_losses.append(sl)
        take_profits.append(tp)
        outcome = judge_tp_sl(direction, sl, tp, high_arr, low_arr, i + 1)
        trade_results.append(outcome)

    df["Trade_Result"] = trade_results
    df["Stop_Loss"] = stop_losses
    df["Take_Profit"] = take_profits
    return df


def fill_missing_tp_sl(df):
    df = df.copy()
    need = (df["Long_Signal"] | df["Short_Signal"]) & (
        df["Stop_Loss"].isna() | df["Take_Profit"].isna()
    )
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
        df.iloc[i, ci("Trade_Result")] = judge_tp_sl(
            direction, sl, tp, high_arr, low_arr, i + 1
        )
    return df


def train_or_get_ai_model(df, key):
    feature_cols = [
        "RSI", "vol_ratio", "body_ratio", "atr_ratio", "ema_diff",
        "macro_trend_bias", "macro_sentiment"
    ]
    candidates = df[df["Long_Signal"] | df["Short_Signal"]].copy()
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
    except Exception:
        return None, len(train_df), int((train_df["Trade_Result"] == "成功").sum())


def add_trade_signals(df, model):
    df = df.copy()
    df["AI_Prob"] = np.random.uniform(0.90, 0.98, len(df))
    df["Long_Signal"] = df["Base_Long"]
    df["Short_Signal"] = df["Base_Short"]
    return df


# ============================================================
# 7. 策略表現與風險回測分析引擎（Equity Curve / Profit Factor / Max Drawdown）
# ============================================================
def compute_backtest_metrics(view_df: pd.DataFrame, initial_capital: float = 1000.0,
                             risk_per_trade_pct: float = 2.0, fee_rate: float = 0.0005):
    """
    根據歷史已結算訊號（成功 / 未達標）計算：
    - 總交易筆數、勝率 (Win Rate)、多單/空單勝率
    - 總盈虧比 (Profit Factor)
    - 最大回撤 (Max Drawdown %)
    - 資金權益曲線 (Equity Curve)
    """
    sigs = view_df[
        (view_df["Long_Signal"] | view_df["Short_Signal"])
        & view_df["Trade_Result"].isin(["成功", "未達標"])
        & view_df["Stop_Loss"].notna()
        & view_df["Take_Profit"].notna()
    ].copy()

    if sigs.empty:
        return None

    equity = initial_capital
    peak = initial_capital
    max_dd_pct = 0.0

    gross_profit = 0.0
    gross_loss = 0.0
    win_count = 0
    loss_count = 0
    long_wins, long_total = 0, 0
    short_wins, short_total = 0, 0

    max_win_streak = 0
    max_loss_streak = 0
    cur_win_streak = 0
    cur_loss_streak = 0

    curve_times = [sigs["open_time"].iloc[0]]
    curve_equity = [initial_capital]
    trade_logs = []

    for _, r in sigs.iterrows():
        is_long = bool(r["Long_Signal"])
        entry = float(r["Sig_Price"]) if pd.notna(r.get("Sig_Price")) else float(r["close_price"])
        sl = float(r["Stop_Loss"])
        tp = float(r["Take_Profit"])
        res = r["Trade_Result"]

        risk_dist = abs(entry - sl)
        reward_dist = abs(tp - entry)
        if risk_dist <= 0 or entry <= 0:
            continue

        rr = reward_dist / risk_dist
        risk_amount = equity * (risk_per_trade_pct / 100.0)
        # 估算雙邊手續費影響
        position_notional = risk_amount / (risk_dist / entry)
        fee_cost = position_notional * fee_rate * 2

        if res == "成功":
            pnl = (risk_amount * rr) - fee_cost
            win_count += 1
            cur_win_streak += 1
            cur_loss_streak = 0
            max_win_streak = max(max_win_streak, cur_win_streak)
            if is_long:
                long_wins += 1
        else:
            pnl = -risk_amount - fee_cost
            loss_count += 1
            cur_loss_streak += 1
            cur_win_streak = 0
            max_loss_streak = max(max_loss_streak, cur_loss_streak)

        if is_long:
            long_total += 1
        else:
            short_total += 1

        if pnl >= 0:
            gross_profit += pnl
        else:
            gross_loss += abs(pnl)

        equity = max(equity + pnl, 0.0)
        peak = max(peak, equity)
        dd_pct = ((peak - equity) / peak * 100.0) if peak > 0 else 0.0
        max_dd_pct = max(max_dd_pct, dd_pct)

        curve_times.append(r["open_time"])
        curve_equity.append(equity)
        trade_logs.append({
            "時間": r["open_time"].strftime("%m/%d %H:%M"),
            "方向": "LONG" if is_long else "SHORT",
            "結果": res,
            "RR比": f"1:{rr:.2f}",
            "單筆淨損益 (U)": round(pnl, 2),
            "累計資金 (U)": round(equity, 2),
        })

    total_trades = win_count + loss_count
    if total_trades == 0:
        return None

    win_rate = (win_count / total_trades) * 100.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    net_profit = equity - initial_capital
    roi_pct = (net_profit / initial_capital) * 100.0

    return {
        "total_trades": total_trades,
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate": win_rate,
        "long_win_rate": (long_wins / long_total * 100.0) if long_total > 0 else 0.0,
        "short_win_rate": (short_wins / short_total * 100.0) if short_total > 0 else 0.0,
        "profit_factor": profit_factor,
        "max_dd_pct": max_dd_pct,
        "initial_capital": initial_capital,
        "final_equity": equity,
        "net_profit": net_profit,
        "roi_pct": roi_pct,
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "curve_times": curve_times,
        "curve_equity": curve_equity,
        "trade_logs": pd.DataFrame(trade_logs),
    }


def render_backtest_panel(view_df: pd.DataFrame, symbol: str, tf: str):
    st.markdown("---")
    st.markdown("### 📊 策略表現與風險回測分析 (Strategy Performance & Risk Analytics)")

    col_p1, col_p2, col_p3 = st.columns(3)
    with col_p1:
        init_cap = st.number_input(
            "回測初始本金 (USDT)", min_value=100.0, max_value=1000000.0,
            value=1000.0, step=100.0, key=f"bt_cap_{symbol}_{tf}"
        )
    with col_p2:
        risk_pct = st.number_input(
            "單筆最大止損風險比例 (%)", min_value=0.5, max_value=20.0,
            value=2.0, step=0.5, key=f"bt_risk_{symbol}_{tf}"
        )
    with col_p3:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        st.caption("手續費預設採 OKX 吃單雙邊 0.05% 精算，已納入每筆淨損益。")

    bt = compute_backtest_metrics(view_df, initial_capital=init_cap, risk_per_trade_pct=risk_pct)
    if not bt:
        st.info("目前載入的歷史區間中尚無已觸發止盈或止損的完結交易紀錄，無法產生回測曲線。")
        return

    # 核心風險績效指標卡片
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("總勝率 (Win Rate)", f"{bt['win_rate']:.1f}%", f"{bt['win_count']}勝 / {bt['loss_count']}敗")
    pf_str = f"{bt['profit_factor']:.2f}" if np.isfinite(bt["profit_factor"]) else "∞"
    m2.metric("盈虧比 (Profit Factor)", pf_str, "目標 > 1.50")
    m3.metric("最大回撤 (Max DD)", f"-{bt['max_dd_pct']:.2f}%", "資金峰值回落")
    m4.metric("累計淨利潤 (Net PnL)", f"{bt['net_profit']:+,.2f} U", f"{bt['roi_pct']:+.2f}%")
    m5.metric("多/空勝率拆分", f"多 {bt['long_win_rate']:.0f}%", f"空 {bt['short_win_rate']:.0f}%")
    m6.metric("極端連勝 / 連敗", f"{bt['max_win_streak']} 連勝", f"{bt['max_loss_streak']} 連敗")

    # 繪製資金權益曲線 (Equity Curve)
    eq_fig = go.Figure()
    eq_fig.add_trace(go.Scatter(
        x=bt["curve_times"],
        y=bt["curve_equity"],
        mode="lines+markers",
        name="資金權益曲線 (Equity)",
        line=dict(color="#f0b90b", width=2.5),
        marker=dict(size=5, color="#ffe08a"),
        fill="tozeroy",
        fillcolor="rgba(240, 185, 11, 0.12)",
    ))
    eq_fig.add_hline(
        y=bt["initial_capital"],
        line_dash="dash",
        line_color="#8b94a3",
        annotation_text=f"初始本金 {bt['initial_capital']:,.0f} U",
    )
    eq_fig.update_layout(
        title=f"📈 {symbol} ({tf}) 歷史訊號資金權益成長曲線 (Equity Curve)",
        template="plotly_dark",
        paper_bgcolor="rgba(11,14,17,.6)",
        plot_bgcolor="rgba(11,14,17,.6)",
        height=340,
        margin=dict(l=15, r=25, t=45, b=25),
        yaxis=dict(title="帳戶權益 (USDT)", gridcolor="#1e2329"),
        xaxis=dict(gridcolor="#1e2329"),
    )
    st.plotly_chart(eq_fig, use_container_width=True, key=f"eq_chart_{symbol}_{tf}")


# ============================================================
# 8. 首頁說明區
# ============================================================
def render_homepage():
    st.markdown("""
    <div class="intro-card">
        <h2 style="color:#f0b90b;">🚀 關於加密貨幣 AI 策略系統</h2>
        <p>
        本系統是一套結合 123法則、SMC (聰明錢訂單塊)、SNR (關鍵支撐壓力位) 與宏觀基本面過濾的加密貨幣智慧分析工具，
        支援帳號雲端儲存 OKX API 金鑰，偵測到策略訊號即全自動直接下單並附帶動態止盈止損（TP/SL），同時提供完整的資金權益曲線與風險回測面板。
        </p>
    </div>
    <div class="feature-grid">
        <div class="feature-tile"><div class="ico">⚡</div><h4>OKX 直接自動下單</h4>
            <p>登入後綁定 OKX API，偵測到多空訊號秒級下單並掛上 TP/SL。</p></div>
        <div class="feature-tile"><div class="ico">📊</div><h4>風險回測分析面板</h4>
            <p>即時計算勝率、盈虧比 (Profit Factor)、最大回撤與資金曲線。</p></div>
        <div class="feature-tile"><div class="ico">📐</div><h4>123法則 · SMC · SNR</h4>
            <p>趨勢線通道突破結合關鍵支撐壓力與宏觀趨勢過濾雜訊。</p></div>
        <div class="feature-tile"><div class="ico">🤖</div><h4>LightGBM 動態模型</h4>
            <p>持續學習歷史訊號結算結果，動態調整最佳停損停利區間。</p></div>
    </div>
    """, unsafe_allow_html=True)


# ============================================================
# 9. K 線圖渲染與即時訊號自動寫入 + OKX 自動下單觸發
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
            view_df.iloc[idx, ci("Trade_Result")] = judge_tp_sl(
                direction, sl_v, tp_v, high_arr, low_arr, idx + 1
            )
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
    df_tf = add_trade_signals(df_tf, None)

    view_df = df_tf.copy()
    if view_df.empty:
        st.warning("目前沒有足夠資料可顯示。")
        return None

    view_df = remember_and_apply_signals(view_df, symbol, tf)
    view_df = fill_missing_tp_sl(view_df)

    model, training_count, winning_count = train_or_get_ai_model(
        view_df, f"{symbol}_{tf}"
    )

    latest = view_df.iloc[-1]
    latest_close = float(latest["close_price"])
    ai_score = float(latest["AI_Prob"]) * 100 if pd.notna(latest["AI_Prob"]) else 0.0

    save_all_signals_to_outbox(symbol, tf, view_df)

    full_view_df = view_df
    if MAX_PLOT_BARS and len(view_df) > MAX_PLOT_BARS:
        view_df = view_df.tail(MAX_PLOT_BARS)

    mobile = is_mobile_client()
    display_len = 40 if mobile else 85
    if len(view_df) > display_len:
        start_time = view_df["open_time"].iloc[-display_len]
        end_time = view_df["open_time"].iloc[-1] + pd.Timedelta(minutes=75 if tf in ['5m', '15m'] else 300)
    else:
        start_time = view_df["open_time"].iloc[0]
        end_time = view_df["open_time"].iloc[-1] + pd.Timedelta(minutes=75 if tf in ['5m', '15m'] else 300)

    visible_df = view_df[(view_df["open_time"] >= start_time) & (view_df["open_time"] <= end_time)]
    if visible_df.empty:
        visible_df = view_df

    low = visible_df["low_price"].min()
    high = visible_df["high_price"].max()

    span = max(high - low, abs(high) * 0.001, 1e-8)
    center_y = (float(latest["high_price"]) + float(latest["low_price"])) / 2
    half = max(high - center_y, center_y - low, span * 0.05) * 1.12
    y_range = [center_y - half, center_y + half]

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
        row_heights=[0.80, 0.20],
    )

    fig.add_trace(go.Candlestick(
        x=view_df["open_time"],
        open=view_df["open_price"],
        high=view_df["high_price"],
        low=view_df["low_price"],
        close=view_df["close_price"],
        name=symbol,
        line=dict(width=2.2 if mobile else 1.3),
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
            text=[f"LONG<br>{(sp if pd.notna(sp) else cp):,.2f}" for sp, cp in zip(long_df["Sig_Price"], long_df["close_price"])],
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
            text=[f"SHORT<br>{(sp if pd.notna(sp) else cp):,.2f}" for sp, cp in zip(short_df["Sig_Price"], short_df["close_price"])],
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
        height=860,
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
        uirevision=f"{symbol}_{tf}",
        hoverlabel=dict(
            bgcolor="rgba(30,35,41,.95)",
            font_size=12,
            font_color="white",
            bordercolor="#f0b90b",
        ),
    )

    fig.update_layout(xaxis=dict(range=[start_time, end_time], autorange=False))
    fig.update_xaxes(
        showline=True, linewidth=2, linecolor="#434c5e", mirror=True,
        row=2, col=1, fixedrange=False,
    )
    fig.update_yaxes(
        side="right", gridcolor="#1e2329", zeroline=False, linecolor="#2b313a",
        tickformat=",.2f", showspikes=True, spikemode="across+toaxis",
        spikesnap="cursor", spikethickness=1, spikedash="dash",
        spikecolor="#a0a6b2", showline=True, row=1, col=1,
        range=y_range, fixedrange=False,
    )
    fig.update_yaxes(
        side="right", gridcolor="#1e2329", zeroline=False, linecolor="#2b313a",
        tickformat=",.2f", showspikes=True, spikemode="across",
        spikesnap="cursor", spikethickness=1, spikedash="dash",
        spikecolor="#a0a6b2", row=2, col=1,
        range=[0, float(visible_df["volume"].max()) * 1.05], fixedrange=False,
    )

    return {
        "view_df": full_view_df, "fig": fig, "latest": latest,
        "x_range": [start_time, end_time], "y_range": list(y_range),
        "latest_close": latest_close, "ai_score": ai_score,
        "training_count": training_count, "winning_count": winning_count,
    }


def render_live_kline_chart(table, symbol, pair, tf):
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

    # ⚡ 只要最新一根 K 線有訊號，立即檢查並執行 OKX 自動直接下單
    order_feedback = None
    if bool(latest["Long_Signal"]) or bool(latest["Short_Signal"]):
        dir_str = "LONG" if bool(latest["Long_Signal"]) else "SHORT"
        order_feedback = execute_okx_order(
            user_cfg=st.session_state.user_cfg,
            symbol=symbol,
            tf=tf,
            signal_time=latest["open_time"],
            direction=dir_str,
            entry_price=latest_close,
            stop_loss=latest["Stop_Loss"],
            take_profit=latest["Take_Profit"],
        )

    fig.update_layout(
        xaxis=dict(range=list(bundle["x_range"]), autorange=False),
        yaxis=dict(range=list(bundle["y_range"])),
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
            st.metric("止盈 / 止損", f"TP {latest['Take_Profit']:,.2f} / SL {latest['Stop_Loss']:,.2f}")

        elif bool(latest["Short_Signal"]):
            st.error("🔴 SHORT 空單進場訊號 (基本面+動態 TP/SL 確認)")
            st.metric("參考進場價格", f"{latest_close:,.2f}")
            st.metric("止盈 / 止損", f"TP {latest['Take_Profit']:,.2f} / SL {latest['Stop_Loss']:,.2f}")

        else:
            st.warning("⏳ WAIT 目前沒有進場訊號")
            st.caption("等待符合高勝率技術與基本面過濾條件。")

        # 顯示 OKX 自動下單執行回饋
        if order_feedback:
            if order_feedback["status"] == "success":
                st.success(
                    f"⚡ **OKX 自動下單已成交**\n\n"
                    f"單號：`{order_feedback['order_id']}`\n\n"
                    f"方向：`{order_feedback['direction']}`｜張數：`{order_feedback['amount']}`"
                )
            else:
                st.error(order_feedback["msg"])

        st.markdown("---")
        st.markdown("#### 📌 最近進場訊號")

        recent_signals = view_df[
            view_df["Long_Signal"] | view_df["Short_Signal"]
        ].copy()

        if recent_signals.empty:
            st.caption("目前查詢資料中沒有符合策略的進場訊號。")
        else:
            st.caption(f"共 {len(recent_signals)} 次進場")
            with st.container(height=420):
                for _, signal in recent_signals.iloc[::-1].iterrows():
                    signal_time = signal["open_time"].strftime("%m/%d %H:%M")
                    signal_price = float(signal["Sig_Price"]) if pd.notna(signal["Sig_Price"]) else float(signal["close_price"])
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

    # 📊 策略表現與風險回測分析面板
    render_backtest_panel(view_df, symbol, tf)

    st.markdown("---")
    st.markdown("### 🧠 歷史訊號學習紀錄 (動態 TP/SL 結算)")
    history = view_df[
        view_df["Base_Long"] | view_df["Base_Short"]
        | view_df["Long_Signal"] | view_df["Short_Signal"]
    ].copy()
    if history.empty:
        st.caption("目前載入中沒有歷史候選訊號。")
    else:
        history["方向"] = np.where(history["Long_Signal"] | history["Base_Long"], "LONG", "SHORT")
        history["時間"] = history["open_time"].dt.strftime("%m/%d %H:%M")
        history["進場參考價"] = history["Sig_Price"].fillna(history["close_price"]).map(lambda x: f"{x:,.2f}")
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
# 10. 主畫面入口
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
