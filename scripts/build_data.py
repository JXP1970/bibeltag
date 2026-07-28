#!/usr/bin/env python3
"""Holt die Lesepläne für einen Zeitraum und schreibt sie in daten.json.

Einmalig (bzw. wenn der Vorrat zur Neige geht) auszuführen. Danach braucht
die App keine Aktualisierung mehr: alle Texte liegen fertig in der Datei,
die App sucht sich nur noch den heutigen Tag heraus.

Quelle: die-bibel.de
  - Tageslese   /leseplaene/oeab-leseplan/oeab-<JJJJ-MM-TT>
  - Sonntag     /leseplaene/predigttexte/predigttext-<JJJJ-MM-TT>

Die Leseordnung wird dort jahresweise veröffentlicht – irgendwann sind
keine weiteren Tage mehr abrufbar. Das Skript erkennt das automatisch
(zwei aufeinanderfolgende Abschnitte ganz ohne Treffer) und hört dann auf,
statt stur bis zum Enddatum weiterzuversuchen. Ein späterer Lauf holt die
neu veröffentlichten Tage einfach nach.

Bereits vorhandene Einträge in daten.json werden übersprungen, ein
abgebrochener Lauf kann also jederzeit wiederholt werden.

Aufruf:
    python scripts/build_data.py [--von JJJJ-MM-TT] [--bis JJJJ-MM-TT]
"""

from __future__ import annotations

import argparse
import asyncio
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

CONCURRENCY = 8          # gleichzeitig geöffnete Seiten
CHUNK = 12                # Daten pro Durchgang
LEER_ABSCHNITTE_STOPP = 2  # so viele Durchgänge ganz ohne Treffer -> Ende erreicht
PROBE_TIMEOUT_MS = 9000   # wie lange auf einen Treffer gewartet wird

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


async def hole_seite(context, url: str, js: str):
    """Lädt eine Seite und liest sie aus. None, wenn es keinen Eintrag gibt."""
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        try:
            await page.wait_for_selector("span.verse", timeout=PROBE_TIMEOUT_MS)
        except Exception:
            return None  # kein Leseplan-Eintrag für dieses Datum
        await page.wait_for_timeout(500)
        return await page.evaluate(js)
    except Exception as e:  # noqa: BLE001
        print(f"    Fehler ({url}): {e}")
        return None
    finally:
        await page.close()


async def verarbeite(context, items, url_von, js, semaphore):
    async def eins(item):
        async with semaphore:
            iso = item.isoformat()
            res = await hole_seite(context, url_von(iso), js)
            return item, res
    return await asyncio.gather(*(eins(i) for i in items))


async def main_async(args) -> int:
    heute = date.today()
    von = datetime.strptime(args.von, "%Y-%m-%d").date()
    bis = datetime.strptime(args.bis, "%Y-%m-%d").date()
    if bis < von:
        print("Fehler: --bis liegt vor --von")
        return 1

    daten = lade_bestand(args.out)

    offene_tage = [d for d in tage_zwischen(von, bis)
                   if d.isoformat() not in daten["tage"]]
    sonntage = sorted({(d - timedelta(days=(d.weekday() + 1) % 7))
                       for d in tage_zwischen(von, bis)})
    offene_sonntage = [d for d in sonntage if d.isoformat() not in daten["sonntage"]]

    print(f"Zeitraum {von} bis {bis} (Ende wird automatisch erkannt)")
    print(f"  Tageslesen zu prüfen : {len(offene_tage)}")
    print(f"  Sonntage zu prüfen   : {len(offene_sonntage)}")
    if not offene_tage and not offene_sonntage:
        print("Nichts zu tun – alles bereits vorhanden.")
        return 0

    from playwright.async_api import async_playwright

    begonnen = time.time()
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(locale="de-DE")

        # --- Sonntage zuerst (wenige, wichtig) ---
        leere_abschnitte = 0
        verarbeitet = 0
        for start in range(0, len(offene_sonntage), CHUNK):
            chunk = offene_sonntage[start:start + CHUNK]
            ergebnisse = await verarbeite(
                context, chunk, lambda i: PREDIGT_URL.format(d=i), _JS_SONNTAG, semaphore)
            treffer = 0
            for d, res in ergebnisse:
                verarbeitet += 1
                if not res or not res.get("name") or not res.get("predigtVerse"):
                    continue
                daten["sonntage"][d.isoformat()] = {
                    "name": res["name"],
                    "datum": res.get("datum") or de_long(d),
                    "spruch": {"text": res.get("spruchText", ""),
                               "cite": res.get("spruchCite", "")},
                    "predigt": {"ref": res.get("predigtRef", ""),
                                "verse": [[int(n), t] for n, t in res.get("predigtVerse", []) if t]},
                    "psalm": {"ref": res.get("psalmRef", ""),
                              "verse": [[int(n), t] for n, t in res.get("psalmVerse", []) if t]},
                }
                treffer += 1
            print(f"  Sonntage [{verarbeitet}/{len(offene_sonntage)}] "
                  f"{treffer}/{len(chunk)} Treffer in diesem Abschnitt")
            speichere(args.out, daten)
            leere_abschnitte = 0 if treffer else leere_abschnitte + 1
            if leere_abschnitte >= LEER_ABSCHNITTE_STOPP:
                print("  Ende des veröffentlichten Sonntagsplans erreicht.")
                break

        # --- Tageslesen ---
        leere_abschnitte = 0
        verarbeitet = 0
        for start in range(0, len(offene_tage), CHUNK):
            chunk = offene_tage[start:start + CHUNK]
            ergebnisse = await verarbeite(
                context, chunk, lambda i: OEAB_URL.format(d=i), _JS_TAGESLESE, semaphore)
            treffer = 0
            for d, res in ergebnisse:
                verarbeitet += 1
                if not res or not res.get("ref") or not res.get("verse"):
                    continue
                daten["tage"][d.isoformat()] = {
                    "ref": res["ref"],
                    "verse": [[int(n), t] for n, t in res["verse"] if t],
                }
                treffer += 1
            speichere(args.out, daten)
            verstrichen = time.time() - begonnen
            print(f"  Tageslesen [{verarbeitet}/{len(offene_tage)}] "
                  f"{treffer}/{len(chunk)} Treffer ({verstrichen/60:.1f} min)")
            leere_abschnitte = 0 if treffer else leere_abschnitte + 1
            if leere_abschnitte >= LEER_ABSCHNITTE_STOPP:
                print("  Ende des veröffentlichten Tagesplans erreicht.")
                break

        await context.close()
        await browser.close()

    speichere(args.out, daten)

    groesse = os.path.getsize(args.out) / 1024
    letzter_tag = max(daten["tage"]) if daten["tage"] else "–"
    print()
    print(f"Fertig in {(time.time()-begonnen)/60:.1f} Minuten.")
    print(f"  Tageslesen : {len(daten['tage'])}  (bis {letzter_tag})")
    print(f"  Sonntage   : {len(daten['sonntage'])}")
    print(f"  Datei      : {args.out} ({groesse:.0f} KB)")
    return 0


def main() -> int:
    heute = date.today()
    p = argparse.ArgumentParser()
    p.add_argument("--von", default=heute.isoformat())
    p.add_argument("--bis", default=(heute + timedelta(days=1100)).isoformat())
    p.add_argument("--out", default=os.path.abspath(OUT_PATH))
    args = p.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
