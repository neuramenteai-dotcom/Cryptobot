# Coinbase Trading Bot - Project Overview

Questo documento riassume lo scopo, l'architettura e le logiche del bot di trading automatico sviluppato. Può essere usato come riferimento per riprendere il progetto o condividerlo con altri sviluppatori.

## 🎯 Scopo del Progetto
Un bot automatizzato e hostato in cloud (tramite **Render**) per fare scalping / trend-following algoritmico sul mercato crypto tramite **Coinbase Advanced Trade**. Il bot ricerca continuamente monete in forte rialzo, calcola il momento d'ingresso e gestisce le posizioni difendendo il capitale con stop-loss e chiusure dinamiche basate sulla media mobile.

## 🛠 Stack Tecnologico
- **Linguaggio**: Python 3
- **Librerie Principali**:
  - `ccxt`: Per comunicare con le API di Coinbase Advanced Trade tramite le chiavi CDP (Coinbase Developer Platform).
  - `Flask` & `Gunicorn`: Per la creazione dell'interfaccia web di monitoraggio e per permettere l'hosting 24/7 su piattaforme cloud-native come Render.
- **Hosting**: Render (Cloud Application).

## 📂 Architettura e File Principali

- `bot_engine.py` *(Il Cervello)*: Contiene la classe `TradingBot`. Esegue il ciclo principale (`loop()`), gestendo le posizioni aperte (controllo stop-loss / trend reversal) e posizionando nuovi ordini calcolando l'importo giusto da investire (Position Sizing Dinamico).
- `fmp_radar.py` *(Gli Occhi)*: Interroga Coinbase per trovare i migliori "gainers" del momento. È configurato per restituire **solo ed esclusivamente** mercati in Euro (`/EUR`), garantendo la compatibilità con il saldo dell'utente.
- `coinbase_executor.py` *(Le Mani)*: Il wrapper per la libreria `ccxt`. Si occupa di instanziare l'autenticazione tramite le chiavi CDP, eseguire gli ordini a mercato (Market Buy / Market Sell) e ricavare la SMA (Simple Moving Average) dal grafico a candele. Include un sistema di mock per quando si opera in `SIMULATION`.
- `app.py`: L'interfaccia web Flask. Restituisce il file HTML di visualizzazione e genera le API (`/api/status`) per leggere il bilancio, i trade aperti e i log in tempo reale. Provvede anche all'avvio automatico del bot in parallelo al server web.
- `simulate.py`: Un modulo di backtesting per scaricare i dati storici delle ultime 24 ore e simulare la strategia sulle candele da 5 minuti senza rischiare denaro reale, calcolando Win Rate e PnL netto.
- `config.py` e `.env`: Contengono le chiavi API CDP e le impostazioni sensibili (`BUDGET`, `TRADE_MODE`, percentuali di Stop-Loss).
- `Procfile`: File di istruzioni per Render per indicargli come avviare il web server tramite Gunicorn (`web: gunicorn app:app --bind 0.0.0.0:$PORT`).

## 📈 Strategia di Trading (Trend Following & Momentum)

1. **Ricerca Segnale**: Ogni 60 secondi il bot cerca asset con un incremento giornaliero superiore a una certa soglia (es. +3%).
2. **Filtro di Sicurezza (SMA)**: Il bot scarica le ultime candele (es. 5 minuti) e calcola la **Media Mobile Semplice (SMA)** a 10 periodi. Compra solo se il prezzo attuale è *sopra* questa media (conferma di trend rialzista).
3. **Position Sizing Dinamico**: Il bot non investe la stessa cifra su tutto. Assegna un "peso":
   - Investe una percentuale del capitale (base 5%).
   - Se la moneta ha volumi immensi (es. BTC, SOL) aggiunge un moltiplicatore, rischiando di più perché più stabile.
   - Se la moneta sta subendo un pump molto violento (>15%), aumenta la puntata per cavalcare l'onda.
4. **Gestione dell'Uscita**:
   - **Trend Reversal (Take Profit Dinamico)**: Appena il prezzo chiude *sotto* l'SMA, significa che l'onda rialzista si è rotta. Il bot incassa subito i profitti (o accetta una piccolissima perdita strutturale).
   - **Hard Stop-Loss**: Fissato rigidamente (es. -1.0%). Se si verifica un crollo improvviso ("flash crash"), il bot svuota la posizione all'istante a mercato, prevenendo disastri.
5. **Autoliquidazione**: Se il bot trova un'ottima occasione ma non ha Euro liberi, analizza il portafoglio e liquida automaticamente asset non bloccati (HODL list) per trovare la liquidità necessaria.

## 🚀 Setup e Manutenzione Futura
Per chi prenderà in mano il codice in futuro:
- Il bot supporta le nuove chiavi **CDP di Coinbase** nel formato JSON lungo (es. `organizations/XXX/apiKeys/YYY`). Non usare le vecchie Legacy Keys.
- Per passare dal conto reale al test, basta modificare la variabile `TRADE_MODE="SIMULATION"` o `TRADE_MODE="LIVE"` nel `.env` o nei settings di Render.
- Tutti i parametri finanziari base si trovano in `config.py`.
- Se si aggiungono monete da *non toccare mai* nel wallet di Coinbase, bisogna aggiornare l'array `self.blocked_assets` in `bot_engine.py`.
