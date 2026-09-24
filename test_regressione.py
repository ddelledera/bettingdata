"""
TEST DI REGRESSIONE — controlli automatici che girano a ogni modifica del
codice (workflow "Test"). Se uno fallisce, il workflow diventa rosso e dice
quale: vuol dire che una modifica ha rotto qualcosa che prima funzionava.

Non fanno richieste alle API e non toccano data.db (usano database in
memoria), tranne l'ultimo, che apre la dashboard sul data.db del progetto
per controllare che nessuna scheda vada in errore.

Si lanciano con:  pytest test_regressione.py
"""

import itertools
import math
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import markets
import value_calculator as vc
from db_utils import init_db


def fresh_db():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    return conn


# ---------------------------------------------------------------------------
# Mercati e prezzo giusto
# ---------------------------------------------------------------------------
def test_prezzo_giusto_somma_uno_e_toglie_il_margine():
    fair = markets.fair_probabilities({"Home": 2.0, "Draw": 3.4, "Away": 4.0,
                                       "Over2.5": 1.9, "Under2.5": 1.95})
    assert abs(fair["Home"] + fair["Draw"] + fair["Away"] - 1) < 1e-9
    assert abs(fair["Over2.5"] + fair["Under2.5"] - 1) < 1e-9
    assert abs(fair["1X"] - (fair["Home"] + fair["Draw"])) < 1e-9
    # col margine tolto la probabilità è più bassa di 1/quota
    assert fair["Home"] < 1 / 2.0 and fair["Away"] < 1 / 4.0


def test_metodo_potenza_toglie_piu_margine_agli_sfavoriti():
    odds = [1.30, 5.5, 11.0]
    power = markets.devig_power(odds)
    inv = [1 / o for o in odds]
    prop = [x / sum(inv) for x in inv]
    assert power[2] < prop[2]      # lo sfavorito vale meno che col metodo proporzionale
    assert power[0] > prop[0]


def test_esiti_delle_selezioni():
    assert markets.is_winner("Home", 2, 1) and not markets.is_winner("Home", 1, 1)
    assert markets.is_winner("12", 0, 1) and not markets.is_winner("12", 2, 2)
    assert markets.is_winner("Goal", 1, 1) and markets.is_winner("NoGoal", 3, 0)
    assert markets.is_winner("Under2.5", 1, 1) and not markets.is_winner("Under2.5", 2, 1)
    assert markets.market_of(markets.scorer_key(12)) == "Marcatori"


# ---------------------------------------------------------------------------
# Schedina: programmazione dinamica contro la forza bruta
# ---------------------------------------------------------------------------
def _legs(seed):
    import random
    rnd = random.Random(seed)
    return {m: [{"model_probability": p, "odds": round(1 / p * rnd.uniform(1.0, 1.08), 2)}
                for p in (rnd.uniform(0.2, 0.85) for _ in range(rnd.randint(1, 3)))]
            for m in range(rnd.randint(3, 5))}


@pytest.mark.parametrize("seed", range(8))
def test_combinazione_migliore_come_forza_bruta(seed):
    legs = _legs(seed)
    k, target = 2, 1.0
    res = vc.find_best_combination(legs, k, target)
    best = None
    for ms in itertools.combinations(legs, k):
        for combo in itertools.product(*(legs[m] for m in ms)):
            q = math.prod(l["odds"] for l in combo)
            p = math.prod(l["model_probability"] for l in combo)
            if q >= 1 + target and (best is None or p > best):
                best = p
    if best is None:
        assert res[1] is False
    else:
        assert res[1] is True
        assert abs(math.prod(l["model_probability"] for l in res[0]) - best) < 1e-6


@pytest.mark.parametrize("seed", range(5))
def test_frontiera_equilibrio_contiene_la_migliore_di_kelly(seed):
    legs = _legs(seed + 100)
    front = vc.find_tradeoff_frontier(legs, 2)
    best = max(vc.kelly_growth(math.prod(l["model_probability"] for l in c),
                               math.prod(l["odds"] for l in c))
               for ms in itertools.combinations(legs, 2)
               for c in itertools.product(*(legs[m] for m in ms)))
    assert max(x["kelly_growth"] for x in front) >= best * 0.999 - 1e-12


def test_kelly_zero_senza_valore():
    assert vc.kelly_fraction(0.5, 1.9) == 0
    assert vc.kelly_growth(0.5, 1.9) == 0
    assert vc.kelly_fraction(0.6, 2.0) > 0


# ---------------------------------------------------------------------------
# Quote: niente quote live, chiusura solo prima dell'inizio, ID esterni
# ---------------------------------------------------------------------------
def test_partita_iniziata_riconosciuta():
    import fetch_odds as fo
    assert fo.fixture_started({"statusId": 1, "startTime": "2099-01-01T00:00:00Z"})
    assert fo.fixture_started({"statusId": 0, "startTime": "2020-01-01T00:00:00Z"})
    assert not fo.fixture_started({"statusId": 0, "startTime": "2099-01-01T00:00:00Z"})


def _match(conn, kickoff, league="Serie A"):
    cur = conn.cursor()
    cur.execute("INSERT INTO teams (name) VALUES ('Casa'), ('Ospite')")
    cur.execute("""INSERT INTO matches (date, league, season, home_team_id, away_team_id, kickoff_utc)
                   VALUES (?, ?, 'current', 1, 2, datetime(?))""",
                (kickoff[:10], league, kickoff))
    return cur.lastrowid


def test_chiusura_non_si_aggiorna_a_partita_iniziata():
    import fetch_odds as fo
    conn = fresh_db()
    now = datetime.now(timezone.utc)
    mid = _match(conn, (now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    conn.execute("""INSERT INTO virtual_bets (match_id, selection, bookmaker, odds, fair_prob, edge,
                    found_at, close_fair_prob, close_updated_at) VALUES (?, 'Home', 'X', 2.1, .5, .05, 'x', .5, 'x')""",
                 (mid,))
    fo.update_virtual_closing(conn.cursor(), mid, {"Home": 0.9}, now.isoformat())
    assert conn.execute("SELECT close_fair_prob FROM virtual_bets").fetchone()[0] == 0.5


def test_chiusura_si_aggiorna_prima_dell_inizio():
    import fetch_odds as fo
    conn = fresh_db()
    now = datetime.now(timezone.utc)
    mid = _match(conn, (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    conn.execute("""INSERT INTO virtual_bets (match_id, selection, bookmaker, odds, fair_prob, edge,
                    found_at, close_fair_prob, close_updated_at) VALUES (?, 'Home', 'X', 2.1, .5, .05, 'x', .5, 'x')""",
                 (mid,))
    fo.update_virtual_closing(conn.cursor(), mid, {"Home": 0.55}, now.isoformat())
    assert conn.execute("SELECT close_fair_prob FROM virtual_bets").fetchone()[0] == 0.55


def test_quote_uguali_non_duplicano_le_righe():
    import fetch_odds as fo
    conn = fresh_db()
    mid = _match(conn, "2099-01-01T15:00:00Z")
    cur = conn.cursor()
    fo.save_prices(cur, "odds_snapshots", mid, "Goldbet", {"Home": 2.0}, "2099-01-01T06:00:00")
    fo.save_prices(cur, "odds_snapshots", mid, "Goldbet", {"Home": 2.0}, "2099-01-01T07:00:00")
    fo.save_prices(cur, "odds_snapshots", mid, "Goldbet", {"Home": 2.1}, "2099-01-01T08:00:00")
    rows = conn.execute("SELECT odds, snapshot_time FROM odds_snapshots ORDER BY snapshot_time").fetchall()
    assert rows == [(2.0, "2099-01-01T07:00:00"), (2.1, "2099-01-01T08:00:00")]


def test_lettura_mercati_oddspapi():
    import fetch_odds as fo
    meta = {"101": ("Full Time Result", 0.0, {"101": "1", "102": "X", "103": "2"}),
            "1010": ("Over Under Full Time", 2.5, {"1010": "Over", "1011": "Under"})}
    book = {"markets": {
        "101": {"outcomes": {"101": {"players": {"0": {"price": 2.1}}},
                             "102": {"players": {"0": {"price": 3.3, "active": False}}}}},
        "1010": {"outcomes": {"1010": {"players": {"0": {"price": 1.9}}}}}}}
    prices, _ = fo.parse_markets(book, meta)
    assert prices == {"Home": 2.1, "Over2.5": 1.9}     # la quota non attiva è esclusa


# ---------------------------------------------------------------------------
# Chiusura Pinnacle: quando serve e limite mensile
# ---------------------------------------------------------------------------
def test_chiusura_serve_solo_per_partite_imminenti_con_scommesse():
    import check_closing as cc
    conn = fresh_db()
    now = datetime.now(timezone.utc)
    mid = _match(conn, (now + timedelta(minutes=25)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    assert cc.closing_targets(conn, now) == []            # nessuna scommessa: niente da fare
    conn.execute("""INSERT INTO virtual_bets (match_id, selection, bookmaker, odds, fair_prob, edge,
                    found_at) VALUES (?, 'Home', 'X', 2.1, .5, .05, 'x')""", (mid,))
    assert len(cc.closing_targets(conn, now)) == 1
    conn.execute("""INSERT INTO reference_odds (match_id, bookmaker, market, selection, odds, snapshot_time)
                    VALUES (?, 'Pinnacle', '1X2', 'Home', 2.0, ?)""", (mid, now.isoformat()))
    assert cc.closing_targets(conn, now) == []            # chiusura già presa


def test_limite_mensile_delle_chiusure():
    import check_closing as cc
    conn = fresh_db()
    now = datetime.now(timezone.utc)
    conn.executemany("INSERT INTO api_calls (provider, purpose, called_at) VALUES ('oddspapi', 'chiusura', ?)",
                     [(now.isoformat(),)] * cc.CLOSING_MONTHLY_CAP)
    assert cc.budget_status(conn, now)[0] is False


# ---------------------------------------------------------------------------
# Controllo di salute
# ---------------------------------------------------------------------------
def test_controllo_salute_errore_se_pinnacle_non_risponde():
    import health_check as hc
    conn = fresh_db()
    stats = {"partite": {"per_campionato": {}, "non_riconosciute": {}},
             "quote": {"bookmaker": {"Pinnacle": {"ok": False, "error": "503", "saved": 0},
                                     "Goldbet": {"ok": True, "saved": 10}},
                       "non_abbinate": []}}
    res = hc.run_checks(conn, stats, datetime.now(timezone.utc), {})
    assert any(l == "ERRORE" and "Pinnacle" in m for l, m in res)


# ---------------------------------------------------------------------------
# Marcatori: modello adottato e prova dal vivo
# ---------------------------------------------------------------------------
def test_parametri_marcatori_uguali_al_protocollo_congelato():
    import backtest_marcatori as bm
    import run_player_model as rpm
    import scorer_model as sm
    assert sm.PEN_CONVERSION == bm.PEN_CONVERSION
    assert sm.TAKER_PRIOR == bm.TAKER_PRIOR
    assert sm.CONDITIONAL_MIN_PLAY == bm.CONDITIONAL_MIN_PLAY
    assert rpm.PEN_XG == bm.PEN_XG
    assert (rpm.PRIOR_MINUTES, rpm.PRIOR_PER_90) == (bm.PRIOR_MINUTES, bm.PRIOR_PER_90)
    assert bm.CANDIDATE == "M2 npxG + rigori | se gioca"


def test_marcatori_rigorista_e_se_gioca():
    import scorer_model as sm
    base = {"npxg_per_90": 0.4, "expected_minutes": 90, "play_rate": 1.0, "penalties_scored": 0}
    players = [dict(base, player_id=1, penalties_scored=5), dict(base, player_id=2),
               dict(base, player_id=3, play_rate=0.5, expected_minutes=45, npxg_per_90=0.8)]
    res = {r["player_id"]: r for r in sm.distribute_team_goals(1.5, players, 0.15)}
    assert res[1]["expected_goals"] > res[2]["expected_goals"]        # il rigorista vale di più
    # il 3 gioca metà delle partite: "se gioca" raddoppia i suoi gol attesi
    lam3_unconditional = res[3]["expected_goals"] * 0.5
    assert abs(res[3]["prob_score_anytime"] - (1 - math.exp(-res[3]["expected_goals"]))) < 1e-3
    assert lam3_unconditional < res[3]["expected_goals"]


def test_prova_marcatori_rimborso_se_non_gioca():
    import record_scorer_bets as rs
    conn = fresh_db()
    mid = _match(conn, "2020-01-01T15:00:00Z")
    conn.execute("INSERT INTO players (bsd_id, name) VALUES (10, 'A'), (11, 'B')")
    conn.execute("""INSERT INTO bsd_events (id, league, event_date, home_bsd_team_id, away_bsd_team_id,
                    match_id, stats_done) VALUES (5, 'Serie A', '2020-01-01', 1, 2, ?, 1)""", (mid,))
    conn.execute("""INSERT INTO player_match_stats (player_id, match_bsd_id, match_date, minutes, goals)
                    VALUES (1, 5, '2020-01-01', 80, 1)""")
    for pid in (1, 2):
        conn.execute("""INSERT INTO virtual_scorer_bets (match_id, player_id, bookmaker, odds, model_prob,
                        edge, found_at) VALUES (?, ?, 'Eurobet', 3.0, .4, .2, 'x')""", (mid, pid))
    rs.settle(conn)
    res = dict(conn.execute("SELECT player_id, result FROM virtual_scorer_bets"))
    assert res == {1: "segna", 2: "rimborsata"}


# ---------------------------------------------------------------------------
# Database: si crea da zero e le migrazioni si possono ripetere
# ---------------------------------------------------------------------------
def test_database_da_zero_e_migrazioni_ripetibili():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    init_db(conn)      # seconda volta: nessun errore, niente di duplicato
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("matches", "odds_snapshots", "reference_odds", "virtual_bets", "external_ids",
              "api_calls", "health_checks", "virtual_scorer_bets", "player_match_stats"):
        assert t in tables
    cols = {r[1] for r in conn.execute("PRAGMA table_info(matches)")}
    assert "kickoff_utc" in cols


# ---------------------------------------------------------------------------
# Dashboard: si apre senza errori sul database vero
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not os.path.exists("data.db"), reason="data.db non presente")
def test_dashboard_si_apre_senza_errori():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file("dashboard.py", default_timeout=240).run()
    assert not at.exception, [e.value for e in at.exception]


# ---------------------------------------------------------------------------
# Librerie: versioni fissate (un aggiornamento non deve arrivare da solo)
# ---------------------------------------------------------------------------
def test_versioni_delle_librerie_fissate():
    righe = [l.split("#")[0].strip() for l in open("requirements.txt")]
    righe = [l for l in righe if l]
    assert righe and all("==" in l for l in righe), righe
