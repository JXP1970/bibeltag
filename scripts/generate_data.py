#!/usr/bin/env python3
"""Aktualisiert den DATA-Block in index.html auf den heutigen Tag.

Wird täglich von GitHub Actions ausgeführt.

Ablauf:
1. Die Tageslese wird von die-bibel.de geholt – Bibelstelle UND der
   Luther-2017-Wortlaut stammen damit aus der maßgeblichen Quelle
   (Ökumenische Bibellese), nicht aus Modellwissen.
2. Die Anthropic-API ergänzt: Schlachter-2000-Wortlaut derselben Stelle,
   die Sonntagstexte des Kirchenjahres und den Impuls.
3. Das Ergebnis wird als JavaScript-Objekt zwischen die Marker
   /* ==== DATA START ... */ und /* ==== DATA END ==== */ gespleißt.
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic

OEAB_URL = "https://www.die-bibel.de/leseplaene/oeab-leseplan/oeab-{date}"

MODEL = os.environ.get("BIBELTAG_MODEL", "claude-opus-5")
HTML_PATH = os.path.join(os.path.dirname(__file__), "..", "index.html")

BERLIN = ZoneInfo("Europe/Berlin")
WT = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
MON = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
       "August", "September", "Oktober", "November", "Dezember"]


def de_long(d: datetime) -> str:
    return f"{d.day}. {MON[d.month - 1]} {d.year}"


# ---------------------------------------------------------------- Tageslese

# Im Browser der Seite ausgeführt: liest Bibelstelle und Luther-2017-Verse.
_EXTRACT_JS = r"""
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
    const txt = e.textContent.replace(/\s+/g, ' ').trim();
    if (!txt) return;
    map.set(n, ((map.get(n) || '') + ' ' + txt).trim());
  });
  return {
    ref: refs[0] || '',
    verses: [...map.entries()].sort((a, b) => a[0] - b[0]),
  };
}
"""


def fetch_oeab(iso_date: str, attempts: int = 3) -> dict:
    """Holt die Tageslese der Ökumenischen Bibellese von die-bibel.de.

    Die Seite baut ihren Inhalt erst im Browser auf, deshalb ein echter
    Browser (Playwright) statt eines einfachen Abrufs.
    """
    from playwright.sync_api import sync_playwright

    url = OEAB_URL.format(date=iso_date)
    last_err = None

    for versuch in range(1, attempts + 1):
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page(locale="de-DE")
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_selector("span.verse", timeout=45000)
                page.wait_for_timeout(1200)  # Nachzügler-Verse abwarten
                data = page.evaluate(_EXTRACT_JS)
                browser.close()

            ref = (data.get("ref") or "").strip()
            verses = [[int(n), t] for n, t in data.get("verses", []) if t]
            if not ref or not verses:
                raise ValueError("Bibelstelle oder Verse nicht gefunden")
            print(f"Tageslese von die-bibel.de: {ref} ({len(verses)} Verse)")
            return {"ref": ref, "LU17": verses}

        except Exception as e:  # noqa: BLE001 – bewusst breit, danach Neuversuch
            last_err = e
            print(f"Abruf-Versuch {versuch}/{attempts} fehlgeschlagen: {e}")
            if versuch < attempts:
                time.sleep(5 * versuch)

    raise RuntimeError(f"Tageslese konnte nicht geladen werden: {last_err}")


def build_prompt(today: datetime, daily: dict) -> str:
    iso = today.strftime("%Y-%m-%d")
    wochentag = WT[today.weekday()]
    lu_text = "\n".join(f"{n} {t}" for n, t in daily["LU17"])
    verse_nums = [n for n, _ in daily["LU17"]]
    return f"""Du erzeugst die Tagesdaten für die Andachts-App „Mein Bibeltag".

HEUTE ist {wochentag}, der {de_long(today)} ({iso}), Zeitzone Europe/Berlin.

Die Tageslese steht bereits fest (Quelle: Ökumenische Bibellese, die-bibel.de).
Sie darf NICHT geändert werden:

  Stelle: {daily["ref"]}
  Verse:  {verse_nums[0]} bis {verse_nums[-1]} ({len(verse_nums)} Verse)

Luther-2017-Wortlaut dieser Stelle (maßgeblich, unverändert übernehmen):
---
{lu_text}
---

Gib AUSSCHLIESSLICH ein gültiges JSON-Objekt zurück (kein Markdown, keine
Code-Fences, kein erklärender Text davor oder danach), mit exakt diesen Feldern:

{{
  "updated": "{de_long(today)}",
  "season": "<Kirchenjahreszeit, z.B. Trinitatiszeit / Advent / Passionszeit>",
  "sunday": {{
    "name": "<aktuelle Kirchenwoche = letzter Sonntag heute-oder-davor, z.B. 9. Sonntag nach Trinitatis>",
    "date": "<Datum dieses Sonntags, z.B. Sonntag, 2. August 2026>",
    "spruch": {{ "text": "<Wochenspruch>", "cite": "<Bibelstelle>" }},
    "predigt": {{
      "ref": "<z.B. Johannes 9,1-7>",
      "title": "<kurzer Titel>",
      "url": "https://www.die-bibel.de/bibel/LU17/...",
      "LU17": [[1, "Vers 1 nach Luther 2017"], [2, "..."]],
      "SCH2000": [[1, "Vers 1 nach Schlachter 2000"], [2, "..."]]
    }},
    "psalm": {{
      "ref": "<z.B. Psalm 48>",
      "note": "Wochenpsalm",
      "LU17": [[1, "..."]],
      "SCH2000": [[1, "..."]]
    }},
    "impuls": "<4-6 Sätze zum Predigttext und Wochenspruch>"
  }},
  "daily": {{
    "ref": "{daily["ref"]}",
    "title": "<kurzer, treffender Titel für diese Tageslese>",
    "url": "https://www.die-bibel.de/leseplaene/oeab-leseplan/oeab-{iso}",
    "LU17": <exakt der oben vorgegebene Wortlaut, als [[Nr, "Text"], ...]>,
    "SCH2000": [[{verse_nums[0]}, "derselbe Abschnitt nach Schlachter 2000"], ...]
  }}
}}

Regeln:
- Verse als Arrays [Versnummer, "Text"], Vers für Vers, vollständig.
- daily.ref und daily.LU17 sind vorgegeben – wortgleich übernehmen, nichts
  ergänzen, nichts weglassen, Versnummern exakt beibehalten.
- daily.SCH2000 ist DERSELBE Abschnitt ({daily["ref"]}) nach Schlachter 2000,
  mit denselben Versnummern.
- Bibeltext wortgetreu: Luther 2017 (LU17) und Schlachter 2000 (SCH2000).
- Bestimme Kirchenjahreszeit, aktuellen Sonntag, Wochenspruch, Predigttext
  (Revidierte Perikopenordnung, Reihe des laufenden Kirchenjahres) und
  Wochenpsalm anhand des evangelischen Kirchenjahres.
- Deutsche Typografie im Fließtext (Anführungszeichen „ ", » «).
- Nur das JSON-Objekt ausgeben."""


def extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("Keine JSON-Struktur in der Antwort gefunden.")
    return json.loads(text[start:end + 1])


# JSON-Schema erzwingt syntaktisch gültiges JSON (Structured Outputs),
# unabhängig von Anführungszeichen o.ä. im Bibeltext.
_VERSES = {"type": "array", "items": {"type": "array"}}
_PASSAGE = {
    "type": "object", "additionalProperties": False,
    "required": ["ref", "title", "url", "LU17", "SCH2000"],
    "properties": {
        "ref": {"type": "string"}, "title": {"type": "string"},
        "url": {"type": "string"}, "LU17": _VERSES, "SCH2000": _VERSES,
    },
}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["updated", "season", "sunday", "daily"],
    "properties": {
        "updated": {"type": "string"},
        "season": {"type": "string"},
        "sunday": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "date", "spruch", "predigt", "psalm", "impuls"],
            "properties": {
                "name": {"type": "string"},
                "date": {"type": "string"},
                "spruch": {
                    "type": "object", "additionalProperties": False,
                    "required": ["text", "cite"],
                    "properties": {"text": {"type": "string"}, "cite": {"type": "string"}},
                },
                "predigt": _PASSAGE,
                "psalm": {
                    "type": "object", "additionalProperties": False,
                    "required": ["ref", "note", "LU17", "SCH2000"],
                    "properties": {
                        "ref": {"type": "string"}, "note": {"type": "string"},
                        "LU17": _VERSES, "SCH2000": _VERSES,
                    },
                },
                "impuls": {"type": "string"},
            },
        },
        "daily": _PASSAGE,
    },
}


def already_current(path: str, today: datetime) -> bool:
    """True, wenn index.html bereits die Daten von heute enthält."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()
    except OSError:
        return False
    m = re.search(r'"updated"\s*:\s*"([^"]+)"', html)
    return bool(m) and m.group(1).strip() == de_long(today)


def main() -> int:
    today = datetime.now(BERLIN)
    path = os.path.abspath(HTML_PATH)

    # Sicherheitsnetz-Lauf: nichts tun (und nichts bezahlen), wenn schon aktuell.
    if already_current(path, today) and os.environ.get("BIBELTAG_FORCE") != "1":
        print(f"Bereits aktuell für {de_long(today)} – kein API-Aufruf nötig.")
        return 0

    # Tageslese aus der maßgeblichen Quelle holen
    daily = fetch_oeab(today.strftime("%Y-%m-%d"))

    client = anthropic.Anthropic()

    with client.messages.stream(
        model=MODEL,
        max_tokens=32000,
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": build_prompt(today, daily)}],
    ) as stream:
        message = stream.get_final_message()

    if message.stop_reason == "refusal":
        raise RuntimeError("API-Antwort wurde abgelehnt (refusal).")

    text = "".join(b.text for b in message.content if b.type == "text")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = extract_json(text)

    # Pflichtfelder prüfen
    for key in ("updated", "season", "sunday", "daily"):
        if key not in data:
            raise ValueError(f"Feld '{key}' fehlt im generierten JSON.")

    # Tageslese und Luther-Wortlaut stammen aus der Quelle, nicht vom Modell:
    # hier hart überschreiben, damit nichts abweichen kann.
    data["daily"]["ref"] = daily["ref"]
    data["daily"]["LU17"] = daily["LU17"]
    data["updated"] = de_long(today)

    # Schlachter-Fassung auf Plausibilität prüfen (gleiche Versnummern)
    lu_nums = [n for n, _ in daily["LU17"]]
    sch = data["daily"].get("SCH2000") or []
    sch_nums = [n for n, _ in sch]
    if sch_nums != lu_nums:
        print(f"Hinweis: Schlachter-Verse weichen ab "
              f"({sch_nums[:3]}… statt {lu_nums[:3]}…) – Luther bleibt maßgeblich.")

    json_text = json.dumps(data, ensure_ascii=False, indent=2)
    new_block = "const DATA = " + json_text + ";"

    with open(path, "r", encoding="utf-8") as f:
        html = f.read()

    pattern = re.compile(
        r"const DATA = \{.*?\};(?=\s*/\* ==== DATA END ==== \*/)", re.DOTALL
    )
    if not pattern.search(html):
        raise ValueError("DATA-Block in index.html nicht gefunden.")

    html = pattern.sub(lambda m: new_block, html, count=1)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)

    print(f"index.html aktualisiert für {today.strftime('%Y-%m-%d')} "
          f"(Sonntag: {data['sunday'].get('name')}, Tageslese: {data['daily'].get('ref')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
