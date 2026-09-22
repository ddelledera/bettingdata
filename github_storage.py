"""
Funzioni per leggere e scrivere file nel repository GitHub direttamente
dalla pagina web.

PERCHÉ SERVE: la pagina web (Streamlit) non ha un disco permanente — se
salvasse qualcosa localmente, sparirebbe al prossimo riavvio o al prossimo
aggiornamento automatico dei dati. L'unico posto che resta per sempre è il
repository GitHub stesso. Quindi, quando confermi una schedina, la pagina
web la scrive direttamente lì (tramite l'API di GitHub), dove lo script
automatico del giorno dopo può leggerla e controllare come è andata.
"""

import base64
import json
import requests

API_BASE = "https://api.github.com"


def _headers(token):
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def read_json_file(owner, repo, path, token, default=None):
    """
    Legge un file JSON dal repository.
    Ritorna (dati, sha): 'sha' identifica la versione attuale del file e
    serve per poterlo poi aggiornare in sicurezza (evita di sovrascrivere
    per errore una modifica fatta nel frattempo da qualcun altro).
    Se il file non esiste ancora, ritorna (default, None).
    """
    url = f"{API_BASE}/repos/{owner}/{repo}/contents/{path}"
    response = requests.get(url, headers=_headers(token), timeout=15)
    if response.status_code == 404:
        return (default if default is not None else []), None
    response.raise_for_status()
    data = response.json()
    content = base64.b64decode(data["content"]).decode("utf-8")
    return json.loads(content), data["sha"]


def write_json_file(owner, repo, path, token, data, sha, message):
    """Scrive (crea o aggiorna) un file JSON nel repository."""
    url = f"{API_BASE}/repos/{owner}/{repo}/contents/{path}"
    content_str = json.dumps(data, indent=2, ensure_ascii=False)
    content_b64 = base64.b64encode(content_str.encode("utf-8")).decode("utf-8")
    payload = {"message": message, "content": content_b64, "branch": "main"}
    if sha:
        payload["sha"] = sha
    response = requests.put(url, headers=_headers(token), json=payload, timeout=15)
    response.raise_for_status()
    return response.json()
