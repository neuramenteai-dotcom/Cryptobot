from flask import Flask, render_template, jsonify
from bot_engine import bot_instance
import threading

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/status')
def status():
    # Sicurezza per Render/Gunicorn: se il worker non ha il thread attivo, lo fa partire
    bot_instance.start()
    
    return jsonify({
        "running": bot_instance.running,
        "balance": round(bot_instance.current_balance, 2),
        "total_profit": round(bot_instance.total_profit, 2),
        "win_rate": bot_instance.win_rate,
        "open_positions": bot_instance.open_positions,
        "logs": bot_instance.logs[:20]  # Mandiamo solo gli ultimi 20 log
    })

@app.route('/api/start')
def start_bot():
    bot_instance.start()
    return jsonify({"status": "started"})

@app.route('/api/stop')
def stop_bot():
    bot_instance.stop()
    return jsonify({"status": "stopped"})

# Avvio automatico per il piano GRATUITO di Render.
# Importante: su Render il comando di avvio deve limitare i worker a 1 per non sdoppiare il bot!
bot_instance.start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
