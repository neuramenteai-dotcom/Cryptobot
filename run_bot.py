from bot_engine import bot_instance
import time

if __name__ == '__main__':
    print("=== AVVIO MOTORE BACKGROUND WORKER ===", flush=True)
    bot_instance.start()
    
    # Manteniamo in vita il processo
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        bot_instance.stop()
        print("Worker terminato dal sistema.")
