"""
Script di PROVA, da eseguire una sola volta: verifica se possiamo chiedere
doppia chance e Goal/No Goal nella stessa chiamata "in blocco" che usiamo
già per 1X2 (economica), oppure se servono chiamate separate per ogni
partita (più costose). Non fa parte della pipeline automatica giornaliera —
è solo per capire il costo reale prima di decidere come procedere.
"""

import os
import requests

API_KEY = os.environ.get("ODDS_API_KEY", "")
URL = "https://api.the-odds-api.com/v4/sports/soccer_italy_serie_a/odds"


def main():
    if not API_KEY:
        raise RuntimeError("Manca ODDS_API_KEY.")

    response = requests.get(URL, params={
        "apiKey": API_KEY,
        "regions": "eu",
        "markets": "h2h,double_chance,btts",  # proviamo tutti e tre insieme
        "oddsFormat": "decimal",
    }, timeout=20)

    print(f"Codice risposta HTTP: {response.status_code}")
    print(f"Crediti usati da questa chiamata: {response.headers.get('x-requests-last', '?')}")
    print(f"Crediti totali usati questo mese: {response.headers.get('x-requests-used', '?')}")
    print(f"Crediti rimanenti questo mese: {response.headers.get('x-requests-remaining', '?')}")
    print()

    if response.status_code != 200:
        print("Risposta:", response.text[:500])
        return

    events = response.json()
    print(f"Partite trovate: {len(events)}")
    if not events:
        print("Nessuna partita in programma ora per questo campionato: impossibile "
              "verificare quali mercati sono davvero inclusi.")
        return

    # Guardo i mercati DAVVERO presenti nella risposta, per il primo bookmaker
    # della prima partita, per capire se doppia chance e Goal/No Goal sono
    # arrivati per davvero o sono stati ignorati in silenzio.
    first_event = events[0]
    print(f"\nEsempio partita: {first_event['home_team']} vs {first_event['away_team']}")
    for bookmaker in first_event.get("bookmakers", [])[:3]:
        market_keys = [m["key"] for m in bookmaker.get("markets", [])]
        print(f"  {bookmaker['title']}: mercati presenti = {market_keys}")


if __name__ == "__main__":
    main()
