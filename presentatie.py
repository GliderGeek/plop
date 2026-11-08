"""Weergave: getallen en namen voor de templates en de geometrie van de grafiek. Geen Flask, geen database."""

from logic import SOORTEN


def euro(v):
    return "" if v is None else ("€ %.2f" % v).replace(".", ",")


def cent(v):
    """1250 → '€ 12,50'."""
    return euro(v / 100)


def getal(v):
    """26.0 → '26', 23.5 → '23,5' (voor invoervelden)."""
    return "" if v is None else ("%g" % v).replace(".", ",")


def kortnaam(naam):
    """'Pertois tradition' → 'Pertois'; 'Dagonet ½' blijft."""
    return naam.replace(" tradition", "")


FILTERS = {"euro": euro, "cent": cent, "getal": getal, "kortnaam": kortnaam}


def kolommen_grafiek(stat, hoogte=180, boven=28, breedte=24, gat=2):
    """Gestapelde kolommen per jaar als SVG-geometrie. x in procenten (de grafiek rekt mee),
    y in px. Elke kolom: segmenten van onder naar boven, alleen de bovenste met ronde hoeken."""
    top = max((r["totaal"] for r in stat), default=0)
    stap = 100 if top <= 500 else 200
    schaal_max = max(stap, -(-top // stap) * stap)
    y = lambda v: boven + hoogte * (1 - v / schaal_max)
    h = breedte / 2
    kolommen = []
    for i, r in enumerate(stat):
        delen, onder = [], 0
        zichtbaar = [s for s in SOORTEN if r["flessen"][s]]
        for s in zichtbaar:
            n = r["flessen"][s]
            yt, yb = y(onder + n), y(onder) - (gat if onder else 0)
            if s == zichtbaar[-1] and yb - yt > 4:  # bovenkant rond, onderkant recht
                d = f"M{-h},{yb:.1f}V{yt + 4:.1f}A4,4 0 0 1 {-h + 4},{yt:.1f}H{h - 4}A4,4 0 0 1 {h},{yt + 4:.1f}V{yb:.1f}Z"
            else:
                d = f"M{-h},{yb:.1f}V{yt:.1f}H{h}V{yb:.1f}Z"
            delen.append({"soort": s, "n": n, "d": d})
            onder += n
        kolommen.append({"r": r, "x": 12 + (i + 0.5) * 88 / len(stat), "delen": delen, "top": y(onder)})
    ticks = [{"v": v, "y": y(v)} for v in range(0, schaal_max + 1, stap)]
    return {"kolommen": kolommen, "ticks": ticks, "basis": y(0), "hoogte": boven + hoogte + 32}
