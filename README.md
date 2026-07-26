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

Eine **geplante Claude-Code-Cloud-Routine** schreibt diesen Block täglich neu
(Tageslese, Sonntagstexte, Bibeltext in beiden Übersetzungen, Impuls),
committet und pusht. GitHub Pages veröffentlicht die aktualisierte Seite
dann automatisch.

Routinen verwalten: https://claude.ai/code/routines

## Hinweis

Bibeltexte: Lutherbibel 2017 © Deutsche Bibelgesellschaft, Stuttgart ·
Schlachter 2000 © Genfer Bibelgesellschaft. Für den persönlichen,
andächtlichen Gebrauch.
