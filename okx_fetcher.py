# btc_fetcher.py
import os
import ccxt
import psycopg2
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import streamlit as st
from flask import Flask, jsonify

# 1. 設定 Streamlit 頁面（保持簡潔，不顯示多餘網頁畫面）
st.set_page_config(page_title="BTC Background Worker", layout="centered")
st.title("🔄 BTC 數據同步背景服務運行中")
st.write("此服務正在背景 24/7 自動回補與抓取 OKX BTC K 棒數據並寫入 Supabase。")

SUPABASE_DB_URL = "postgresql://postgres.ygzeqvcyqtvhvjrxhzdv:%4010233960527Ken@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres"

def get_db_connection():
    return psycopg2.connect(
        SUPABASE_DB_URL,
        connect_timeout=10,
        options="-c statement_timeout=15000"
    )

# 💡 歷史資料回補
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

                if current_since and all_data_to_insert and batch_data[0][1] == all_data_to_insert[-1][1]:
                    batch_data = batch_data[1:]

                if not batch_data:
                    break

                all_data_to_insert.extend(batch_data)
                
                if len(ohlcv) < params['limit']:
                    break
                
                next_since = ohlcv[-1][0] + 1
                if current_since and next_since <= current_since:
                    break
                current_since = next_since
                
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
    
    print("🔄 正在檢查並補齊自上次關閉以來遺漏的歷史 K 棒...")
    run_sync_task(catch_up_historical_data)
    print("🚀 歷史回補完畢，正式進入即時高頻監控迴圈！")

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

# 2. 建立輕量 Flask 伺服器專門給 UptimeRobot Ping 用
flask_app = Flask(__name__)

@flask_app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "healthy", "service": "BTC Fetcher is running 24/7"}), 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port, use_reloader=False)

# 3. 利用 Streamlit 的 cache_resource 同時啟動「爬蟲執行緒」與「Flask 監聽執行緒」
@st.cache_resource
def start_services():
    # 啟動爬蟲背景執行緒
    t_worker = threading.Thread(target=background_worker, daemon=True)
    t_worker.start()
    
    # 啟動 Flask 健康檢查伺服器執行緒（給 UptimeRobot 訪問）
    t_flask = threading.Thread(target=run_flask, daemon=True)
    t_flask.start()
    
    return "Started"

start_services()
