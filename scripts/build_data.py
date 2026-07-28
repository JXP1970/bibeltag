#!/usr/bin/env python3
"""Holt die Lesepläne für einen langen Zeitraum und schreibt sie in daten.json.

Einmalig (bzw. einmal im Jahr) auszuführen. Danach braucht die App keine
Aktualisierung mehr und verursacht keine Kosten: alle Texte liegen fertig
in der Datei, die App sucht sich nur noch den heutigen Tag heraus.

Quelle: die-bibel.de
  - Tageslese   /leseplaene/oeab-leseplan/oeab-<JJJJ-MM-TT>
  - Sonntag     /leseplaene/predigttexte/predigttext-<JJJJ-MM-TT>

Die Seiten bauen ihren Inhalt erst im Browser auf, deshalb Playwright.
Bereits vorhandene Einträge in daten.json werden übersprungen, ein
abgebrochener Lauf kann also einfach wiederholt werden.

Aufruf:
    python scripts/build_data.py [--von JJJJ-MM-TT] [--bis JJJJ-MM-TT]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta

OEAB_URL = "https://www.die-bibel.de/leseplaene/oeab-leseplan/oeab-{d}"
PREDIGT_URL = "https://www.die-bibel.de/leseplaene/predigttexte/predigttext-{d}"

OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "daten.json")

MON = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
       "August", "September", "Oktober", "November", "Dezember"]

# Wie viele Seiten gleichzeitig geladen werden. Bewusst niedrig gehalten,
# um die Quelle nicht zu belasten.
PARALLEL = 3

_JS_TAGESLESE = r"""
() => {
  const refs = [...document.querySelectorAll('span.text-grayLight')]
    .map(e => e.textContent.trim())
    .filter(t => /\d/.test(t) && t.length < 60);
  const map = new Map();
  document.querySelectorAll('span.verse').forEach(e => {
    const cls = (e.className.baseVal || e.className || '').toString();
    const m = cls.match(/LU17\.([A-Z0-9]+)\.(\d+)\.(\d+)/);
    if (!m) return;
    const n = Number(m[3]);
    const t = e.textContent.replace(/\s+/g, ' ').trim();
    if (!t) return;
    map.set(n, ((map.get(n) || '') + ' ' + t).trim());
  });
  return { ref: refs[0] || '', verse: [...map.entries()].sort((a, b) => a[0] - b[0]) };
}
"""

_JS_SONNTAG = r"""
() => {
  const txt = (document.querySelector('main')?.innerText || '').replace(/ /g, ' ');
  const g = re => (txt.match(re) || [])[1]?.trim() || '';
  const kopf = txt.match(/^\s*(\d{1,2}\.\s+\S+\s+\d{4}):\s*(.+?)\s*$/m) || [];

  const gruppen = new Map();
  document.querySelectorAll('span.verse').forEach(e => {
    const cls = (e.className.baseVal || e.className || '').toString();
    const m = cls.match(/LU17\.([A-Z0-9]+)\.(\d+)\.(\d+)/);
    if (!m) return;
    const key = m[1] + '.' + m[2];
    const n = Number(m[3]);
    const t = e.textContent.replace(/\s+/g, ' ').trim();
    if (!t) return;
    if (!gruppen.has(key)) gruppen.set(key, new Map());
    gruppen.get(key).set(n, ((gruppen.get(key).get(n) || '') + ' ' + t).trim());
  });

  const kapitelVon = r => { const m = r.match(/(\d+)\s*,/); return m ? Number(m[1]) : null; };
  const liste = k => gruppen.has(k)
    ? [...gruppen.get(k).entries()].sort((a, b) => a[0] - b[0]) : [];

  const waehle = (ref, psalmBevorzugt) => {
    const kap = kapitelVon(ref);
    let treffer = [...gruppen.keys()].filter(k => Number(k.split('.')[1]) === kap);
    if (treffer.length > 1) {
      const psa = treffer.filter(k => k.startsWith('PSA'));
      const rest = treffer.filter(k => !k.startsWith('PSA'));
      treffer = psalmBevorzugt ? (psa.length ? psa : treffer)
                               : (rest.length ? rest : treffer);
    }
    return treffer.length ? liste(treffer[0]) : [];
  };

  const psalmRef = g(/Wochenpsalm:\s*(.+)/);
  const predigtRef = g(/Predigttext:\s*(.+)/);

  return {
    datum: kopf[1] || '', name: kopf[2] || '',
    spruchText: g(/Wochenspruch:\s*[„"«]?([^"„“»]+?)[”"“»]?\s*\(/),
    spruchCite: g(/Wochenspruch:[^(]*\(([^)]+)\)/),
    predigtRef, psalmRef,
    predigtVerse: waehle(predigtRef, false),
    psalmVerse: waehle(psalmRef, true),
  };
}
"""


def de_long(d: date) -> str:
    return f"{d.day}. {MON[d.month - 1]} {d.year}"


def tage_zwischen(von: date, bis: date):
    d = von
    while d <= bis:
        yield d
        d += timedelta(days=1)


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


def hole_seite(page, url: str, js: str, versuche: int = 3):
    """Lädt eine Seite und liest sie aus. None, wenn es keinen Eintrag gibt."""
    for versuch in range(1, versuche + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_selector("span.verse", timeout=20000)
            except Exception:
                return None  # Für dieses Datum gibt es keinen Leseplan-Eintrag
            page.wait_for_timeout(700)
            return page.evaluate(js)
        except Exception as e:  # noqa: BLE001
            if versuch == versuche:
                print(f"    Fehlgeschlagen ({url}): {e}")
                return None
            time.sleep(3 * versuch)
    return None


def main() -> int:
    heute = date.today()
    p = argparse.ArgumentParser()
    p.add_argument("--von", default=heute.isoformat())
    p.add_argument("--bis", default=(heute + timedelta(days=400)).isoformat())
    p.add_argument("--out", default=os.path.abspath(OUT_PATH))
    args = p.parse_args()

    von = datetime.strptime(args.von, "%Y-%m-%d").date()
    bis = datetime.strptime(args.bis, "%Y-%m-%d").date()
    if bis < von:
        print("Fehler: --bis liegt vor --von")
        return 1

    daten = lade_bestand(args.out)

    # Welche Tage / Sonntage fehlen noch?
    offene_tage = [d for d in tage_zwischen(von, bis)
                   if d.isoformat() not in daten["tage"]]
    sonntage = sorted({(d - timedelta(days=(d.weekday() + 1) % 7))
                       for d in tage_zwischen(von, bis)})
    offene_sonntage = [d for d in sonntage if d.isoformat() not in daten["sonntage"]]

    print(f"Zeitraum {von} bis {bis}")
    print(f"  Tageslesen offen : {len(offene_tage)}")
    print(f"  Sonntage offen   : {len(offene_sonntage)}")
    if not offene_tage and not offene_sonntage:
        print("Nichts zu tun – alles bereits vorhanden.")
        return 0

    from playwright.sync_api import sync_playwright

    fehlend_tage, fehlend_sonntage = [], []
    begonnen = time.time()

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(locale="de-DE")
        seiten = [ctx.new_page() for _ in range(PARALLEL)]

        # --- Sonntage zuerst (wenige, wichtig) ---
        for i, d in enumerate(offene_sonntage, 1):
            iso = d.isoformat()
            res = hole_seite(seiten[i % PARALLEL], PREDIGT_URL.format(d=iso), _JS_SONNTAG)
            if not res or not res.get("name") or not res.get("predigtVerse"):
                fehlend_sonntage.append(iso)
                print(f"  [{i}/{len(offene_sonntage)}] {iso}  – kein Eintrag")
                continue
            daten["sonntage"][iso] = {
                "name": res["name"],
                "datum": res.get("datum") or de_long(d),
                "spruch": {"text": res.get("spruchText", ""),
                           "cite": res.get("spruchCite", "")},
                "predigt": {"ref": res.get("predigtRef", ""),
                            "verse": [[int(n), t] for n, t in res.get("predigtVerse", []) if t]},
                "psalm": {"ref": res.get("psalmRef", ""),
                          "verse": [[int(n), t] for n, t in res.get("psalmVerse", []) if t]},
            }
            print(f"  [{i}/{len(offene_sonntage)}] {iso}  {res['name']} "
                  f"| {res.get('predigtRef')}")
            if i % 10 == 0:
                speichere(args.out, daten)

        speichere(args.out, daten)

        # --- Tageslesen ---
        for i, d in enumerate(offene_tage, 1):
            iso = d.isoformat()
            res = hole_seite(seiten[i % PARALLEL], OEAB_URL.format(d=iso), _JS_TAGESLESE)
            if not res or not res.get("ref") or not res.get("verse"):
                fehlend_tage.append(iso)
                print(f"  [{i}/{len(offene_tage)}] {iso}  – kein Eintrag")
                continue
            daten["tage"][iso] = {
                "ref": res["ref"],
                "verse": [[int(n), t] for n, t in res["verse"] if t],
            }
            if i % 25 == 0 or i == len(offene_tage):
                speichere(args.out, daten)
                verstrichen = time.time() - begonnen
                print(f"  [{i}/{len(offene_tage)}] {iso}  {res['ref']} "
                      f"({verstrichen/60:.1f} min)")

        for s in seiten:
            s.close()
        ctx.close()
        browser.close()

    speichere(args.out, daten)

    groesse = os.path.getsize(args.out) / 1024
    print()
    print(f"Fertig in {(time.time()-begonnen)/60:.1f} Minuten.")
    print(f"  Tageslesen : {len(daten['tage'])}")
    print(f"  Sonntage   : {len(daten['sonntage'])}")
    print(f"  Datei      : {args.out} ({groesse:.0f} KB)")
    if fehlend_tage:
        print(f"  Ohne Eintrag (Tage)    : {len(fehlend_tage)} "
              f"– z.B. {fehlend_tage[:3]}")
    if fehlend_sonntage:
        print(f"  Ohne Eintrag (Sonntage): {len(fehlend_sonntage)} "
              f"– z.B. {fehlend_sonntage[:3]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
