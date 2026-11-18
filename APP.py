import streamlit as st
import pandas as pd
import numpy as np
from sqlalchemy import create_engine
import streamlit.components.v1 as components
import lightgbm as lgb
import json

# 頁面基本設定
st.set_page_config(page_title="加密貨幣AI策略系統", layout="wide")

# 注入專業暗色系樣式、強制取消所有畫面變淡與載入遮罩，並修復搜尋框點擊變灰的問題
st.markdown("""
<style>
    .block-container { padding-top: 1.2rem; padding-bottom: 1rem; }
    div[data-testid="stRadio"] > div { flex-direction: row; gap: 12px; }
    
    /* 搜尋框預設與點擊聚焦（Focus）樣式，防止點擊時變成灰色 */
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
    
    /* 絕對禁止任何畫面變淡、閃爍與 Loading 遮罩 */
    .stApp, .element-container, iframe, div[data-testid="stVerticalBlock"], div[data-testid="stFragment"] {
        opacity: 1 !important;
        transition: none !important;
        animation: none !important;
    }
    
    /* 隱藏右上角的載入轉圈動畫 */
    [data-testid="stStatusWidget"] {
        visibility: hidden;
        display: none;
    }
</style>
""", unsafe_allow_html=True)

# ==================== 0. 初始化 Session State ====================
if "is_searched" not in st.session_state:
    st.session_state.is_searched = False
if "search_query" not in st.session_state:
    st.session_state.search_query = ""
if "auto_scroll" not in st.session_state:
    st.session_state.auto_scroll = True

# 全域 AI 模型快取
if "ai_models" not in st.session_state:
    st.session_state.ai_models = {}

# ==================== 1. 頂部標題與搜尋表單 ====================
st.title("加密貨幣AI策略系統")

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

# ==================== 2. 建立帶快取的 SQLAlchemy Engine 連線 ====================
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

# ==================== 3. 主畫面與即時局部刷新區塊 ====================
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
        
    c1, c2, c3 = st.columns([2.5, 2, 1.5])
    with c1:
        selected_tf = st.radio("時間週期 (小波段/大波段)", ['5m', '15m', '1h', '4h'], horizontal=True, key="timeframe_select")
    with c2:
        kline_ratio = st.slider("主圖 / 量佔比", min_value=0.5, max_value=0.9, value=0.75, step=0.05, key="ratio_slider")
    with c3:
        st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
        auto_scroll = st.toggle("📍 跟隨最新價", value=st.session_state.auto_scroll, key="auto_scroll_toggle")
        st.session_state.auto_scroll = auto_scroll
    
    volume_ratio = 1.0 - kline_ratio
    
    @st.fragment(run_every=1)
    def render_live_kline_chart(tbl, sym, pair, tf, k_ratio, v_ratio, follow_latest):
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

            latest = df_tf.iloc[-1]
            latest_close = latest['close_price']
            status_color = "#0ECB81" if latest['close_price'] >= latest['open_price'] else "#F6465D"
            latest_rsi = latest['RSI'] if not pd.isna(latest['RSI']) else 50.0
            latest_ai_prob = latest['AI_Prob'] * 100

            # 準備傳遞給前端 Lightweight Charts 的 K 線資料
            chart_data = []
            for _, row in df_tf.iterrows():
                time_val = int(row['open_time'].timestamp())
                chart_data.append({
                    'time': time_val,
                    'open': float(row['open_price']),
                    'high': float(row['high_price']),
                    'low': float(row['low_price']),
                    'close': float(row['close_price'])
                })

            chart_json = json.dumps(chart_data)

            # 嵌入 TradingView 輕量圖表 (Lightweight Charts)
            tv_html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <script type="text/javascript" src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>
                <style>
                    body, html {{ margin: 0; padding: 0; width: 100%; height: 100%; background-color: #0b0e11; overflow: hidden; font-family: sans-serif; }}
                    #chart-container {{ width: 100%; height: 820px; position: relative; }}
                    
                    /* 右上角策略指標按鈕 */
                    #strategy-legend {{
                        position: absolute;
                        top: 10px;
                        right: 15px;
                        background: rgba(30, 35, 41, 0.85);
                        border: 1px solid #2b313a;
                        padding: 6px 12px;
                        border-radius: 4px;
                        color: #eaecef;
                        font-size: 12px;
                        cursor: pointer;
                        z-index: 100;
                        user-select: none;
                        transition: border-color 0.2s;
                    }}
                    #strategy-legend:hover {{ border-color: #f0b90b; }}
                    
                    /* 參數設定彈跳視窗 */
                    #modal-overlay {{
                        display: none;
                        position: absolute;
                        top: 0; left: 0; width: 100%; height: 100%;
                        background: rgba(0,0,0,0.6);
                        z-index: 1000;
                        justify-content: center;
                        align-items: center;
                    }}
                    #modal-content {{
                        background: #1e2329;
                        border: 1px solid #2b313a;
                        padding: 20px;
                        border-radius: 8px;
                        width: 320px;
                        color: #eaecef;
                    }}
                    #modal-content h3 {{ margin-top: 0; color: #f0b90b; font-size: 15px; }}
                    .param-row {{ display: flex; justify-content: space-between; margin-bottom: 10px; font-size: 13px; }}
                    .param-row span {{ color: #848e9c; }}
                    #close-modal {{
                        background: #f0b90b;
                        color: #000;
                        border: none;
                        padding: 6px 12px;
                        border-radius: 4px;
                        cursor: pointer;
                        font-weight: bold;
                        width: 100%;
                        margin-top: 10px;
                    }}
                </style>
            </head>
            <body>
                <div id="chart-container">
                    <div id="strategy-legend" title="雙擊查看策略指標參數">
                        📈 策略: 雙軌回歸通道 + AI (RSI, ATR, EMA) ⚙️
                    </div>
                </div>

                <div id="modal-overlay">
                    <div id="modal-content">
                        <h3>🔧 策略指標參數設定</h3>
                        <div class="param-row"><span>通道窗口 (Window):</span> <b>{win}</b></div>
                        <div class="param-row"><span>標準差倍數 (Dev):</span> <b>{dev}</b></div>
                        <div class="param-row"><span>RSI 週期:</span> <b>14</b></div>
                        <div class="param-row"><span>EMA 快速/慢速:</span> <b>21 / 50 / 100</b></div>
                        <div class="param-row"><span>AI 勝率過濾門檻:</span> <b>58%</b></div>
                        <button id="close-modal">關閉</button>
                    </div>
                </div>

                <script>
                    const chartData = {chart_json};
                    const latestClose = {latest_close};
                    const statusColor = "{status_color}";

                    const container = document.getElementById('chart-container');
                    // 保底寬度計算，確保圖表正常渲染
                    const chartWidth = container.clientWidth > 0 ? container.clientWidth : (window.innerWidth - 350);

                    const chart = LightweightCharts.createChart(container, {{
                        width: chartWidth,
                        height: 820,
                        layout: {{
                            background: {{ type: 'solid', color: '#0b0e11' }},
                            textColor: '#848e9c',
                        }},
                        grid: {{
                            vertLines: {{ color: '#1e2329' }},
                            horzLines: {{ color: '#1e2329' }},
                        }},
                        crosshair: {{
                            mode: LightweightCharts.CrosshairMode.Normal,
                            vertLine: {{
                                color: '#787f8b',
                                width: 1,
                                style: LightweightCharts.LineStyle.Dashed,
                                labelBackgroundColor: '#1e2329',
                            }},
                            horzLine: {{
                                color: '#787f8b',
                                width: 1,
                                style: LightweightCharts.LineStyle.Dashed,
                                labelBackgroundColor: statusColor,
                            }},
                        }},
                        rightPriceScale: {{
                            borderColor: '#2b313a',
                            scaleMargins: {{ top: 0.1, bottom: 0.1 }},
                        }},
                        timeScale: {{
                            borderColor: '#2b313a',
                            timeVisible: true,
                            secondsVisible: false,
                        }},
                    }});

                    const candlestickSeries = chart.addCandlestickSeries({{
                        upColor: '#0ECB81',
                        downColor: '#F6465D',
                        borderVisible: false,
                        wickUpColor: '#0ECB81',
                        wickDownColor: '#F6465D',
                    }});

                    candlestickSeries.setData(chartData);
                    chart.timeScale().fitContent();

                    // 雙擊右上角策略指標開啟參數視窗
                    const legend = document.getElementById('strategy-legend');
                    const modal = document.getElementById('modal-overlay');
                    const closeModal = document.getElementById('close-modal');

                    legend.addEventListener('dblclick', () => {{
                        modal.style.display = 'flex';
                    }});
                    closeModal.addEventListener('click', () => {{
                        modal.style.display = 'none';
                    }});
                    modal.addEventListener('click', (e) => {{
                        if (e.target === modal) modal.style.display = 'none';
                    }});

                    // 視窗縮放適應
                    window.addEventListener('resize', () => {{
                        const newWidth = container.clientWidth > 0 ? container.clientWidth : (window.innerWidth - 350);
                        chart.applyOptions({{ width: newWidth, height: 820 }});
                    }});
                </script>
            </body>
            </html>
            """

            chart_col, panel_col = st.columns([3.6, 1.2])
            with chart_col:
                components.html(tv_html, height=840, scrolling=False)
            
            with panel_col:
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
                        <div style="font-size: 12px;">進場: {latest_close:,.2f}</div>
                    </div>
                    """, unsafe_allow_html=True)
                elif df_tf.iloc[-1]['Short_Signal']:
                    st.markdown(f"""
                    <div style="background-color: #1e2329; border: 1px solid #F6465D; padding: 12px; border-radius: 6px; border-left: 5px solid #F6465D;">
                        <div style="color: #F6465D; font-weight: bold;">🔴 AI 智能空單 (SHORT)</div>
                        <div style="font-size: 12px;">進場: {latest_close:,.2f}</div>
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

    render_live_kline_chart(target_table, symbol_name, pair_name, selected_tf, kline_ratio, volume_ratio, auto_scroll)

elif st.session_state.search_query != "":
    st.warning(f"找不到與「{st.session_state.search_query}」相關的圖表，請嘗試輸入 **BTC** 或 **ETH**。")
else:
    st.info("💡 請在上方查詢欄輸入 **BTC** 或 **ETH** 並按下 **Enter** 顯示圖表。")
