"""
Definizione UNICA dei mercati che l'app gestisce, usata da tutti gli
script (quote, modello, dashboard, controllo schedine), così un mercato
nuovo si aggiunge in un posto solo.

Ogni "selezione" ha una chiave unica (es. "Over2.5") che finisce nella
colonna 'selection' di odds_snapshots e delle schedine salvate. Per
compatibilità, l'1X2 conserva le chiavi storiche Home / Draw / Away.
"""

# chiave -> (mercato, etichetta breve, etichetta lunga, esito vinto?)
# 'vinta' riceve i gol di casa e trasferta e dice se la selezione è vincente
SELECTIONS = {
    "Home":     ("1X2", "1", "1 · casa", lambda h, a: h > a),
    "Draw":     ("1X2", "X", "X · pareggio", lambda h, a: h == a),
    "Away":     ("1X2", "2", "2 · trasferta", lambda h, a: h < a),
    "1X":       ("Doppia chance", "1X", "1X · casa o pareggio", lambda h, a: h >= a),
    "X2":       ("Doppia chance", "X2", "X2 · pareggio o trasferta", lambda h, a: h <= a),
    "12":       ("Doppia chance", "12", "12 · non pareggio", lambda h, a: h != a),
    "Goal":     ("Goal/No Goal", "Goal", "Goal · segnano entrambe", lambda h, a: h > 0 and a > 0),
    "NoGoal":   ("Goal/No Goal", "No Goal", "No Goal · almeno una non segna", lambda h, a: h == 0 or a == 0),
    "Over1.5":  ("Under/Over", "Over 1.5", "Over 1.5 · almeno 2 gol", lambda h, a: h + a >= 2),
    "Under1.5": ("Under/Over", "Under 1.5", "Under 1.5 · al massimo 1 gol", lambda h, a: h + a <= 1),
    "Over2.5":  ("Under/Over", "Over 2.5", "Over 2.5 · almeno 3 gol", lambda h, a: h + a >= 3),
    "Under2.5": ("Under/Over", "Under 2.5", "Under 2.5 · al massimo 2 gol", lambda h, a: h + a <= 2),
    "Over3.5":  ("Under/Over", "Over 3.5", "Over 3.5 · almeno 4 gol", lambda h, a: h + a >= 4),
    "Under3.5": ("Under/Over", "Under 3.5", "Under 3.5 · al massimo 3 gol", lambda h, a: h + a <= 3),
}

MARKET_NAMES = ["1X2", "Doppia chance", "Goal/No Goal", "Under/Over", "Marcatori"]

# I marcatori non sono in SELECTIONS: la loro chiave è "Scorer:<id giocatore>"
# e l'esito non si ricava dal risultato finale ma dai gol del giocatore
# (vedi check_results.py).
SCORER_PREFIX = "Scorer:"


def is_scorer(key):
    return str(key).startswith(SCORER_PREFIX)


def scorer_key(player_id):
    return f"{SCORER_PREFIX}{player_id}"


def market_of(key):
    return "Marcatori" if is_scorer(key) else SELECTIONS[key][0]


def short_label(key):
    return SELECTIONS[key][1] if key in SELECTIONS else key


def long_label(key, player_name=None):
    if is_scorer(key):
        return f"⚽ {player_name or 'Giocatore'} segna"
    return SELECTIONS[key][2] if key in SELECTIONS else key


def is_winner(key, home_goals, away_goals):
    return SELECTIONS[key][3](home_goals, away_goals)


def model_probabilities(row):
    """Probabilità del modello per tutte le selezioni di una partita, a
    partire da una riga di model_predictions. I mercati per cui la riga
    non ha ancora i dati (previsioni vecchie) vengono semplicemente omessi."""
    ph, pd_, pa = row["prob_home"], row["prob_draw"], row["prob_away"]
    probs = {"Home": ph, "Draw": pd_, "Away": pa,
             "1X": ph + pd_, "X2": pd_ + pa, "12": ph + pa}
    btts = row.get("prob_btts")
    if btts is not None and btts == btts:  # esclude None e NaN
        probs["Goal"], probs["NoGoal"] = btts, 1 - btts
    for line, col in (("1.5", "prob_over15"), ("2.5", "prob_over25"), ("3.5", "prob_over35")):
        p = row.get(col)
        if p is not None and p == p:
            probs[f"Over{line}"], probs[f"Under{line}"] = p, 1 - p
    return probs


# ---------------------------------------------------------------------------
# Probabilità "giuste" dal prezzo di Pinnacle (il bookmaker più efficiente).
# Il backtest ha mostrato che il nostro modello prevede PEGGIO del mercato,
# mentre giocare le quote sopra il prezzo giusto di Pinnacle ha dato CLV
# positivo: il valore si misura quindi contro Pinnacle, non contro il modello.
# ---------------------------------------------------------------------------
TWO_WAY_GROUPS = [("Goal", "NoGoal"), ("Over1.5", "Under1.5"),
                  ("Over2.5", "Under2.5"), ("Over3.5", "Under3.5")]


def devig_power(odds_list):
    """Toglie il margine con il metodo "potenza": il margine pesa di più sugli
    sfavoriti, come nella realtà. (Il metodo proporzionale lascia agli
    sfavoriti una probabilità troppo alta e fa sembrare di valore scommesse
    che non lo sono: nel backtest, sulle quote oltre 5, -35%/-60%.)"""
    inv = [1 / o for o in odds_list]
    lo, hi = 1.0, 3.0
    for _ in range(60):
        k = (lo + hi) / 2
        if sum(x ** k for x in inv) > 1:
            lo = k
        else:
            hi = k
    return [x ** k for x in inv]


def fair_probabilities(reference_odds):
    """{selezione: quota Pinnacle} -> {selezione: probabilità senza margine}.
    La doppia chance si ricava dall'1X2 (i suoi tre esiti si sovrappongono)."""
    fair = {}
    if all(reference_odds.get(k) for k in ("Home", "Draw", "Away")):
        ph, pd_, pa = devig_power([reference_odds[k] for k in ("Home", "Draw", "Away")])
        fair.update({"Home": ph, "Draw": pd_, "Away": pa,
                     "1X": ph + pd_, "X2": pd_ + pa, "12": ph + pa})
    for a, b in TWO_WAY_GROUPS:
        if reference_odds.get(a) and reference_odds.get(b):
            fair[a], fair[b] = devig_power([reference_odds[a], reference_odds[b]])
    return fair
