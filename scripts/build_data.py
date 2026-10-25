#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Holt die Lesepläne für einen Zeitraum und schreibt sie in daten.json.

Einmalig (bzw. wenn der Vorrat zur Neige geht) auszuführen. Danach braucht
die App keine Aktualisierung mehr: alle Texte liegen fertig in der Datei,
die App sucht sich nur noch den heutigen Tag heraus.

Quellen (alle frei zugänglich, kein Login, kein Playwright):
  - Tageslese : die-bibel.de  /leseplaene/oeab-leseplan/oeab-<JJJJ-MM-TT>
  - Sonntage  : Name+Datum aus der freien Predigttexte-Übersicht,
                Bibelstellen aus der festen Tabelle scripts/perikopen.json,
                der Bibeltext (Lutherbibel 2017) aus dem Bibel-Reader
                die-bibel.de/bibel/LU17/<BUCH>.<KAPITEL>

Hintergrund: die-bibel.de hat seinen Sonntags-Leseplan Mitte 2025 hinter
eine Anmeldung gelegt. Deshalb kommen die Bibelstellen der Sonntage jetzt
aus der mitgelieferten Perikopen-Tabelle (perikopen.json, neu erzeugbar mit
scripts/build_perikopen.py), während der eigentliche Bibeltext weiterhin
aus dem frei lesbaren Bibel-Reader geholt wird. Die Perikopenreihe (I–VI)
wird aus dem Kirchenjahr berechnet.

Bereits vorhandene Einträge in daten.json werden übersprungen, ein
abgebrochener Lauf kann also jederzeit wiederholt werden. Sonntage, für die
keine Tabellenzeile existiert, werden NICHT stillschweigend übersprungen,
sondern am Ende deutlich aufgelistet (das hat früher einzelne Lücken
verursacht).

Aufruf:
    python scripts/build_data.py [--von JJJJ-MM-TT] [--bis JJJJ-MM-TT]
"""

from __future__ import annotations

import argparse
import gzip
import html as _html
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta

OEAB_URL = "https://www.die-bibel.de/leseplaene/oeab-leseplan/oeab-{d}"
READER_URL = "https://www.die-bibel.de/bibel/LU17/{book}.{chap}"
OVERVIEW_URL = "https://www.die-bibel.de/leseplaene/predigttexte?tab={tab}"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(SCRIPT_DIR, "..", "daten.json")
PERIKOPEN_PATH = os.path.join(SCRIPT_DIR, "perikopen.json")

MON = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
       "August", "September", "Oktober", "November", "Dezember"]
MON_NR = {m: i + 1 for i, m in enumerate(MON)}

REIHEN = ["I", "II", "III", "IV", "V", "VI"]

CHUNK = 12                 # Tageslesen pro Zwischenspeicherung
LEER_ABSCHNITTE_STOPP = 2  # so viele Durchgänge ohne Treffer -> Ende des Tagesplans

HDR = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.die-bibel.de/",
}


# --------------------------------------------------------------------------- #
#  HTTP / HTML
# --------------------------------------------------------------------------- #
def fetch(url: str) -> str | None:
    """Lädt eine Seite. None bei 404 (kein Eintrag für dieses Datum)."""
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=HDR), timeout=30)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    raw = r.read()
    if r.headers.get("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


def strip_tags(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    s = _html.unescape(s).replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip()


def chapter_verses(html: str, book: str, chap: int) -> dict[int, str]:
    """Verse eines Kapitels aus einer Bibel-Reader-Seite (span.verse, LU17.<BUCH>.<KAP>.<V>)."""
    verses: dict[int, str] = {}
    for m in re.finditer(r'<span\b[^>]*\bclass="([^"]*\bverse\b[^"]*)"[^>]*>', html):
        cm = re.search(r"LU17\.([A-Z0-9]+)\.(\d+)\.(\d+)", m.group(1))
        if not cm or cm.group(1) != book or int(cm.group(2)) != chap:
            continue
        n = int(cm.group(3))
        start = m.end()
        depth, end = 1, start
        for tm in re.finditer(r"<(/?)span\b[^>]*>", html[start:]):
            depth += -1 if tm.group(1) else 1
            if depth == 0:
                end = start + tm.start()
                break
        inner = strip_tags(html[start:end])
        if inner:
            verses[n] = (verses[n] + " " + inner).strip() if n in verses else inner
    return verses


# --------------------------------------------------------------------------- #
#  Kirchenjahr
# --------------------------------------------------------------------------- #
def advent1(year: int) -> date:
    """1. Advent des Kalenderjahres: vierter Sonntag vor dem 25. Dezember."""
    d24 = date(year, 12, 24)
    vierter = d24 - timedelta(days=(d24.weekday() + 1) % 7)  # letzter Sonntag <= 24.12.
    return vierter - timedelta(days=21)


def reihe_fuer(d: date) -> str:
    """Perikopenreihe (I–VI) des Kirchenjahres, in dem d liegt.
    Anker: Kirchenjahr ab 1. Advent 2024 = Reihe I, 2025 = Reihe II, ..."""
    start = d.year if d >= advent1(d.year) else d.year - 1
    return REIHEN[(start - 2024) % 6]


# die-bibel.de-Sonntagsname -> Slug in perikopen.json (= kirchenjahr-evangelisch.de)
ALIAS = {
    "Erntedank": "erntedank",
    "Reformationstag": "reformationstag",
    "Gedenktag der Reformation": "reformationstag",
    "Ewigkeitssonntag": "ewigkeitssonntag",
    "Letzter Sonntag des Kirchenjahres": "ewigkeitssonntag",
    "Vorletzter Sonntag des Kirchenjahres": "vorletzter-sonntag-des-kirchenjahres",
    "Vorletzter Sonntag d. Kj.": "vorletzter-sonntag-des-kirchenjahres",
    "Drittletzter Sonntag des Kirchenjahres": "drittletzter-sonntag-des-kirchenjahres",
    "Drittl.S.d.Kj.": "drittletzter-sonntag-des-kirchenjahres",
    "Palmarum/Palmsonntag": "palmsonntag",
    "Ostersonntag": "ostersonntag",
    "Osternacht": "ostersonntag",
    "Pfingstsonntag": "pfingstsonntag",
    "Trinitatis": "trinitatis",
    "Septuagesimä": "septuagesimae",
    "Sexagesimä": "sexagesimae",
    "Estomihi": "estomihi",
    "Invokavit": "invokavit",
    "Invocavit": "invokavit",
    "Reminiszere": "reminiszere",
    "Okuli": "okuli",
    "Lätare": "laetare",
    "Judika": "judika",
    "Quasimodogeniti": "quasimodogeniti",
    "Miserikordias Domini": "miserikordias-domini",
    "Misericordias Domini": "miserikordias-domini",
    "Jubilate": "jubilate",
    "Kantate": "kantate",
    "Rogate": "rogate",
    "Exaudi": "exaudi",
}


def slugify(name: str) -> str | None:
    n = name.strip()
    if re.search(r"Israel", n, re.I):          # 10. So. n. Trin. = Israelsonntag "Kirche und Israel"
        return "kirche-und-israel"
    n = re.split(r":", n)[0].strip()
    if n in ALIAS:
        return ALIAS[n]
    patterns = [
        (r"(\d+)\.\s*(?:Sonntag|So\.?)\s*(?:nach|n\.?)\s*Trinitatis", "{}-sonntag-nach-trinitatis"),
        (r"(\d+)\.\s*Advent", "{}-advent"),
        (r"(\d+)\.\s*(?:Sonntag|So\.?)\s*(?:nach|n\.?)\s*Epiphanias", "{}-sonntag-nach-epiphanias"),
        (r"(\d+)\.\s*(?:Sonntag|So\.?)\s*(?:nach|n\.?)\s*(?:dem\s*)?Christfest", "{}-sonntag-nach-christfest"),
        (r"(\d+)\.\s*(?:Sonntag|So\.?)\s*vor der Passionszeit", "{}-sonntag-vor-der-passionszeit"),
    ]
    for pat, tmpl in patterns:
        m = re.match(pat, n, re.I)
        if m:
            return tmpl.format(m.group(1))
    if re.match(r"Letzter\s*(?:Sonntag|So\.?)\s*(?:nach|n\.?)\s*Epiphanias", n, re.I):
        return "letzter-sonntag-nach-epiphanias"
    return None


def de_long(d: date) -> str:
    return f"{d.day}. {MON[d.month - 1]} {d.year}"


# --------------------------------------------------------------------------- #
#  Übersicht (Datum -> Name)
# --------------------------------------------------------------------------- #
def overview_rows() -> dict[str, str]:
    """ISO-Datum -> Sonntags-/Feiertagsname aus der freien Predigttexte-Übersicht."""
    seen: dict[str, str] = {}
    for tab in ("upcoming", "archive"):
        html = fetch(OVERVIEW_URL.format(tab=tab))
        if not html:
            continue
        txt = strip_tags(html)
        for m in re.finditer(
                r"(\d{1,2})\.\s+([A-Za-zäöüÄÖÜ]+)\s+(\d{4}):\s*([^|]+?)\s*\|\s*"
                r"\d{1,2}\.\d{1,2}\.\d{4}", txt):
            mon = m.group(2)
            if mon not in MON_NR:
                continue
            iso = date(int(m.group(3)), MON_NR[mon], int(m.group(1))).isoformat()
            seen[iso] = m.group(4).strip()
    return seen


# --------------------------------------------------------------------------- #
#  Bestand laden / speichern
# --------------------------------------------------------------------------- #
def lade_bestand(pfad: str) -> dict:
    if os.path.exists(pfad):
        try:
            with open(pfad, "r", encoding="utf-8") as f:
                d = json.load(f)
            d.setdefault("tage", {})
            d.setdefault("sonntage", {})
            return d
        except (OSError, json.JSONDecodeError):
            print("Vorhandene daten.json unlesbar – wird neu aufgebaut.")
    return {"tage": {}, "sonntage": {}}


def speichere(pfad: str, daten: dict) -> None:
    daten["erzeugt"] = date.today().isoformat()
    tmp = pfad + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(daten, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, pfad)


def tage_zwischen(von: date, bis: date):
    d = von
    while d <= bis:
        yield d
        d += timedelta(days=1)


# --------------------------------------------------------------------------- #
#  Bibeltext holen (mit Kapitel-Cache)
# --------------------------------------------------------------------------- #
class Reader:
    def __init__(self):
        self._cache: dict[str, dict[int, str]] = {}

    def verse(self, book: str, chap: int, nummern: list[int]) -> list[list]:
        key = f"{book}.{chap}"
        if key not in self._cache:
            html = fetch(READER_URL.format(book=book, chap=chap))
            self._cache[key] = chapter_verses(html, book, chap) if html else {}
        kap = self._cache[key]
        return [[n, kap[n]] for n in nummern if n in kap and kap[n]]


# --------------------------------------------------------------------------- #
#  Hauptlauf
# --------------------------------------------------------------------------- #
def main() -> int:
    heute = date.today()
    p = argparse.ArgumentParser()
    p.add_argument("--von", default=heute.isoformat())
    p.add_argument("--bis", default=(heute + timedelta(days=1100)).isoformat())
    p.add_argument("--out", default=os.path.abspath(OUT_PATH))
    args = p.parse_args()

    von = datetime.strptime(args.von, "%Y-%m-%d").date()
    bis = datetime.strptime(args.bis, "%Y-%m-%d").date()
    if bis < von:
        print("Fehler: --bis liegt vor --von")
        return 1

    with open(PERIKOPEN_PATH, "r", encoding="utf-8") as f:
        perikopen = json.load(f)

    daten = lade_bestand(args.out)
    reader = Reader()
    begonnen = time.time()

    offene_tage = [d for d in tage_zwischen(von, bis) if d.isoformat() not in daten["tage"]]
    sonntage = sorted({d - timedelta(days=(d.weekday() + 1) % 7) for d in tage_zwischen(von, bis)})
    offene_sonntage = [d for d in sonntage if d.isoformat() not in daten["sonntage"]]

    print(f"Zeitraum {von} bis {bis}")
    print(f"  Tageslesen zu prüfen : {len(offene_tage)}")
    print(f"  Sonntage zu prüfen   : {len(offene_sonntage)}")

    fehlende_tabelle: list[str] = []

    # ----- Sonntage -------------------------------------------------------- #
    if offene_sonntage:
        plan = overview_rows()
        max_bekannt = max(plan) if plan else None
        treffer = 0
        for d in offene_sonntage:
            iso = d.isoformat()
            if max_bekannt and iso > max_bekannt:
                print(f"  Ende des veröffentlichten Sonntagsplans erreicht (ab {iso}).")
                break
            name = plan.get(iso)
            if not name:
                # Einzellücke (z. B. die laufende Woche fehlt in der Übersicht)
                print(f"  Hinweis: {iso} fehlt in der Übersicht – übersprungen (später erneut versuchen).")
                continue
            slug = slugify(name)
            eintrag = perikopen.get(slug) if slug else None
            if not eintrag:
                fehlende_tabelle.append(f"{iso}  {name!r}  (Slug {slug})")
                continue
            reihe = reihe_fuer(d)
            pred = (eintrag.get("predigt") or {}).get(reihe)
            psal = eintrag.get("psalm")
            if not pred or not pred.get("verses"):
                fehlende_tabelle.append(f"{iso}  {name!r}  (Predigttext Reihe {reihe} fehlt in der Tabelle)")
                continue
            pv = reader.verse(pred["book"], pred["chapter"], pred["verses"])
            sv = reader.verse(psal["book"], psal["chapter"], psal["verses"]) if psal and psal.get("verses") else []
            spruch = eintrag.get("spruch") or {}
            daten["sonntage"][iso] = {
                "name": name,
                "datum": de_long(d),
                "spruch": {"text": spruch.get("text", ""), "cite": spruch.get("cite", "")},
                "predigt": {"ref": pred.get("ref", ""), "verse": pv},
                "psalm": {"ref": (psal or {}).get("ref", ""), "verse": sv},
            }
            treffer += 1
            print(f"  Sonntag {iso} [{reihe}] {name}: Predigt {pred.get('ref')} ({len(pv)} V.), "
                  f"Psalm {(psal or {}).get('ref')} ({len(sv)} V.)")
            speichere(args.out, daten)
        print(f"  Sonntage neu: {treffer}")

    # ----- Tageslesen (ökumenische Bibellese) ------------------------------ #
    leere = 0
    verarbeitet = 0
    for start in range(0, len(offene_tage), CHUNK):
        chunk = offene_tage[start:start + CHUNK]
        treffer = 0
        for d in chunk:
            verarbeitet += 1
            html = fetch(OEAB_URL.format(d=d.isoformat()))
            if not html:
                continue
            refs = [strip_tags(x) for x in re.findall(
                r'class="[^"]*text-grayLight[^"]*"[^>]*>(.*?)</span>', html, re.S)]
            ref = next((r for r in refs if re.search(r"[A-Za-zÄÖÜäöü].*\d+,\d", r)), "")
            verse_map: dict[int, str] = {}
            for m in re.finditer(r'<span\b[^>]*\bclass="([^"]*\bverse\b[^"]*)"[^>]*>', html):
                cm = re.search(r"LU17\.([A-Z0-9]+)\.(\d+)\.(\d+)", m.group(1))
                if not cm:
                    continue
                n = int(cm.group(3))
                s2 = m.end()
                depth, end = 1, s2
                for tm in re.finditer(r"<(/?)span\b[^>]*>", html[s2:]):
                    depth += -1 if tm.group(1) else 1
                    if depth == 0:
                        end = s2 + tm.start()
                        break
                inner = strip_tags(html[s2:end])
                if inner:
                    verse_map[n] = (verse_map[n] + " " + inner).strip() if n in verse_map else inner
            if not ref or not verse_map:
                continue
            daten["tage"][d.isoformat()] = {
                "ref": ref,
                "verse": [[n, verse_map[n]] for n in sorted(verse_map)],
            }
            treffer += 1
        speichere(args.out, daten)
        verstrichen = (time.time() - begonnen) / 60
        print(f"  Tageslesen [{verarbeitet}/{len(offene_tage)}] "
              f"{treffer}/{len(chunk)} Treffer ({verstrichen:.1f} min)")
        leere = 0 if treffer else leere + 1
        if leere >= LEER_ABSCHNITTE_STOPP:
            print("  Ende des veröffentlichten Tagesplans erreicht.")
            break

    speichere(args.out, daten)

    groesse = os.path.getsize(args.out) / 1024
    letzter_tag = max(daten["tage"]) if daten["tage"] else "–"
    print()
    print(f"Fertig in {(time.time() - begonnen) / 60:.1f} Minuten.")
    print(f"  Tageslesen : {len(daten['tage'])}  (bis {letzter_tag})")
    print(f"  Sonntage   : {len(daten['sonntage'])}")
    print(f"  Datei      : {args.out} ({groesse:.0f} KB)")

    if fehlende_tabelle:
        print()
        print("ACHTUNG – für folgende Sonntage fehlt eine Zeile in perikopen.json:")
        for z in fehlende_tabelle:
            print("   " + z)
        print("Bitte perikopen.json ergänzen (scripts/build_perikopen.py neu erzeugen) "
              "und das Skript erneut laufen lassen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
