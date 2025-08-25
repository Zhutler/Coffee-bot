import os
import threading
from flask import Flask
from bot import run_bot   # см. ниже bot.py

app = Flask(__name__)

@app.route("/")
def home():
    return "Coffee Bot is running on Render!"

if __name__ == "__main__":
    # Запускаем бота в отдельном потоке
    threading.Thread(target=run_bot).start()
    
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
