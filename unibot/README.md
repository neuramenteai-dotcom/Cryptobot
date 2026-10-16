# Unibot - bot unificato crypto + azioni USA (solo paper)

Unisce in un unico motore il CryptoBot (Coinbase, EUR/USDC) e il Radar Volatilita' Azioni USA (FMP).
**Non invia ordini reali**: il broker e' simulato (`paper.py`). Non servono chiavi Coinbase (dati pubblici);
per le azioni serve `FMP_API_KEY` nel file `.env`.

## Comandi (dalla cartella del progetto)
```
python -m unibot.test_unibot                 # 104 controlli, senza rete
python -m unibot.run --once                  # un ciclo + report
python -m unibot.run --loop --interval 60    # ciclo continuo (Ctrl+C per fermare)
python -m unibot.run --report                # report dallo stato salvato
python -m unibot.run --add-signal SOL --source nome [--side avoid] [--ttl 24]
python -m unibot.run --backtest --start 2026-06-28 --days 11 --symbols 40
python -m unibot.run --signal-lab export.csv # testa segnali datati (es. export Quiver, dati FMP)
python -m unibot.run --signal-lab quiver-api # storico Congresso da Quiver (serve QUIVER_API_TOKEN in .env; non provato)
python -m unibot.run --insider-lab           # acquisti insider SEC Form 4 vs SPY/IWM e gruppo di controllo (~25 min)
python -m unibot.run --funding-lab           # funding crypto (perpetui Binance, dati pubblici) a quintili (~5 min)
python -m unibot.run --trend-lab             # trend a bassa rotazione su BTC/ETH/paniere (usa i dati del funding lab, pochi secondi)
```

## Come e' fatto
| File | Ruolo |
|---|---|
| `strategy.py` | Regole di ingresso/uscita (funzioni pure, usate anche dal backtest) |
| `engine.py` | Ciclo: uscite -> ingressi; sizing, circuit breaker, limite perdita giornaliero |
| `universe.py` | Crypto (ccxt, cache candele) e azioni (radar FMP: filtri ETF a leva, prezzo, % , liquidita') |
| `paper.py` | Fee e slippage per classe di asset |
| `store.py` | SQLite: posizioni, trade, stato |
| `signals.py` | Casella dei segnali esterni NON verificati (priorita' o veto, mai ordini da soli) |
| `signal_lab.py` | Misura l'alpha vs SPY dopo la PUBBLICAZIONE di un segnale (no look-ahead) |
| `backtest.py` | Replay storico crypto: regole vecchie vs nuove, con e senza commissioni |

## Regole di sicurezza incorporate
- Regime di mercato ignoto = nessun ingresso (usa l'ultimo Fear&Greed valido fino a 36 ore).
- Crypto: blocco ingressi se Fear&Greed <= 25. Azioni: solo con borsa aperta, SPY non in calo, dati non fermi,
  nessuna posizione overnight, budget FMP riservato alle uscite.
- Un titolo azionario senza prezzo FMP (non coperto dal piano gratuito) non viene aperto.
- Le uscite non sono mai bloccate da circuit breaker o limiti giornalieri.

## Risultati del backtest crypto (39 simboli USDC, candele 5m, fee 0,6% per lato)
| Finestra | Regole | Trade | Media per trade con fee | Media lorda (senza fee) |
|---|---|---|---|---|
| 28/06-09/07 (12 giorni su 13 in panico) | vecchie | 772 | -1,31% | -0,03% |
| | nuove | 75 | -1,82% | -0,45% |
| 06-16/10 (nessun giorno in panico) | vecchie | 686 | -1,26% | -0,01% |
| | nuove | 606 | -1,26% | +0,00% |

Lettura: **nessun vantaggio lordo** (la media lorda e' indistinguibile da zero); le commissioni (~1,3% a giro)
decidono il risultato. I filtri nuovi riducono l'attivita' in panico ma non creano un edge.
Limiti: simboli listati oggi (survivorship), fill ottimistici, simboli indipendenti, nessun limite di portafoglio.

## Non fatto
- Nessun backtest delle azioni (il piano FMP gratuito non ha candele intraday).
- `brokers.py`/Alpaca non collegato (servono le chiavi paper); nessuna dashboard web.
- Connettore Quiver non utilizzabile da questa sessione (vedi `signal_lab.py`, `load_quiver_api` NON testato).

## ETF lab (`python -m unibot.run --etf-lab`)
9 ETF USA (SPY, QQQ, IWM, EFA, EEM, AGG, TLT, GLD, VNQ), rendimento totale dal 2004-11 al 2026-10 (Yahoo, non ufficiale),
costo 0,10% e 0,30% a lato, periodo di scelta fino al 2014 e di verifica dal 2015.
Esito (2026-10-16): compra-e-tieni SPY +10,9%/anno ma calo massimo -55%; il trend SMA200 su SPY/QQQ dimezza il calo (-22/-28%)
e rende meno (+6,5/+9,8% con costo 0,30%); la rotazione top3 fa +8,4/+9,6% con calo -25%. Nessuna regola batte il compra-e-tieni sul rendimento:
riduce solo il rischio. Il trend su EFA (ex-USA) e' piu' debole (+2/+4%).

## Test in avanti (`python -m unibot.run --etf-forward`)
Due regole fisse (paper): ETF = 50% SPY + 50% QQQ ciascuno solo sopra la SMA200; CRYPTO = 50% BTC + 50% ETH ciascuno solo sopra la SMA100 (costo 0,6%). Report giornaliero schedulato alle 08:00. La data di partenza si fissa alla prima esecuzione
(2026-10-16) in `unibot_data/etf_forward.json`; ogni lancio ricalcola tutto dai prezzi. Verdetto non prima di 4-8 settimane.

## Variazioni giornaliere (`unibot/daily_lab.py`, 2026-10-16)
Rendimento del giorno dopo un movimento > ±1% / ±2% (chiusura-chiusura). SPY/QQQ (21 anni): dopo un forte calo il giorno dopo rende in media +0,17..+0,35%
(t~2: effetto reale ma piccolo), dopo un rialzo e' neutro o negativo. 'Segui il rialzo' perde 30-65% netto. 'Compra dopo il calo' >2% su SPY: +5% / +28% in dieci anni
(in mercato ~4% dei giorni), molto sotto compra-e-tieni, e con costo 0,3%/lato (piu' dell'edge) sparisce. BTC/ETH: nessun effetto (t<1), i costi 0,6% distruggono tutto.
