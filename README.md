# Mein Bibeltag

Eine schlichte, eigenständige Andachts-App (eine einzige `index.html`, ohne
externe Abhängigkeiten) für den täglichen Bibelgebrauch – im Stil der
Württembergischen Landeskirche.

**Live:** https://jxp1970.github.io/bibeltag/

## Was die App zeigt

- **Tageslese** nach der Ökumenischen Bibellese (ÖAB)
- **Dieser Sonntag im Kirchenjahr**: Wochenspruch, Predigttext (nach der
  Revidierten Perikopenordnung), Wochenpsalm und ein kurzer Impuls
- Umschalter **Luther 2017 ↔ Schlachter 2000**
- Schriftgröße und Hell-/Dunkel-Modus

## Automatische Aktualisierung

Der gesamte Inhalt steckt im `DATA`-Block in `index.html` zwischen den Markern

```
/* ==== DATA START ... */
const DATA = { ... };
/* ==== DATA END ==== */
```

Ein **GitHub-Actions-Workflow** ([.github/workflows/daily-update.yml](.github/workflows/daily-update.yml))
läuft täglich um 03:00 UTC, ruft über die Anthropic-API ([scripts/generate_data.py](scripts/generate_data.py))
Tageslese, Sonntagstexte, Bibeltext in beiden Übersetzungen und einen Impuls ab,
schreibt den `DATA`-Block neu, committet und pusht. GitHub Pages veröffentlicht
die aktualisierte Seite dann automatisch. Der Workflow läuft auf GitHubs
Servern — unabhängig davon, ob ein PC an ist.

**Einrichtung (einmalig):** In den Repo-Einstellungen unter
*Settings → Secrets and variables → Actions* ein Secret `ANTHROPIC_API_KEY`
mit dem eigenen Anthropic-API-Schlüssel anlegen. Manuell auslösen:
*Actions → Täglicher Bibeltag-Update → Run workflow*.

## Hinweis

Bibeltexte: Lutherbibel 2017 © Deutsche Bibelgesellschaft, Stuttgart ·
Schlachter 2000 © Genfer Bibelgesellschaft. Für den persönlichen,
andächtlichen Gebrauch.
