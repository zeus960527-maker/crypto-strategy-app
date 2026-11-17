import ccxt
import psycopg2
import time
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

# 📌 請務必將下方 url 中的專案代碼換成你 Supabase 真正的 Reference ID，並把密碼中的 @ 改為 %40
SUPABASE_DB_URL = "postgresql://postgres.ygzeqvcyqtvhvjrxhzdv:%4010233960527Ken@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres"

# 建立 Supabase 資料庫連線 helper
def get_db_connection():
    return psycopg2.connect(SUPABASE_DB_URL)

def fetch_single_timeframe_realtime(tf):
    """ 
    高頻即時同步：
    - 針對指定週期（5m, 15m, 1h, 4h）
    - 每次直接抓取 OKX 最近的幾筆 K 棒（例如 limit=10），確保能夠覆蓋並更新當前正在跳動的最新一根 K 棒
    - 透過資料庫的 ON CONFLICT 即時覆蓋更新高、低、收、量
    """
    exchange = ccxt.okx({'enableRateLimit': True})
    symbol = 'ETH/USDT:USDT'  # ETH 合約交易對
    
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # 抓取最近的 15 筆最新資料（包含正在進行中的當前 K 棒與前幾根歷史 K 棒）
            params = {'symbol': symbol, 'timeframe': tf, 'limit': 15}
            
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

            # 即時批次寫入 / 覆蓋更新 Supabase (PostgreSQL 語法)
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
            
            latest_close = batch_data[-1][4]
            latest_vol = batch_data[-1][6]
            print(f"[ETH-{tf}] 即時同步成功 | 最新收盤價: {latest_close:,.2f} | 成交量: {latest_vol:.4f}")

    except Exception as e:
        connection.rollback()
        print(f"[ETH-{tf}] 資料庫寫入錯誤: {e}")
    finally:
        connection.close()

def run_realtime_sync_loop():
    """ 使用 ThreadPoolExecutor 平行高頻同步 5m, 15m, 1h, 4h 四個週期 """
    timeframes = ['5m', '15m', '1h', '4h']
    
    with ThreadPoolExecutor(max_workers=len(timeframes)) as executor:
        futures = [executor.submit(fetch_single_timeframe_realtime, tf) for tf in timeframes]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"執行緒發生錯誤: {e}")

if __name__ == "__main__":
    print("==========================================")
    print(" ETH 多週期【高頻即時】數據同步控制台 (Supabase)")
    print("==========================================")
    print("【狀態】已啟動高頻監控，一有新價格將立即寫入資料庫...")

    # 主迴圈：以極短間隔（例如每 2 秒）持續向 OKX 抓取最新價格並寫入 Supabase
    try:
        while True:
            start_time = time.time()
            
            # 執行一輪多執行緒即時同步
            run_realtime_sync_loop()
            
            # 計算執行耗費的時間，控制頻率（例如每 3 秒跑一次，兼顧 API 頻率限制與即時性）
            elapsed = time.time() - start_time
            sleep_time = max(1.0, 3.0 - elapsed)  # 至少休眠 1 秒，目標維持 3 秒一輪
            time.sleep(sleep_time)
            
    except KeyboardInterrupt:
        print("【狀態】程式手動終止。")
        sys.exit(0)
