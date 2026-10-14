# -*- coding: utf-8 -*-
"""verifica_acte.py — trec actele unei colecții prin lista de legislație? Verificarea dintre cele
două jumătăți: aducerea (`adu_lege.py`) și lista (`build_legislation_list.py`).

Cazul care a cerut-o, 12 octombrie 2026: o colecție cu 53 de acte, din care numai 19 aveau toate
câmpurile pe care le cere lista; restul ieșeau din listă tăcut sau cu rânduri ciuntite, iar
autorul trebuia să ceară de fiecare dată verificarea. Nicio comandă nu spunea, pe o colecție, care
acte sunt incomplete, care sunt dubluri și care forme nu se deosebesc.

Ce raportează, pe fiecare item `statute` din colecție:

- **câmpurile listei** lipsă — `shortTitle`, `nameOfAct`, `history` (formatul din
  `build_legislation_list.fmt`);
- **câmpurile regulii** lipsă — `dateEnacted`, rândul de versiune din `extra`, `url`
  (`citari-zotero.md` §G);
- `publicLawNumber` ținut în locul lui `history`;
- **dublurile**: același cod și aceeași formă (aceeași pagină în `url`, ori același rând de
  versiune);
- **formele nedeosebite în text**: același cod și același titlu scurt la mai multe forme. Lista
  le ține pe toate sau pe niciuna, fiindcă textul le citează după titlul scurt. Cum se numește în
  text o formă anume e o decizie a autorului, nu a uneltei; aici doar se arată unde e nevoie de ea.

Read-only: merge cu Zotero deschis.

    python verifica_acte.py --colectie "proiect/acte"            # raportul
    python verifica_acte.py --colectie "proiect/acte" --propune  # + titlurile scurte deductibile
    python verifica_acte.py --colectie "proiect/acte" --amana    # le lasă în scrieri_amanate

`--amana` lasă numai titlurile scurte deduse din denumire și cod („Legea 76/2002”); celelalte
câmpuri cer citirea actului pe Portal și se refac cu `adu_lege.py`.
"""
import argparse, os, re, sys
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass
AICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AICI)
import zot  # noqa: E402

CAMPURI_LISTA = ("shortTitle", "nameOfAct", "history")
VERSIUNE = re.compile(r"^(Versiune .+|Forma publicată.+)$", re.M)


def _pagina(url):
    m = re.search(r"DetaliiDocument(?:Afis)?/(\d+)", url or "")
    return m.group(0) if m else ""


def acte(colectie):
    """[{cheie, campuri}] pentru itemii `statute` din colecție (fără coș). O singură conexiune.
    `colectie=None` înseamnă toată biblioteca: regula se aplică oricărui act adus, nu unei colecții."""
    if colectie is not None:
        g = zot.find_collection(colectie, verifica_api=False)
        if not g:
            raise SystemExit("Colecția %r nu există." % colectie)
    con = zot._ro()
    try:
        if colectie is None:
            rows = con.execute(
                "SELECT i.itemID, i.key FROM items i JOIN itemTypes t USING(itemTypeID) WHERE t.typeName='statute' "
                "AND i.itemID NOT IN (SELECT itemID FROM deletedItems)").fetchall()
        else:
            rows = con.execute(
                "SELECT i.itemID, i.key FROM collectionItems ci JOIN items i USING(itemID) "
                "JOIN itemTypes t USING(itemTypeID) WHERE ci.collectionID=? AND t.typeName='statute' "
                "AND i.itemID NOT IN (SELECT itemID FROM deletedItems)", (g[1],)).fetchall()
        out = []
        for iid, cheie in rows:
            f = dict(con.execute(
                "SELECT f.fieldName, v.value FROM itemData d JOIN fields f USING(fieldID) "
                "JOIN itemDataValues v USING(valueID) WHERE d.itemID=?", (iid,)).fetchall())
            cr = [c for (c,) in con.execute(
                "SELECT c.lastName FROM itemCreators ic JOIN creators c USING(creatorID) WHERE ic.itemID=? "
                "ORDER BY orderIndex", (iid,)).fetchall()]
            out.append({"cheie": cheie, "campuri": f, "creatori": cr})
        return out
    finally:
        con.close()


def raport(colectie, spune=True):
    """Întoarce {"incomplete": [...], "dubluri": [...], "nedeosebite": [...], "total": n}."""
    A = acte(colectie)
    incomplete, dubluri, nedeosebite = [], [], []
    pe_cod = defaultdict(list)
    for x in A:
        f = x["campuri"]
        v = VERSIUNE.search(f.get("extra") or "")
        lipsa = [c for c in CAMPURI_LISTA if not (f.get(c) or "").strip()]
        lipsa += [c for c, ok in (("dateEnacted", f.get("dateEnacted")), ("rând de versiune în extra", v),
                                  ("url", f.get("url"))) if not ok]
        note = []
        if not f.get("history") and f.get("publicLawNumber"):
            note.append("are publicLawNumber în locul lui history: %r" % f["publicLawNumber"])
        if x.get("creatori"):
            # Regula din §G: actul normativ n-are creator; emitentul stă pe primul rând din `extra`.
            note.append("are creator (%s); emitentul se ține în extra" % "; ".join(x["creatori"]))
        if lipsa or note:
            incomplete.append((x["cheie"], f.get("shortTitle") or f.get("nameOfAct", "")[:60], lipsa, note))
        pe_cod[f.get("codeNumber") or "(fără cod)"].append((x["cheie"], f, v.group(1) if v else ""))
    for cod, forme in pe_cod.items():
        if len(forme) < 2:
            continue
        vazute = defaultdict(list)
        for cheie, f, v in forme:
            vazute[_pagina(f.get("url")) or v or "(fără formă)"].append(cheie)
        for forma, chei in vazute.items():
            if len(chei) > 1:
                dubluri.append((cod, forma, chei))
        scurte = defaultdict(list)
        for cheie, f, v in forme:
            scurte[f.get("shortTitle") or ""].append((cheie, v))
        for st, chei in scurte.items():
            if st and len(chei) > 1:
                nedeosebite.append((cod, st, chei))
    if spune:
        print("Acte în colecție: %d | complete: %d | incomplete: %d | dubluri: %d | titluri scurte "
              "comune mai multor forme: %d" % (len(A), len(A) - len(incomplete), len(incomplete),
                                                len(dubluri), len(nedeosebite)))
        for cheie, nume, lipsa, note in incomplete:
            print("  INCOMPLET %s %s — lipsă: %s%s" % (cheie, nume[:50], ", ".join(lipsa) or "—",
                                                       ("; " + "; ".join(note)) if note else ""))
        for cod, forma, chei in dubluri:
            print("  DUBLURĂ %s, aceeași formă (%s): %s" % (cod, forma[:60], ", ".join(chei)))
        for cod, st, chei in nedeosebite:
            print("  NEDEOSEBITE %s: %d forme cu titlul scurt %r — lista le ține pe toate sau pe "
                  "niciuna; cum se numește în text o formă anume decide autorul:" % (cod, len(chei), st))
            for cheie, v in chei:
                print("      %s %s" % (cheie, v[:80] or "(fără rând de versiune)"))
    return {"incomplete": incomplete, "dubluri": dubluri, "nedeosebite": nedeosebite, "total": len(A)}


def titluri_propuse(colectie):
    """[(cheie, titlu scurt dedus)] pentru actele fără titlu scurt, unde tipul se recunoaște."""
    import adu_lege
    out = []
    for x in acte(colectie):
        f = x["campuri"]
        if not (f.get("shortTitle") or "").strip():
            s = adu_lege.scurt_implicit(f.get("nameOfAct"), f.get("codeNumber"))
            out.append((x["cheie"], f.get("nameOfAct", "")[:70], s))
    return out


def operatii_creatori(colectie):
    """Operațiile care aduc actele cu creator la regula din §G: creatorul iese, iar emitentul rămâne
    pe primul rând din `extra` — se mută acolo numai dacă primul rând nu-l are deja."""
    ops = []
    for x in acte(colectie):
        if not x.get("creatori"):
            continue
        emitent = "; ".join(x["creatori"])
        extra = x["campuri"].get("extra") or ""
        if extra.split("\n")[0].strip() != emitent:
            ops.append({"op": "set_field", "item": x["cheie"], "camp": "extra",
                        "valoare": emitent + ("\n" + extra if extra else "")})
        ops.append({"op": "set_creators", "item": x["cheie"], "creatori": []})
    return ops


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--colectie", required=True, help="nume, cale („proiect/acte”) sau cheie")
    p.add_argument("--propune", action="store_true", help="arată titlurile scurte deductibile")
    p.add_argument("--amana", action="store_true",
                   help="lasă titlurile scurte deduse în scrieri_amanate (le scrie primul ciclu)")
    a = p.parse_args()
    r = raport(a.colectie)
    if a.propune or a.amana:
        prop = titluri_propuse(a.colectie)
        print("\nTitluri scurte deduse din denumire și cod (%d):" % sum(1 for *_, s in prop if s))
        for cheie, nume, s in prop:
            print("  %s %-45s -> %s" % (cheie, nume[:45], s or "NU SE POATE DEDUCE: dă-l de mână"))
        if a.amana:
            import scrieri_amanate as sa
            ops = [{"op": "set_field", "item": k, "camp": "shortTitle", "valoare": s} for k, _, s in prop if s]
            ops += operatii_creatori(a.colectie)
            if ops:
                print("\nLăsat în scrieri amânate: %s" % sa.lasa(
                    "titluri scurte pentru actele din %s" % a.colectie, ops, cine="verifica_acte.py"))
    return 1 if (r["incomplete"] or r["dubluri"]) else 0


if __name__ == "__main__":
    sys.exit(main())
