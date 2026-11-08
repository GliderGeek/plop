import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import migraties

SCHEMA = Path(__file__).with_name("schema.sql")

VIAS = [("dd", "dd"), ("j", "j"), ("h", "h"), ("m", "m"), ("hm", "hm")]  # startdata voor een eerste jaar

BASIS_WIJNEN = ["Pertois tradition", "Dagonet tradition", "Pertois ½", "Dagonet ½"]  # startdata


def connect(path):
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)  # transacties expliciet via tx()
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init(conn):
    """Schema aanmaken, oude databases bijwerken (migraties.py), en een lege database
    vullen met een eerste jaar."""
    conn.executescript(SCHEMA.read_text())
    migraties.migreer(conn)
    with tx(conn):
        if not conn.execute("SELECT 1 FROM jaar").fetchone():
            maak_jaar(conn, date.today().year, VIAS, BASIS_WIJNEN)
        # is_vast en categorie 'basis' moeten samenvallen; de berekeningen gaan daarvan uit
        fout = conn.execute(
            "SELECT naam FROM wine WHERE (is_vast = 1) <> (categorie = 'basis')"
        ).fetchall()
        if fout:
            raise RuntimeError(
                "Wijnen met categorie 'basis' die geen vaste basisfles zijn (of omgekeerd): "
                + ", ".join(r["naam"] for r in fout)
            )


def maak_jaar(conn, jaar, vias, basis_wijnen):
    """Nieuw jaar met via's [(code, naam)] en vaste basisflessen [naam], in die volgorde.
    Zonder controles; die doet logic.jaar_aanmaken."""
    conn.execute("INSERT INTO jaar (jaar) VALUES (?)", (jaar,))
    conn.executemany("INSERT INTO via (jaar, code, naam, volgorde) VALUES (?, ?, ?, ?)",
                     [(jaar, code, naam, i) for i, (code, naam) in enumerate(vias)])
    conn.executemany("INSERT INTO wine (jaar, naam, categorie, is_vast) VALUES (?, ?, 'basis', 1)",
                     [(jaar, naam) for naam in basis_wijnen])


@contextmanager
def tx(conn):
    """Transactie; rolt terug bij een exception (ook ValidatieFout)."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")  # schrijflock vooraf: geen 'database is locked' bij lezen-dan-schrijven
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
