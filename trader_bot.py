from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import sys
import ccxt
import numpy as np
import pandas as pd
import psycopg2
import requests

# 📌 你的 Supabase 連線網址
SUPABASE_DB_URL = "postgresql://postgres.ygzeqvcyqtvhvjrxhzdv:%4010233960527Ken@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres"

# 📌 請務必填入你的 Discord Webhook URL
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1571457845772165170/MZt_7HpNi9VD_peLI8ttwP_EXlAD5gAePTNSPff65whlJ-IMdv9qbPRnVy22npwjftI7"

# 記錄上一次發送過信號的時間戳，避免同一根 K 棒重複推播
last_notified_timestamps = {}


def get_db_connection():
  return psycopg2.connect(
      SUPABASE_DB_URL,
      connect_timeout=10,
      options="-c statement_timeout=15000",
  )


def send_discord_alert(symbol, tf, signal_type, price, sl, tp):
  """發送 Discord Webhook 即時通知"""
  if not DISCORD_WEBHOOK_URL or "你的_DISCORD_WEBHOOK_URL" in DISCORD_WEBHOOK_URL:
    print("[警告] 未設定 Discord Webhook URL，無法發送通知！")
    return

  color = 0x0ECB81 if "LONG" in signal_type else 0xF6465D
  emoji = "🟢" if "LONG" in signal_type else "🔴"
  current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

  payload = {
      "embeds": [{
          "title": f"{emoji} 【雙軌策略信號通知 (大小通吃全捕捉版)】",
          "color": color,
          "fields": [
              {
                  "name": "交易對 / 週期",
                  "value": str(f"{symbol}/USDT | 週期: {tf}"),
                  "inline": True,
              },
              {
                  "name": "做單方向",
                  "value": str(f"**{signal_type}**"),
                  "inline": True,
              },
              {
                  "name": "目前時間",
                  "value": str(f"`{current_time}`"),
                  "inline": True,
              },
              {
                  "name": "進場點位 (Entry)",
                  "value": str(f"`{price:,.2f} USDT`"),
                  "inline": True,
              },
              {
                  "name": "建議止損 (SL)",
                  "value": str(f"`{sl:,.2f} USDT`"),
                  "inline": True,
              },
              {
                  "name": "建議止盈 (TP)",
                  "value": str(f"`{tp:,.2f} USDT`"),
                  "inline": True,
              },
          ],
          "footer": {"text": "24/7 自動化交易與策略監控系統"},
      }]
  }

  try:
    response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
    if response.status_code not in [200, 204]:
      print(
          f"[Discord] 推送失敗，狀態碼: {response.status_code}, 回應:"
          f" {response.text}"
      )
    else:
      print(f"[Discord] 成功推送 {symbol} {tf} 訊號！")
  except Exception as e:
    print(f"[Discord] 推送異常: {e}")


def calculate_trendline_channel(df, window=30, num_dev=1.8):
  """計算動態線性回歸趨勢線與通道"""
  close = df["close_price"].values
  n = len(close)
  upper = np.zeros(n)
  lower = np.zeros(n)

  for i in range(n):
    if i < window:
      upper[i] = close[i]
      lower[i] = close[i]
    else:
      y = close[i - window + 1 : i + 1]
      x = np.arange(window)
      slope, intercept = np.polyfit(x, y, 1)
      mid = slope * (window - 1) + intercept
      std = np.std(y - (slope * x + intercept))
      upper[i] = mid + num_dev * std
      lower[i] = mid - num_dev * std

  df["Trend_Resistance"] = upper
  df["Trend_Support"] = lower
  return df


def check_signal_and_notify(symbol, tf, df_tf):
  """檢測多空信號：兼顧大小波動與多空雙向，全面捕捉變盤機會"""
  global last_notified_timestamps
  if df_tf.empty or len(df_tf) < 50:
    return

  # 計算均線 (保留 EMA21 與 EMA50 作為動態參考)
  df_tf["EMA_21"] = df_tf["close_price"].ewm(span=21, adjust=False).mean()
  df_tf["EMA_50"] = df_tf["close_price"].ewm(span=50, adjust=False).mean()

  # 針對不同週期動態調整通道參數 (5m/15m 靈敏抓小波段，1h/4h 穩健抓大行情)
  win = 20 if tf in ["5m", "15m"] else 40
  dev = 1.5 if tf in ["5m", "15m"] else 2.0
  df_tf = calculate_trendline_channel(df_tf, window=win, num_dev=dev)

  # 計算 RSI (14)
  delta = df_tf["close_price"].diff()
  gain = delta.where(delta > 0, 0).rolling(window=14).mean()
  loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
  rs = gain / loss
  df_tf["RSI"] = 100 - (100 / (1 + rs))

  # 計算 ATR (用於動態止損)
  high_low = df_tf["high_price"] - df_tf["low_price"]
  high_close = (df_tf["high_price"] - df_tf["close_price"].shift()).abs()
  low_close = (df_tf["low_price"] - df_tf["close_price"].shift()).abs()
  tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
  df_tf["ATR"] = tr.rolling(window=14).mean()

  # 成交量均線與實體比例
  df_tf["Vol_SMA20"] = df_tf["volume"].rolling(window=20).mean()
  body_size = (df_tf["close_price"] - df_tf["open_price"]).abs()
  candle_range = df_tf["high_price"] - df_tf["low_price"]
  df_tf["Body_Ratio"] = body_size / candle_range.replace(0, 1)

  # ==================== 全方位捕捉策略 (多空皆做、大小通吃) ====================
  if tf in ["5m", "15m"]:
    # 小波段閃電模式：只要突破上下軌，且伴隨基本量能與動能，立即雙向捕捉
    long_cond = (
        (df_tf["close_price"] > df_tf["Trend_Resistance"])
        & (df_tf["RSI"] >= 42)
        & (df_tf["RSI"] <= 85)
        & (df_tf["volume"] > df_tf["Vol_SMA20"] * 1.05)  # 稍微放寬量能，避免漏掉起漲
        & (df_tf["Body_Ratio"] > 0.3)
    )
    short_cond = (
        (df_tf["close_price"] < df_tf["Trend_Support"])
        & (df_tf["RSI"] >= 15)
        & (df_tf["RSI"] <= 58)
        & (df_tf["volume"] > df_tf["Vol_SMA20"] * 1.05)
        & (df_tf["Body_Ratio"] > 0.3)
    )
  else:
    # 大波段趨勢模式 (1h/4h)：捕捉大行情變盤
    long_cond = (
        (df_tf["close_price"] > df_tf["Trend_Resistance"])
        & (df_tf["RSI"] >= 45)
        & (df_tf["RSI"] <= 80)
        & (df_tf["volume"] > df_tf["Vol_SMA20"] * 1.0)
    )
    short_cond = (
        (df_tf["close_price"] < df_tf["Trend_Support"])
        & (df_tf["RSI"] >= 20)
        & (df_tf["RSI"] <= 55)
        & (df_tf["volume"] > df_tf["Vol_SMA20"] * 1.0)
    )

  df_tf["Long_Signal"] = long_cond
  df_tf["Short_Signal"] = short_cond

  latest = df_tf.iloc[-1]
  latest_time = latest["open_time"]
  latest_close = float(latest["close_price"])
  latest_atr = (
      float(latest["ATR"])
      if not pd.isna(latest["ATR"])
      else latest_close * 0.015
  )

  key = f"{symbol}_{tf}"

  # 觸發做多信號
  if latest["Long_Signal"] and last_notified_timestamps.get(key) != latest_time:
    last_notified_timestamps[key] = latest_time

    sl = round(latest_close - (latest_atr * 1.5), 2)
    risk = latest_close - sl
    tp = round(latest_close + (risk * 2.5), 2)

    send_discord_alert(symbol, tf, "LONG (做多-全面捕捉)", latest_close, sl, tp)

  # 觸發做空信號
  elif (
      latest["Short_Signal"] and last_notified_timestamps.get(key) != latest_time
  ):
    last_notified_timestamps[key] = latest_time

    sl = round(latest_close + (latest_atr * 1.5), 2)
    risk = sl - latest_close
    tp = round(latest_close - (risk * 2.5), 2)

    send_discord_alert(symbol, tf, "SHORT (做空-全面捕捉)", latest_close, sl, tp)


def fetch_and_process_symbol(symbol_name, table_name, tf):
  """抓取數據並執行監控"""
  exchange = ccxt.okx({"enableRateLimit": True})

  connection = get_db_connection()
  try:
    with connection.cursor() as cursor:
      params = {"symbol": symbol_name, "timeframe": tf, "limit": 100}
      ohlcv = exchange.fetch_ohlcv(**params)
      if not ohlcv:
        return

      batch_data = [
          (
              tf,
              candle[0],
              candle[1],
              candle[2],
              candle[3],
              candle[4],
              candle[5],
          )
          for candle in ohlcv
      ]

      sql = f"""
                INSERT INTO {table_name} 
                (timeframe, open_time, open_price, high_price, low_price, close_price, volume)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (timeframe, open_time) DO UPDATE SET 
                high_price = GREATEST({table_name}.high_price, EXCLUDED.high_price),
                low_price = LEAST({table_name}.low_price, EXCLUDED.low_price),
                close_price = EXCLUDED.close_price,
                volume = EXCLUDED.volume;
            """
      cursor.executemany(sql, batch_data)
      connection.commit()

      df_tf = pd.DataFrame(
          batch_data,
          columns=[
              "timeframe",
              "open_time",
              "open_price",
              "high_price",
              "low_price",
              "close_price",
              "volume",
          ],
      )
      df_tf["open_time"] = pd.to_datetime(df_tf["open_time"], unit="ms")

      symbol_short = "ETH" if "ETH" in symbol_name else "BTC"
      check_signal_and_notify(symbol_short, tf, df_tf)

  except Exception as e:
    connection.rollback()
    print(f"[{symbol_name}-{tf}] 處理錯誤: {e}")
  finally:
    connection.close()


def run_all_tasks():
  """同時監控 BTC 與 ETH 的 5m, 15m, 1h, 4h 週期"""
  targets = [
      ("ETH/USDT:USDT", "eth_contract_klines"),
      ("BTC/USDT:USDT", "btc_contract_klines"),
  ]
  timeframes = ["5m", "15m", "1h", "4h"]

  tasks = []
  for symbol_name, table_name in targets:
    for tf in timeframes:
      tasks.append((symbol_name, table_name, tf))

  with ThreadPoolExecutor(max_workers=8) as executor:
    futures = [
        executor.submit(fetch_and_process_symbol, sym, tbl, tf)
        for sym, tbl, tf in tasks
    ]
    for future in as_completed(futures):
      try:
        future.result()
      except Exception as e:
        print(f"執行緒錯誤: {e}")


if __name__ == "__main__":
  print("================================================================")
  print(" BTC & ETH 雙軌策略【大小通吃 + 多空全抓即時推播系統】")
  print("================================================================")

  try:
    while True:
      start_time = time.time()
      run_all_tasks()
      elapsed = time.time() - start_time
      time.sleep(max(1.0, 3.0 - elapsed))
  except KeyboardInterrupt:
    print("程式已手動終止。")
    sys.exit(0)
