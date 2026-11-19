import os
import time
import threading
import subprocess
from flask import Flask, jsonify
from datetime import datetime
from zoneinfo import ZoneInfo

TAIWAN_TZ = ZoneInfo('Asia/Taipei')
app = Flask(__name__)

def background_scanner_loop():
    """背景持續執行的無限迴圈，每 60 秒自動執行一次掃描與發送"""
    print("🚀 背景自動掃描續跑執行緒已啟動...")
    while True:
        current_time = datetime.now(TAIWAN_TZ).strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{current_time}] 開始執行自動排程掃描...")
        try:
            # 執行核心掃描與發送腳本
            result = subprocess.run(["python", "okx_signal_scanner.py"], capture_output=True, text=True)
            if result.returncode == 0:
                print(f"[{current_time}] ✅ 自動掃描執行成功:\n{result.stdout.strip()}")
            else:
                print(f"[{current_time}] ⚠️ 自動掃描執行出錯:\n{result.stderr.strip()}")
        except Exception as e:
            print(f"[{current_time}] ❌ 背景執行例外錯誤: {e}")
        
        # 每 60 秒執行一次迴圈
        time.sleep(60)

# 當 Flask 啟動時，自動在背景開一個獨立執行緒跑迴圈
def start_background_task():
    thread = threading.Thread(target=background_scanner_loop, daemon=True)
    thread.start()

# 啟動背景執行緒
start_background_task()

@app.route("/")
def home():
    return "Crypto AI Scanner Web Service with Background Worker is running 24/7! 🚀"

@app.route("/run-scanner", methods=["GET", "POST"])
def manual_run():
    """提供手動觸發與防休眠 Ping 的路由"""
    try:
        result = subprocess.run(["python", "okx_signal_scanner.py"], capture_output=True, text=True)
        return jsonify({
            "status": "success",
            "message": "Scanner executed successfully",
            "output": result.stdout
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
