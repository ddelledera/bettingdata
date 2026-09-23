"""
Scarica le partite in programma nei prossimi giorni (non ancora giocate)
per i campionati che seguiamo, e le inserisce nel database.

Fonte: football-data.org (livello gratuito). Serve una chiave API gratuita,
te lo spiego tra un attimo: è un passaggio manuale, ma richiede letteralmente
un click e un copia-incolla, niente di tecnico.

La chiave va inserita come "variabile d'ambiente" chiamata
FOOTBALL_DATA_API_KEY. Quando arriveremo a pubblicare l'app, ti mostrerò
esattamente dove incollarla (una casella di testo, non codice).
"""

import os
import sqlite3
from db_utils import init_db
import requests
from datetime import date, timedelta
import difflib
import re
import unicodedata

from team_utils import get_or_create_team, normalize_team_name
from fetch_odds import ABBREVIATIONS

DB_PATH = "data.db"
API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY", "")
BASE_URL = "https://api.football-data.org/v4/competitions/{code}/matches"

# Codice-competizione usato da football-data.org per ciascun campionato
COMPETITION_CODES = {
    "SA": "Serie A",
    "PL": "Premier League",
    "PD": "La Liga",
    "BL1": "Bundesliga",
    "FL1": "Ligue 1",
}

DAYS_AHEAD = 21  # quante partite future guardare (abbastanza da superare le
                  # soste per le nazionali, che durano fino a 2 settimane)


def fetch_upcoming(competition_code):
    if not API_KEY:
        raise RuntimeError(
            "Manca la chiave API (FOOTBALL_DATA_API_KEY). "
            "Va impostata prima di eseguire questo script."
        )
    date_from = date.today().isoformat()
    date_to = (date.today() + timedelta(days=DAYS_AHEAD)).isoformat()
    url = BASE_URL.format(code=competition_code)
    # NOTA: non filtriamo per "status" lato server. football-data.org usa
    # SCHEDULED per le partite senza orario confermato e TIMED per quelle
    # con orario già fissato (la maggior parte, nel breve periodo) — filtrare
    # solo su SCHEDULED escluderebbe quasi tutte le partite reali. Filtriamo
    # invece noi stessi, escludendo solo quelle già concluse o annullate.
    response = requests.get(
        url,
        headers={"X-Auth-Token": API_KEY},
        params={"dateFrom": date_from, "dateTo": date_to},
        timeout=20,
    )
    response.raise_for_status()
    all_matches = response.json().get("matches", [])
    escluse = {"FINISHED", "POSTPONED", "CANCELLED", "SUSPENDED", "AWARDED"}
    return [m for m in all_matches if m.get("status") not in escluse]


# ---------------------------------------------------------------------------
# NOMI SQUADRE. football-data.org chiama le squadre in modo diverso dallo
# storico (football-data.co.uk): "Genoa CFC" invece di "Genoa", "US Lecce"
# invece di "Lecce", ecc. Se salviamo il nome così com'è, il modello non
# riconosce la squadra e NON fa la previsione (e il risultato finale non si
# aggancia mai alla partita). Qui colleghiamo ogni nome alla squadra già
# presente nello storico, confrontando i nomi per somiglianza.
# ---------------------------------------------------------------------------
EUROPEAN = ("Champions League", "Europa League", "Conference League")

# Parole che non distinguono una squadra dall'altra ("Genoa CFC" = "Genoa")
FILLER = {"fc", "cf", "cfc", "afc", "ac", "as", "ss", "ssc", "us", "sc", "sv", "fk",
          "rc", "rcd", "cd", "ud", "ca", "sco", "osc", "ogc", "losc", "vfb", "vfl",
          "tsg", "fsv", "calcio", "club", "de", "la", "real", "racing", "deportivo",
          "city", "town", "united", "wanderers", "athletic"}


# Nomi che il confronto automatico non collega con sicurezza (troppo diversi
# o ambigui, es. "Espanyol de Barcelona"): corrispondenza scritta a mano.
# Se il log segnala una squadra non riconosciuta, va aggiunta qui.
FIXTURE_ALIASES = {
    "RCD Espanyol de Barcelona": "Espanol", "Athletic Club": "Ath Bilbao",
    "Rayo Vallecano de Madrid": "Vallecano", "RC Celta de Vigo": "Celta",
    "AJ Auxerre": "Auxerre", "ES Troyes AC": "Troyes",
    "RC Strasbourg Alsace": "Strasbourg", "Stade Brestois 29": "Brest",
}


def _tokens(name):
    name = ABBREVIATIONS.get(name, name)
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    words = re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    kept = [w for w in words if w not in FILLER and not w.isdigit()]
    return kept or [w for w in words if not w.isdigit()] or words


def _token_score(a, b):
    """Somiglianza tra due nomi squadra, parola per parola e in ENTRAMBI i
    sensi: "Espanyol Barcelona" contro "Barcelona" fa solo 0.75 (manca
    "espanyol"), così non viene scambiata per il Barcellona."""
    ta, tb = _tokens(a), _tokens(b)
    def covered(x, y):
        return sum(max(difflib.SequenceMatcher(None, w, v).ratio() for v in y) >= 0.85
                   for w in x) / len(x)
    return (covered(ta, tb) + covered(tb, ta)) / 2


def resolve_team(cur, name, league):
    """Id della squadra dello storico corrispondente a 'name', o None se non
    c'è una corrispondenza chiara (meglio nessun collegamento che uno
    sbagliato: in quel caso il log lo segnala)."""
    marks = ",".join("?" * len(EUROPEAN))
    played = f"""SELECT DISTINCT t.id, t.name FROM teams t JOIN matches m
                 ON t.id IN (m.home_team_id, m.away_team_id)
                 WHERE m.home_goals IS NOT NULL AND m.league NOT IN ({marks})
                   AND m.date >= date('now', '-500 days') {{extra}}"""
    same_league = cur.execute(played.format(extra="AND m.league = ?"),
                              (*EUROPEAN, league)).fetchall()
    everywhere = cur.execute(played.format(extra=""), EUROPEAN).fetchall()

    # 1. nome già uniformato a mano in team_utils.py
    fixed = FIXTURE_ALIASES.get(name) or normalize_team_name(name)
    for tid, n in everywhere:
        if n == fixed:
            return tid
    # 2. somiglianza parola per parola: prima nello stesso campionato, poi
    #    ovunque (neopromosse che arrivano dalla serie inferiore)
    for rows in (same_league, everywhere):
        scored = sorted(((_token_score(name, n), tid) for tid, n in rows), reverse=True)
        if scored and scored[0][0] >= 0.8 and \
                (len(scored) == 1 or scored[1][0] < scored[0][0]):
            return scored[0][1]
    return None


def repoint_match(cur, old_id, new_id):
    """Sposta tutto ciò che è collegato a una partita su un'altra."""
    for table in ("odds_snapshots", "reference_odds", "player_predictions", "bsd_events"):
        cur.execute(f"UPDATE {table} SET match_id = ? WHERE match_id = ?", (new_id, old_id))
    cur.execute("UPDATE OR IGNORE model_predictions SET match_id = ? WHERE match_id = ?",
                (new_id, old_id))
    cur.execute("DELETE FROM model_predictions WHERE match_id = ?", (old_id,))


def repair_team_names(conn, leagues):
    """Ripara le partite già salvate con il nome 'sbagliato': le riassegna
    alla squadra dello storico, conservando quote, previsioni e schedine
    (l'id della partita non cambia). Se nel frattempo lo storico ha già
    inserito la stessa partita col nome giusto (e il risultato), le due
    vengono unite in una sola."""
    cur = conn.cursor()
    marks = ",".join("?" * len(leagues))
    orphans = cur.execute(f"""
        SELECT DISTINCT t.id, t.name, m.league FROM teams t
        JOIN matches m ON t.id IN (m.home_team_id, m.away_team_id)
        WHERE m.league IN ({marks}) AND t.id NOT IN (
            SELECT home_team_id FROM matches WHERE home_goals IS NOT NULL
                AND league NOT IN ({",".join("?" * len(EUROPEAN))})
            UNION SELECT away_team_id FROM matches WHERE home_goals IS NOT NULL
                AND league NOT IN ({",".join("?" * len(EUROPEAN))}))
    """, (*leagues, *EUROPEAN, *EUROPEAN)).fetchall()
    fixed = []
    for bad_id, bad_name, league in orphans:
        good_id = resolve_team(cur, bad_name, league)
        if good_id is None or good_id == bad_id:
            continue
        for side in ("home_team_id", "away_team_id"):
            other = "away_team_id" if side == "home_team_id" else "home_team_id"
            for mid, mdate, other_id in cur.execute(
                    f"SELECT id, date, {other} FROM matches WHERE {side} = ?", (bad_id,)).fetchall():
                twin = cur.execute(f"""SELECT id, home_goals, away_goals FROM matches
                                       WHERE date = ? AND {side} = ? AND {other} = ?""",
                                   (mdate, good_id, other_id)).fetchone()
                if twin:
                    twin_id, hg, ag = twin
                    repoint_match(cur, twin_id, mid)
                    cur.execute("DELETE FROM matches WHERE id = ?", (twin_id,))
                    if hg is not None:
                        cur.execute("UPDATE matches SET home_goals = ?, away_goals = ? WHERE id = ?",
                                    (hg, ag, mid))
                cur.execute(f"UPDATE matches SET {side} = ? WHERE id = ?", (good_id, mid))
        cur.execute("UPDATE players SET team_id = ? WHERE team_id = ?", (good_id, bad_id))
        cur.execute("UPDATE OR IGNORE bsd_teams SET team_id = ? WHERE team_id = ?", (good_id, bad_id))
        good_name = cur.execute("SELECT name FROM teams WHERE id = ?", (good_id,)).fetchone()[0]
        fixed.append(f"{bad_name} -> {good_name}")
    conn.commit()
    if fixed:
        print(f"Nomi squadra corretti ({len(fixed)}): " + ", ".join(fixed))


def load_into_db(matches, league_name, conn):
    cur = conn.cursor()
    inserted = 0
    unresolved = set()
    for m in matches:
        home_name = m["homeTeam"]["name"]
        away_name = m["awayTeam"]["name"]
        match_date = m["utcDate"][:10]  # es. "2026-09-27"

        home_id = resolve_team(cur, home_name, league_name)
        away_id = resolve_team(cur, away_name, league_name)
        for name, tid in ((home_name, home_id), (away_name, away_id)):
            if tid is None:
                unresolved.add(name)
        home_id = home_id or get_or_create_team(cur, home_name)
        away_id = away_id or get_or_create_team(cur, away_name)

        cur.execute("""
            INSERT INTO matches (date, league, season, home_team_id, away_team_id,
                                  home_goals, away_goals)
            VALUES (?, ?, ?, ?, ?, NULL, NULL)
            ON CONFLICT(date, home_team_id, away_team_id) DO NOTHING
        """, (match_date, league_name, "current", home_id, away_id))
        inserted += 1
    conn.commit()
    if unresolved:
        # il modello non potrà prevedere queste partite: nome da aggiungere
        # in team_utils.py (NORMALIZE_MAP)
        print(f"  ATTENZIONE, squadre non riconosciute: {sorted(unresolved)}")
    return inserted


def main():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    repair_team_names(conn, list(COMPETITION_CODES.values()))

    for code, league_name in COMPETITION_CODES.items():
        print(f"Controllo partite in arrivo: {league_name}...")
        matches = fetch_upcoming(code)
        n = load_into_db(matches, league_name, conn)
        print(f"  -> {n} partite in programma trovate")

    conn.close()


if __name__ == "__main__":
    main()
