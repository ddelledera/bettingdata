"""
Funzione condivisa per inizializzare il database: crea le tabelle che
mancano (dallo schema.sql) e aggiunge le colonne nuove alle tabelle che
esistono già da una versione precedente dello schema.

PERCHÉ SERVE: 'CREATE TABLE IF NOT EXISTS' crea una tabella solo se non
esiste ancora — se il database esiste già (il nostro data.db, salvato ogni
giorno nel repository), una tabella che c'era già da prima NON viene
aggiornata con le colonne aggiunte in seguito allo schema. Va fatto a mano,
con ALTER TABLE, controllando prima se la colonna manca davvero.
"""

import sqlite3

# Colonne aggiunte allo schema DOPO la prima versione di ciascuna tabella.
# Quando si aggiunge una nuova colonna a una tabella esistente in schema.sql,
# va aggiunta anche qui, altrimenti un database creato prima di quel
# cambiamento non la riceve mai.
MIGRATIONS = [
    ("model_predictions", "expected_goals_home", "REAL"),
    ("model_predictions", "expected_goals_away", "REAL"),
    ("players", "availability", "TEXT"),
    ("players", "injury_type", "TEXT"),
]


def _column_exists(conn, table, column):
    cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    return column in cols


def init_db(conn, schema_path="schema.sql"):
    """Da chiamare subito dopo aver aperto la connessione, al posto di
    eseguire schema.sql direttamente: crea le tabelle mancanti E aggiorna
    quelle già esistenti con le colonne nuove."""
    with open(schema_path) as f:
        conn.executescript(f.read())

    for table, column, col_type in MIGRATIONS:
        if not _column_exists(conn, table, column):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    conn.commit()
