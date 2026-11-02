"""Berekeningen en mutaties. Alles wat getallen afleidt of invoer valideert zit hier."""

import math
from collections import defaultdict

from db import tx

CATEGORIEEN = {
    "basis": "Basis",
    "iets_luxer": "Iets luxer",
    "luxe": "Luxe",
}
ZOEK_CATEGORIEEN = ["iets_luxer", "luxe"]  # tijdens de trip te kopen
KOSTEN_VERDELINGEN = {
    "bestellers": "Reiskosten",
    "personen": "Eigen kosten",
}


class ValidatieFout(Exception):
    pass


# --- lezen -------------------------------------------------------------------

def vias(conn):
    return conn.execute("SELECT * FROM via ORDER BY volgorde").fetchall()


def wijnen(conn, vast=None):
    sql, args = "SELECT * FROM wine", ()
    if vast is not None:
        sql, args = sql + " WHERE is_vast = ?", (1 if vast else 0,)
    return conn.execute(sql + " ORDER BY is_vast DESC, CASE WHEN is_vast THEN id END, categorie, naam COLLATE NOCASE", args).fetchall()


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
    totaal = {c: _samen(r["cats"][c] for r in per_via) for c in ZOEK_CATEGORIEEN}
    totaal_vast = [{"wijn": w, **_samen(r["vast"][i] for r in per_via)} for i, w in enumerate(vaste)]
    samen = [*totaal.values(), *totaal_vast]
    return {
        "per_via": per_via,
        "totaal": totaal,
        "totaal_vast": totaal_vast,
        "nog_kopen": sum(s["nog"] for s in samen),
        "te_veel": sum(s["overschot"] for s in samen),
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
        sql + " ORDER BY (SELECT volgorde FROM via WHERE code = klant.via), naam COLLATE NOCASE", args
    ).fetchall()

    vast = defaultdict(dict)
    for r in conn.execute("SELECT * FROM klant_vast"):
        vast[r["klant_id"]][r["wine_id"]] = r["aantal"]

    tks = defaultdict(list)
    for r in conn.execute(
        """SELECT t.klant_id, t.aantal, w.id wine_id, w.naam, w.categorie
           FROM toekenning t JOIN wine w ON w.id = t.wine_id ORDER BY w.naam COLLATE NOCASE"""
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

    uit = []
    for a in bonnen:
        rs = regels[a["id"]]
        zonder_prijs = sum(1 for x in rs if x["r"]["prijs"] is None)
        ontbreekt = [] if a["betaald_door"] else ["betaler ontbreekt"]
        if zonder_prijs:
            ontbreekt.append(f"{zonder_prijs} zonder prijs")
        uit.append({"a": a, "regels": rs, "totaal": sum(x["r"]["aantal"] for x in rs),
                    "cent": sum(_cent(x["r"]["prijs"], x["r"]["aantal"]) for x in rs
                                if x["r"]["prijs"] is not None),
                    "ontbreekt": ontbreekt})
    return uit


def kosten(conn, kosten_id=None):
    """Overige aankopen met deelnemers en wat er nog ontbreekt, oudste eerst."""
    deelnemers = defaultdict(list)
    for r in conn.execute(
        """SELECT d.kosten_id, d.via FROM kosten_deelnemer d
           JOIN via ON via.code = d.via ORDER BY via.volgorde"""
    ):
        deelnemers[r["kosten_id"]].append(r["via"])
    sql, args = "SELECT * FROM kosten", ()
    if kosten_id is not None:
        sql, args = sql + " WHERE id = ?", (kosten_id,)
    uit = []
    for k in conn.execute(sql + " ORDER BY id", args):
        ontbreekt = [] if k["bedrag"] is not None else ["bedrag ontbreekt"]
        if not k["betaald_door"]:
            ontbreekt.append("betaler ontbreekt")
        uit.append({"k": k, "deelnemers": deelnemers[k["id"]], "ontbreekt": ontbreekt})
    return uit


# --- verrekening -------------------------------------------------------------
# Alle bedragen in hele centen, zodat de standen exact optellen.

def _cent(bedrag, aantal=1):
    return round(bedrag * aantal * 100)


def _verdeel(cent, gewichten):
    """{sleutel: gewicht} → {sleutel: centen} naar rato; telt exact op (grootste rest)."""
    totaal = sum(gewichten.values())
    if not totaal:
        return {k: 0 for k in gewichten}
    exact = {k: cent * g / totaal for k, g in gewichten.items()}
    uit = {k: int(x) for k, x in exact.items()}
    rest = cent - sum(uit.values())
    for k in sorted(exact, key=lambda k: exact[k] - uit[k], reverse=True)[:rest]:
        uit[k] += 1
    return uit


def verrekening(conn):
    """Stand per via: wijn + kosten tegenover wat de via betaald heeft. Saldo > 0: krijgt terug.
    Ontbrekende prijzen en bedragen tellen niet mee.
    kosten: aandeel in de posten voor alle bestellers (naar rato van de wijn), doorberekend.
    eigen: aandeel in de eigen kosten (per post gelijk over de deelnemers).
    doorberekend: wat een via aan zijn bestellingen kan doorberekenen.
    onbekende_betaler: wel verdeeld, maar nog bij niemand betaald.
    onverdeeld: kosten voor de bestellers zolang er geen wijn met prijs is om ze over te verdelen.
    De saldi plus onbekende_betaler min onverdeeld zijn samen precies 0."""
    bonnen, posten, alle = aankopen(conn), kosten(conn), vias(conn)
    wijn = {v["code"]: 0 for v in alle}
    betaald = dict(wijn)
    onbekend = 0
    for b in bonnen:
        for x in b["regels"]:
            if x["r"]["prijs"] is not None:
                for via, c in _verdeel(_cent(x["r"]["prijs"], x["r"]["aantal"]), x["verdeling"]).items():
                    wijn[via] += c
        if b["a"]["betaald_door"]:
            betaald[b["a"]["betaald_door"]] += b["cent"]
        else:
            onbekend += b["cent"]

    voor_bestellers = 0
    eigen = defaultdict(int)
    for p in posten:
        if p["k"]["bedrag"] is None:
            continue
        c = _cent(p["k"]["bedrag"])
        if p["k"]["verdeling"] == "personen":
            for via, n in _verdeel(c, {d: 1 for d in p["deelnemers"]}).items():
                eigen[via] += n
        else:
            voor_bestellers += c
        if p["k"]["betaald_door"]:
            betaald[p["k"]["betaald_door"]] += c
        else:
            onbekend += c

    aandeel = _verdeel(voor_bestellers, wijn)
    per_via = []
    for v in alle:
        c = v["code"]
        doorberekend = wijn[c] + aandeel[c]
        te_betalen = doorberekend + eigen.get(c, 0)
        per_via.append({"via": v, "wijn": wijn[c], "kosten": aandeel[c], "eigen": eigen.get(c, 0),
                        "doorberekend": doorberekend, "te_betalen": te_betalen,
                        "betaald": betaald[c], "saldo": betaald[c] - te_betalen})
    waarschuwingen = [
        {"tekst": f"{b['a']['omschrijving'] or 'Aankoop'}: {', '.join(b['ontbreekt'])}", "aankoop_id": b["a"]["id"],
         "raakt_bedragen": any(x["r"]["prijs"] is None for x in b["regels"])}
        for b in bonnen if b["ontbreekt"]
    ] + [
        {"tekst": f"{p['k']['omschrijving']}: {', '.join(p['ontbreekt'])}", "kosten_id": p["k"]["id"],
         "raakt_bedragen": p["k"]["bedrag"] is None}
        for p in posten if p["ontbreekt"]
    ]
    return {"per_via": per_via, "kosten": posten, "onbekende_betaler": onbekend,
            "onverdeeld": voor_bestellers - sum(aandeel.values()), "waarschuwingen": waarschuwingen,
            "totaal": {s: sum(r[s] for r in per_via) for s in ("wijn", "kosten", "eigen", "te_betalen", "betaald")}}


def klant_bedragen(conn, via):
    """Wat elke bestelling van een via kost. Toegekende flessen tellen voor de gemiddelde prijs
    van die wijn binnen de via; de rest staat op 'vrij'. Samen precies het totaal van de via.
    ontbreekt: wat nog ingevuld moet worden. Zolang dat niet leeg is, zijn de bedragen niet definitief:
    elke prijs verschuift via de reiskosten de bedragen van iedereen, ook bij andere via's."""
    vr = verrekening(conn)
    stand = next(r for r in vr["per_via"] if r["via"]["code"] == via)
    gemiddeld = {}
    for r in conn.execute(
        """SELECT r.wine_id, SUM(v.aantal * r.prijs) som, SUM(v.aantal) met
           FROM regel_verdeling v JOIN aankoop_regel r ON r.id = v.regel_id
           WHERE v.via = ? AND r.prijs IS NOT NULL GROUP BY r.wine_id""",
        (via,),
    ):
        gemiddeld[r["wine_id"]] = r["som"] / r["met"]

    ks = klanten(conn, via)
    gewichten = {k["k"]["id"]: sum(t["aantal"] * gemiddeld.get(t["wine_id"], 0) for t in k["toekenningen"])
                 for k in ks}
    p = pool(conn, via)
    gewichten[None] = sum(x["vrij"] * gemiddeld.get(x["wijn"]["id"], 0) for x in p)
    wijn, aandeel = _verdeel(stand["wijn"], gewichten), _verdeel(stand["kosten"], gewichten)

    def rij(sleutel):
        return {"wijn": wijn[sleutel], "kosten": aandeel[sleutel], "totaal": wijn[sleutel] + aandeel[sleutel]}

    rijen = []
    for k in ks:
        open_ = sum(max(s["open"], 0) for s in k["vast_status"]) + sum(max(n, 0) for n in k["open"].values())
        rijen.append({"k": k["k"], "flessen": sum(t["aantal"] for t in k["toekenningen"]),
                      "open": open_, **rij(k["k"]["id"])})
    return {"rijen": rijen, "vrij": {"flessen": sum(x["vrij"] for x in p), **rij(None)},
            "stand": stand, "ontbreekt": [w for w in vr["waarschuwingen"] if w["raakt_bedragen"]]}


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


def _prijs(v, veld="Prijs"):
    if v in (None, "") or not str(v).strip():
        return None
    try:
        bedrag = float(str(v).replace(",", "."))
    except ValueError:
        raise ValidatieFout(f"{veld} is geen geldig getal.")
    if not math.isfinite(bedrag):
        raise ValidatieFout(f"{veld} is geen geldig getal.")
    if bedrag < 0:
        raise ValidatieFout(f"{veld} mag niet negatief zijn.")
    return bedrag


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
        raise ValidatieFout("Deze bestelling bestaat niet (meer). Laad de pagina opnieuw.")


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


def klant_betaald(conn, klant_id, betaald):
    """Afvinken dat iemand betaald heeft; raakt de versie niet, zodat een open bewerkformulier blijft werken."""
    with tx(conn):
        _bestaande_klant(conn, klant_id)
        conn.execute("UPDATE klant SET betaald=? WHERE id=?", (1 if betaald else 0, klant_id))


def via_verrekend(conn, via, verrekend):
    """Afvinken dat een via zijn saldo verrekend heeft."""
    with tx(conn):
        _via(conn, via)
        conn.execute("UPDATE via SET verrekend=? WHERE code=?", (1 if verrekend else 0, via))


# --- aankopen ----------------------------------------------------------------

def aankoop_opslaan(conn, aankoop_id, omschrijving, regels, versie=None, betaald_door=None):
    """Slaat een bon op. regels: [{naam, categorie, prijs, verdeling: {via: n}}].
    Wijnen worden op naam herkend (hoofdletterongevoelig) en zo nodig aangemaakt.
    Met versie (uit het formulier) mislukt het als de bon intussen is gewijzigd,
    bijvoorbeeld door een overdracht. betaald_door mag leeg blijven en later worden ingevuld."""
    schoon = [_regel(r) for r in regels]
    if not schoon:
        raise ValidatieFout("Vul minimaal één wijn in.")
    _geen_tegenstrijdige_categorie(schoon)
    versie = _versie(versie)
    omschrijving = (omschrijving or "").strip()
    betaald_door = betaald_door or None
    with tx(conn):
        for via in {v for r in schoon for v in r["verdeling"]} | ({betaald_door} if betaald_door else set()):
            _via(conn, via)
        if aankoop_id:
            sql, args = ("UPDATE aankoop SET omschrijving=?, betaald_door=?, versie = versie + 1 WHERE id=?",
                         [omschrijving, betaald_door, aankoop_id])
            if versie is not None:
                sql, args = sql + " AND versie=?", args + [versie]
            if not conn.execute(sql, args).rowcount:
                raise _intussen_gewijzigd(f"Aankoop {omschrijving}".strip())
            conn.execute("DELETE FROM aankoop_regel WHERE aankoop_id=?", (aankoop_id,))
        else:
            aankoop_id = conn.execute(
                "INSERT INTO aankoop (omschrijving, betaald_door) VALUES (?, ?)", (omschrijving, betaald_door)
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


# --- overige aankopen -------------------------------------------------------------

def kosten_opslaan(conn, kosten_id, omschrijving, bedrag, betaald_door, versie=None, verdeling="bestellers",
                   deelnemers=()):
    """Nieuwe post (kosten_id leeg) of bestaande bijwerken; het bedrag mag leeg blijven.
    deelnemers: wie meedoet aan eigen kosten; bij reiskosten genegeerd."""
    omschrijving = (omschrijving or "").strip()
    if not omschrijving:
        raise ValidatieFout("Omschrijving is verplicht.")
    bedrag = _prijs(bedrag, "Bedrag")
    if not betaald_door:
        raise ValidatieFout(f"{omschrijving}: kies wie het betaald heeft.")
    if verdeling not in KOSTEN_VERDELINGEN:
        raise ValidatieFout(f"{omschrijving}: kies een categorie.")
    if verdeling != "personen":
        deelnemers = ()
    elif not deelnemers:
        raise ValidatieFout(f"{omschrijving}: kies wie er meedoet.")
    versie = _versie(versie)
    with tx(conn):
        for via in {betaald_door, *deelnemers}:
            _via(conn, via)
        if kosten_id:
            sql, args = ("UPDATE kosten SET omschrijving=?, bedrag=?, betaald_door=?, verdeling=?, versie = versie + 1 WHERE id=?",
                         [omschrijving, bedrag, betaald_door, verdeling, kosten_id])
            if versie is not None:
                sql, args = sql + " AND versie=?", args + [versie]
            if not conn.execute(sql, args).rowcount:
                raise _intussen_gewijzigd(omschrijving)
        else:
            kosten_id = conn.execute(
                "INSERT INTO kosten (omschrijving, bedrag, betaald_door, verdeling) VALUES (?, ?, ?, ?)",
                (omschrijving, bedrag, betaald_door, verdeling),
            ).lastrowid
        conn.execute("DELETE FROM kosten_deelnemer WHERE kosten_id=?", (kosten_id,))
        conn.executemany("INSERT INTO kosten_deelnemer (kosten_id, via) VALUES (?, ?)",
                         [(kosten_id, via) for via in set(deelnemers)])
    return kosten_id


def kosten_verwijderen(conn, kosten_id):
    with tx(conn):
        conn.execute("DELETE FROM kosten WHERE id=?", (kosten_id,))


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
