#!/usr/bin/env python3
"""Holt die Losungen der Herrnhuter Brüdergemeine und schreibt sie in losungen.json.

Quelle: https://www.losungen.de/download/ – dort liegt je Jahrgang eine
ZIP-Datei mit einer XML-Datei (Losungsvers aus dem Alten Testament,
Lehrtext aus dem Neuen Testament, jeweils mit Stellenangabe).

Nutzungsbedingungen der Brüder-Unität (Stand November 2023), die dieses
Skript einhält:
  - Es werden ausschließlich die Daten von www.losungen.de verwendet,
    unverändert im Wortlaut und in den Stellenangaben.
  - Bereitgestellt werden höchstens das laufende Jahr sowie das
    vorangehende und das nachfolgende Jahr. Die App braucht nur den
    heutigen Tag, deshalb holt das Skript von sich aus nur das laufende
    und das kommende Jahr; ältere Jahrgänge fallen bei jedem Lauf
    aus losungen.json heraus.
  - Losungsvers und Lehrtext gehören zusammen; Tage, bei denen einer der
    beiden fehlt, werden übersprungen (die App zeigt immer beide).
Der sichtbare Copyright-Hinweis steht im Fuß der App (index.html).

Ein Jahrgang für das kommende Jahr erscheint erst im Lauf des Herbstes.
Fehlt er noch, wird er einfach übersprungen; der nächste Lauf holt ihn nach.

Aufruf:
    python scripts/build_losungen.py [--jahr 2026] [--jahr 2027]
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import date

ZIP_URL = "https://www.losungen.de/fileadmin/media-losungen/download/Losung_{jahr}_XML.zip"

OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "losungen.json")

TIMEOUT = 60


def hole_jahrgang(jahr: int) -> dict:
    """Lädt einen Jahrgang und gibt {JJJJ-MM-TT: Eintrag} zurück."""
    url = ZIP_URL.format(jahr=jahr)
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
            rohdaten = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"  {jahr}: noch nicht veröffentlicht – übersprungen.")
            return {}
        print(f"  {jahr}: nicht abrufbar ({e}).")
        return {}
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"  {jahr}: nicht abrufbar ({e}).")
        return {}

    with zipfile.ZipFile(io.BytesIO(rohdaten)) as z:
        namen = [n for n in z.namelist() if n.lower().endswith(".xml")]
        if not namen:
            print(f"  {jahr}: keine XML-Datei im Archiv.")
            return {}
        xml_text = z.read(namen[0]).decode("utf-8-sig")

    wurzel = ET.fromstring(xml_text)
    tage: dict[str, dict] = {}
    for eintrag in wurzel.findall("Losungen"):
        def feld(name: str) -> str:
            el = eintrag.find(name)
            return (el.text or "").strip() if el is not None else ""

        datum = feld("Datum")[:10]          # "2026-08-16T00:00:00.000"
        losung = feld("Losungstext")
        lehrtext = feld("Lehrtext")
        if len(datum) != 10 or not losung or not lehrtext:
            continue
        tage[datum] = {
            "losung": {"text": losung, "vers": feld("Losungsvers")},
            "lehrtext": {"text": lehrtext, "vers": feld("Lehrtextvers")},
            "sonntag": feld("Sonntag"),
            "wtag": feld("Wtag"),
        }

    print(f"  {jahr}: {len(tage)} Tage geholt.")
    return tage


def lade_bestand(pfad: str) -> dict:
    if os.path.exists(pfad):
        try:
            with open(pfad, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            print("Vorhandene losungen.json unlesbar – wird neu aufgebaut.")
    return {}


def main() -> int:
    heute = date.today()
    p = argparse.ArgumentParser()
    p.add_argument("--jahr", type=int, action="append", dest="jahre",
                   help="Jahrgang (mehrfach möglich). Vorgabe: laufendes "
                        "Jahr und Folgejahr.")
    p.add_argument("--out", default=os.path.abspath(OUT_PATH))
    args = p.parse_args()

    jahre = sorted(set(args.jahre or [heute.year, heute.year + 1]))

    print("Losungen der Herrnhuter Brüdergemeine holen")
    print(f"  Jahrgänge: {', '.join(str(j) for j in jahre)}")

    tage: dict[str, dict] = {}
    geholt: list[int] = []
    for jahr in jahre:
        neu = hole_jahrgang(jahr)
        if neu:
            geholt.append(jahr)
            tage.update(neu)

    if not tage:
        print("Kein Jahrgang abrufbar – bestehende Datei bleibt unverändert.")
        return 1

    alt = lade_bestand(args.out)
    if alt.get("tage") == tage and alt.get("jahre") == geholt:
        print("Keine Änderung – Datei bleibt unverändert.")
        return 0

    daten = {"erzeugt": heute.isoformat(), "jahre": geholt, "tage": tage}
    tmp = args.out + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(daten, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, args.out)

    groesse = os.path.getsize(args.out) / 1024
    print()
    print(f"Fertig: {len(tage)} Tage ({min(tage)} bis {max(tage)})")
    print(f"  Datei: {args.out} ({groesse:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
