import os
from flask import Flask, jsonify
import subprocess

app = Flask(__name__)

@app.route("/")
def home():
    return "Crypto AI Scanner Web Service is running successfully! 🚀"

@app.route("/run-scanner", methods=["GET", "POST"])
def run_scanner():
    try:
        # 執行你原本寫好的 okx_signal_scanner.py 腳本
        result = subprocess.run(["python", "okx_signal_scanner.py"], capture_output=True, text=True)
        
        if result.returncode == 0:
            print("✅ 掃描任務執行成功:", result.stdout)
            return jsonify({
                "status": "success",
                "message": "Scanner executed successfully",
                "output": result.stdout
            }), 200
        else:
            print("⚠️ 掃描任務執行出錯:", result.stderr)
            return jsonify({
                "status": "error",
                "message": "Scanner failed",
                "error": result.stderr
            }), 500
            
    except Exception as e:
        print(f"❌ 執行例外錯誤: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
