import re
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")

MIGRATIE_JAAR = 2026  # alle data van vóór de jaren hoort bij dit jaar

VIAS = [("dd", "dd"), ("j", "j"), ("h", "h"), ("m", "m"), ("hm", "hm")]  # startdata voor een eerste jaar
PERSONEN = ["dd", "j", "h", "m"]  # hm is de gezamenlijke bestelling van h en m, geen persoon; alleen nog voor de migratie

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
    _voeg_klant_betaald_toe(conn)
    _voeg_via_verrekend_toe(conn)
    _voeg_kostenverdeling_toe(conn)
    _voeg_jaren_toe(conn)
    _vul_kostendeelnemers(conn)
    with tx(conn):
        # categorie 'klein' (halve flessen) bestaat niet meer: valt onder basis
        conn.execute("UPDATE wine SET categorie = 'basis' WHERE categorie = 'klein'")
        if not conn.execute("SELECT 1 FROM jaar").fetchone():
            maak_jaar(conn, date.today().year, VIAS, [(naam, prijs) for naam, _cat, prijs in VASTE_WIJNEN])
        # is_vast en categorie 'basis' moeten samenvallen; de berekeningen gaan daarvan uit
        fout = conn.execute(
            "SELECT naam FROM wine WHERE (is_vast = 1) <> (categorie = 'basis')"
        ).fetchall()
        if fout:
            raise RuntimeError(
                "Wijnen met categorie 'basis' die geen vaste basisfles zijn (of omgekeerd): "
                + ", ".join(r["naam"] for r in fout)
            )


def maak_jaar(conn, jaar, vias, vaste_wijnen):
    """Nieuw jaar met via's [(code, naam)] en vaste basisflessen [(naam, prijs)], in die volgorde.
    Zonder controles; die doet logic.jaar_aanmaken."""
    conn.execute("INSERT INTO jaar (jaar) VALUES (?)", (jaar,))
    conn.executemany("INSERT INTO via (jaar, code, naam, volgorde) VALUES (?, ?, ?, ?)",
                     [(jaar, code, naam, i) for i, (code, naam) in enumerate(vias)])
    conn.executemany("INSERT INTO wine (jaar, naam, categorie, prijs, is_vast) VALUES (?, ?, 'basis', ?, 1)",
                     [(jaar, naam, prijs) for naam, prijs in vaste_wijnen])


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


def _voeg_klant_betaald_toe(conn):
    """Of een besteller zijn rekening aan de via betaald heeft."""
    if "betaald" not in _kolommen(conn, "klant"):
        conn.execute("ALTER TABLE klant ADD COLUMN betaald INTEGER NOT NULL DEFAULT 0")


def _voeg_via_verrekend_toe(conn):
    """Of een via zijn saldo met de anderen verrekend heeft."""
    if "verrekend" not in _kolommen(conn, "via"):
        conn.execute("ALTER TABLE via ADD COLUMN verrekend INTEGER NOT NULL DEFAULT 0")


def _voeg_kostenverdeling_toe(conn):
    """Over wie overige aankopen verdeeld worden; bestaande posten blijven bij de bestellers."""
    if "verdeling" not in _kolommen(conn, "kosten"):
        conn.execute("""ALTER TABLE kosten ADD COLUMN verdeling TEXT NOT NULL DEFAULT 'bestellers'
                        CHECK (verdeling IN ('bestellers', 'personen'))""")


def _vul_kostendeelnemers(conn):
    """Eigen kosten van vóór de deelnemers: daar deed iedereen aan mee."""
    posten = conn.execute(
        """SELECT id, jaar FROM kosten WHERE verdeling = 'personen'
           AND id NOT IN (SELECT kosten_id FROM kosten_deelnemer)"""
    ).fetchall()
    conn.executemany("INSERT INTO kosten_deelnemer (kosten_id, jaar, via) VALUES (?, ?, ?)",
                     [(r["id"], r["jaar"], p) for r in posten for p in PERSONEN])


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
            # regel_verdeling is dan al nieuw aangemaakt, met een jaar
            conn.execute(
                f"""INSERT INTO regel_verdeling (regel_id, jaar, via, aantal)
                    SELECT aankoop_id, {MIGRATIE_JAAR}, via, aantal FROM aankoop_verdeling"""
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
            # alleen de regels: tabellen die al naar via(jaar, code) verwijzen, controleert _voeg_jaren_toe
            if conn.execute("PRAGMA foreign_key_check(aankoop_regel)").fetchone():
                raise RuntimeError("Migratie naar aankoopregels gaf ongeldige verwijzingen.")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


# kolommen van vóór de jaren; die gaan mee naar de nieuwe tabellen, met jaar = MIGRATIE_JAAR
_JAREN_KOLOMMEN = {
    "via": "code, naam, volgorde, verrekend",
    "wine": "id, naam, categorie, prijs, is_vast",
    "klant": "id, via, naam, aantekening, iets_luxer_n, luxe_n, versie, betaald",
    "aankoop": "id, omschrijving, aangemaakt_op, versie, betaald_door",
    "regel_verdeling": "regel_id, via, aantal",
    "kosten": "id, omschrijving, bedrag, betaald_door, verdeling, aangemaakt_op, versie",
    "kosten_deelnemer": "kosten_id, via",
}


def _tabel_sql(tabel, nieuwe_naam):
    """De CREATE TABLE uit schema.sql, onder een andere naam."""
    m = re.search(rf"CREATE TABLE IF NOT EXISTS {tabel} \(.*?\n\);", SCHEMA.read_text(), re.S)
    return m.group(0).replace(f"IF NOT EXISTS {tabel} (", f"{nieuwe_naam} (", 1)


def _voeg_jaren_toe(conn):
    """Alles van vóór de jaren hoort bij MIGRATIE_JAAR. De tabellen met een via of een unieke naam
    krijgen een jaar in hun sleutel, dus die worden opnieuw opgebouwd. Vooraf een kopie van de database."""
    if "jaar" in _kolommen(conn, "via"):
        return
    pad = conn.execute("PRAGMA database_list").fetchone()["file"]
    if pad:
        kopie = sqlite3.connect(pad + ".voor-jaren")
        try:
            conn.backup(kopie)
        finally:
            kopie.close()
    conn.execute("PRAGMA foreign_keys = OFF")  # nodig om tabellen om te bouwen
    try:
        with tx(conn):
            conn.execute("INSERT OR IGNORE INTO jaar (jaar) VALUES (?)", (MIGRATIE_JAAR,))
            for tabel, kolommen in _JAREN_KOLOMMEN.items():
                conn.execute(_tabel_sql(tabel, f"{tabel}_nieuw"))
                conn.execute(f"INSERT INTO {tabel}_nieuw (jaar, {kolommen}) "
                             f"SELECT {MIGRATIE_JAAR}, {kolommen} FROM {tabel}")
                conn.execute(f"DROP TABLE {tabel}")
                conn.execute(f"ALTER TABLE {tabel}_nieuw RENAME TO {tabel}")
            if conn.execute("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError("Migratie naar jaren gaf ongeldige verwijzingen.")
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
