# CryptoBot AI - Recap Completo

Questo documento è il recap definitivo del progetto **CryptoBot AI**, un sistema automatizzato di trading algoritmico (scalping e trend-following) interfacciato con **Coinbase Advanced Trade**. È progettato per essere condiviso con altri sviluppatori per far capire esattamente le logiche, l'architettura e lo stack tecnologico.

## 🎯 1. Scopo del Progetto
Il bot scansiona 24/7 il mercato cripto alla ricerca di opportunità di scalping a basso rischio. Opera su mercati **EUR** e **USDC**, individua le monete in forte rialzo (gainers), calcola il punto ottimale di ingresso tramite indicatori tecnici (SMA, RSI, MACD) e gestisce l'uscita proteggendo il capitale con Trailing Stop, calcolo delle fee in tempo reale (breakeven dinamico) e Circuit Breaker. Include una **Dashboard Web** in Flask per il monitoraggio in tempo reale.

## 🛠 2. Stack Tecnologico e Linguaggio
- **Linguaggio**: Python 3.10+
- **Librerie Core**: 
  - `ccxt`: Per comunicare con le API di Coinbase Advanced Trade tramite chiavi CDP (Coinbase Developer Platform).
  - `Flask` & `Gunicorn`: Per la web dashboard in ascolto sulla porta 5000.
  - `sqlite3` (built-in): Per la persistenza dello stato (trades aperti, statistiche, fee pagate).
- **Hosting Target**: Render (o simili servizi PaaS Docker-based).
- **Repository**: Git standard, deploy automatico.

## 📂 3. Struttura del Progetto (Architettura)

- **`bot_engine.py` (Il Motore Principale)**: Esegue il ciclo infinito (loop) in un thread separato. Gestisce le posizioni (Stop Loss/Trailing), scansiona i gainers, valuta le entrate, e gestisce routine come l'autoliquidazione (Dust Cleanup) e il Circuit Breaker.
- **`coinbase_executor.py` (Le Mani)**: Wrapper su `ccxt`. Effettua ordini a mercato (Buy/Sell), recupera grafici a candele (OHLCV) ed espone le metriche (SMA, RSI, MACD calcolati tramite `utils.py`). Implementa un robusto mock system per eseguire la modalità simulazione senza chiamate reali e rate-limiting handling.
- **`fmp_radar.py` (Gli Occhi sulle News)**: Interroga le API di Financial Modeling Prep (FMP) per scaricare il sentiment sulle notizie di mercato. Funge da filtro soft per evitare l'acquisto di monete con recenti notizie disastrose.
- **`app.py` & `templates/index.html` (L'Interfaccia)**: Server Flask che espone la UI Web con design moderno e premium. Legge lo stato thread-safe da `bot_engine.py` e aggiorna la UI ogni secondo tramite polling AJAX (`/api/status`).
- **`database.py` (La Memoria)**: Interazione diretta con `bot_state.db` (SQLite). Salva i trade attivi in caso di crash e persiste metriche chiave come `total_profit`, `consecutive_losses`, fee pagate, win rate e volume scambiato nel mese per Coinbase One.
- **`config.py` e `.env` (Le Regole)**: Mappatura centralizzata delle variabili d'ambiente (chiavi API, BUDGET, soglie di rischio, percentuali indicatori).

## 🧠 4. Regole di Trading e Logiche nel Dettaglio

### A. Pre-Filtro e Ottimizzazione
1. **Multi-Quote (EUR & USDC)**: Il bot scansiona sia i mercati `/EUR` che `/USDC` (centinaia di mercati potenziali).
2. **Hard-Cap & Controllo Liquidità**: Per evitare di infrangere i Rate-Limit API di Coinbase (`fetch_ohlcv`), il bot filtra preventivamente le monete per cui l'utente *non ha liquidità disponibile* (es. salta tutti gli USDC se il wallet ha solo EUR). Dopodiché, calcola gli indicatori pesanti solo sui **Top 15 Gainers** assoluti.
3. **New Listings Detection**: Il bot memorizza in database l'elenco dei mercati conosciuti. Se rileva un mercato *appena listato*, lo attacca istantaneamente con una puntata piccola fissa (Risk Capital) per speculare sul pump iniziale, bypassando volutamente gli indicatori storici.

### B. Condizioni di Ingresso (Candidati Normali)
Affinché il bot piazzi un ordine `Market Buy`, TUTTE queste regole devono essere verificate contemporaneamente:
1. **Performance**: L'asset deve crescere di almeno `MIN_GAINER_PCT` (default +2.0%) con volumi rilevanti (> 100k).
2. **SMA (Media Mobile)**: Il prezzo attuale deve essere al di sopra della SMA a 10 periodi (su candele a 5 min). È la conferma di uptrend primario.
3. **RSI**: Deve essere `< 70`. Evita acquisti all'apice di un pump esplosivo per non rischiare l'ipercomprato.
4. **MACD**: Deve indicare momentum rialzista (MACD Line > Signal Line & Istogramma > 0).
5. **FMP Sentiment**: Non deve esserci un sentiment news pesantemente negativo (`< -0.5`).
6. **Position Sizing Dinamico**: Il bot non investe tutto il budget in un singolo asset. Calcola quanto investire partendo dal 5% del portafoglio (nella valuta base), scalando con moltiplicatori positivi se la moneta ha volumi immensi (sicurezza) o un pump > 15% (momentum), fino a un massimo del 15% per posizione.

### C. Gestione Posizioni (Uscita) e Protezione
1. **Trailing Stop Dinamico**: Il bot traccia costantemente il picco massimo (`highest_price`) raggiunto dalla moneta dopo l'acquisto. Se il prezzo ritraccia di `STOP_LOSS_PCT` (default 1.5%) dal picco, piazza un ordine di `Market Sell`. Se l'asset era salito, si esce in netto profitto.
2. **Trend Reversal (SMA) & Breakeven Dinamico**: Se il prezzo buca a ribasso la SMA, il bot vende MA SOLO SE il PnL netto in quel momento copre ampiamente le commissioni di round-trip (compra + vendi) registrate. Il bot non chiude trade orizzontali in perdita per colpa delle fee.
3. **Auto-calibrazione Commissioni (Fee Aware)**: Il bot non stima semplicemente le fee a 0.6%. Legge i payload dei fill reali da `ccxt` calcolando la spesa EUR esatta e creando un `effective_fee_rate`. Se l'utente ha un abbonamento *Coinbase One*, le fee calano automaticamente e il breakeven si abbassa, aumentando vertiginosamente le finestre di uscita utili.
4. **Circuit Breaker (Il Freno a Mano)**: Se il bot chiude in perdita 3 trade consecutivi, blocca forzatamente tutte le scansioni di ingresso per 30 minuti, dando tempo alle medie mobili a 5m (6 candele) di stabilizzarsi dopo un flash crash, prevenendo il wipeout dell'account per revenge-trading.
5. **Dust Cleanup**: Ad ogni riavvio o ciclo lungo, cerca rimasugli di monete (polveri) invendute, e se convertibili (sopra al minimo d'ordine), le auto-liquida per liberare liquidità, preservando rigorosamente gli asset protetti (`BLOCKED_ASSETS`).

## 🔄 5. Esecuzione e Test
Per eseguire l'app localmente (avvia sia Dashboard web che Engine in parallelo):
```bash
python app.py
```
Per modificare la modalità operativa, modificare il `.env`:
- `TRADE_MODE="SIMULATION"` -> Le chiamate REST per gli acquisti/vendite sono mockate, l'API gira in read-only.
- `TRADE_MODE="LIVE"` -> Vengono utilizzati soldi veri, le fee vengono misurate sul fill Coinbase.
