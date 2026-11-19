import ccxt
import psycopg2
import time
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

SUPABASE_DB_URL = "postgresql://postgres.ygzeqvcyqtvhvjrxhzdv:%4010233960527Ken@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres"

def get_db_connection():
    return psycopg2.connect(
        SUPABASE_DB_URL,
        connect_timeout=10,
        options="-c statement_timeout=15000"
    )

def catch_up_historical_data(tf):
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

            all_data_to_insert = []
            current_since = last_open_time
            
            while True:
                params = {'symbol': symbol, 'timeframe': tf, 'limit': 300}
                if current_since:
                    params['since'] = current_since
                
                try:
                    ohlcv = exchange.fetch_ohlcv(**params)
                except Exception as e:
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
                print(f"  -> ETH 週期 [{tf}] 歷史補齊成功！共補入 {len(all_data_to_insert)} 筆")

    except Exception as e:
        connection.rollback()
        print(f"  -> ETH 週期 [{tf}] 歷史補齊錯誤: {e}")
    finally:
        connection.close()

def fetch_single_timeframe_realtime(tf):
    exchange = ccxt.okx({'enableRateLimit': True})
    symbol = 'ETH/USDT:USDT'
    
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            params = {'symbol': symbol, 'timeframe': tf, 'limit': 15}
            ohlcv = exchange.fetch_ohlcv(**params)
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
            print(f"[ETH-{tf}] 即時同步成功 | 最新收盤價: {latest_close:,.2f}")

    except Exception as e:
        connection.rollback()
        print(f"[ETH-{tf}] 即時寫入錯誤: {e}")
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
                print(f"執行緒發生錯誤: {e}")

if __name__ == "__main__":
    print("==========================================")
    print(" ETH 純背景爬蟲 Worker 啟動")
    print("==========================================")
    run_sync_task(catch_up_historical_data)

    while True:
        try:
            start_time = time.time()
            run_sync_task(fetch_single_timeframe_realtime)
            elapsed = time.time() - start_time
            time.sleep(max(1.0, 3.0 - elapsed))
        except Exception as e:
            print(f"背景迴圈錯誤: {e}")
            time.sleep(5)
