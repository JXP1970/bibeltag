#!/usr/bin/env python3
"""Aktualisiert den DATA-Block in index.html auf den heutigen Tag.

Wird täglich von GitHub Actions ausgeführt.

Ablauf:
1. Von die-bibel.de werden geholt (maßgebliche Quelle, kein Modellwissen):
   - Tageslese der Ökumenischen Bibellese
   - Sonntagstexte: Wochenspruch, Wochenpsalm, Predigttext
   jeweils mit Bibelstelle UND Luther-2017-Wortlaut.
2. Die Anthropic-API ergänzt nur noch: Schlachter-2000-Fassungen derselben
   Stellen, kurze Titel und den Impuls.
3. Alle Quelldaten werden anschließend hart gesetzt, damit das Modell sie
   nicht verändern kann.
4. Das Ergebnis wird als JavaScript-Objekt zwischen die Marker
   /* ==== DATA START ... */ und /* ==== DATA END ==== */ gespleißt.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import anthropic

OEAB_URL = "https://www.die-bibel.de/leseplaene/oeab-leseplan/oeab-{date}"
PREDIGT_URL = "https://www.die-bibel.de/leseplaene/predigttexte/predigttext-{date}"

MODEL = os.environ.get("BIBELTAG_MODEL", "claude-opus-5")
HTML_PATH = os.path.join(os.path.dirname(__file__), "..", "index.html")

BERLIN = ZoneInfo("Europe/Berlin")
WT = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
MON = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
       "August", "September", "Oktober", "November", "Dezember"]


def de_long(d: datetime) -> str:
    return f"{d.day}. {MON[d.month - 1]} {d.year}"


# ------------------------------------------------- Abruf von die-bibel.de

# Tageslese: Bibelstelle + Luther-2017-Verse.
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

# Sonntag: Name, Wochenspruch, Wochenpsalm, Predigttext – je mit Wortlaut.
_JS_SONNTAG = r"""
() => {
  const txt = (document.querySelector('main')?.innerText || '').replace(/ /g, ' ');
  const g = re => (txt.match(re) || [])[1]?.trim() || '';
  const kopf = txt.match(/^\s*(\d{1,2}\.\s+\S+\s+\d{4}):\s*(.+?)\s*$/m) || [];

  const spruchText = g(/Wochenspruch:\s*[„"«]?([^"„“»]+?)[”"“»]?\s*\(/);
  const spruchCite = g(/Wochenspruch:[^(]*\(([^)]+)\)/);
  const psalmRef   = g(/Wochenpsalm:\s*(.+)/);
  const predigtRef = g(/Predigttext:\s*(.+)/);

  // Verse nach Buch+Kapitel gruppieren
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
    const gm = gruppen.get(key);
    gm.set(n, ((gm.get(n) || '') + ' ' + t).trim());
  });

  const kapitelVon = r => { const m = r.match(/(\d+)\s*,/); return m ? Number(m[1]) : null; };
  const alsListe = k => gruppen.has(k)
    ? [...gruppen.get(k).entries()].sort((a, b) => a[0] - b[0]) : [];

  const waehle = (ref, bevorzugtPsalm) => {
    const kap = kapitelVon(ref);
    let treffer = [...gruppen.keys()].filter(k => Number(k.split('.')[1]) === kap);
    if (treffer.length > 1) {
      const psa = treffer.filter(k => k.startsWith('PSA'));
      treffer = bevorzugtPsalm
        ? (psa.length ? psa : treffer)
        : (treffer.filter(k => !k.startsWith('PSA')).length
            ? treffer.filter(k => !k.startsWith('PSA')) : treffer);
    }
    return treffer.length ? alsListe(treffer[0]) : [];
  };

  return {
    datum: kopf[1] || '', name: kopf[2] || '',
    spruchText, spruchCite, psalmRef, predigtRef,
    psalmVerse: waehle(psalmRef, true),
    predigtVerse: waehle(predigtRef, false),
  };
}
"""


def _scrape(url: str, js: str, attempts: int = 3):
    """Lädt eine die-bibel.de-Seite in einem echten Browser und liest sie aus.

    Die Seiten bauen ihren Inhalt erst im Browser auf – ein einfacher
    Abruf liefert nur eine leere Hülle.
    """
    from playwright.sync_api import sync_playwright

    last_err = None
    for versuch in range(1, attempts + 1):
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page(locale="de-DE")
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_selector("span.verse", timeout=45000)
                page.wait_for_timeout(1200)  # Nachzügler-Verse abwarten
                data = page.evaluate(js)
                browser.close()
            return data
        except Exception as e:  # noqa: BLE001 – bewusst breit, danach Neuversuch
            last_err = e
            print(f"  Abruf-Versuch {versuch}/{attempts} fehlgeschlagen: {e}")
            if versuch < attempts:
                time.sleep(5 * versuch)
    raise RuntimeError(f"Seite konnte nicht geladen werden ({url}): {last_err}")


def fetch_oeab(iso_date: str) -> dict:
    """Tageslese der Ökumenischen Bibellese für ein Datum."""
    data = _scrape(OEAB_URL.format(date=iso_date), _JS_TAGESLESE)
    ref = (data.get("ref") or "").strip()
    verses = [[int(n), t] for n, t in data.get("verses", []) if t]
    if not ref or not verses:
        raise RuntimeError("Tageslese: Bibelstelle oder Verse nicht gefunden")
    print(f"Tageslese: {ref} ({len(verses)} Verse)")
    return {"ref": ref, "LU17": verses}


def fetch_sonntag(iso_sunday: str) -> dict:
    """Sonntagstexte (Wochenspruch, Wochenpsalm, Predigttext) für einen Sonntag."""
    data = _scrape(PREDIGT_URL.format(date=iso_sunday), _JS_SONNTAG)
    name = (data.get("name") or "").strip()
    predigt = [[int(n), t] for n, t in data.get("predigtVerse", []) if t]
    psalm = [[int(n), t] for n, t in data.get("psalmVerse", []) if t]
    if not name or not predigt or not psalm:
        raise RuntimeError(
            f"Sonntagstexte unvollständig (Name: {name!r}, "
            f"Predigt: {len(predigt)}, Psalm: {len(psalm)})")
    print(f"Sonntag: {name} | Predigt {data.get('predigtRef')} ({len(predigt)} V) "
          f"| Psalm {data.get('psalmRef')} ({len(psalm)} V)")
    return {
        "name": name,
        "datum": (data.get("datum") or "").strip(),
        "spruchText": (data.get("spruchText") or "").strip(),
        "spruchCite": (data.get("spruchCite") or "").strip(),
        "predigtRef": (data.get("predigtRef") or "").strip(),
        "predigtLU17": predigt,
        "psalmRef": (data.get("psalmRef") or "").strip(),
        "psalmLU17": psalm,
    }


def build_prompt(today: datetime, daily: dict, so: dict) -> str:
    iso = today.strftime("%Y-%m-%d")
    wochentag = WT[today.weekday()]

    def block(verse):
        return "\n".join(f"{n} {t}" for n, t in verse)

    def nums(verse):
        return [n for n, _ in verse]

    d_nums, p_nums, ps_nums = nums(daily["LU17"]), nums(so["predigtLU17"]), nums(so["psalmLU17"])

    return f"""Du ergaenzst die Tagesdaten fuer die Andachts-App "Mein Bibeltag".

HEUTE ist {wochentag}, der {de_long(today)} ({iso}), Zeitzone Europe/Berlin.

Alle Bibelstellen und ihr Luther-2017-Wortlaut stehen bereits FEST
(Quelle: die-bibel.de). Sie duerfen NICHT geaendert werden. Deine Aufgabe ist
ausschliesslich: die Schlachter-2000-Fassungen, kurze Titel und den Impuls.

=== TAGESLESE: {daily["ref"]} ({len(d_nums)} Verse, {d_nums[0]}-{d_nums[-1]}) ===
{block(daily["LU17"])}

=== PREDIGTTEXT: {so["predigtRef"]} ({len(p_nums)} Verse, {p_nums[0]}-{p_nums[-1]}) ===
{block(so["predigtLU17"])}

=== WOCHENPSALM: {so["psalmRef"]} ({len(ps_nums)} Verse) ===
{block(so["psalmLU17"])}

Gib AUSSCHLIESSLICH ein gueltiges JSON-Objekt zurueck (kein Markdown, keine
Code-Fences, kein erklaerender Text davor oder danach):

{{
  "updated": "{de_long(today)}",
  "season": "<Kirchenjahreszeit zum {de_long(today)}, z.B. Trinitatiszeit / Advent / Passionszeit>",
  "sunday": {{
    "name": "{so["name"]}",
    "date": "<Sonntag, {so["datum"]}>",
    "spruch": {{ "text": "{so["spruchText"]}", "cite": "{so["spruchCite"]}" }},
    "predigt": {{
      "ref": "{so["predigtRef"]}",
      "title": "<kurzer, treffender Titel fuer diesen Predigttext>",
      "url": "{PREDIGT_URL.format(date=so["iso"])}",
      "LU17": [[{p_nums[0]}, "erster Vers wie oben vorgegeben"]],
      "SCH2000": [[{p_nums[0]}, "erster Vers nach Schlachter 2000"]]
    }},
    "psalm": {{
      "ref": "{so["psalmRef"]}",
      "note": "Wochenpsalm",
      "LU17": [[{ps_nums[0]}, "erster Vers wie oben vorgegeben"]],
      "SCH2000": [[{ps_nums[0]}, "erster Vers nach Schlachter 2000"]]
    }},
    "impuls": "<4-6 Saetze zum Predigttext und Wochenspruch>"
  }},
  "daily": {{
    "ref": "{daily["ref"]}",
    "title": "<kurzer, treffender Titel fuer diese Tageslese>",
    "url": "{OEAB_URL.format(date=iso)}",
    "LU17": [[{d_nums[0]}, "erster Vers wie oben vorgegeben"]],
    "SCH2000": [[{d_nums[0]}, "erster Vers nach Schlachter 2000"]]
  }}
}}

Regeln:
- Verse als Arrays [Versnummer, "Text"].
- Jede SCH2000-Liste enthaelt DIESELBEN Versnummern wie die zugehoerige
  LU17-Liste, vollstaendig: Tageslese {len(d_nums)}, Predigttext {len(p_nums)},
  Psalm {len(ps_nums)} Verse. Eine leere oder verkuerzte Liste ist ein Fehler.
- Beim Psalm koennen Versnummern Luecken haben (z.B. 2,3,9,10) - genau diese
  Nummern uebernehmen, keine ergaenzen.
- Bibeltext wortgetreu nach Schlachter 2000.
- Die LU17-Listen werden ohnehin ersetzt; gib sie kurz an, sie sind nicht wichtig.
- Deutsche Typografie im Fliesstext.
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

    # Beides aus der maßgeblichen Quelle holen
    daily = fetch_oeab(today.strftime("%Y-%m-%d"))

    # Sonntag der laufenden Kirchenwoche = letzter Sonntag heute-oder-davor
    sonntag_dt = today - timedelta(days=(today.weekday() + 1) % 7)
    so = fetch_sonntag(sonntag_dt.strftime("%Y-%m-%d"))
    so["iso"] = sonntag_dt.strftime("%Y-%m-%d")
    if not so["datum"]:
        so["datum"] = de_long(sonntag_dt)

    client = anthropic.Anthropic()

    # Die API ist gelegentlich überlastet – mit wachsender Wartezeit wiederholen.
    message = None
    letzte = None
    for versuch in range(1, 5):
        try:
            with client.messages.stream(
                model=MODEL,
                max_tokens=32000,
                output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
                messages=[{"role": "user", "content": build_prompt(today, daily, so)}],
            ) as stream:
                message = stream.get_final_message()
            break
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            letzte = e
            status = getattr(e, "status_code", None)
            if status is not None and status not in (408, 409, 429, 500, 502, 503, 504, 529):
                raise  # echter Fehler – nicht wiederholen
            wartezeit = 15 * versuch
            print(f"API-Versuch {versuch}/4 fehlgeschlagen ({status or type(e).__name__}) "
                  f"– neuer Versuch in {wartezeit}s")
            time.sleep(wartezeit)

    if message is None:
        raise RuntimeError(f"API nicht erreichbar: {letzte}")

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

    # Alles aus der Quelle hart setzen - das Modell kann es nicht veraendern.
    data["updated"] = de_long(today)
    data["daily"]["ref"] = daily["ref"]
    data["daily"]["LU17"] = daily["LU17"]

    s_ = data["sunday"]
    s_["name"] = so["name"]
    s_["date"] = "Sonntag, " + so["datum"]
    s_["spruch"] = {"text": so["spruchText"], "cite": so["spruchCite"]}
    s_["predigt"]["ref"] = so["predigtRef"]
    s_["predigt"]["LU17"] = so["predigtLU17"]
    s_["predigt"]["url"] = PREDIGT_URL.format(date=so["iso"])
    s_["psalm"]["ref"] = so["psalmRef"]
    s_["psalm"]["LU17"] = so["psalmLU17"]
    s_["psalm"]["note"] = "Wochenpsalm"

    # Schlachter-Fassungen pruefen und notfalls gezielt nachfordern
    def sichere_schlachter(ziel: dict, ref: str, lu: list, label: str) -> None:
        nums = [n for n, _ in lu]
        sch = ziel.get("SCH2000") or []
        if [n for n, _ in sch] == nums:
            return
        print(f"{label}: Schlachter unvollstaendig ({len(sch)}/{len(nums)}) - fordere nach.")
        try:
            nach = client.messages.create(
                model=MODEL,
                max_tokens=16000,
                output_config={"format": {"type": "json_schema", "schema": {
                    "type": "object", "additionalProperties": False,
                    "required": ["SCH2000"],
                    "properties": {"SCH2000": {"type": "array", "items": {"type": "array"}}},
                }}},
                messages=[{"role": "user", "content":
                    f'Gib {ref} nach der Schlachter-2000-Uebersetzung als JSON zurueck: '
                    f'{{"SCH2000": [[Versnummer, "Verstext"], ...]}} - '
                    f'genau diese Versnummern: {nums} '
                    f'({len(nums)} Stueck), vollstaendig und wortgetreu.'}],
            )
            kand = json.loads("".join(b.text for b in nach.content if b.type == "text")).get("SCH2000") or []
            if [n for n, _ in kand] == nums:
                ziel["SCH2000"] = kand
                print(f"{label}: Schlachter nachgeliefert.")
            else:
                raise ValueError(f"weiterhin {len(kand)}/{len(nums)} Verse")
        except Exception as e:  # noqa: BLE001
            print(f"{label}: Nachforderung fehlgeschlagen ({e}) - App zeigt Luther als Ersatz.")
            ziel["SCH2000"] = []

    sichere_schlachter(data["daily"], daily["ref"], daily["LU17"], "Tageslese")
    sichere_schlachter(s_["predigt"], so["predigtRef"], so["predigtLU17"], "Predigttext")
    sichere_schlachter(s_["psalm"], so["psalmRef"], so["psalmLU17"], "Wochenpsalm")

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
