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


def load_team_players_stats(conn, team_id):
    """Statistiche stagionali dei giocatori di una squadra, già pronte per
    il modello di distribuzione (xG/90, gol/90, minuti attesi)."""
    rows = conn.execute("""
        SELECT pl.id, pl.name, s.xg, s.goals, s.minutes
        FROM players pl
        JOIN player_match_stats s ON s.player_id = pl.id AND s.match_bsd_id = -1
        WHERE pl.team_id = ?
    """, (team_id,)).fetchall()

    players_stats = []
    for player_id, name, xg, goals, minutes in rows:
        if not minutes or minutes <= 0:
            continue  # nessun minuto giocato: non possiamo stimare nulla di utile
        # "Minuti attesi" qui è una prima stima grezza: i minuti medi a
        # partita in stagione. Una volta collegate le formazioni ufficiali
        # (vicino al calcio d'inizio), questo numero potrà essere affinato
        # in base a titolarità reale invece che alla sola media stagionale.
        matches_played = max(minutes / 75, 1)  # stima approssimativa di quante partite
        expected_minutes = min(minutes / matches_played, 90)
        players_stats.append({
            "player_id": player_id,
            "name": name,
            "xg_per_90": (xg / minutes) * 90 if xg else 0,
            "goals_per_90": (goals / minutes) * 90 if goals else 0,
            "expected_minutes": expected_minutes,
        })
    return players_stats


def save_player_prediction(conn, player_id, match_id, result):
    conn.execute("""
        INSERT INTO player_predictions
            (player_id, match_id, expected_minutes, starting_probability,
             team_xg_share, expected_goals, prob_score_anytime, computed_at)
        VALUES (?, ?, ?, NULL, ?, ?, ?, ?)
        ON CONFLICT(player_id, match_id) DO UPDATE SET
            expected_minutes = excluded.expected_minutes,
            team_xg_share = excluded.team_xg_share,
            expected_goals = excluded.expected_goals,
            prob_score_anytime = excluded.prob_score_anytime,
            computed_at = excluded.computed_at
    """, (player_id, match_id, result["expected_minutes"], result["team_xg_share"],
          result["expected_goals"], result["prob_score_anytime"],
          datetime.now(timezone.utc).isoformat()))


def main():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    matches = load_upcoming_matches_with_goals(conn)
    print(f"Partite in arrivo con gol attesi disponibili: {len(matches)}")

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
