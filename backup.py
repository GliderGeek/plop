"""Snapshot van alle data als Excel-werkboek."""

from io import BytesIO

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font

import logic
from logic import CATEGORIEEN


def _tabblad(wb, titel, kop, rijen):
    ws = wb.create_sheet(titel)
    ws.append(kop)
    for cel in ws[1]:
        cel.font = Font(bold=True)
    ws.freeze_panes = "A2"
    for rij in rijen:
        ws.append([ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v for v in rij])
        for cel in ws[ws.max_row]:
            if cel.data_type == "f":  # vrije tekst met '=' vooraan is geen formule
                cel.data_type = "s"
    for kolom in ws.columns:
        breedte = max(len(str(c.value or "")) for c in kolom)
        ws.column_dimensions[kolom[0].column_letter].width = min(breedte + 2, 50)


def werkboek(conn, jaar, tijdstip):
    """Het werkboek van één jaar als bytes: bestellingen, toekenningen, aankopen, kosten en verrekening op moment `tijdstip`."""
    vaste = logic.wijnen(conn, jaar, vast=True)
    vias = [v["code"] for v in logic.vias(conn, jaar)]
    klanten = logic.klanten(conn, jaar)

    wb = Workbook()
    wb.remove(wb.active)
    _tabblad(wb, "Bestellingen",
             ["Via", "Voor wie", *(w["naam"] for w in vaste), "Iets luxer", "Luxe", "Totaal", "Aantekening", "Betaald"],
             ([k["k"]["via"], k["k"]["naam"], *(k["vast"].get(w["id"], 0) for w in vaste),
               k["nodig"]["iets_luxer"], k["nodig"]["luxe"], k["totaal"], k["k"]["aantekening"],
               "ja" if k["k"]["betaald"] else "nee"]
              for k in klanten))
    _tabblad(wb, "Toekenningen", ["Via", "Voor wie", "Wijn", "Categorie", "Aantal"],
             ([k["k"]["via"], k["k"]["naam"], t["naam"], CATEGORIEEN[t["categorie"]], t["aantal"]]
              for k in klanten for t in k["toekenningen"]))
    _tabblad(wb, "Aankopen",
             ["Bon", "Omschrijving", "Datum", "Betaald door", "Wijn", "Categorie", "Aantal", "Prijs", *vias],
             ([b["a"]["id"], b["a"]["omschrijving"], b["a"]["aangemaakt_op"], b["a"]["betaald_door"], x["r"]["naam"],
               CATEGORIEEN[x["r"]["categorie"]], x["r"]["aantal"], x["r"]["prijs"],
               *(x["verdeling"].get(via, 0) for via in vias)]
              for b in logic.aankopen(conn, jaar) for x in b["regels"]))
    vr = logic.verrekening(conn, jaar)
    _tabblad(wb, "Kosten", ["Omschrijving", "Bedrag", "Betaald door", "Categorie", "Deelnemers"],
             ([p["k"]["omschrijving"], p["k"]["bedrag"], p["k"]["betaald_door"], logic.KOSTEN_VERDELINGEN[p["k"]["verdeling"]],
               ", ".join(p["deelnemers"])] for p in vr["kosten"]))
    _tabblad(wb, "Verrekening", ["Via", "Champagne", "Reiskosten", "Eigen kosten", "Te betalen", "Betaald", "Saldo", "Verrekend"],
             ([r["via"]["code"], *(r[s] / 100 for s in ("wijn", "kosten", "eigen", "te_betalen", "betaald", "saldo")),
               "ja" if r["via"]["verrekend"] else "nee"]
              for r in vr["per_via"]))
    _tabblad(wb, "Snapshot", ["Jaar", "Gemaakt op"], [[jaar, tijdstip.strftime("%Y-%m-%d %H:%M:%S")]])

    uit = BytesIO()
    wb.save(uit)
    return uit.getvalue()
