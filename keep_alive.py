from flask import Flask
from threading import Thread
import os

app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

def run():
    # Render sẽ tự động gán PORT qua biến môi trường
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True  # Thread sẽ tự động kết thúc khi main thread kết thúc
    t.start()
    print("🌐 Web server started on port", os.environ.get('PORT', 8080))