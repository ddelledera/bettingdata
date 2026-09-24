"""
Decide se in questo momento serve scaricare la quota di chiusura di
Pinnacle (per misurare bene il CLV della prova dal vivo).

Gira ogni mezz'ora (workflow closing.yml) e usa SOLO la libreria standard
di Python: nella grande maggioranza dei casi la risposta è "no" e il
workflow si ferma qui in pochi secondi, senza installare niente, senza
richieste all'API e senza salvare nulla.

Serve una chiusura quando:
  - una partita con scommesse virtuali inizia entro CLOSE_WINDOW_MIN minuti;
  - per quella partita non abbiamo già una quota Pinnacle presa negli
    ultimi ALREADY_CLOSE_MIN minuti prima dell'inizio;
  - c'è ancora spazio nel limite mensile di OddsPapi.
Una sola richiesta copre TUTTE le partite dei 5 campionati, quindi le
partite che iniziano alla stessa ora costano una richiesta in tutto.
"""

import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = "data.db"
CLOSE_WINDOW_MIN = 40      # guardiamo le partite che iniziano entro 40 minuti
ALREADY_CLOSE_MIN = 45     # una quota presa a meno di 45' dall'inizio basta già
MONTHLY_LIMIT = 250        # piano gratuito OddsPapi
CLOSING_MONTHLY_CAP = 80   # massimo di richieste "chiusura" al mese
DAILY_RESERVE = 6          # richieste da tenere da parte per ogni giorno rimasto
                           # del mese (il giro giornaliero ne usa circa 5)


def closing_targets(conn, now):
    """Partite con scommesse virtuali che iniziano a breve e non hanno
    ancora una quota Pinnacle abbastanza vicina all'inizio."""
    now_s = now.strftime("%Y-%m-%d %H:%M:%S")
    return conn.execute(f"""
        SELECT m.id, m.kickoff_utc FROM matches m
        WHERE m.home_goals IS NULL AND m.kickoff_utc IS NOT NULL
          AND m.kickoff_utc > datetime(?)
          AND m.kickoff_utc <= datetime(?, '+{CLOSE_WINDOW_MIN} minutes')
          AND EXISTS (SELECT 1 FROM virtual_bets v WHERE v.match_id = m.id)
          AND NOT EXISTS (
              SELECT 1 FROM reference_odds r
              WHERE r.match_id = m.id AND r.bookmaker = 'Pinnacle'
                AND datetime(r.snapshot_time) >= datetime(m.kickoff_utc, '-{ALREADY_CLOSE_MIN} minutes'))
        ORDER BY m.kickoff_utc
    """, (now_s, now_s)).fetchall()


def budget_status(conn, now):
    """(si può fare una richiesta?, spiegazione)"""
    month = now.strftime("%Y-%m")
    used, closing = conn.execute("""
        SELECT COUNT(*), COALESCE(SUM(purpose = 'chiusura'), 0) FROM api_calls
        WHERE provider = 'oddspapi' AND substr(called_at, 1, 7) = ?""", (month,)).fetchone()
    # giorni rimasti nel mese, oggi escluso (il giro di oggi potrebbe essere già fatto)
    next_month = datetime(now.year + (now.month == 12), now.month % 12 + 1, 1, tzinfo=timezone.utc)
    days_left = (next_month - now).days
    reserve = days_left * DAILY_RESERVE
    detail = (f"richieste registrate questo mese: {used} (di cui chiusura: {closing}); "
              f"riserva per i giri giornalieri: {reserve}")
    if closing >= CLOSING_MONTHLY_CAP:
        return False, f"tetto mensile delle chiusure raggiunto ({CLOSING_MONTHLY_CAP}) — {detail}"
    if used + reserve + 1 > MONTHLY_LIMIT:
        return False, f"limite mensile OddsPapi troppo vicino — {detail}"
    return True, detail


def main():
    now = datetime.now(timezone.utc)
    needed = False
    try:
        conn = sqlite3.connect(DB_PATH)
        targets = closing_targets(conn, now)
        if not targets:
            print("Nessuna partita con scommesse virtuali in partenza a breve: niente da fare.")
        else:
            ok, detail = budget_status(conn, now)
            print(f"Partite in partenza senza chiusura: {len(targets)} "
                  f"(prima alle {targets[0][1]} UTC). {detail}")
            if ok:
                needed = True
            else:
                print("Salto: niente richiesta. Resta valida l'ultima quota del giro giornaliero.")
        conn.close()
    except sqlite3.OperationalError as e:
        # database di una versione precedente (colonne/tabelle nuove non
        # ancora create): le crea il prossimo giro giornaliero
        print(f"Database non ancora aggiornato ({e}): salto.")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"needed={'true' if needed else 'false'}\n")


if __name__ == "__main__":
    main()
