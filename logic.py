"""Berekeningen en mutaties. Alles wat getallen afleidt of invoer valideert zit hier."""

from collections import defaultdict

from db import tx

CATEGORIEEN = {
    "basis": "Basis",
    "iets_luxer": "Iets luxer",
    "luxe": "Luxe",
}
ZOEK_CATEGORIEEN = ["iets_luxer", "luxe"]  # tijdens de trip te kopen


class ValidatieFout(Exception):
    pass


# --- lezen -------------------------------------------------------------------

def vias(conn):
    return conn.execute("SELECT * FROM via ORDER BY volgorde").fetchall()


def wijnen(conn, vast=None):
    sql, args = "SELECT * FROM wine", ()
    if vast is not None:
        sql, args = sql + " WHERE is_vast = ?", (1 if vast else 0,)
    return conn.execute(sql + " ORDER BY is_vast DESC, CASE WHEN is_vast THEN id END, categorie, naam", args).fetchall()


def wijn(conn, wine_id):
    return conn.execute("SELECT * FROM wine WHERE id = ?", (wine_id,)).fetchone()


def voorraad(conn):
    """{(via, wine_id): aantal} — som van de verdelingen van alle aankoopregels."""
    rows = conn.execute(
        """SELECT v.via, r.wine_id, SUM(v.aantal) n
           FROM regel_verdeling v JOIN aankoop_regel r ON r.id = v.regel_id
           GROUP BY v.via, r.wine_id"""
    )
    return {(r["via"], r["wine_id"]): r["n"] for r in rows}


def toegekend(conn):
    """{(via, wine_id): aantal} — toegekend aan klanten van die via."""
    rows = conn.execute(
        """SELECT k.via, t.wine_id, SUM(t.aantal) n
           FROM toekenning t JOIN klant k ON k.id = t.klant_id
           GROUP BY k.via, t.wine_id"""
    )
    return {(r["via"], r["wine_id"]): r["n"] for r in rows}


def pool(conn, via):
    """Wijnen die een via heeft, met hoeveel er nog niet toegekend zijn."""
    vr, tk = voorraad(conn), toegekend(conn)
    uit = []
    for w in wijnen(conn):
        n, t = vr.get((via, w["id"]), 0), tk.get((via, w["id"]), 0)
        if n or t:
            uit.append({"wijn": w, "voorraad": n, "toegekend": t, "vrij": n - t})
    return uit


def _stand(nodig, gekocht):
    return {"nodig": nodig, "gekocht": gekocht, "nog": nodig - gekocht}


def _samen(standen):
    """Totaal over de via's; tekorten en overschotten niet tegen elkaar wegstrepen."""
    standen = list(standen)
    return {"nodig": sum(s["nodig"] for s in standen),
            "gekocht": sum(s["gekocht"] for s in standen),
            "nog": sum(max(s["nog"], 0) for s in standen),
            "overschot": sum(max(-s["nog"], 0) for s in standen)}


def overzicht(conn):
    """Per via: nodig / gekocht / nog per zoekcategorie en per vaste fles, plus totalen."""
    nodig = defaultdict(int)  # (via, categorie)
    for r in conn.execute(
        "SELECT via, SUM(iets_luxer_n) il, SUM(luxe_n) lx FROM klant GROUP BY via"
    ):
        nodig[(r["via"], "iets_luxer")] = r["il"]
        nodig[(r["via"], "luxe")] = r["lx"]

    nodig_vast = defaultdict(int)  # (via, wine_id)
    for r in conn.execute(
        """SELECT k.via, kv.wine_id, SUM(kv.aantal) n FROM klant_vast kv
           JOIN klant k ON k.id = kv.klant_id GROUP BY k.via, kv.wine_id"""
    ):
        nodig_vast[(r["via"], r["wine_id"])] = r["n"]

    vr = voorraad(conn)
    cat_van = {w["id"]: w["categorie"] for w in wijnen(conn)}
    gekocht = defaultdict(int)  # (via, categorie)
    for (via, wid), n in vr.items():
        gekocht[(via, cat_van[wid])] += n

    vaste = wijnen(conn, vast=True)
    per_via = [
        {"via": v,
         "cats": {c: _stand(nodig[(v["code"], c)], gekocht[(v["code"], c)]) for c in ZOEK_CATEGORIEEN},
         "vast": [{"wijn": w, **_stand(nodig_vast[(v["code"], w["id"])], vr.get((v["code"], w["id"]), 0))}
                  for w in vaste]}
        for v in vias(conn)
    ]
    return {
        "per_via": per_via,
        "totaal": {c: _samen(r["cats"][c] for r in per_via) for c in ZOEK_CATEGORIEEN},
        "totaal_vast": [{"wijn": w, **_samen(r["vast"][i] for r in per_via)}
                        for i, w in enumerate(vaste)],
    }


def nog_per_via(conn):
    """Voor het aankoopformulier: {"basis": {basisfles: {via: nog}}, categorie: {via: nog}}."""
    nog = {"basis": {}, **{c: {} for c in ZOEK_CATEGORIEEN}}
    for r in overzicht(conn)["per_via"]:
        for x in r["vast"]:
            nog["basis"].setdefault(x["wijn"]["naam"], {})[r["via"]["code"]] = x["nog"]
        for c in ZOEK_CATEGORIEEN:
            nog[c][r["via"]["code"]] = r["cats"][c]["nog"]
    return nog


def bestel_totalen(conn, ks=None):
    """Wat er besteld is, per via en samen (sleutel '_'): per vaste fles, per zoekcategorie en totaal."""
    ks = klanten(conn) if ks is None else ks
    vaste = wijnen(conn, vast=True)
    totalen = {}
    for v in vias(conn):
        rij = [k for k in ks if k["k"]["via"] == v["code"]]
        totalen[v["code"]] = {
            "vast": {w["id"]: sum(k["vast"].get(w["id"], 0) for k in rij) for w in vaste},
            **{c: sum(k["nodig"][c] for k in rij) for c in ZOEK_CATEGORIEEN},
            "totaal": sum(k["totaal"] for k in rij),
        }
    totalen["_"] = {
        "vast": {w["id"]: sum(t["vast"][w["id"]] for t in totalen.values()) for w in vaste},
        **{c: sum(t[c] for t in totalen.values()) for c in (*ZOEK_CATEGORIEEN, "totaal")},
    }
    return totalen


def klant(conn, klant_id):
    """Eén klant zoals klanten() hem teruggeeft, of None."""
    return next(iter(klanten(conn, klant_id=klant_id)), None)


def klanten(conn, via=None, klant_id=None):
    """Klanten met hun vaste bestelling, toekenningen en open aantallen per zoekcategorie."""
    sql, args = "SELECT * FROM klant", ()
    if klant_id is not None:
        sql, args = sql + " WHERE id = ?", (klant_id,)
    elif via:
        sql, args = sql + " WHERE via = ?", (via,)
    rows = conn.execute(
        sql + " ORDER BY (SELECT volgorde FROM via WHERE code = klant.via), naam", args
    ).fetchall()

    vast = defaultdict(dict)
    for r in conn.execute("SELECT * FROM klant_vast"):
        vast[r["klant_id"]][r["wine_id"]] = r["aantal"]

    tks = defaultdict(list)
    for r in conn.execute(
        """SELECT t.klant_id, t.aantal, w.id wine_id, w.naam, w.categorie
           FROM toekenning t JOIN wine w ON w.id = t.wine_id ORDER BY w.naam"""
    ):
        tks[r["klant_id"]].append(dict(r))

    vaste = wijnen(conn, vast=True)
    uit = []
    for k in rows:
        heeft_wijn = defaultdict(int)
        for t in tks[k["id"]]:
            heeft_wijn[t["wine_id"]] += t["aantal"]
        vast_status = [
            {"wijn": w, "nodig": vast[k["id"]].get(w["id"], 0), "heeft": heeft_wijn[w["id"]],
             "open": vast[k["id"]].get(w["id"], 0) - heeft_wijn[w["id"]]}
            for w in vaste
            if vast[k["id"]].get(w["id"]) or heeft_wijn[w["id"]]
        ]
        nodig = {"iets_luxer": k["iets_luxer_n"], "luxe": k["luxe_n"]}
        heeft = {c: sum(t["aantal"] for t in tks[k["id"]] if t["categorie"] == c)
                 for c in ZOEK_CATEGORIEEN}
        open_ = {c: nodig[c] - heeft[c] for c in ZOEK_CATEGORIEEN}
        totaal = sum(vast[k["id"]].values()) + nodig["iets_luxer"] + nodig["luxe"]
        uit.append({
            "k": k,
            "vast": vast[k["id"]],
            "vast_status": vast_status,
            "toekenningen": tks[k["id"]],
            "nodig": nodig,
            "heeft": heeft,
            "open": open_,
            "totaal": totaal,
            "compleet": totaal > 0 and all(s["open"] <= 0 for s in vast_status)
                        and all(n <= 0 for n in open_.values()),
        })
    return uit


def aankopen(conn, aankoop_id=None):
    """Bonnen met hun regels (wijn, aantal, prijs, verdeling per via), nieuwste eerst."""
    sql, args = "SELECT * FROM aankoop", ()
    if aankoop_id is not None:
        sql, args = sql + " WHERE id = ?", (aankoop_id,)
    bonnen = conn.execute(sql + " ORDER BY id DESC", args).fetchall()

    verdeling = defaultdict(dict)
    for r in conn.execute(
        """SELECT v.regel_id, v.via, v.aantal FROM regel_verdeling v
           JOIN via ON via.code = v.via ORDER BY via.volgorde"""
    ):
        verdeling[r["regel_id"]][r["via"]] = r["aantal"]

    regels = defaultdict(list)
    for r in conn.execute(
        """SELECT r.*, w.naam, w.categorie, w.is_vast FROM aankoop_regel r
           JOIN wine w ON w.id = r.wine_id ORDER BY r.id"""
    ):
        regels[r["aankoop_id"]].append({"r": r, "verdeling": verdeling[r["id"]]})

    return [
        {"a": a, "regels": regels[a["id"]], "totaal": sum(x["r"]["aantal"] for x in regels[a["id"]])}
        for a in bonnen
    ]


# --- consistentie ------------------------------------------------------------

def controleer(conn, bij_toekennen=False):
    """Niet meer toegekend dan een via heeft."""
    vr, tk = voorraad(conn), toegekend(conn)
    for (via, wid), t in tk.items():
        n = vr.get((via, wid), 0)
        if t > n:
            naam = wijn(conn, wid)["naam"]
            if bij_toekennen:
                raise ValidatieFout(f"Niet genoeg {naam} vrij in de pool van {via}.")
            raise ValidatieFout(
                f"{via} heeft {n} × {naam}, maar er is {t} toegekend. "
                "Haal eerst toekenningen weg."
            )


def _int(v, veld, minimum=0):
    try:
        n = int(v or 0)
    except (TypeError, ValueError):
        raise ValidatieFout(f"{veld}: geen geldig getal.")
    if minimum is not None and n < minimum:
        raise ValidatieFout(f"{veld} moet minimaal {minimum} zijn.")
    return n


def _versie(v):
    """Versie uit een formulier; None = niet controleren."""
    return None if v in (None, "") else _int(v, "Versie")


def _prijs(v):
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        raise ValidatieFout("Prijs is geen geldig getal.")


def _via(conn, code):
    if not conn.execute("SELECT 1 FROM via WHERE code = ?", (code,)).fetchone():
        raise ValidatieFout(f"Onbekende via: {code}.")
    return code


def _bestaande_wijn(conn, wine_id):
    w = wijn(conn, wine_id)
    if w is None:
        raise ValidatieFout("Onbekende wijn.")
    return w


def _bestaande_klant(conn, klant_id):
    if not conn.execute("SELECT 1 FROM klant WHERE id = ?", (klant_id,)).fetchone():
        raise ValidatieFout("Deze klant bestaat niet (meer). Laad de pagina opnieuw.")


def _intussen_gewijzigd(wat):
    return ValidatieFout(f"{wat} is intussen gewijzigd of verwijderd. Laad de pagina opnieuw.")


# --- klanten -----------------------------------------------------------------

def klant_opslaan(conn, klant_id, via, naam, aantekening, iets_luxer_n, luxe_n, vast, versie=None):
    """Nieuwe klant (klant_id leeg) of bestaande bijwerken. Met versie (uit het formulier)
    mislukt het als iemand anders de klant intussen heeft gewijzigd."""
    naam = (naam or "").strip()
    if not naam:
        raise ValidatieFout("Naam is verplicht.")
    il = _int(iets_luxer_n, "Iets luxer")
    lx = _int(luxe_n, "Luxe")
    vast = {wid: _int(n, "Aantal") for wid, n in vast.items()}
    versie = _versie(versie)
    with tx(conn):
        _via(conn, via)
        if klant_id:
            sql, args = ("""UPDATE klant SET via=?, naam=?, aantekening=?, iets_luxer_n=?, luxe_n=?,
                            versie = versie + 1 WHERE id=?""",
                         [via, naam, aantekening or "", il, lx, klant_id])
            if versie is not None:
                sql, args = sql + " AND versie=?", args + [versie]
            if not conn.execute(sql, args).rowcount:
                raise _intussen_gewijzigd(naam)
        else:
            klant_id = conn.execute(
                """INSERT INTO klant (via, naam, aantekening, iets_luxer_n, luxe_n)
                   VALUES (?, ?, ?, ?, ?)""",
                (via, naam, aantekening or "", il, lx),
            ).lastrowid
        for wid, n in vast.items():
            if not conn.execute("SELECT 1 FROM wine WHERE id = ? AND is_vast = 1", (wid,)).fetchone():
                raise ValidatieFout("Onbekende basisfles.")
            conn.execute(
                "DELETE FROM klant_vast WHERE klant_id=? AND wine_id=?", (klant_id, wid)
            )
            if n:
                conn.execute(
                    "INSERT INTO klant_vast (klant_id, wine_id, aantal) VALUES (?, ?, ?)",
                    (klant_id, wid, n),
                )
        controleer(conn)  # via gewijzigd → toekenningen verhuizen mee
    return klant_id


def klant_verwijderen(conn, klant_id):
    with tx(conn):
        conn.execute("DELETE FROM klant WHERE id=?", (klant_id,))


# --- aankopen ----------------------------------------------------------------

def aankoop_opslaan(conn, aankoop_id, omschrijving, regels, versie=None):
    """Slaat een bon op. regels: [{naam, categorie, prijs, verdeling: {via: n}}].
    Wijnen worden op naam herkend (hoofdletterongevoelig) en zo nodig aangemaakt.
    Met versie (uit het formulier) mislukt het als de bon intussen is gewijzigd,
    bijvoorbeeld door een overdracht."""
    schoon = [_regel(r) for r in regels]
    if not schoon:
        raise ValidatieFout("Vul minimaal één wijn in.")
    _geen_tegenstrijdige_categorie(schoon)
    versie = _versie(versie)
    omschrijving = (omschrijving or "").strip()
    with tx(conn):
        for via in {v for r in schoon for v in r["verdeling"]}:
            _via(conn, via)
        if aankoop_id:
            sql, args = "UPDATE aankoop SET omschrijving=?, versie = versie + 1 WHERE id=?", [omschrijving, aankoop_id]
            if versie is not None:
                sql, args = sql + " AND versie=?", args + [versie]
            if not conn.execute(sql, args).rowcount:
                raise _intussen_gewijzigd(f"Aankoop {omschrijving}".strip())
            conn.execute("DELETE FROM aankoop_regel WHERE aankoop_id=?", (aankoop_id,))
        else:
            aankoop_id = conn.execute(
                "INSERT INTO aankoop (omschrijving) VALUES (?)", (omschrijving,)
            ).lastrowid
        for r in schoon:
            wine_id = _wijn_voor(conn, r["naam"], r["categorie"])
            regel_id = conn.execute(
                "INSERT INTO aankoop_regel (aankoop_id, wine_id, aantal, prijs) VALUES (?, ?, ?, ?)",
                (aankoop_id, wine_id, r["aantal"], r["prijs"]),
            ).lastrowid
            conn.executemany(
                "INSERT INTO regel_verdeling (regel_id, via, aantal) VALUES (?, ?, ?)",
                [(regel_id, via, n) for via, n in r["verdeling"].items()],
            )
        controleer(conn)
        _ruim_wijnen_op(conn)
    return aankoop_id


def _regel(r):
    naam = (r.get("naam") or "").strip()
    if not naam:
        raise ValidatieFout("Vul bij elke regel een wijn in.")
    verdeling = {v: _int(n, f"{naam}: aantal {v}") for v, n in r.get("verdeling", {}).items()}
    verdeling = {v: n for v, n in verdeling.items() if n}
    if not verdeling:
        raise ValidatieFout(f"{naam}: verdeel minimaal één fles.")
    # het aantal van de regel is de som van de verdeling
    return {"naam": naam, "categorie": r.get("categorie") or None, "aantal": sum(verdeling.values()),
            "prijs": _prijs(r.get("prijs")), "verdeling": verdeling}


def _geen_tegenstrijdige_categorie(regels):
    """Dezelfde wijn mag vaker op een bon staan, maar niet met verschillende categorieën."""
    gezien = {}
    for r in regels:
        if not r["categorie"]:
            continue
        cat = gezien.setdefault(r["naam"].lower(), r["categorie"])
        if cat != r["categorie"]:
            raise ValidatieFout(f"{r['naam']}: staat twee keer op de bon met een andere categorie.")


def _wijn_voor(conn, naam, categorie):
    """Wijn op naam opzoeken of aanmaken. Vaste basisflessen hebben hun eigen categorie;
    voor een nieuwe wijn is een categorie verplicht."""
    w = conn.execute("SELECT * FROM wine WHERE naam = ? COLLATE NOCASE", (naam,)).fetchone()
    if w is None:
        if categorie not in ZOEK_CATEGORIEEN:
            raise ValidatieFout(f"{naam}: kies een categorie.")
        return conn.execute(
            "INSERT INTO wine (naam, categorie) VALUES (?, ?)", (naam, categorie)
        ).lastrowid
    if w["is_vast"] or not categorie or w["categorie"] == categorie:
        return w["id"]
    if categorie not in ZOEK_CATEGORIEEN:
        raise ValidatieFout(f"{naam}: ongeldige categorie.")
    # alleen omzetten als geen andere bon of toekenning deze wijn gebruikt
    elders = conn.execute(
        """SELECT 1 FROM aankoop_regel WHERE wine_id = ?
           UNION SELECT 1 FROM toekenning WHERE wine_id = ?""",
        (w["id"], w["id"]),
    ).fetchone()
    if elders:
        raise ValidatieFout(
            f"{w['naam']} staat al als {CATEGORIEEN[w['categorie']]} op een andere bon "
            "of is al toegekend. Kies die categorie."
        )
    conn.execute("UPDATE wine SET categorie=? WHERE id=?", (categorie, w["id"]))
    return w["id"]


def _ruim_wijnen_op(conn):
    """Wijnen die op geen enkele bon meer staan, verdwijnen."""
    conn.execute(
        """DELETE FROM wine WHERE is_vast = 0
           AND id NOT IN (SELECT wine_id FROM aankoop_regel)
           AND id NOT IN (SELECT wine_id FROM toekenning)"""
    )


def aankoop_verwijderen(conn, aankoop_id):
    with tx(conn):
        conn.execute("DELETE FROM aankoop WHERE id=?", (aankoop_id,))
        controleer(conn)
        _ruim_wijnen_op(conn)


# --- overdragen ---------------------------------------------------------------

def overdragen(conn, wine_id, van_via, naar_via, aantal):
    """Verschuift flessen tussen via's door de verdeling van de aankoopregels aan te passen
    (nieuwste bon eerst). Er wordt geen aparte overdracht opgeslagen."""
    wine_id = _int(wine_id, "Wijn", 1)
    aantal = _int(aantal, "Aantal", 1)
    if van_via == naar_via:
        raise ValidatieFout("Kies een andere via.")
    with tx(conn):
        w = _bestaande_wijn(conn, wine_id)
        _via(conn, van_via)
        _via(conn, naar_via)
        rows = conn.execute(
            """SELECT v.regel_id, v.aantal FROM regel_verdeling v
               JOIN aankoop_regel r ON r.id = v.regel_id
               WHERE r.wine_id = ? AND v.via = ? ORDER BY r.aankoop_id DESC, r.id DESC""",
            (wine_id, van_via),
        ).fetchall()
        heeft = sum(r["aantal"] for r in rows)
        if heeft < aantal:
            raise ValidatieFout(f"{van_via} heeft maar {heeft} × {w['naam']}.")
        rest = aantal
        for r in rows:
            n = min(rest, r["aantal"])
            _verplaats(conn, r["regel_id"], van_via, naar_via, n)
            rest -= n
            if not rest:
                break
        controleer(conn)


def _verplaats(conn, regel_id, van_via, naar_via, n):
    huidig = conn.execute(
        "SELECT aantal FROM regel_verdeling WHERE regel_id=? AND via=?", (regel_id, van_via)
    ).fetchone()["aantal"]
    if huidig == n:
        conn.execute("DELETE FROM regel_verdeling WHERE regel_id=? AND via=?", (regel_id, van_via))
    else:
        conn.execute(
            "UPDATE regel_verdeling SET aantal = aantal - ? WHERE regel_id=? AND via=?",
            (n, regel_id, van_via),
        )
    conn.execute(
        """INSERT INTO regel_verdeling (regel_id, via, aantal) VALUES (?, ?, ?)
           ON CONFLICT (regel_id, via) DO UPDATE SET aantal = aantal + excluded.aantal""",
        (regel_id, naar_via, n),
    )
    # de bon is gewijzigd: een open bewerkformulier mag dit niet meer overschrijven
    conn.execute(
        "UPDATE aankoop SET versie = versie + 1 WHERE id = (SELECT aankoop_id FROM aankoop_regel WHERE id = ?)",
        (regel_id,),
    )


# --- toekenningen ------------------------------------------------------------

def toekenning_wijzig(conn, klant_id, wine_id, delta):
    klant_id, wine_id = _int(klant_id, "Klant", 1), _int(wine_id, "Wijn", 1)
    delta = _int(delta, "Aantal", minimum=None)
    with tx(conn):
        _bestaande_klant(conn, klant_id)
        _bestaande_wijn(conn, wine_id)
        r = conn.execute(
            "SELECT aantal FROM toekenning WHERE klant_id=? AND wine_id=?", (klant_id, wine_id)
        ).fetchone()
        nieuw = (r["aantal"] if r else 0) + delta
        conn.execute(
            "DELETE FROM toekenning WHERE klant_id=? AND wine_id=?", (klant_id, wine_id)
        )
        if nieuw > 0:
            conn.execute(
                "INSERT INTO toekenning (klant_id, wine_id, aantal) VALUES (?, ?, ?)",
                (klant_id, wine_id, nieuw),
            )
        controleer(conn, bij_toekennen=delta > 0)


def toekenning_aanvullen(conn, klant_id, wine_id):
    """Kent van een vaste wijn in één keer toe wat de klant nog nodig heeft,
    zover de pool van zijn via het toelaat."""
    klant_id, wine_id = _int(klant_id, "Klant", 1), _int(wine_id, "Wijn", 1)
    with tx(conn):
        k = klant(conn, klant_id)
        if k is None:
            _bestaande_klant(conn, klant_id)  # geeft de melding
        st = next((x for x in k["vast_status"] if x["wijn"]["id"] == wine_id), None)
        open_ = st["open"] if st else 0
        vrij = next((p["vrij"] for p in pool(conn, k["k"]["via"]) if p["wijn"]["id"] == wine_id), 0)
        n = min(open_, vrij)
        if n <= 0:
            raise ValidatieFout(f"Niets aan te vullen voor {k['k']['naam']}.")
        toekenning_wijzig(conn, klant_id, wine_id, n)
