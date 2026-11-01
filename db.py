import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")

VIAS = [("dd", "dd"), ("j", "j"), ("h", "h"), ("m", "m"), ("hm", "hm")]

VASTE_WIJNEN = [
    ("Pertois tradition", "basis", 19.3),
    ("Dagonet tradition", "basis", 19.5),
    ("Pertois ½", "basis", 10.9),
    ("Dagonet ½", "basis", 10.5),
]


def connect(path):
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)  # transacties expliciet via tx()
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init(conn):
    conn.executescript(SCHEMA.read_text())
    _migreer_naar_regels(conn)
    _voeg_versie_toe(conn)
    _voeg_betaler_toe(conn)
    _voeg_kostenverdeling_toe(conn)
    with tx(conn):
        # categorie 'klein' (halve flessen) bestaat niet meer: valt onder basis
        conn.execute("UPDATE wine SET categorie = 'basis' WHERE categorie = 'klein'")
        for i, (code, naam) in enumerate(VIAS):
            conn.execute(
                "INSERT OR IGNORE INTO via (code, naam, volgorde) VALUES (?, ?, ?)",
                (code, naam, i),
            )
        for naam, cat, prijs in VASTE_WIJNEN:
            conn.execute(
                "INSERT OR IGNORE INTO wine (naam, categorie, prijs, is_vast) VALUES (?, ?, ?, 1)",
                (naam, cat, prijs),
            )
        # is_vast en categorie 'basis' moeten samenvallen; de berekeningen gaan daarvan uit
        fout = conn.execute(
            "SELECT naam FROM wine WHERE (is_vast = 1) <> (categorie = 'basis')"
        ).fetchall()
        if fout:
            raise RuntimeError(
                "Wijnen met categorie 'basis' die geen vaste basisfles zijn (of omgekeerd): "
                + ", ".join(r["naam"] for r in fout)
            )


def _kolommen(conn, tabel):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({tabel})")}


def _voeg_versie_toe(conn):
    """Versiekolom op aankoop en klant (optimistische vergrendeling bij bewerken)."""
    for tabel in ("aankoop", "klant"):
        if "versie" not in _kolommen(conn, tabel):
            conn.execute(f"ALTER TABLE {tabel} ADD COLUMN versie INTEGER NOT NULL DEFAULT 0")


def _voeg_betaler_toe(conn):
    """Wie een aankoop betaald heeft (leeg: nog onbekend)."""
    if "betaald_door" not in _kolommen(conn, "aankoop"):
        conn.execute("ALTER TABLE aankoop ADD COLUMN betaald_door TEXT REFERENCES via(code)")


def _voeg_kostenverdeling_toe(conn):
    """Over wie overige aankopen verdeeld worden; bestaande posten blijven bij de bestellers."""
    if "verdeling" not in _kolommen(conn, "kosten"):
        conn.execute("""ALTER TABLE kosten ADD COLUMN verdeling TEXT NOT NULL DEFAULT 'bestellers'
                        CHECK (verdeling IN ('bestellers', 'personen'))""")


def _migreer_naar_regels(conn):
    """Oude opzet (aankoop = één wijn + aankoop_verdeling) omzetten naar bon + regels."""
    if "wine_id" not in _kolommen(conn, "aankoop"):
        return
    conn.execute("PRAGMA foreign_keys = OFF")  # nodig om de aankoop-tabel om te bouwen
    try:
        with tx(conn):
            conn.execute(
                """INSERT INTO aankoop_regel (id, aankoop_id, wine_id, aantal, prijs)
                   SELECT id, id, wine_id, aantal, prijs FROM aankoop"""
            )
            conn.execute(
                """INSERT INTO regel_verdeling (regel_id, via, aantal)
                   SELECT aankoop_id, via, aantal FROM aankoop_verdeling"""
            )
            conn.execute("DROP TABLE aankoop_verdeling")
            conn.execute(
                """CREATE TABLE aankoop_nieuw (
                       id INTEGER PRIMARY KEY,
                       omschrijving TEXT NOT NULL DEFAULT '',
                       aangemaakt_op TEXT NOT NULL DEFAULT (datetime('now', 'localtime')))"""
            )
            conn.execute(
                "INSERT INTO aankoop_nieuw (id, aangemaakt_op) SELECT id, aangemaakt_op FROM aankoop"
            )
            conn.execute("DROP TABLE aankoop")
            conn.execute("ALTER TABLE aankoop_nieuw RENAME TO aankoop")
            if conn.execute("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError("Migratie naar aankoopregels gaf ongeldige verwijzingen.")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


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
