import ccxt
import pandas as pd
import numpy as np
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class OKXSignalScanner:
    def __init__(self):
        self.exchange = ccxt.okx({
            'enableRateLimit': True,
        })

    def fetch_ohlcv(self, symbol: str = 'BTC/USDT:USDT', timeframe: str = '1h', limit: int = 100) -> pd.DataFrame:
        """從 OKX 獲取歷史 K 線資料"""
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
        except Exception as e:
            logger.error(f"獲取 {symbol} K 線資料失敗: {e}")
            return pd.DataFrame()

    def calculate_ema(self, df: pd.DataFrame, periods=[21, 50, 100]) -> pd.DataFrame:
        """計算指數移動平均線 (EMA)"""
        for p in periods:
            df[f'ema_{p}'] = df['close'].ewm(span=p, adjust=False).mean()
        return df

    def check_signals(self, symbol: str = 'BTC/USDT:USDT') -> dict:
        """檢查當前市場技術指標並回傳訊號"""
        df = self.fetch_ohlcv(symbol, timeframe='1h', limit=120)
        if df.empty:
            return None

        df = self.calculate_ema(df)
        
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        close_price = latest['close']
        ema_21 = latest['ema_21']
        ema_50 = latest['ema_50']

        signal = None
        action = "HOLD"
        
        # 簡易範例策略：EMA 穿越交叉訊號
        if prev['ema_21'] <= prev['ema_50'] and latest['ema_21'] > latest['ema_50']:
            signal = "EMA_CROSS_GOLDEN"
            action = "BUY / LONG"
        elif prev['ema_21'] >= prev['ema_50'] and latest['ema_21'] < latest['ema_50']:
            signal = "EMA_CROSS_DEATH"
            action = "SELL / SHORT"

        return {
            "symbol": symbol,
            "price": close_price,
            "ema_21": ema_21,
            "ema_50": ema_50,
            "signal": signal,
            "action": action
        }

if __name__ == "__main__":
    scanner = OKXSignalScanner()
    res = scanner.check_signals('BTC/USDT:USDT')
    print("掃描結果:", res)
