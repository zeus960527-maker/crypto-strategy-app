import ccxt
import psycopg2
import time
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

# Supabase 雲端資料庫連線字串（請確認密碼中的 @ 是否已改為 %40）
SUPABASE_DB_URL = "postgresql://postgres.ygzeqvcyqtvhvjrxhzdv:%4010233960527Ken@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres"

# 建立 Supabase 資料庫連線 helper
def get_db_connection():
    return psycopg2.connect(SUPABASE_DB_URL)

def fetch_single_timeframe(tf):
    """ 高頻即時抓取與寫入：專注於同步最新幾根 K 棒（5m, 15m, 1h, 4h） """
    # 啟用 CCXT 內建 Rate Limit 控制
    exchange = ccxt.okx({'enableRateLimit': True})
    symbol = 'BTC/USDT:USDT'
    
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # 1. 查詢 Supabase 中該週期目前最新的 open_time
            cursor.execute(
                "SELECT MAX(open_time) FROM btc_contract_klines WHERE timeframe = %s",
                (tf,)
            )
            result = cursor.fetchone()
            last_open_time = result[0] if result and result[0] is not None else None

            # 2. 高頻即時模式：我們只需要抓取最近的 10 筆資料來更新當前 K 棒與最新價格
            params = {'symbol': symbol, 'timeframe': tf, 'limit': 10}
            if last_open_time:
                # 從資料庫最新一根 K 棒的時間開始抓，確保不會漏掉正在進行中的 K 棒
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

            # 3. 立即批次寫入或更新 Supabase (PostgreSQL 語法)
            # 透過 ON CONFLICT 確保如果當前 K 棒價位變動（成交價、高低點更新），會立即覆蓋最新數據
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
    """ 使用 ThreadPoolExecutor 平行高頻抓取 5m, 15m, 1h, 4h 四個週期 """
    timeframes = ['5m', '15m', '1h', '4h']
    
    with ThreadPoolExecutor(max_workers=len(timeframes)) as executor:
        futures = [executor.submit(fetch_single_timeframe, tf) for tf in timeframes]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"執行緒發生錯誤: {e}")

if __name__ == "__main__":
    print("==========================================")
    print(" BTC 雲端高頻即時數據同步程式 (Supabase)")
    print("==========================================")

    # 主迴圈：每隔 2 秒執行一次高頻抓取，一有新價格或 K 棒變動立刻寫入資料庫
    try:
        while True:
            fetch_and_save_btc_contracts()
            time.sleep(2)  # 每 2 秒請求一次 OKX 並同步寫入 Supabase，兼顧即時性與防封鎖
    except KeyboardInterrupt:
        print("【狀態】程式已安全終止。")
        sys.exit(0)
