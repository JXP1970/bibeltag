# Mein Bibeltag

Eine schlichte Andachts-App für den täglichen Bibelgebrauch – im Stil der
Württembergischen Landeskirche.

**Live:** https://jxp1970.github.io/bibeltag/

## Was die App zeigt

- **Die Losungen** der Herrnhuter Brüdergemeine: Losungsvers (AT) und
  Lehrtext (NT) des Tages – immer beide zusammen
- **Tageslese** nach der Ökumenischen Bibellese (ÖAB)
- **Dieser Sonntag im Kirchenjahr**: Wochenspruch, Predigttext (nach der
  Revidierten Perikopenordnung) und Wochenpsalm
- **Meine Gedanken**: eigener Notizbereich, nur im Browser gespeichert
- Schriftgröße und Hell-/Dunkel-Modus

Bibeltext: **Lutherbibel 2017**.

## Wie die Texte hineinkommen

Alle Texte liegen fertig in zwei Dateien – [`daten.json`](daten.json)
(Tageslesen und Sonntage) und [`losungen.json`](losungen.json) (Losungen).
Die App lädt beide einmal und sucht sich daraus den heutigen Tag heraus.

**Das bedeutet: keine tägliche Aktualisierung, kein Sprachmodell, keine
laufenden Kosten.** Die App ist reines HTML und läuft allein im Browser.

### Nachfüllen

Wenn der Vorrat zur Neige geht, weist die App oben darauf hin. Dann im Repo
unter *Actions* den Workflow **„Lesepläne holen"** starten
([build-data.yml](.github/workflows/build-data.yml)) – er holt die Texte von
die-bibel.de und schreibt sie in `daten.json`. Der Workflow läuft zusätzlich
automatisch einmal im Jahr am 1. November.

Optional lassen sich Start- und Enddatum angeben; ohne Angabe wird ab heute
für 400 Tage nachgefüllt. Bereits vorhandene Tage werden übersprungen, ein
abgebrochener Lauf kann also einfach wiederholt werden.

Lokal geht es auch:

```bash
pip install playwright && playwright install chromium
python scripts/build_data.py --von 2026-07-28 --bis 2027-12-31
```

### Losungen nachfüllen

Darum kümmert sich der Workflow **„Losungen holen"**
([build-losungen.yml](.github/workflows/build-losungen.yml)) von selbst: Er
läuft am 2. jedes Monats, holt von losungen.de das laufende und – sobald
veröffentlicht (in der Regel im Herbst) – das kommende Jahr und committet nur,
wenn sich etwas geändert hat. Von Hand geht es auch:

```bash
python scripts/build_losungen.py          # laufendes + kommendes Jahr
```

## Quellen und Rechte

Bibeltext und Leseordnung stammen von
[die-bibel.de](https://www.die-bibel.de/leseplaene):

- Tageslese: `/leseplaene/oeab-leseplan/oeab-<JJJJ-MM-TT>`
- Sonntag: `/leseplaene/predigttexte/predigttext-<JJJJ-MM-TT>`

Die Losungen stammen von [losungen.de](https://www.losungen.de/download/)
(ZIP je Jahrgang mit XML-Datei).

Lutherbibel 2017 © Deutsche Bibelgesellschaft, Stuttgart.
„Die Losungen" © Evangelische Brüder-Unität – Herrnhuter Brüdergemeine
([herrnhuter.de](https://www.herrnhuter.de)), genutzt nach deren
Nutzungsbedingungen (Stand November 2023): kostenlose, nichtkommerzielle
Website, Losungsvers und Lehrtext immer gemeinsam, Wortlaut und
Stellenangaben unverändert, nur laufendes und benachbartes Jahr,
sichtbarer Copyright-Hinweis im Fuß der Seite.

Für den persönlichen, andächtlichen Gebrauch.
