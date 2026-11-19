import os
import ccxt
import psycopg2
import time
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, jsonify

app = Flask(__name__)

# 用來確保背景執行緒只會被啟動一次的鎖與旗標
_worker_started = False
_worker_lock = threading.Lock()

def ensure_background_worker_running():
    global _worker_started
    if not _worker_started:
        with _worker_lock:
            if not _worker_started:
                t = threading.Thread(target=background_worker, daemon=True)
                t.start()
                _worker_started = True

@app.before_request
def before_request_func():
    # 當任何 HTTP 請求（例如 Render 健康檢查或 cron-job.org Ping）進來時，自動安全啟動背景爬蟲
    ensure_background_worker_running()

@app.route("/")
def home():
    return "BTC OKX Fetcher is running 24/7! 🚀"

@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "healthy"}), 200

SUPABASE_DB_URL = "postgresql://postgres.ygzeqvcyqtvhvjrxhzdv:%4010233960527Ken@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres"

def get_db_connection():
    return psycopg2.connect(SUPABASE_DB_URL)

def fetch_single_timeframe(tf):
    exchange = ccxt.okx({'enableRateLimit': True})
    symbol = 'BTC/USDT:USDT'
    
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT MAX(open_time) FROM btc_contract_klines WHERE timeframe = %s",
                (tf,)
            )
            result = cursor.fetchone()
            last_open_time = result[0] if result and result[0] is not None else None

            params = {'symbol': symbol, 'timeframe': tf, 'limit': 10}
            if last_open_time:
                params['since'] = last_open_time

            try:
                ohlcv = exchange.fetch_ohlcv(**params)
            except Exception as e:
                print(f"[BTC-{tf}] OKX API 抓取失敗: {e}")
                return

            if not ohlcv:
                return

            batch_data = [
                (tf, candle[0], candle[1], candle[2], candle[3], candle[4], candle[5])
                for candle in ohlcv
            ]

            if batch_data:
                sql = """
                    INSERT INTO btc_contract_klines 
                    (timeframe, open_time, open_price, high_price, low_price, close_price, volume)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (timeframe, open_time) DO UPDATE SET 
                    high_price = GREATEST(btc_contract_klines.high_price, EXCLUDED.high_price),
                    low_price = LEAST(btc_contract_klines.low_price, EXCLUDED.low_price),
                    close_price = EXCLUDED.close_price,
                    volume = EXCLUDED.volume;
                """
                cursor.executemany(sql, batch_data)
                connection.commit()
                latest_close = batch_data[-1][4]
                latest_vol = batch_data[-1][5]
                print(f" -> [BTC-{tf}] 即時同步成功 | 最新收盤價: {latest_close:,.2f} | 成交量: {latest_vol:.4f}")

    except Exception as e:
        connection.rollback()
        print(f" -> [BTC-{tf}] 資料庫寫入錯誤: {e}")
    finally:
        connection.close()

def fetch_and_save_btc_contracts():
    timeframes = ['5m', '15m', '1h', '4h']
    with ThreadPoolExecutor(max_workers=len(timeframes)) as executor:
        futures = [executor.submit(fetch_single_timeframe, tf) for tf in timeframes]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"執行緒發生錯誤: {e}")

def background_worker():
    print("==========================================")
    print(" BTC 雲端高頻即時數據同步背景執行緒已啟動")
    print("==========================================")
    while True:
        try:
            fetch_and_save_btc_contracts()
            time.sleep(2)
        except Exception as e:
            print(f"背景迴圈錯誤: {e}")
            time.sleep(0.5)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
