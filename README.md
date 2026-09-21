# La tua app di previsioni calcio

https://bettingdata-2026.streamlit.app/

Non devi capire il codice qui dentro. Questa guida ti spiega solo i click da
fare per portare l'app online. Sono due parti: GitHub (dove "vive" il
progetto) e Streamlit (dove diventa una pagina web vera).

Tempo previsto: 10-15 minuti, una tantum.

---

## Parte 1 — Carica il progetto su GitHub

1. Vai su github.com e crea un account gratuito (se non ce l'hai già).
2. Clicca sul "+" in alto a destra → "New repository".
3. Dai un nome al progetto (es. "previsioni-calcio"), lascialo **Public**,
   e clicca "Create repository".
4. Nella pagina del repository appena creato, clicca "uploading an existing
   file" (o il pulsante "Add file" → "Upload files").
5. Trascina dentro TUTTI i file che ti ho dato finora:
   - schema.sql
   - model.py
   - value_calculator.py
   - test_model.py
   - ingest_historical.py
   - fetch_fixtures.py
   - fetch_odds.py
   - run_model.py
   - dashboard.py
   - requirements.txt
   - la cartella `.github` (con dentro `workflows/update_data.yml`) —
     se il trascinamento della cartella non funziona dal browser, carica
     prima gli altri file, poi te lo dirò io come sistemare quel pezzo.
6. Scrivi un messaggio a piacere (es. "primo caricamento") e clicca
   "Commit changes".

### Aggiungi le tue due chiavi segrete

7. Nel repository, vai su **Settings** (in alto) → nel menu a sinistra
   **Secrets and variables** → **Actions**.
8. Clicca "New repository secret":
   - Nome: `FOOTBALL_DATA_API_KEY` — Valore: la tua chiave di football-data.org
   - Salva, poi ripeti per: Nome: `ODDS_API_KEY` — Valore: la tua chiave di the-odds-api.com

Queste chiavi restano private: nessuno le vede, nemmeno guardando il codice.

### Fai partire il primo aggiornamento

9. Vai sulla scheda **Actions** del repository.
10. Clicca su "Aggiorna dati e previsioni" nella lista a sinistra, poi sul
    pulsante "Run workflow" → "Run workflow" di nuovo per confermare.
11. Aspetta un paio di minuti: quando il pallino diventa verde ✅, i dati
    veri sono stati scaricati e il modello ha calcolato le previsioni.
    Da qui in poi succede da solo, ogni giorno, senza che tu faccia nulla.

---

## Parte 2 — Pubblica la pagina web

1. Vai su **share.streamlit.io** e accedi con il tuo account GitHub
   (stesso account di prima, un click, nessuna nuova password).
2. Clicca "New app" (o "Create app").
3. Seleziona il repository che hai appena creato.
4. Nel campo "Main file path" scrivi: `dashboard.py`
5. Clicca "Deploy".

Dopo un minuto o due, la tua pagina sarà online con un indirizzo tipo
`tuonome-previsioni-calcio.streamlit.app` — apribile da telefono, computer,
ovunque, senza installare nulla.

---

Se qualcosa non torna in uno di questi passaggi, fammi uno screenshot e ti
dico esattamente cosa correggere.
