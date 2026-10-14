#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_legislation_list.py — generate a chronological "Legislation and case law" list.

Companion to build_manuscris.py. A CSL style covers the ACADEMIC apparatus only; statutes,
secondary legislation and case law follow a custom house format (see SKILL.md §G), so they
get their own list, separate from the academic "References".

Pipeline:
  Zotero collection (statutes/cases) via the local web API (format=json)
    -> FILTER to what is actually CITED in the manuscript body
    -> FORMAT (house style):
         laws:   {shortTitle}. {nameOfAct}, {history}.        (history = official gazette ref)
         HCL/secondary (nameOfAct starts "Hotărârea Consiliului Local"):  {nameOfAct}.
         cases:  {nameOfAct}[, {history}].
    -> SORT chronologically, DETERMINISTICALLY
       (year -> fine date from gazette / decision date -> type -> issuer -> act number)
    -> REPLACE the "# Legislation and case law" section in the manuscript.

Filtering rule: an item counts as cited if its codeNumber (e.g. "227/2015") appears in the
body, OR (for laws) its short title appears. Short titles whose base repeats across the
collection (e.g. two "Fiscal Code"s) require the year-qualified form so they stay distinct.

Usage:
  python build_legislation_list.py --manuscript <md> --collection <ZOTERO_COLLECTION_KEY>
        [--user-id N] [--source zotero|local --local-json <file>] [--write] [--header "..."]
  (default = DRY-RUN: prints the proposed section. --write replaces it, with a .bak backup.)
"""
import re, sys, os, json, shutil, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import zot

# Force UTF-8 stdout: Windows cp1252 consoles crash on diacritics/emoji in status prints
# (after the work is done), making a successful run look failed.
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

DEF_HEADER = "# Legislation and case law"
NOTE = ("*Statutes, case law, and international instruments, in chronological order. "
        "In-text references use the short title.*")
RO_MONTHS = {"ianuarie":1,"februarie":2,"martie":3,"aprilie":4,"mai":5,"iunie":6,
             "iulie":7,"august":8,"septembrie":9,"octombrie":10,"noiembrie":11,"decembrie":12}

def parse_args(argv):
    a = {"md": None, "coll": None, "uid": None, "source": "zotero",
         "local": None, "write": False, "header": DEF_HEADER}
    i = 0
    while i < len(argv):
        t = argv[i]
        if t == "--manuscript": a["md"] = argv[i+1]; i += 2
        elif t == "--collection": a["coll"] = argv[i+1]; i += 2
        elif t == "--user-id": a["uid"] = argv[i+1]; i += 2
        elif t == "--source": a["source"] = argv[i+1]; i += 2
        elif t == "--local-json": a["local"] = argv[i+1]; i += 2
        elif t == "--header": a["header"] = argv[i+1]; i += 2
        elif t == "--write": a["write"] = True; i += 1
        else: i += 1
    if not a["md"]: sys.exit("Lipsește --manuscript <md>.")
    if a["source"] == "zotero" and not a["coll"]: sys.exit("Lipsește --collection <KEY> (sursa zotero).")
    if a["source"] == "local" and not a["local"]: sys.exit("Lipsește --local-json <file> (sursa local).")
    return a

def _din(s):
    if not s: return None
    m = re.search(r'din\s+(\d{1,2})\s+([a-zăâîșț]+)\s+(\d{4})', s, re.I)
    return (RO_MONTHS.get(m.group(2).lower(), 13), int(m.group(1))) if m else None

def fine_date(it):
    # cases sort by decision date (in nameOfAct); laws by gazette publication date.
    if it["type"] == "case": return _din(it["nameOfAct"]) or _din(it["mo"]) or (13, 32)
    return _din(it["mo"]) or _din(it["nameOfAct"]) or (13, 32)

def _norm(d):
    code = d.get("codeNumber") or d.get("code") or d.get("id") or ""
    name = d.get("nameOfAct") or d.get("title") or ""
    if re.match(r'\s*Hotărârea Consiliului Local', name): typ = "hcl"
    elif re.match(r'\s*(Curtea|Tribunalul|Înalta Curte|Judecătoria)', name): typ = "case"
    else: typ = "law"
    de = d.get("dateEnacted") or str(d.get("year") or "")
    ym = re.search(r'(\d{4})', de)
    extra = d.get("extra") or ""
    mod = re.search(r"^Modificată prin: (.+)$", extra, re.M)
    apl = re.search(r"^Aplicabilă:? (?:de la )?(.+)$", extra, re.M)
    return {"type": typ, "shortTitle": d.get("shortTitle",""), "nameOfAct": name,
            "mo": d.get("history") or d.get("mo"), "year": int(ym.group(1)) if ym else None,
            "code": code, "versiune": _versiune(extra), "url": d.get("url") or "",
            "modificata_prin": mod.group(1).strip() if mod else "",
            # „Aplicabilă până la …" e forma publicată a unui act modificat ulterior: începutul aplicării
            # nu se scrie, fiindcă textul poate amâna intrarea în vigoare (`adu_lege`, 12.10.2026).
            "aplicabila": ("de la " if apl and "–" not in apl.group(1) and not apl.group(1).startswith("până")
                           else "") + apl.group(1).strip() if apl else ""}


def acte_scurte(modificata_prin):
    """„Legea nr. 296/2023 (MO …); Ordonanța de urgență a Guvernului nr. 115/2023 (MO …)" ->
    ["Legea 296/2023", "OUG 115/2023"]: forma în care textul numește decizia care a produs o formă."""
    import adu_lege
    out = []
    for parte in re.split(r";\s*(?![^()]*\))", modificata_prin or ""):
        m = re.match(r"\s*(.+?) nr\. ([\d./]+)", parte)
        if m:
            s = adu_lege.scurt_implicit(m.group(1), m.group(2))
            if s:
                out.append(s)
    return out


def _versiune(extra):
    """Rândul de versiune din `extra` („Versiune consolidată la …, Portal Legislativ …"), sau "".
    E sursa fișierului, nu o dată a actului: în citare vine după Monitorul Oficial, cum cere
    corespondența cu Bluebook (baza de date, cu actualitatea ei) — vezi SKILL.md, secțiunea G."""
    m = re.search(r"^(Versiune .+|Forma publicată.+)$", extra, re.M)
    return m.group(1).strip().rstrip(".") if m else ""

def load_zotero(uid, coll):
    url = f"http://127.0.0.1:23119/api/users/{uid}/collections/{coll}/items?format=json&limit=500"
    raw = json.loads(urllib.request.urlopen(url, timeout=30).read().decode())
    out = []
    for it in raw:
        d = it.get("data", it)
        if d.get("itemType") == "attachment": continue
        out.append(_norm(d))
    return out

def load_local(path):
    j = json.load(open(path, encoding="utf-8"))
    out = []
    if isinstance(j, dict):
        for grp in ("laws", "hcl", "cases"):
            for d in j.get(grp, []): out.append(_norm(d))
    else:
        for d in j: out.append(_norm(d))
    return out

def base_short(s): return re.sub(r'\s*\(\d{4}\)\s*$', '', s).strip()

def cited_filter(items, body):
    bases = {}
    for it in items:
        if it["type"] == "law":
            bases[base_short(it["shortTitle"])] = bases.get(base_short(it["shortTitle"]), 0) + 1
    forme = {}
    for it in items:
        if it["type"] == "law" and it["code"]:
            forme[(it["code"], it["shortTitle"])] = forme.get((it["code"], it["shortTitle"]), 0) + 1
    kept, dropped = [], []
    for it in items:
        tokens = []
        mai_multe = forme.get((it["code"], it["shortTitle"]), 0) > 1
        if mai_multe and it.get("modificata_prin"):
            # Mai multe forme ale aceluiași act: textul numește forma prin decizia care a produs-o,
            # „Codul fiscal, modificat prin Legea 296/2023" (regula din 12.10.2026, §G). Titlul scurt
            # sau codul singure le-ar ține pe toate.
            # Prima mențiune: „Codul fiscal, modificat prin Legea 296/2023 (aplicabil …)"; următoarele:
            # „Codul fiscal (mod. Legea 296/2023, …)" — decizia autorului, 12.10.2026.
            for s in acte_scurte(it["modificata_prin"]):
                tokens += ["%s, modificat prin %s" % (it["shortTitle"], s), "%s (mod. %s" % (it["shortTitle"], s)]
        else:
            if it["type"] == "law" and it["shortTitle"]:
                b = base_short(it["shortTitle"])
                tokens.append(it["shortTitle"] if bases.get(b, 0) > 1 else b)
            if it["code"]: tokens.append(it["code"])
        (kept if any(t and t in body for t in tokens) else dropped).append(it)
    # Perioada de aplicare se arată numai când în listă rămân mai multe forme ale aceluiași act.
    pastrate = {}
    for it in kept:
        if it["type"] == "law" and it["code"]:
            pastrate[it["code"]] = pastrate.get(it["code"], 0) + 1
    for it in kept:
        it["arata_perioada"] = pastrate.get(it["code"], 0) > 1
    return kept, dropped

def fmt(it):
    st, name, mo = it["shortTitle"], it["nameOfAct"].strip(), it["mo"]
    if it["type"] == "law":
        if it.get("modificata_prin"):
            # Forma numită prin decizie, cum o numește Monitorul Oficial; linkul formei de pe Portal
            # rămâne sursa textului (regula din 12.10.2026, `citari-zotero.md` §G).
            acte = [p.strip() for p in re.split(r";\s*(?![^()]*\))", it["modificata_prin"]) if p.strip()]
            numite = acte[0] if len(acte) == 1 else ", ".join(acte[:-1]) + " și " + acte[-1]
            coada = (f", {mo}" if mo else "") + (", cu modificările și completările ulterioare, inclusiv cele "
                                                f"aduse prin {numite}")
            if it.get("arata_perioada") and it.get("aplicabila"):
                coada += f"; aplicabilă {it['aplicabila']}"
            return f"{st}. {name}{coada}." + (f" <{it['url']}>." if it.get("url") else "")
        v = it.get("versiune") or ""
        coada = (f", {mo}" if mo else "") + (f"; {v[0].lower() + v[1:]}" if v else "")
        return f"{st}. {name}{coada}."
    if it["type"] == "hcl": return name.rstrip(".") + "."
    return f"{name.rstrip('.')}, {mo}." if mo else name.rstrip(".") + "."

def sort_key(it):
    grp = {"law": 0, "case": 1, "hcl": 2}.get(it["type"], 3)
    name = it["nameOfAct"]
    issuer = 0 if "Oradea" in name else (1 if "Timișoara" in name else 0)
    m = re.match(r'(\d+)', it["code"] or "")
    # Formele aceluiași act, în ordinea aplicării (data de început din „Aplicabilă”).
    a = re.match(r'(?:de la )?(\d{2})\.(\d{2})\.(\d{4})', it.get("aplicabila") or "")
    forma = (a.group(3), a.group(2), a.group(1)) if a else ("",)
    return (it["year"] or 9999, *fine_date(it), grp, issuer, int(m.group(1)) if m else 0, forma)

def main():
    a = parse_args(sys.argv[1:])
    if a["source"] == "zotero":
        uid = a["uid"] or zot.detect_user_id()
        if not uid: sys.exit("user id nedetectat. Dă --user-id N sau setează ZOTERO_USER_ID.")
        items = load_zotero(uid, a["coll"])
        # Ce n-are câmpurile listei iese tăcut sau ciuntit; se spune înainte, cu cheia fiecăruia
        # (`verifica_acte.py`, 12.10.2026).
        import verifica_acte
        verifica_acte.raport(a["coll"])
        print()
    else:
        items = load_local(a["local"])
    text = open(a["md"], encoding="utf-8").read()
    body = text.split("\n" + a["header"])[0]
    kept, dropped = cited_filter(items, body)
    entries = sorted(kept, key=sort_key)
    section = "\n".join([a["header"], "", NOTE, ""] + [l for it in entries for l in (fmt(it), "")]).rstrip() + "\n"

    print(f"[sursă: {a['source']}] {len(items)} itemi | citați: {len(kept)} | scoși (necitați): {len(dropped)}")
    for it in dropped: print(f"   - scos: {it['shortTitle'] or it['nameOfAct'][:50]} [{it['code']}]")
    print("\n" + "="*70 + "\n" + section + "="*70)

    if not a["write"]:
        print("(dry-run: nimic scris. Adaugă --write ca să înlocuiești în manuscris.)"); return
    if a["header"] not in text: sys.exit(f"ABORT: '{a['header']}' negăsit în {a['md']}")
    bak = a["md"] + ".pre_leglist.bak"; shutil.copy2(a["md"], bak)
    before = text.split("\n" + a["header"])[0].rstrip("\n")
    open(a["md"], "w", encoding="utf-8").write(before + "\n\n\n" + section)
    print(f"✅ scris. backup -> {bak}")

if __name__ == "__main__":
    main()
