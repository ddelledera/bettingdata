"""
Funzioni per confrontare le nostre probabilità stimate con le quote dei
bookmaker, e capire quali scommesse hanno "valore" (value) e quanto
punterci sopra in modo prudente.
"""


def remove_bookmaker_margin(odds_dict):
    """
    Le quote di un bookmaker includono sempre un margine (il loro guadagno
    garantito). Questa funzione lo rimuove, per ottenere le probabilità
    "vere" implicite nel mercato.

    odds_dict: es. {"Home": 2.10, "Draw": 3.40, "Away": 3.60}
    Ritorna: probabilità de-vigged che sommano esattamente a 1.0
    """
    implied = {k: 1 / v for k, v in odds_dict.items()}
    total = sum(implied.values())  # sarà > 1.0, quello è il margine
    return {k: v / total for k, v in implied.items()}


def expected_value(model_probability, odds):
    """
    EV = quanto ti aspetti di guadagnare (in media, su tante ripetizioni)
    puntando 1 unità su questa scommessa.
    EV positivo = scommessa di valore secondo il tuo modello.
    """
    return (model_probability * odds) - 1


def kelly_fraction(model_probability, odds, fraction=0.25):
    """
    Quanto del tuo capitale (bankroll) puntare, secondo il criterio di Kelly.
    'fraction=0.25' significa Kelly frazionato a 1/4: molto più prudente del
    Kelly "pieno", che in pratica è troppo aggressivo e rischioso da usare.

    Ritorna una percentuale del bankroll (es. 0.02 = punta il 2% del capitale).
    Se il risultato è negativo o zero, significa: non c'è valore, non puntare.
    """
    b = odds - 1  # guadagno netto per unità puntata
    q = 1 - model_probability
    full_kelly = (model_probability * b - q) / b
    stake = max(full_kelly, 0) * fraction
    return stake


def find_value_bets(model_probabilities, odds_dict, min_ev=0.02):
    """
    Confronta le probabilità del modello con le quote e ritorna solo le
    scommesse con EV sopra la soglia minima (default 2%), ordinate dalla
    più conveniente.

    model_probabilities: es. {"Home": 0.52, "Draw": 0.25, "Away": 0.23}
    odds_dict: es. {"Home": 2.10, "Draw": 3.40, "Away": 3.60}
    """
    results = []
    for selection, prob in model_probabilities.items():
        odds = odds_dict[selection]
        ev = expected_value(prob, odds)
        if ev >= min_ev:
            results.append({
                "selection": selection,
                "model_probability": round(prob, 3),
                "odds": odds,
                "ev": round(ev, 3),
                "kelly_stake_pct": round(kelly_fraction(prob, odds) * 100, 2),
            })
    results.sort(key=lambda r: r["ev"], reverse=True)
    return results


def combine_parlay(legs):
    """
    Combina più selezioni indipendenti (partite diverse) in un'unica
    schedina. Le partite diverse si considerano indipendenti tra loro
    (il risultato dell'una non influenza l'altra), quindi le probabilità
    si moltiplicano e le quote pure — è così che funziona davvero una
    schedina dal vivo.

    legs: lista di dict, ciascuno con almeno "model_probability" e "odds"
    Ritorna: probabilità combinata, quota combinata, EV, puntata Kelly.
    """
    if not legs:
        return {"combined_probability": 0, "combined_odds": 0, "ev": 0, "kelly_stake_pct": 0}

    combined_prob = 1.0
    combined_odds = 1.0
    for leg in legs:
        combined_prob *= leg["model_probability"]
        combined_odds *= leg["odds"]

    ev = expected_value(combined_prob, combined_odds)
    stake = kelly_fraction(combined_prob, combined_odds) * 100

    return {
        "combined_probability": round(combined_prob, 4),
        "combined_odds": round(combined_odds, 2),
        "ev": round(ev, 3),
        "kelly_stake_pct": round(stake, 2),
    }


def find_best_combination(legs_by_match, num_matches, target_roi):
    """
    Trova la combinazione di 'num_matches' partite (una sola selezione
    per partita, quella migliore disponibile) che raggiunge un ritorno
    atteso almeno pari a 'target_roi' (es. 1.0 = vuoi raddoppiare i
    soldi puntati), scegliendo — tra tutte le combinazioni che centrano
    l'obiettivo — quella con la probabilità combinata più alta, cioè la
    più "sicura" tra quelle che comunque raggiungono il ritorno voluto.

    legs_by_match: dict {match_id: leg}, un solo leg per partita (già
        quello migliore per quella partita), ciascuno con almeno "odds"
        e "model_probability".
    num_matches: quante partite mettere in combinazione (es. 1-5).
    target_roi: ritorno desiderato come frazione (es. 1.0 = +100%).

    Ritorna: (combinazione scelta, ha_raggiunto_il_target) oppure None
    se non ci sono abbastanza partite disponibili per formare la
    combinazione richiesta.
    """
    from itertools import combinations
    import math

    legs_pool = list(legs_by_match.values())
    if len(legs_pool) < num_matches:
        return None

    target_odds = 1 + target_roi
    best_combo, best_prob = None, -1
    fallback_combo, fallback_odds = None, -1

    for combo in combinations(legs_pool, num_matches):
        combined_odds = math.prod(leg["odds"] for leg in combo)
        combined_prob = math.prod(leg["model_probability"] for leg in combo)

        if combined_odds >= target_odds and combined_prob > best_prob:
            best_combo, best_prob = combo, combined_prob

        if combined_odds > fallback_odds:
            fallback_combo, fallback_odds = combo, combined_odds

    if best_combo is not None:
        return best_combo, True
    return fallback_combo, False
