"""
Il "direttore d'orchestra": prende i dati storici dal database, allena il
modello, e calcola le probabilità per le partite in arrivo. Salva i
risultati nella tabella model_predictions, pronti per essere mostrati
nella pagina web.

Va eseguito dopo aver aggiornato i dati (ingest_historical.py,
fetch_fixtures.py, fetch_odds.py).
"""

import sqlite3
from datetime import datetime, date, timezone
from model import DixonColesModel

DB_PATH = "data.db"


def load_training_matches(conn, league):
    """Carica le partite già giocate DI UN CAMPIONATO, con quanti giorni fa
    sono avvenute. Alleniamo un modello per campionato: squadre di leghe
    diverse non giocano mai tra loro, quindi mischiarle in un unico modello
    non avrebbe senso (e sarebbe anche molto più lento)."""
    today = date.today()
    rows = conn.execute("""
        SELECT m.date, h.name, a.name, m.home_goals, m.away_goals
        FROM matches m
        JOIN teams h ON m.home_team_id = h.id
        JOIN teams a ON m.away_team_id = a.id
        WHERE m.home_goals IS NOT NULL AND m.away_goals IS NOT NULL
          AND m.league = ?
    """, (league,)).fetchall()

    matches = []
    for match_date, home, away, hg, ag in rows:
        d = datetime.strptime(match_date, "%Y-%m-%d").date()
        days_ago = (today - d).days
        matches.append({
            "home_team": home, "away_team": away,
            "home_goals": hg, "away_goals": ag,
            "days_ago": max(days_ago, 0),
        })
    return matches


def load_upcoming_matches(conn, league):
    rows = conn.execute("""
        SELECT m.id, h.name, a.name
        FROM matches m
        JOIN teams h ON m.home_team_id = h.id
        JOIN teams a ON m.away_team_id = a.id
        WHERE m.home_goals IS NULL AND m.date >= date('now') AND m.league = ?
    """, (league,)).fetchall()
    return rows


def list_leagues(conn):
    rows = conn.execute("SELECT DISTINCT league FROM matches").fetchall()
    return [r[0] for r in rows]


def save_prediction(conn, match_id, prob_home, prob_draw, prob_away):
    conn.execute("""
        INSERT INTO model_predictions (match_id, prob_home, prob_draw, prob_away, computed_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(match_id) DO UPDATE SET
            prob_home = excluded.prob_home,
            prob_draw = excluded.prob_draw,
            prob_away = excluded.prob_away,
            computed_at = excluded.computed_at
    """, (match_id, prob_home, prob_draw, prob_away, datetime.now(timezone.utc).isoformat()))


def main():
    conn = sqlite3.connect(DB_PATH)
    with open("schema.sql") as f:
        conn.executescript(f.read())

    leagues = list_leagues(conn)
    print(f"Campionati trovati nel database: {leagues}")

    predicted = 0
    for league in leagues:
        training_matches = load_training_matches(conn, league)
        print(f"\n{league}: {len(training_matches)} partite storiche disponibili")
        if len(training_matches) < 50:
            print("  Attenzione: pochi dati storici, previsioni poco affidabili per ora.")
            if not training_matches:
                continue

        model = DixonColesModel()
        model.fit(training_matches)
        print(f"  Modello allenato ({len(model.teams)} squadre).")

        upcoming = load_upcoming_matches(conn, league)
        print(f"  Partite in arrivo da prevedere: {len(upcoming)}")

        for match_id, home, away in upcoming:
            if home not in model.teams or away not in model.teams:
                continue  # squadra mai vista nei dati storici: non possiamo stimarla
            ph, pd_, pa = model.predict_match(home, away)
            save_prediction(conn, match_id, ph, pd_, pa)
            predicted += 1
            print(f"    {home} vs {away}: casa {ph:.0%}  pareggio {pd_:.0%}  trasferta {pa:.0%}")

    conn.commit()
    conn.close()
    print(f"\nFatto. {predicted} previsioni salvate in totale.")


if __name__ == "__main__":
    main()
