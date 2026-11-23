# okx_fetcher.py
import os
import ccxt
import psycopg2
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import streamlit as st
import schedule

# 設定頁面空白或僅顯示簡單提示
st.set_page_config(page_title="OKX Background Worker", layout="centered")
st.title("🔄 OKX 數據同步背景服務運行中")
st.write("此服務正在背景 24/7 自動回補與定時抓取 OKX K 棒數據並寫入 Supabase。")

SUPABASE_DB_URL = "postgresql://postgres.ygzeqvcyqtvhvjrxhzdv:%4010233960527Ken@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres"

def get_db_connection():
    return psycopg2.connect(
        SUPABASE_DB_URL,
        connect_timeout=10,
        options="-c statement_timeout=15000"
    )

# 💡 啟動時自動補齊歷史資料
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

# 💡 定時抓取最新資料的任務
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
                print(f" -> [BTC-{tf}] Cron 定時同步成功 | 最新收盤價: {latest_close:,.2f}")

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

def scheduled_job():
    """由 Cron Job 觸發的排程任務"""
    try:
        print("⏰ [Cron Job] 開始執行定時抓取任務...")
        run_sync_task(fetch_single_timeframe_realtime)
    except Exception as e:
        print(f"Cron Job 執行錯誤: {e}")

def background_worker():
    print("==========================================")
    print(" BTC 雲端定時排程（Cron）背景執行緒已啟動")
    print("==========================================")
    
    # 💡 啟動時先執行一次歷史斷點補齊
    print("🔄 正在檢查並補齊歷史 K 棒...")
    run_sync_task(catch_up_historical_data)
    print("🚀 歷史回補完畢，正式註冊 Cron 定時排程！")

    # 💡 設定 Cron 排程規則（例如：每隔 1 分鐘執行一次，你也可以改為 schedule.every(5).minutes）
    schedule.every(1).minutes.do(scheduled_job)
    
    # 也可以設定特定時間（例如每天早上 8 點執行）：
    # schedule.every().day.at("08:00").do(scheduled_job)

    # 持續檢查並執行排程
    while True:
        schedule.run_pending()
        time.sleep(1)

# 利用 Streamlit 的 cache_resource 確保背景排程執行緒只會啟動一次
@st.cache_resource
def start_background_service():
    t = threading.Thread(target=background_worker, daemon=True)
    t.start()
    return "Started"

# 自動啟動背景服務
start_background_service()
