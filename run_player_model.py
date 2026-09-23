"""
Calcola, per ogni partita in arrivo, la probabilità che ciascun giocatore
delle due squadre segni almeno un gol — combinando i gol attesi della
squadra (dal Dixon-Coles) con le statistiche individuali dei giocatori
(da BSD, scaricate da fetch_player_data.py).

Va eseguito dopo fetch_player_data.py e run_model.py (serve sia
expected_goals_home/away sia le statistiche giocatore già salvate).
"""

import sqlite3
from db_utils import init_db
from datetime import datetime, timezone
from scorer_model import distribute_team_goals

DB_PATH = "data.db"


def load_upcoming_matches_with_goals(conn):
    return conn.execute("""
        SELECT m.id, m.home_team_id, m.away_team_id, m.league,
               p.expected_goals_home, p.expected_goals_away
        FROM matches m
        JOIN model_predictions p ON p.match_id = m.id
        WHERE m.home_goals IS NULL AND m.date >= date('now')
          AND p.expected_goals_home IS NOT NULL
    """).fetchall()


RECENT_TEAM_MATCHES = 5    # partite recenti della squadra per stimare i minuti
STATS_WINDOW_DAYS = 365    # statistiche individuali: ultimi 12 mesi
PRIOR_MINUTES = 450        # "restringimento" verso la media per chi ha giocato poco
PRIOR_PER_90 = 0.10        # xG e gol per 90' di un giocatore qualunque


def load_team_players_stats(conn, team_id):
    """Per ogni giocatore ATTUALMENTE in rosa (e non infortunato):
    - xG/90 e gol/90 negli ultimi 12 mesi, "ristretti" verso una media
      generica quando i minuti sono pochi (5 minuti con un gol non fanno
      di nessuno un bomber da 18 gol a partita);
    - minuti attesi e probabilità di partire titolare, dalle ultime
      RECENT_TEAM_MATCHES partite della squadra (0 minuti se non ha giocato).
    """
    recent = [r[0] for r in conn.execute(f"""
        SELECT e.id FROM bsd_events e
        WHERE e.stats_done = 1 AND (
              e.home_bsd_team_id IN (SELECT bsd_id FROM bsd_teams WHERE team_id = ?)
           OR e.away_bsd_team_id IN (SELECT bsd_id FROM bsd_teams WHERE team_id = ?))
        ORDER BY e.event_date DESC LIMIT {RECENT_TEAM_MATCHES}
    """, (team_id, team_id))]
    if not recent:
        return []
    marks = ",".join("?" * len(recent))

    rows = conn.execute(f"""
        SELECT pl.id, pl.name,
               -- ultimi 12 mesi, tutte le squadre in cui ha giocato
               (SELECT SUM(minutes) FROM player_match_stats s
                 WHERE s.player_id = pl.id AND s.match_bsd_id > 0
                   AND s.match_date >= date('now', '-{STATS_WINDOW_DAYS} days')),
               (SELECT SUM(goals) FROM player_match_stats s
                 WHERE s.player_id = pl.id AND s.match_bsd_id > 0
                   AND s.match_date >= date('now', '-{STATS_WINDOW_DAYS} days')),
               (SELECT SUM(minutes) FROM player_match_stats s
                 WHERE s.player_id = pl.id AND s.match_bsd_id > 0 AND s.xg IS NOT NULL
                   AND s.match_date >= date('now', '-{STATS_WINDOW_DAYS} days')),
               (SELECT SUM(xg) FROM player_match_stats s
                 WHERE s.player_id = pl.id AND s.match_bsd_id > 0 AND s.xg IS NOT NULL
                   AND s.match_date >= date('now', '-{STATS_WINDOW_DAYS} days')),
               -- ultime partite della squadra
               (SELECT COALESCE(SUM(minutes), 0) FROM player_match_stats s
                 WHERE s.player_id = pl.id AND s.match_bsd_id IN ({marks})),
               (SELECT COALESCE(SUM(started), 0) FROM player_match_stats s
                 WHERE s.player_id = pl.id AND s.match_bsd_id IN ({marks}))
        FROM players pl
        WHERE pl.team_id = ?
          AND (pl.availability IS NULL OR pl.availability IN ('', 'available'))
    """, (*recent, *recent, team_id)).fetchall()

    n = len(recent)
    players_stats = []
    for player_id, name, mins, goals, mins_xg, xg, recent_mins, recent_starts in rows:
        expected_minutes = min(recent_mins / n, 90)
        if expected_minutes <= 0 or not mins:
            continue  # non ha giocato di recente: non lo consideriamo
        k = PRIOR_MINUTES
        xg90 = ((xg or 0) + PRIOR_PER_90 * k / 90) / ((mins_xg or 0) + k) * 90
        goals90 = ((goals or 0) + PRIOR_PER_90 * k / 90) / (mins + k) * 90
        players_stats.append({
            "player_id": player_id,
            "name": name,
            "xg_per_90": xg90,
            "goals_per_90": goals90,
            "expected_minutes": expected_minutes,
            "starting_probability": recent_starts / n,
        })
    return players_stats


def save_player_prediction(conn, player_id, match_id, result):
    conn.execute("""
        INSERT INTO player_predictions
            (player_id, match_id, expected_minutes, starting_probability,
             team_xg_share, expected_goals, prob_score_anytime, computed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(player_id, match_id) DO UPDATE SET
            expected_minutes = excluded.expected_minutes,
            starting_probability = excluded.starting_probability,
            team_xg_share = excluded.team_xg_share,
            expected_goals = excluded.expected_goals,
            prob_score_anytime = excluded.prob_score_anytime,
            computed_at = excluded.computed_at
    """, (player_id, match_id, round(result["expected_minutes"], 1),
          round(result["starting_probability"], 2), result["team_xg_share"],
          result["expected_goals"], result["prob_score_anytime"],
          datetime.now(timezone.utc).isoformat()))


def main():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    matches = load_upcoming_matches_with_goals(conn)
    print(f"Partite in arrivo con gol attesi disponibili: {len(matches)}")

    # Ricalcoliamo da zero: così spariscono i giocatori nel frattempo
    # infortunati o trasferiti, invece di restare con la previsione vecchia.
    if matches:
        ids = [m[0] for m in matches]
        conn.execute(f"DELETE FROM player_predictions WHERE match_id IN "
                     f"({','.join('?' * len(ids))})", ids)

    predicted = 0
    for match_id, home_team_id, away_team_id, league, eg_home, eg_away in matches:
        for team_id, team_expected_goals, lato in [
            (home_team_id, eg_home, "casa"), (away_team_id, eg_away, "ospite")
        ]:
            players_stats = load_team_players_stats(conn, team_id)
            if not players_stats:
                continue  # nessun dato giocatore per questa squadra, saltiamo

            results = distribute_team_goals(team_expected_goals, players_stats)
            for r in results:
                save_player_prediction(conn, r["player_id"], match_id, r)
                predicted += 1

    conn.commit()
    conn.close()
    print(f"Fatto. {predicted} previsioni marcatori salvate.")


if __name__ == "__main__":
    main()
