import pymysql
import psycopg2

# 1. 你的 MySQL 連線設定
mysql_config = {
    'host': 'localhost',
    'user': 'root',
    'password': '10233',  # 請確認這是否為你的 MySQL 密碼
    'database': 'crypto_db',
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.Cursor
}

# 2. Supabase 雲端資料庫連線字串（使用 Session Pooler 連接埠 6543）
SUPABASE_DB_URL = "postgresql://postgres.ygzeqvcyqtvhvjrxhzdv:%4010233960527Ken@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres"

def migrate_data():
    print("正在連接 MySQL...")
    mysql_conn = pymysql.connect(**mysql_config)
    
    print("正在連接 Supabase...")
    pg_conn = psycopg2.connect(SUPABASE_DB_URL)
    
    try:
        with mysql_conn.cursor() as mysql_cursor, pg_conn.cursor() as pg_cursor:
            print("從 MySQL 讀取 btc_contract_klines 所有資料...")
            mysql_cursor.execute("SELECT timeframe, open_time, open_price, high_price, low_price, close_price, volume FROM btc_contract_klines")
            rows = mysql_cursor.fetchall()
            
            total_rows = len(rows)
            print(f"共讀取到 {total_rows} 筆資料，開始上傳至 Supabase...")
            
            if total_rows > 0:
                # 使用 PostgreSQL 的批量 UPSERT 語法
                sql = """
                    INSERT INTO btc_contract_klines 
                    (timeframe, open_time, open_price, high_price, low_price, close_price, volume)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (timeframe, open_time) DO NOTHING;
                """
                
                # 批次分段上傳（每 5000 筆為一批）
                batch_size = 5000
                for i in range(0, total_rows, batch_size):
                    batch = rows[i:i + batch_size]
                    pg_cursor.executemany(sql, batch)
                    pg_conn.commit()
                    print(f"已成功上傳 {min(i + batch_size, total_rows)} / {total_rows} 筆...")
                    
        print("BTC 資料遷移全部完成！")
        
    except Exception as e:
        pg_conn.rollback()
        print(f"遷移過程發生錯誤: {e}")
    finally:
        mysql_conn.close()
        pg_conn.close()

if __name__ == "__main__":
    migrate_data()