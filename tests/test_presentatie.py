import presentatie
from presentatie import cent, euro, getal, kortnaam


def test_bedragen_en_getallen():
    assert cent(1250) == "€ 12,50"
    assert euro(3) == "€ 3,00"
    assert euro(None) == ""
    assert getal(26.0) == "26"
    assert getal(23.5) == "23,5"
    assert getal(None) == ""


def test_kortnaam():
    assert kortnaam("Pertois tradition") == "Pertois"
    assert kortnaam("Dagonet ½") == "Dagonet ½"


def jaar(jaar, **flessen):
    f = dict.fromkeys(presentatie.SOORTEN, 0) | flessen
    return {"jaar": jaar, "flessen": f, "totaal": sum(f.values())}


def test_grafiek_schaal():
    g = presentatie.kolommen_grafiek([jaar(2024, basis=120, luxe=30), jaar(2025, basis=410)])
    assert [t["v"] for t in g["ticks"]] == [0, 100, 200, 300, 400, 500]
    assert [len(k["delen"]) for k in g["kolommen"]] == [2, 1]  # lege soorten tellen niet mee
    g = presentatie.kolommen_grafiek([jaar(2025, basis=650)])
    assert [t["v"] for t in g["ticks"]] == [0, 200, 400, 600, 800]


def test_grafiek_zonder_jaren():
    g = presentatie.kolommen_grafiek([])
    assert g["kolommen"] == [] and [t["v"] for t in g["ticks"]] == [0, 100]
