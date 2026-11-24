import time
import threading
import ccxt
import psycopg2
import streamlit as st

st.title("ETH 數據自動同步系統")
st.write("狀態：背景自動同步與歷史補齊執行中 🚀")

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
    若 is_backfill 為 True，代表是啟動時的歷史資料補齊，抓取量可以稍微拉大（例如 limit=100），
    若是即時同步，limit=10 即可。
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

            # 補齊歷史時可以抓多一點筆數（例如 100 根），即時同步抓 10 根即可
            limit_count = 100 if is_backfill else 10
            params = {'symbol': symbol, 'timeframe': tf, 'limit': limit_count}
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
                mode_str = "歷史補齊" if is_backfill else "即時同步"
                print(f" -> [ETH-{tf}] {mode_str}成功")

    except Exception as e:
        connection.rollback()
        print(f" -> [ETH-{tf}] 資料庫寫入錯誤: {e}")
    finally:
        connection.close()

def background_scanner():
    """背景執行緒：開機先補齊資料，之後進入 60 秒循環即時同步"""
    timeframes = ['5m', '15m', '1h', '4h']
    
    # 1. 程式啟動時，先執行一次「缺失資料補齊」
    print("=== 開始執行啟動前的歷史資料補齊 ===")
    try:
        for tf in timeframes:
            fetch_and_fill_timeframe(tf, is_backfill=True)
        print("=== 歷史資料補齊完成，開始進入即時監控迴圈 ===")
    except Exception as e:
        print(f"初始化補齊資料錯誤: {e}")

    # 2. 進入無限迴圈，每 60 秒跑一次即時同步
    while True:
        try:
            for tf in timeframes:
                fetch_and_fill_timeframe(tf, is_backfill=False)
        except Exception as e:
            print(f"掃描執行錯誤: {e}")
        time.sleep(60)

# 使用 st.cache_resource 確保背景執行緒只會啟動一次
@st.cache_resource
def start_background_task():
    thread = threading.Thread(target=background_scanner, daemon=True)
    thread.start()
    return "Started"

# 啟動背景任務
start_background_task()
