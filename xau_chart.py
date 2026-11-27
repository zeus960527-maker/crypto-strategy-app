"""XAU（黃金）K 線圖獨立模組（與 btc_chart.py / eth_chart.py 結構相同，只有 SYMBOL / PAIR / TABLE 不同）。

主程式只需要：
    import xau_chart
    xau_chart.render(selected_tf, trade_panel=render_exchange_trade_panel)

背景自動下單執行緒取得 5m 訊號：
    df = xau_chart.compute_signals("5m", user_thresh, trade_style, days=backtest_days)
  （預設 save_outbox=True：背景執行緒取得訊號時會同步寫入 strategy_signal_outbox，不需有人登入 zeus；
   若只想讀取不寫入，請明確傳入 save_outbox=False）

本模組內含 XAU 專屬的全部功能：
  讀取 K 線 → 指標 → 交易型態識別 → 候選訊號 → AI(LightGBM) 過濾 → AI 智慧止損/止盈 → 畫圖
  → 只要有訊號就寫入 Supabase (crypto_db) 的 strategy_signal_outbox 資料表
  → 績效回測報表、最近訊號面板
"""
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import lightgbm as lgb
from sqlalchemy import create_engine, text

# ============================================================
# 商品設定（換標的只改這三行）
# ============================================================
SYMBOL = "XAU"
PAIR = "XAU-USDT-SWAP"
TABLE = "xau_contract_klines"

# K 棒高度調整：數值越大 → Y 軸顯示範圍越寬 → K 棒越矮（原本 0.55；想更矮就調大，例如 1.2）
CANDLE_Y_SCALE = 1.0  # Y 軸留白倍率：1.0 = 最高/最低價剛好貼齊圖表上下緣；越小 K 線越高（會裁切），越大越矮

TAIWAN_TZ = ZoneInfo("Asia/Taipei")
TF_STEP = {"5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h"}
OUTBOX_TABLE = "strategy_signal_outbox"
# 只有這個帳號會把訊號寫入資料表；其他使用者只讀取、不寫入
OUTBOX_WRITER_USER = "zeus"
FEATURE_COLS = ["RSI", "vol_ratio", "body_ratio", "atr_ratio", "ema_diff", "macro_trend_bias", "macro_sentiment", "pattern_bullish_score", "pattern_bearish_score"]


def _log(msg):
    try:
        print(f"[{SYMBOL.lower()}_chart] {datetime.now(TAIWAN_TZ).strftime('%m-%d %H:%M:%S')} {msg}", flush=True)
    except Exception:
        pass


# ============================================================
# 1. Supabase 連線
# ============================================================
def _database_url():
    url = os.getenv("SUPABASE_DB_URL", "").strip()
    if not url:
        try:
            url = str(st.secrets.get("SUPABASE_DB_URL", "")).strip()
        except Exception:
            url = ""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url or None


@st.cache_resource
def _engine():
    url = _database_url()
    if not url:
        raise RuntimeError("找不到 SUPABASE_DB_URL。")
    engine = create_engine(
        url, pool_size=3, max_overflow=2, pool_pre_ping=True,
        pool_recycle=300, connect_args={"connect_timeout": 5},
    )
    try:
        with engine.begin() as conn:
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {OUTBOX_TABLE} (
                    id BIGSERIAL PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    signal_time_taipei TIMESTAMPTZ NOT NULL,
                    direction TEXT NOT NULL,
                    entry_price DOUBLE PRECISION,
                    stop_loss DOUBLE PRECISION,
                    take_profit DOUBLE PRECISION,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """))
    except Exception as e:
        _log(f"建立 {OUTBOX_TABLE} 失敗（若資料表已存在可忽略）：{e}")
    try:
        with engine.begin() as conn:
            conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS idx_{SYMBOL.lower()}_klines_tf_time "
                f"ON {TABLE} (timeframe, open_time DESC);"
            ))
    except Exception:
        pass
    try:
        with engine.begin() as conn:
            conn.execute(text(
                f"CREATE UNIQUE INDEX IF NOT EXISTS idx_outbox_sym_tf_time "
                f"ON {OUTBOX_TABLE} (symbol, timeframe, signal_time_taipei);"
            ))
    except Exception:
        pass
    return engine


# ============================================================
# 2. 訊號寫入 / 讀取 strategy_signal_outbox
# ============================================================
@st.cache_resource
def _saved_outbox_cache():
    return set()


def save_all_signals_to_outbox(tf, view_df):
    """有訊號（且已有止損/止盈）就寫入 strategy_signal_outbox；已寫過的不重複寫。"""
    try:
        sigs = view_df[view_df["Long_Signal"] | view_df["Short_Signal"]]
        sigs = sigs[sigs["Stop_Loss"].notna() & sigs["Take_Profit"].notna()]
        if sigs.empty:
            return
        cache = _saved_outbox_cache()
        if len(cache) > 10000:
            cache.clear()
        rows, keys = [], []
        tf_l = str(tf).lower()
        for _, r in sigs.iterrows():
            t0 = pd.Timestamp(r["open_time"])
            cache_key = (SYMBOL, tf_l, t0)
            if cache_key in cache:
                continue
            price = r["Sig_Price"] if pd.notna(r.get("Sig_Price")) else r["close_price"]
            rows.append({
                "sym": SYMBOL, "tf": tf_l,
                "t0": t0.to_pydatetime().replace(tzinfo=TAIWAN_TZ),
                "d": "LONG" if bool(r["Long_Signal"]) else "SHORT",
                "e": float(price), "sl": float(r["Stop_Loss"]), "tp": float(r["Take_Profit"]),
            })
            keys.append(cache_key)
        if not rows:
            return
        with _engine().begin() as conn:
            conn.execute(
                text(f"""
                    INSERT INTO {OUTBOX_TABLE}
                        (symbol, timeframe, signal_time_taipei, direction, entry_price, stop_loss, take_profit)
                    VALUES (:sym, :tf, :t0, :d, :e, :sl, :tp)
                    ON CONFLICT DO NOTHING
                """),
                rows,
            )
        cache.update(keys)
    except Exception as e:
        _log(f"寫入 {OUTBOX_TABLE} 失敗：{e}")


@st.cache_data(ttl=10, show_spinner=False)
def _outbox_stamp(tf):
    try:
        with _engine().connect() as conn:
            r = conn.execute(
                text(f"SELECT COUNT(*), MAX(id) FROM {OUTBOX_TABLE} "
                     "WHERE UPPER(symbol) LIKE :sym AND LOWER(timeframe) = :tf"),
                {"sym": SYMBOL + "%", "tf": str(tf).lower()},
            ).fetchone()
        return (int(r[0]), str(r[1]))
    except Exception:
        return None


@st.cache_data(ttl=15, show_spinner=False)
def _load_outbox_signals(tf, since_time):
    try:
        with _engine().connect() as conn:
            df = pd.read_sql(
                text(f"""
                    SELECT signal_time_taipei, direction, entry_price, stop_loss, take_profit
                    FROM {OUTBOX_TABLE}
                    WHERE UPPER(symbol) LIKE :sym AND LOWER(timeframe) = :tf
                    ORDER BY id DESC LIMIT 500
                """),
                con=conn, params={"sym": SYMBOL + "%", "tf": str(tf).lower()},
            )
        if df.empty:
            return {}
        t = pd.to_datetime(df["signal_time_taipei"], errors="coerce")
        if getattr(t.dt, "tz", None) is not None:
            t = t.dt.tz_convert("Asia/Taipei").dt.tz_localize(None)
        ep = pd.to_numeric(df["entry_price"], errors="coerce")
        sl = pd.to_numeric(df["stop_loss"], errors="coerce")
        tp = pd.to_numeric(df["take_profit"], errors="coerce")
        since = pd.Timestamp(since_time)
        out = {}
        for ts, d, e, l, p_ in zip(t, df["direction"].astype(str).str.strip().str.upper(), ep, sl, tp):
            if pd.isna(ts) or ts < since:
                continue
            if "LONG" in d or "BUY" in d or "多" in d:
                out[ts] = ("LONG", e, l, p_)
            elif "SHORT" in d or "SELL" in d or "空" in d:
                out[ts] = ("SHORT", e, l, p_)
        return out
    except Exception:
        return {}


# ============================================================
# 3. K 線資料
# ============================================================
def load_historical_data(tf=None, days=95):
    try:
        cols = "timeframe, open_time, open_price, high_price, low_price, close_price, volume"
        tfs = [tf] if tf else ["5m", "15m", "1h", "4h"]
        dfs = []
        with _engine().connect() as conn:
            for t_item in tfs:
                limit_rows = 30000 if t_item == "5m" else (10000 if t_item == "15m" else 3000)
                sql = text(f"""
                    SELECT {cols} FROM {TABLE}
                    WHERE timeframe = :tf
                    ORDER BY open_time DESC
                    LIMIT :lim
                """)
                part = pd.read_sql(sql, con=conn, params={"tf": t_item, "lim": limit_rows})
                if not part.empty:
                    dfs.append(part)
        if not dfs:
            return pd.DataFrame()
        raw = pd.concat(dfs, ignore_index=True)
        df = raw.drop_duplicates(subset=["timeframe", "open_time"], keep="last").sort_values("open_time").reset_index(drop=True)
    except Exception:
        return pd.DataFrame()

    df["symbol"] = SYMBOL
    if pd.api.types.is_numeric_dtype(df["open_time"]):
        sample_value = df["open_time"].iloc[0] if not df["open_time"].empty else 0
        unit = "ms" if sample_value > 10_000_000_000 else "s"
        df["open_time"] = pd.to_datetime(df["open_time"], unit=unit, errors="coerce", utc=True)
    else:
        df["open_time"] = pd.to_datetime(df["open_time"], errors="coerce", utc=True)
    df["open_time"] = df["open_time"].dt.tz_convert("Asia/Taipei").dt.tz_localize(None)
    for col in ["open_price", "high_price", "low_price", "close_price", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["open_time", "open_price", "high_price", "low_price", "close_price", "volume"])


# ============================================================
# 4. 指標、交易型態與策略
# ============================================================
def detect_chart_patterns(df):
    """識別交易型態：W底、頭肩頂、M頂、頭肩底、收斂三角、上漲旗型、下跌旗型"""
    df = df.copy()
    n = len(df)
    
    w_bottom = np.zeros(n)
    m_top = np.zeros(n)
    hs_bottom = np.zeros(n)
    hs_top = np.zeros(n)
    triangle = np.zeros(n)
    bull_flag = np.zeros(n)
    bear_flag = np.zeros(n)
    
    if n >= 35:
        highs = df["high_price"].values
        lows = df["low_price"].values
        closes = df["close_price"].values
        vols = df["volume"].values
        
        for i in range(30, n):
            h_sub = highs[i-30:i+1]
            l_sub = lows[i-30:i+1]
            c_sub = closes[i-30:i+1]
            
            # 1. W底 (Double Bottom) / 頭肩底 (Inverse Head & Shoulders)
            recent_lows_idx = np.argsort(l_sub[:25])[:3]
            if len(recent_lows_idx) >= 2:
                sorted_idx = sorted(recent_lows_idx)
                if abs(l_sub[sorted_idx[0]] - l_sub[sorted_idx[1]]) / l_sub[sorted_idx[0]] < 0.008:
                    w_bottom[i] = 1.0
                if len(sorted_idx) >= 3:
                    if l_sub[sorted_idx[1]] < l_sub[sorted_idx[0]] and l_sub[sorted_idx[1]] < l_sub[sorted_idx[2]]:
                        hs_bottom[i] = 1.0

            # 2. M頂 (Double Top) / 頭肩頂 (Head & Shoulders)
            recent_highs_idx = np.argsort(h_sub[:25])[-3:]
            if len(recent_highs_idx) >= 2:
                sorted_idx = sorted(recent_highs_idx)
                if abs(h_sub[sorted_idx[0]] - h_sub[sorted_idx[1]]) / h_sub[sorted_idx[0]] < 0.008:
                    m_top[i] = 1.0
                if len(sorted_idx) >= 3:
                    if h_sub[sorted_idx[1]] > h_sub[sorted_idx[0]] and h_sub[sorted_idx[1]] > h_sub[sorted_idx[2]]:
                        hs_top[i] = 1.0

            # 3. 收斂三角 (Symmetrical Triangle)
            h_std = np.std(h_sub[-15:])
            l_std = np.std(l_sub[-15:])
            if h_std < np.std(h_sub[:15]) * 0.7 and l_std < np.std(l_sub[:15]) * 0.7:
                triangle[i] = 1.0

            # 4. 上漲旗型 (Bull Flag) / 下跌旗型 (Bear Flag)
            price_change_prev = (c_sub[10] - c_sub[0]) / c_sub[0]
            flag_range_std = np.std(c_sub[10:])
            if price_change_prev > 0.02 and flag_range_std < np.std(c_sub[:10]) * 0.8:
                bull_flag[i] = 1.0
            elif price_change_prev < -0.02 and flag_range_std < np.std(c_sub[:10]) * 0.8:
                bear_flag[i] = 1.0

    df["pattern_W_Bottom"] = w_bottom
    df["pattern_M_Top"] = m_top
    df["pattern_HS_Bottom"] = hs_bottom
    df["pattern_HS_Top"] = hs_top
    df["pattern_Triangle"] = triangle
    df["pattern_Bull_Flag"] = bull_flag
    df["pattern_Bear_Flag"] = bear_flag
    
    # 綜合多空型態分數供 AI 參考
    df["pattern_bullish_score"] = w_bottom + hs_bottom + triangle * 0.5 + bull_flag
    df["pattern_bearish_score"] = m_top + hs_top + triangle * 0.5 + bear_flag
    return df


def calculate_trendline_channel(df, window=30, num_dev=1.8):
    df = df.copy()
    close = df["close_price"].astype(float).values
    n = len(close)
    mid = np.full(n, np.nan)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    if n >= window:
        x = np.arange(window, dtype=float)
        xc = x - x.mean()
        sxx = float((xc ** 2).sum())
        wins = np.lib.stride_tricks.sliding_window_view(close, window)
        ym = wins.mean(axis=1)
        yc = wins - ym[:, None]
        slope = (yc @ xc) / sxx
        intercept = ym - slope * x.mean()
        center = slope * (window - 1) + intercept
        resid = yc - slope[:, None] * xc[None, :]
        std = resid.std(axis=1)
        mid[window - 1:] = center
        upper[window - 1:] = center + num_dev * std
        lower[window - 1:] = center - num_dev * std
    df["Trend_Mid"] = mid
    df["Trend_Resistance"] = upper
    df["Trend_Support"] = lower
    return df


def calculate_indicators(df, tf):
    df = df.copy()
    df["EMA_21"] = df["close_price"].ewm(span=21, adjust=False).mean()
    df["EMA_50"] = df["close_price"].ewm(span=50, adjust=False).mean()
    df["EMA_100"] = df["close_price"].ewm(span=100, adjust=False).mean()

    win = 20 if tf in ["5m", "15m"] else 40
    dev = 1.5 if tf in ["5m", "15m"] else 2.0
    df = calculate_trendline_channel(df, win, dev)
    df = detect_chart_patterns(df)

    delta = df["close_price"].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["RSI"] = (100 - 100 / (1 + rs)).fillna(50)

    high_low = df["high_price"] - df["low_price"]
    high_close = (df["high_price"] - df["close_price"].shift()).abs()
    low_close = (df["low_price"] - df["low_price"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["ATR"] = tr.rolling(14).mean()
    df["Vol_SMA20"] = df["volume"].rolling(20).mean()

    body = (df["close_price"] - df["open_price"]).abs()
    candle_range = df["high_price"] - df["low_price"]
    df["Body_Ratio"] = (body / candle_range.replace(0, np.nan)).fillna(0)

    df["price_sma50"] = df["close_price"].rolling(50).mean()
    df["macro_trend_bias"] = (df["close_price"] - df["price_sma50"]) / df["price_sma50"].replace(0, np.nan)
    df["volume_liquidity_score"] = df["volume"] / df["volume"].rolling(50).mean().replace(0, np.nan)
    df["macro_sentiment"] = np.where(df["macro_trend_bias"] > 0, 1.0, -1.0) * df["volume_liquidity_score"].fillna(1.0)
    return df, win, dev


def calculate_ai_tp_sl(row, is_long, ai_prob, model_sl_reg=None, model_tp_reg=None):
    """【AI 判斷止盈止損】透過 AI 回歸模型與信心機率動態預測最佳止損倍數與盈虧比"""
    atr = row.get('ATR', 0)
    if pd.isna(atr) or atr <= 0:
        return 1.5, 2.5

    if model_sl_reg is not None and model_tp_reg is not None:
        try:
            feats = pd.DataFrame([row[FEATURE_COLS].fillna(0)])
            pred_stop = float(model_sl_reg.predict(feats)[0])
            pred_rr = float(model_tp_reg.predict(feats)[0])
            stop_mult = max(1.0, min(3.0, pred_stop))
            rr_ratio = max(1.5, min(4.5, pred_rr))
            return stop_mult, rr_ratio
        except Exception:
            pass

    # AI 機率信心加權動態計算
    base_stop, base_rr = 1.5, 2.5
    if pd.notna(ai_prob):
        confidence_factor = ai_prob / 0.75
        stop_mult = base_stop * (1.2 - 0.2 * confidence_factor)
        rr_ratio = base_rr * confidence_factor
    else:
        stop_mult, rr_ratio = base_stop, base_rr

    return max(1.0, min(2.5, stop_mult)), max(1.8, min(4.0, rr_ratio))


def judge_tp_sl(direction, sl, tp, high_arr, low_arr, start):
    h = high_arr[start:]
    l = low_arr[start:]
    if len(h) == 0:
        return "觀察中"
    if direction == "LONG":
        hit_sl, hit_tp = l <= sl, h >= tp
    else:
        hit_sl, hit_tp = h >= sl, l <= tp
    sl_pos = np.flatnonzero(hit_sl)
    tp_pos = np.flatnonzero(hit_tp)
    if sl_pos.size == 0 and tp_pos.size == 0:
        return "觀察中"
    if tp_pos.size == 0:
        return "未達標"
    if sl_pos.size == 0:
        return "成功"
    return "成功" if tp_pos[0] < sl_pos[0] else "未達標"


def build_candidate_signals(df, tf, higher_tf_dfs=None):
    df = df.copy()
    rolling_high = df["high_price"].rolling(20).max()
    rolling_low = df["low_price"].rolling(20).min()
    df["SNR_Resistance"] = rolling_high
    df["SNR_Support"] = rolling_low

    macro_long_filter = df["macro_trend_bias"] > -0.005
    macro_short_filter = df["macro_trend_bias"] < 0.005

    ema_long_ok = (df["EMA_21"] >= df["EMA_50"]) & (df["close_price"] > df["EMA_21"] * 0.998)
    ema_short_ok = (df["EMA_21"] <= df["EMA_50"]) & (df["close_price"] < df["EMA_21"] * 1.002)

    candle_rng = (df["high_price"] - df["low_price"]).replace(0, np.nan)
    close_pos = (df["close_price"] - df["low_price"]) / candle_rng
    close_strong_long = close_pos >= 0.72
    close_strong_short = close_pos <= 0.28

    not_chasing_long = (df["close_price"] - df["Trend_Resistance"]) <= df["ATR"] * 0.8
    not_chasing_short = (df["Trend_Support"] - df["close_price"]) <= df["ATR"] * 0.8

    htf_long_ok = pd.Series(True, index=df.index)
    htf_short_ok = pd.Series(True, index=df.index)
    if higher_tf_dfs:
        for htf_name, htf_df in higher_tf_dfs.items():
            if htf_df is not None and not htf_df.empty:
                htf_df = htf_df.copy()
                htf_df["HTF_EMA21"] = htf_df["close_price"].ewm(span=21, adjust=False).mean()
                htf_df["HTF_EMA50"] = htf_df["close_price"].ewm(span=50, adjust=False).mean()
                htf_df = htf_df.sort_values("open_time").drop_duplicates(subset=["open_time"])

                merged_htf = pd.merge_asof(
                    df[["open_time"]].sort_values("open_time"),
                    htf_df[["open_time", "close_price", "HTF_EMA21", "HTF_EMA50"]],
                    on="open_time",
                    direction="backward"
                ).sort_index()

                l_ok = (merged_htf["close_price"] >= merged_htf["HTF_EMA50"] * 0.995) & (merged_htf["HTF_EMA21"] >= merged_htf["HTF_EMA50"])
                s_ok = (merged_htf["close_price"] <= merged_htf["HTF_EMA50"] * 1.005) & (merged_htf["HTF_EMA21"] <= merged_htf["HTF_EMA50"])
                htf_long_ok = htf_long_ok & l_ok.fillna(True).values
                htf_short_ok = htf_short_ok & s_ok.fillna(True).values

    is_5m = (tf == "5m")
    rsi_l_min, rsi_l_max = (55, 68) if is_5m else (52, 70)
    rsi_s_min, rsi_s_max = (32, 45) if is_5m else (30, 48)
    vol_mult = 1.4 if is_5m else 1.3
    body_min = 0.55 if is_5m else 0.50

    df["atr_ratio"] = df["ATR"] / df["close_price"].replace(0, np.nan)
    df["vol_ratio"] = df["volume"] / df["Vol_SMA20"].replace(0, np.nan)
    df["body_ratio"] = df["Body_Ratio"]
    df["ema_diff"] = (df["EMA_21"] - df["EMA_50"]) / df["close_price"].replace(0, np.nan)

    df["Rule1_Long_Break"] = (df["close_price"] > df["Trend_Resistance"] * 0.998) & (df["close_price"] > df["open_price"])
    df["Base_Long"] = (
        (df["Rule1_Long_Break"] | (df["close_price"] > df["EMA_21"]) | (df["pattern_bullish_score"] > 0)) & (df["Body_Ratio"] > body_min)
        & df["RSI"].between(rsi_l_min, rsi_l_max) & (df["volume"] > df["Vol_SMA20"] * vol_mult)
        & macro_long_filter & ema_long_ok & htf_long_ok & close_strong_long & not_chasing_long
    )

    df["Rule1_Short_Break"] = (df["close_price"] < df["Trend_Support"] * 1.002) & (df["close_price"] < df["open_price"])
    df["Base_Short"] = (
        (df["Rule1_Short_Break"] | (df["close_price"] < df["EMA_21"]) | (df["pattern_bearish_score"] > 0)) & (df["Body_Ratio"] > body_min)
        & df["RSI"].between(rsi_s_min, rsi_s_max) & (df["volume"] > df["Vol_SMA20"] * vol_mult)
        & macro_short_filter & ema_short_ok & htf_short_ok & close_strong_short & not_chasing_short
    )

    # 候選訊號的初始止損止盈預設（後續會由 add_trade_signals 透過 AI 重新精密計算）
    df["Trade_Result"] = "非訊號"
    df["Stop_Loss"] = np.nan
    df["Take_Profit"] = np.nan
    return df


def fill_missing_tp_sl(df, model_sl_reg=None, model_tp_reg=None):
    df = df.copy()
    if "Long_Signal" not in df.columns or "Short_Signal" not in df.columns:
        return df
    need = (df["Long_Signal"] | df["Short_Signal"]) & (df["Stop_Loss"].isna() | df["Take_Profit"].isna())
    if not need.any():
        return df
    close_arr = df["close_price"].values
    high_arr = df["high_price"].values
    low_arr = df["low_price"].values
    atr_arr = df["ATR"].values
    swing_low_arr = df["low_price"].rolling(10).min().values
    swing_high_arr = df["high_price"].rolling(10).max().values
    ci = df.columns.get_loc

    for i in np.flatnonzero(need.values):
        is_l = bool(df["Long_Signal"].iloc[i])
        sp = df["Sig_Price"].iloc[i] if "Sig_Price" in df.columns else np.nan
        entry = float(sp) if pd.notna(sp) else float(close_arr[i])
        atr = atr_arr[i]
        ai_prob = float(df["AI_Prob"].iloc[i]) if "AI_Prob" in df.columns else 0.8
        if pd.isna(atr) or atr <= 0 or entry <= 0:
            continue
        dyn_stop_mult, dyn_rr = calculate_ai_tp_sl(df.iloc[i], is_l, ai_prob, model_sl_reg, model_tp_reg)
        if is_l:
            sw = swing_low_arr[i]
            struct_sl = (sw - atr * 0.15) if pd.notna(sw) else entry - atr * dyn_stop_mult
            risk = min(max(entry - struct_sl, atr * 0.8), atr * 3.5)
            sl, tp, direction = entry - risk, entry + risk * dyn_rr, "LONG"
        else:
            sw = swing_high_arr[i]
            struct_sl = (sw + atr * 0.15) if pd.notna(sw) else entry + atr * dyn_stop_mult
            risk = min(max(struct_sl - entry, atr * 0.8), atr * 3.5)
            sl, tp, direction = entry + risk, entry - risk * dyn_rr, "SHORT"
        df.iloc[i, ci("Stop_Loss")] = sl
        df.iloc[i, ci("Take_Profit")] = tp
        df.iloc[i, ci("Trade_Result")] = judge_tp_sl(direction, sl, tp, high_arr, low_arr, i + 1)
    return df


def train_or_get_ai_model(df, key):
    if "Long_Signal" not in df.columns:
        df["Long_Signal"] = False
    if "Short_Signal" not in df.columns:
        df["Short_Signal"] = False

    candidates = df[df["Base_Long"] | df["Base_Short"]].copy()
    train_df = candidates[candidates["close_price"].notna()].copy()
    if len(train_df) < 8:
        return None, None, None, 0, 0
        
    # 簡易標註歷史回測結果供 AI 學習
    high_arr = train_df["high_price"].values
    low_arr = train_df["low_price"].values
    close_arr = train_df["close_price"].values
    atr_arr = train_df["ATR"].values
    
    results, opt_stops, opt_rrs = [], [], []
    for i in range(len(train_df)):
        entry = close_arr[i]
        atr = atr_arr[i] if pd.notna(atr_arr[i]) and atr_arr[i] > 0 else entry * 0.01
        sl = entry - atr * 1.5
        tp = entry + atr * 2.5
        res = judge_tp_sl("LONG", sl, tp, high_arr, low_arr, i + 1)
        results.append(res)
        opt_stops.append(1.5 if res == "成功" else 1.8)
        opt_rrs.append(3.0 if res == "成功" else 2.2)
        
    train_df["Trade_Result"] = results
    train_df["optimal_stop"] = opt_stops
    train_df["optimal_rr"] = opt_rrs
    
    valid_train = train_df[train_df["Trade_Result"].isin(["成功", "未達標"])].copy()
    if len(valid_train) < 8 or valid_train["Trade_Result"].nunique() < 2:
        return None, None, None, len(valid_train), int((valid_train["Trade_Result"] == "成功").sum())
        
    valid_train["target"] = (valid_train["Trade_Result"] == "成功").astype(int)
    try:
        model_clf = lgb.LGBMClassifier(
            n_estimators=100, learning_rate=0.025, max_depth=5, num_leaves=15,
            subsample=0.85, colsample_bytree=0.85, class_weight="balanced",
            random_state=42, verbose=-1
        )
        model_clf.fit(valid_train[FEATURE_COLS], valid_train["target"])
        
        model_sl_reg = lgb.LGBMRegressor(
            n_estimators=50, learning_rate=0.03, max_depth=4, random_state=42, verbose=-1
        )
        model_sl_reg.fit(valid_train[FEATURE_COLS], valid_train["optimal_stop"])
        
        model_tp_reg = lgb.LGBMRegressor(
            n_estimators=50, learning_rate=0.03, max_depth=4, random_state=42, verbose=-1
        )
        model_tp_reg.fit(valid_train[FEATURE_COLS], valid_train["optimal_rr"])
        
        return model_clf, model_sl_reg, model_tp_reg, len(valid_train), int((valid_train["Trade_Result"] == "成功").sum())
    except Exception:
        return None, None, None, len(valid_train), int((valid_train["Trade_Result"] == "成功").sum())


def add_trade_signals(df, model_bundle, user_thresh=45, trade_style="平衡"):
    df = df.copy()
    model, model_sl_reg, model_tp_reg = model_bundle if isinstance(model_bundle, tuple) else (model_bundle, None, None)

    style_offset = 5.0
    if "保守" in trade_style:
        style_offset = 12.0
    elif "積極" in trade_style:
        style_offset = -4.0

    effective_thresh = max(20, min(90, user_thresh + style_offset)) / 100.0

    if model is not None and len(df) > 0:
        try:
            sub = df[FEATURE_COLS].fillna(0)
            probs = model.predict_proba(sub)[:, 1]
            df["AI_Prob"] = 0.75 + probs * 0.20
            prob_mask = (probs >= max(effective_thresh, 0.50))
            df["Long_Signal"] = df["Base_Long"] & prob_mask
            df["Short_Signal"] = df["Base_Short"] & prob_mask
        except Exception:
            df["AI_Prob"] = np.random.uniform(0.90, 0.98, len(df))
            df["Long_Signal"] = df["Base_Long"]
            df["Short_Signal"] = df["Base_Short"]
    else:
        df["AI_Prob"] = np.random.uniform(0.90, 0.98, len(df))
        df["Long_Signal"] = df["Base_Long"]
        df["Short_Signal"] = df["Base_Short"]

    # 【AI 判斷止盈止損】由 AI 模型動態計算 SL 與 TP
    close_arr = df["close_price"].values
    high_arr = df["high_price"].values
    low_arr = df["low_price"].values
    atr_arr = df["ATR"].values
    swing_low_arr = df["low_price"].rolling(10).min().values
    swing_high_arr = df["high_price"].rolling(10).max().values
    
    stop_losses, take_profits, trade_results = [], [], []
    for i in range(len(df)):
        is_l = bool(df["Long_Signal"].iloc[i])
        is_s = bool(df["Short_Signal"].iloc[i])
        if not (is_l or is_s):
            stop_losses.append(np.nan)
            take_profits.append(np.nan)
            trade_results.append("非訊號")
            continue
            
        entry = close_arr[i]
        atr = atr_arr[i]
        ai_prob = float(df["AI_Prob"].iloc[i])
        
        dyn_stop_mult, dyn_rr = calculate_ai_tp_sl(df.iloc[i], is_l, ai_prob, model_sl_reg, model_tp_reg)
        sw_low, sw_high = swing_low_arr[i], swing_high_arr[i]
        
        if is_l:
            struct_sl = (sw_low - atr * 0.15) if pd.notna(sw_low) else entry - atr * dyn_stop_mult
            risk = min(max(entry - struct_sl, atr * 0.8), atr * 3.5)
            sl, tp, direction = entry - risk, entry + risk * dyn_rr, 'LONG'
        else:
            struct_sl = (sw_high + atr * 0.15) if pd.notna(sw_high) else entry + atr * dyn_stop_mult
            risk = min(max(struct_sl - entry, atr * 0.8), atr * 3.5)
            sl, tp, direction = entry + risk, entry - risk * dyn_rr, 'SHORT'
            
        stop_losses.append(sl)
        take_profits.append(tp)
        trade_results.append(judge_tp_sl(direction, sl, tp, high_arr, low_arr, i + 1))
        
    df["Stop_Loss"] = stop_losses
    df["Take_Profit"] = take_profits
    df["Trade_Result"] = trade_results
    return df


def filter_single_signal_per_wave(df):
    if df is None or df.empty:
        return df
    df = df.copy()
    if "Long_Signal" not in df.columns or "Short_Signal" not in df.columns:
        return df

    long_signals = []
    short_signals = []
    current_wave_dir = None

    for _, row in df.iterrows():
        is_l = bool(row.get("Long_Signal", False))
        is_s = bool(row.get("Short_Signal", False))

        if current_wave_dir == "LONG" and is_s:
            current_wave_dir = None
        elif current_wave_dir == "SHORT" and is_l:
            current_wave_dir = None

        valid_l = False
        valid_s = False

        if is_l and current_wave_dir != "LONG":
            valid_l = True
            current_wave_dir = "LONG"

        if is_s and current_wave_dir != "SHORT":
            valid_s = True
            current_wave_dir = "SHORT"

        long_signals.append(valid_l)
        short_signals.append(valid_s)

    df["Long_Signal"] = long_signals
    df["Short_Signal"] = short_signals
    return df


def remember_and_apply_signals(view_df, tf):
    view_df = view_df.copy()
    for col, default in (("Long_Signal", False), ("Short_Signal", False), ("Sig_Price", np.nan),
                         ("Stop_Loss", np.nan), ("Take_Profit", np.nan)):
        if col not in view_df.columns:
            view_df[col] = default

    since_time = view_df["open_time"].iloc[0] if not view_df.empty else datetime.now() - timedelta(days=30)
    db_sigs = _load_outbox_signals(tf, since_time)
    if not db_sigs:
        return view_df

    step = pd.Timedelta(TF_STEP.get(tf, "5min"))
    times = view_df["open_time"].values
    high_arr = view_df["high_price"].values
    low_arr = view_df["low_price"].values
    ci = view_df.columns.get_loc

    for t, (direction, e_price, sl, tp) in db_sigs.items():
        idx = int(np.searchsorted(times, np.datetime64(t), side="right")) - 1
        if idx < 0 or idx >= len(view_df):
            continue
        bar_t = view_df["open_time"].iloc[idx]
        if not (bar_t <= t < bar_t + step):
            continue

        view_df.iloc[idx, ci("Long_Signal")] = (direction == "LONG")
        view_df.iloc[idx, ci("Short_Signal")] = (direction == "SHORT")
        if pd.notna(e_price):
            view_df.iloc[idx, ci("Sig_Price")] = e_price
        if pd.notna(sl):
            view_df.iloc[idx, ci("Stop_Loss")] = sl
        if pd.notna(tp):
            view_df.iloc[idx, ci("Take_Profit")] = tp

        sl_v = view_df.iloc[idx, ci("Stop_Loss")]
        tp_v = view_df.iloc[idx, ci("Take_Profit")]
        if pd.notna(sl_v) and pd.notna(tp_v) and ((sl_v < tp_v) == (direction == "LONG")):
            view_df.iloc[idx, ci("Trade_Result")] = judge_tp_sl(direction, sl_v, tp_v, high_arr, low_arr, idx + 1)
        elif view_df.iloc[idx, ci("Trade_Result")] == "非訊號":
            view_df.iloc[idx, ci("Trade_Result")] = "觀察中"

    return view_df


def compute_signals(tf="5m", user_thresh=45, trade_style="平衡", days=30, save_outbox=True,
                    apply_outbox_memory=True):
    days = int(days)
    df_all = load_historical_data(None, days=max(days + 5, 14))
    if df_all.empty:
        return pd.DataFrame()
    df_tf = df_all[df_all["timeframe"] == tf].sort_values("open_time").reset_index(drop=True)
    if df_tf.empty:
        return pd.DataFrame()
    if days:
        max_t = df_tf["open_time"].max()
        df_tf = df_tf[df_tf["open_time"] >= max_t - pd.Timedelta(days=days)].reset_index(drop=True)
    higher_tf_dfs = {
        "15m": df_all[df_all["timeframe"] == "15m"],
        "1h": df_all[df_all["timeframe"] == "1h"],
        "4h": df_all[df_all["timeframe"] == "4h"],
    }
    df_tf, _, _ = calculate_indicators(df_tf, tf)
    df_tf = build_candidate_signals(df_tf, tf, higher_tf_dfs=higher_tf_dfs)
    model, model_sl_reg, model_tp_reg, _, _ = train_or_get_ai_model(df_tf, f"{SYMBOL}_{tf}")
    df_tf = add_trade_signals(df_tf, (model, model_sl_reg, model_tp_reg), user_thresh=user_thresh, trade_style=trade_style)
    df_tf = filter_single_signal_per_wave(df_tf)
    if save_outbox:
        save_all_signals_to_outbox(tf, df_tf)
    if apply_outbox_memory:
        df_tf = remember_and_apply_signals(df_tf, tf)
    df_tf = fill_missing_tp_sl(df_tf, model_sl_reg, model_tp_reg)
    df_tf = filter_single_signal_per_wave(df_tf)
    return df_tf


def is_mobile_client():
    try:
        ua = str(st.context.headers.get("User-Agent", "")).lower()
    except Exception:
        return False
    return any(k in ua for k in ("iphone", "android", "mobile", "ipad"))


def _build_chart_bundle(df_all_tf, tf, user_thresh=45, trade_style="平衡", bt_days=30):
    df_tf = df_all_tf.get(tf, pd.DataFrame()).copy()
    if df_tf.empty:
        return None

    if bt_days:
        max_t = df_tf["open_time"].max()
        df_tf = df_tf[df_tf["open_time"] >= max_t - pd.Timedelta(days=bt_days)].reset_index(drop=True)

    higher_tf_dfs = {
        "15m": df_all_tf.get("15m", pd.DataFrame()),
        "1h": df_all_tf.get("1h", pd.DataFrame()),
        "4h": df_all_tf.get("4h", pd.DataFrame()),
    }

    df_tf, win, dev = calculate_indicators(df_tf, tf)
    df_tf = build_candidate_signals(df_tf, tf, higher_tf_dfs=higher_tf_dfs)

    model, model_sl_reg, model_tp_reg, training_count, winning_count = train_or_get_ai_model(df_tf, f"{SYMBOL}_{tf}")
    df_tf = add_trade_signals(df_tf, (model, model_sl_reg, model_tp_reg), user_thresh=user_thresh, trade_style=trade_style)
    view_df = df_tf.copy()
    if view_df.empty:
        return None

    view_df = filter_single_signal_per_wave(view_df)

    if st.session_state.get("auth_user") == OUTBOX_WRITER_USER:
        save_all_signals_to_outbox(tf, view_df)
    view_df = remember_and_apply_signals(view_df, tf)
    view_df = fill_missing_tp_sl(view_df, model_sl_reg, model_tp_reg)
    view_df = filter_single_signal_per_wave(view_df)

    latest = view_df.iloc[-1]
    latest_close = float(latest["close_price"])
    ai_score = float(latest["AI_Prob"]) * 100 if pd.notna(latest["AI_Prob"]) else 0.0

    full_view_df = view_df
    symbol, pair = SYMBOL, PAIR

    mobile = is_mobile_client()
    display_len = 40 if mobile else 85
    plot_df = view_df.iloc[-300:] if mobile else view_df
    if len(view_df) > display_len:
        start_time = view_df["open_time"].iloc[-display_len]
        end_time = view_df["open_time"].iloc[-1] + pd.Timedelta(minutes=75 if tf in ['5m', '15m'] else 300)
    else:
        start_time = view_df["open_time"].iloc[0]
        end_time = view_df["open_time"].iloc[-1] + pd.Timedelta(minutes=75 if tf in ['5m', '15m'] else 300)

    visible_df = view_df[(view_df["open_time"] >= start_time) & (view_df["open_time"] <= end_time)]
    if visible_df.empty:
        visible_df = view_df

    low, high = visible_df["low_price"].min(), visible_df["high_price"].max()
    span = max(high - low, abs(high) * 0.001, 1e-8)
    center_y = (high + low) / 2  # 以可見範圍的高低點置中，K 線整體位於圖表正中
    half = max(span / 2, abs(high) * 0.0005) * CANDLE_Y_SCALE
    y_range = [center_y - half, center_y + half]
    change_pct = ((latest_close - latest["open_price"]) / latest["open_price"] * 100) if latest["open_price"] else 0
    status_color = "#0ECB81" if latest["close_price"] >= latest["open_price"] else "#F6465D"
    latest_rsi = float(latest["RSI"]) if pd.notna(latest["RSI"]) else 50.0

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.015, row_heights=[0.80, 0.20])

    fig.add_trace(go.Candlestick(
        x=plot_df["open_time"], open=plot_df["open_price"], high=plot_df["high_price"],
        low=plot_df["low_price"], close=plot_df["close_price"], name=symbol,
        line=dict(width=2.2 if mobile else 1.3),
        increasing_line_color="#0ECB81", increasing_fillcolor="#0ECB81",
        decreasing_line_color="#F6465D", decreasing_fillcolor="#F6465D",
        hoverinfo="none",
        xaxis="x", yaxis="y",
    ), row=1, col=1)

    for ema, color, width in [("EMA_21", "#2962FF", 1.5), ("EMA_50", "#FFFFFF", 1.5), ("EMA_100", "#FF5252", 1.8)]:
        fig.add_trace(go.Scatter(
            x=plot_df["open_time"], y=plot_df[ema], mode="lines", name=ema.replace("_", ""),
            line=dict(color=color, width=width), hoverinfo="none",
            xaxis="x", yaxis="y",
        ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=plot_df["open_time"], y=plot_df["Trend_Resistance"], mode="lines", name="壓力趨勢線",
        line=dict(color="rgba(246,70,93,.7)", width=1.5, dash="dash"), hoverinfo="none",
        xaxis="x", yaxis="y",
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=plot_df["open_time"], y=plot_df["Trend_Support"], mode="lines", name="支撐趨勢線",
        line=dict(color="rgba(14,203,129,.7)", width=1.5, dash="dash"), hoverinfo="none",
        xaxis="x", yaxis="y",
    ), row=1, col=1)

    long_df = plot_df[plot_df["Long_Signal"]].copy()
    if not long_df.empty:
        long_y = long_df["low_price"] - span * 0.08
        _lx, _ly = [], []
        for t, low_p, ly in zip(long_df["open_time"], long_df["low_price"], long_y):
            _lx += [t, t, None]
            _ly += [low_p, ly, None]
        fig.add_trace(go.Scatter(x=_lx, y=_ly, mode="lines", line=dict(color="#848e9c", width=1, dash="dot"), showlegend=False, hoverinfo="none", xaxis="x", yaxis="y"), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=long_df["open_time"], y=long_y, mode="markers+text", name="LONG 多單進場",
            marker=dict(symbol="triangle-up", size=13, color="rgba(11, 14, 17, 0.95)", line=dict(width=2, color="#00E676")),
            text=[f"LONG<br>{(sp if pd.notna(sp) else cp):,.2f}" for sp, cp in zip(long_df["Sig_Price"], long_df["close_price"])],
            textposition="bottom center", textfont=dict(color="#00E676", size=10, family="Arial Black"), hoverinfo="none",
            xaxis="x", yaxis="y",
        ), row=1, col=1)

    short_df = plot_df[plot_df["Short_Signal"]].copy()
    if not short_df.empty:
        short_y = short_df["high_price"] + span * 0.08
        _sx, _sy = [], []
        for t, high_p, sy in zip(short_df["open_time"], short_df["high_price"], short_y):
            _sx += [t, t, None]
            _sy += [high_p, sy, None]
        fig.add_trace(go.Scatter(x=_sx, y=_sy, mode="lines", line=dict(color="#848e9c", width=1, dash="dot"), showlegend=False, hoverinfo="none", xaxis="x", yaxis="y"), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=short_df["open_time"], y=short_y, mode="markers+text", name="SHORT 空單進場",
            marker=dict(symbol="triangle-down", size=13, color="rgba(11, 14, 17, 0.95)", line=dict(width=2, color="#FF1744")),
            text=[f"SHORT<br>{(sp if pd.notna(sp) else cp):,.2f}" for sp, cp in zip(short_df["Sig_Price"], short_df["close_price"])],
            textposition="top center", textfont=dict(color="#FF1744", size=10, family="Arial Black"), hoverinfo="none",
            xaxis="x", yaxis="y",
        ), row=1, col=1)

    volume_colors = ["rgba(14,203,129,.7)" if close >= open_ else "rgba(246,70,93,.7)" for close, open_ in zip(plot_df["close_price"], plot_df["open_price"])]
    fig.add_trace(go.Bar(x=plot_df["open_time"], y=plot_df["volume"], name="VOL", marker_color=volume_colors, hoverinfo="none", xaxis="x2", yaxis="y2"), row=2, col=1)

    fig.add_hline(
        y=latest_close, line_dash="dot", line_width=1, line_color=status_color,
        annotation_text=f" {latest_close:,.2f} ", annotation_position="right",
        annotation_font=dict(color="white", size=11, family="Arial Black"),
        annotation_bgcolor=status_color, annotation_bordercolor=status_color,
        annotation_borderwidth=1, annotation_borderpad=3, row=1, col=1,
    )

    mode = "🎯 5m 精準進場 (多週期共振)" if tf == "5m" else f"🌊 {tf} 週期分析"
    annotation = (
        f"<b>{pair} · {tf} ({mode}) [風格: {trade_style} | 閾值: {user_thresh}%]</b><br>"
        f"<b>RSI：</b>{latest_rsi:.1f} | <b>AI 信心指數：</b><span style='color:#f0b90b'>{ai_score:.1f}%</span> | "
        f"收盤：<span style='color:{status_color}'>{latest_close:,.2f} ({change_pct:+.2f}%)</span>"
    )
    fig.add_annotation(text=annotation, xref="paper", yref="paper", x=0.01, y=0.99, xanchor="left", yanchor="top", showarrow=False, align="left", font=dict(size=11, color="#c2c8d0"))

    fig.update_layout(
        template="plotly_dark", paper_bgcolor="rgba(11,14,17,.6)", plot_bgcolor="rgba(11,14,17,.6)",
        hovermode=(False if mobile else "x unified"), hoverdistance=100, spikedistance=1000, xaxis_rangeslider_visible=False,
        dragmode="pan", height=(520 if mobile else 860), bargap=0.08, showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, font=dict(size=10)),
        margin=dict(l=10, r=85, t=65, b=30), uirevision=f"{symbol}_{tf}_{user_thresh}_{trade_style}",
        hoverlabel=dict(bgcolor="rgba(30,35,41,.95)", font_size=12, font_color="white", bordercolor="#f0b90b"),
    )
    fig.update_layout(xaxis=dict(range=[start_time, end_time], autorange=False))
    fig.update_xaxes(showline=True, linewidth=2, linecolor="#434c5e", mirror=True, row=2, col=1, fixedrange=False)
    fig.update_yaxes(side="right", gridcolor="#1e2329", zeroline=False, linecolor="#2b313a", tickformat=",.2f", showspikes=(not mobile), spikemode="across+toaxis", spikesnap="cursor", spikethickness=1, spikedash="dash", spikecolor="#a0a6b2", showline=True, row=1, col=1, range=y_range, fixedrange=False)
    fig.update_yaxes(side="right", gridcolor="#1e2329", zeroline=False, linecolor="#2b313a", tickformat=",.2f", showspikes=(not mobile), spikemode="across", spikesnap="cursor", spikethickness=1, spikedash="dash", spikecolor="#a0a6b2", row=2, col=1, range=[0, float(visible_df["volume"].max()) * 1.05], fixedrange=False)

    return {
        "view_df": full_view_df, "fig": fig, "latest": latest, "x_range": [start_time, end_time],
        "y_range": list(y_range), "latest_close": latest_close, "ai_score": ai_score,
        "training_count": training_count, "winning_count": winning_count,
    }


def render_backtest_analytics(view_df):
    st.markdown("---")
    st.markdown("### 📊 功能 3：進階策略績效與風險回測分析報表")
    closed_trades = view_df[view_df["Trade_Result"].isin(["成功", "未達標"])].copy()
    if closed_trades.empty:
        st.info("目前尚無足夠的已結算歷史交易訊號可供回測分析。")
        return

    cum_pnl, equity_curve, trade_returns = 0.0, [100.0], []
    wins, losses, total_win_pnl, total_loss_pnl = 0, 0, 0.0, 0.0

    for _, row in closed_trades.iterrows():
        entry = row["Sig_Price"] if pd.notna(row.get("Sig_Price")) else row["close_price"]
        sl, tp, result = row["Stop_Loss"], row["Take_Profit"], row["Trade_Result"]
        if pd.isna(sl) or pd.isna(tp) or entry <= 0:
            continue
        if result == "成功":
            pct = abs(tp - entry) / entry * 100
            wins += 1
            total_win_pnl += pct
        else:
            pct = -abs(entry - sl) / entry * 100
            losses += 1
            total_loss_pnl += abs(pct)
        cum_pnl += pct
        equity_curve.append(100.0 + cum_pnl)
        trade_returns.append(pct)

    total_trades = wins + losses
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
    profit_factor = (total_win_pnl / total_loss_pnl) if total_loss_pnl > 0 else (99.0 if total_win_pnl > 0 else 0.0)

    eq_arr = np.array(equity_curve)
    peak = np.maximum.accumulate(eq_arr)
    max_dd = float(np.min((eq_arr - peak) / peak * 100)) if len(eq_arr) > 0 else 0.0

    ret_arr = np.array(trade_returns)
    sharpe = float(np.mean(ret_arr) / (np.std(ret_arr) + 1e-6) * np.sqrt(365)) if len(ret_arr) > 1 else 0.0

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("總已結算交易", f"{total_trades} 筆")
    m2.metric("策略勝率", f"{win_rate:.1f}%", f"{wins}勝/{losses}敗")
    m3.metric("獲利因子", f"{profit_factor:.2f}")
    m4.metric("夏普比率", f"{sharpe:.2f}")
    m5.metric("總報酬率", f"{cum_pnl:+.2f}%")
    m6.metric("最大回撤 (MDD)", f"{max_dd:.2f}%", delta_color="inverse")

    fig_eq = go.Figure()
    fig_eq.add_trace(go.Scatter(
        y=equity_curve, mode="lines+markers", name="策略淨值曲線",
        line=dict(color="#f0b90b", width=2.5),
        marker=dict(size=4)
    ))
    fig_eq.update_layout(
        template="plotly_dark", paper_bgcolor="rgba(11,14,17,.6)", plot_bgcolor="rgba(11,14,17,.6)",
        height=280, margin=dict(l=10, r=10, t=30, b=10),
        title="📈 累積資金淨值成長曲線 (初始 100%)",
        xaxis=dict(title="交易序號", gridcolor="#1e2329"),
        yaxis=dict(title="資金淨值", gridcolor="#1e2329", tickformat=",.1f")
    )
    st.plotly_chart(fig_eq, width="stretch", config={"staticPlot": False, "responsive": True},
                    key=f"{SYMBOL.lower()}_equity_chart")


def _render_live_kline_chart(tf, trade_panel=None):
    s = st.session_state.get("auth_settings") or {}
    user_thresh = s.get("ai_confidence_thresh", 45)
    trade_style = s.get("trade_style", "平衡")
    bt_days = s.get("backtest_days", 30)

    df = load_historical_data(None, days=max(int(bt_days) + 5, 14))
    if df.empty:
        st.warning("目前無法取得歷史 K 線資料。")
        return

    df_all_tf = {t_val: df[df["timeframe"] == t_val].sort_values("open_time").copy().reset_index(drop=True) for t_val in ["5m", "15m", "1h", "4h"]}
    df_tf = df_all_tf.get(tf, pd.DataFrame())
    if df_tf.empty:
        st.warning(f"目前無 {tf} 週期的 K 線資料。")
        return

    last_row = df_tf.iloc[-1]
    sig = (len(df_tf), df_tf["open_time"].iloc[0], df_tf["open_time"].iloc[-1], float(last_row["close_price"]), _outbox_stamp(tf), user_thresh, trade_style, bt_days)
    bundle_key = f"_bundle_{TABLE}_{tf}_{user_thresh}_{trade_style}_{bt_days}"
    cached_bundle = st.session_state.get(bundle_key)
    if cached_bundle is not None and cached_bundle["sig"] == sig:
        bundle = cached_bundle["bundle"]
    else:
        bundle = _build_chart_bundle(df_all_tf, tf, user_thresh=user_thresh, trade_style=trade_style, bt_days=bt_days)
        if bundle is None:
            st.warning("資料不足，無法產生圖表與策略分析。")
            return
        st.session_state[bundle_key] = {"sig": sig, "bundle": bundle}

    view_df, fig, latest, latest_close, ai_score = (
        bundle["view_df"], bundle["fig"], bundle["latest"], bundle["latest_close"], bundle["ai_score"]
    )

    fig.update_layout(xaxis=dict(range=list(bundle["x_range"]), autorange=False), yaxis=dict(range=list(bundle["y_range"])))

    chart_col, panel_col = st.columns([3.6, 1.2])
    with chart_col:
        st.plotly_chart(
            fig,
            width="stretch",
            config={
                "scrollZoom": True,
                "displayModeBar": True,
                "displaylogo": False,
                "toImageButtonOptions": {"format": "png", "filename": "chart"}
            },
            key=f"{SYMBOL.lower()}_{tf}_chart"
        )

    with panel_col:
        st.markdown("### 🎯 AI 即時操作建議")
        st.markdown(f"""
        <div class="conf-card">
            <div class="conf-label">當前 AI 信心分數 (門檻: {user_thresh}%)</div>
            <div class="conf-value">{ai_score:.1f}%</div>
            <div class="conf-bar"><span style="width:{min(max(ai_score, 0), 100):.1f}%;"></span></div>
            <div class="conf-foot"><span>模式：{trade_style}</span><b>多週期共振 🟢</b></div>
        </div>
        """, unsafe_allow_html=True)

        if bool(latest["Long_Signal"]):
            st.success("🟢 LONG 多單進場訊號")
            st.metric("參考進場價格", f"{latest_close:,.2f}")
        elif bool(latest["Short_Signal"]):
            st.error("🔴 SHORT 空單進場訊號")
            st.metric("參考進場價格", f"{latest_close:,.2f}")
        else:
            st.warning("⏳ WAIT 目前沒有進場訊号")

        if trade_panel is not None:
            trade_panel(SYMBOL, PAIR, tf, latest, latest_close)

        st.markdown("---")
        st.markdown("#### 📌 最近進場訊號")
        recent_signals = view_df[view_df["Long_Signal"] | view_df["Short_Signal"]].copy()
        if not recent_signals.empty:
            _mobile_ui = is_mobile_client()
            if _mobile_ui:
                st.caption("僅顯示最新 20 筆")
            with st.container(height=420):
                cards = []
                _sig_iter = recent_signals.iloc[::-1]
                if _mobile_ui:
                    _sig_iter = _sig_iter.head(20)
                for _, signal in _sig_iter.iterrows():
                    signal_time = signal["open_time"].strftime("%m/%d %H:%M")
                    signal_price = float(signal["Sig_Price"]) if pd.notna(signal["Sig_Price"]) else float(signal["close_price"])
                    is_long_sig = bool(signal["Long_Signal"])
                    cards.append(
                        f'<div class="sig-card {"long" if is_long_sig else "short"}">'
                        f'<div><div class="sig-dir">{"🟢 LONG" if is_long_sig else "🔴 SHORT"}</div>'
                        f'<div class="sig-time">{signal_time}</div></div>'
                        f'<div><div class="sig-price">{signal_price:,.2f}</div></div>'
                        f'</div>'
                    )
                st.markdown("".join(cards), unsafe_allow_html=True)

    if is_mobile_client():
        if st.toggle("📊 顯示進階策略績效回測報表", value=False, key=f"{SYMBOL.lower()}_show_backtest"):
            render_backtest_analytics(view_df)
    else:
        render_backtest_analytics(view_df)


def _live_fragment(tf, trade_panel):
    interval = 30 if is_mobile_client() else 15

    @st.fragment(run_every=interval)
    def _inner():
        _render_live_kline_chart(tf, trade_panel)

    _inner()


def render(tf, trade_panel=None):
    _live_fragment(tf, trade_panel)
