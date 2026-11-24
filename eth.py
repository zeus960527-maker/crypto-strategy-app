import time
import ccxt
import psycopg2
import streamlit as st
from datetime import datetime

st.set_page_config(page_title="ETH 數據自動同步系統", layout="centered")

st.title("ETH 數據自動同步系統")
st.write("狀態：前端即時輪詢與自動同步執行中 🚀")

SUPABASE_DB_URL = "postgresql://postgres.ygzeqvcyqtvhvjrxhzdv:%4010233960527Ken@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres"

def get_db_connection():
    return psycopg2.connect(
        SUPABASE_DB_URL,
        connect_timeout=10,
        options="-c statement_timeout=15000"
    )

def fetch_and_fill_timeframe(tf, is_backfill=False):
    """
    抓取並寫入資料。
    若 is_backfill為 True，抓取 100 筆（歷史補齊）；否則抓取 10 筆（即時同步）。
    """
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

            limit_count = 100 if is_backfill else 10
            params = {'symbol': symbol, 'timeframe': tf, 'limit': limit_count}
            if last_open_time:
                params['since'] = last_open_time

            try:
                ohlcv = exchange.fetch_ohlcv(**params)
            except Exception as e:
                print(f"[ETH-{tf}] OKX API 抓取失敗: {e}")
                return False

            if not ohlcv:
                return False

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
                mode_str = "歷史補齊" if is_backfill else "即時同步"
                print(f" -> [ETH-{tf}] {mode_str}成功")
                return True

    except Exception as e:
        connection.rollback()
        print(f" -> [ETH-{tf}] 資料庫寫入錯誤: {e}")
    finally:
        connection.close()
    return False

# 1. 確保每次應用程式啟動或重新整理時，一定先執行一次歷史資料補齊
@st.cache_resource
def run_initial_backfill():
    timeframes = ['5m', '15m', '1h', '4h']
    print("=== 開始執行啟動前的歷史資料補齊 ===")
    try:
        for tf in timeframes:
            fetch_and_fill_timeframe(tf, is_backfill=True)
        print("=== 歷史資料補齊完成 ===")
    except Exception as e:
        print(f"初始化補齊資料錯誤: {e}")
    return True

# 執行初始化補齊
run_initial_backfill()

# 2. 顯示最後更新時間與狀態提示
status_placeholder = st.empty()
log_placeholder = st.empty()

timeframes = ['5m', '15m', '1h', '4h']

# 執行即時同步
status_placeholder.info(f"🔄 正在同步最新 K 線資料... ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
try:
    for tf in timeframes:
        fetch_and_fill_timeframe(tf, is_backfill=False)
    status_placeholder.success(f"✅ 同步完成！下次更新倒數中... ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
except Exception as e:
    status_placeholder.error(f"❌ 同步發生錯誤: {e}")

# 3. 自動循環機制：等待 60 秒後自動重新整理畫面並再次執行同步
time.sleep(0.5)
st.rerun()
