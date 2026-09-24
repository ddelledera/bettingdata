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

h1, h2, h3, .leg-teams, .leg-odds-value {
    font-family: 'Oswald', sans-serif !important;
    letter-spacing: 0.2px;
}
h1 { color: #1B4332; font-weight: 700; }

/* Riga "tabellone" per ogni partita di una schedina */
.leg-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: linear-gradient(155deg, #1B4332 0%, #163B2B 100%);
    border-left: 5px solid var(--risk-color, #D4A017);
    border-radius: 8px;
    padding: 14px 20px;
    margin-bottom: 10px;
}
.leg-basso { --risk-color: #4CAF7D; }
.leg-medio { --risk-color: #D4A017; }
.leg-alto  { --risk-color: #D96C6C; }

.leg-match { flex: 1; }
.leg-teams { font-size: 1.08rem; font-weight: 600; color: #F7F9F6; }
.leg-date { font-size: 0.8rem; color: #8FBFA3; margin-top: 1px; }

.leg-pick {
    background: rgba(247, 249, 246, 0.12);
    color: #F7F9F6;
    font-weight: 600;
    font-size: 0.85rem;
    padding: 4px 12px;
    border-radius: 20px;
    margin: 0 16px;
    white-space: nowrap;
}

.leg-odds { text-align: right; min-width: 90px; }
.leg-odds-value { font-size: 1.35rem; font-weight: 700; color: #D4A017; }
.leg-odds-prob { font-size: 0.78rem; color: #8FBFA3; }

/* Schede "in evidenza" per le migliori opportunità, in cima alla pagina */
.spot-card {
    background: linear-gradient(155deg, #1B4332 0%, #163B2B 100%);
    border-radius: 10px;
    padding: 20px 22px;
    color: #F7F9F6;
    height: 100%;
}
.spot-league {
    font-size: 0.72rem;
    color: #8FBFA3;
    font-weight: 600;
}
.spot-teams {
    font-family: 'Oswald', sans-serif !important;
    font-size: 1.25rem;
    font-weight: 600;
    margin: 4px 0 14px 0;
    line-height: 1.25;
}
.spot-ev-value {
    font-family: 'Oswald', sans-serif !important;
    font-size: 2.4rem;
    font-weight: 700;
    color: #D4A017;
    line-height: 1;
}
.spot-ev-label {
    font-size: 0.7rem;
    color: #8FBFA3;
    margin-bottom: 12px;
}
.spot-details {
    display: flex;
    justify-content: space-between;
    font-size: 0.82rem;
    border-top: 1px solid rgba(247, 249, 246, 0.15);
    padding-top: 10px;
}
.spot-details b { color: #F7F9F6; }

/* Schede per lo storico delle schedine */
.slip-card {
    border-radius: 8px;
    padding: 14px 18px;
    margin-bottom: 10px;
    border-left: 5px solid var(--slip-color, #7A8A81);
    background: #FFFFFF;
    box-shadow: 0 1px 2px rgba(27, 67, 50, 0.08);
}
.slip-pending { --slip-color: #D4A017; }
.slip-won { --slip-color: #2D6A4F; }
.slip-lost { --slip-color: #A63A3A; }
.slip-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    font-family: 'Oswald', sans-serif;
    font-size: 1.05rem;
    font-weight: 600;
    color: #1A2420;
}
.slip-profit-won { color: #2D6A4F; font-weight: 700; }
.slip-profit-lost { color: #A63A3A; font-weight: 700; }
.slip-meta { font-size: 0.82rem; color: #6B7972; margin-top: 4px; }

/* Intestazione con banda colorata, invece del titolo semplice */
.app-header {
    background: linear-gradient(135deg, #1B4332 0%, #163B2B 100%);
    margin: -3.5rem -4rem 1.8rem -4rem;
    padding: 2.2rem 4rem 1.6rem 4rem;
}
.app-header h1 {
    color: #F7F9F6 !important;
    margin: 0 0 0.2rem 0 !important;
    font-size: 2.1rem !important;
}
.app-header p {
    color: #8FBFA3;
    font-size: 0.95rem;
    margin: 0;
}

/* Schede (tab) più marcate, come una vera navigazione da app */
[data-testid="stTabs"] [data-baseweb="tab-list"] {
    gap: 4px;
    border-bottom: 2px solid #E2E8E5;
}
[data-testid="stTabs"] button[data-baseweb="tab"] {
    font-family: 'Oswald', sans-serif;
    font-weight: 600;
    font-size: 0.95rem;
    padding: 10px 22px;
    color: #7A8A81;
}
[data-testid="stTabs"] button[aria-selected="true"] {
    color: #1B4332 !important;
}

/* Pulsanti principali, più decisi */
[data-testid="stButton"] button {
    border-radius: 6px;
    font-weight: 600;
}
[data-testid="stButton"] button[kind="primary"] {
    background-color: #1B4332;
    border-color: #1B4332;
}
[data-testid="stButton"] button[kind="primary"]:hover {
    background-color: #0F2B1E;
    border-color: #0F2B1E;
}

/* Pulsanti radio come "pillole" invece dei soliti pallini */
[data-testid="stRadio"] > div[role="radiogroup"] {
    gap: 8px;
}
[data-testid="stRadio"] label {
    background: #EEF3F0;
    padding: 7px 18px;
    border-radius: 20px;
    border: 1px solid #D5E0DA;
}
[data-testid="stRadio"] label[data-checked="true"] {
    background: #1B4332;
    border-color: #1B4332;
}
[data-testid="stRadio"] label[data-checked="true"] p {
    color: #F7F9F6 !important;
}
[data-testid="stRadio"] label > div:first-child {
    display: none;
}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="app-header">
    <h1>⚽ Le mie previsioni calcio</h1>
    <p>Partite in arrivo ordinate per convenienza, con quota migliore e puntata consigliata.</p>
</div>
""", unsafe_allow_html=True)


def get_connection():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def load_upcoming_with_predictions(conn):
    query = """
        SELECT m.id, m.date, m.league, h.name AS home, a.name AS away,
               p.prob_home, p.prob_draw, p.prob_away,
               p.prob_btts, p.prob_over15, p.prob_over25, p.prob_over35
        FROM matches m
        JOIN teams h ON m.home_team_id = h.id
        JOIN teams a ON m.away_team_id = a.id
        JOIN model_predictions p ON p.match_id = m.id
        WHERE m.date >= date('now') AND m.home_goals IS NULL
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
        SELECT selection, odds, bookmaker, snapshot_time FROM ultima_quota WHERE rn = 1
    """
    rows = conn.execute(query, (match_id,)).fetchall()
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
    query = """
        SELECT selection, odds FROM odds_snapshots
        WHERE match_id = ? AND bookmaker = ?
        ORDER BY snapshot_time DESC
    """
    rows = conn.execute(query, (match_id, bookmaker)).fetchall()
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
    query = """
        SELECT pl.id AS player_id, pl.name AS player, t.name AS team, m.id AS match_id,
               m.date, m.league,
               ht.name AS home_team, at.name AS away_team,
               pp.expected_goals, pp.prob_score_anytime,
               pp.expected_minutes, pp.starting_probability
        FROM player_predictions pp
        JOIN players pl ON pl.id = pp.player_id
        JOIN teams t ON t.id = pl.team_id
        JOIN matches m ON m.id = pp.match_id
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at ON at.id = m.away_team_id
        WHERE m.date >= date('now') AND m.home_goals IS NULL
        ORDER BY pp.prob_score_anytime DESC
    """
    return pd.read_sql_query(query, conn)


def load_scorer_odds(conn):
    """Ultima quota 'marcatore' di ogni bookmaker per ogni giocatore
    riconosciuto, per le partite in arrivo."""
    query = """
        WITH ultima AS (
            SELECT s.match_id, s.player_id, s.bookmaker, s.odds, s.snapshot_time,
                   ROW_NUMBER() OVER (PARTITION BY s.match_id, s.player_id, s.bookmaker
                                      ORDER BY s.snapshot_time DESC) AS rn
            FROM scorer_odds s JOIN matches m ON m.id = s.match_id
            WHERE s.player_id IS NOT NULL AND m.date >= date('now') AND m.home_goals IS NULL
        )
        SELECT match_id, player_id, bookmaker, odds, snapshot_time FROM ultima WHERE rn = 1
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


def render_scorer_card(row):
    """Disegna una scheda per la probabilità di un giocatore di segnare,
    nello stesso stile delle schede 'in evidenza' delle opportunità."""
    avversario = row["away_team"] if row["team"] == row["home_team"] else row["home_team"]
    minuti = f"{row['expected_minutes']:.0f}'" if pd.notna(row.get("expected_minutes")) else "—"
    titolare = f"{row['starting_probability']:.0%}" if pd.notna(row.get("starting_probability")) else "—"
    quota_html = ""
    if pd.notna(row.get("best_odds")):
        ev = expected_value(row["prob_score_anytime"], row["best_odds"])
        colore = "#5EB78A" if ev > 0 else "#E28F8F"
        quota_html = (f'<div class="spot-details" style="margin-top:6px; border-top:none; padding-top:0;">'
                      f'<span>Quota <b>{row["best_odds"]}</b> · {row["best_bookmaker"]} · '
                      f'{odds_age_label(row["best_time"])}</span>'
                      f'<span style="color:{colore}; font-weight:600;">EV {ev:+.1%}</span></div>')
    st.markdown(f"""
    <div class="spot-card">
        <div class="spot-league">{row['league'].upper()}</div>
        <div class="spot-teams">{row['player']}<br><span style="font-size:0.85rem; font-weight:400; color:#8FBFA3;">{row['team']} vs {avversario}</span></div>
        <div class="spot-ev-value">{row['prob_score_anytime']:.0%}</div>
        <div class="spot-ev-label">probabilità di segnare (nostro modello) — {row['date']}</div>
        <div class="spot-details">
            <span>Gol attesi <b>{row['expected_goals']:.2f}</b></span>
            <span>Minuti attesi <b>{minuti}</b></span>
            <span>Titolare <b>{titolare}</b></span>
        </div>
        {quota_html}
    </div>
    """, unsafe_allow_html=True)


def competition_filter(matches_df, key):
    """Mostra un selettore di competizioni (una, più di una, o tutte) e
    ritorna solo le partite di quelle scelte. Usata in ogni scheda."""
    available = sorted(matches_df["league"].unique())
    selected = st.multiselect("Competizione:", options=available, default=available, key=key)
    if not selected:
        return matches_df.iloc[0:0]
    return matches_df[matches_df["league"].isin(selected)]


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
    st.markdown(f"""
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
    """, unsafe_allow_html=True)


RISK_COLORS = {"leg-basso": "#5EB78A", "leg-medio": "#D4A017", "leg-alto": "#E28F8F"}
# Nota: è una FASCIA DI QUOTA (≤1.80 / ≤3.00 / oltre), non una misura di
# rischio vera: il modello non stima ancora quanto è affidabile ogni previsione.
RISK_TEXT = {"leg-basso": "quota bassa", "leg-medio": "quota media", "leg-alto": "quota alta"}


def risk_badge_html(odds):
    """Piccola etichetta colorata per il livello di rischio, da inserire in una scheda."""
    cls = risk_css_class(odds)
    return f'<span style="color:{RISK_COLORS[cls]}; font-weight:600;">● {RISK_TEXT[cls]}</span>'


EV_SOSPETTO = 0.25  # oltre +25% è molto più probabile un errore del modello che un regalo


def render_spotlight_card(opp):
    """Disegna una scheda per un'opportunità di valore, con l'EV come numero
    grande e protagonista, e tutti i dettagli utili sotto."""
    avviso = ""
    ev = float(str(opp["Valore atteso (EV)"]).replace("%", "").replace("+", "")) / 100
    pm = opp.get("Prob. modello")
    modello_txt = f" · nostro modello {pm:.0f}%" if pm is not None and pm == pm else ""
    if ev > EV_SOSPETTO:
        avviso = ('<div style="font-size:0.78rem; color:#E28F8F; margin-top:4px;">⚠️ Scarto '
                  'molto grande dal mercato: più probabile un limite del modello (o una '
                  'quota non aggiornata) che un vero affare. Verifica prima di giocare.</div>')
    st.markdown(f"""
    <div class="spot-card">
        <div class="spot-league">{opp['Campionato'].upper()}</div>
        <div class="spot-teams">{opp['Partita']}</div>
        <div class="spot-ev-value">{opp['Valore atteso (EV)']}</div>
        <div class="spot-ev-label">valore atteso — {opp['Esito']} · prob. giusta (Pinnacle) {opp['Nostra probabilità']:.0f}%{modello_txt}</div>
        {avviso}
        <div class="spot-details">
            <span>Quota <b>{opp['Quota migliore']}</b></span>
            <span>{opp['Bookmaker']} · {opp.get('Aggiornata', '')}</span>
        </div>
        <div class="spot-details" style="margin-top:6px; border-top:none; padding-top:0;">
            {risk_badge_html(opp['Quota migliore'])}
            <span>Kelly teorico <b>{opp['Puntata consigliata']}</b></span>
        </div>
    </div>
    """, unsafe_allow_html=True)


def render_slip_card(slip):
    """Disegna una scheda per una voce dello storico: in attesa, vinta o persa."""
    legs_desc = ", ".join(l["match_label"] for l in slip["legs"])
    if slip["status"] == "pending":
        st.markdown(f"""
        <div class="slip-card slip-pending">
            <div class="slip-header"><span>🕒 {slip['stake']}€ @ {slip['combined_odds']} · {slip['bookmaker']}</span></div>
            <div class="slip-meta">{legs_desc}</div>
        </div>
        """, unsafe_allow_html=True)
    else:
        esito = slip["result_summary"]
        won = slip["status"] == "won"
        icona = "✅" if won else "❌"
        profit_class = "slip-profit-won" if won else "slip-profit-lost"
        st.markdown(f"""
        <div class="slip-card {'slip-won' if won else 'slip-lost'}">
            <div class="slip-header">
                <span>{icona} {slip['stake']}€ @ {slip['combined_odds']} · {slip['bookmaker']}</span>
                <span class="{profit_class}">€{esito['profit']:+.2f}</span>
            </div>
            <div class="slip-meta">{legs_desc}</div>
        </div>
        """, unsafe_allow_html=True)
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
    rows = conn.execute("""
        SELECT selection, odds FROM (
            SELECT selection, odds, ROW_NUMBER() OVER (
                PARTITION BY selection ORDER BY snapshot_time DESC) AS rn
            FROM reference_odds WHERE match_id = ? AND bookmaker = 'Pinnacle')
        WHERE rn = 1""", (match_id,)).fetchall()
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
                "Data": row["date"],
                "Esito": long_label(sel),
                "Nostra probabilità": round(p * 100, 1),
                "Prob. modello": round(model[sel] * 100, 1) if sel in model else None,
                "Quota migliore": odds,
                "Bookmaker": best_bookmaker[sel],
                "Aggiornata": odds_age_label(best_time[sel]),
                "Valore atteso (EV)": f"{ev:+.1%}",
                "Puntata consigliata": f"{kelly_fraction(p, odds) * 100:.1f}%",
                "Rischio": risk_label(odds),
                "_ev_sort": ev,
            })
    return all_opportunities


conn = get_connection()

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

tab_opportunita, tab_schedina, tab_storico, tab_marcatori, tab_tutte, tab_performance = st.tabs(
    ["🎯 Opportunità di valore", "🎟️ Schedina", "📊 Storico schedine", "⚽ Marcatori",
     "📋 Tutte le partite", "📈 Performance del modello"]
)

# ---------------------------------------------------------------------------
# SCHEDA 1: Opportunità di valore
# ---------------------------------------------------------------------------
with tab_opportunita:
    filtered_opp = competition_filter(matches_df, key="comp_opportunita")
    mercati_verificabili = [m for m in MARKET_NAMES if m != "Marcatori"]
    markets_opp = st.multiselect("Mercato:", mercati_verificabili, default=mercati_verificabili,
                                 key="mercati_opportunita")
    st.caption(
        "Come si trova il valore: quando un bookmaker italiano paga più del prezzo "
        "'giusto' di Pinnacle (il bookmaker più efficiente, margine tolto). Nel backtest "
        "questa strategia ha avuto quote migliori della chiusura (CLV positivo), mentre "
        "le previsioni del nostro modello, da sole, perdevano. Solo quote fino a "
        f"{MAX_ODDS:g}: oltre, anche il 'valore' apparente perdeva. I marcatori non sono "
        "qui perché Pinnacle non li quota e il nostro modello non è verificato.")

    min_ev = st.slider("Mostra solo scommesse con valore atteso di almeno:",
                        min_value=0, max_value=10, value=1, format="%d%%") / 100

    all_opportunities = compute_opportunities(conn, filtered_opp, min_ev, markets_opp)

    if not all_opportunities:
        st.info("Nessuna scommessa di valore trovata al momento con la soglia scelta. "
                 "Prova ad abbassare la soglia qui sopra, oppure torna più tardi.")
    else:
        opp_df = pd.DataFrame(all_opportunities).sort_values("_ev_sort", ascending=False)
        opp_df = opp_df.drop(columns=["_ev_sort"])

        st.subheader(f"{len(opp_df)} opportunità trovate, ordinate per convenienza")

        records = opp_df.to_dict("records")
        for i in range(0, len(records), 3):
            row_chunk = records[i:i + 3]
            cols = st.columns(3)
            for col, opp in zip(cols, row_chunk):
                with col:
                    render_spotlight_card(opp)
            st.write("")

        st.caption(
            "Il 'valore atteso' è quanto ti aspetti di guadagnare in media, su tante "
            "ripetizioni, puntando su questa scommessa — non è una garanzia sulla "
            "singola partita. Il 'Kelly teorico' è la frazione del capitale che il "
            "criterio di Kelly (frazionato, prudente) suggerirebbe SE le probabilità "
            "del modello fossero esatte: finché il modello non è stato verificato sui "
            "risultati passati, prendila come indicazione, non come consiglio. "
            "Le quote si aggiornano una volta al giorno: prima di giocare controlla "
            "che siano ancora quelle sul sito del bookmaker."
        )

# ---------------------------------------------------------------------------
# SCHEDA 2: Schedina (a mano, o trovata automaticamente) da un unico bookmaker
# ---------------------------------------------------------------------------
with tab_schedina:
    st.caption(
        "Una schedina reale va giocata tutta presso lo stesso bookmaker (non puoi "
        "combinare una quota di un sito con una di un altro)."
    )

    bookmakers = list_bookmakers(conn)
    if not bookmakers:
        st.info("Non ci sono ancora quote salvate per costruire una schedina.")
    else:
        filtered_sched = competition_filter(matches_df, key="comp_schedina")

        col_a, col_b = st.columns(2)
        with col_a:
            selected_bookmaker = st.selectbox("Bookmaker:", bookmakers,
                                               format_func=bookmaker_display_label, key="sched_bookmaker")
        with col_b:
            stake = st.number_input("Puntata (€):", min_value=1.0, value=10.0, step=1.0, key="sched_stake")
        markets_sched = st.multiselect(
            "Mercati da usare:", MARKET_NAMES,
            default=[m for m in MARKET_NAMES if m != "Marcatori"], key="mercati_schedina",
            help="Al massimo una selezione per partita: esiti della stessa partita "
                 "(es. 1 e Over 2.5) sono legati tra loro e non si possono combinare "
                 "come se fossero indipendenti.")

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

        mode = st.radio(
            "Come vuoi costruire la schedina?",
            ["🖐️ Scelgo io le partite", "🔍 Trova la combinazione migliore per me",
             "⚖️ Miglior equilibrio probabilità/valore"],
            key="schedina_mode", horizontal=True,
        )
        st.divider()

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
                    options=list(candidate_legs.keys()),
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
                num_matches_eq = st.slider("Numero di partite:", min_value=1, max_value=5,
                                           value=3, key="eq_num_matches")
            with col_f:
                max_risk_eq = st.select_slider(
                    "Quota massima per singola selezione:",
                    options=["🟢 Solo basse (≤1.80)", "🟡 Fino a medie (≤3.00)", "🔴 Qualsiasi"],
                    value="🔴 Qualsiasi", key="eq_max_risk",
                )
            risk_order_eq = {"🟢 Quota bassa": 0, "🟡 Quota media": 1, "🔴 Quota alta": 2}
            max_level_eq = {"🟢 Solo basse (≤1.80)": 0, "🟡 Fino a medie (≤3.00)": 1,
                            "🔴 Qualsiasi": 2}[max_risk_eq]

            if st.button("⚖️ Calcola le combinazioni", type="primary"):
                legs_by_match = {}
                for _, row in filtered_sched.iterrows():
                    bm_odds = odds_by_bookmaker_for_match(conn, row["id"], selected_bookmaker) or {}
                    # solo selezioni con valore (EV >= 0), come nelle altre modalità
                    candidates = (entro_quota_max(find_value_bets(sched_probs(row), bm_odds, min_ev=0.0))
                                  + sched_scorer_legs(row))
                    candidates = [c for c in candidates
                                  if risk_order_eq[risk_label(c["odds"])] <= max_level_eq]
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
                target_roi_pct = st.slider("Ritorno desiderato:", min_value=20, max_value=500,
                                            value=100, step=10, format="+%d%%")
            with col_d:
                num_matches = st.slider("Numero di partite:", min_value=1, max_value=5, value=3)

            max_risk = st.select_slider(
                "Quota massima per singola selezione:",
                options=["🟢 Solo basse (≤1.80)", "🟡 Fino a medie (≤3.00)", "🔴 Qualsiasi"],
                value="🔴 Qualsiasi",
            )
            risk_order = {"🟢 Quota bassa": 0, "🟡 Quota media": 1, "🔴 Quota alta": 2}
            max_risk_level = {"🟢 Solo basse (≤1.80)": 0, "🟡 Fino a medie (≤3.00)": 1,
                              "🔴 Qualsiasi": 2}[max_risk]
            st.caption(
                "Limitare alle quote basse riduce le partite disponibili tra cui scegliere: "
                "con poche selezioni a quota bassa, potrebbe non essere possibile raggiungere il "
                "ritorno desiderato — in quel caso te lo segnalo."
            )

            if st.button("🔍 Trova la combinazione migliore", type="primary"):
                legs_by_match = {}
                for _, row in filtered_sched.iterrows():
                    bm_odds = odds_by_bookmaker_for_match(conn, row["id"], selected_bookmaker) or {}
                    # solo selezioni con valore (EV >= 0): la schedina deve essere
                    # conveniente, non solo "probabile"
                    candidates = (entro_quota_max(find_value_bets(sched_probs(row), bm_odds, min_ev=0.0))
                                  + sched_scorer_legs(row))
                    candidates = [c for c in candidates
                                  if risk_order[risk_label(c["odds"])] <= max_risk_level]
                    if candidates:
                        for c in candidates:
                            c["match_id"] = row["id"]
                            c["match_label"] = f"{row['home']} vs {row['away']}"
                            c["match_date"] = row["date"]
                        legs_by_match[row["id"]] = candidates

                result = find_best_combination(legs_by_match, num_matches, target_roi_pct / 100)

                if result is None:
                    st.session_state.pop("auto_combo", None)
                    st.warning(f"Non ci sono abbastanza partite disponibili su {selected_bookmaker} "
                               f"(con il rischio scelto) per formare una combinazione di "
                               f"{num_matches} partite. Prova ad allargare il rischio massimo, "
                               f"o riduci il numero di partite.")
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
                    st.info(f"Non ho trovato una combinazione che raggiunga +{target_roi_pct}% "
                            f"— questa è quella con il ritorno più alto possibile disponibile ora.")
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
        "Probabilità che ogni giocatore segni almeno un gol, secondo il nostro modello "
        "(distribuiamo i gol attesi della squadra tra i giocatori in base al loro "
        "rendimento stagionale). Non c'è ancora un confronto con le quote reali dei "
        "bookmaker su questo mercato specifico, quindi questa scheda ti aiuta a farti "
        "un'idea, non calcola un valore atteso come le altre schede."
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
        df = competition_filter(df, key="comp_marcatori")

        f1, f2 = st.columns(2)
        with f1:
            partite = ["Tutte le partite"] + sorted(df["partita"].unique(),
                                                    key=lambda p: (p[-11:], p))
            partita = st.selectbox("Partita:", partite, key="marc_partita")
        if partita != "Tutte le partite":
            df = df[df["partita"] == partita]
        with f2:
            squadre = st.multiselect("Squadra (vuoto = tutte):", sorted(df["team"].unique()),
                                     key="marc_squadre")
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

        f3, f4, f5 = st.columns([2, 1, 1])
        with f3:
            min_prob = st.slider("Probabilità di segnare almeno:",
                                 min_value=0, max_value=80, value=20, format="%d%%",
                                 key="marc_prob") / 100
        with f4:
            ordini = ["Probabilità", "Gol attesi"] + (["Valore atteso"] if libri else [])
            ordine = st.radio("Ordina per:", ordini, key="marc_ordine")
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
                         + (f" — mostrati i primi {MAX_SCHEDE}" if len(df) > MAX_SCHEDE else ""))
            records = df.head(MAX_SCHEDE).to_dict("records")
            for i in range(0, len(records), 3):
                row_chunk = records[i:i + 3]
                cols = st.columns(3)
                for col, row in zip(cols, row_chunk):
                    with col:
                        render_scorer_card(row)
                st.write("")

# ---------------------------------------------------------------------------
# SCHEDA 5: Tutte le partite in arrivo
# ---------------------------------------------------------------------------
with tab_tutte:
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
        "'Opportunità di valore'."
    )


# ---------------------------------------------------------------------------
# SCHEDA 6: Performance del modello (risultati del backtest settimanale)
# ---------------------------------------------------------------------------
with tab_performance:
    # --- Prova dal vivo: scommesse virtuali -------------------------------
    st.subheader("Prova dal vivo: scommesse virtuali")
    st.caption(
        "Ogni giorno registriamo, senza giocare soldi, le quote dei bookmaker italiani "
        "che superano il prezzo giusto di Pinnacle (quote fino a 5). "
        "CLV = quanto la quota presa batteva l'ultimo prezzo di Pinnacle prima della "
        "partita: è l'indicatore che si stabilizza prima (bastano poche centinaia di "
        "scommesse), mentre il rendimento sui risultati richiede molto più tempo.")
    try:
        vb = pd.read_sql_query("""
            SELECT v.*, m.home_goals, m.away_goals, m.date, m.league,
                   h.name AS home, a.name AS away
            FROM virtual_bets v JOIN matches m ON m.id = v.match_id
            JOIN teams h ON h.id = m.home_team_id JOIN teams a ON a.id = m.away_team_id
        """, conn)
    except Exception:
        vb = pd.DataFrame()
    if vb.empty:
        st.info("Nessuna scommessa virtuale ancora registrata: la raccolta parte dal "
                "prossimo aggiornamento delle quote.")
    else:
        vb["clv"] = vb["odds"] * vb["close_fair_prob"] - 1
        chiuse = vb[vb["home_goals"].notna()].copy()
        if not chiuse.empty:
            chiuse["vinta"] = [is_winner(s_, int(h), int(a)) for s_, h, a in
                               zip(chiuse["selection"], chiuse["home_goals"], chiuse["away_goals"])]
            chiuse["profitto"] = [o - 1 if w else -1 for o, w in zip(chiuse["odds"], chiuse["vinta"])]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Scommesse virtuali", len(vb))
        m2.metric("Concluse", len(chiuse))
        m3.metric("CLV medio", f"{vb['clv'].mean():+.1%}",
                  help="Positivo = le quote trovate battevano il prezzo finale di Pinnacle.")
        m4.metric("Rendimento (concluse)",
                  f"{chiuse['profitto'].mean():+.1%}" if not chiuse.empty else "—",
                  help="Profitto medio per unità puntata. Con poche scommesse è molto "
                       "influenzato dalla fortuna: guarda soprattutto il CLV.")
        per_book = vb.groupby("bookmaker").agg(scommesse=("id", "count"),
                                               clv_medio=("clv", "mean")).reset_index()
        per_book["clv_medio"] = (per_book["clv_medio"] * 100).round(1)
        st.dataframe(per_book.rename(columns={"bookmaker": "Bookmaker", "scommesse": "Scommesse",
                                              "clv_medio": "CLV medio %"}),
                     hide_index=True, width='stretch')
        with st.expander("Ultime scommesse virtuali"):
            ultime = vb.sort_values("found_at", ascending=False).head(50)
            st.dataframe(pd.DataFrame({
                "Data": ultime["date"], "Partita": ultime["home"] + " - " + ultime["away"],
                "Esito": [long_label(x) for x in ultime["selection"]],
                "Bookmaker": ultime["bookmaker"], "Quota": ultime["odds"],
                "Vantaggio %": (ultime["edge"] * 100).round(1),
                "CLV %": (ultime["clv"] * 100).round(1),
            }), hide_index=True, width='stretch')
    st.divider()

    try:
        with open("backtest_report.json") as f:
            bt = json.load(f)
    except (FileNotFoundError, ValueError):
        bt = None

    if bt is None:
        st.info("Il backtest non è ancora stato eseguito. Gira in automatico una volta a "
                "settimana (workflow 'Backtest'), oppure puoi avviarlo a mano da GitHub Actions.")
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
        if scarto > 0:
            st.warning(
                "Il modello, da solo, prevede peggio del mercato. È normale per un modello "
                "basato solo sui risultati: significa che gran parte del 'valore' che trova "
                "è in realtà errore del modello, non un vantaggio reale. Guarda sotto la "
                "miscela modello/mercato e il ROI per fascia di valore atteso.")

        st.subheader("Calibrazione (1X2)")
        st.caption("Per ogni fascia di probabilità prevista: quante volte l'esito si è "
                   "verificato davvero. Un modello ben calibrato ha le due colonne simili.")
        cal = pd.DataFrame(bt["1x2"]["calibrazione"])
        if not cal.empty:
            st.dataframe(cal.rename(columns={"fascia": "Fascia", "n": "Esiti", "prevista":
                                             "Prob. prevista", "reale": "Frequenza reale"}),
                         hide_index=True, width='stretch')
            st.line_chart(cal.set_index("fascia")[["prevista", "reale"]])

        st.subheader("Scommettere dove il modello vede valore")
        st.caption("1 unità su ogni esito con valore atteso positivo, alle quote medie dei "
                   "bookmaker qualche giorno prima della partita. CLV = quanto la quota "
                   "presa batteva la quota finale di Pinnacle senza margine: se è positivo "
                   "in media, il vantaggio è probabilmente reale e non fortuna.")
        def bet_table(rows):
            df = pd.DataFrame(rows)
            if df.empty:
                return df
            for c in ("vinte", "roi", "clv_medio"):
                df[c] = (df[c] * 100).round(1)
            return df.rename(columns={"fascia_ev": "Fascia EV", "scommesse": "Scommesse",
                                      "vinte": "Vinte %", "roi": "ROI %", "clv_medio": "CLV medio %"})
        st.markdown("**Solo modello**")
        st.dataframe(bet_table(bt["1x2"]["scommesse_modello"]), hide_index=True, width='stretch')
        if bt["1x2"].get("scommesse_miscela"):
            w = bt["1x2"]["peso_modello_migliore"]
            st.markdown(f"**Miscela: {w:.0%} modello + {1 - w:.0%} mercato (Pinnacle prima della partita)**")
            st.dataframe(bet_table(bt["1x2"]["scommesse_miscela"]), hide_index=True, width='stretch')

        st.subheader("Quanto fidarsi del modello rispetto al mercato")
        st.caption("Errore (log loss) delle previsioni che mescolano modello e quote di "
                   "Pinnacle con pesi diversi. Il peso con l'errore più basso dice quanto "
                   "il modello aggiunge informazione rispetto al mercato.")
        st.dataframe(pd.DataFrame(bt["1x2"]["miscela"]).rename(
            columns={"peso_modello": "Peso del modello", "log_loss": "Log loss"}),
            hide_index=True, width='stretch')

        if bt.get("contro_pinnacle"):
            st.subheader("Strategia senza modello: bookmaker contro Pinnacle")
            st.caption("Si gioca quando la quota di un bookmaker supera la quota 'giusta' di "
                       "Pinnacle (senza margine) nello stesso momento, almeno della soglia "
                       "indicata. Nessuna previsione nostra: Pinnacle fa da stima della "
                       "probabilità vera. 'Quota massima' = la migliore tra tutti i "
                       "bookmaker del file (include anche siti non disponibili in Italia).")
            nomi = {"1x2_bet365": "1X2 · bet365", "1x2_quota_massima": "1X2 · quota massima",
                    "ou25_bet365": "Under/Over 2.5 · bet365",
                    "ou25_quota_massima": "Under/Over 2.5 · quota massima",
                    "1x2_quota_massima_potenza": "1X2 · quota massima (margine 'potenza')",
                    "ou25_quota_massima_potenza": "Under/Over 2.5 · quota massima (margine 'potenza')"}
            righe = []
            for chiave, rows in bt["contro_pinnacle"].items():
                for r in rows:
                    righe.append({"Strategia": nomi.get(chiave, chiave),
                                  "Soglia": r["soglia_vantaggio"], "Scommesse": r["scommesse"],
                                  "ROI %": round(r["roi"] * 100, 1),
                                  "CLV medio %": round(r["clv_medio"] * 100, 1)
                                  if r["clv_medio"] is not None else None})
            st.dataframe(pd.DataFrame(righe), hide_index=True, width='stretch')

        with st.expander("Under/Over 2.5 e Goal/No Goal"):
            ou = bt["over_under_2_5"]
            if ou.get("log_loss"):
                st.write(f"Log loss Under/Over 2.5 — modello {ou['log_loss']['modello']:.4f}, "
                         f"Pinnacle chiusura {ou['log_loss']['pinnacle_chiusura']:.4f}")
            st.dataframe(pd.DataFrame(ou["calibrazione"]), hide_index=True, width='stretch')
            st.dataframe(bet_table(ou["scommesse_modello"]), hide_index=True, width='stretch')
            st.markdown("**Goal/No Goal — calibrazione** (per questo mercato lo storico non ha quote)")
            st.dataframe(pd.DataFrame(bt["goal_no_goal"]["calibrazione"]), hide_index=True,
                         width='stretch')
        with st.expander("Per campionato"):
            st.dataframe(pd.DataFrame(bt["per_campionato_log_loss"]).T, width='stretch')
