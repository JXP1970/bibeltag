#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Erzeugt scripts/perikopen.json: die feste Tabelle der Sonntags-/Feiertags-
Perikopen (Wochenspruch, Wochenpsalm, Predigttext je Reihe I–VI).

Quelle der Bibelstellen: kirchenjahr-evangelisch.de (frei, serverseitig
gerendert, mit die-bibel.de-Links in der Lutherbibel 2017). Der eigentliche
Bibeltext wird daraus NICHT geholt – nur die Stellenangaben. Den Text holt
build_data.py später aus dem Bibel-Reader.

Die Tabelle ist nach Slug (= kirchenjahr-evangelisch.de-Seitenname) geordnet.
build_data.py bildet die die-bibel.de-Sonntagsnamen über slugify() darauf ab.

Klammerverse "(…)" sind optional und werden – wie bei die-bibel.de – NICHT
übernommen. Volle Psalmen ("Ps 122") werden als komplettes Kapitel erfasst.

Aufruf:
    python scripts/build_perikopen.py            # erzeugen + gegen daten.json prüfen
    python scripts/build_perikopen.py --out X    # anderes Ziel
"""

from __future__ import annotations

import argparse
import datetime
import html as _html
import json
import os
import re
import sys

from build_data import (fetch, strip_tags, slugify, reihe_fuer, REIHEN,
                         SCRIPT_DIR, OUT_PATH)

DAY_URL = "https://kirchenjahr-evangelisch.de/{slug}/"
PERIKOPEN_PATH = os.path.join(SCRIPT_DIR, "perikopen.json")


def ndash(s: str) -> str:
    return s.replace("–", "-").replace("−", "-")


def parse_verses(versspec: str):
    """'1.4-7(8-9)10-14' -> [1,4,5,6,7,10,11,12,13,14] (Klammerverse weggelassen)."""
    s = ndash(versspec)
    s = re.sub(r"\([^)]*\)", ".", s)          # optionale Verse raus, Klammer trennt
    s = s.replace("ff", "").replace("f", "")  # 'Vf'/'Vff' -> nur Startvers
    out: list[int] = []
    for grp in s.split("."):
        grp = grp.strip()
        if not grp:
            continue
        m = re.match(r"^(\d+)[a-z]?\s*-\s*(\d+)[a-z]?$", grp)
        if m:
            out.extend(range(int(m.group(1)), int(m.group(2)) + 1))
            continue
        m = re.match(r"^(\d+)[a-z]?$", grp)
        if m:
            out.append(int(m.group(1)))
            continue
        return None
    seen, res = set(), []
    for n in out:
        if n not in seen:
            seen.add(n)
            res.append(n)
    return res


def parse_ref(display_ref: str):
    """'Jer 29,1.4-7(8-9)10-14' -> (kapitel, versliste); (None,None) bei Mehr-Kapitel."""
    r = ndash(display_ref).strip()
    m = re.search(r"(\d+)\s*,\s*(.+)$", r)
    if not m:
        return (None, None)
    rest = m.group(2)
    if ";" in rest:
        return (None, None)
    return (int(m.group(1)), parse_verses(rest))


def _span_ref(fragment: str):
    """(Buchcode, Anzeige-Ref) aus einem <a href="...LU17/BUCH...">​<span>REF ...-Fragment."""
    m = re.search(
        r'<a\b[^>]*href="https://www\.die-bibel\.de/bibel/LU17/([A-Z0-9]+)\.[^"]*"[^>]*>'
        r"\s*<span>\s*([^<]+?)\s*(?:<|$)", fragment)
    if not m:
        return None, None
    return m.group(1), ndash(m.group(2)).strip()


def _subline(html: str, label: str):
    """Inhalt des liturgical-text-subline-Divs direkt nach der Überschrift <label>."""
    m = re.search(label + r".*?<div[^>]*liturgical-text-subline[^>]*>(.*?)</div>", html, re.S)
    return m.group(1) if m else None


def _quote(fragment: str) -> str:
    vis = strip_tags(fragment)
    tm = re.search(r'["„“]([^"„“”]{2,}?)["“”]', vis)
    return tm.group(1).strip() if tm else ""


def parse_day(html: str) -> dict:
    res: dict = {"spruch": None, "psalm": None, "predigt": {}}

    frag = _subline(html, "Wochenspruch")
    if frag:
        book, ref = _span_ref(frag)
        text = _quote(frag)
        if ref or text:
            res["spruch"] = {"cite": ref or "", "text": text}

    frag = _subline(html, "Wochenpsalm")
    if frag:
        book, ref = _span_ref(frag)
        if ref:
            kap, verses = parse_ref(ref)
            res["psalm"] = {"ref": ref, "book": book, "chapter": kap, "verses": verses}

    cm = re.search(r"texts-pericops-content(.*?)(?:</section|texts-pericops-footer|holiday-content-section-entry-container)",
                   html, re.S)
    block = cm.group(1) if cm else html
    for em in re.finditer(r'texts-pericops-link-text"[^>]*>\s*([IVX]+)\s*</h4>(.*?)(?=texts-pericops-link-element|$)',
                          block, re.S):
        reihe = em.group(1).strip()
        book, ref = _span_ref(em.group(2))
        if reihe in REIHEN and ref:
            kap, verses = parse_ref(ref)
            res["predigt"][reihe] = {"ref": ref, "book": book, "chapter": kap, "verses": verses}
    return res


def canonical_slugs() -> list[str]:
    s: list[str] = []
    s += [f"{i}-advent" for i in range(1, 5)]
    s += [f"{i}-sonntag-nach-christfest" for i in range(1, 3)]
    s += [f"{i}-sonntag-nach-epiphanias" for i in range(1, 7)]
    s += ["letzter-sonntag-nach-epiphanias"]
    s += ["septuagesimae", "sexagesimae", "estomihi"]
    s += [f"{i}-sonntag-vor-der-passionszeit" for i in range(1, 7)]
    s += ["invokavit", "reminiszere", "okuli", "laetare", "judika", "palmsonntag"]
    s += ["ostersonntag", "quasimodogeniti", "miserikordias-domini", "jubilate",
          "kantate", "rogate", "exaudi"]
    s += ["pfingstsonntag", "trinitatis"]
    s += [f"{i}-sonntag-nach-trinitatis" for i in range(1, 25)]
    s += ["kirche-und-israel", "drittletzter-sonntag-des-kirchenjahres",
          "vorletzter-sonntag-des-kirchenjahres", "ewigkeitssonntag",
          "erntedank", "reformationstag"]
    return s


def validiere(table: dict, daten_pfad: str) -> None:
    """Gegen den vorhandenen Bestand prüfen (Versmengen von Predigt und Psalm)."""
    try:
        with open(daten_pfad, "r", encoding="utf-8") as f:
            dd = json.load(f)
    except OSError:
        print("  (keine daten.json zum Prüfen gefunden)")
        return
    ok = bad = 0
    for iso in sorted(dd.get("sonntage", {})):
        e = dd["sonntage"][iso]
        slug = slugify(e.get("name", ""))
        t = table.get(slug)
        reihe = reihe_fuer(datetime.date.fromisoformat(iso))
        if not t:
            print(f"  DIFF {iso} {e.get('name')!r}: kein Tabelleneintrag (Slug {slug})")
            bad += 1
            continue
        got_pv = set((t["predigt"].get(reihe) or {}).get("verses") or [])
        got_sv = set((t["psalm"] or {}).get("verses") or [])
        exp_pv = set(n for n, _ in e["predigt"]["verse"])
        exp_sv = set(n for n, _ in e["psalm"]["verse"])
        altbug = (not exp_sv) and bool(got_sv)   # alter Voll-Psalm-Bug: daten leer, Tabelle füllt
        if got_pv == exp_pv and (got_sv == exp_sv or altbug):
            ok += 1
        else:
            bad += 1
            print(f"  DIFF {iso} [{reihe}] {e.get('name')!r} (Slug {slug})")
            if got_pv != exp_pv:
                print(f"       predigt Tabelle {sorted(got_pv)} / daten {sorted(exp_pv)}")
            if got_sv != exp_sv and not altbug:
                print(f"       psalm   Tabelle {sorted(got_sv)} / daten {sorted(exp_sv)}")
    print(f"  Validierung gegen daten.json: {ok} OK, {bad} abweichend")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=PERIKOPEN_PATH)
    ap.add_argument("--daten", default=os.path.abspath(OUT_PATH))
    args = ap.parse_args()

    slugs = sorted(set(canonical_slugs()))
    print(f"Lade {len(slugs)} Tagesseiten von kirchenjahr-evangelisch.de ...")
    table: dict = {}
    for slug in slugs:
        html = fetch(DAY_URL.format(slug=slug))
        if html is None:
            continue   # 404: diesen (seltenen) Tag gibt es dort nicht
        tag = parse_day(html)
        if not tag["predigt"]:
            print(f"  Hinweis: {slug} ohne Predigttexte-Block – übersprungen.")
            continue
        table[slug] = tag
        fehlt = [r for r in REIHEN if r not in tag["predigt"]]
        if fehlt or not tag["psalm"]:
            print(f"  WARN {slug}: Reihen fehlen={fehlt} psalm={bool(tag['psalm'])}")

    validiere(table, args.daten)

    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(table, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(f"geschrieben: {args.out} ({len(table)} Tage)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
