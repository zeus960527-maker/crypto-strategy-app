import ccxt
import psycopg2
import schedule
import time
import threading
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

# 控制狀態全域變數
is_running = False
is_paused = False
program_active = True

# 📌 請務必將下方 url 中的專案代碼換成你 Supabase 真正的 Reference ID，並把密碼中的 @ 改為 %40
SUPABASE_DB_URL = "postgresql://postgres.ygzeqvcyqtvhvjrxhzdv:%4010233960527Ken@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres"

# 建立 Supabase 資料庫連線 helper
def get_db_connection():
    return psycopg2.connect(SUPABASE_DB_URL)

def fetch_single_timeframe(tf):
    """ 處理指定週期的完整增量抓取與分頁補齊 (涵蓋 5m, 15m, 1h, 4h) """
    global is_running, is_paused
    if not is_running or is_paused:
        return

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
            while is_running and not is_paused:
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
    global is_running, is_paused
    if not is_running or is_paused:
        return

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

def keyboard_listener():
    """ 獨立執行緒監聽鍵盤輸入 """
    global is_running, is_paused, program_active
    
    print("==========================================")
    print(" ETH 4週期雲端數據同步控制台 (Supabase)")
    print(" 指令控制說明：")
    print("  [ENTER] 開始執行抓取")
    print("  [S]     暫停抓取")
    print("  [Q]     結束程式")
    print("==========================================")
    
    while program_active:
        user_input = input().strip().upper()
        
        if user_input == '': # 按下 ENTER
            if not is_running:
                is_running = True
                is_paused = False
                print("【狀態】ETH 程式已開始執行！")
            elif is_paused:
                is_paused = False
                print("【狀態】ETH 程式已恢復執行！")
            else:
                print("【提示】程式已經在執行中。")
                
        elif user_input == 'S': # 按下 S
            if is_running and not is_paused:
                is_paused = True
                print("【狀態】程式已【暫停】（按下 Enter 可恢復）。")
            else:
                print("【提示】目前並未在執行中。")
                
        elif user_input == 'Q': # 按下 Q
            is_running = False
            program_active = False
            print("【狀態】正在安全結束程式...")
            sys.exit(0)

if __name__ == "__main__":
    # 啟動鍵盤監聽執行緒
    t = threading.Thread(target=keyboard_listener, daemon=True)
    t.start()

    # 排程設定（每 10 秒檢查一次）
    schedule.every(10).seconds.do(fetch_and_save_eth_contracts)

    # 主迴圈維持程式運行
    while program_active:
        if is_running and not is_paused:
            schedule.run_pending()
        time.sleep(0.5)