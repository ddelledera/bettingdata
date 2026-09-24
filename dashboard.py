"""
La pagina web dell'app. Mostra le partite in arrivo, confronta le nostre
probabilità con le migliori quote disponibili tra i bookmaker, e ordina
tutto per convenienza (valore). Organizzata in schede per restare leggibile.

Per vederla in azione (una volta pubblicata): si apre da sola nel browser.
Non c'è nulla da capire o configurare qui dentro.
"""

import sqlite3
import pandas as pd
import streamlit as st
from value_calculator import (remove_bookmaker_margin, find_value_bets, combine_parlay, find_best_combination,
                              find_tradeoff_frontier, pick_representatives)
from markets import (model_probabilities, long_label, market_of, MARKET_NAMES, scorer_key,
                     fair_probabilities, is_winner)
from value_calculator import expected_value, kelly_fraction
from github_storage import read_json_file, write_json_file
import json
import math
import uuid
from datetime import datetime, timezone

DB_PATH = "data.db"

# Repository dove sono salvate le schedine confermate (lo stesso di questo
# progetto) — se un giorno cambi nome al repository o account, va aggiornato qui.
GITHUB_OWNER = "ddelledera"
GITHUB_REPO = "bettingdata"
SLIPS_PATH = "tracked_slips.json"


def get_github_token():
    """Legge il token GitHub dai 'secrets' della pagina web, se configurato."""
    try:
        return st.secrets["GITHUB_TOKEN"]
    except Exception:
        return None

st.set_page_config(page_title="Previsioni Calcio", page_icon="⚽", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Oswald:wght@500;600;700&display=swap');

/* Colori dell'app: verde scuro, crema, oro (come prima) */
:root {
    --verde: #1B4332; --verde-scuro: #163B2B; --verde-medio: #2D6A4F;
    --menta: #8FBFA3; --oro: #D4A017; --crema: #F7F9F6;
    --inchiostro: #1A2420; --grigio: #55635C; --linea: #D5E0DA;
}

/* Contenuto centrato, non più largo di ~1350 px: righe leggibili e
   controlli di dimensione normale anche su monitor grandi */
.block-container, [data-testid="stMainBlockContainer"] {
    max-width: 1360px !important;
    margin: 0 auto;
    padding-top: 3.2rem !important;
}

h1, h2, h3, .leg-teams, .leg-odds-value {
    font-family: 'Oswald', sans-serif !important;
    letter-spacing: 0.2px;
}
h1 { color: var(--verde); font-weight: 700; }
h3 { color: var(--inchiostro); }

/* Testi secondari: più grandi e più scuri di prima */
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {
    font-size: 0.9rem !important;
    color: var(--grigio) !important;
    line-height: 1.5;
}

/* Intestazione */
.app-header {
    background: linear-gradient(135deg, var(--verde) 0%, var(--verde-scuro) 100%);
    border-radius: 12px;
    padding: 1.4rem 1.8rem 1.2rem 1.8rem;
    margin-bottom: 1.2rem;
}
.app-header h1 {
    color: var(--crema) !important;
    margin: 0 0 0.15rem 0 !important;
    padding: 0 !important;
    font-size: 1.9rem !important;
}
.app-header p { color: var(--menta); font-size: 0.98rem; margin: 0; }

/* Navigazione (vale sia per le versioni vecchie sia per quelle nuove di Streamlit) */
[data-testid="stTabs"] [data-baseweb="tab-list"], [data-testid="stTabs"] [role="tablist"] {
    gap: 4px;
    border-bottom: 2px solid #E2E8E5;
}
[data-testid="stTabs"] button[data-baseweb="tab"], [data-testid="stTabs"] [data-testid="stTab"] {
    padding: 10px 18px !important;
    color: #7A8A81;
}
[data-testid="stTabs"] [data-baseweb="tab"] p, [data-testid="stTabs"] [data-testid="stTab"] p {
    font-family: 'Oswald', sans-serif !important;
    font-weight: 600;
    font-size: 1.02rem !important;
}
[data-testid="stTabs"] [aria-selected="true"], [data-testid="stTabs"] [aria-selected="true"] p {
    color: var(--verde) !important;
}

/* Pulsanti */
[data-testid="stButton"] button, [data-testid="stPopover"] button { border-radius: 6px; font-weight: 600; }
[data-testid="stButton"] button[kind="primary"] { background-color: var(--verde); border-color: var(--verde); }
[data-testid="stButton"] button[kind="primary"]:hover { background-color: #0F2B1E; border-color: #0F2B1E; }

/* Pulsanti radio come "pillole" */
[data-testid="stRadio"] > div[role="radiogroup"] { gap: 8px; }
[data-testid="stRadio"] div[role="radiogroup"] label {
    background: #EEF3F0; padding: 5px 14px; border-radius: 20px; border: 1px solid var(--linea);
}
[data-testid="stRadio"] div[role="radiogroup"] label:has(input:checked) { background: var(--verde); border-color: var(--verde); }
[data-testid="stRadio"] div[role="radiogroup"] label:has(input:checked) p { color: var(--crema) !important; }
[data-testid="stRadio"] div[role="radiogroup"] label > div:first-child { display: none; }

/* Passaggi numerati della schedina (è davvero una sequenza) */
.step-title {
    display: flex; align-items: center; gap: 10px;
    font-family: 'Oswald', sans-serif; font-size: 1.15rem; font-weight: 600;
    color: var(--inchiostro); margin: 0.6rem 0 0.4rem 0;
}
.step-num {
    display: inline-flex; align-items: center; justify-content: center;
    width: 26px; height: 26px; border-radius: 50%;
    background: var(--verde); color: var(--crema); font-size: 0.9rem;
}

/* Riquadri delle tre modalità della schedina */
.mode-card {
    border: 1px solid var(--linea); border-radius: 10px; background: #FFFFFF;
    padding: 14px 16px 10px 16px; min-height: 104px; margin-bottom: 6px;
}
.mode-card.on { border: 2px solid var(--verde); background: #EEF5F0; }
.mode-card .t { font-family: 'Oswald', sans-serif; font-size: 1.08rem; font-weight: 600; color: var(--inchiostro); }
.mode-card .d { font-size: 0.88rem; color: var(--grigio); margin-top: 3px; }

/* Riga "tabellone" per ogni partita di una schedina */
.leg-row {
    display: flex; justify-content: space-between; align-items: center;
    background: #FFFFFF; border: 1px solid var(--linea);
    border-left: 5px solid var(--risk-color, var(--oro));
    border-radius: 8px; padding: 12px 18px; margin-bottom: 8px;
}
.leg-basso { --risk-color: #4CAF7D; }
.leg-medio { --risk-color: #D4A017; }
.leg-alto  { --risk-color: #D96C6C; }
.leg-match { flex: 1; }
.leg-teams { font-size: 1.05rem; font-weight: 600; color: var(--inchiostro); }
.leg-date { font-size: 0.82rem; color: var(--grigio); margin-top: 1px; }
.leg-pick {
    background: #EEF3F0; color: var(--verde); font-weight: 600; font-size: 0.88rem;
    padding: 4px 12px; border-radius: 20px; margin: 0 16px; white-space: nowrap;
}
.leg-odds { text-align: right; min-width: 90px; }
.leg-odds-value { font-size: 1.35rem; font-weight: 700; color: var(--verde); }
.leg-odds-prob { font-size: 0.8rem; color: var(--grigio); }

/* Schede delle opportunità e dei marcatori.
   Chiare di default; le prime tre in verde pieno. */
.spot-card {
    border-radius: 10px; padding: 16px 18px 14px 18px; height: 100%;
    background: #FFFFFF; color: var(--inchiostro);
    border: 1px solid #CFDDD5; border-left: 4px solid var(--verde-medio);
}
.spot-card.top {
    background: linear-gradient(155deg, var(--verde) 0%, var(--verde-scuro) 100%);
    color: var(--crema); border: none;
}
.spot-league { font-size: 0.8rem; color: var(--grigio); }
.spot-card.top .spot-league { color: var(--menta); }
.spot-teams {
    font-family: 'Oswald', sans-serif !important; font-size: 1.2rem; font-weight: 600;
    margin: 2px 0 2px 0; line-height: 1.25;
}
.spot-pick { font-size: 0.95rem; font-weight: 600; color: var(--verde-medio); margin-bottom: 10px; }
.spot-card.top .spot-pick { color: #CFE3D8; }
.spot-price { display: flex; justify-content: space-between; align-items: flex-end; gap: 12px; }
.spot-odds { font-family: 'Oswald', sans-serif; font-size: 2.1rem; font-weight: 700; line-height: 1; color: var(--verde); }
.spot-card.top .spot-odds { color: var(--crema); }
.spot-book { font-size: 0.95rem; font-weight: 600; margin-left: 6px; }
.spot-fair { text-align: right; font-size: 0.85rem; color: var(--grigio); line-height: 1.35; }
.spot-card.top .spot-fair { color: var(--menta); }
.spot-fair b { color: var(--inchiostro); }
.spot-card.top .spot-fair b { color: var(--crema); }
.spot-edge {
    display: inline-block; font-weight: 700; font-size: 0.9rem; padding: 1px 8px;
    border-radius: 12px; background: #F6ECCB; color: #7A5C00;
}
.spot-card.top .spot-edge { background: var(--oro); color: var(--verde-scuro); }
.spot-meta {
    display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 0.8rem; color: var(--grigio);
    border-top: 1px solid #E4ECE7; padding-top: 8px; margin-top: 12px;
}
.spot-card.top .spot-meta { color: var(--menta); border-top-color: rgba(247, 249, 246, 0.15); }
.spot-meta b { color: var(--inchiostro); font-weight: 600; }
.spot-meta .faint { opacity: 0.6; font-size: 0.76rem; }
.spot-q { font-size: 0.85rem; color: var(--grigio); margin-right: 2px; }
.spot-card.top .spot-q { color: var(--menta); }
.spot-card.top .spot-meta b { color: var(--crema); }
.spot-warn { font-size: 0.8rem; color: #A63A3A; margin-top: 6px; }
.spot-card.top .spot-warn { color: #F2B8B8; }
/* numero protagonista dei marcatori: la probabilità di segnare */
.spot-big { font-family: 'Oswald', sans-serif; font-size: 2.1rem; font-weight: 700; line-height: 1; color: var(--verde); }
.spot-big-label { font-size: 0.82rem; color: var(--grigio); margin-top: 2px; }

/* Storico schedine */
.slip-card {
    border-radius: 8px; padding: 14px 18px; margin-bottom: 10px;
    border-left: 5px solid var(--slip-color, #7A8A81); background: #FFFFFF;
    box-shadow: 0 1px 2px rgba(27, 67, 50, 0.08);
}
.slip-pending { --slip-color: #D4A017; }
.slip-won { --slip-color: #2D6A4F; }
.slip-lost { --slip-color: #A63A3A; }
.slip-header {
    display: flex; justify-content: space-between; align-items: baseline;
    font-family: 'Oswald', sans-serif; font-size: 1.05rem; font-weight: 600; color: var(--inchiostro);
}
.slip-profit-won { color: #2D6A4F; font-weight: 700; }
.slip-profit-lost { color: #A63A3A; font-weight: 700; }
.slip-meta { font-size: 0.85rem; color: var(--grigio); margin-top: 4px; }

/* Panoramica della Performance: un blocco per la prova dal vivo e uno per il backtest */
.verdict {
    background: #FFFFFF; border: 1px solid var(--linea); border-radius: 12px;
    padding: 18px 20px; min-height: 170px; margin-bottom: 14px;
}
.verdict .k { font-size: 0.85rem; color: var(--grigio); }
.verdict .big { font-family: 'Oswald', sans-serif; font-size: 2.6rem; font-weight: 700; color: var(--verde); line-height: 1.05; }
.verdict .sub { font-size: 0.92rem; color: var(--inchiostro); margin-top: 4px; }
.verdict .ci { font-size: 0.85rem; color: var(--grigio); margin-top: 2px; }
.light-badge {
    display: inline-block; margin-top: 10px; padding: 3px 10px; border-radius: 12px;
    font-size: 0.85rem; font-weight: 600;
}
.light-rosso { background: #F6DADA; color: #8A2F2F; }
.light-giallo { background: #F6ECCB; color: #7A5C00; }
.light-verde { background: #D8EDE1; color: #1F5A3E; }
.mini-list { font-size: 0.92rem; }
.mini-list div { display: flex; justify-content: space-between; padding: 5px 0; border-bottom: 1px solid #E9EFEB; }
.mini-list div:last-child { border-bottom: none; }
.mini-list span.n { color: var(--grigio); font-size: 0.85rem; }
</style>
""", unsafe_allow_html=True)


def get_connection():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


# Una quota più vecchia di FRESH_HOURS rispetto all'ultimo aggiornamento della
# stessa partita non viene usata: vuol dire che il bookmaker non l'ha più
# riproposta (esito tolto, o fonte che non ha risposto) e potrebbe non
# essere più giocabile. Ogni giro "rinfresca" l'orario delle quote ancora
# valide, anche se non sono cambiate.
FRESH_HOURS = 3


def has_kickoff(conn):
    """True se il database ha già la colonna dell'orario di inizio (la crea il
    primo aggiornamento automatico dopo questa versione)."""
    return "kickoff_utc" in [r[1] for r in conn.execute("PRAGMA table_info(matches)")]


def not_started_sql(conn, alias="m"):
    """Condizione SQL: partita non ancora iniziata (per orario se lo
    conosciamo, altrimenti per data come prima)."""
    if has_kickoff(conn):
        return (f"({alias}.kickoff_utc > datetime('now') OR "
                f"({alias}.kickoff_utc IS NULL AND {alias}.date >= date('now')))")
    return f"{alias}.date >= date('now')"


GIORNI = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]
MESI = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"]


def fmt_when(date_str, kickoff_utc=None):
    """'sab 11 ott, 16:00' (ora italiana) se conosciamo l'orario, altrimenti 'sab 11 ott'."""
    from zoneinfo import ZoneInfo
    try:
        if kickoff_utc and kickoff_utc == kickoff_utc:
            t = (datetime.fromisoformat(str(kickoff_utc)).replace(tzinfo=timezone.utc)
                 .astimezone(ZoneInfo("Europe/Rome")))
            return f"{GIORNI[t.weekday()]} {t.day} {MESI[t.month - 1]}, {t:%H:%M}"
        d = datetime.fromisoformat(str(date_str)[:10])
        return f"{GIORNI[d.weekday()]} {d.day} {MESI[d.month - 1]}"
    except (ValueError, TypeError):
        return str(date_str)


def load_upcoming_with_predictions(conn):
    ko = "m.kickoff_utc" if has_kickoff(conn) else "NULL"
    query = f"""
        SELECT m.id, m.date, m.league, {ko} AS kickoff_utc, h.name AS home, a.name AS away,
               p.prob_home, p.prob_draw, p.prob_away,
               p.prob_btts, p.prob_over15, p.prob_over25, p.prob_over35
        FROM matches m
        JOIN teams h ON m.home_team_id = h.id
        JOIN teams a ON m.away_team_id = a.id
        JOIN model_predictions p ON p.match_id = m.id
        WHERE {not_started_sql(conn)} AND m.home_goals IS NULL
        ORDER BY m.date
    """
    return pd.read_sql_query(query, conn)


def best_odds_for_match(conn, match_id):
    """Per ogni esito di ogni mercato (1X2, doppia chance, Goal/No Goal,
    Under/Over), trova la quota più alta tra i bookmaker, usando SOLO
    l'ultima quota registrata per ciascun bookmaker (non il massimo tra
    tutti gli snapshot storici, che potrebbe non essere più disponibile).

    Per ogni esito si usano PRIMA i soli bookmaker italiani (ADM): sono le
    quote che puoi davvero giocare. Solo se nessun italiano quota
    quell'esito si ripiega sugli altri (es. alcune partite di coppa)."""
    query = """
        WITH ultima_quota AS (
            SELECT bookmaker, selection, odds, snapshot_time,
                   ROW_NUMBER() OVER (
                       PARTITION BY bookmaker, selection
                       ORDER BY snapshot_time DESC
                   ) AS rn
            FROM odds_snapshots
            WHERE match_id = ?
        )
        SELECT selection, odds, bookmaker, snapshot_time FROM ultima_quota
        WHERE rn = 1 AND datetime(snapshot_time) >= datetime(
            (SELECT MAX(snapshot_time) FROM odds_snapshots WHERE match_id = ?),
            '-{FRESH_HOURS} hours')
    """
    rows = conn.execute(query.replace("{FRESH_HOURS}", str(FRESH_HOURS)),
                        (match_id, match_id)).fetchall()
    # Quote "anomale": se almeno 3 bookmaker quotano un esito e uno è molto
    # sopra gli altri (oltre il 30% sopra la mediana), è quasi sempre un
    # errore della fonte o una quota vecchia non più disponibile, non un
    # regalo del bookmaker. La scartiamo per non mostrare falsi +300% di EV.
    by_sel = {}
    for selection, odds, bookmaker, snap in rows:
        by_sel.setdefault(selection, []).append(odds)
    medians = {sel: sorted(v)[len(v) // 2] for sel, v in by_sel.items() if len(v) >= 3}
    rows = [r for r in rows if r[0] not in medians or r[1] <= medians[r[0]] * 1.3]

    best, best_it = {}, {}
    for selection, odds, bookmaker, snap in rows:
        if selection not in best or odds > best[selection][0]:
            best[selection] = (odds, bookmaker, snap)
        if bookmaker in BOOKMAKER_ITALIA and (selection not in best_it
                                             or odds > best_it[selection][0]):
            best_it[selection] = (odds, bookmaker, snap)
    chosen = {sel: best_it.get(sel, val) for sel, val in best.items()}
    if not chosen:
        return None
    return ({sel: v[0] for sel, v in chosen.items()},
            {sel: v[1] for sel, v in chosen.items()},
            {sel: v[2] for sel, v in chosen.items()})


def odds_age_label(snapshot_time):
    """'aggiornata 3 h fa': le quote si scaricano una volta al giorno (alle
    6:00 UTC) per restare nel limite gratuito dell'API, quindi nel corso
    della giornata il bookmaker può averle cambiate."""
    try:
        t = datetime.fromisoformat(str(snapshot_time))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        minutes = (datetime.now(timezone.utc) - t).total_seconds() / 60
    except ValueError:
        return "aggiornamento sconosciuto"
    if minutes < 60:
        return f"aggiornata {max(int(minutes), 1)} min fa"
    if minutes < 48 * 60:
        return f"aggiornata {int(minutes // 60)} h fa"
    return f"aggiornata {int(minutes // 1440)} giorni fa"


def list_bookmakers(conn):
    """Tutti i bookmaker di cui abbiamo quote per le partite in arrivo,
    con quelli italiani (ADM) messi per primi — servono per la schedina,
    che va giocata tutta presso lo stesso bookmaker."""
    query = """
        SELECT DISTINCT o.bookmaker
        FROM odds_snapshots o
        JOIN matches m ON o.match_id = m.id
        WHERE m.date >= date('now')
        ORDER BY o.bookmaker
    """
    all_bookmakers = [r[0] for r in conn.execute(query).fetchall()]
    italiani = sorted(b for b in all_bookmakers if b in BOOKMAKER_ITALIA)
    altri = sorted(b for b in all_bookmakers if b not in BOOKMAKER_ITALIA)
    return italiani + altri


def bookmaker_display_label(bookmaker):
    """Etichetta mostrata nei menu: segnala con una bandierina i bookmaker
    che sappiamo avere licenza ADM (autorizzati in Italia). Gli altri sono
    bookmaker reali ma non necessariamente utilizzabili legalmente
    dall'Italia — mostrati comunque, per confronto e per non bloccare le
    simulazioni quando i pochi bookmaker italiani coperti dalla nostra
    fonte non hanno quote per una partita."""
    if bookmaker in BOOKMAKER_ITALIA:
        stesse_quote = BOOKMAKER_CLONI.get(bookmaker)
        if stesse_quote:
            return f"🇮🇹 {bookmaker} (ADM) — stessa piattaforma di: {stesse_quote}"
        return f"🇮🇹 {bookmaker} (ADM)"
    return bookmaker


def odds_by_bookmaker_for_match(conn, match_id, bookmaker):
    """Le quote di UN SOLO bookmaker per una partita (la più recente per
    ciascun esito, se ne abbiamo registrate più di una nel tempo)."""
    query = f"""
        SELECT selection, odds FROM odds_snapshots
        WHERE match_id = ? AND bookmaker = ?
          AND datetime(snapshot_time) >= datetime(
              (SELECT MAX(snapshot_time) FROM odds_snapshots WHERE match_id = ?),
              '-{FRESH_HOURS} hours')
        ORDER BY snapshot_time DESC
    """
    # le quote non più riproposte dal bookmaker (vedi FRESH_HOURS) restano
    # fuori; tra le altre si tiene la più recente per ogni esito
    rows = conn.execute(query, (match_id, bookmaker, match_id)).fetchall()
    odds = {}
    for selection, o in rows:
        if selection not in odds:
            odds[selection] = o
    return odds or None


def risk_label(odds):
    """Etichetta semplice del livello di rischio in base alla quota."""
    if odds <= 1.8:
        return "🟢 Quota bassa"
    elif odds <= 3.0:
        return "🟡 Quota media"
    return "🔴 Quota alta"


# Bookmaker con licenza ADM (autorizzati in Italia) di cui abbiamo le quote.
# Goldbet, Eurobet, bet365 e Sisal arrivano da OddsPapi (fetch_odds.py),
# fonte che distingue esplicitamente le versioni italiane (.it) dei siti.
# Unibet e Codere arrivano ancora da The Odds API (coppe europee); per
# Unibet la fonte non garantisce che sia l'entità italiana.
BOOKMAKER_ITALIA = {"Goldbet", "Eurobet", "bet365", "Sisal", "Unibet", "Codere"}

# Marchi che OddsPapi indica come "clone" (stessa piattaforma): di solito le
# quote coincidono, ma non l'abbiamo verificato su tutti i mercati — prima di
# giocare conviene controllare la quota sul sito del marchio che usi.
BOOKMAKER_CLONI = {
    "Goldbet": "Lottomatica, Planetwin365, BetFlag",
    "Sisal": "Snai, PokerStars",
}


def load_upcoming_player_predictions(conn):
    """Previsioni marcatori per le partite in arrivo, con nome squadra e avversario."""
    ko = "m.kickoff_utc" if has_kickoff(conn) else "NULL"
    query = f"""
        SELECT pl.id AS player_id, pl.name AS player, t.name AS team, m.id AS match_id,
               m.date, m.league, {ko} AS kickoff_utc,
               ht.name AS home_team, at.name AS away_team,
               pp.expected_goals, pp.prob_score_anytime,
               pp.expected_minutes, pp.starting_probability
        FROM player_predictions pp
        JOIN players pl ON pl.id = pp.player_id
        JOIN teams t ON t.id = pl.team_id
        JOIN matches m ON m.id = pp.match_id
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at ON at.id = m.away_team_id
        WHERE {not_started_sql(conn)} AND m.home_goals IS NULL
        ORDER BY pp.prob_score_anytime DESC
    """
    return pd.read_sql_query(query, conn)


def load_scorer_odds(conn):
    """Ultima quota 'marcatore' di ogni bookmaker per ogni giocatore
    riconosciuto, per le partite in arrivo."""
    query = f"""
        WITH ultima AS (
            SELECT s.match_id, s.player_id, s.bookmaker, s.odds, s.snapshot_time,
                   ROW_NUMBER() OVER (PARTITION BY s.match_id, s.player_id, s.bookmaker
                                      ORDER BY s.snapshot_time DESC) AS rn
            FROM scorer_odds s JOIN matches m ON m.id = s.match_id
            WHERE s.player_id IS NOT NULL AND {not_started_sql(conn)} AND m.home_goals IS NULL
        ),
        ultimo_giro AS (SELECT match_id, MAX(snapshot_time) AS t FROM ultima GROUP BY match_id)
        SELECT u.match_id, u.player_id, u.bookmaker, u.odds, u.snapshot_time
        FROM ultima u JOIN ultimo_giro g ON g.match_id = u.match_id
        WHERE u.rn = 1 AND datetime(u.snapshot_time) >= datetime(g.t, '-{FRESH_HOURS} hours')
    """
    try:
        return pd.read_sql_query(query, conn)
    except Exception:
        return pd.DataFrame(columns=["match_id", "player_id", "bookmaker", "odds", "snapshot_time"])


def best_scorer_odds(scorer_odds_df, bookmakers):
    """Per ogni (partita, giocatore) la quota marcatore migliore tra i
    bookmaker scelti, con il nome del bookmaker."""
    df = scorer_odds_df[scorer_odds_df["bookmaker"].isin(bookmakers)]
    if df.empty:
        return df.assign(best_odds=[], best_bookmaker=[], best_time=[])[
            ["match_id", "player_id", "best_odds", "best_bookmaker", "best_time"]]
    idx = df.groupby(["match_id", "player_id"])["odds"].idxmax()
    return (df.loc[idx, ["match_id", "player_id", "odds", "bookmaker", "snapshot_time"]]
              .rename(columns={"odds": "best_odds", "bookmaker": "best_bookmaker",
                               "snapshot_time": "best_time"}))


def render_html(html):
    """Mostra un blocco HTML. Toglie rientri e righe vuote prima di passarlo a
    st.markdown: per il Markdown una riga vuota seguita da righe rientrate di 4+
    spazi è un blocco di codice, e l'HTML comparirebbe come testo (succedeva
    quando un pezzo opzionale, come l'avviso, era vuoto)."""
    st.markdown("\n".join(line.strip() for line in html.splitlines() if line.strip()),
                unsafe_allow_html=True)


def render_scorer_card(row):
    """Scheda della probabilità di un giocatore di segnare. Numero
    protagonista: la probabilità; quota e valore, se ci sono, nei dettagli."""
    avversario = row["away_team"] if row["team"] == row["home_team"] else row["home_team"]
    minuti = f"{row['expected_minutes']:.0f}'" if pd.notna(row.get("expected_minutes")) else "—"
    titolare = f"{row['starting_probability']:.0%}" if pd.notna(row.get("starting_probability")) else "—"
    quota_html = ""
    if pd.notna(row.get("best_odds")):
        ev = expected_value(row["prob_score_anytime"], row["best_odds"])
        colore = "#2D6A4F" if ev > 0 else "#A63A3A"
        quota_html = (f'<span>Quota <b>{row["best_odds"]:g}</b> {row["best_bookmaker"]}</span>'
                      f'<span style="color:{colore}; font-weight:600;">EV {ev:+.1%}</span>'
                      f'<span>{odds_age_label(row["best_time"])}</span>')
    render_html(f"""
    <div class="spot-card">
        <div class="spot-league">{row['league']}, {fmt_when(row['date'], row.get('kickoff_utc'))}</div>
        <div class="spot-teams">{row['player']}</div>
        <div class="spot-pick">{row['team']} contro {avversario}</div>
        <div class="spot-big">{row['prob_score_anytime']:.0%}</div>
        <div class="spot-big-label">probabilità di segnare, se gioca (nostro modello)</div>
        <div class="spot-meta">
            <span>Gol attesi <b>{row['expected_goals']:.2f}</b></span>
            <span>Minuti attesi <b>{minuti}</b></span>
            <span>Titolare <b>{titolare}</b></span>
            {quota_html}
        </div>
    </div>
    """)


def _summary(selected, options, all_label, one_word):
    """Riassunto breve di una scelta multipla, per l'etichetta del pulsante."""
    if not selected:
        return "nessuna"
    if len(selected) == len(options):
        return f"{all_label} ({len(options)})"
    if len(selected) <= 2:
        return ", ".join(selected)
    return f"{len(selected)} {one_word}"


def competition_filter(matches_df, key):
    """Selettore delle competizioni, compatto: un pulsante col riassunto
    ("Competizioni: tutte (7)") che apre la lista. Ritorna solo le partite
    delle competizioni scelte. Usata in ogni scheda."""
    available = sorted(matches_df["league"].unique())
    current = [c for c in st.session_state.get(key, available) if c in available]
    with st.popover(f"🏆 Competizioni: {_summary(current, available, 'tutte', 'competizioni')}"):
        selected = st.multiselect("Competizioni", options=available, default=available, key=key)
    if not selected:
        return matches_df.iloc[0:0]
    return matches_df[matches_df["league"].isin(selected)]


def market_filter(options, key, default=None, help=None):
    """Come competition_filter, per i mercati."""
    default = options if default is None else default
    current = [m for m in st.session_state.get(key, default) if m in options]
    with st.popover(f"📋 Mercati: {_summary(current, options, 'tutti', 'mercati')}"):
        return st.multiselect("Mercati", options, default=default, key=key, help=help)


def risk_css_class(odds):
    """Classe CSS corrispondente al livello di rischio, per colorare il bordo della riga."""
    if odds <= 1.8:
        return "leg-basso"
    elif odds <= 3.0:
        return "leg-medio"
    return "leg-alto"


def render_leg_row(leg):
    """Disegna una partita della schedina come riga del 'tabellone', con squadre,
    esito scelto, quota e probabilità del modello ben distinti visivamente.
    Il livello di rischio è indicato sia dal colore del bordo sia da
    un'etichetta di testo, per chi non riesce a distinguere bene i colori."""
    esito_label = leg.get("label") or long_label(leg["selection"])
    render_html(f"""
    <div class="leg-row {risk_css_class(leg['odds'])}">
        <div class="leg-match">
            <div class="leg-teams">{leg['match_label']}</div>
            <div class="leg-date">{leg['match_date']}</div>
        </div>
        <div class="leg-pick">{esito_label}</div>
        <div class="leg-odds">
            <div class="leg-odds-value">{leg['odds']}</div>
            <div class="leg-odds-prob">prob. giusta {leg['model_probability']:.0%}</div>
            <div style="font-size:0.72rem; margin-top:2px;">{risk_badge_html(leg['odds'])}</div>
        </div>
    </div>
    """)


RISK_COLORS = {"leg-basso": "#5EB78A", "leg-medio": "#D4A017", "leg-alto": "#E28F8F"}
# Nota: è una FASCIA DI QUOTA (≤1.80 / ≤3.00 / oltre), non una misura di
# rischio vera: il modello non stima ancora quanto è affidabile ogni previsione.
RISK_TEXT = {"leg-basso": "quota bassa", "leg-medio": "quota media", "leg-alto": "quota alta"}


def risk_badge_html(odds):
    """Piccola etichetta colorata per il livello di rischio, da inserire in una scheda."""
    cls = risk_css_class(odds)
    return f'<span style="color:{RISK_COLORS[cls]}; font-weight:600;">● {RISK_TEXT[cls]}</span>'


EV_SOSPETTO = 0.25  # oltre +25% è molto più probabile un errore del modello che un regalo


def render_spotlight_card(opp, top=False):
    """Scheda di un'opportunità. Il dato principale è la quota e il bookmaker,
    confrontati col prezzo giusto di Pinnacle: "bet365 paga 1.33, il prezzo
    giusto è 1.32" si capisce subito. Il vantaggio (EV) è un'etichetta più
    piccola, perché di solito è un numero piccolo; le probabilità e il resto
    stanno nella riga dei dettagli. top=True: una delle prime tre (verde pieno)."""
    p, odds, ev = opp["_p"], opp["Quota migliore"], opp["_ev"]
    fair_odds = 1 / p if p else float("nan")
    pm = opp.get("Prob. modello")
    avviso = ""
    if ev > EV_SOSPETTO:
        avviso = ('<div class="spot-warn">⚠️ Scarto molto grande dal mercato: più probabile '
                  'una quota non aggiornata che un vero affare. Verifica prima di giocare.</div>')
    modello = (f'<span class="faint">nostro modello {pm:.0f}%</span>' if pm is not None and pm == pm else "")
    render_html(f"""
    <div class="spot-card{' top' if top else ''}">
        <div class="spot-league">{opp['Campionato']}, {opp['Data']}</div>
        <div class="spot-teams">{opp['Partita']}</div>
        <div class="spot-pick">{opp['Esito']}</div>
        <div class="spot-price">
            <div><span class="spot-q">Quota</span> <span class="spot-odds">{odds:g}</span><span class="spot-book">{opp['Bookmaker']}</span></div>
            <div class="spot-fair">prezzo giusto <b>{fair_odds:.2f}</b><br>
                <span class="spot-edge">{ev:+.1%}</span></div>
        </div>
        {avviso}
        <div class="spot-meta">
            <span>Pinnacle <b>{p:.0%}</b></span>
            <span>{opp.get('Aggiornata', '')}</span>
            {modello}
            <span class="faint">Kelly teorico {opp['Puntata consigliata']}</span>
        </div>
    </div>
    """)


def render_slip_card(slip):
    """Disegna una scheda per una voce dello storico: in attesa, vinta o persa."""
    legs_desc = ", ".join(l["match_label"] for l in slip["legs"])
    if slip["status"] == "pending":
        render_html(f"""
        <div class="slip-card slip-pending">
            <div class="slip-header"><span>🕒 {slip['stake']}€ @ {slip['combined_odds']} · {slip['bookmaker']}</span></div>
            <div class="slip-meta">{legs_desc}</div>
        </div>
        """)
    else:
        esito = slip["result_summary"]
        won = slip["status"] == "won"
        icona = "✅" if won else "❌"
        profit_class = "slip-profit-won" if won else "slip-profit-lost"
        render_html(f"""
        <div class="slip-card {'slip-won' if won else 'slip-lost'}">
            <div class="slip-header">
                <span>{icona} {slip['stake']}€ @ {slip['combined_odds']} · {slip['bookmaker']}</span>
                <span class="{profit_class}">€{esito['profit']:+.2f}</span>
            </div>
            <div class="slip-meta">{legs_desc}</div>
        </div>
        """)
        with st.expander("Dettaglio"):
            for leg in esito["legs"]:
                check = "✔️" if leg["won"] else "✖️"
                st.write(f"{check} {leg['match_label']} — puntato: {leg['selection']}, "
                         f"risultato vero: {leg['actual_result']}")


def scorer_legs_for_match(conn_or_df, match_id, predictions_df, bookmakers):
    """Selezioni 'marcatore' di una partita (quota migliore tra i bookmaker
    indicati), nello stesso formato delle altre selezioni."""
    odds_df = best_scorer_odds(conn_or_df[conn_or_df["match_id"] == match_id], bookmakers)
    if odds_df.empty:
        return []
    merged = odds_df.merge(predictions_df[predictions_df["match_id"] == match_id],
                           on=["match_id", "player_id"])
    legs = []
    for _, r in merged.iterrows():
        prob, odds = r["prob_score_anytime"], r["best_odds"]
        key = scorer_key(int(r["player_id"]))
        legs.append({"selection": key, "label": long_label(key, r["player"]),
                     "model_probability": round(prob, 3), "odds": odds,
                     "ev": round(expected_value(prob, odds), 3),
                     "kelly_stake_pct": round(kelly_fraction(prob, odds) * 100, 2),
                     "bookmaker": r["best_bookmaker"],
                     "aggiornata": odds_age_label(r["best_time"])})
    return legs


MAX_ODDS = 5.0   # oltre, nel backtest, perdite pesanti anche con "valore" apparente


def fair_for_match(conn, match_id):
    """Probabilità giuste della partita dall'ultima quota di Pinnacle
    (margine tolto col metodo potenza). Vuoto se Pinnacle non la quota."""
    # Pinnacle vale come riferimento solo se non è più vecchia delle quote
    # italiane della stessa partita (vedi FRESH_HOURS): se non la quota più,
    # non sappiamo qual è il prezzo giusto.
    rows = conn.execute(f"""
        SELECT selection, odds FROM (
            SELECT selection, odds, snapshot_time, ROW_NUMBER() OVER (
                PARTITION BY selection ORDER BY snapshot_time DESC) AS rn
            FROM reference_odds WHERE match_id = ? AND bookmaker = 'Pinnacle')
        WHERE rn = 1 AND datetime(snapshot_time) >= datetime(
            COALESCE((SELECT MAX(snapshot_time) FROM odds_snapshots WHERE match_id = ?),
                     snapshot_time), '-{FRESH_HOURS} hours')""", (match_id, match_id)).fetchall()
    return fair_probabilities(dict(rows)) if rows else {}


def compute_opportunities(conn, matches_df, min_ev, markets=None):
    """Opportunità di valore: quote dei bookmaker italiani SOPRA il prezzo
    giusto di Pinnacle. Il backtest (scheda Performance) ha mostrato che il
    nostro modello, da solo, prevede peggio del mercato e scommettere sui suoi
    'valori' perdeva; confrontare le quote con Pinnacle ha invece dato CLV
    positivo. La probabilità del modello resta visibile solo come informazione."""
    all_opportunities = []
    for _, row in matches_df.iterrows():
        fair = fair_for_match(conn, row["id"])
        if not fair:
            continue  # senza il prezzo di Pinnacle non sappiamo cosa è "giusto"
        odds_info = best_odds_for_match(conn, row["id"])
        if odds_info is None:
            continue
        best_odds, best_bookmaker, best_time = odds_info
        model = model_probabilities(row)
        for sel, p in fair.items():
            if markets and market_of(sel) not in markets:
                continue
            odds = best_odds.get(sel)
            if not odds or odds > MAX_ODDS or best_bookmaker[sel] not in BOOKMAKER_ITALIA:
                continue
            ev = expected_value(p, odds)
            if ev < min_ev:
                continue
            all_opportunities.append({
                "Partita": f"{row['home']} vs {row['away']}",
                "Campionato": row["league"],
                "Data": fmt_when(row["date"], row.get("kickoff_utc")),
                "Esito": long_label(sel),
                "Nostra probabilità": round(p * 100, 1),
                "Prob. modello": round(model[sel] * 100, 1) if sel in model else None,
                "Quota migliore": odds,
                "Bookmaker": best_bookmaker[sel],
                "Aggiornata": odds_age_label(best_time[sel]),
                "Valore atteso (EV)": f"{ev:+.1%}",
                "Puntata consigliata": f"{kelly_fraction(p, odds) * 100:.1f}%",
                "Rischio": risk_label(odds),
                "_ev_sort": ev, "_ev": ev, "_p": p,
            })
    return all_opportunities


conn = get_connection()


def last_health(conn):
    """Esito dell'ultimo controllo di salute del giro giornaliero:
    (quando, [(livello, messaggio), ...]) oppure (None, [])."""
    try:
        run_at = conn.execute("SELECT MAX(run_at) FROM health_checks").fetchone()[0]
        if not run_at:
            return None, []
        return run_at, conn.execute("SELECT level, message FROM health_checks WHERE run_at = ? "
                                    "ORDER BY level DESC", (run_at,)).fetchall()
    except sqlite3.Error:
        return None, []


def last_update_label(conn):
    """'aggiornate gio 24 set, 11:13' in ora italiana, dall'ultima quota salvata."""
    try:
        t = conn.execute("SELECT MAX(snapshot_time) FROM odds_snapshots").fetchone()[0]
        t = datetime.fromisoformat(t).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        return "Quote aggiornate " + fmt_when(t[:10], t)
    except (sqlite3.Error, TypeError, ValueError):
        return ""


st.markdown(f"""
<div class="app-header">
    <h1>⚽ Le mie previsioni calcio</h1>
    <p>Le quote dei bookmaker italiani confrontate con il prezzo giusto di Pinnacle.
       {last_update_label(conn)}.</p>
</div>
""", unsafe_allow_html=True)

health_at, health_rows = last_health(conn)
health_errors = [m for lvl, m in health_rows if lvl == "ERRORE"]
if health_errors:
    st.error("L'ultimo aggiornamento dei dati ha avuto problemi, quindi alcune quote o "
             "opportunità potrebbero mancare o essere vecchie:\n\n"
             + "\n".join(f"- {m}" for m in health_errors))

try:
    matches_df = load_upcoming_with_predictions(conn)
except Exception:
    st.warning("Il database non è ancora pronto. Esegui prima gli script di "
               "aggiornamento dati (storico, partite in arrivo, quote, modello).")
    st.stop()

if matches_df.empty:
    st.info("Nessuna partita in arrivo con previsione calcolata al momento. "
             "Torna più tardi, oppure aggiorna i dati.")
    st.stop()

tab_opportunita, tab_schedina, tab_marcatori, tab_storico, tab_analisi = st.tabs(
    ["🎯 Opportunità", "🎟️ Schedina", "⚽ Marcatori", "📊 Storico", "📈 Analisi"]
)

# ---------------------------------------------------------------------------
# SCHEDA 1: Opportunità di valore
# ---------------------------------------------------------------------------
with tab_opportunita:
    mercati_verificabili = [m for m in MARKET_NAMES if m != "Marcatori"]
    f1, f2, f3 = st.columns([1, 1, 2])
    with f1:
        filtered_opp = competition_filter(matches_df, key="comp_opportunita")
    with f2:
        markets_opp = market_filter(mercati_verificabili, key="mercati_opportunita",
                                    help="I marcatori non ci sono: Pinnacle non li quota, quindi "
                                         "manca il prezzo giusto con cui confrontarli.")
    with f3:
        min_ev = st.slider("Vantaggio minimo sul prezzo giusto", min_value=0, max_value=10,
                           value=0, format="%d%%") / 100

    all_opportunities = compute_opportunities(conn, filtered_opp, min_ev, markets_opp)

    if not all_opportunities:
        st.info("Nessuna quota sopra il prezzo giusto con questi filtri. Abbassa il "
                "vantaggio minimo o allarga competizioni e mercati.")
    else:
        opp_df = pd.DataFrame(all_opportunities).sort_values("_ev_sort", ascending=False)
        n = len(opp_df)
        st.subheader(f"{n} quote sopra il prezzo giusto" if n > 1 else "1 quota sopra il prezzo giusto")

        records = opp_df.to_dict("records")
        for i in range(0, len(records), 3):
            row_chunk = records[i:i + 3]
            cols = st.columns(3)
            for j, (col, opp) in enumerate(zip(cols, row_chunk)):
                with col:
                    render_spotlight_card(opp, top=(i + j) < 3)
            st.write("")

    with st.expander("Come leggere queste schede"):
        st.markdown(
            f"""
Ogni scheda confronta la **quota del bookmaker italiano** con il **prezzo giusto**:
la quota di Pinnacle (il bookmaker più efficiente) senza il suo margine. Se il
bookmaker paga più del prezzo giusto, la scommessa ha un piccolo vantaggio,
indicato dall'etichetta in percentuale.

- Nel backtest questa strategia ha preso quote migliori della chiusura (CLV
  positivo); le previsioni del nostro modello, da sole, perdevano. Per questo la
  probabilità del modello è solo un'informazione in più.
- Solo quote fino a {MAX_ODDS:g}: oltre, anche il vantaggio apparente perdeva.
- **Kelly teorico** è la frazione del capitale suggerita dal criterio di Kelly
  (1/4, prudente) *se* il prezzo giusto fosse esatto. Il vantaggio non è ancora
  dimostrato: meglio puntate piccole e uguali.
- Le quote si aggiornano una volta al giorno: prima di giocare controlla che siano
  ancora quelle sul sito del bookmaker.
""")

# ---------------------------------------------------------------------------
# SCHEDA 2: Schedina (a mano, o trovata automaticamente) da un unico bookmaker
# ---------------------------------------------------------------------------
QUOTA_MAX_OPZIONI = {"🟢 fino a 1.80": 1.80, "🟡 fino a 3.00": 3.00, "🔴 qualsiasi": float("inf")}


def quota_max_selector(key):
    """Quota massima per singola selezione, come tre pulsanti (prima era un
    cursore grande per un controllo secondario). Ritorna la quota massima."""
    scelta = st.segmented_control("Quota massima per selezione", list(QUOTA_MAX_OPZIONI),
                                  default="🔴 qualsiasi", key=key)
    return QUOTA_MAX_OPZIONI.get(scelta or "🔴 qualsiasi")


def step_title(n, text):
    """Titolo di un passaggio numerato (la schedina si costruisce in sequenza)."""
    st.markdown(f'<div class="step-title"><span class="step-num">{n}</span>{text}</div>',
                unsafe_allow_html=True)


SCHEDINA_MODI = [
    ("🖐️ Scelgo io le partite", "🖐️ Manuale", "Scegli tu le selezioni, tra quelle con valore."),
    ("🔍 Trova la combinazione migliore per me", "🔍 Obiettivo",
     "Scegli una quota obiettivo: trovo la combinazione più probabile che ci arriva."),
    ("⚖️ Miglior equilibrio probabilità/valore", "⚖️ Equilibrio",
     "Nessun obiettivo da fissare: trovo il miglior compromesso tra probabilità e valore."),
]

with tab_schedina:
    bookmakers = list_bookmakers(conn)
    if not bookmakers:
        st.info("Non ci sono ancora quote salvate per costruire una schedina.")
    else:
        step_title(1, "Dove giochi")
        col_a, col_b = st.columns([2, 1])
        with col_a:
            selected_bookmaker = st.selectbox("Bookmaker", bookmakers,
                                               format_func=bookmaker_display_label, key="sched_bookmaker",
                                               help="Una schedina va giocata tutta sullo stesso "
                                                    "bookmaker: non si mescolano quote di siti diversi.")
        with col_b:
            stake = st.number_input("Puntata (€)", min_value=1.0, value=10.0, step=1.0, key="sched_stake")

        leghe = sorted(matches_df["league"].unique())
        mercati_default = [m for m in MARKET_NAMES if m != "Marcatori"]
        comp_now = [c for c in st.session_state.get("comp_schedina", leghe) if c in leghe]
        merc_now = [m for m in st.session_state.get("mercati_schedina", mercati_default)
                    if m in MARKET_NAMES]
        with st.expander(f"⚙️ Filtri: {_summary(comp_now, leghe, 'tutte le competizioni', 'competizioni')}, "
                         f"{len(merc_now)} mercati"):
            sel_leghe = st.multiselect("Competizioni", leghe, default=leghe, key="comp_schedina")
            markets_sched = st.multiselect(
                "Mercati", MARKET_NAMES, default=mercati_default, key="mercati_schedina",
                help="Al massimo una selezione per partita: esiti della stessa partita "
                     "(es. 1 e Over 2.5) sono legati tra loro. I Marcatori sono in prova: "
                     "il loro valore viene dal nostro modello, non verificato.")
        filtered_sched = matches_df[matches_df["league"].isin(sel_leghe)]
        if "Marcatori" in markets_sched:
            st.caption("⚠️ Marcatori attivi: per questi il valore è stimato dal nostro modello, "
                       "non verificato (Pinnacle non li quota). Considerali in prova.")

        def sched_probs(row):
            """Probabilità giuste da Pinnacle (non dal nostro modello: vedi il
            backtest). Il 'valore' c'è quando la quota del bookmaker scelto è
            più alta del prezzo giusto."""
            return {k: v for k, v in fair_for_match(conn, row["id"]).items()
                    if market_of(k) in markets_sched}

        def entro_quota_max(legs):
            return [l for l in legs if l["odds"] <= MAX_ODDS]

        sched_scorer_odds = load_scorer_odds(conn) if "Marcatori" in markets_sched else None
        sched_scorer_preds = (load_upcoming_player_predictions(conn)
                              if sched_scorer_odds is not None and not sched_scorer_odds.empty
                              else None)

        def sched_scorer_legs(row):
            """Marcatori con valore di questa partita, sul bookmaker scelto.
            ATTENZIONE: qui il valore è stimato dal NOSTRO modello marcatori,
            non verificato (Pinnacle non quota i marcatori): esclusi di default."""
            if sched_scorer_preds is None:
                return []
            return [l for l in scorer_legs_for_match(sched_scorer_odds, row["id"],
                                                     sched_scorer_preds, [selected_bookmaker])
                    if l["ev"] >= 0]

        step_title(2, "Come la costruisci")
        if "schedina_mode" not in st.session_state:
            st.session_state["schedina_mode"] = SCHEDINA_MODI[0][0]
        mode = st.session_state["schedina_mode"]
        for col, (valore, titolo, descr) in zip(st.columns(3), SCHEDINA_MODI):
            with col:
                scelto = mode == valore
                st.markdown(f'<div class="mode-card{" on" if scelto else ""}"><div class="t">{titolo}</div>'
                            f'<div class="d">{descr}</div></div>', unsafe_allow_html=True)
                if st.button("✓ Scelta" if scelto else "Scegli", key=f"modo_{titolo}",
                             type="primary" if scelto else "secondary", width="stretch"):
                    st.session_state["schedina_mode"] = valore
                    st.rerun()

        step_title(3, "Scegli le selezioni" if mode.startswith("🖐️") else "Preferenze")

        combo = None
        target_roi_pct = None

        if mode.startswith("🖐️"):
            # --- Modalità manuale: scegli tu quali partite mettere in schedina ---
            candidate_legs = {}
            for _, row in filtered_sched.iterrows():
                bm_odds = odds_by_bookmaker_for_match(conn, row["id"], selected_bookmaker) or {}
                for vb in entro_quota_max(find_value_bets(sched_probs(row), bm_odds, min_ev=0.0)) + sched_scorer_legs(row):
                    esito_label = vb.get("label") or long_label(vb["selection"])
                    label = (f"{row['home']} vs {row['away']} — {esito_label} @ {vb['odds']} "
                             f"(prob. giusta {vb['model_probability']:.0%}, EV {vb['ev']:+.1%})")
                    vb["match_id"] = row["id"]
                    vb["match_label"] = f"{row['home']} vs {row['away']}"
                    vb["match_date"] = row["date"]
                    candidate_legs[label] = vb

            if not candidate_legs:
                st.info(f"Nessuna selezione con valore trovata su {selected_bookmaker} al momento.")
            else:
                chosen = st.multiselect(
                    "Seleziona le partite da mettere in schedina:",
                    options=list(candidate_legs.keys()), placeholder="Scegli una o più selezioni",
                )
                if chosen:
                    combo, seen = [], set()
                    for c in chosen:
                        leg = candidate_legs[c]
                        if leg["match_id"] in seen:
                            st.warning(f"{leg['match_label']}: puoi mettere una sola selezione "
                                       "per partita — tengo solo la prima che hai scelto.")
                            continue
                        seen.add(leg["match_id"])
                        combo.append(leg)
                else:
                    st.caption("Seleziona una o più partite qui sopra per vedere la schedina combinata.")

        elif mode.startswith("⚖️"):
            # --- Modalità equilibrio: nessun obiettivo da fissare, scelgo io il
            # miglior compromesso tra probabilità di vincere e valore ---
            st.caption(
                "Per ogni livello di quota cerco la schedina più probabile (a parità di quota "
                "è anche quella con più valore, perché valore = probabilità × quota). Poi ti "
                "consiglio quella che fa crescere di più il capitale nel lungo periodo "
                "(criterio di Kelly): né la più sicura con poco valore, né la più ricca quasi "
                "impossibile da vincere. Col cursore puoi spostarti verso una delle due."
            )
            col_e, col_f = st.columns(2)
            with col_e:
                num_matches_eq = st.slider("Numero di partite", min_value=1, max_value=5,
                                           value=3, key="eq_num_matches")
            with col_f:
                max_odds_eq = quota_max_selector("eq_max_risk")

            if st.button("⚖️ Calcola le combinazioni", type="primary"):
                legs_by_match = {}
                for _, row in filtered_sched.iterrows():
                    bm_odds = odds_by_bookmaker_for_match(conn, row["id"], selected_bookmaker) or {}
                    # solo selezioni con valore (EV >= 0), come nelle altre modalità
                    candidates = (entro_quota_max(find_value_bets(sched_probs(row), bm_odds, min_ev=0.0))
                                  + sched_scorer_legs(row))
                    candidates = [c for c in candidates if c["odds"] <= max_odds_eq]
                    for c in candidates:
                        c["match_id"] = row["id"]
                        c["match_label"] = f"{row['home']} vs {row['away']}"
                        c["match_date"] = row["date"]
                    if candidates:
                        legs_by_match[row["id"]] = candidates

                frontier = find_tradeoff_frontier(legs_by_match, num_matches_eq)
                if not frontier:
                    st.session_state.pop("eq_points", None)
                    st.warning(f"Non ci sono abbastanza partite con valore su {selected_bookmaker} "
                               f"(con la quota massima scelta) per formare una schedina di "
                               f"{num_matches_eq} partite. Riduci il numero di partite o allarga "
                               f"la quota massima.")
                else:
                    st.session_state["eq_points"] = pick_representatives(frontier)
                    st.session_state.pop("eq_choice", None)   # riparte dalla consigliata

            if "eq_points" in st.session_state:
                points = st.session_state["eq_points"]
                best_i = max(range(len(points)), key=lambda i: points[i]["kelly_growth"])

                def eq_label(i):
                    p = points[i]
                    tag = " ⭐ consigliata" if i == best_i else ""
                    return f"quota {p['odds']:.2f} · vince {p['probability']:.0%}{tag}"

                if len(points) > 1:
                    labels = [eq_label(i) for i in range(len(points))]
                    chosen_label = st.select_slider(
                        "Più sicura ← → più remunerativa",
                        options=labels, value=labels[best_i], key="eq_choice",
                    )
                    idx = labels.index(chosen_label)
                else:
                    idx = 0

                try:
                    import altair as alt
                    chart_df = pd.DataFrame([{
                        "Probabilità di vincita (%)": p["probability"] * 100,
                        "Valore atteso (%)": p["ev"] * 100,
                        "Quota": round(p["odds"], 2),
                        "Tipo": ("Scelta" if i == idx else
                                 "Consigliata" if i == best_i else "Alternativa"),
                    } for i, p in enumerate(points)])
                    chart = alt.Chart(chart_df).mark_circle(size=110).encode(
                        x=alt.X("Probabilità di vincita (%):Q", scale=alt.Scale(type="log")),
                        y="Valore atteso (%):Q",
                        color=alt.Color("Tipo:N", scale=alt.Scale(
                            domain=["Scelta", "Consigliata", "Alternativa"],
                            range=["#e4572e", "#f2a541", "#8a8a8a"])),
                        tooltip=["Quota", "Probabilità di vincita (%)", "Valore atteso (%)"],
                    ).properties(height=260)
                    st.altair_chart(chart, width="stretch")
                except Exception:
                    pass  # il grafico è solo un aiuto: se manca altair si va avanti senza

                chosen_pt = points[idx]
                combo = list(chosen_pt["combo"])
                target_roi_pct = round((chosen_pt["odds"] - 1) * 100)
                stake_pct = kelly_fraction(chosen_pt["probability"], chosen_pt["odds"]) * 100
                st.caption(
                    f"Valore atteso di questa schedina: {chosen_pt['ev']:+.1%} "
                    f"(probabilità giuste da Pinnacle). Puntata prudente suggerita "
                    f"(Kelly 1/4): {stake_pct:.2f}% del capitale che dedichi alle scommesse."
                )

        else:
            # --- Modalità automatica: dato un obiettivo, trovo io la combinazione ---
            col_c, col_d = st.columns(2)
            with col_c:
                quota_obiettivo = st.slider(
                    "Quota obiettivo", min_value=1.2, max_value=6.0, value=2.0, step=0.1,
                    format="%.1f", key="quota_obiettivo",
                    help="La quota combinata minima della schedina: 2.0 vuol dire raddoppiare "
                         "la puntata se vince. Tra le combinazioni che ci arrivano, scelgo la più probabile.")
                target_roi_pct = round((quota_obiettivo - 1) * 100)
            with col_d:
                num_matches = st.slider("Numero di partite", min_value=1, max_value=5, value=3)
            max_odds_auto = quota_max_selector("auto_max_risk")
            st.caption("Con quote massime basse le selezioni disponibili sono meno: se la quota "
                       "obiettivo non si può raggiungere, te lo segnalo.")

            if st.button("🔍 Trova la combinazione migliore", type="primary"):
                legs_by_match = {}
                for _, row in filtered_sched.iterrows():
                    bm_odds = odds_by_bookmaker_for_match(conn, row["id"], selected_bookmaker) or {}
                    # solo selezioni con valore (EV >= 0): la schedina deve essere
                    # conveniente, non solo "probabile"
                    candidates = (entro_quota_max(find_value_bets(sched_probs(row), bm_odds, min_ev=0.0))
                                  + sched_scorer_legs(row))
                    candidates = [c for c in candidates if c["odds"] <= max_odds_auto]
                    if candidates:
                        for c in candidates:
                            c["match_id"] = row["id"]
                            c["match_label"] = f"{row['home']} vs {row['away']}"
                            c["match_date"] = row["date"]
                        legs_by_match[row["id"]] = candidates

                result = find_best_combination(legs_by_match, num_matches, target_roi_pct / 100)

                if result is None:
                    st.session_state.pop("auto_combo", None)
                    st.warning(f"Non ci sono abbastanza partite con valore su {selected_bookmaker} "
                               f"(con la quota massima scelta) per una schedina di "
                               f"{num_matches} partite. Alza la quota massima o riduci il numero "
                               f"di partite.")
                else:
                    found_combo, hit_target = result
                    st.session_state["auto_combo"] = {
                        "combo": found_combo, "hit_target": hit_target,
                        "target_roi_pct": target_roi_pct,
                    }

            if "auto_combo" in st.session_state:
                auto = st.session_state["auto_combo"]
                combo = auto["combo"]
                target_roi_pct = auto["target_roi_pct"]
                if not auto["hit_target"]:
                    st.info(f"Nessuna combinazione arriva a quota {1 + target_roi_pct / 100:.2f}: "
                            f"questa è quella con la quota più alta disponibile ora.")
                else:
                    st.success("Trovata una combinazione che raggiunge l'obiettivo:")

        # --- Da qui in poi: riepilogo e conferma, uguale per entrambe le modalità ---
        if combo:
            combined_odds = math.prod(leg["odds"] for leg in combo)
            combined_prob = math.prod(leg["model_probability"] for leg in combo)
            projected_return = stake * combined_odds
            projected_profit = projected_return - stake

            st.subheader("Combinazione")
            for leg in combo:
                render_leg_row(leg)

            c1, c2, c3 = st.columns(3)
            c1.metric("Quota combinata", f"{combined_odds:.2f}")
            c2.metric("Ritorno potenziale", f"€{projected_return:.2f}",
                      help="Quanto incassi SE la schedina è vincente (puntata × quota).")
            c3.metric("Vincita netta potenziale", f"€{projected_profit:.2f}")

            st.caption(
                f"Probabilità combinata (prezzi giusti di Pinnacle; per i marcatori il nostro "
                f"modello): {combined_prob:.1%}. "
                "Assume che le partite scelte siano indipendenti tra loro (vero nella "
                "stragrande maggioranza dei casi, a meno di partite con conseguenze dirette "
                "l'una sull'altra)."
            )

            github_token = get_github_token()
            if github_token is None:
                st.caption(
                    "Per confermare e salvare questa schedina (con resoconto automatico "
                    "a fine partite) serve collegare un token GitHub."
                )
            else:
                if st.button("✅ Conferma questa schedina", key="confirm_slip_btn", type="primary"):
                    slip = {
                        "id": str(uuid.uuid4()),
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "bookmaker": selected_bookmaker,
                        "stake": stake,
                        "target_roi_pct": target_roi_pct,
                        "mode": ("automatica" if mode.startswith("🔍") else
                                 "equilibrio" if mode.startswith("⚖️") else "manuale"),
                        "status": "pending",
                        "combined_odds": round(combined_odds, 3),
                        "legs": [
                            {"match_id": leg["match_id"], "match_label": leg["match_label"],
                             "match_date": leg["match_date"], "selection": leg["selection"],
                             "label": leg.get("label") or long_label(leg["selection"]),
                             "odds": leg["odds"], "model_probability": leg["model_probability"]}
                            for leg in combo
                        ],
                    }
                    try:
                        slips, sha = read_json_file(GITHUB_OWNER, GITHUB_REPO, SLIPS_PATH,
                                                      github_token, default=[])
                        slips.append(slip)
                        write_json_file(GITHUB_OWNER, GITHUB_REPO, SLIPS_PATH, github_token,
                                         slips, sha,
                                         f"Nuova schedina confermata ({len(slip['legs'])} partite)")
                        st.success("Schedina salvata! La trovi nella scheda 'Storico schedine'.")
                        st.session_state.pop("auto_combo", None)
                        st.session_state.pop("eq_points", None)
                    except Exception as e:
                        st.error(f"Non sono riuscito a salvare la schedina: {e}")

# ---------------------------------------------------------------------------
# SCHEDA 3: Storico schedine confermate
# ---------------------------------------------------------------------------
with tab_storico:
    github_token = get_github_token()
    if github_token is None:
        st.info(
            "Questa scheda mostra le schedine che confermi nella scheda Schedina, "
            "con il resoconto automatico una volta finite le partite. Per attivarla "
            "serve collegare un token GitHub — vedi le istruzioni che ti ho dato."
        )
    else:
        try:
            slips, _ = read_json_file(GITHUB_OWNER, GITHUB_REPO, SLIPS_PATH, github_token, default=[])
        except Exception as e:
            st.error(f"Non sono riuscito a leggere lo storico: {e}")
            slips = []

        pending = [s for s in slips if s.get("status") == "pending"]
        settled = [s for s in slips if s.get("status") in ("won", "lost")]

        st.subheader(f"In attesa ({len(pending)})")
        if not pending:
            st.caption("Nessuna schedina in attesa al momento.")
        for slip in sorted(pending, key=lambda s: s["created_at"], reverse=True):
            render_slip_card(slip)
            slip_id = slip["id"]
            confirm_key = f"confirm_delete_{slip_id}"
            if not st.session_state.get(confirm_key):
                if st.button("🗑️ Cancella", key=f"delete_{slip_id}"):
                    st.session_state[confirm_key] = True
                    st.rerun()
            else:
                st.warning("Vuoi davvero cancellare questa schedina? Non si può annullare.")
                col_yes, col_no, _ = st.columns([1, 1, 3])
                if col_yes.button("Sì, cancella", key=f"yes_{slip_id}", type="primary"):
                    try:
                        # rileggo il file appena prima di scrivere: nel frattempo il
                        # resoconto automatico potrebbe averlo aggiornato
                        current, sha = read_json_file(GITHUB_OWNER, GITHUB_REPO, SLIPS_PATH,
                                                      github_token, default=[])
                        target = next((x for x in current if x.get("id") == slip_id), None)
                        if target is None:
                            st.info("Questa schedina non c'è più: forse è già stata cancellata.")
                        elif target.get("status") != "pending":
                            st.error("Nel frattempo questa schedina si è conclusa: non la cancello, "
                                     "così il suo risultato resta nello storico.")
                        else:
                            remaining = [x for x in current if x.get("id") != slip_id]
                            write_json_file(GITHUB_OWNER, GITHUB_REPO, SLIPS_PATH, github_token,
                                            remaining, sha,
                                            f"Schedina cancellata ({len(target['legs'])} partite)")
                            st.session_state.pop(confirm_key, None)
                            st.rerun()
                    except Exception as e:
                        st.error(f"Non sono riuscito a cancellare la schedina: {e}")
                if col_no.button("Annulla", key=f"no_{slip_id}"):
                    st.session_state.pop(confirm_key, None)
                    st.rerun()

        st.divider()
        st.subheader(f"Concluse ({len(settled)})")
        if not settled:
            st.caption("Nessuna schedina ancora conclusa.")
        for slip in sorted(settled, key=lambda s: s.get("settled_at", ""), reverse=True):
            render_slip_card(slip)

        if settled:
            total_staked = sum(s["stake"] for s in settled)
            total_profit = sum(s["result_summary"]["profit"] for s in settled)
            wins = sum(1 for s in settled if s["status"] == "won")
            st.divider()
            c1, c2, c3 = st.columns(3)
            c1.metric("Schedine vinte", f"{wins}/{len(settled)}")
            c2.metric("Totale puntato", f"€{total_staked:.2f}")
            c3.metric("Profitto totale", f"€{total_profit:+.2f}")

# ---------------------------------------------------------------------------
# SCHEDA 4: Marcatori (probabilità di segnare, senza confronto con le quote
# — non abbiamo ancora una fonte di quote sui marcatori verificata)
# ---------------------------------------------------------------------------
with tab_marcatori:
    st.caption(
        "Probabilità che il giocatore segni SE GIOCA (se non entra, la scommessa marcatore "
        "viene rimborsata): gol attesi della squadra divisi in base agli xG senza rigori, più "
        "i rigori attribuiti al probabile rigorista. Verificato sui risultati passati: le "
        "probabilità sono ben calibrate. Non ancora verificato contro le quote (Pinnacle non "
        "quota i marcatori): il valore atteso qui resta indicativo. Per chi ha giocato poco "
        "di recente la stima è più incerta."
    )

    try:
        player_predictions_df = load_upcoming_player_predictions(conn)
    except Exception:
        player_predictions_df = pd.DataFrame()

    if player_predictions_df.empty:
        st.info(
            "Nessuna previsione marcatori disponibile al momento. Questa è una "
            "funzione nuova — se hai appena attivato la fonte dati, il primo "
            "aggiornamento automatico potrebbe non essere ancora passato."
        )
    else:
        df = player_predictions_df.copy()
        df["partita"] = df["home_team"] + " - " + df["away_team"] + "  (" + df["date"] + ")"

        # Filtri a cascata: ogni menu mostra solo le voci ancora possibili
        # dopo le scelte precedenti (competizione -> partita -> squadra).
        f0, f1, f2 = st.columns([1, 1.5, 1.5])
        with f0:
            df = competition_filter(df, key="comp_marcatori")
        with f1:
            partite = ["Tutte le partite"] + sorted(df["partita"].unique(),
                                                    key=lambda p: (p[-11:], p))
            partita = st.selectbox("Partita:", partite, key="marc_partita")
        if partita != "Tutte le partite":
            df = df[df["partita"] == partita]
        with f2:
            squadre = st.multiselect("Squadra:", sorted(df["team"].unique()),
                                     key="marc_squadre", placeholder="Tutte")
        if squadre:
            df = df[df["team"].isin(squadre)]

        # Quote marcatore dei bookmaker (compaiono 1-3 giorni prima della partita)
        scorer_odds_df = load_scorer_odds(conn)
        libri = sorted(scorer_odds_df["bookmaker"].unique())
        if libri:
            scelti = st.multiselect("Bookmaker (quota migliore tra quelli scelti):", libri,
                                    default=libri, format_func=bookmaker_display_label,
                                    key="marc_bookmaker")
            df = df.merge(best_scorer_odds(scorer_odds_df, scelti),
                          on=["match_id", "player_id"], how="left")
            df["ev"] = df["prob_score_anytime"] * df["best_odds"] - 1
        else:
            st.caption("Le quote marcatore non sono ancora disponibili: i bookmaker aprono "
                       "questo mercato solo 1-3 giorni prima della partita. Nel frattempo "
                       "vedi le probabilità del nostro modello.")
            df["best_odds"], df["best_bookmaker"], df["ev"] = None, None, None

        f3, f4, f5 = st.columns([1.5, 1.5, 1])
        with f3:
            min_prob = st.slider("Probabilità di segnare almeno:",
                                 min_value=0, max_value=80, value=20, format="%d%%",
                                 key="marc_prob") / 100
        with f4:
            ordini = ["Probabilità", "Gol attesi"] + (["Valore atteso"] if libri else [])
            ordine = st.radio("Ordina per:", ordini, key="marc_ordine", horizontal=True)
        with f5:
            solo_titolari = st.checkbox("Solo probabili titolari", key="marc_titolari",
                                        help="Titolare in almeno 3 delle ultime 5 partite della squadra.")
            solo_valore = st.checkbox("Solo con valore (EV > 0)", key="marc_valore",
                                      disabled=not libri)

        df = df[df["prob_score_anytime"] >= min_prob]
        if solo_titolari:
            df = df[df["starting_probability"].fillna(0) >= 0.6]
        if solo_valore:
            df = df[df["ev"].fillna(-1) > 0]
        colonna = {"Probabilità": "prob_score_anytime", "Gol attesi": "expected_goals",
                   "Valore atteso": "ev"}[ordine]
        df = df.sort_values(colonna, ascending=False, na_position="last")

        MAX_SCHEDE = 60
        if df.empty:
            st.info("Nessun giocatore con questi filtri. Prova ad abbassare la soglia "
                    "o ad allargare la selezione.")
        else:
            st.subheader(f"{len(df)} giocatori"
                         + (f", mostrati i primi {MAX_SCHEDE}" if len(df) > MAX_SCHEDE else ""))
            records = df.head(MAX_SCHEDE).to_dict("records")
            for i in range(0, len(records), 3):
                row_chunk = records[i:i + 3]
                cols = st.columns(3)
                for col, row in zip(cols, row_chunk):
                    with col:
                        render_scorer_card(row)
                st.write("")

# ---------------------------------------------------------------------------
# SCHEDA 5: Analisi — come sta andando (prova dal vivo, backtest) e tutte le partite
# ---------------------------------------------------------------------------
def bootstrap_ci(values, reps=4000, seed=0):
    """Intervallo al 95% della media, ricampionando i dati (bootstrap): dice
    quanto potrebbe essere diverso il numero vero, dato il campione piccolo."""
    import numpy as np
    v = np.asarray([x for x in values if x == x], dtype=float)
    if len(v) < 5:
        return None
    rng = np.random.default_rng(seed)
    means = rng.choice(v, size=(reps, len(v)), replace=True).mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def semaforo(n):
    """Quanto è affidabile un risultato, in base al numero di scommesse."""
    if n < 50:
        return "light-rosso", "Campione insufficiente"
    if n < 200:
        return "light-giallo", "Risultato preliminare"
    return "light-verde", "Campione più affidabile"


def ci_text(ci):
    if ci is None:
        return "troppo poche scommesse per un intervallo"
    lo, hi = ci
    tail = ("comprende lo zero: non si distingue ancora dalla fortuna" if lo < 0 < hi
            else "non comprende lo zero")
    return f"intervallo al 95%: da {lo:+.1%} a {hi:+.1%} ({tail})"


def mini_list(title, df, key_col):
    """Piccolo elenco "voce, CLV medio, numero" al posto di una tabella larga."""
    righe = "".join(
        f'<div><span>{r[key_col]}</span><span><b>{r["clv"]:+.1%}</b> '
        f'<span class="n">{int(r["n"])} scomm.</span></span></div>'
        for _, r in df.sort_values("n", ascending=False).iterrows())
    render_html(f"""
    <div class="verdict">
        <div class="k">{title}</div>
        <div class="mini-list">{righe}</div>
    </div>
    """)


def load_virtual_bets(conn):
    """Scommesse virtuali. Il CLV è definitivo solo quando la partita è
    iniziata: prima, la "chiusura" è solo l'ultimo prezzo visto e il CLV è
    provvisorio (appena registrata, coincide col vantaggio, quindi è sempre
    positivo e non dice niente). Colonna 'definitiva' = partita iniziata."""
    ko = "m.kickoff_utc" if has_kickoff(conn) else "NULL"
    try:
        vb = pd.read_sql_query(f"""
            SELECT v.*, m.home_goals, m.away_goals, m.date, m.league,
                   h.name AS home, a.name AS away,
                   (m.home_goals IS NOT NULL
                    OR ({ko} IS NOT NULL AND {ko} <= datetime('now'))
                    OR ({ko} IS NULL AND m.date < date('now'))) AS definitiva
            FROM virtual_bets v JOIN matches m ON m.id = v.match_id
            JOIN teams h ON h.id = m.home_team_id JOIN teams a ON a.id = m.away_team_id
        """, conn)
    except Exception:
        return pd.DataFrame(), pd.DataFrame()
    if vb.empty:
        return vb, vb
    vb["definitiva"] = vb["definitiva"].astype(bool)
    vb["clv"] = vb["odds"] * vb["close_fair_prob"] - 1
    vb["mercato"] = [market_of(x) for x in vb["selection"]]
    chiuse = vb[vb["home_goals"].notna()].copy()
    if not chiuse.empty:
        chiuse["vinta"] = [is_winner(s_, int(h), int(a)) for s_, h, a in
                           zip(chiuse["selection"], chiuse["home_goals"], chiuse["away_goals"])]
        chiuse["profitto"] = [o - 1 if w else -1 for o, w in zip(chiuse["odds"], chiuse["vinta"])]
    return vb, chiuse


def load_backtest():
    try:
        with open("backtest_report.json") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


def strategia_app_nel_backtest(bt):
    """Risultato nel backtest della strategia usata dall'app: quota sopra il
    prezzo giusto di Pinnacle (margine tolto col metodo potenza), quote fino a 5."""
    rows = (bt or {}).get("contro_pinnacle", {}).get("1x2_quota_massima_potenza") or []
    rows = [r for r in rows if str(r.get("soglia_vantaggio", "")).startswith("+0%")
            and "oltre" not in str(r.get("soglia_vantaggio", ""))]
    n = sum(r["scommesse"] for r in rows)
    if not n:
        return None
    roi = sum(r["roi"] * r["scommesse"] for r in rows) / n
    clv_rows = [r for r in rows if r.get("clv_medio") is not None]
    clv = (sum(r["clv_medio"] * r["scommesse"] for r in clv_rows)
           / sum(r["scommesse"] for r in clv_rows)) if clv_rows else None
    return n, roi, clv


with tab_analisi:
    vb, chiuse = load_virtual_bets(conn)
    bt = load_backtest()
    sub_panoramica, sub_live, sub_backtest, sub_calibrazione, sub_tutte = st.tabs(
        ["Panoramica", "Prova dal vivo", "Backtest", "Calibrazione", "Tutte le partite"])

    # --- Panoramica: il sistema sta funzionando? --------------------------
    with sub_panoramica:
        c_live, c_bt = st.columns(2)
        with c_live:
            if vb.empty:
                render_html("""
                <div class="verdict">
                    <div class="k">Prova dal vivo, CLV medio</div>
                    <div class="big">—</div>
                    <div class="sub">Nessuna scommessa virtuale ancora registrata.</div>
                </div>""")
            else:
                fin = vb[vb["definitiva"]]
                cls, testo = semaforo(len(fin))
                attesa = len(vb) - len(fin)
                render_html(f"""
                <div class="verdict">
                    <div class="k">Prova dal vivo, CLV medio</div>
                    <div class="big">{f"{fin['clv'].mean():+.1%}" if len(fin) else "—"}</div>
                    <div class="sub">{len(fin)} scommesse con chiusura definitiva,
                        {attesa} in attesa dell'inizio della partita</div>
                    <div class="ci">{ci_text(bootstrap_ci(fin['clv'])) if len(fin) else
                        "il CLV si misura quando le partite iniziano"}</div>
                    <span class="light-badge {cls}">{testo}</span>
                </div>""")
        with c_bt:
            if bt is None:
                render_html("""
                <div class="verdict">
                    <div class="k">Backtest</div>
                    <div class="big">—</div>
                    <div class="sub">Non ancora eseguito (gira ogni lunedì).</div>
                </div>""")
            else:
                ll = bt["1x2"]["log_loss"]
                tot = bt["1x2"].get("totale_modello") or {}
                roi_txt = f"{tot['roi']:+.0%}" if tot.get("roi") is not None else "—"
                n_partite = f"{bt['partite_valutate']:,}".replace(",", ".")
                n_scomm = f"{tot.get('scommesse', 0):,}".replace(",", ".")
                render_html(f"""
                <div class="verdict">
                    <div class="k">Backtest su {n_partite} partite</div>
                    <div class="big">{roi_txt}</div>
                    <div class="sub">rendimento scommettendo sui valori del nostro modello
                        ({n_scomm} scommesse)</div>
                    <div class="ci">errore di previsione (log loss, più basso è meglio):
                        modello {ll['modello']:.3f}, Pinnacle {ll['pinnacle_chiusura']:.3f}</div>
                </div>""")

        if bt is not None:
            ll = bt["1x2"]["log_loss"]
            testo = ("Pinnacle prevede meglio del nostro modello. Per questo l'app usa Pinnacle "
                     "come prezzo giusto e mostra il modello solo come informazione in più."
                     if ll["modello"] > ll["pinnacle_chiusura"] else
                     "Il modello prevede quanto o meglio di Pinnacle: vale la pena ridargli peso.")
            st.write("")
            st.markdown(f"**{testo}**")
            app = strategia_app_nel_backtest(bt)
            if app:
                n_app, roi_app, clv_app = app
                st.caption(
                    f"La strategia usata dall'app (quota sopra il prezzo giusto di Pinnacle, quote "
                    f"fino a {MAX_ODDS:g}) nel backtest: rendimento {roi_app:+.1%}"
                    + (f", CLV {clv_app:+.1%}" if clv_app is not None else "")
                    + f" su {n_app:,} scommesse. ".replace(",", ".")
                    + "Attenzione: usa la quota migliore tra ~40 bookmaker, più generosa dei 4 "
                      "italiani. È la prova dal vivo a dire se regge anche da noi.")

        st.write("")
        st.markdown("**Stato dell'ultimo aggiornamento dei dati**")
        if health_at is None:
            st.caption("Nessun controllo ancora registrato: il primo arriva col prossimo giro "
                       "giornaliero.")
        else:
            quando = fmt_when(health_at[:10], datetime.fromisoformat(health_at)
                              .strftime("%Y-%m-%d %H:%M:%S"))
            if not any(lvl in ("ERRORE", "AVVISO") for lvl, _ in health_rows):
                st.caption(f"🟢 {quando}: tutti i controlli superati (quote di ogni bookmaker, "
                           "Pinnacle, abbinamenti, previsioni, marcatori).")
            else:
                st.caption(f"Controllo del {quando}:")
                for lvl, msg in health_rows:
                    if lvl in ("ERRORE", "AVVISO"):
                        st.caption(("🔴 " if lvl == "ERRORE" else "🟡 ") + msg)

    # --- Prova dal vivo ---------------------------------------------------
    with sub_live:
        st.caption(
            "Ogni giorno registriamo, senza giocare soldi, le quote dei bookmaker italiani "
            "che superano il prezzo giusto di Pinnacle (quote fino a 5). CLV = quanto la "
            "quota presa batteva il prezzo di Pinnacle poco prima della partita: si "
            "stabilizza con poche centinaia di scommesse, il rendimento ne richiede molte di più.")
        if vb.empty:
            st.info("Nessuna scommessa virtuale ancora registrata: la raccolta parte dal "
                    "prossimo aggiornamento delle quote.")
        else:
            fin = vb[vb["definitiva"]]
            cls, testo = semaforo(len(fin))
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Scommesse virtuali", len(vb),
                      help="Tutte quelle registrate, anche su partite non ancora iniziate.")
            m2.metric("Con chiusura definitiva", len(fin),
                      help="Partite iniziate: il prezzo di chiusura di Pinnacle non cambia più.")
            m3.metric("CLV medio", f"{fin['clv'].mean():+.1%}" if len(fin) else "—",
                      help="Solo sulle chiusure definitive. Positivo = le quote trovate "
                           "battevano il prezzo finale di Pinnacle.")
            m4.metric("Rendimento (concluse)",
                      f"{chiuse['profitto'].mean():+.1%}" if not chiuse.empty else "—",
                      help="Profitto medio per unità puntata. Con poche scommesse dipende "
                           "molto dalla fortuna: guarda soprattutto il CLV.")
            render_html(f'<span class="light-badge {cls}">{testo}: {len(fin)} scommesse con '
                        f'chiusura definitiva (ne servono almeno 200 per un giudizio)</span>')
            if len(fin):
                st.caption(f"CLV: {ci_text(bootstrap_ci(fin['clv']))}.")
            else:
                st.caption("Il CLV si misura quando le partite iniziano: finché una partita "
                           "non comincia, la chiusura di Pinnacle può ancora cambiare.")
            if not chiuse.empty:
                st.caption(f"Rendimento: {ci_text(bootstrap_ci(chiuse['profitto']))}.")
            if has_kickoff(conn):
                ko = pd.read_sql_query("SELECT id AS match_id, kickoff_utc FROM matches "
                                       "WHERE kickoff_utc IS NOT NULL", conn)
                vk = vb.merge(ko, on="match_id", how="inner")
                vk = vk[vk["home_goals"].notna() | (pd.to_datetime(vk["kickoff_utc"], utc=True)
                                                     <= pd.Timestamp.now(tz="UTC"))]
                if not vk.empty:
                    anticipo = ((pd.to_datetime(vk["kickoff_utc"], utc=True)
                                 - pd.to_datetime(vk["close_updated_at"], utc=True, format="ISO8601"))
                                .dt.total_seconds() / 60)
                    prima = anticipo[anticipo >= 0]
                    dopo = int((anticipo < 0).sum())
                    testo_ch = (f"Chiusura delle scommesse già iniziate: presa in mediana "
                                f"{prima.median():.0f} minuti prima del calcio d'inizio; entro "
                                f"un'ora per il {(prima <= 60).mean():.0%}. Più è vicina "
                                f"all'inizio, più il CLV è affidabile." if len(prima) else "")
                    if dopo:
                        testo_ch += (f" {dopo} scommesse hanno una chiusura presa dopo l'inizio "
                                     f"(registrate prima della correzione): il loro CLV è meno affidabile.")
                    st.caption(testo_ch.strip())

            if len(fin):
                g1, g2 = st.columns(2)
                with g1:
                    mini_list("CLV per bookmaker",
                              fin.groupby("bookmaker").agg(clv=("clv", "mean"), n=("id", "count"))
                              .reset_index(), "bookmaker")
                with g2:
                    mini_list("CLV per mercato",
                              fin.groupby("mercato").agg(clv=("clv", "mean"), n=("id", "count"))
                              .reset_index(), "mercato")
                st.write("")
            with st.expander("Ultime scommesse virtuali"):
                ultime = vb.sort_values("found_at", ascending=False).head(50)
                st.dataframe(pd.DataFrame({
                    "Data": ultime["date"], "Partita": ultime["home"] + " - " + ultime["away"],
                    "Esito": [long_label(x) for x in ultime["selection"]],
                    "Bookmaker": ultime["bookmaker"], "Quota": ultime["odds"],
                    "Vantaggio %": (ultime["edge"] * 100).round(1),
                    "CLV %": [round(c * 100, 1) if d else None
                              for c, d in zip(ultime["clv"], ultime["definitiva"])],
                }), hide_index=True, width='stretch')
                st.caption("CLV vuoto = partita non ancora iniziata.")

    def bet_table(rows):
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        for c in ("vinte", "roi", "clv_medio"):
            df[c] = (df[c] * 100).round(1)
        return df.rename(columns={"fascia_ev": "Fascia EV", "scommesse": "Scommesse",
                                  "vinte": "Vinte %", "roi": "ROI %", "clv_medio": "CLV medio %"})

    # --- Backtest ----------------------------------------------------------
    with sub_backtest:
        if bt is None:
            st.info("Il backtest non è ancora stato eseguito. Gira in automatico ogni lunedì "
                    "(workflow 'Backtest'), oppure puoi avviarlo a mano da GitHub Actions.")
        else:
            st.caption(
                f"Simulazione su {bt['partite_valutate']} partite dei 5 campionati principali "
                f"({bt['periodo']['da']} → {bt['periodo']['a']}): ogni mese il modello è stato "
                "allenato solo sulle partite precedenti e ha previsto quelle del mese, come fa "
                "l'app ogni giorno. Aggiornato: " + bt["generato"][:10] + ".")
            ll = bt["1x2"]["log_loss"]
            scarto = ll["modello"] / ll["pinnacle_chiusura"] - 1
            c1, c2, c3 = st.columns(3)
            c1.metric("Errore del modello (log loss 1X2)", f"{ll['modello']:.4f}",
                      help="Più basso è meglio. Misura quanto le probabilità erano lontane dai risultati.")
            c2.metric("Errore di Pinnacle alla chiusura", f"{ll['pinnacle_chiusura']:.4f}",
                      help="Il prezzo più efficiente del mercato: il riferimento da battere.")
            c3.metric("Modello rispetto a Pinnacle", f"{scarto:+.1%}",
                      help="Positivo = il modello sbaglia più del mercato.")

            v3 = bt.get("v3")
            if v3:
                st.subheader("I nostri dati migliorano il prezzo di Pinnacle?")
                st.caption(
                    "Modello a correzione: si parte dalla probabilità di Pinnacle del mattino e si "
                    "impara solo una piccola correzione dai nostri segnali (forza da gol, tiri, tiri "
                    "in porta, campionato). Se i segnali non servono, la correzione resta zero. "
                    f"Periodo di sviluppo {v3['periodo_sviluppo']['da']} / {v3['periodo_sviluppo']['a']}, "
                    "ogni mese allenato solo sui mesi precedenti. Differenza negativa = meglio di Pinnacle.")
                ref = v3["riferimenti"]
                if ref.get("partite"):
                    st.caption(f"Log loss sulle stesse {ref['partite']} partite: Pinnacle mattino "
                               f"{ref['pinnacle_mattino']}, Pinnacle chiusura {ref['pinnacle_chiusura']}, "
                               f"Dixon-Coles da solo {ref['dixon_coles']}.")
                righe = []
                for v in v3["varianti"]:
                    c = v.get("contro_pinnacle") or {}
                    esito = ("—" if not c else "meglio di Pinnacle" if c["significativa"] and c["differenza"] < 0
                             else "peggio di Pinnacle" if c["significativa"] else "non distinguibile")
                    righe.append({"Variante": v["variante"] + ("  ← scelta" if v["variante"] == v3["variante_scelta"] else ""),
                                  "Log loss": v["log_loss"], "Differenza": c.get("differenza"),
                                  "Intervallo 95%": f"{c['da']:+.5f} / {c['a']:+.5f}" if c else "",
                                  "Esito": esito})
                st.dataframe(pd.DataFrame(righe), hide_index=True, width='stretch')
                ho = v3["holdout"]
                if ho.get("esito"):
                    reg = ho["esito"]["regole"]
                    (st.success if ho["esito"]["adottare"] else st.warning)(
                        ("Holdout aperto: la correzione SUPERA la regola di adozione."
                         if ho["esito"]["adottare"] else "Holdout aperto: la correzione NON supera la regola di adozione.")
                        + "\n\n" + "\n".join(f"- {'✅' if ok else '❌'} {k}" for k, ok in reg.items()))
                else:
                    st.caption(f"Holdout (dal {ho['da']}) ancora chiuso: {ho['partite_con_pinnacle']} partite "
                               "con Pinnacle raccolte. Si apre una volta sola, a variante decisa.")
                ant = v3.get("anteprima_regola_sviluppo")
                if ant:
                    with st.expander("Regola di adozione applicata allo sviluppo (solo indicativa)"):
                        st.markdown("\n".join(f"- {'✅' if ok else '❌'} {k}" for k, ok in ant["regole"].items()))
                        st.caption("Sullo sviluppo il risultato è ottimista (la variante è stata scelta lì): "
                                   "decide solo l'holdout.")
                cov = v3.get("copertura_pinnacle")
                if cov:
                    with st.expander("Copertura delle quote Pinnacle nei file storici"):
                        fav = cov["favorita_media"]
                        st.caption(f"Probabilità media della favorita: {fav['con_pinnacle']} nelle partite "
                                   f"con Pinnacle, {fav['senza_pinnacle']} in quelle senza (se sono molto "
                                   "diverse, le partite senza Pinnacle non sono un campione casuale).")
                        for titolo, chiave in (("Per stagione", "per_stagione"),
                                               ("Per campionato", "per_campionato"), ("Per mese", "per_mese")):
                            st.markdown(f"**{titolo}**")
                            st.dataframe(pd.DataFrame(cov[chiave]).rename(columns={
                                "gruppo": "Gruppo", "partite": "Partite", "con_pinnacle": "Con Pinnacle",
                                "copertura": "Copertura"}), hide_index=True, width='stretch')

            v2 = bt.get("v2")
            if v2 and not v3:
                st.subheader("Migliorare il modello senza ingannarsi")
                st.caption(
                    f"Le varianti del modello si confrontano sulla VALIDAZIONE "
                    f"({v2['divisione']['validazione']}) e la migliore si verifica sul TEST "
                    f"({v2['divisione']['test']}), che non serve mai a scegliere. Log loss: più "
                    "basso è meglio. Una differenza conta solo se il suo intervallo al 95% non "
                    "comprende lo zero.")
                rif = v2.get("riferimenti", {})
                var_df = pd.DataFrame(v2["varianti"])
                var_df["nota"] = ["base (usata oggi)" if b else ("scelta" if v == v2["variante_scelta"] else "")
                                  for v, b in zip(var_df["variante"], var_df["base"])]
                var_df.loc[var_df["variante"] == v2["variante_scelta"], "nota"] = (
                    var_df.loc[var_df["variante"] == v2["variante_scelta"], "nota"]
                    .replace("base (usata oggi)", "base e scelta").replace("", "scelta"))
                st.dataframe(var_df[["variante", "validazione", "test", "nota"]].rename(columns={
                    "variante": "Variante", "validazione": "Log loss validazione",
                    "test": "Log loss test", "nota": ""}), hide_index=True, width='stretch')
                if rif.get("test"):
                    st.caption(f"Per confronto, Pinnacle sulle stesse partite del test: "
                               f"{rif['test']['pinnacle_prima']} qualche giorno prima, "
                               f"{rif['test']['pinnacle_chiusura']} alla chiusura.")
                righe = []
                for c in v2.get("confronti", []):
                    if c["significativa"]:
                        esito = "A migliore" if c["differenza"] < 0 else "A peggiore"
                    else:
                        esito = "non distinguibili"
                    righe.append({"Confronto (A contro B)": c["confronto"], "Periodo": c["periodo"],
                                  "Differenza": c["differenza"],
                                  "Intervallo 95%": f"{c['da']:+.4f} / {c['a']:+.4f}",
                                  "Esito": esito, "Partite": c["partite"]})
                if righe:
                    st.dataframe(pd.DataFrame(righe), hide_index=True, width='stretch')

            if bt.get("contro_pinnacle"):
                st.subheader("Strategia dell'app: bookmaker contro Pinnacle")
                st.caption("Si gioca quando la quota di un bookmaker supera la quota 'giusta' di "
                           "Pinnacle (senza margine) nello stesso momento. Nessuna previsione "
                           "nostra. 'Quota massima' = la migliore tra tutti i bookmaker del file "
                           "(include siti non disponibili in Italia).")
                nomi = {"1x2_bet365": "1X2, bet365", "1x2_quota_massima": "1X2, quota massima",
                        "ou25_bet365": "Under/Over 2.5, bet365",
                        "ou25_quota_massima": "Under/Over 2.5, quota massima",
                        "1x2_quota_massima_potenza": "1X2, quota massima (margine 'potenza')",
                        "ou25_quota_massima_potenza": "Under/Over 2.5, quota massima (margine 'potenza')"}
                righe = []
                for chiave, rows in bt["contro_pinnacle"].items():
                    for r in rows:
                        righe.append({"Strategia": nomi.get(chiave, chiave),
                                      "Soglia": r["soglia_vantaggio"], "Scommesse": r["scommesse"],
                                      "ROI %": round(r["roi"] * 100, 1),
                                      "CLV medio %": round(r["clv_medio"] * 100, 1)
                                      if r["clv_medio"] is not None else None})
                st.dataframe(pd.DataFrame(righe), hide_index=True, width='stretch')

            st.subheader("Scommettere dove il nostro modello vede valore")
            st.caption("1 unità su ogni esito con valore atteso positivo secondo il modello, "
                       "alle quote medie qualche giorno prima della partita.")
            st.dataframe(bet_table(bt["1x2"]["scommesse_modello"]), hide_index=True, width='stretch')
            if bt["1x2"].get("scommesse_miscela"):
                w = bt["1x2"]["peso_modello_migliore"]
                st.markdown(f"**Miscela: {w:.0%} modello + {1 - w:.0%} mercato**")
                st.dataframe(bet_table(bt["1x2"]["scommesse_miscela"]), hide_index=True, width='stretch')

            with st.expander("Quanto fidarsi del modello rispetto al mercato"):
                st.caption("Errore (log loss) delle previsioni che mescolano modello e quote di "
                           "Pinnacle con pesi diversi. Il peso con l'errore più basso dice quanto "
                           "il modello aggiunge informazione rispetto al mercato.")
                st.dataframe(pd.DataFrame(bt["1x2"]["miscela"]).rename(
                    columns={"peso_modello": "Peso del modello", "log_loss": "Log loss"}),
                    hide_index=True, width='stretch')
            with st.expander("Under/Over 2.5"):
                ou = bt["over_under_2_5"]
                if ou.get("log_loss"):
                    st.write(f"Log loss Under/Over 2.5: modello {ou['log_loss']['modello']:.4f}, "
                             f"Pinnacle chiusura {ou['log_loss']['pinnacle_chiusura']:.4f}")
                st.dataframe(bet_table(ou["scommesse_modello"]), hide_index=True, width='stretch')
            with st.expander("Per campionato"):
                st.dataframe(pd.DataFrame(bt["per_campionato_log_loss"]).T, width='stretch')

    # --- Calibrazione --------------------------------------------------------
    with sub_calibrazione:
        if bt is None:
            st.info("Disponibile dopo il primo backtest.")
        else:
            st.caption("Per ogni fascia di probabilità prevista dal nostro modello: quante volte "
                       "l'esito si è verificato davvero. Un modello ben calibrato ha le due linee vicine.")
            cal = pd.DataFrame(bt["1x2"]["calibrazione"])
            if not cal.empty:
                st.subheader("1X2")
                st.line_chart(cal.set_index("fascia")[["prevista", "reale"]])
                with st.expander("Tabella"):
                    st.dataframe(cal.rename(columns={"fascia": "Fascia", "n": "Esiti",
                                                     "prevista": "Prob. prevista",
                                                     "reale": "Frequenza reale"}),
                                 hide_index=True, width='stretch')
            with st.expander("Under/Over 2.5 e Goal/No Goal"):
                st.dataframe(pd.DataFrame(bt["over_under_2_5"]["calibrazione"]),
                             hide_index=True, width='stretch')
                st.markdown("**Goal/No Goal** (per questo mercato lo storico non ha quote)")
                st.dataframe(pd.DataFrame(bt["goal_no_goal"]["calibrazione"]), hide_index=True,
                             width='stretch')

    # --- Tutte le partite ---------------------------------------------------
    with sub_tutte:
        filtered_df = competition_filter(matches_df, key="comp_tutte")

        display_df = filtered_df.copy()
        # Doppia chance: somma delle probabilità del modello per due esiti insieme.
        # Nessun costo, nessuna nuova quota: sono solo i numeri che già abbiamo,
        # sommati in modo diverso.
        display_df["prob_1x"] = (display_df["prob_home"] + display_df["prob_draw"]) * 100
        display_df["prob_x2"] = (display_df["prob_draw"] + display_df["prob_away"]) * 100
        display_df["prob_12"] = (display_df["prob_home"] + display_df["prob_away"]) * 100
        for col in ["prob_home", "prob_draw", "prob_away", "prob_btts", "prob_over25"]:
            display_df[col] = display_df[col] * 100

        percent_cols = ["Prob. 1", "Prob. X", "Prob. 2", "Prob. 1X", "Prob. X2", "Prob. 12",
                        "Prob. Goal", "Prob. Over 2.5"]
        st.dataframe(
            display_df[["date", "league", "home", "away", "prob_home", "prob_draw", "prob_away",
                         "prob_1x", "prob_x2", "prob_12", "prob_btts", "prob_over25"]]
            .rename(columns={
                "date": "Data", "league": "Campionato", "home": "Casa", "away": "Trasferta",
                "prob_home": "Prob. 1", "prob_draw": "Prob. X", "prob_away": "Prob. 2",
                "prob_1x": "Prob. 1X", "prob_x2": "Prob. X2", "prob_12": "Prob. 12",
                "prob_btts": "Prob. Goal", "prob_over25": "Prob. Over 2.5",
            }),
            width='stretch', hide_index=True,
            column_config={col: st.column_config.NumberColumn(col, format="%.0f%%") for col in percent_cols},
        )
        st.caption(
            "1X = vittoria casa o pareggio · X2 = pareggio o vittoria trasferta · "
            "12 = vittoria di una delle due squadre (esclude il pareggio) · "
            "Goal = segnano entrambe. Sono le probabilità del nostro modello: il "
            "confronto con le quote (e quindi la convenienza) è nella scheda "
            "'Opportunità'."
        )
