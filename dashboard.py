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
from value_calculator import remove_bookmaker_margin, find_value_bets, combine_parlay, find_best_combination
from github_storage import read_json_file, write_json_file
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
               p.prob_home, p.prob_draw, p.prob_away
        FROM matches m
        JOIN teams h ON m.home_team_id = h.id
        JOIN teams a ON m.away_team_id = a.id
        JOIN model_predictions p ON p.match_id = m.id
        WHERE m.date >= date('now')
        ORDER BY m.date
    """
    return pd.read_sql_query(query, conn)


def best_odds_for_match(conn, match_id):
    """Per ogni esito (1/X/2), trova la quota più alta tra i bookmaker,
    usando SOLO l'ultima quota registrata per ciascun bookmaker (non il
    massimo tra tutti gli snapshot storici mai salvati, che potrebbe non
    essere più la quota realmente disponibile oggi).

    Si usano PRIMA i soli bookmaker italiani (ADM): sono le quote che puoi
    davvero giocare. Solo se per quella partita non ci sono quote italiane
    complete (es. alcune partite di coppa europea) si ripiega su tutti."""
    query = """
        WITH ultima_quota AS (
            SELECT bookmaker, selection, odds,
                   ROW_NUMBER() OVER (
                       PARTITION BY bookmaker, selection
                       ORDER BY snapshot_time DESC
                   ) AS rn
            FROM odds_snapshots
            WHERE match_id = ? {filtro}
        )
        SELECT selection, MAX(odds) as best_odds, bookmaker
        FROM ultima_quota
        WHERE rn = 1
        GROUP BY selection
    """
    italiani = sorted(BOOKMAKER_ITALIA)
    segnaposti = ",".join("?" * len(italiani))
    rows = conn.execute(query.format(filtro=f"AND bookmaker IN ({segnaposti})"),
                        (match_id, *italiani)).fetchall()
    if len(rows) < 3:
        rows = conn.execute(query.format(filtro=""), (match_id,)).fetchall()
    if len(rows) < 3:
        return None
    return {r[0]: r[1] for r in rows}, {r[0]: r[2] for r in rows}


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
            return f"🇮🇹 {bookmaker} (ADM) — stesse quote: {stesse_quote}"
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
    return odds if len(odds) >= 3 else None


def risk_label(odds):
    """Etichetta semplice del livello di rischio in base alla quota."""
    if odds <= 1.8:
        return "🟢 Rischio basso"
    elif odds <= 3.0:
        return "🟡 Rischio medio"
    return "🔴 Rischio alto"


# Bookmaker con licenza ADM (autorizzati in Italia) di cui abbiamo le quote.
# Goldbet, Eurobet, bet365 e Sisal arrivano da OddsPapi (fetch_odds.py),
# fonte che distingue esplicitamente le versioni italiane (.it) dei siti.
# Unibet e Codere arrivano ancora da The Odds API (coppe europee); per
# Unibet la fonte non garantisce che sia l'entità italiana.
BOOKMAKER_ITALIA = {"Goldbet", "Eurobet", "bet365", "Sisal", "Unibet", "Codere"}

# Marchi diversi che usano le stesse identiche quote (stessa piattaforma),
# secondo OddsPapi: giocare su uno o sull'altro è equivalente.
BOOKMAKER_CLONI = {
    "Goldbet": "Lottomatica, Planetwin365, BetFlag",
    "Sisal": "Snai, PokerStars",
}


def load_upcoming_player_predictions(conn):
    """Previsioni marcatori per le partite in arrivo, con nome squadra e avversario."""
    query = """
        SELECT pl.name AS player, t.name AS team, m.id AS match_id, m.date, m.league,
               ht.name AS home_team, at.name AS away_team,
               pp.expected_goals, pp.prob_score_anytime,
               pp.expected_minutes, pp.starting_probability
        FROM player_predictions pp
        JOIN players pl ON pl.id = pp.player_id
        JOIN teams t ON t.id = pl.team_id
        JOIN matches m ON m.id = pp.match_id
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at ON at.id = m.away_team_id
        WHERE m.date >= date('now')
        ORDER BY pp.prob_score_anytime DESC
    """
    return pd.read_sql_query(query, conn)


def render_scorer_card(row):
    """Disegna una scheda per la probabilità di un giocatore di segnare,
    nello stesso stile delle schede 'in evidenza' delle opportunità."""
    avversario = row["away_team"] if row["team"] == row["home_team"] else row["home_team"]
    minuti = f"{row['expected_minutes']:.0f}'" if pd.notna(row.get("expected_minutes")) else "—"
    titolare = f"{row['starting_probability']:.0%}" if pd.notna(row.get("starting_probability")) else "—"
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
    esito_label = {"Home": "1 · casa", "Draw": "X · pareggio", "Away": "2 · trasferta"}[leg["selection"]]
    st.markdown(f"""
    <div class="leg-row {risk_css_class(leg['odds'])}">
        <div class="leg-match">
            <div class="leg-teams">{leg['match_label']}</div>
            <div class="leg-date">{leg['match_date']}</div>
        </div>
        <div class="leg-pick">{esito_label}</div>
        <div class="leg-odds">
            <div class="leg-odds-value">{leg['odds']}</div>
            <div class="leg-odds-prob">prob. modello {leg['model_probability']:.0%}</div>
            <div style="font-size:0.72rem; margin-top:2px;">{risk_badge_html(leg['odds'])}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)


RISK_COLORS = {"leg-basso": "#5EB78A", "leg-medio": "#D4A017", "leg-alto": "#E28F8F"}
RISK_TEXT = {"leg-basso": "rischio basso", "leg-medio": "rischio medio", "leg-alto": "rischio alto"}


def risk_badge_html(odds):
    """Piccola etichetta colorata per il livello di rischio, da inserire in una scheda."""
    cls = risk_css_class(odds)
    return f'<span style="color:{RISK_COLORS[cls]}; font-weight:600;">● {RISK_TEXT[cls]}</span>'


def render_spotlight_card(opp):
    """Disegna una scheda per un'opportunità di valore, con l'EV come numero
    grande e protagonista, e tutti i dettagli utili sotto."""
    st.markdown(f"""
    <div class="spot-card">
        <div class="spot-league">{opp['Campionato'].upper()}</div>
        <div class="spot-teams">{opp['Partita']}</div>
        <div class="spot-ev-value">{opp['Valore atteso (EV)']}</div>
        <div class="spot-ev-label">valore atteso — {opp['Esito']} · prob. modello {opp['Nostra probabilità']:.0f}%</div>
        <div class="spot-details">
            <span>Quota <b>{opp['Quota migliore']}</b></span>
            <span>{opp['Bookmaker']}</span>
        </div>
        <div class="spot-details" style="margin-top:6px; border-top:none; padding-top:0;">
            {risk_badge_html(opp['Quota migliore'])}
            <span>Punta <b>{opp['Puntata consigliata']}</b></span>
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


def compute_opportunities(conn, matches_df, min_ev):
    """Calcola tutte le opportunità di valore (quota migliore tra tutti i
    bookmaker), usata dalla scheda principale."""
    all_opportunities = []
    for _, row in matches_df.iterrows():
        odds_info = best_odds_for_match(conn, row["id"])
        if odds_info is None:
            continue
        best_odds, best_bookmaker = odds_info

        model_probs = {"Home": row["prob_home"], "Draw": row["prob_draw"], "Away": row["prob_away"]}
        value_bets = find_value_bets(model_probs, best_odds, min_ev=min_ev)

        for vb in value_bets:
            all_opportunities.append({
                "Partita": f"{row['home']} vs {row['away']}",
                "Campionato": row["league"],
                "Data": row["date"],
                "Esito": {"Home": "1 (casa)", "Draw": "X (pareggio)", "Away": "2 (trasferta)"}[vb["selection"]],
                "Nostra probabilità": round(vb["model_probability"] * 100, 1),
                "Quota migliore": vb["odds"],
                "Bookmaker": best_bookmaker[vb["selection"]],
                "Valore atteso (EV)": f"{vb['ev']:+.1%}",
                "Puntata consigliata": f"{vb['kelly_stake_pct']:.1f}% del capitale",
                "Rischio": risk_label(vb["odds"]),
                "_ev_sort": vb["ev"],
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

tab_opportunita, tab_schedina, tab_storico, tab_marcatori, tab_tutte = st.tabs(
    ["🎯 Opportunità di valore", "🎟️ Schedina", "📊 Storico schedine", "⚽ Marcatori", "📋 Tutte le partite"]
)

# ---------------------------------------------------------------------------
# SCHEDA 1: Opportunità di valore
# ---------------------------------------------------------------------------
with tab_opportunita:
    filtered_opp = competition_filter(matches_df, key="comp_opportunita")

    min_ev = st.slider("Mostra solo scommesse con valore atteso di almeno:",
                        min_value=0, max_value=20, value=3, format="%d%%") / 100

    all_opportunities = compute_opportunities(conn, filtered_opp, min_ev)

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
            "singola partita. La puntata consigliata è calcolata in modo prudente "
            "(Kelly frazionato): più alta è, più il modello è convinto del valore."
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

        mode = st.radio(
            "Come vuoi costruire la schedina?",
            ["🖐️ Scelgo io le partite", "🔍 Trova la combinazione migliore per me"],
            key="schedina_mode", horizontal=True,
        )
        st.divider()

        combo = None
        target_roi_pct = None

        if mode.startswith("🖐️"):
            # --- Modalità manuale: scegli tu quali partite mettere in schedina ---
            candidate_legs = {}
            for _, row in filtered_sched.iterrows():
                bm_odds = odds_by_bookmaker_for_match(conn, row["id"], selected_bookmaker)
                if bm_odds is None:
                    continue
                model_probs = {"Home": row["prob_home"], "Draw": row["prob_draw"], "Away": row["prob_away"]}
                for vb in find_value_bets(model_probs, bm_odds, min_ev=0.0):
                    esito_label = {"Home": "1 (casa)", "Draw": "X (pareggio)", "Away": "2 (trasferta)"}[vb["selection"]]
                    label = (f"{row['home']} vs {row['away']} — {esito_label} @ {vb['odds']} "
                             f"(nostra prob. {vb['model_probability']:.0%}, EV {vb['ev']:+.1%})")
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
                    combo = [candidate_legs[c] for c in chosen]
                else:
                    st.caption("Seleziona una o più partite qui sopra per vedere la schedina combinata.")

        else:
            # --- Modalità automatica: dato un obiettivo, trovo io la combinazione ---
            col_c, col_d = st.columns(2)
            with col_c:
                target_roi_pct = st.slider("Ritorno desiderato:", min_value=20, max_value=500,
                                            value=100, step=10, format="+%d%%")
            with col_d:
                num_matches = st.slider("Numero di partite:", min_value=1, max_value=5, value=3)

            max_risk = st.select_slider(
                "Rischio massimo per singola selezione:",
                options=["🟢 Solo basso", "🟡 Basso o medio", "🔴 Qualsiasi"],
                value="🔴 Qualsiasi",
            )
            risk_order = {"🟢 Rischio basso": 0, "🟡 Rischio medio": 1, "🔴 Rischio alto": 2}
            max_risk_level = {"🟢 Solo basso": 0, "🟡 Basso o medio": 1, "🔴 Qualsiasi": 2}[max_risk]
            st.caption(
                "Limitare al rischio basso riduce le partite disponibili tra cui scegliere: "
                "con poche selezioni 'sicure', potrebbe non essere possibile raggiungere il "
                "ritorno desiderato — in quel caso te lo segnalo."
            )

            if st.button("🔍 Trova la combinazione migliore", type="primary"):
                legs_by_match = {}
                for _, row in filtered_sched.iterrows():
                    bm_odds = odds_by_bookmaker_for_match(conn, row["id"], selected_bookmaker)
                    if bm_odds is None:
                        continue
                    model_probs = {"Home": row["prob_home"], "Draw": row["prob_draw"], "Away": row["prob_away"]}
                    candidates = find_value_bets(model_probs, bm_odds, min_ev=-1)
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
            c2.metric("Ritorno stimato", f"€{projected_return:.2f}")
            c3.metric("Guadagno stimato", f"€{projected_profit:.2f}")

            st.caption(
                f"Probabilità combinata secondo il nostro modello: {combined_prob:.1%}. "
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
                        "mode": "automatica" if mode.startswith("🔍") else "manuale",
                        "status": "pending",
                        "combined_odds": round(combined_odds, 3),
                        "legs": [
                            {"match_id": leg["match_id"], "match_label": leg["match_label"],
                             "match_date": leg["match_date"], "selection": leg["selection"],
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

        f3, f4, f5 = st.columns([2, 1, 1])
        with f3:
            min_prob = st.slider("Probabilità di segnare almeno:",
                                 min_value=0, max_value=80, value=20, format="%d%%",
                                 key="marc_prob") / 100
        with f4:
            ordine = st.radio("Ordina per:", ["Probabilità", "Gol attesi"], key="marc_ordine")
        with f5:
            solo_titolari = st.checkbox("Solo probabili titolari", key="marc_titolari",
                                        help="Titolare in almeno 3 delle ultime 5 partite della squadra.")

        df = df[df["prob_score_anytime"] >= min_prob]
        if solo_titolari:
            df = df[df["starting_probability"].fillna(0) >= 0.6]
        df = df.sort_values("prob_score_anytime" if ordine == "Probabilità" else "expected_goals",
                            ascending=False)

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
    for col in ["prob_home", "prob_draw", "prob_away"]:
        display_df[col] = display_df[col] * 100

    percent_cols = ["Prob. 1", "Prob. X", "Prob. 2", "Prob. 1X", "Prob. X2", "Prob. 12"]
    st.dataframe(
        display_df[["date", "league", "home", "away", "prob_home", "prob_draw", "prob_away",
                     "prob_1x", "prob_x2", "prob_12"]]
        .rename(columns={
            "date": "Data", "league": "Campionato", "home": "Casa", "away": "Trasferta",
            "prob_home": "Prob. 1", "prob_draw": "Prob. X", "prob_away": "Prob. 2",
            "prob_1x": "Prob. 1X", "prob_x2": "Prob. X2", "prob_12": "Prob. 12",
        }),
        width='stretch', hide_index=True,
        column_config={col: st.column_config.NumberColumn(col, format="%.0f%%") for col in percent_cols},
    )
    st.caption(
        "1X = vittoria casa o pareggio · X2 = pareggio o vittoria trasferta · "
        "12 = vittoria di una delle due squadre (esclude il pareggio). "
        "Sono le nostre probabilità di modello — qui non abbiamo ancora le quote "
        "reali dei bookmaker per questi mercati, quindi non possiamo dirti se sono "
        "convenienti, solo quanto le riteniamo probabili."
    )
