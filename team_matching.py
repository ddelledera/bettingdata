"""
Riconoscimento dei nomi delle squadre tra fonti diverse.

Lo storico (football-data.co.uk) chiama le squadre in un modo, football-data.org
e The Odds API in un altro ("Genoa CFC", "Sporting Clube de Portugal",
"Atlético Madrid"...). Se una squadra finisce salvata con due nomi, il modello
la tratta come due squadre diverse: niente previsione, oppure — peggio, nelle
coppe europee — una squadra "fantasma" che ha giocato solo 3-4 partite e
riceve una forza stimata del tutto irrealistica.

Qui ogni nome nuovo viene collegato alla squadra dello storico: prima con le
corrispondenze scritte a mano (FIXTURE_ALIASES), poi con un confronto parola
per parola abbastanza severo da non scambiare squadre diverse. Se non c'è
una corrispondenza chiara, il nome resta com'è e viene segnalato nel log.
"""

import difflib
import re
import unicodedata

from team_utils import normalize_team_name
from fetch_odds import ABBREVIATIONS

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
    "Real Sociedad de Fútbol": "Sociedad", "Real Sociedad": "Sociedad",
    # coppe europee (nomi di football-data.org e di The Odds API)
    "Atlético Madrid": "Ath Madrid", "Atletico Madrid": "Ath Madrid",
    "Celta Vigo": "Celta", "Girona FC": "Girona",
    "Sporting Clube de Portugal": "Sp Lisbon", "Sporting Lisbon": "Sp Lisbon",
    "Sporting CP": "Sp Lisbon", "Sport Lisboa e Benfica": "Benfica",
    "SC Braga": "Sp Braga", "Sporting Braga": "Sp Braga",
    "PSV": "PSV Eindhoven", "AFC Ajax": "Ajax", "Feyenoord Rotterdam": "Feyenoord",
    "FC Twente Enschede": "Twente", "NEC Nijmegen": "Nijmegen",
    "PAE Olympiakos SFP": "Olympiakos", "Olympiakos Piraeus": "Olympiakos",
    "Olympiacos": "Olympiakos", "PAE AEK": "AEK", "AEK Athens": "AEK",
    "Panathinaikos FC": "Panathinaikos", "PAOK Salonika": "PAOK",
    "Fenerbahçe SK": "Fenerbahce", "Galatasaray SK": "Galatasaray",
    "Besiktas JK": "Besiktas", "Beşiktaş JK": "Besiktas",
    "Club Brugge KV": "Club Brugge", "Sint Truiden": "St Truiden",
    "Royale Union Saint-Gilloise": "St. Gilloise", "Union Saint-Gilloise": "St. Gilloise",
    "KRC Genk": "Genk", "RSC Anderlecht": "Anderlecht", "KAA Gent": "Gent",
    "Rangers FC": "Rangers", "Heart of Midlothian": "Hearts",
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


def resolve_team(cur, name, league=None):
    """Id della squadra dello storico corrispondente a 'name', o None se non
    c'è una corrispondenza chiara (meglio nessun collegamento che uno
    sbagliato: in quel caso il log lo segnala). 'league' (se è un campionato
    nazionale) fa cercare prima tra le squadre di quel campionato."""
    marks = ",".join("?" * len(EUROPEAN))
    played = f"""SELECT DISTINCT t.id, t.name FROM teams t JOIN matches m
                 ON t.id IN (m.home_team_id, m.away_team_id)
                 WHERE m.home_goals IS NOT NULL AND m.league NOT IN ({marks})
                   AND m.date >= date('now', '-500 days') {{extra}}"""
    same_league = (cur.execute(played.format(extra="AND m.league = ?"),
                               (*EUROPEAN, league)).fetchall()
                   if league and league not in EUROPEAN else [])
    everywhere = cur.execute(played.format(extra=""), EUROPEAN).fetchall()

    # 1. nome già uniformato a mano in team_utils.py
    fixed = FIXTURE_ALIASES.get(name) or normalize_team_name(name)
    for tid, n in everywhere:
        if n == fixed:
            return tid
    # 2. somiglianza parola per parola: prima nello stesso campionato, poi
    #    ovunque (neopromosse che arrivano dalla serie inferiore)
    # tra TUTTE le squadre (circa 500, di tanti paesi) serve una somiglianza
    # più alta: "Red Star Belgrade" non deve diventare il Red Star di Parigi
    for rows, min_score in ((same_league, 0.8), (everywhere, 0.9)):
        scored = sorted(((_token_score(name, n), tid) for tid, n in rows), reverse=True)
        if scored and scored[0][0] >= min_score and \
                (len(scored) == 1 or scored[1][0] < scored[0][0]):
            return scored[0][1]
    return None


def repoint_match(cur, old_id, new_id):
    """Sposta tutto ciò che è collegato a una partita su un'altra."""
    for table in ("odds_snapshots", "reference_odds", "scorer_odds",
                  "player_predictions", "bsd_events"):
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
