import ccxt
import psycopg2
import schedule
import time
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

# 📌 請務必將下方 url 中的專案代碼換成你 Supabase 真正的 Reference ID，並把密碼中的 @ 改為 %40
SUPABASE_DB_URL = "postgresql://postgres.ygzeqvcyqtvhvjrxhzdv:%4010233960527Ken@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres"

# 建立 Supabase 資料庫連線 helper
def get_db_connection():
    return psycopg2.connect(SUPABASE_DB_URL)

def fetch_single_timeframe(tf):
    """ 處理指定週期的完整增量抓取與分頁補齊 (涵蓋 5m, 15m, 1h, 4h) """
    # 啟用 CCXT 內建 Rate Limit 控制，避免觸發交易所封鎖
    exchange = ccxt.okx({'enableRateLimit': True})
    symbol = 'ETH/USDT:USDT'  # ETH 合約交易對
    
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # 1. 查詢 Supabase 中該週期目前最新的 open_time
            cursor.execute(
                "SELECT MAX(open_time) FROM eth_contract_klines WHERE timeframe = %s",
                (tf,)
            )
            result = cursor.fetchone()
            last_open_time = result[0] if result and result[0] is not None else None

            all_data_to_insert = []
            current_since = last_open_time
            
            if current_since:
                print(f"[ETH-{tf}] 開始增量同步，自時間戳 {current_since} 起...")
            else:
                print(f"[ETH-{tf}] 首次同步，正在從 OKX 抓取 [{tf}] 歷史資料...")

            # 2. 分頁迴圈（Pagination loop）確保大量歷史資料一次補滿
            while True:
                params = {'symbol': symbol, 'timeframe': tf, 'limit': 300}
                if current_since:
                    params['since'] = current_since
                
                try:
                    ohlcv = exchange.fetch_ohlcv(**params)
                except Exception as e:
                    print(f"[ETH-{tf}] OKX API 抓取失敗: {e}")
                    break

                if not ohlcv:
                    break

                batch_data = [
                    (tf, candle[0], candle[1], candle[2], candle[3], candle[4], candle[5])
                    for candle in ohlcv
                ]

                # 避免重複寫入邊界資料
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

            # 3. 批次寫入 Supabase (PostgreSQL 語法)
            if all_data_to_insert:
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
                cursor.executemany(sql, all_data_to_insert)
                connection.commit()
                latest_volume = all_data_to_insert[-1][6]
                print(f"  -> ETH 週期 [{tf}] 成功同步 {len(all_data_to_insert)} 筆資料 | 最新成交量: {latest_volume:.4f}")
            else:
                print(f"  -> ETH 週期 [{tf}] 目前沒有新資料需要同步。")

    except Exception as e:
        connection.rollback()
        print(f"  -> ETH 週期 [{tf}] 資料庫寫入或處理錯誤: {e}")
    finally:
        connection.close()

def fetch_and_save_eth_contracts():
    """ 使用 ThreadPoolExecutor 平行抓取 5m, 15m, 1h, 4h 四個週期 """
    print("--- 開始 ETH 多週期同步作業 (5m, 15m, 1h, 4h) ---")
    timeframes = ['5m', '15m', '1h', '4h']
    
    with ThreadPoolExecutor(max_workers=len(timeframes)) as executor:
        futures = [executor.submit(fetch_single_timeframe, tf) for tf in timeframes]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"執行緒發生錯誤: {e}")
                
    print("--- ETH 本輪同步作業結束 ---")

if __name__ == "__main__":
    print("==========================================")
    print(" ETH 4週期雲端數據同步控制台 (Supabase 雲端自動化執行)")
    print("==========================================")

    # 1. 程式啟動時立即執行一次同步
    fetch_and_save_eth_contracts()

    # 2. 設定定時排程（例如每 60 秒執行一次，或配合你的需求調整）
    schedule.every(60).seconds.do(fetch_and_save_eth_contracts)

    # 3. 主迴圈持續維持背景運行
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        print("【狀態】程式手動終止。")
        sys.exit(0)
