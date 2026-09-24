# Baseline "pre-live" — 24 settembre 2026

Questo documento fissa la versione dell'app con cui parte la prova dal vivo e
**come la giudicheremo**. È scritto prima di vedere i risultati: a novembre si
confrontano i dati con queste regole, non si cambiano le regole dopo aver
visto i dati. Il codice esatto è quello del tag GitHub `pre-live-2026-09-24`.

## Cosa è congelato

- **Valore sulle quote (1X2, doppia chance, Goal/No Goal, Under/Over):** quota
  di un bookmaker italiano sopra il prezzo giusto di Pinnacle (margine tolto
  col metodo potenza), quote fino a 5. Il modello Dixon-Coles è solo
  informativo: nel backtest non aggiunge niente a Pinnacle (versione 3,
  18 varianti tutte indistinguibili).
- **Marcatori:** modello "M2 npxG + rigori | se gioca", adottato perché ha
  superato l'holdout (log loss −0,0138 contro M0, intervallo −0,0181/−0,0104).
  Parametri nel protocollo di `backtest_marcatori.py`, protetti da un test.
- **Raccolta dati:** giro giornaliero alle 6:00 UTC; chiusura Pinnacle presa
  entro 40 minuti dall'inizio per le partite con scommesse virtuali;
  quote vecchie escluse (oltre 3 ore dall'ultimo giro della partita).
- **Librerie:** versioni fissate in `requirements.txt` (Python 3.11).

Fino al verdetto, si correggono solo **errori** (con un test che li
riproduce), non si cambiano modelli, soglie o strategie.

## Controllo dei primi giorni di partita (10-11 ottobre)

Workflow "Controllo giorni di partita". Funziona se:
1. la chiusura Pinnacle è presa entro un'ora dall'inizio per almeno l'80%
   delle partite con scommesse virtuali;
2. almeno il 90% delle quote marcatore è abbinato a un nostro giocatore;
3. (solo informativo) BSD pubblica o no le formazioni probabili prima della
   partita: decide se ha senso il lavoro sui minuti "se titolare / se subentra".

Se 1 o 2 falliscono è un errore da correggere, non un risultato della prova.

## Prova A — quote italiane contro Pinnacle

- **Misura principale:** CLV medio sulle scommesse con **chiusura definitiva**
  (partita iniziata), con intervallo al 95% (bootstrap).
- **Verdetto a 200 scommesse con chiusura definitiva:**
  - intervallo tutto sopra zero → il vantaggio c'è anche con i 4 bookmaker
    italiani: si continua e si valuta come usarlo;
  - intervallo tutto sotto zero → il vantaggio non c'è con i bookmaker
    italiani: l'app resta uno strumento di confronto, non di scommessa;
  - intervallo che comprende lo zero → non si decide: si continua fino a 500.
- **Controllo di qualità:** si guarda anche il CLV sulle sole scommesse con
  chiusura presa entro un'ora dall'inizio; se è molto diverso dal totale, conta
  quello.
- **Solo descrittivi, non decidono niente:** rendimento (serve molto più
  tempo), divisione per bookmaker, mercato, fascia di quota.

## Prova B — marcatori

- **Misure principali**, sulle scommesse concluse (esclusi i rimborsi):
  1. **calibrazione**: probabilità media del modello contro frequenza reale
     di gol;
  2. **rendimento**, con intervallo al 95%;
  3. **movimento della quota** (quota presa / ultima quota prima della
     partita − 1), con intervallo al 95%.
- **Verdetto a 200 scommesse concluse:**
  - rendimento con intervallo tutto sopra zero, oppure movimento della quota
    con intervallo tutto sopra zero → c'è valore da approfondire;
  - probabilità media del modello più alta della frequenza reale di oltre
    5 punti → il modello è troppo ottimista proprio dove trova "valore":
    da studiare (prima candidata: i giocatori "a rotazione");
  - altrimenti → non si decide, si continua.
- **Candidata per il periodo successivo** (non adottata): "M1 npxG | se gioca",
  risultata un filo migliore nell'holdout solo come diagnostica.

## Cosa NON fare fino al verdetto

- Nessuna nuova variante del modello 1X2 contro Pinnacle.
- Nessun cambio ai parametri dei marcatori.
- `data.db` resta nel repository: spostarlo è un lavoro di architettura per
  quando il progetto crescerà, non migliora previsioni né validazione.
