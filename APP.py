import streamlit as st
import pandas as pd
import numpy as np
requests = None
try:
    import requests
except ImportError:
    pass
from sqlalchemy import create_engine
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# 頁面基本設定
st.set_page_config(page_title="加密貨幣超精準防假突破策略", layout="wide")

# 注入 OKX 專業暗色系樣式、介面微調與十字游標設定
st.markdown("""
<style>
    .block-container { padding-top: 1.2rem; padding-bottom: 1rem; }
    div[data-testid="stRadio"] > div { flex-direction: row; gap: 12px; }
    .stTextInput input { background-color: #1e2329; color: #eaecef; border: 1px solid #2b313a; }
    .stTextInput input:focus { border-color: #f0b90b; }
    /* 將 Plotly 圖表區域滑鼠游標強制改為十字 */
    .js-plotly-plot .plotly .nsewdrag, 
    .js-plotly-plot .plotly .draglayer,
    .js-plotly-plot .plotly .plot-container {
        cursor: crosshair !important;
    }
</style>
""", unsafe_allow_html=True)

# ==================== 0. 初始化 Session State ====================
if "is_searched" not in st.session_state:
    st.session_state.is_searched = False
if "search_query" not in st.session_state:
    st.session_state.search_query = ""
if "auto_scroll" not in st.session_state:
    st.session_state.auto_scroll = True  # 預設跟隨最新價
if "last_notified_times" not in st.session_state:
    st.session_state.last_notified_times = {}

# ==================== 1. 頂部標題與搜尋表單 ====================
st.title("加密貨幣分析 - 動態趨勢突破 + 視覺標記優化版")

with st.form(key="search_form"):
    col1, col2 = st.columns([4, 1])
    with col1:
        input_val = st.text_input("🔍 請輸入加密貨幣名稱 (例如 BTC 或 ETH)：", value=st.session_state.search_query)
    with col2:
        st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
        submitted = st.form_submit_button(label="送出查詢", use_container_width=True)

if submitted:
    query_upper = input_val.strip().upper()
    st.session_state.search_query = query_upper
    if "BTC" in query_upper or "ETH" in query_upper:
        st.session_state.is_searched = True
    else:
        st.session_state.is_searched = False

st.markdown("---")

# ==================== 2. Supabase 資料庫連線與工具函式 ====================
DB_USER = "postgres.ygzeqvcyqtvhvjrxhzdv"
DB_PASSWORD = "%4010233960527Ken"
DB_HOST = "aws-0-ap-northeast-1.pooler.supabase.com"
DB_PORT = "5432"
DB_NAME = "postgres"

DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1571457845772165170/MZt_7HpNi9VD_peLI8ttwP_EXlAD5gAePTNSPff65whlJ-IMdv9qbPRnVy22npwjftI7"

def send_discord_alert(message):
    """發送 Discord Webhook 通知"""
    try:
        payload = {"content": message}
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
        return response.status_code == 204
    except Exception as e:
        print(f"Discord 推播失敗: {e}")
        return False

@st.cache_data(ttl=10)
def load_historical_data(table_name, symbol_name):
    """從 Supabase (PostgreSQL) 讀取 K 線資料，並精準解析時間戳記轉換為台灣時間 (UTC+8)"""
    try:
        db_url = f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
        engine = create_engine(db_url)
        query = f"SELECT timeframe, open_time, open_price, high_price, low_price, close_price, volume FROM {table_name}"
        df = pd.read_sql(query, con=engine)
    except Exception as e:
        st.error(f"Supabase 連線或讀取錯誤: {e}")
        return pd.DataFrame()
    
    df['symbol'] = symbol_name
    
    if not df.empty and 'open_time' in df.columns:
        if pd.api.types.is_numeric_dtype(df['open_time']):
            sample_val = df['open_time'].dropna().iloc[0] if not df['open_time'].empty else 0
            unit_type = 'ms' if sample_val > 10_000_000_000 else 's'
            df['open_time'] = pd.to_datetime(df['open_time'], unit=unit_type, errors='coerce')
        else:
            df['open_time'] = pd.to_datetime(df['open_time'], errors='coerce')

        if df['open_time'].dt.tz is None:
            df['open_time'] = df['open_time'].dt.tz_localize('UTC').dt.tz_convert('Asia/Taipei').dt.tz_localize(None)
        else:
            df['open_time'] = df['open_time'].dt.tz_convert('Asia/Taipei').dt.tz_localize(None)
    
    price_columns = ['open_price', 'high_price', 'low_price', 'close_price', 'volume']
    for col in price_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
            
    return df

def get_htf_trend_dataframe(df_all, current_tf):
    """根據當前時間週期自動對應大時間級別 (HTF)，計算大週期趨勢並與主表對齊"""
    hierarchy = {'1m': '15m', '5m': '1h', '15m': '4h', '30m': '4h', '1h': '4h', '4h': '1d', '1d': '1d'}
    htf = hierarchy.get(current_tf, '4h')
    
    available_tfs = df_all['timeframe'].unique()
    if htf not in available_tfs:
        htf = current_tf
        
    df_htf = df_all[df_all['timeframe'] == htf].sort_values('open_time').copy()
    if df_htf.empty:
        df_all['HTF_Trend'] = 1
        df_all['HTF_Name'] = current_tf
        return df_all
        
    df_htf['HTF_EMA21'] = df_htf['close_price'].ewm(span=21, adjust=False).mean()
    df_htf['HTF_EMA50'] = df_htf['close_price'].ewm(span=50, adjust=False).mean()
    df_htf['HTF_Trend'] = (df_htf['HTF_EMA21'] > df_htf['HTF_EMA50']).astype(int) * 2 - 1
    df_htf['HTF_Name'] = htf
    
    return df_htf[['open_time', 'HTF_Trend', 'HTF_Name']]

def calculate_trendline_channel(df, window=35, num_dev=2.0):
    """計算動態線性回歸趨勢線與通道"""
    close = df['close_price'].values
    n = len(close)
    mid = np.zeros(n)
    upper = np.zeros(n)
    lower = np.zeros(n)
    
    for i in range(n):
        if i < window:
            mid[i] = close[i]
            upper[i] = close[i]
            lower[i] = close[i]
        else:
            y = close[i-window+1:i+1]
            x = np.arange(window)
            slope, intercept = np.polyfit(x, y, 1)
            mid[i] = slope * (window - 1) + intercept
            std = np.std(y - (slope * x + intercept))
            upper[i] = mid[i] + num_dev * std
            lower[i] = mid[i] - num_dev * std
            
    df['Trend_Mid'] = mid
    df['Trend_Resistance'] = upper  
    df['Trend_Support'] = lower     
    return df

# ==================== 3. 全域背景即時多空信號與風控偵測 ====================
@st.fragment(run_every=1)
def global_background_signal_scanner():
    """背景持續自動掃描 Supabase 中的 K 線資料"""
    all_monitoring_targets = [
        ('btc_contract_klines', 'BTC', 'BTC-USDT-SWAP'),
        ('eth_contract_klines', 'ETH', 'ETH-USDT-SWAP')
    ]
    
    for m_tbl, m_sym, m_pair in all_monitoring_targets:
        try:
            df_m = load_historical_data(m_tbl, m_sym)
            if df_m.empty:
                continue
            for m_tf in df_m['timeframe'].unique():
                df_m_tf = df_m[df_m['timeframe'] == m_tf].sort_values('open_time').copy()
                if df_m_tf.empty:
                    continue
                
                df_htf_part = get_htf_trend_dataframe(df_m, m_tf)
                df_m_tf = pd.merge_asof(
                    df_m_tf.sort_values('open_time'),
                    df_htf_part.sort_values('open_time'),
                    on='open_time',
                    direction='backward'
                )
                df_m_tf['HTF_Trend'] = df_m_tf['HTF_Trend'].fillna(1)

                df_m_tf['EMA_21'] = df_m_tf['close_price'].ewm(span=21, adjust=False).mean()
                df_m_tf['EMA_50'] = df_m_tf['close_price'].ewm(span=50, adjust=False).mean()
                
                df_m_tf['Vol_MA20'] = df_m_tf['volume'].rolling(window=20).mean()
                df_m_tf = calculate_trendline_channel(df_m_tf, window=35, num_dev=2.0)
                
                delta = df_m_tf['close_price'].diff()
                gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
                rs = gain / loss
                df_m_tf['RSI'] = 100 - (100 / (1 + rs))

                high_low = df_m_tf['high_price'] - df_m_tf['low_price']
                high_close = (df_m_tf['high_price'] - df_m_tf['close_price'].shift()).abs()
                low_close = (df_m_tf['low_price'] - df_m_tf['close_price'].shift()).abs()
                tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
                df_m_tf['ATR'] = tr.rolling(window=14).mean()
                
                # 🎯【優化後的靈敏進場邏輯】：只要價格突破通道且均線多頭/空頭排列即標記
                df_m_tf['Long_Signal'] = (
                    (df_m_tf['close_price'] > df_m_tf['Trend_Resistance']) & 
                    (df_m_tf['EMA_21'] > df_m_tf['EMA_50']) &
                    (df_m_tf['RSI'] >= 45)
                )
                
                df_m_tf['Short_Signal'] = (
                    (df_m_tf['close_price'] < df_m_tf['Trend_Support']) & 
                    (df_m_tf['EMA_21'] < df_m_tf['EMA_50']) &
                    (df_m_tf['RSI'] <= 55)
                )

                # --- 檢查做多訊號 ---
                long_key = f"{m_sym}_{m_tf}_LONG"
                long_signals = df_m_tf[df_m_tf['Long_Signal']]
                if long_key not in st.session_state.last_notified_times:
                    st.session_state.last_notified_times[long_key] = long_signals.iloc[-1]['open_time'] if not long_signals.empty else None

                if not long_signals.empty:
                    latest_long = long_signals.iloc[-1]
                    long_time = latest_long['open_time']
                    if st.session_state.last_notified_times[long_key] != long_time:
                        st.session_state.last_notified_times[long_key] = long_time
                        entry = latest_long['close_price']
                        atr_val = latest_long['ATR'] if not pd.isna(latest_long['ATR']) else entry * 0.015
                        sl = entry - (atr_val * 1.5)
                        tp = entry + (atr_val * 1.5 * 2.5)
                        msg = (
                            f"🚨 **【Supabase策略 - 做多 LONG】** 🚨\n"
                            f"📌 **標的:** {m_pair} ({m_tf})\n"
                            f"🕒 **時間:** `{long_time.strftime('%Y-%m-%d %H:%M:%S')}`\n"
                            f"🟢 **精準進場價:** `{entry:,.2f}`\n"
                            f"🔴 **止損價 (SL):** `{sl:,.2f}`\n"
                            f"🎯 **止盈價 (TP):** `{tp:,.2f}` (1:2.5 盈虧比)"
                        )
                        send_discord_alert(msg)

                # --- 檢查做空訊號 ---
                short_key = f"{m_sym}_{m_tf}_SHORT"
                short_signals = df_m_tf[df_m_tf['Short_Signal']]
                if short_key not in st.session_state.last_notified_times:
                    st.session_state.last_notified_times[short_key] = short_signals.iloc[-1]['open_time'] if not short_signals.empty else None

                if not short_signals.empty:
                    latest_short = short_signals.iloc[-1]
                    short_time = latest_short['open_time']
                    if st.session_state.last_notified_times[short_key] != short_time:
                        st.session_state.last_notified_times[short_key] = short_time
                        entry = latest_short['close_price']
                        atr_val = latest_short['ATR'] if not pd.isna(latest_short['ATR']) else entry * 0.015
                        sl = entry + (atr_val * 1.5)
                        tp = entry - (atr_val * 1.5 * 2.5)
                        msg = (
                            f"🚨 **【Supabase策略 - 做空 SHORT】** 🚨\n"
                            f"📌 **標的:** {m_pair} ({m_tf})\n"
                            f"🕒 **時間:** `{short_time.strftime('%Y-%m-%d %H:%M:%S')}`\n"
                            f"🔴 **精準進場價:** `{entry:,.2f}`\n"
                            f"🟢 **止損價 (SL):** `{sl:,.2f}`\n"
                            f"🎯 **止盈價 (TP):** `{tp:,.2f}` (1:2.5 盈虧比)"
                        )
                        send_discord_alert(msg)

        except Exception as e:
            print(f"背景掃描錯誤 ({m_sym}): {e}")

global_background_signal_scanner()

# ==================== 4. 主畫面渲染邏輯 ====================
if st.session_state.is_searched:
    query_str = st.session_state.search_query.upper()
    
    if "ETH" in query_str:
        target_table = 'eth_contract_klines'
        symbol_name = 'ETH'
        pair_name = 'ETH-USDT-SWAP'
    else:
        target_table = 'btc_contract_klines'
        symbol_name = 'BTC'
        pair_name = 'BTC-USDT-SWAP'
        
    df_initial = load_historical_data(target_table, symbol_name)
    
    if not df_initial.empty:
        c1, c2, c3 = st.columns([2.5, 2, 1.5])
        with c1:
            timeframes = list(df_initial['timeframe'].unique())
            selected_tf = st.radio("時間週期", timeframes, horizontal=True, key="timeframe_select")
        with c2:
            kline_ratio = st.slider("主圖 / 量佔比", min_value=0.5, max_value=0.9, value=0.75, step=0.05, key="ratio_slider")
        with c3:
            st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
            auto_scroll = st.toggle("📍 跟隨最新價", value=st.session_state.auto_scroll, key="auto_scroll_toggle")
            st.session_state.auto_scroll = auto_scroll
        
        volume_ratio = 1.0 - kline_ratio
        
        @st.fragment(run_every=1)
        def render_okx_live_chart(tbl, sym, pair, tf, k_ratio, v_ratio, follow_latest):
            df = load_historical_data(tbl, sym)
            df_tf = df[df['timeframe'] == tf].sort_values('open_time').copy()
            
            if not df_tf.empty:
                df_htf_part = get_htf_trend_dataframe(df, tf)
                df_tf = pd.merge_asof(
                    df_tf.sort_values('open_time'),
                    df_htf_part.sort_values('open_time'),
                    on='open_time',
                    direction='backward'
                )
                df_tf['HTF_Trend'] = df_tf['HTF_Trend'].fillna(1)
                htf_label = df_tf['HTF_Name'].iloc[-1] if 'HTF_Name' in df_tf.columns else 'HTF'
                htf_status_text = "🟢 多頭向上" if df_tf['HTF_Trend'].iloc[-1] == 1 else "🔴 空頭向下"

                df_tf['EMA_21'] = df_tf['close_price'].ewm(span=21, adjust=False).mean()
                df_tf['EMA_50'] = df_tf['close_price'].ewm(span=50, adjust=False).mean()

                df_tf['Vol_MA20'] = df_tf['volume'].rolling(window=20).mean()
                df_tf = calculate_trendline_channel(df_tf, window=35, num_dev=2.0)
                
                delta = df_tf['close_price'].diff()
                gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
                rs = gain / loss
                df_tf['RSI'] = 100 - (100 / (1 + rs))

                high_low = df_tf['high_price'] - df_tf['low_price']
                high_close = (df_tf['high_price'] - df_tf['close_price'].shift()).abs()
                low_close = (df_tf['low_price'] - df_tf['close_price'].shift()).abs()
                tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
                df_tf['ATR'] = tr.rolling(window=14).mean()

                # 🎯【圖表端同步優化條件】
                df_tf['Long_Signal'] = (
                    (df_tf['close_price'] > df_tf['Trend_Resistance']) & 
                    (df_tf['EMA_21'] > df_tf['EMA_50']) &
                    (df_tf['RSI'] >= 45)
                )
                df_tf['Short_Signal'] = (
                    (df_tf['close_price'] < df_tf['Trend_Support']) & 
                    (df_tf['EMA_21'] < df_tf['EMA_50']) &
                    (df_tf['RSI'] <= 55)
                )

                display_count = 90 if sym == 'ETH' else 120
                default_view_df = df_tf.tail(display_count)
                interval = default_view_df['open_time'].diff().median() if len(default_view_df) > 1 else pd.Timedelta(minutes=1)
                
                start_time = default_view_df['open_time'].iloc[0] - (interval * 5)
                end_time = default_view_df['open_time'].iloc[-1] + (interval * 10)

                vis_low = min(default_view_df['low_price'].min(), default_view_df['Trend_Support'].min() if not default_view_df['Trend_Support'].isna().all() else default_view_df['low_price'].min())
                vis_high = max(default_view_df['high_price'].max(), default_view_df['Trend_Resistance'].max() if not default_view_df['Trend_Resistance'].isna().all() else default_view_df['high_price'].max())
                y_span = vis_high - vis_low
                y_range = [vis_low - y_span * 0.03, vis_high + y_span * 0.03]

                latest = df_tf.iloc[-1]
                change_pct = ((latest['close_price'] - latest['open_price']) / latest['open_price']) * 100
                status_color = "#0ECB81" if latest['close_price'] >= latest['open_price'] else "#F6465D"
                latest_rsi = latest['RSI'] if not pd.isna(latest['RSI']) else 50.0

                fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.015, row_heights=[k_ratio, v_ratio])
                
                fig.add_trace(go.Candlestick(
                    x=df_tf['open_time'], open=df_tf['open_price'], high=df_tf['high_price'], low=df_tf['low_price'], close=df_tf['close_price'],
                    name=sym, line=dict(width=1.5), 
                    increasing_line_color='#0ECB81', increasing_fillcolor='#0ECB81',
                    decreasing_line_color='#F6465D', decreasing_fillcolor='#F6465D'
                ), row=1, col=1)
                
                fig.add_trace(go.Scatter(x=df_tf['open_time'], y=df_tf['EMA_21'], mode='lines', name='EMA21', line=dict(color='#2962FF', width=1.2)), row=1, col=1)
                fig.add_trace(go.Scatter(x=df_tf['open_time'], y=df_tf['EMA_50'], mode='lines', name='EMA50', line=dict(color='#FFB300', width=1.2)), row=1, col=1)

                fig.add_trace(go.Scatter(x=df_tf['open_time'], y=df_tf['Trend_Resistance'], mode='lines', name='壓力趨勢線', line=dict(color='rgba(246, 70, 93, 0.7)', width=1.5, dash='dash')), row=1, col=1)
                fig.add_trace(go.Scatter(x=df_tf['open_time'], y=df_tf['Trend_Support'], mode='lines', name='支撐趨勢線', line=dict(color='rgba(14, 203, 129, 0.7)', width=1.5, dash='dash'), fill='tonexty', fillcolor='rgba(126, 87, 194, 0.02)'), row=1, col=1)

                # 🎯【視覺化優化】：將買賣點明確標記在 K 線實體附近，並放大顯示
                long_df = df_tf[df_tf['Long_Signal']]
                if not long_df.empty:
                    fig.add_trace(go.Scatter(
                        x=long_df['open_time'], 
                        y=long_df['low_price'] * 0.995, 
                        mode='markers+text', 
                        name='做多買點', 
                        marker=dict(symbol='triangle-up', size=16, color='#0ECB81', line=dict(width=1, color='white')), 
                        text=["🟢 多" for _ in range(len(long_df))], 
                        textposition="bottom center",
                        textfont=dict(color='#0ECB81', size=12, family="Arial Black")
                    ), row=1, col=1)

                short_df = df_tf[df_tf['Short_Signal']]
                if not short_df.empty:
                    fig.add_trace(go.Scatter(
                        x=short_df['open_time'], 
                        y=short_df['high_price'] * 1.005, 
                        mode='markers+text', 
                        name='做空賣點', 
                        marker=dict(symbol='triangle-down', size=16, color='#F6465D', line=dict(width=1, color='white')), 
                        text=["🔴 空" for _ in range(len(short_df))], 
                        textposition="top center",
                        textfont=dict(color='#F6465D', size=12, family="Arial Black")
                    ), row=1, col=1)

                vol_colors = ['rgba(14, 203, 129, 0.5)' if c >= o else 'rgba(246, 70, 93, 0.5)' for c, o in zip(df_tf['close_price'], df_tf['open_price'])]
                fig.add_trace(go.Bar(x=df_tf['open_time'], y=df_tf['volume'], name='VOL', marker_color=vol_colors), row=2, col=1)

                latest_close = latest['close_price']
                fig.add_hline(y=latest_close, line_dash="dot", line_width=1, line_color=status_color, annotation_text=f" {latest_close:,.2f} ", annotation_position="right", row=1, col=1)

                active_signal_type = 'None'
                latest_atr = latest['ATR'] if not pd.isna(latest['ATR']) else latest_close * 0.015

                if df_tf.iloc[-1]['Long_Signal']:
                    active_signal_type = 'LONG'
                    active_entry, active_sl, active_tp = latest_close, latest_close - (latest_atr * 1.5), latest_close + (latest_atr * 1.5 * 2.5)
                elif df_tf.iloc[-1]['Short_Signal']:
                    active_signal_type = 'SHORT'
                    active_entry, active_sl, active_tp = latest_close, latest_close + (latest_atr * 1.5), latest_close - (latest_atr * 1.5 * 2.5)

                if active_signal_type != 'None':
                    fig.add_hline(y=active_entry, line_dash="dash", line_color="#f0b90b", annotation_text=f" 進場: {active_entry:,.2f} ", annotation_position="left", row=1, col=1)
                    fig.add_hline(y=active_sl, line_dash="dash", line_color="#F6465D", annotation_text=f" ATR止損: {active_sl:,.2f} ", annotation_position="left", row=1, col=1)
                    fig.add_hline(y=active_tp, line_dash="dash", line_color="#0ECB81", annotation_text=f" ATR止盈: {active_tp:,.2f} ", annotation_position="left", row=1, col=1)

                ohlc_text = (
                    f"<b>{pair} · {tf} (Supabase)</b> &nbsp;&nbsp; <b>大週期({htf_label}):</b> {htf_status_text} &nbsp;&nbsp; <b>RSI(14):</b> {latest_rsi:.1f}<br>"
                    f"收: <span style='color:{status_color}'>{latest['close_price']:,.2f} ({change_pct:+.2f}%)</span>"
                )
                fig.add_annotation(text=ohlc_text, xref="paper", yref="paper", x=0.01, y=0.98, xanchor="left", yanchor="top", showarrow=False, font=dict(size=11, color="#848e9c"))

                fig.update_layout(
                    template="plotly_dark", paper_bgcolor="#0b0e11", plot_bgcolor="#0b0e11", hovermode="closest",
                    xaxis_rangeslider_visible=False, dragmode='pan', height=820, bargap=0.15, showlegend=False,
                    margin=dict(l=10, r=85, t=25, b=30), uirevision=f"{sym}_{tf}_v20"
                )
                if follow_latest:
                    fig.update_layout(xaxis=dict(range=[start_time, end_time], autorange=False))

                fig.update_xaxes(showticklabels=True, gridcolor="#1e2329", zeroline=False, linecolor="#2b313a")
                fig.update_yaxes(side="right", gridcolor="#1e2329", zeroline=False, linecolor="#2b313a", tickformat=",.2f", showspikes=True, row=1, col=1, range=y_range)
                fig.update_yaxes(side="right", gridcolor="#1e2329", zeroline=False, linecolor="#2b313a", tickformat=",.2f", row=2, col=1)
                
                chart_config = {'scrollZoom': True, 'displayModeBar': True, 'displaylogo': False}
                
                chart_col, panel_col = st.columns([3.6, 1.2])
                with chart_col:
                    st.plotly_chart(fig, use_container_width=True, config=chart_config, key=f"{sym.lower()}_supabase_chart_key")
                
                with panel_col:
                    st.markdown("### 🎯 即時操作建議")
                    if df_tf.iloc[-1]['Long_Signal']:
                        st.markdown(f"""
                        <div style="background-color: #1e2329; border: 1px solid #0ECB81; padding: 12px; border-radius: 6px; border-left: 5px solid #0ECB81;">
                            <div style="color: #0ECB81; font-weight: bold;">🟢 做多 (LONG)</div>
                            <div style="font-size: 12px;">進場: {latest_close:,.2f}</div>
                        </div>
                        """, unsafe_allow_html=True)
                    elif df_tf.iloc[-1]['Short_Signal']:
                        st.markdown(f"""
                        <div style="background-color: #1e2329; border: 1px solid #F6465D; padding: 12px; border-radius: 6px; border-left: 5px solid #F6465D;">
                            <div style="color: #F6465D; font-weight: bold;">🔴 做空 (SHORT)</div>
                            <div style="font-size: 12px;">進場: {latest_close:,.2f}</div>
                        </div>
                        """, unsafe_allow_html=True)
                    else:
                        st.markdown("""
                        <div style="background-color: #1e2329; border: 1px solid #2b313a; padding: 15px; border-radius: 6px; text-align: center; border-left: 5px solid #f0b90b;">
                            <div style="color: #f0b90b; font-weight: bold;">⏳ 場外觀望 (WAIT)</div>
                        </div>
                        """, unsafe_allow_html=True)
            else:
                st.warning("此週期目前尚無資料。")

        render_okx_live_chart(target_table, symbol_name, pair_name, selected_tf, kline_ratio, volume_ratio, auto_scroll)
    else:
        st.warning("Supabase 資料庫中目前尚無資料。")

elif st.session_state.search_query != "":
    st.warning(f"找不到與「{st.session_state.search_query}」相關的圖表，請嘗試輸入 **BTC** 或者 **ETH**。")
else:
    st.info("💡 請在上方查詢欄輸入 **BTC** 或 **ETH** 並按下 **Enter** 顯示圖表。")
