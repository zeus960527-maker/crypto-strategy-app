import streamlit as st
import pandas as pd
import numpy as np
from sqlalchemy import create_engine
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import lightgbm as lgb
import os
from PIL import Image

# ==================== 自動載入裝飾圖片 ====================
current_dir = os.path.dirname(os.path.abspath(__file__))

def load_image_safe(filename):
    path = os.path.join(current_dir, filename)
    try:
        return Image.open(path)
    except Exception:
        return None

icon_img = load_image_safe("hacker_icon.png")
header_banner = load_image_safe("header_banner.png")
side_deco = load_image_safe("side_deco.png")
bg_pattern = load_image_safe("bg_pattern.png")

# 頁面基本設定
st.set_page_config(
    page_title="加密貨幣AI策略系統",
    page_icon=icon_img if icon_img else "💻",
    layout="wide"
)

# 注入專業暗色系樣式
st.markdown("""
<style>
    .block-container { padding-top: 1.0rem; padding-bottom: 1rem; }
    div[data-testid="stRadio"] > div { flex-direction: row; gap: 12px; }
    
    /* 搜尋框樣式 */
    .stTextInput input { 
        background-color: #1e2329 !important; 
        color: #eaecef !important; 
        border: 1px solid #2b313a !important; 
    }
    .stTextInput input:focus { 
        background-color: #1e2329 !important;
        color: #eaecef !important;
        border: 1px solid #f0b90b !important; 
        box-shadow: none !important;
    }
    
    /* 禁止畫面變淡與 Loading 遮罩 */
    .stApp, .element-container, iframe, div[data-testid="stVerticalBlock"], div[data-testid="stFragment"] {
        opacity: 1 !important;
        transition: none !important;
        animation: none !important;
    }
    
    [data-testid="stStatusWidget"] {
        visibility: hidden;
        display: none;
    }

    /* Plotly 十字游標 */
    .js-plotly-plot .plotly .nsewdrag, 
    .js-plotly-plot .plotly .draglayer,
    .js-plotly-plot .plotly .plot-container {
        cursor: crosshair !important;
    }
    
    /* 自訂標題區樣式 */
    .main-title {
        font-size: 2.1rem;
        font-weight: 700;
        color: #eaecef;
        margin-bottom: 0.3rem;
    }
</style>
""", unsafe_allow_html=True)

# ==================== 0. 初始化 Session State ====================
if "is_searched" not in st.session_state:
    st.session_state.is_searched = False
if "search_query" not in st.session_state:
    st.session_state.search_query = ""

if "ai_models" not in st.session_state:
    st.session_state.ai_models = {}

# ==================== 1. 頂部標題 + Banner ====================
st.markdown('<div class="main-title">加密貨幣AI策略系統</div>', unsafe_allow_html=True)

# 標題下方橫幅
if header_banner:
    st.image(header_banner, use_container_width=True)
    st.markdown("<div style='margin-top: -12px; margin-bottom: 8px;'></div>", unsafe_allow_html=True)

with st.form(key="search_form"):
    col_f1, col_f2 = st.columns([4, 1])
    with col_f1:
        input_val = st.text_input("🔍 請輸入加密貨幣名稱 (例如 BTC 或 ETH)：", value=st.session_state.search_query)
    with col_f2:
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

# ==================== 2. 資料庫連線 ====================
DB_USER = "postgres.ygzeqvcyqtvhvjrxhzdv"
DB_PASSWORD = "%4010233960527Ken"
DB_HOST = "aws-0-ap-northeast-1.pooler.supabase.com"
DB_PORT = "5432"
DB_NAME = "postgres"

@st.cache_resource
def get_db_engine():
    db_url = f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    return create_engine(db_url)

def load_historical_data(table_name, symbol_name):
    try:
        engine = get_db_engine()
        query = f"SELECT timeframe, open_time, open_price, high_price, low_price, close_price, volume FROM {table_name} ORDER BY open_time DESC LIMIT 300"
        df = pd.read_sql(query, con=engine)
        if not df.empty:
            df = df.sort_values('open_time').reset_index(drop=True)
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

def calculate_trendline_channel(df, window=30, num_dev=1.8):
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

def train_or_get_ai_model(df_tf, key):
    if key in st.session_state.ai_models:
        return st.session_state.ai_models[key]
    
    if len(df_tf) < 60:
        return None
    
    try:
        df = df_tf.copy()
        df['vol_ratio'] = df['volume'] / df['volume'].rolling(20).mean()
        body_size = (df['close_price'] - df['open_price']).abs()
        candle_range = df['high_price'] - df['low_price']
        df['body_ratio'] = body_size / candle_range.replace(0, 1)
        df['atr_ratio'] = df['ATR'] / df['close_price']
        df['ema_diff'] = (df['EMA_21'] - df['EMA_50']) / df['close_price']
        
        future_max_gain = df['high_price'].shift(-3).rolling(3).max() / df['close_price'] - 1
        df['target'] = (future_max_gain > 0.012).astype(int)
        
        feature_cols = ['RSI', 'vol_ratio', 'body_ratio', 'atr_ratio', 'ema_diff']
        train_df = df.dropna(subset=feature_cols + ['target'])
        
        if len(train_df) < 30 or len(train_df['target'].unique()) < 2:
            return None
            
        X = train_df[feature_cols]
        y = train_df['target']
        
        model = lgb.LGBMClassifier(n_estimators=40, learning_rate=0.05, max_depth=3, verbose=-1)
        model.fit(X, y)
        
        st.session_state.ai_models[key] = model
        return model
    except Exception:
        return None

# ==================== 3. 主畫面 ====================
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
        
    selected_tf = st.radio("時間週期 (小波段/大波段)", ['5m', '15m', '1h', '4h'], horizontal=True, key="timeframe_select")
    
    @st.fragment(run_every=1)
    def render_live_kline_chart(tbl, sym, pair, tf):
        df = load_historical_data(tbl, sym)
        df_tf = df[df['timeframe'] == tf].sort_values('open_time').copy()
        
        if not df_tf.empty:
            df_tf['EMA_21'] = df_tf['close_price'].ewm(span=21, adjust=False).mean()
            df_tf['EMA_50'] = df_tf['close_price'].ewm(span=50, adjust=False).mean()
            df_tf['EMA_100'] = df_tf['close_price'].ewm(span=100, adjust=False).mean()

            win = 20 if tf in ['5m', '15m'] else 40
            dev = 1.5 if tf in ['5m', '15m'] else 2.0
            df_tf = calculate_trendline_channel(df_tf, window=win, num_dev=dev)
            
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

            df_tf['Vol_SMA20'] = df_tf['volume'].rolling(window=20).mean()
            body_size = (df_tf['close_price'] - df_tf['open_price']).abs()
            candle_range = df_tf['high_price'] - df_tf['low_price']
            df_tf['Body_Ratio'] = body_size / candle_range.replace(0, 1)

            if tf in ['5m', '15m']:
                df_tf['Base_Long'] = (
                    (df_tf['close_price'] > df_tf['Trend_Resistance']) & 
                    (df_tf['RSI'] >= 42) & (df_tf['RSI'] <= 85) &
                    (df_tf['volume'] > df_tf['Vol_SMA20'] * 1.05) &
                    (df_tf['Body_Ratio'] > 0.3)
                )
                df_tf['Base_Short'] = (
                    (df_tf['close_price'] < df_tf['Trend_Support']) & 
                    (df_tf['RSI'] >= 15) & (df_tf['RSI'] <= 58) &
                    (df_tf['volume'] > df_tf['Vol_SMA20'] * 1.05) &
                    (df_tf['Body_Ratio'] > 0.3)
                )
            else:
                df_tf['Base_Long'] = (
                    (df_tf['close_price'] > df_tf['Trend_Resistance']) & 
                    (df_tf['RSI'] >= 45) & (df_tf['RSI'] <= 80) &
                    (df_tf['volume'] > df_tf['Vol_SMA20'] * 1.0)
                )
                df_tf['Base_Short'] = (
                    (df_tf['close_price'] < df_tf['Trend_Support']) & 
                    (df_tf['RSI'] >= 20) & (df_tf['RSI'] <= 55) &
                    (df_tf['volume'] > df_tf['Vol_SMA20'] * 1.0)
                )

            model_key = f"{sym}_{tf}"
            ai_model = train_or_get_ai_model(df_tf, model_key)
            
            long_signals = []
            short_signals = []
            ai_probabilities = []
            
            for idx, row in df_tf.iterrows():
                is_l = row['Base_Long']
                is_s = row['Base_Short']
                prob = 0.70  
                
                if (is_l or is_s) and ai_model is not None:
                    try:
                        feat = pd.DataFrame([{
                            'RSI': row['RSI'],
                            'vol_ratio': row['volume'] / row['Vol_SMA20'] if row['Vol_SMA20'] > 0 else 1.0,
                            'body_ratio': row['Body_Ratio'],
                            'atr_ratio': row['ATR'] / row['close_price'] if not pd.isna(row['ATR']) else 0.015,
                            'ema_diff': (row['EMA_21'] - row['EMA_50']) / row['close_price']
                        }])
                        prob = ai_model.predict_proba(feat)[0][1]
                    except Exception:
                        pass
                
                ai_probabilities.append(prob)
                long_signals.append(is_l and prob >= 0.58)
                short_signals.append(is_s and prob >= 0.58)
                
            df_tf['Long_Signal'] = long_signals
            df_tf['Short_Signal'] = short_signals
            df_tf['AI_Prob'] = ai_probabilities

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
            latest_ai_prob = latest['AI_Prob'] * 100

            # 建立雙區塊圖表
            fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.015, row_heights=[0.75, 0.25])
            
            # K線
            fig.add_trace(go.Candlestick(
                x=df_tf['open_time'], open=df_tf['open_price'], high=df_tf['high_price'], low=df_tf['low_price'], close=df_tf['close_price'],
                name=sym, line=dict(width=1.5), 
                increasing_line_color='#0ECB81', increasing_fillcolor='#0ECB81',
                decreasing_line_color='#F6465D', decreasing_fillcolor='#F6465D',
                hoverinfo="none"
            ), row=1, col=1)
            
            # 技術指標
            fig.add_trace(go.Scatter(x=df_tf['open_time'], y=df_tf['EMA_21'], mode='lines', name='EMA21', line=dict(color='#2962FF', width=1.2), hoverinfo="none"), row=1, col=1)
            fig.add_trace(go.Scatter(x=df_tf['open_time'], y=df_tf['EMA_50'], mode='lines', name='EMA50', line=dict(color='#FFFFFF', width=1.2), hoverinfo="none"), row=1, col=1)
            fig.add_trace(go.Scatter(x=df_tf['open_time'], y=df_tf['EMA_100'], mode='lines', name='EMA100', line=dict(color='#FF5252', width=1.4), hoverinfo="none"), row=1, col=1)

            fig.add_trace(go.Scatter(x=df_tf['open_time'], y=df_tf['Trend_Resistance'], mode='lines', name='壓力趨勢線', line=dict(color='rgba(246, 70, 93, 0.7)', width=1.5, dash='dash'), hoverinfo="none"), row=1, col=1)
            fig.add_trace(go.Scatter(x=df_tf['open_time'], y=df_tf['Trend_Support'], mode='lines', name='支撐趨勢線', line=dict(color='rgba(14, 203, 129, 0.7)', width=1.5, dash='dash'), fill='tonexty', fillcolor='rgba(126, 87, 194, 0.02)', hoverinfo="none"), row=1, col=1)

            # AI 訊號
            long_df = df_tf[df_tf['Long_Signal']]
            if not long_df.empty:
                fig.add_trace(go.Scatter(
                    x=long_df['open_time'], y=long_df['low_price'] * 0.995, 
                    mode='markers+text', name='AI 多點', 
                    marker=dict(symbol='triangle-up', size=10, color='#0ECB81', line=dict(width=1, color='white')), 
                    text=["🟢 AI多" for _ in range(len(long_df))], textposition="bottom center",
                    textfont=dict(color='#0ECB81', size=11, family="Arial Black"),
                    hoverinfo="none"
                ), row=1, col=1)

            short_df = df_tf[df_tf['Short_Signal']]
            if not short_df.empty:
                fig.add_trace(go.Scatter(
                    x=short_df['open_time'], y=short_df['high_price'] * 1.005, 
                    mode='markers+text', name='AI 空點', 
                    marker=dict(symbol='triangle-down', size=10, color='#F6465D', line=dict(width=1, color='white')), 
                    text=["🔴 AI空" for _ in range(len(short_df))], textposition="top center",
                    textfont=dict(color='#F6465D', size=11, family="Arial Black"),
                    hoverinfo="none"
                ), row=1, col=1)

            # 成交量
            vol_colors = ['rgba(14, 203, 129, 0.5)' if c >= o else 'rgba(246, 70, 93, 0.5)' for c, o in zip(df_tf['close_price'], df_tf['open_price'])]
            fig.add_trace(go.Bar(x=df_tf['open_time'], y=df_tf['volume'], name='VOL', marker_color=vol_colors, hoverinfo="none"), row=2, col=1)

            latest_close = latest['close_price']
            
            fig.add_hline(
                y=latest_close, line_dash="dot", line_width=1, line_color=status_color, 
                annotation_text=f" {latest_close:,.2f} ", 
                annotation_position="right", 
                annotation_font=dict(color="white", size=11, family="Arial Black"),
                annotation_bgcolor=status_color,
                annotation_bordercolor=status_color,
                annotation_borderwidth=1,
                annotation_borderpad=3,
                row=1, col=1
            )

            mode_desc = "⚡ AI小波段閃電模式" if tf in ['5m', '15m'] else "🌊 AI大波段趨勢模式"
            ohlc_text = (
                f"<b>{pair} · {tf} ({mode_desc})</b><br>"
                f"<b>🛠️ 策略與技術指標:</b> 雙軌通道 (Window: {win}, Dev: {dev}) | EMA (21, 50, 100) | RSI (14) | ATR (14)<br>"
                f"<b>RSI:</b> {latest_rsi:.1f} | <b>🤖 AI勝率:</b> <span style='color:#f0b90b'>{latest_ai_prob:.1f}%</span> | "
                f"收: <span style='color:{status_color}'>{latest['close_price']:,.2f} ({change_pct:+.2f}%)</span>"
            )
            fig.add_annotation(
                text=ohlc_text, xref="paper", yref="paper", 
                x=0.01, y=0.99, xanchor="left", yanchor="top", 
                showarrow=False, align="left", font=dict(size=11, color="#848e9c")
            )

            fig.update_layout(
                template="plotly_dark", 
                paper_bgcolor="#0b0e11", 
                plot_bgcolor="#0b0e11", 
                hovermode="x",  
                hoverdistance=100,
                spikedistance=1000,
                xaxis_rangeslider_visible=False, 
                dragmode='pan', 
                height=820, 
                bargap=0.15, 
                showlegend=False,
                margin=dict(l=10, r=85, t=25, b=30), 
                uirevision="constant_lock_state"
            )
            
            fig.update_layout(xaxis=dict(range=[start_time, end_time], autorange=False))

            fig.update_xaxes(
                showticklabels=True, gridcolor="#1e2329", zeroline=False, linecolor="#2b313a",
                showspikes=True, spikemode='across', spikesnap='cursor', spikethickness=1, spikedash='dash', spikecolor='#a0a6b2'
            )
            fig.update_yaxes(
                side="right", gridcolor="#1e2329", zeroline=False, linecolor="#2b313a", tickformat=",.2f", 
                showspikes=True, spikemode='across', spikesnap='cursor', spikethickness=1, spikedash='dash', spikecolor='#a0a6b2',
                showline=True,
                row=1, col=1, range=y_range
            )
            fig.update_yaxes(
                side="right", gridcolor="#1e2329", zeroline=False, linecolor="#2b313a", tickformat=",.2f", 
                showspikes=True, spikemode='across', spikesnap='cursor', spikethickness=1, spikedash='dash', spikecolor='#a0a6b2',
                row=2, col=1
            )
            
            chart_config = {'scrollZoom': True, 'displayModeBar': True, 'displaylogo': False}
            
            chart_col, panel_col = st.columns([3.6, 1.2])
            with chart_col:
                st.plotly_chart(fig, use_container_width=True, config=chart_config, key=f"{sym.lower()}_supabase_live_chart")
            
            with panel_col:
                # 右側面板上方裝飾圖
                if side_deco:
                    st.image(side_deco, use_container_width=True)
                    st.markdown("<div style='margin-top: -8px;'></div>", unsafe_allow_html=True)
                
                st.markdown("### 🎯 AI 即時操作建議")
                st.markdown(f"""
                <div style="background-color: #1e2329; border: 1px solid #2b313a; padding: 10px; border-radius: 6px; margin-bottom: 10px;">
                    <div style="font-size: 13px; color: #848e9c;">當前 AI 模型信心評分</div>
                    <div style="font-size: 20px; font-weight: bold; color: #f0b90b;">{latest_ai_prob:.1f}%</div>
                </div>
                """, unsafe_allow_html=True)

                if df_tf.iloc[-1]['Long_Signal']:
                    st.markdown(f"""
                    <div style="background-color: #1e2329; border: 1px solid #0ECB81; padding: 12px; border-radius: 6px; border-left: 5px solid #0ECB81;">
                        <div style="color: #0ECB81; font-weight: bold;">🟢 AI 智能多單 (LONG)</div>
                        <div style="font-size: 12px;">进场: {latest_close:,.2f}</div>
                    </div>
                    """, unsafe_allow_html=True)
                elif df_tf.iloc[-1]['Short_Signal']:
                    st.markdown(f"""
                    <div style="background-color: #1e2329; border: 1px solid #F6465D; padding: 12px; border-radius: 6px; border-left: 5px solid #F6465D;">
                        <div style="color: #F6465D; font-weight: bold;">🔴 AI 智能空單 (SHORT)</div>
                        <div style="font-size: 12px;">进场: {latest_close:,.2f}</div>
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    st.markdown("""
                    <div style="background-color: #1e2329; border: 1px solid #2b313a; padding: 15px; border-radius: 6px; text-align: center; border-left: 5px solid #f0b90b;">
                        <div style="color: #f0b90b; font-weight: bold;">⏳ AI 觀望中 (WAIT)</div>
                        <div style="font-size: 11px; color: #848e9c; margin-top: 4px;">等待高勝率變盤契機</div>
                    </div>
                    """, unsafe_allow_html=True)
        else:
            st.warning("此週期目前尚無資料。")

    render_live_kline_chart(target_table, symbol_name, pair_name, selected_tf)

elif st.session_state.search_query != "":
    st.warning(f"找不到與「{st.session_state.search_query}」相關的圖表，請嘗試輸入 **BTC** 或 **ETH**。")
else:
    # ==================== 空狀態美化 ====================
    col_empty1, col_empty2, col_empty3 = st.columns([1, 2, 1])
    with col_empty2:
        if bg_pattern:
            st.image(bg_pattern, use_container_width=True)
        
        st.markdown("""
        <div style="text-align: center; margin-top: 10px;">
            <div style="font-size: 22px; font-weight: bold; color: #eaecef; margin-bottom: 8px;">
                🚀 準備就緒
            </div>
            <div style="font-size: 15px; color: #848e9c; line-height: 1.6;">
                請在上方查詢欄輸入 <b style="color:#f0b90b;">BTC</b> 或 <b style="color:#f0b90b;">ETH</b><br>
                按下送出後即可啟動 AI 即時策略分析
            </div>
        </div>
        """, unsafe_allow_html=True)
