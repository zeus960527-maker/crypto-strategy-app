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

def catch_up_historical_data(tf):
    """ 
    【啟動補齊函式】
    檢查 Supabase 中該週期最新的 open_time，自動分頁補齊至當前最新資料，
    確保重新執行時不會遺漏中斷期間的歷史 K 棒。
    """
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
                print(f"[ETH-{tf}] 檢測到歷史斷點，開始補齊自時間戳 {current_since} 起的資料...")
            else:
                print(f"[ETH-{tf}] 首次同步，正在從 OKX 完整抓取 [{tf}] 歷史資料...")

            # 2. 分頁迴圈（Pagination loop）補滿中斷的歷史資料
            while True:
                params = {'symbol': symbol, 'timeframe': tf, 'limit': 300}
                if current_since:
                    params['since'] = current_since
                
                try:
                    ohlcv = exchange.fetch_ohlcv(**params)
                except Exception as e:
                    print(f"[ETH-{tf}] 歷史補齊 OKX API 抓取失敗: {e}")
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

            # 3. 批次寫入 Supabase
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
                print(f"  -> ETH 週期 [{tf}] 歷史資料補齊成功！共補入 {len(all_data_to_insert)} 筆")
            else:
                print(f"  -> ETH 週期 [{tf}] 歷史資料已是最新，無需補齊。")

    except Exception as e:
        connection.rollback()
        print(f"  -> ETH 週期 [{tf}] 歷史補齊寫入錯誤: {e}")
    finally:
        connection.close()

def fetch_single_timeframe_realtime(tf):
    """ 
    【高頻即時同步】
    針對指定週期（5m, 15m, 1h, 4h）抓取最近 15 筆 K 棒，
    即時覆蓋更新當前正在跳動的最新一根 K 棒。
    """
    exchange = ccxt.okx({'enableRateLimit': True})
    symbol = 'ETH/USDT:USDT'
    
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            params = {'symbol': symbol, 'timeframe': tf, 'limit': 15}
            
            try:
                ohlcv = exchange.fetch_ohlcv(**params)
            except Exception as e:
                print(f"[ETH-{tf}] 即時同步 OKX API 抓取失敗: {e}")
                return

            if not ohlcv:
                return

            batch_data = [
                (tf, candle[0], candle[1], candle[2], candle[3], candle[4], candle[5])
                for candle in ohlcv
            ]

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
        print(f"[ETH-{tf}] 即時寫入錯誤: {e}")
    finally:
        connection.close()

def run_sync_task(task_func):
    """ 使用 ThreadPoolExecutor 平行執行 5m, 15m, 1h, 4h 四個週期 """
    timeframes = ['5m', '15m', '1h', '4h']
    
    with ThreadPoolExecutor(max_workers=len(timeframes)) as executor:
        futures = [executor.submit(task_func, tf) for tf in timeframes]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"執行緒發生錯誤: {e}")

if __name__ == "__main__":
    print("==========================================")
    print(" ETH 多週期【啟動補齊 + 高頻即時】同步控制台")
    print("==========================================")

    # 1. 【第一步】程式重新啟動時，先自動把上次中斷到最新的歷史資料全數補齊
    print("【狀態】正在檢查並補齊歷史數據...")
    run_sync_task(catch_up_historical_data)
    print("【狀態】歷史數據補齊完畢！正式進入高頻即時監控...")

    # 2. 【第二步】進入常駐的高頻即時迴圈，每 3 秒更新一次最新價格與 K 棒
    try:
        while True:
            start_time = time.time()
            
            # 執行一輪多執行緒即時同步
            run_sync_task(fetch_single_timeframe_realtime)
            
            # 控制頻率：維持大約 3 秒一輪，兼顧效能與 API 限制
            elapsed = time.time() - start_time
            sleep_time = max(1.0, 3.0 - elapsed)
            time.sleep(sleep_time)
            
    except KeyboardInterrupt:
        print("【狀態】程式手動終止。")
        sys.exit(0)
