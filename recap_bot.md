# CryptoBot AI — Recap di Sistema

> Ultimo aggiornamento: 1 luglio 2026. Documento pensato per essere condiviso: riassume scopo, architettura, stato live, risultati reali raccolti e i prossimi passi. Non richiede contesto esterno per essere capito.

## 🎯 1. Scopo del Progetto
Sistema di trading algoritmico automatizzato per crypto (scalping/trend-following), interfacciato con **Coinbase Advanced Trade**, pensato per evolvere in una piattaforma multi-asset (crypto oggi, azioni USA in arrivo via Alpaca). Include dashboard web di monitoraggio in tempo reale, persistenza cloud dello storico trade, e un layer di "market intelligence" (sentiment, rischio geopolitico, regime di mercato).

## 🟢 2. Stato Attuale — cosa gira ORA
- **Deploy**: [Render](https://cryptobot-4k3g.onrender.com) (piano free), 1 worker Gunicorn, deploy automatico ad ogni push su `main`.
- **Modalità**: `SIMULATION` (nessun ordine reale) — in fase di raccolta dati per validare la strategia prima di passare a `LIVE`.
- **Persistenza**: SQLite locale (stato del bot) + **Supabase (Postgres)** per lo storico trade, che sopravvive ai riavvii/standby di Render.
- **Uptime**: monitor esterno (UptimeRobot, ping ogni 5 min) per tenere il servizio sveglio 24/7 — necessario perché il piano Render free va in standby dopo ~15 min di inattività.
- **Repository**: `github.com/neuramenteai-dotcom/Cryptobot`, storia git ripulita da segreti.

## 🔐 3. Sicurezza
- Chiavi API Coinbase e Render **ruotate** dopo un'esposizione accidentale iniziale; la vecchia history git contenente segreti è stata riscritta (`git filter-branch`) e il remote force-pushato.
- `.gitignore` copre `.env*`, `*.json` (chiavi CDP), `*.db` (stato locale).
- Nessuna chiave hardcoded nel codice: tutto passa da variabili d'ambiente (locali via `.env`, su Render via Environment Variables).

## 🛠 4. Stack Tecnologico
- **Linguaggio**: Python 3.10+
- **Librerie core**: `ccxt` (Coinbase Advanced Trade via chiavi CDP), `Flask`/`Gunicorn` (dashboard web), `sqlite3` (stato locale), `requests` (integrazioni HTTP dirette con FMP, GDELT, CryptoPanic, Supabase, Alpaca).
- **Hosting**: Render (bot + dashboard), Supabase (persistenza cloud), GitHub (repo + deploy trigger).

## 📂 5. Architettura — file principali
| File | Ruolo |
|---|---|
| `bot_engine.py` | Motore principale: loop in thread separato, gestione posizioni, position sizing, circuit breaker, dust cleanup, new-listing detection |
| `coinbase_executor.py` | Wrapper `ccxt`: ordini a mercato, calcolo fee reali dai fill, SMA/RSI/MACD |
| `brokers.py` | **Astrazione broker**: interfaccia comune per estendere il bot oltre Coinbase. `CoinbaseBroker` pronto; `AlpacaBroker` pronto per paper trading su azioni USA (ordini asincroni con polling fill) |
| `market_intel.py` | Aggregatore intelligence: Fear&Greed Index, rischio geopolitico (GDELT, gratuito), sentiment per-asset (FMP momentum + CryptoPanic) |
| `fmp_radar.py` | Client Financial Modeling Prep (API "stable"): momentum gratuito, news a pagamento (non attivo) |
| `cloud_store.py` | Persistenza storico trade su Supabase (best-effort, degrada in sicurezza se non configurato) |
| `database.py` | SQLite locale: posizioni aperte, stato persistente (circuit breaker, statistiche, fee), storico trade |
| `app.py` + `templates/index.html` | Dashboard Flask: stato live, storico trade, metriche performance |
| `config.py` | Tutti i parametri via env var (fee, soglie rischio, quote abilitate, feature flag) |

## 🧠 6. Logica di Trading

**Ingresso** (tutte le condizioni devono valere):
1. Asset in crescita ≥ soglia configurabile (default +2%) con volume rilevante
2. Prezzo sopra SMA a 10 periodi (conferma trend)
3. RSI < 70 (evita ipercomprato)
4. MACD rialzista
5. Sentiment news non negativo (FMP/CryptoPanic, filtro soft)
6. Position sizing dinamico: 5% base del capitale, scalato su volume/momentum, cap al 15% per posizione

**Uscita**:
- **Trailing stop** dinamico dal picco massimo raggiunto
- **Trend reversal** con **breakeven dinamico**: vende solo se il guadagno lordo copre le fee reali misurate + margine (mai vendita in "finto profitto")
- **Circuit breaker**: 3 perdite consecutive → pausa 30 minuti

**Multi-quote**: scansiona sia mercati `/EUR` (35) che `/USDC` (~550), ampliando enormemente l'universo investibile.

**Fee auto-calibrate**: il bot non assume una fee fissa — la misura dai fill reali di ogni ordine, e adatta il breakeven di conseguenza (rilevante per capire se Coinbase One azzera davvero le fee su Advanced Trade).

## 📊 7. Risultati Reali Raccolti (onesto, dati persistenti Supabase)

Campione simulazione, **28 giugno → 1 luglio 2026** (~72 ore):

| Metrica | Valore |
|---|---|
| Trade chiusi | 30 |
| Win rate | **3.3%** (1 vinto / 29 persi) |
| PnL netto | **−€3.40** |
| Fee totali | €1.87 |
| PnL lordo stimato | **−€1.53** |

**Verdetto onesto**: l'infrastruttura funziona correttamente (persistenza, deploy, dashboard, tutto verificato), ma **la strategia sta perdendo anche al lordo delle fee** in questo campione. Il mercato è rimasto per gran parte del periodo in regime di **"panico estremo"** (Fear&Greed Index ~12-15): comprare breakout di momentum in un mercato in caduta produce sistematicamente falsi segnali seguiti da stop loss. Il bot al momento **non distingue il regime di mercato per decidere se operare**, solo per scalare la size — è il gap principale da chiudere prima di considerare capitale reale.

## 🚧 8. Prossimi Passi (roadmap aperta)
1. **Bloccare nuovi ingressi in regime di panico/falling knife** (oggi riduce solo la size, non blocca) — proposto, non ancora implementato.
2. **Filtro liquidità più severo** sui mercati USDC micro-cap, dove avvengono i falsi breakout osservati.
3. **Alpaca (azioni USA)**: codice pronto (`AlpacaBroker`), in attesa delle chiavi paper trading dell'utente per il primo test end-to-end.
4. **CryptoPanic**: integrazione pronta, in attesa del token API gratuito (sito momentaneamente non raggiungibile al tentativo).
5. Continuare la raccolta dati in simulazione fino a un campione statisticamente più robusto prima di qualunque passaggio a `LIVE`.

## 🔄 9. Esecuzione Locale
```bash
python app.py          # Dashboard + bot engine (thread separato)
python sim_runner.py   # Runner di sola simulazione, forza SIMULATION indipendentemente da .env
```
Modalità operativa (`.env` o env Render):
- `TRADE_MODE=SIMULATION` → ordini mockati, letture in tempo reale, zero rischio
- `TRADE_MODE=LIVE` → ordini reali, fee misurate sui fill Coinbase — **da non attivare finché la strategia non mostra un edge positivo lordo**

## 🔗 Link
- Dashboard live: https://cryptobot-4k3g.onrender.com
- Repository: https://github.com/neuramenteai-dotcom/Cryptobot
