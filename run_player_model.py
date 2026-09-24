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
PEN_XG = 0.79              # xG di un rigore: si toglie per avere gli npxG
# Stessi valori del protocollo congelato in backtest_marcatori.py.


def league_penalty_rate(conn, league):
    """Rigori tentati attesi per squadra e partita nel campionato, dall'ultimo
    anno (rigori segnati / 78% di trasformazione), con una piccola spinta
    verso la media come nel backtest."""
    goals, team_matches = conn.execute(f"""
        SELECT COALESCE(SUM(s.penalties_scored), 0), COUNT(DISTINCT e.id) * 2
        FROM bsd_events e LEFT JOIN player_match_stats s ON s.match_bsd_id = e.id
        WHERE e.league = ? AND e.pens_done = 1
          AND e.event_date >= date('now', '-{STATS_WINDOW_DAYS} days')""", (league,)).fetchone()
    from scorer_model import PEN_CONVERSION
    return (goals / PEN_CONVERSION + 2) / (team_matches + 20)


def load_team_players_stats(conn, team_id):
    """Per ogni giocatore ATTUALMENTE in rosa (e non infortunato):
    - npxG/90 negli ultimi 12 mesi (xG senza rigori), "ristretti" verso una
      media generica quando i minuti sono pochi;
    - rigori segnati nell'ultimo anno (per stimare il rigorista);
    - minuti attesi, frequenza di presenza e di titolarità nelle ultime
      RECENT_TEAM_MATCHES partite della squadra.
    Come nel backtest: le righe con tiri ma senza xG (inizio stagione) non
    contano negli xG; quelle senza tiri valgono xG zero.
    """
    team_bsd = [r[0] for r in conn.execute("SELECT bsd_id FROM bsd_teams WHERE team_id = ?", (team_id,))]
    if not team_bsd:
        return []
    tb = ",".join("?" * len(team_bsd))
    recent = [r[0] for r in conn.execute(f"""
        SELECT e.id FROM bsd_events e
        WHERE e.stats_done = 1 AND (e.home_bsd_team_id IN ({tb}) OR e.away_bsd_team_id IN ({tb}))
        ORDER BY e.event_date DESC LIMIT {RECENT_TEAM_MATCHES}
    """, (*team_bsd, *team_bsd))]
    if len(recent) < RECENT_TEAM_MATCHES:
        return []
    marks = ",".join("?" * len(recent))
    ok_xg = "NOT (s.xg IS NULL AND s.shots > 0)"
    window = f"s.match_date >= date('now', '-{STATS_WINDOW_DAYS} days')"

    rows = conn.execute(f"""
        SELECT pl.id, pl.name,
               (SELECT SUM(minutes) FROM player_match_stats s
                 WHERE s.player_id = pl.id AND {window}),
               (SELECT SUM(minutes) FROM player_match_stats s
                 WHERE s.player_id = pl.id AND {window} AND {ok_xg}),
               (SELECT SUM(COALESCE(xg, 0)) FROM player_match_stats s
                 WHERE s.player_id = pl.id AND {window} AND {ok_xg}),
               (SELECT SUM(COALESCE(penalties_scored, 0)) FROM player_match_stats s
                 WHERE s.player_id = pl.id AND {window}),
               (SELECT SUM(COALESCE(penalties_scored, 0)) FROM player_match_stats s
                 WHERE s.player_id = pl.id AND {window} AND {ok_xg}),
               -- ultime partite della squadra (solo con questa squadra)
               (SELECT COALESCE(SUM(minutes), 0) FROM player_match_stats s
                 WHERE s.player_id = pl.id AND s.match_bsd_id IN ({marks})
                   AND (s.bsd_team_id IS NULL OR s.bsd_team_id IN ({tb}))),
               (SELECT COALESCE(SUM(minutes > 0), 0) FROM player_match_stats s
                 WHERE s.player_id = pl.id AND s.match_bsd_id IN ({marks})
                   AND (s.bsd_team_id IS NULL OR s.bsd_team_id IN ({tb}))),
               (SELECT COALESCE(SUM(started), 0) FROM player_match_stats s
                 WHERE s.player_id = pl.id AND s.match_bsd_id IN ({marks})
                   AND (s.bsd_team_id IS NULL OR s.bsd_team_id IN ({tb})))
        FROM players pl
        WHERE pl.team_id = ?
          AND (pl.availability IS NULL OR pl.availability IN ('', 'available'))
    """, (*recent, *team_bsd, *recent, *team_bsd, *recent, *team_bsd, team_id)).fetchall()

    n = RECENT_TEAM_MATCHES
    k = PRIOR_MINUTES
    shrink = lambda num, den: (num + PRIOR_PER_90 * k / 90) / (den + k) * 90
    players_stats = []
    for (player_id, name, mins, mins_xg, xg, pens, pens_xgok,
         recent_mins, recent_apps, recent_starts) in rows:
        expected_minutes = min(recent_mins / n, 90)
        if expected_minutes <= 0 or not mins:
            continue  # non ha giocato di recente: non lo consideriamo
        players_stats.append({
            "player_id": player_id,
            "name": name,
            "npxg_per_90": shrink(max((xg or 0) - PEN_XG * (pens_xgok or 0), 0), mins_xg or 0),
            "penalties_scored": pens or 0,
            "expected_minutes": expected_minutes,
            "play_rate": recent_apps / n,
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

    # Ricalcoliamo da zero tutte le partite in arrivo: così spariscono i
    # giocatori nel frattempo infortunati o trasferiti (e le partite escluse),
    # invece di restare con la previsione vecchia.
    conn.execute("""DELETE FROM player_predictions WHERE match_id IN (
                        SELECT id FROM matches WHERE date >= date('now'))""")

    predicted = 0
    pen_rates = {}
    for match_id, home_team_id, away_team_id, league, eg_home, eg_away in matches:
        if league not in pen_rates:
            pen_rates[league] = league_penalty_rate(conn, league)
        for team_id, team_expected_goals, lato in [
            (home_team_id, eg_home, "casa"), (away_team_id, eg_away, "ospite")
        ]:
            players_stats = load_team_players_stats(conn, team_id)
            if not players_stats:
                continue  # nessun dato giocatore per questa squadra, saltiamo

            results = distribute_team_goals(team_expected_goals, players_stats, pen_rates[league])
            for r in results:
                save_player_prediction(conn, r["player_id"], match_id, r)
                predicted += 1

    conn.commit()
    conn.close()
    print(f"Fatto. {predicted} previsioni marcatori salvate.")


if __name__ == "__main__":
    main()
