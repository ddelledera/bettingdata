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
    background: #FFFFFF;
    border-left: 5px solid var(--risk-color, #1B4332);
    border-radius: 4px;
    padding: 12px 18px;
    margin-bottom: 8px;
    box-shadow: 0 1px 2px rgba(27, 67, 50, 0.08);
}
.leg-basso { --risk-color: #2D6A4F; }
.leg-medio { --risk-color: #B08900; }
.leg-alto  { --risk-color: #A63A3A; }

.leg-match { flex: 1; }
.leg-teams { font-size: 1.08rem; font-weight: 600; color: #1A2420; }
.leg-date { font-size: 0.8rem; color: #7A8A81; margin-top: 1px; }

.leg-pick {
    background: #EEF3F0;
    color: #1B4332;
    font-weight: 600;
    font-size: 0.85rem;
    padding: 4px 12px;
    border-radius: 20px;
    margin: 0 16px;
    white-space: nowrap;
}

.leg-odds { text-align: right; min-width: 90px; }
.leg-odds-value { font-size: 1.35rem; font-weight: 700; color: #1B4332; }
.leg-odds-prob { font-size: 0.78rem; color: #7A8A81; }
</style>
""", unsafe_allow_html=True)

st.title("⚽ Le mie previsioni calcio")
st.caption("Partite in arrivo ordinate per convenienza, con quota migliore e puntata consigliata.")


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
    essere più la quota realmente disponibile oggi)."""
    query = """
        WITH ultima_quota AS (
            SELECT bookmaker, selection, odds,
                   ROW_NUMBER() OVER (
                       PARTITION BY bookmaker, selection
                       ORDER BY snapshot_time DESC
                   ) AS rn
            FROM odds_snapshots
            WHERE match_id = ?
        )
        SELECT selection, MAX(odds) as best_odds, bookmaker
        FROM ultima_quota
        WHERE rn = 1
        GROUP BY selection
    """
    rows = conn.execute(query, (match_id,)).fetchall()
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


# Bookmaker con licenza ADM (autorizzati in Italia) TRA QUELLI CHE LA NOSTRA
# FONTE DELLE QUOTE COPRE DAVVERO. Ho controllato l'elenco ufficiale e
# completo di The Odds API (tutte le regioni: EU, UK, Francia, Svezia,
# Finlandia): la maggior parte dei bookmaker italiani noti (Eurobet, Snai,
# Sisal, Lottomatica, Goldbet, Betflag, Planetwin365, AdmiralBet, ecc.)
# NON sono coperti da questa fonte gratuita, quindi non possiamo mostrarli
# anche se esistono davvero. Gli unici due esplicitamente etichettati come
# entità italiane sono questi:
BOOKMAKER_ITALIA = {"Unibet", "Codere"}
# Nota: "Unibet" potrebbe in teoria riferirsi anche a Unibet Francia/Olanda/
# Svezia (la fonte non distingue sempre il paese nel nome mostrato) — quindi
# anche questo non è garantito al 100% essere l'entità italiana.


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
    esito scelto, quota e probabilità del modello ben distinti visivamente."""
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
        </div>
    </div>
    """, unsafe_allow_html=True)


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

tab_opportunita, tab_schedina, tab_storico, tab_tutte = st.tabs(
    ["🎯 Opportunità di valore", "🎟️ Schedina", "📊 Storico schedine", "📋 Tutte le partite"]
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
        st.dataframe(
            opp_df, width='stretch', hide_index=True,
            column_config={
                "Nostra probabilità": st.column_config.ProgressColumn(
                    "Nostra probabilità", format="%.0f%%", min_value=0, max_value=100,
                ),
            },
        )

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

            if st.button("🔍 Trova la combinazione migliore"):
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
                if st.button("✅ Conferma questa schedina", key="confirm_slip_btn"):
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
            legs_desc = ", ".join(l["match_label"] for l in slip["legs"])
            st.write(f"🕒 **{slip['stake']}€ @ {slip['combined_odds']}** su {slip['bookmaker']} "
                     f"— {legs_desc}")

        st.divider()
        st.subheader(f"Concluse ({len(settled)})")
        if not settled:
            st.caption("Nessuna schedina ancora conclusa.")
        for slip in sorted(settled, key=lambda s: s.get("settled_at", ""), reverse=True):
            esito = slip["result_summary"]
            icona = "✅" if slip["status"] == "won" else "❌"
            st.write(f"{icona} **{slip['stake']}€ @ {slip['combined_odds']}** su {slip['bookmaker']} "
                     f"— profitto: **€{esito['profit']:+.2f}**")
            with st.expander("Dettaglio"):
                for leg in esito["legs"]:
                    check = "✔️" if leg["won"] else "✖️"
                    st.write(f"{check} {leg['match_label']} — puntato: {leg['selection']}, "
                             f"risultato vero: {leg['actual_result']}")

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
# SCHEDA 4: Tutte le partite in arrivo
# ---------------------------------------------------------------------------
with tab_tutte:
    filtered_df = competition_filter(matches_df, key="comp_tutte")
    st.dataframe(
        filtered_df[["date", "league", "home", "away", "prob_home", "prob_draw", "prob_away"]]
        .rename(columns={
            "date": "Data", "league": "Campionato", "home": "Casa", "away": "Trasferta",
            "prob_home": "Prob. 1", "prob_draw": "Prob. X", "prob_away": "Prob. 2",
        }),
        width='stretch', hide_index=True,
    )
