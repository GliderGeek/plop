# Plop

Een webapp om de champagne te beheren die we tijdens onze trip kopen voor onszelf, vrienden en familie. De app vervangt de Excelsheet die we hiervoor gebruikten. Grootste wijziging is dat je beter apart kan werken aan de bestellingen die via jou zijn gedaan. De app ondersteunt verschillende fases:
1. Voorbereiding: registreren van bestellingen
2. Tijdens: aankopen invoeren en bijhouden welke openstaande wensen er nog zijn
3. Achteraf: kosten verrekenen

## TODO
- code opschonen en open-source zetten
  - licentie erop
- aanvinken fixen op mobiel

## Techniek

- Python + Flask + Jinja2-templates
- htmx
- SQLite

Meer info over de technische setup in `docs.md`

## Ontwikkelen

1. Installeren

```sh
uv sync
```

2. `.env` aanmaken en vullen (zie `.env.example` voor de keys)

3. De app starten

```sh
uv run flask --app app run --debug
```

4. Bekijken op http://localhost:5000. 

5. Tests draaien:

```sh
uv run pytest
```
