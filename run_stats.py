"""
Piccolo "diario di bordo" del giro giornaliero: ogni script ci scrive cosa
è successo (quante quote per bookmaker, partite non abbinate, squadre non
riconosciute...), e health_check.py lo legge alla fine per dire se il giro
è andato davvero bene. Il file resta sul computer di GitHub e non viene
salvato nel progetto.
"""

import json
import os

PATH = "run_stats.json"


def record(section, data):
    stats = load()
    stats[section] = data
    with open(PATH, "w") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)


def load():
    if not os.path.exists(PATH):
        return {}
    try:
        with open(PATH) as f:
            return json.load(f)
    except ValueError:
        return {}
