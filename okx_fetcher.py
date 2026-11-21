import os
import ccxt
import psycopg2
import time
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, jsonify

app = Flask(__name__)

# 用來確保 BTC 背景執行緒只會被安全啟動一次的鎖與旗標
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
    ensure_background_worker_running()

@app.route("/")
def home():
    return "BTC OKX Fetcher is running 24/7! 🚀"

@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "healthy"}), 200

@app.route("/run-scanner", methods=["GET", "POST"])
def run_scanner():
    return jsonify({"status": "success", "message": "BTC Ping received, background worker is active!"}), 200

SUPABASE_DB_URL = "postgresql://postgres.ygzeqvcyqtvhvjrxhzdv:%4010233960527Ken@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres"

def get_db_connection():
    return psycopg2.connect(
        SUPABASE_DB_URL,
        connect_timeout=10,
        options="-c statement_timeout=15000"
    )

# 💡 啟動時自動補齊歷史資料（從資料庫最新時間開始接續往後抓，直到追到最新）
def catch_up_historical_data(tf):
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

            all_data_to_insert = []
            current_since = last_open_time

            print(f" -> [BTC-{tf}] 開始檢查歷史斷點回補，起始時間點: {current_since}")

            while True:
                params = {'symbol': symbol, 'timeframe': tf, 'limit': 300}
                if current_since:
                    params['since'] = current_since
                
                try:
                    ohlcv = exchange.fetch_ohlcv(**params)
                except Exception as e:
                    print(f"[BTC-{tf}] 歷史回補 API 抓取錯誤: {e}")
                    break

                if not ohlcv:
                    break

                batch_data = [
                    (tf, candle[0], candle[1], candle[2], candle[3], candle[4], candle[5])
                    for candle in ohlcv
                ]

                # 避免重複抓到邊界的那一根
                if current_since and all_data_to_insert and batch_data[0][1] == all_data_to_insert[-1][1]:
                    batch_data = batch_data[1:]

                if not batch_data:
                    break

                all_data_to_insert.extend(batch_data)
                
                # 如果抓回來的數量小於 limit，代表已經追到交易所最新資料
                if len(ohlcv) < params['limit']:
                    break
                
                next_since = ohlcv[-1][0] + 1
                if current_since and next_since <= current_since:
                    break
                current_since = next_since
                
                # 避免請求過快觸發交易所限制
                time.sleep(0.2)

            if all_data_to_insert:
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
                cursor.executemany(sql, all_data_to_insert)
                connection.commit()
                print(f" ✅ [BTC-{tf}] 歷史斷點補齊完成！共補入 {len(all_data_to_insert)} 筆資料。")

    except Exception as e:
        connection.rollback()
        print(f" ❌ [BTC-{tf}] 歷史回補資料庫錯誤: {e}")
    finally:
        connection.close()

# 💡 即時高頻抓取最新 10 根 K 棒
def fetch_single_timeframe_realtime(tf):
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
                print(f" -> [BTC-{tf}] 即時同步成功 | 最新收盤價: {latest_close:,.2f}")

    except Exception as e:
        connection.rollback()
    finally:
        connection.close()

def run_sync_task(task_func):
    timeframes = ['5m', '15m', '1h', '4h']
    with ThreadPoolExecutor(max_workers=len(timeframes)) as executor:
        futures = [executor.submit(task_func, tf) for tf in timeframes]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                pass

def background_worker():
    print("==========================================")
    print(" BTC 雲端高頻即時數據同步背景執行緒已啟動")
    print("==========================================")
    
    # 💡 步驟 1：每次啟動時，先執行歷史斷點補齊
    print("🔄 正在檢查並補齊自上次關閉以來遺漏的歷史 K 棒...")
    run_sync_task(catch_up_historical_data)
    print("🚀 歷史回補完畢，正式進入即時高頻監控迴圈！")

    # 💡 步驟 2：進入無限迴圈，每幾秒同步最新即時資料
    while True:
        try:
            start_time = time.time()
            run_sync_task(fetch_single_timeframe_realtime)
            elapsed = time.time() - start_time
            sleep_time = max(1.0, 3.0 - elapsed)
            time.sleep(sleep_time)
        except Exception as e:
            print(f"背景迴圈錯誤: {e}")
            time.sleep(5)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
