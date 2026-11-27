import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import streamlit as st
from sqlalchemy import create_engine, text

st.set_page_config(page_title="OKX Signal Notifier", page_icon="🚀", layout="centered")

TAIWAN_TZ = ZoneInfo("Asia/Taipei")
POLL_INTERVAL_SECONDS = 30   # 每幾秒檢查一次 outbox
BATCH_SIZE = 10              # 每次最多處理幾筆


def now_text():
    return datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d %H:%M:%S")


def get_config(name):
    """先讀環境變數，再讀 Streamlit Secrets。"""
    value = os.getenv(name, "").strip()
    if value:
        return value
    try:
        return str(st.secrets.get(name, "")).strip()
    except Exception:
        return ""


def build_engine():
    url = get_config("SUPABASE_DB_URL")
    if not url:
        return None
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return create_engine(
        url,
        pool_size=1,
        max_overflow=0,
        pool_pre_ping=True,
        pool_recycle=300,
    )


def build_payload(symbol, tf, direction, sig_time, entry, sl, tp):
    emoji = "🟢" if direction == "LONG" else "🔴"
    return {
        "content": "🚨 **加密貨幣技術分析 AI 策略新訊號** 🚨",
        "embeds": [{
            "title": f"{emoji} {symbol} ({tf}) - {direction}",
            "color": 3066993 if direction == "LONG" else 15158332,
            "fields": [
                {"name": "⏰ 訊號時間 (TAIPEI)", "value": str(sig_time), "inline": False},
                {"name": "💰 參考進場價", "value": f"`{entry:,.2f}`", "inline": True},
                {"name": "🛡️ 建議止損價 (SL)", "value": f"`{sl:,.2f}`", "inline": True},
                {"name": "🎯 建議止盈價 (TP)", "value": f"`{tp:,.2f}`", "inline": True},
            ],
            "footer": {"text": f"OKX Strategy Scanner Bot | {now_text()}"},
        }],
    }


def send_to_discord(webhook_url, payload):
    """回傳 (成功與否, 錯誤訊息)。遇到 429 會等待後重試一次。"""
    for attempt in range(2):
        try:
            res = requests.post(webhook_url, json=payload, timeout=10)
        except requests.RequestException as e:
            return False, f"request error: {e}"

        if res.status_code in (200, 204):
            return True, None
        if res.status_code == 429 and attempt == 0:
            try:
                wait = float(res.json().get("retry_after", 1))
            except Exception:
                wait = 1.0
            time.sleep(min(wait, 10))
            continue
        return False, f"HTTP {res.status_code}: {res.text[:200]}"
    return False, "rate limited"


def reset_stuck_rows(engine):
    """程式重啟時，把上次中斷而卡在 processing 的訊號放回 pending。"""
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE strategy_signal_outbox
            SET status = 'pending'
            WHERE status = 'processing'
        """))


def process_outbox(engine, webhook_url):
    """領取 pending 訊號 → 發送 Discord → 標記 sent。回傳成功發送數量。"""
    # 先把資料「領取」並 commit，避免送 Discord 時長時間占用交易
    with engine.begin() as conn:
        rows = conn.execute(text("""
            UPDATE strategy_signal_outbox
            SET status = 'processing'
            WHERE id IN (
                SELECT id FROM strategy_signal_outbox
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT :limit
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id, symbol, timeframe, direction, signal_time_taipei,
                      entry_price, stop_loss, take_profit, created_at
        """), {"limit": BATCH_SIZE}).fetchall()

    rows = sorted(rows, key=lambda r: (r[8], r[0]))
    sent_count = 0

    for sig_id, symbol, tf, direction, sig_time, entry, sl, tp, _ in rows:
        payload = build_payload(symbol, tf, direction, sig_time, entry, sl, tp)
        ok, err = send_to_discord(webhook_url, payload)

        with engine.begin() as conn:
            if ok:
                conn.execute(text("""
                    UPDATE strategy_signal_outbox
                    SET status = 'sent', sent_at = NOW(), error_message = NULL
                    WHERE id = :id
                """), {"id": sig_id})
                sent_count += 1
                print(f"✅ 訊號 ID {sig_id} ({symbol} {tf} {direction}) 已發送到 Discord！")
            else:
                conn.execute(text("""
                    UPDATE strategy_signal_outbox
                    SET status = 'pending', error_message = :err
                    WHERE id = :id
                """), {"id": sig_id, "err": err})
                print(f"⚠️ 訊號 ID {sig_id} 發送失敗，稍後重試: {err}")

        time.sleep(0.5)  # 避免觸發 Discord 速率限制

    return sent_count


@st.cache_resource
def get_worker_state():
    """整個 Streamlit 行程只會建立一次，不會因為多個瀏覽器連線而重複啟動。"""
    state = {"last_run": None, "last_error": None, "total_sent": 0, "started_at": now_text()}

    def background_loop():
        engine = None
        webhook_url = get_config("DISCORD_WEBHOOK_URL")
        first_run = True

        while True:
            try:
                if engine is None:
                    engine = build_engine()
                if engine is None or not webhook_url:
                    state["last_error"] = "未設定 SUPABASE_DB_URL 或 DISCORD_WEBHOOK_URL"
                else:
                    if first_run:
                        reset_stuck_rows(engine)
                        first_run = False
                    state["total_sent"] += process_outbox(engine, webhook_url)
                    state["last_error"] = None
                state["last_run"] = now_text()
            except Exception as e:
                state["last_error"] = str(e)
                print(f"❌ 背景迴圈異常: {e}")
            time.sleep(POLL_INTERVAL_SECONDS)

    threading.Thread(target=background_loop, daemon=True, name="outbox-notifier").start()
    return state


# ---- 頁面（給 UptimeRobot 訪問用） ----
state = get_worker_state()
st.write("OKX Signal Notifier is running 24/7! 🚀")
st.caption(
    f"啟動時間：{state['started_at']}｜最近檢查：{state['last_run'] or '尚未執行'}｜"
    f"累計發送：{state['total_sent']}"
)
if state["last_error"]:
    st.warning(f"最近錯誤：{state['last_error']}")
