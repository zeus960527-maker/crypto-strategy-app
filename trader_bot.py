"""
Discord 訊號佇列 Worker
由 GitHub Actions 雲端排程執行；不依賴使用者電腦開機。
讀取 dashboard_app.py 寫入 Supabase 的 strategy_signal_outbox，
成功傳送 Discord 後才標記 sent。
"""
import os
import sys
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg2
import requests
from psycopg2.extras import RealDictCursor

TAIWAN_TZ = ZoneInfo("Asia/Taipei")
DATABASE_URL = os.getenv("SUPABASE_DB_URL", "").strip()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
BATCH_SIZE = int(os.getenv("DISCORD_BATCH_SIZE", "30"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


class TaiwanFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, TAIWAN_TZ)
        return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S")


for handler in logging.getLogger().handlers:
    handler.setFormatter(TaiwanFormatter("%(asctime)s %(levelname)s %(message)s"))


def validate_config():
    missing = []
    if not DATABASE_URL:
        missing.append("SUPABASE_DB_URL")
    if not DISCORD_WEBHOOK_URL:
        missing.append("DISCORD_WEBHOOK_URL")
    if missing:
        raise RuntimeError("缺少環境變數：" + ", ".join(missing))


def connect_db():
    return psycopg2.connect(
        DATABASE_URL,
        connect_timeout=10,
        sslmode="require",
        options="-c statement_timeout=20000",
    )


def fetch_pending_signals(conn):
    """
    原子性地把 pending 改成 processing 並取出資料。
    GitHub Actions workflow 同時只允許一個 worker，避免並行重複發送。
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            WITH picked AS (
                SELECT id
                FROM strategy_signal_outbox
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT %s
                FOR UPDATE SKIP LOCKED
            )
            UPDATE strategy_signal_outbox AS q
            SET status = 'processing', error_message = NULL
            FROM picked
            WHERE q.id = picked.id
            RETURNING q.*
        """, (BATCH_SIZE,))
        rows = cur.fetchall()
    conn.commit()
    return rows


def send_discord(signal):
    is_long = signal["direction"] == "LONG"
    color = 0x0ECB81 if is_long else 0xF6465D
    emoji = "🟢" if is_long else "🔴"
    probability = signal.get("ai_probability")
    ai_text = (
        f"{float(probability) * 100:.1f}%（模型估計值，不是實際勝率）"
        if probability is not None else "暫無模型分數"
    )

    payload = {
        "embeds": [{
            "title": f"{emoji} AI 進場訊號：{signal['direction']}",
            "color": color,
            "fields": [
                {"name": "交易對", "value": f"{signal['symbol']}/USDT 永續", "inline": True},
                {"name": "週期", "value": signal["timeframe"], "inline": True},
                {"name": "進場方向", "value": signal["direction"], "inline": True},
                {"name": "訊號時間（台灣）", "value": signal["signal_time_taipei"] + " UTC+8", "inline": False},
                {"name": "進場參考價", "value": f"{float(signal['entry_price']):,.4f} USDT", "inline": True},
                {"name": "止損 SL", "value": f"{float(signal['stop_loss']):,.4f} USDT", "inline": True},
                {"name": "止盈 TP", "value": f"{float(signal['take_profit']):,.4f} USDT", "inline": True},
                {"name": "AI 模型分數", "value": ai_text, "inline": False},
            ],
            "footer": {"text": "BTC / ETH AI 交易監控｜ATR 止損 × 2.5R 止盈"},
        }]
    }

    response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=15)
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Discord HTTP {response.status_code}: {response.text[:400]}")


def update_status(conn, signal_id, status, error=None):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE strategy_signal_outbox
            SET status = %s,
                sent_at = CASE WHEN %s = 'sent' THEN NOW() ELSE sent_at END,
                error_message = %s
            WHERE id = %s
        """, (status, status, error[:1000] if error else None, signal_id))
    conn.commit()


def main():
    validate_config()
    sent = 0
    failed = 0

    with connect_db() as conn:
        # 確保 dashboard 尚未啟動時 worker 也能建立資料表。
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS strategy_signal_outbox (
                    id BIGSERIAL PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    direction TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
                    candle_open_time TIMESTAMPTZ NOT NULL,
                    signal_time_taipei TEXT NOT NULL,
                    entry_price DOUBLE PRECISION NOT NULL,
                    stop_loss DOUBLE PRECISION NOT NULL,
                    take_profit DOUBLE PRECISION NOT NULL,
                    atr DOUBLE PRECISION,
                    ai_probability DOUBLE PRECISION,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'processing', 'sent')),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    sent_at TIMESTAMPTZ,
                    error_message TEXT,
                    UNIQUE(symbol, timeframe, candle_open_time, direction)
                )
            """)
        # 若前一次 runner 在傳送中途被強制終止，超過 10 分鐘的 processing 重新排隊。
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE strategy_signal_outbox
                SET status = 'pending',
                    error_message = 'Recovered stale processing claim'
                WHERE status = 'processing'
                  AND created_at < NOW() - INTERVAL '10 minutes'
            """)
        conn.commit()

        signals = fetch_pending_signals(conn)
        logging.info("本次取得待傳送訊號：%s 筆", len(signals))

        for signal in signals:
            try:
                send_discord(signal)
                update_status(conn, signal["id"], "sent")
                sent += 1
                logging.info(
                    "Discord 已傳送：%s %s %s",
                    signal["symbol"], signal["timeframe"], signal["direction"],
                )
            except Exception as exc:
                failed += 1
                logging.exception("訊號 %s 傳送失敗", signal["id"])
                # 退回 pending，下一次 GitHub Actions 可重試。
                update_status(conn, signal["id"], "pending", str(exc))

    logging.info("Worker 完成：成功 %s 筆，失敗 %s 筆", sent, failed)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
