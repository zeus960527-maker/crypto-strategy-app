import time
import ccxt
import psycopg2
import streamlit as st
from apscheduler.schedulers.background import BackgroundScheduler

# 網頁端顯示簡單的運行狀態
st.title("ETH 數據自動同步系統")
st.write("狀態：內建 Cron 執行緒排程中 🚀")

SUPABASE_DB_URL = "postgresql://postgres.ygzeqvcyqtvhvjrxhzdv:%4010233960527Ken@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres"

def get_db_connection():
    return psycopg2.connect(
        SUPABASE_DB_URL,
        connect_timeout=10,
        options="-c statement_timeout=15000"
    )

def fetch_single_timeframe(tf):
    exchange = ccxt.okx({'enableRateLimit': True})
    symbol = 'ETH/USDT:USDT'
    
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT MAX(open_time) FROM eth_contract_klines WHERE timeframe = %s",
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
                print(f"[ETH-{tf}] OKX API 抓取失敗: {e}")
                return

            if not ohlcv:
                return

            batch_data = [
                (tf, candle[0], candle[1], candle[2], candle[3], candle[4], candle[5])
                for candle in ohlcv
            ]

            if batch_data:
                sql = """
                    INSERT INTO eth_contract_klines 
                    (timeframe, open_time, open_price, high_price, low_price, close_price, volume)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (timeframe, open_time) DO UPDATE SET 
                    high_price = GREATEST(eth_contract_klines.high_price, EXCLUDED.high_price),
                    low_price = LEAST(eth_contract_klines.low_price, EXCLUDED.low_price),
                    close_price = EXCLUDED.close_price,
                    volume = EXCLUDED.volume;
                """
                cursor.executemany(sql, batch_data)
                connection.commit()
                print(f" -> [ETH-{tf}] 即時同步成功")

    except Exception as e:
        connection.rollback()
        print(f" -> [ETH-{tf}] 資料庫寫入錯誤: {e}")
    finally:
        connection.close()

def run_scanner():
    """每次排程觸發時執行的動作"""
    try:
        timeframes = ['5m', '15m', '1h', '4h']
        for tf in timeframes:
            fetch_single_timeframe(tf)
    except Exception as e:
        print(f"排程執行錯誤: {e}")

# 使用 st.cache_resource 確保排程器只初始化一次，避免重複建立
@st.cache_resource
def init_scheduler():
    scheduler = BackgroundScheduler()
    # 設定每 1 分鐘執行一次 run_scanner（可根據需求調整秒數或改用其他 cron 表達式）
    scheduler.add_job(run_scanner, 'interval', minutes=1)
    scheduler.start()
    return scheduler

# 啟動排程器
init_scheduler()
