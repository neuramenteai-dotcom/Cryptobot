from flask import Flask, render_template, jsonify
from bot_engine import bot_instance
import database

app = Flask(__name__)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/status')
def status():
    # Sicurezza per Render/Gunicorn: se il worker non ha il thread attivo, lo avvia
    bot_instance.start()
    # Snapshot thread-safe: evita race condition con il thread del bot
    return jsonify(bot_instance.get_status_snapshot())


@app.route('/api/history')
def history():
    """Storico trade chiusi + statistiche aggregate + log circuit breaker."""
    return jsonify({
        "stats": database.get_trade_stats(),
        "trades": database.load_trade_history(limit=50),
        "circuit_breaker": database.load_circuit_breaker_log(limit=10),
    })


@app.route('/api/start')
def start_bot():
    bot_instance.start()
    return jsonify({"status": "started"})


@app.route('/api/stop')
def stop_bot():
    bot_instance.stop()
    return jsonify({"status": "stopped"})


# Avvio automatico (piano gratuito di Render).
# IMPORTANTE: il Procfile limita Gunicorn a 1 worker per non sdoppiare il bot.
bot_instance.start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
