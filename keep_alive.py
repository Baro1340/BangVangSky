from flask import Flask
from threading import Thread
import os
import logging

# Tắt log của Flask để tránh rối
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask('')

@app.route('/')
def home():
    return "Bot is running! 🤖"

@app.route('/health')
def health():
    return "OK", 200

def run():
    # QUAN TRỌNG: Render yêu cầu host='0.0.0.0'
    port = int(os.environ.get('PORT', 10000))
    print(f"🌐 Web server sẽ chạy trên port {port}")
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()
    print(f"🌐 Web server started on port {os.environ.get('PORT', 10000)}")