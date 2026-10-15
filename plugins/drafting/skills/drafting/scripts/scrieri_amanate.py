# -*- coding: utf-8 -*-
"""scrieri_amanate.py — ce n-ai putut scrie fiindcă Zotero era deschis, scris de cine ajunge primul.

Problema, măsurată cu șapte sesiuni pe aceeași mașină: fiecare are de scris puțin și rar, dar
scrierea cere baza liberă, iar aplicația e ținută deschisă de cineva. Fiecare sesiune fie
așteaptă, fie abandonează sarcina — și, când în sfârșit se eliberează, fiecare plătește **câte
un ciclu de închidere** de două-patru minute.

Aici sarcinile se adună într-un dosar, iar prima sesiune care ajunge la baza liberă le rulează
pe toate **într-un singur ciclu**. Câștigul nu e că nu se pierde munca — e că se plătește un
ciclu pentru mai multe sesiuni.

## Ce se poate amâna, și de ce doar atât

Sarcina e **date, nu cod**. Cine o lasă descrie ce vrea scris, într-un vocabular fix; cine o
rulează execută **codul lui**, nu al altcuiva. Asta e diferența care face lucrul acceptabil: fără
ea, prima sesiune liberă ar rula scripturi scrise de altă sesiune, iar cine răspunde de rezultat
n-ar mai fi nimeni.

Operațiile recunoscute — toate din `zot.py`, niciuna nouă:

    {"op": "add_item", "tip": "journalArticle", "campuri": {...}, "nume": "raport",
     "creatori": [["author", "Familie", "Prenume"]], "colectie": "proiect-exemplu/metodologie"}
    {"op": "attach_pdf", "parinte": "raport", "cale": "C:/…/x.pdf", "titlu": "…", "nume": "pdf"}
    {"op": "add_to_collection", "item": "raport", "colectie": "cadru normativ"}
    {"op": "ensure_cached", "atasament": "pdf", "cale": "C:/…/x.pdf"}
    {"op": "set_field", "item": "ABCD1234", "camp": "shortTitle", "valoare": "Legea 1/2001"}
    {"op": "relateaza", "a": "raport", "b": "EFGH5678"}
    {"op": "set_creators", "item": "ABCD1234", "creatori": []}      # [] scoate creatorii
    {"op": "muta_atasament", "atasament": "WXYZ5678", "parinte": "capitol"}   # PDF-ul trece sub alt item
    {"op": "sterge_camp", "item": "ABCD1234", "camp": "pages"}
    {"op": "fuzioneaza", "pastrat": "ABCD1234", "dublura": "EFGH5678"}   # notele trec, dublura la coș

`set_field` și `relateaza` au intrat pe 12 octombrie 2026, din cazul real: o corecție de câmp pe
actele unei colecții nu putea lăsa nicio sarcină cu Zotero deschis, iar un act adus cu Zotero
deschis nu se putea amâna întreg, fiindcă formele lui se leagă „Related" de celelalte. `add_item`
primește și `daca_exista` (`intoarce`, `refuza`, `creeaza`), cu același sens ca în `zot.add_item`:
formele succesive ale unui act au același cod și se creează explicit.

**`nume` leagă pașii între ei.** Operațiile care creează ceva — `add_item` și `attach_pdf` — își
pot boteza rezultatul, iar pașii următori îl cer pe nume, în locul unei chei pe care încă n-o are
nimeni. Așa se amână o sursă nouă ÎNTREAGĂ: item, fișier și text memoizat, într-un singur ciclu.

Ce nu încape aici rămâne la proprietarul lui. Vocabularul se lărgește dacă apare un caz real,
nu preventiv.

## Folosire

    python scrieri_amanate.py --arata          # ce așteaptă, și de la cine
    python scrieri_amanate.py --ruleaza        # le scrie pe toate, într-un ciclu (Zotero ÎNCHIS)
    python scrieri_amanate.py --ruleaza --sec  # arată ce s-ar face, fără să scrie

Ca funcție, din sesiunea care e blocată:

    import scrieri_amanate as sa
    sa.lasa("șase acte normative", [{"op": "add_item", ...}, ...])

## Ce se întâmplă cu rezultatul

Fiecare sarcină primește un fișier de rezultat lângă ea: ce s-a scris, ce chei au ieșit, ce a
căzut. Proprietarul îl citește când se întoarce — sesiunea lui poate să nu mai existe, fișierul
rămâne.

**Fiecare sarcină rulează în punctul ei de salvare.** Dacă una cade, se derulează înapoi numai
ea, iar celelalte se scriu. Fără asta, o sarcină greșită a unei sesiuni ar anula munca tuturor —
iar cine a lăsat-o n-ar afla niciodată de ce.
"""
import io, json, os, sys, time

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

AICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AICI)
import zot  # noqa: E402
import pdf_text_cache as ptc  # noqa: E402

# Pe D:, cu restul fișierelor de lucru (`zot.LUCRU`, decizia autorului din 12.10.2026).
DOSAR = os.path.join(zot.LUCRU, "scrieri-amanate")
VERSIUNE = 1
OPERATII = ("add_item", "attach_pdf", "add_to_collection", "ensure_cached", "set_field", "relateaza",
            "set_creators", "la_cos", "muta_atasament", "sterge_camp", "adauga_in_extra", "scoate_din_colectie", "add_collection",
            "fuzioneaza")


def _asigura_dosar():
    os.makedirs(DOSAR, exist_ok=True)
    return DOSAR


def lasa(eticheta, operatii, cine=None):
    """Lasă o sarcină pentru prima sesiune care ajunge la baza liberă. Întoarce calea fișierului.

    Se validează ACUM, nu la rulare: o sarcină cu o operație necunoscută respinsă peste trei ore,
    de altcineva, e o eroare fără proprietar."""
    for i, op in enumerate(operatii):
        if op.get("op") not in OPERATII:
            raise ValueError("operația %d e %r; recunoscute: %s"
                             % (i, op.get("op"), ", ".join(OPERATII)))
    _asigura_dosar()
    # Ceasul si PID-ul nu ajung: doua sarcini lasate in aceeasi secunda de aceeasi sesiune
    # ies cu acelasi nume, iar a doua o sterge pe prima — tacut. Masurat la prima proba, unde
    # din trei sarcini a ramas una.
    baza = "%s-%d" % (time.strftime("%Y%m%d-%H%M%S"), os.getpid())
    n = 0
    while True:
        nume = "%s%s.json" % (baza, "" if n == 0 else "-%d" % n)
        cale = os.path.join(DOSAR, nume)
        if not os.path.exists(cale):
            break
        n += 1
    io.open(cale, "w", encoding="utf-8", newline="\n").write(json.dumps({
        "versiune": VERSIUNE,
        "eticheta": eticheta,
        "cine": cine or ("PID %d" % os.getpid()),
        "de_la": zot.now(),
        "operatii": operatii,
    }, ensure_ascii=False, indent=2) + "\n")
    return cale


def sarcini():
    """Sarcinile care așteaptă, cele mai vechi întâi. [(cale, dict)]."""
    if not os.path.isdir(DOSAR):
        return []
    out = []
    for nume in sorted(os.listdir(DOSAR)):
        if not nume.endswith(".json") or nume.endswith(".rezultat.json"):
            continue
        cale = os.path.join(DOSAR, nume)
        try:
            d = json.loads(io.open(cale, encoding="utf-8").read())
        except Exception as e:  # noqa: BLE001
            out.append((cale, {"eroare": "nu se poate citi: %s" % e}))
            continue
        out.append((cale, d))
    return out


def _titlu(cheie):
    """Cheia, cu titlul itemului alături dacă itemul există deja (read-only)."""
    try:
        con = zot._ro()
        r = con.execute("SELECT v.value FROM items i JOIN itemData d USING(itemID) JOIN itemDataValues v USING(valueID) "
                        "JOIN fields f USING(fieldID) WHERE i.key=? AND f.fieldName IN ('title','nameOfAct','caseName')",
                        (cheie,)).fetchone()
        con.close()
        return "%s „%s”" % (cheie, r[0][:70]) if r else str(cheie)
    except Exception:  # noqa: BLE001
        return str(cheie)


def _colectie(nume):
    """(key, id) pentru numele/calea/cheia dată, sau None. Se rezolvă ÎNAINTE de ciclu."""
    if not nume:
        return None
    return zot.find_collection(nume, verifica_api=False)


def _colectie_in_scriere(cur, cale):
    """(key, id) prin cursorul de scriere, pentru o colecție creată mai devreme în același ciclu (o altă sarcină din
    coadă), pe care conexiunea de citire n-o vede. `cale` = „părinte/copil" sau numele singur."""
    parti = [p for p in cale.split("/") if p]
    rows = cur.execute("SELECT c.key, c.collectionID, p.collectionName FROM collections c LEFT JOIN collections p "
                       "ON p.collectionID=c.parentCollectionID WHERE c.collectionName=? AND c.collectionID NOT IN "
                       "(SELECT collectionID FROM deletedCollections)", (parti[-1],)).fetchall()
    if len(parti) > 1:
        rows = [r for r in rows if r[2] == parti[-2]]
    return (rows[0][0], rows[0][1]) if len(rows) == 1 else None


def _executa(cur, op, chei):
    """O operație, în cursorul dat. Întoarce ce s-a produs, pentru raport."""
    fel = op["op"]
    if fel == "add_item":
        cid = None
        if op.get("colectie"):
            g = _colectie(op["colectie"])
            if not g:
                raise LookupError("colecția %r nu există" % op["colectie"])
            cid = g[1]
        creatori = [tuple(c) for c in (op.get("creatori") or [])]
        iid = zot.add_item(cur, op["tip"], op.get("campuri") or {}, creatori or None,
                           collection_id=cid, daca_exista=op.get("daca_exista", "intoarce"))
        cheie = zot.key_for_item_id(cur, iid)
        if op.get("nume"):          # nume local, ca pașii următori să-l poată numi
            chei[op["nume"]] = cheie
        return {"item": cheie}
    if fel == "attach_pdf":
        parinte = chei.get(op["parinte"], op["parinte"])
        pid = zot.item_id_for_key(cur, parinte)
        if pid is None:
            raise LookupError("itemul %r nu există" % parinte)
        if not os.path.exists(op["cale"]):
            raise FileNotFoundError(op["cale"])
        aid = zot.attach_pdf(cur, pid, op["cale"], title=op.get("titlu"))
        cheie = zot.key_for_item_id(cur, aid)
        # Si atasamentul isi poate numi rezultatul, ca `ensure_cached` sa-l poata cere. Fara asta,
        # o sursa noua nu se putea amana INTREAGA: item si fisier plecau, iar memoizarea ramanea
        # in urma, fiindca cerea tocmai cheia pe care nimeni n-o scria in tabela de nume. Masurat
        # pe 16 septembrie 2026, pe cinci rapoarte metodologice.
        if op.get("nume"):
            chei[op["nume"]] = cheie
        return {"atasament": cheie}
    if fel == "add_to_collection":
        # O colecție creată în aceeași sarcină (`add_collection` cu `nume`) nu se vede prin conexiunea de citire,
        # deci se ia din `chei`, cu id-ul ei.
        g = chei.get("colectie:" + op["colectie"]) or _colectie(op["colectie"]) or _colectie_in_scriere(cur, op["colectie"])
        if not g:
            raise LookupError("colecția %r nu există" % op["colectie"])
        iid = zot.item_id_for_key(cur, chei.get(op["item"], op["item"]))
        if iid is None:
            raise LookupError("itemul %r nu există" % op["item"])
        zot.add_to_collection(cur, iid, g[1])
        return {"filat": True}
    if fel == "ensure_cached":
        cheie = chei.get(op["atasament"], op["atasament"])
        text, era = ptc.ensure_cached(cur, cheie, op["cale"])
        return {"memoizat": len(text), "era_in_cache": era}
    if fel == "set_field":
        cheie = chei.get(op["item"], op["item"])
        iid = zot.item_id_for_key(cur, cheie)
        if iid is None:
            raise LookupError("itemul %r nu există" % cheie)
        tip = cur.execute("SELECT it.typeName FROM items i JOIN itemTypes it USING(itemTypeID) "
                          "WHERE i.itemID=?", (iid,)).fetchone()[0]
        # Câmpul se verifică pe tipul itemului: un câmp străin tipului se scrie fără eroare, dar
        # Zotero nu-l arată și nu-l exportă — corecția ar părea făcută și n-ar fi.
        if op["camp"] not in zot.campuri_valide(cur, tip):
            raise ValueError("câmpul %r nu există la tipul %s" % (op["camp"], tip))
        r = cur.execute("SELECT v.value FROM itemData d JOIN itemDataValues v USING(valueID) "
                        "WHERE d.itemID=? AND d.fieldID=?", (iid, zot.field_id(cur, op["camp"]))).fetchone()
        zot.set_field(cur, iid, zot.field_id(cur, op["camp"]), op["valoare"])
        zot.touch(cur, iid)
        return {"item": cheie, "camp": op["camp"], "era": r[0] if r else None}
    if fel == "set_creators":
        # Înlocuiește creatorii itemului cu lista dată; lista goală îi scoate. Cazul: actele
        # normative, care după regula din §G n-au creator (emitentul stă pe primul rând din `extra`).
        cheie = chei.get(op["item"], op["item"])
        iid = zot.item_id_for_key(cur, cheie)
        if iid is None:
            raise LookupError("itemul %r nu există" % cheie)
        era = cur.execute("SELECT ct.creatorType, c.lastName, c.firstName FROM itemCreators ic JOIN creators c "
                          "USING(creatorID) JOIN creatorTypes ct USING(creatorTypeID) WHERE ic.itemID=? "
                          "ORDER BY orderIndex", (iid,)).fetchall()
        cur.execute("DELETE FROM itemCreators WHERE itemID=?", (iid,))
        if op.get("creatori"):
            zot._set_creators(cur, iid, [tuple(c) for c in op["creatori"]])
        zot.touch(cur, iid)
        return {"item": cheie, "era": era}
    if fel == "la_cos":
        # Mută itemul la coș (reversibil, din interfață). Cazul: o dublură creată din greșeală.
        cheie = chei.get(op["item"], op["item"])
        iid = zot.item_id_for_key(cur, cheie)
        if iid is None:
            raise LookupError("itemul %r nu există" % cheie)
        cur.execute("INSERT OR IGNORE INTO deletedItems (itemID, dateDeleted) VALUES (?, ?)", (iid, zot.now()))
        zot.touch(cur, iid)
        return {"la_cos": cheie}
    if fel == "relateaza":
        a, b = chei.get(op["a"], op["a"]), chei.get(op["b"], op["b"])
        return {"legate": bool(zot.relateaza(cur, a, b)), "a": a, "b": b}
    if fel == "muta_atasament":
        # Atașamentul (cu adnotările și nota de cache, care stau pe el) trece sub alt item. Cazul: PDF-urile
        # capitolelor rămase sub volum sau sub alt capitol (13.10.2026).
        ak, pk = chei.get(op["atasament"], op["atasament"]), chei.get(op["parinte"], op["parinte"])
        aid, pid = zot.item_id_for_key(cur, ak), zot.item_id_for_key(cur, pk)
        if aid is None or pid is None:
            raise LookupError("atașamentul %r sau părintele %r nu există" % (ak, pk))
        era = cur.execute("SELECT parentItemID FROM itemAttachments WHERE itemID=?", (aid,)).fetchone()
        if era is None:
            raise LookupError("%r nu e atașament" % ak)
        cur.execute("UPDATE itemAttachments SET parentItemID=? WHERE itemID=?", (pid, aid))
        for i in (aid, pid) + ((era[0],) if era[0] else ()):
            zot.touch(cur, i)
        return {"atasament": ak, "parinte": pk, "era_sub": era[0]}
    if fel == "add_collection":
        # Colecție nouă (sau cea existentă cu același nume sub același părinte); `nume` o face numibilă de pașii
        # următori. Cazul: subcolecțiile pe versiuni ale unei lucrări (13.10.2026).
        parinte = _colectie(op["parinte"]) if op.get("parinte") else None
        if op.get("parinte") and not parinte:
            raise LookupError("colecția părinte %r nu există" % op["parinte"])
        cid = zot.add_collection(cur, op["colectie"], parinte[1] if parinte else None)
        cheie = cur.execute("SELECT key FROM collections WHERE collectionID=?", (cid,)).fetchone()[0]
        if op.get("nume"):
            chei[op["nume"]] = cheie
            chei["colectie:" + op["nume"]] = (cheie, cid)
        return {"colectie": op["colectie"], "cheie": cheie}
    if fel == "fuzioneaza":
        # Două iteme ale aceleiași lucrări: rămâne `pastrat`, `dublura` trece la coș. Ce rămâne o alege cine pune
        # sarcina, după regula autorului (15.10.2026): originalul (articolul publicat în revistă, nu o copie), apoi
        # cel care nu cere OCR, apoi cel citat, apoi oricare. Operația face doar partea mecanică, iar scopul ei e
        # al regulii: „să nu ștergi notele și versiunile originale, cu care se lucrează ușor".
        #   - notele dublurii trec la itemul păstrat; nota de cache a textului rămâne, fiindcă descrie fișierul
        #     dublurii, nu lucrarea;
        #   - atașamentele dublurii care au adnotări sau note trec și ele; celelalte merg la coș cu dublura,
        #     de unde se pot scoate;
        #   - itemul păstrat intră în toate colecțiile dublurii;
        #   - itemul păstrat primește `dc:replaces` către dublură, ca la o fuziune făcută din Zotero.
        import pdf_text_cache  # noqa: PLC0415
        pk, dk = chei.get(op["pastrat"], op["pastrat"]), chei.get(op["dublura"], op["dublura"])
        pid, did = zot.item_id_for_key(cur, pk), zot.item_id_for_key(cur, dk)
        if pid is None or did is None or pid == did:
            raise LookupError("itemul păstrat %r sau dublura %r nu există (sau sunt același)" % (pk, dk))
        viu = "itemID NOT IN (SELECT itemID FROM deletedItems)"
        note = [(n, t) for n, t in cur.execute("SELECT itemID, note FROM itemNotes WHERE parentItemID=? AND " + viu,
                                               (did,))]
        mutate_note = [n for n, t in note if pdf_text_cache.MARKER not in (t or "")]
        for n in mutate_note:
            cur.execute("UPDATE itemNotes SET parentItemID=? WHERE itemID=?", (pid, n))
            zot.touch(cur, n)
        mutate_atas = []
        for (aid,) in cur.execute("SELECT itemID FROM itemAttachments WHERE parentItemID=? AND " + viu,
                                  (did,)).fetchall():
            adn = cur.execute("SELECT COUNT(*) FROM itemAnnotations WHERE parentItemID=? AND " + viu, (aid,)).fetchone()[0]
            nn = [t for (t,) in cur.execute("SELECT note FROM itemNotes WHERE parentItemID=? AND " + viu, (aid,))]
            if adn or any(pdf_text_cache.MARKER not in (t or "") for t in nn):
                cur.execute("UPDATE itemAttachments SET parentItemID=? WHERE itemID=?", (pid, aid))
                zot.touch(cur, aid)
                mutate_atas.append(zot.key_for_item_id(cur, aid))
        colectii = [c for (c,) in cur.execute("SELECT collectionID FROM collectionItems WHERE itemID=?", (did,))]
        for c in colectii:
            cur.execute("INSERT OR IGNORE INTO collectionItems (collectionID, itemID, orderIndex) VALUES (?,?,0)",
                        (c, pid))
        pred = zot._predicat_id(cur, "dc:replaces")
        cur.execute("INSERT OR IGNORE INTO itemRelations (itemID, predicateID, object) VALUES (?,?,?)",
                    (pid, pred, "http://zotero.org/users/%s/items/%s" % (zot.detect_user_id(), dk)))
        cur.execute("INSERT OR IGNORE INTO deletedItems (itemID, dateDeleted) VALUES (?, ?)", (did, zot.now()))
        zot.touch(cur, pid)
        zot.touch(cur, did)
        return {"pastrat": pk, "dublura_la_cos": dk, "note_mutate": len(mutate_note), "atasamente_mutate": mutate_atas,
                "colectii_adaugate": len(colectii)}
    if fel == "scoate_din_colectie":
        # Itemul iese din colecție și rămâne în bibliotecă. Cazul: capitole puse în colecțiile volumului, deși
        # colecțiile urmează citările (regula autorului, 13.10.2026: necitat → doar My Library).
        cheie = chei.get(op["item"], op["item"])
        iid = zot.item_id_for_key(cur, cheie)
        g = _colectie(op["colectie"])
        if iid is None or not g:
            raise LookupError("itemul %r sau colecția %r nu există" % (cheie, op["colectie"]))
        n = cur.execute("DELETE FROM collectionItems WHERE collectionID=? AND itemID=?", (g[1], iid)).rowcount
        zot.touch(cur, iid)
        return {"item": cheie, "colectie": op["colectie"], "scos": bool(n)}
    if fel == "adauga_in_extra":
        # Un rând adăugat la Extra în momentul scrierii, nu o valoare întreagă calculată la punerea în coadă:
        # marcajul de verificare scris ca `set_field` ar fi șters o cheie de citare pusă de altă sarcină
        # din aceeași coadă (13.10.2026).
        cheie = chei.get(op["item"], op["item"])
        iid = zot.item_id_for_key(cur, cheie)
        if iid is None:
            raise LookupError("itemul %r nu există" % cheie)
        fid = zot.field_id(cur, "extra")
        r = cur.execute("SELECT v.value FROM itemData d JOIN itemDataValues v USING(valueID) "
                        "WHERE d.itemID=? AND d.fieldID=?", (iid, fid)).fetchone()
        era = r[0] if r else ""
        prefix = op.get("daca_lipseste") or op["rand"]
        if any(l.startswith(prefix) for l in era.split("\n")):
            return {"item": cheie, "neschimbat": True}
        zot.set_field(cur, iid, fid, (era.rstrip("\n") + "\n" if era else "") + op["rand"])
        zot.touch(cur, iid)
        return {"item": cheie, "adaugat": op["rand"]}
    if fel == "sterge_camp":
        # Scoate un câmp care nu aparține itemului (paginile și volumul-gazdă ale unui capitol, rămase pe volum).
        cheie = chei.get(op["item"], op["item"])
        iid = zot.item_id_for_key(cur, cheie)
        if iid is None:
            raise LookupError("itemul %r nu există" % cheie)
        fid = zot.field_id(cur, op["camp"])
        r = cur.execute("SELECT v.value FROM itemData d JOIN itemDataValues v USING(valueID) "
                        "WHERE d.itemID=? AND d.fieldID=?", (iid, fid)).fetchone()
        cur.execute("DELETE FROM itemData WHERE itemID=? AND fieldID=?", (iid, fid))
        zot.touch(cur, iid)
        return {"item": cheie, "camp": op["camp"], "era": r[0] if r else None}
    raise ValueError("operație necunoscută: %r" % fel)


def ruleaza(doar_arata=False):
    """Rulează toate sarcinile care așteaptă, într-un singur ciclu de închidere.

    Fiecare sarcină în punctul ei de salvare: dacă una cade, se derulează înapoi numai ea."""
    lot = sarcini()
    if not lot:
        print("Nicio sarcină amânată.")
        return 0
    print("%d sarcini în așteptare:" % len(lot))
    for cale, d in lot:
        print("  %-28s %s  (%s, %s)" % (os.path.basename(cale), d.get("eticheta", "?"),
                                        d.get("cine", "?"), d.get("de_la", "?")))
        for op in d.get("operatii") or []:
            if op.get("op") == "relateaza":
                # Legătura se arată cu TITLUL țintei: o cheie singură nu lasă să se vadă o legătură
                # greșită înainte de rulare (12.10.2026, o hotărâre legată de o lege cu același număr).
                print("      relateaza %s -> %s" % (op.get("a"), _titlu(op.get("b"))))
                continue
            if op.get("op") == "set_field":
                print("      set_field %s.%s = %r  (%s)" % (op.get("item"), op.get("camp"),
                                                         str(op.get("valoare"))[:60], _titlu(op.get("item"))))
                continue
            if op.get("op") == "set_creators":
                print("      set_creators %s = %s" % (_titlu(op.get("item")), op.get("creatori") or "[] (fără creator)"))
                continue
            if op.get("op") == "fuzioneaza":
                print("      fuzioneaza: rămâne %s; la coș %s" % (_titlu(op.get("pastrat")), op.get("dublura")))
                continue
            print("      %s %s" % (op.get("op"), op.get("tip") or op.get("cale") or
                                   op.get("colectie") or ""))
    if doar_arata:
        print("\n(--sec: n-am scris nimic)")
        return 0

    rapoarte = []
    with zot.write_session("scrieri-amanate-%d" % len(lot)) as cur:
        for cale, d in lot:
            nume = os.path.basename(cale)
            if d.get("versiune") != VERSIUNE:
                rapoarte.append((cale, {"stare": "sarit", "de_ce":
                                        "versiune %r, aștept %d" % (d.get("versiune"), VERSIUNE)}))
                continue
            cur.execute("SAVEPOINT s")
            chei, produse = {}, []
            try:
                for op in d["operatii"]:
                    produse.append(_executa(cur, op, chei))
                cur.execute("RELEASE s")
                rapoarte.append((cale, {"stare": "scris", "produse": produse}))
                print("  OK   %s" % nume)
            except Exception as e:  # noqa: BLE001
                cur.execute("ROLLBACK TO s")
                cur.execute("RELEASE s")
                rapoarte.append((cale, {"stare": "cazut", "de_ce": str(e),
                                        "produse_pana_atunci": produse}))
                print("  CADE %s — %s" % (nume, e))

    for cale, raport in rapoarte:
        raport["rulat_de"] = "PID %d" % os.getpid()
        raport["la"] = zot.now()
        io.open(cale.replace(".json", ".rezultat.json"), "w", encoding="utf-8",
                newline="\n").write(json.dumps(raport, ensure_ascii=False, indent=2) + "\n")
        if raport["stare"] == "scris":
            os.remove(cale)          # sarcina scrisă dispare; raportul rămâne
    cazute = sum(1 for _, r in rapoarte if r["stare"] != "scris")
    print("\n%d scrise, %d rămase (vezi fișierele .rezultat.json)."
          % (len(rapoarte) - cazute, cazute))
    return 1 if cazute else 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--arata" in args:
        sys.exit(ruleaza(doar_arata=True))
    if "--ruleaza" in args:
        sys.exit(ruleaza(doar_arata="--sec" in args))
    print(__doc__.split("## Folosire")[1].split("## Ce se întâmplă")[0].strip())
    sys.exit(2)
