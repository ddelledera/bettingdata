"""
PROVA DAL VIVO DEI MARCATORI — senza giocare soldi.

Il modello marcatori è calibrato sui risultati (backtest_marcatori.py), ma
non sappiamo ancora se trova VALORE contro le quote vere. Qui, a ogni giro
giornaliero (dopo run_player_model.py):
  1. registra ogni quota marcatore di un bookmaker italiano (Eurobet,
     bet365) sopra la nostra probabilità "se gioca" (odds × prob >= 1), per
     le partite non ancora iniziate, con la quota e la probabilità di quel
     momento. Solo quote ancora offerte nell'ultimo giro.
  2. aggiorna la "chiusura": l'ultima quota vista prima del calcio d'inizio.
     Se la quota è scesa, il mercato si è mosso nella nostra direzione.
     (Le quote marcatore si scaricano solo nel giro del mattino, quindi la
     chiusura è quella del mattino della partita.)
  3. chiude le scommesse delle partite giocate: segna / non segna, oppure
     rimborsata se il giocatore non è sceso in campo (come fanno i bookmaker).
Nessuna richiesta alle API: usa solo i dati già nel database.
"""

import sqlite3
from datetime import datetime, timezone

from db_utils import init_db

DB_PATH = "data.db"
MAX_ODDS = 10.0          # oltre, quote da "sogno" con troppo rumore
FRESH_HOURS = 3          # come la dashboard: quota riproposta nell'ultimo giro


def latest_scorer_odds(conn):
    """Ultima quota di ogni (partita, giocatore, bookmaker) per le partite non
    ancora iniziate, solo se riproposta nell'ultimo giro di quella partita."""
    started = ("(m.kickoff_utc IS NOT NULL AND m.kickoff_utc <= datetime('now')) "
               "OR (m.kickoff_utc IS NULL AND m.date < date('now'))")
    return conn.execute(f"""
        WITH ultima AS (
            SELECT s.match_id, s.player_id, s.bookmaker, s.odds, s.snapshot_time,
                   ROW_NUMBER() OVER (PARTITION BY s.match_id, s.player_id, s.bookmaker
                                      ORDER BY s.snapshot_time DESC) AS rn
            FROM scorer_odds s JOIN matches m ON m.id = s.match_id
            WHERE s.player_id IS NOT NULL AND m.home_goals IS NULL AND NOT ({started})
        ),
        giro AS (SELECT match_id, bookmaker, MAX(snapshot_time) AS t FROM ultima GROUP BY 1, 2)
        SELECT u.match_id, u.player_id, u.bookmaker, u.odds, u.snapshot_time
        FROM ultima u JOIN giro g ON g.match_id = u.match_id AND g.bookmaker = u.bookmaker
        WHERE u.rn = 1 AND datetime(u.snapshot_time) >= datetime(g.t, '-{FRESH_HOURS} hours')
    """).fetchall()


def record(conn, now):
    cur = conn.cursor()
    preds = {(r[0], r[1]): r[2:] for r in cur.execute(
        "SELECT player_id, match_id, prob_score_anytime, expected_minutes, starting_probability "
        "FROM player_predictions")}
    new = updated = 0
    for match_id, player_id, book, odds, snap in latest_scorer_odds(conn):
        # chiusura: l'ultima quota vista (la partita non è ancora iniziata)
        cur.execute("""UPDATE virtual_scorer_bets SET close_odds = ?, close_updated_at = ?
                       WHERE match_id = ? AND player_id = ? AND bookmaker = ? AND result IS NULL""",
                    (odds, snap, match_id, player_id, book))
        updated += cur.rowcount
        p = preds.get((player_id, match_id))
        if not p or not p[0] or odds > MAX_ODDS or odds * p[0] < 1:
            continue
        cur.execute("""INSERT OR IGNORE INTO virtual_scorer_bets
                           (match_id, player_id, bookmaker, odds, model_prob, edge,
                            expected_minutes, starting_probability, found_at,
                            close_odds, close_updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (match_id, player_id, book, odds, p[0], odds * p[0] - 1, p[1], p[2],
                     now, odds, snap))
        new += cur.rowcount
    conn.commit()
    return new, updated


def settle(conn):
    """Esito delle scommesse sulle partite giocate, dalle statistiche BSD."""
    cur = conn.cursor()
    todo = cur.execute("""
        SELECT v.id, v.player_id, e.id FROM virtual_scorer_bets v
        JOIN bsd_events e ON e.match_id = v.match_id
        WHERE v.result IS NULL AND e.stats_done = 1""").fetchall()
    n = 0
    for bet_id, player_id, event_id in todo:
        row = cur.execute("""SELECT minutes, goals FROM player_match_stats
                             WHERE player_id = ? AND match_bsd_id = ?""", (player_id, event_id)).fetchone()
        if not row or not row[0]:
            result = "rimborsata"          # non è sceso in campo
        else:
            result = "segna" if (row[1] or 0) > 0 else "non segna"
        cur.execute("UPDATE virtual_scorer_bets SET result = ? WHERE id = ?", (result, bet_id))
        n += 1
    conn.commit()
    return n


def main():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    now = datetime.now(timezone.utc).isoformat()
    new, updated = record(conn, now)
    closed = settle(conn)
    tot = conn.execute("SELECT COUNT(*), SUM(result IS NULL) FROM virtual_scorer_bets").fetchone()
    print(f"Scommesse virtuali marcatori: {new} nuove, {updated} chiusure aggiornate, "
          f"{closed} concluse oggi. Totale {tot[0]} (in attesa {tot[1] or 0}).")
    conn.close()


if __name__ == "__main__":
    main()
