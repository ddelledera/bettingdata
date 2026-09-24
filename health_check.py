"""
Controllo di salute del giro giornaliero.

Prima "verde" voleva dire solo "nessuno script si è bloccato". Questo
controllo guarda invece se i DATI sono arrivati davvero: quote fresche di
ogni bookmaker, Pinnacle presente, partite abbinate, squadre riconosciute,
previsioni calcolate, rose dei giocatori, richieste API nel limite.

Due livelli:
  ERRORE  -> il workflow diventa rosso (ma i dati vengono salvati lo stesso)
  AVVISO  -> il workflow resta verde, con un avviso giallo nel riepilogo

Il risultato finisce anche nel database (tabella health_checks), così la
dashboard può mostrare se l'ultimo aggiornamento ha avuto problemi.

Uso nel workflow:
  python health_check.py           controlla, scrive il riepilogo (non fallisce)
  python health_check.py --esito   fallisce se l'ultimo controllo ha trovato errori
"""

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import run_stats
from db_utils import init_db

DB_PATH = "data.db"
LEAGUES = ["Serie A", "Premier League", "La Liga", "Bundesliga", "Ligue 1"]
MAIN_BOOKS = ["Goldbet", "Eurobet", "bet365"]   # quelli che coprono tutti i campionati
REFERENCE = "Pinnacle"
MIN_COVERAGE = 0.6        # un bookmaker principale deve quotare almeno il 60% delle
                          # partite quotate da Pinnacle
MONTHLY_WARN = 200        # richieste OddsPapi al mese oltre le quali avvisare (limite 250)
FIXTURE_WINDOW_DAYS = 21  # come fetch_fixtures.py


def run_checks(conn, stats, now, outcomes):
    """Ritorna la lista di (livello, messaggio)."""
    out = []
    err = lambda m: out.append(("ERRORE", m))
    warn = lambda m: out.append(("AVVISO", m))
    today = now.date().isoformat()
    horizon = (now.date() + timedelta(days=FIXTURE_WINDOW_DAYS)).isoformat()
    ph = ",".join("?" * len(LEAGUES))

    # --- Partite in programma -------------------------------------------
    upcoming = dict(conn.execute(f"""
        SELECT league, COUNT(*) FROM matches
        WHERE home_goals IS NULL AND date >= ? AND date <= ? AND league IN ({ph})
        GROUP BY league""", (today, horizon, *LEAGUES)).fetchall())
    total_upcoming = sum(upcoming.values())
    if total_upcoming == 0:
        err(f"Nessuna partita dei 5 campionati nei prossimi {FIXTURE_WINDOW_DAYS} giorni: "
            "football-data.org non ha risposto o la chiave non funziona.")
    else:
        vuoti = [l for l in LEAGUES if not upcoming.get(l)]
        if vuoti:
            warn(f"Nessuna partita in programma nei prossimi {FIXTURE_WINDOW_DAYS} giorni per: "
                 f"{', '.join(vuoti)}.")
    for league, names in (stats.get("partite", {}).get("non_riconosciute") or {}).items():
        warn(f"{league}: squadre non riconosciute ({', '.join(names)}). Il modello non può "
             "prevederne le partite: il nome va aggiunto in team_utils.py.")
    if "partite" not in stats:
        warn("Lo script delle partite in arrivo non ha lasciato il suo resoconto.")

    senza_orario = conn.execute(f"""
        SELECT COUNT(*) FROM matches WHERE home_goals IS NULL AND date >= ? AND date <= ?
        AND league IN ({ph}) AND kickoff_utc IS NULL""", (today, horizon, *LEAGUES)).fetchone()[0]
    if senza_orario:
        warn(f"{senza_orario} partite in programma senza orario di inizio: per queste la "
             "chiusura del CLV e il blocco a partita iniziata usano solo la data.")

    # --- Quote --------------------------------------------------------------
    q = stats.get("quote")
    if not q:
        err("Lo script delle quote (fetch_odds.py) non ha lasciato il suo resoconto: "
            "probabilmente non è arrivato alla fine.")
    else:
        books = q.get("bookmaker", {})
        falliti = [b for b, s in books.items() if not s.get("ok")]
        if REFERENCE in falliti:
            err(f"Pinnacle non ha risposto: senza prezzo giusto niente opportunità né "
                f"scommesse virtuali oggi. ({books[REFERENCE].get('error', '')})")
        principali_falliti = [b for b in falliti if b in MAIN_BOOKS]
        if len(principali_falliti) >= 2:
            err(f"Non hanno risposto: {', '.join(principali_falliti)}.")
        for b in falliti:
            if b != REFERENCE and not (len(principali_falliti) >= 2 and b in MAIN_BOOKS):
                warn(f"{b} non ha risposto ({books[b].get('error', '')}).")

        pin = books.get(REFERENCE, {}).get("saved", 0)
        if books.get(REFERENCE, {}).get("ok") and pin == 0 and total_upcoming:
            err("Pinnacle ha risposto ma nessuna sua quota è stata abbinata a una nostra partita.")
        if pin:
            for b in MAIN_BOOKS:
                s = books.get(b, {})
                if s.get("ok") and s.get("saved", 0) < MIN_COVERAGE * pin:
                    warn(f"{b}: quote salvate per {s.get('saved', 0)} partite contro {pin} di "
                         f"Pinnacle (sotto il {MIN_COVERAGE:.0%}).")
        if books.get("Sisal", {}).get("ok") and books.get("Sisal", {}).get("saved", 0) == 0 and pin:
            warn("Sisal: nessuna quota salvata (di solito copre almeno la Premier League).")

        # partite non abbinate: normali oltre la finestra delle partite in arrivo
        vicine = [u for u in q.get("non_abbinate", []) if u["data"] <= horizon]
        if vicine:
            elenco = "; ".join(f"{u['campionato']}: {u['casa']} - {u['trasferta']} ({u['data']})"
                               for u in vicine[:8])
            abbinate = max(pin, max((s.get("saved", 0) for s in books.values()), default=0))
            livello = err if len(vicine) > max(3, 0.2 * (abbinate + len(vicine))) else warn
            quante = "1 partita vicina" if len(vicine) == 1 else f"{len(vicine)} partite vicine"
            livello(f"{quante} con quote ma senza abbinamento a una nostra partita (nome "
                    f"squadra diverso o partita mancante): {elenco}")

    # quote davvero salvate nel database in questo giro
    fresh = conn.execute("SELECT MAX(snapshot_time) FROM odds_snapshots").fetchone()[0]
    try:
        eta = now - datetime.fromisoformat(fresh)
        if eta > timedelta(hours=3):
            err(f"Le quote più recenti nel database sono di {eta.total_seconds() / 3600:.0f} "
                "ore fa: questo giro non ne ha salvate.")
    except (TypeError, ValueError):
        err("Nel database non ci sono quote.")

    # --- Previsioni -----------------------------------------------------------
    senza_prev = conn.execute(f"""
        SELECT COUNT(*) FROM matches m LEFT JOIN model_predictions p ON p.match_id = m.id
        WHERE m.home_goals IS NULL AND m.date >= ? AND m.date <= ? AND m.league IN ({ph})
        AND p.match_id IS NULL""", (today, horizon, *LEAGUES)).fetchone()[0]
    if total_upcoming and senza_prev == total_upcoming:
        err("Nessuna previsione del modello per le partite in arrivo (run_model.py).")
    elif senza_prev:
        warn(f"{senza_prev} partite in programma senza previsione del modello.")

    # --- Giocatori (BSD) e marcatori -------------------------------------------
    for step, nome in (("bsd", "statistiche giocatori (BSD)"), ("marcatori", "previsioni marcatori")):
        if outcomes.get(step) == "failure":
            warn(f"Il passaggio '{nome}' è fallito (il resto del giro è andato avanti).")
    settimana = (now.date() + timedelta(days=7)).isoformat()
    partite_sett = conn.execute(f"""
        SELECT COUNT(*) FROM matches WHERE home_goals IS NULL AND date >= ? AND date <= ?
        AND league IN ({ph})""", (today, settimana, *LEAGUES)).fetchone()[0]
    if partite_sett:
        con_marcatori = conn.execute(f"""
            SELECT COUNT(DISTINCT pp.match_id) FROM player_predictions pp
            JOIN matches m ON m.id = pp.match_id
            WHERE m.home_goals IS NULL AND m.date >= ? AND m.date <= ? AND m.league IN ({ph})""",
            (today, settimana, *LEAGUES)).fetchone()[0]
        if con_marcatori < 0.5 * partite_sett:
            warn(f"Previsioni marcatori solo per {con_marcatori} delle {partite_sett} partite "
                 "dei prossimi 7 giorni.")

    # --- Limite richieste OddsPapi ---------------------------------------------
    usate = conn.execute("""SELECT COUNT(*) FROM api_calls WHERE provider = 'oddspapi'
                            AND substr(called_at, 1, 7) = ?""", (now.strftime("%Y-%m"),)).fetchone()[0]
    if usate > MONTHLY_WARN:
        warn(f"Richieste OddsPapi questo mese: {usate} su 250.")

    return out


def save(conn, results, now):
    conn.execute("""CREATE TABLE IF NOT EXISTS health_checks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, run_at TEXT NOT NULL,
                        level TEXT NOT NULL, message TEXT NOT NULL)""")
    rows = results or [("OK", "Tutti i controlli superati.")]
    conn.executemany("INSERT INTO health_checks (run_at, level, message) VALUES (?, ?, ?)",
                     [(now.isoformat(), lvl, msg) for lvl, msg in rows])
    # teniamo solo gli ultimi 60 giri
    conn.execute("""DELETE FROM health_checks WHERE run_at NOT IN (
                        SELECT DISTINCT run_at FROM health_checks ORDER BY run_at DESC LIMIT 60)""")
    conn.commit()


def report(results, stats):
    errori = [m for l, m in results if l == "ERRORE"]
    avvisi = [m for l, m in results if l == "AVVISO"]
    for m in errori:
        print(f"::error title=Controllo dati::{m}")
    for m in avvisi:
        print(f"::warning title=Controllo dati::{m}")
    if not results:
        print("Controllo dati: tutto a posto.")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        return
    righe = ["## Controllo di salute dei dati", ""]
    if errori:
        righe.append(f"🔴 **Errori: {len(errori)}**, avvisi: {len(avvisi)}")
    elif avvisi:
        righe.append(f"🟡 Nessun errore, **avvisi: {len(avvisi)}**")
    else:
        righe.append("🟢 Tutti i controlli superati")
    righe.append("")
    righe += [f"- 🔴 {m}" for m in errori] + [f"- 🟡 {m}" for m in avvisi]
    books = stats.get("quote", {}).get("bookmaker", {})
    if books:
        righe += ["", "| Bookmaker | Risposta | Partite ricevute | Quote salvate |",
                  "|---|---|---|---|"]
        for b, s in books.items():
            righe.append(f"| {b} | {'✅' if s.get('ok') else '❌'} | {s.get('fixtures', 0)} | "
                         f"{s.get('saved', 0)} |")
    with open(summary, "a") as f:
        f.write("\n".join(righe) + "\n")


def main():
    conn = sqlite3.connect(DB_PATH)
    if "--esito" in sys.argv:
        try:
            row = conn.execute("""SELECT COUNT(*) FROM health_checks WHERE level = 'ERRORE'
                                  AND run_at = (SELECT MAX(run_at) FROM health_checks)""").fetchone()
        except sqlite3.OperationalError:
            row = None
        if row and row[0]:
            print(f"Il controllo dei dati ha trovato {row[0]} "
                  f"{'errore' if row[0] == 1 else 'errori'}: vedi il passaggio "
                  "'Controllo di salute dei dati' o il riepilogo in cima alla pagina.")
            sys.exit(1)
        print("Nessun errore nei dati.")
        return
    init_db(conn)
    now = datetime.now(timezone.utc)
    outcomes = {"bsd": os.environ.get("ESITO_BSD", ""), "marcatori": os.environ.get("ESITO_MARCATORI", "")}
    stats = run_stats.load()
    results = run_checks(conn, stats, now, outcomes)
    save(conn, results, now)
    report(results, stats)
    conn.close()


if __name__ == "__main__":
    main()
