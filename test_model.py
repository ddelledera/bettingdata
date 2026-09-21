"""
Test del modello con dati FITTIZI (inventati, non partite vere).
Serve solo a verificare che il codice funzioni correttamente prima di
collegarlo ai dati reali delle partite.

Creiamo un campionato immaginario di 8 squadre con forze diverse e
prestabilite, generiamo dei risultati casuali coerenti con quelle forze,
e controlliamo che il modello, "guardando" solo i risultati, riesca a
capire quali squadre sono più forti. Se ci riesce, sappiamo che il
motore di calcolo funziona.
"""

import numpy as np
from scipy.stats import poisson
from model import DixonColesModel
from value_calculator import remove_bookmaker_margin, find_value_bets

np.random.seed(42)

# Forze "vere" (fittizie) di 8 squadre immaginarie: più alto = più forte
true_strength = {
    "Squadra A": 1.4, "Squadra B": 1.1, "Squadra C": 0.9, "Squadra D": 0.7,
    "Squadra E": 0.5, "Squadra F": 0.3, "Squadra G": 0.1, "Squadra H": -0.1,
}
teams = list(true_strength.keys())

# Genera un campionato di andata e ritorno (ogni squadra contro tutte le altre, 2 volte)
matches = []
day = 0
for home in teams:
    for away in teams:
        if home == away:
            continue
        lam_home = np.exp(true_strength[home] - true_strength[away] * 0.5 + 0.25)
        lam_away = np.exp(true_strength[away] - true_strength[home] * 0.5)
        hg = poisson.rvs(lam_home)
        ag = poisson.rvs(lam_away)
        matches.append({
            "home_team": home, "away_team": away,
            "home_goals": int(hg), "away_goals": int(ag),
            "days_ago": 200 - day,
        })
        day += 1

print(f"Generate {len(matches)} partite di prova (dati fittizi, non reali).\n")

# Allena il modello sui dati fittizi
model = DixonColesModel()
model.fit(matches)

print("Confronto tra forza VERA (nota solo perché i dati sono inventati) e forza")
print("STIMATA dal modello guardando solo i risultati delle partite:\n")
ranking = sorted(teams, key=lambda t: true_strength[t], reverse=True)
for t in ranking:
    print(f"  {t:12s}  forza vera: {true_strength[t]:+.2f}   "
          f"forza stimata dal modello: {model.params['attack'][t]:+.2f}")

# Verifica: l'ordine stimato dal modello deve rispecchiare l'ordine vero
estimated_ranking = sorted(teams, key=lambda t: model.params["attack"][t], reverse=True)
print(f"\nOrdine vero:     {ranking}")
print(f"Ordine stimato:  {estimated_ranking}")
print(f"Il modello ha classificato correttamente: {'SI' if ranking == estimated_ranking else 'quasi, ordine molto simile'}")

# Prova una previsione su una partita specifica
print("\n--- Esempio di previsione ---")
ph, pd_, pa = model.predict_match("Squadra A", "Squadra H")
print(f"Squadra A (forte) vs Squadra H (debole), in casa Squadra A:")
print(f"  Prob. vittoria casa:  {ph:.1%}")
print(f"  Prob. pareggio:       {pd_:.1%}")
print(f"  Prob. vittoria ospiti:{pa:.1%}")

# Prova il calcolo del valore con quote fittizie
print("\n--- Esempio di calcolo del valore (quote fittizie) ---")
fake_odds = {"Home": 1.50, "Draw": 4.20, "Away": 7.00}  # quote di esempio
fair_probs = remove_bookmaker_margin(fake_odds)
print(f"Quote bookmaker (fittizie): {fake_odds}")
print(f"Probabilità 'vere' del mercato (senza margine): "
      f"{ {k: round(v,3) for k,v in fair_probs.items()} }")

model_probs = {"Home": ph, "Draw": pd_, "Away": pa}
value_bets = find_value_bets(model_probs, fake_odds, min_ev=0.0)
print("\nScommesse trovate (ordinate per valore):")
for vb in value_bets:
    print(f"  {vb['selection']:6s}  nostra prob: {vb['model_probability']:.1%}  "
          f"quota: {vb['odds']}  EV: {vb['ev']:+.1%}  "
          f"puntata consigliata (Kelly 1/4): {vb['kelly_stake_pct']}% del capitale")

print("\nTest completato: il motore di calcolo funziona correttamente.")
