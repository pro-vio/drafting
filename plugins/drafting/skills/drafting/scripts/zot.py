# -*- coding: utf-8 -*-
"""
zot.py — token-minimal Zotero helper (portable).

Two jobs:
  1. Read Zotero offline (zotero.sqlite, read-only) — find items, PDF paths, user id.
  2. Write NATIVE annotations / edit fields directly in zotero.sqlite (Zotero CLOSED + backup).

Why direct sqlite: the local Zotero API (port 23119) is READ-ONLY for items
(POST -> "Endpoint does not support method"). The connector endpoint
(/connector/saveItems) creates NEW items live, but editing/moving existing items
and writing annotations must go through the DB. Local-only library (no sync) =
safe to write directly; bump items.version + synced=0 so a later sync is clean.

Config (env, with sane defaults):
  ZOTERO_DIR   -> default ~/Zotero  (holds zotero.sqlite + storage/)

CLI:
  python zot.py find "<query>"                  # by ANY field value or creator surname -> (key,title,attachKey,pdf)
  python zot.py find "<query>" --in "<colecție>"  # same, scoped to a collection + subcollections (name, "parent/child" path or key)
  python zot.py find --in "<colecție>"            # list a whole collection
  python zot.py userid             # print the library user id (for the web-API URLs)
  python zot.py copii              # list the zotero.sqlite.pre-*.bak copies left by write cycles
  python zot.py copii --muta       # move all but the newest to the Recycle Bin (--toate: all)
"""
import sqlite3, sys, os, json, random, datetime, urllib.request, time, shutil, hashlib, re
try:  # Windows cp1252 consoles crash on diacritics/emoji prints
    sys.stdout.reconfigure(encoding="utf-8"); sys.stderr.reconfigure(encoding="utf-8")
except Exception: pass

def _dosar_zotero():
    """Dosarul de date Zotero (baza și `storage`, adică PDF-urile): `ZOTERO_DIR`, altfel cel ales în
    setările Zotero (`extensions.zotero.dataDir` din `prefs.js`), altfel `~/Zotero`. Din setări, nu
    dintr-o cale fixă: dosarul se mută din Zotero, iar uneltele trebuie să-l urmeze singure."""
    if os.environ.get("ZOTERO_DIR"):
        return os.environ["ZOTERO_DIR"]
    import glob  # noqa: PLC0415
    for prefs in glob.glob(os.path.join(os.environ.get("APPDATA", ""), "Zotero", "Zotero", "Profiles", "*", "prefs.js")):
        try:
            t = open(prefs, encoding="utf-8").read()
        except OSError:
            continue
        m = re.search(r'user_pref\("extensions\.zotero\.dataDir", "(.+?)"\);', t)
        if m and 'user_pref("extensions.zotero.useDataDir", true)' in t:
            return m.group(1).replace("\\\\", "\\")
    return os.path.expanduser("~/Zotero")


ZDIR = _dosar_zotero()
DB = os.path.join(ZDIR, "zotero.sqlite")
STORAGE = os.path.join(ZDIR, "storage")
# Tot ce produc uneltele în afara bazei — copiile de siguranță dinaintea scrierilor, instantaneele de
# citire, coada de scrieri amânate, PDF-urile pregătite de `adu_lege` — stă pe D:, nu pe C: și nu
# lângă PDF-uri (decizia autorului, 12.10.2026: „pdf-urile stau pe E, backup și restul pe D").
# Motivul măsurat: instantaneele lăsate în Temp au umplut C: (19 GB) și au oprit o scriere.
LUCRU = os.environ.get("ZOTERO_LUCRU") or (r"D:\zotero-lucru" if os.path.isdir("D:\\") else ZDIR)
COPII = os.path.join(LUCRU, "copii")
CLAUDE_COLOR = "#a28ae5"  # purple — convention: color encodes the annotation author
KEYCHARS = "23456789ABCDEFGHIJKLMNPQRSTUVWXYZ"

_INSTANTANEU = {}


def _ro():
    """Read-only connection — safe to open while Zotero is RUNNING.

    Cu jurnalul gol, citește baza direct, `immutable=1`. Cu un `zotero.sqlite-wal` plin, nu:
    `immutable=1` ignoră jurnalul prin proiectare, deci ar citi tăcut o stare veche, iar
    `mode=ro` simplu întoarce „database is locked" cât Zotero ține baza. Atunci se copiază
    baza împreună cu jurnalul într-un dosar temporar și se citește copia, unde SQLite aplică
    jurnalul. Copia se refolosește cât jurnalul nu se schimbă. Măsurat pe 8 octombrie 2026: cu un
    jurnal de 30 MB rămas după o închidere murdară, auditul a raportat un arbore de zece colecții
    cu zero iteme, fără nicio avertizare."""
    wal = DB + "-wal"
    try:
        marime = os.path.getsize(wal)
    except OSError:
        marime = 0
    if not marime:
        return sqlite3.connect(f"file:{DB}?mode=ro&immutable=1", uri=True)
    semn = (marime, os.path.getmtime(wal), os.path.getmtime(DB))
    if _INSTANTANEU.get("semn") != semn:
        # Un singur dosar de instantanee, sub LUCRU, cu câte un subdosar pe starea jurnalului: procesele
        # care citesc aceeași stare refolosesc aceeași copie, iar stările vechi se șterg. Înainte, fiecare
        # proces făcea un `mkdtemp` nou în Temp și nu-l ștergea: 239 de copii, 19 GB, discul C: plin
        # și o scriere căzută (12.10.2026).
        radacina = os.path.join(LUCRU, "instantaneu")
        d = os.path.join(radacina, hashlib.md5(repr(semn).encode()).hexdigest()[:12])
        gata = os.path.join(d, "gata")
        if not os.path.exists(gata):
            os.makedirs(d, exist_ok=True)
            shutil.copy2(DB, os.path.join(d, "zotero.sqlite"))
            shutil.copy2(wal, os.path.join(d, "zotero.sqlite-wal"))
            open(gata, "w").close()
            print(f"[zot] jurnalul -wal are {marime} octeți: citesc o copie a bazei cu jurnalul aplicat",
                  file=sys.stderr)
        for vechi in os.listdir(radacina):
            # Copia altei stări se șterge; una deschisă de alt proces rămâne până data viitoare.
            if vechi != os.path.basename(d):
                shutil.rmtree(os.path.join(radacina, vechi), ignore_errors=True)
        _INSTANTANEU.update(semn=semn, dir=d)
    return sqlite3.connect(os.path.join(_INSTANTANEU["dir"], "zotero.sqlite"))
def newkey():
    return ''.join(random.choice(KEYCHARS) for _ in range(8))
def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def zotero_running():
    """True if Zotero is up and serving on 23119.

    An HTTP *error* status still means the server answered, so Zotero IS running:
    the root path "/" has no handler and replies 404, which urllib raises as
    HTTPError. Catching that as "not running" made this return False for a live
    Zotero, so wait_until_quiescent() waved DB writes through against an open
    library (only SQLite's own lock then stopped them). Connection refused /
    timeout — nothing listening — is the sole "not running" signal."""
    try:
        urllib.request.urlopen("http://127.0.0.1:23119/", timeout=3)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False

def detect_user_id():
    """Best-effort: the library user id used in web-API URLs. Reads zotero.sqlite settings.
    Returns str or None (fall back to ZOTERO_USER_ID env / explicit arg)."""
    env = os.environ.get("ZOTERO_USER_ID")
    if env: return env
    try:
        c = _ro().cursor()
        for sql in (
            "SELECT value FROM settings WHERE setting='account' AND key='userID'",
            "SELECT userID FROM users LIMIT 1",
        ):
            try:
                r = c.execute(sql).fetchone()
                if r and r[0]: return str(r[0]).strip('"')
            except sqlite3.Error:
                continue
    except Exception:
        pass
    return None

# A trashed item stays in `items`/`itemAttachments` with all its rows intact — Zotero
# soft-deletes via this junction table, not by removing the row. Any read query that
# means "active library content" must exclude it explicitly, or trashed parents AND
# trashed attachments (independently — an attachment can be trashed while its parent
# is not, e.g. a scan superseded by a text-native duplicate) silently reappear as if live.
NOT_TRASHED = "itemID NOT IN (SELECT itemID FROM deletedItems)"

def collection_ids(name, recursive=True):
    """The collectionID of collection `name`, plus its descendants when recursive. Read-only.
    [] if nothing matches.

    `name` se rezolva cu aceeasi regula ca in `find_collection`: nume, cale („parinte/copil")
    sau cheie. **Un nume gol care se potriveste la mai multe colectii ridica LookupError**, cu
    lista potrivirilor, in loc sa le intoarca pe toate.

    De ce: functia intorcea deliberat toate colectiile cu acelasi nume, iar `find_collection`
    spunea, in aceeasi biblioteca, ca numele singur nu identifica o colectie. Doua reguli pentru
    „colectia cu numele X". Cazul care a hotarat-o pe a doua: doua colectii „metodologie", iar
    sase acte normative au ajuns in colectia altui proiect (26 august 2026). Dupa prima regula,
    `find --in "metodologie"` amesteca tacut itemii a doua proiecte."""
    gasita = find_collection(name, verifica_api=False)
    if not gasita:
        return []
    c = _ro()
    ids = [gasita[1]]
    frontier = list(ids)
    while recursive and frontier:
        qs = ",".join("?" * len(frontier))
        kids = [r[0] for r in c.execute(
            f"SELECT collectionID FROM collections WHERE parentCollectionID IN ({qs})",
            frontier).fetchall() if r[0] not in ids]
        ids.extend(kids); frontier = kids
    return ids

def find(query, collection=None, recursive=True):
    """Find items by ANY field value or creator surname. -> [(itemKey, title, attachKey, pdfPath)].

    **Caută în toate câmpurile**, nu doar în titlu: titlu, denumire de act, editură, an, DOI,
    `extra`, orice. Prima linie a descrierii ăsteia spunea „title/nameOfAct/creator", iar o
    sesiune a citit-o și a tras concluzia că nu se poate căuta după conținutul unui câmp. Linia
    de sus se citește, restul nu — deci ea trebuie să fie adevărul, nu un rezumat.
    Excludes trashed items/attachments (see NOT_TRASHED).

    `collection` (name, "parent/child" path, or key) restricts the search to that collection
    and, unless recursive=False, its subcollections. Membership is read on the TOP-LEVEL item,
    which is how Zotero files things — an attachment belongs wherever its parent does.
    An empty `query` plus a collection lists the whole collection.

    Raises LookupError when the collection matches nothing: an empty result would
    otherwise read as "the query found nothing", which is a different fact. Raises it too when
    a bare name matches several collections — see `collection_ids`.

    Notes, attachments and annotations are not returned as items; the types are resolved by
    name (`itemTypes.typeName`), never by number.

    This searches METADATA (any field value + creator surnames), not the text of the PDFs.
    For text, populate the cache over the collection (`prefetch_collection.py`) and search
    that — see references/pdf-text-cache.md."""
    c = _ro().cursor()
    where, params = [], []
    if query:
        where.append("""(EXISTS(SELECT 1 FROM itemData d JOIN itemDataValues v ON v.valueID=d.valueID
                                WHERE d.itemID=i.itemID AND v.value LIKE ?)
                         OR EXISTS(SELECT 1 FROM itemCreators ic JOIN creators cr ON cr.creatorID=ic.creatorID
                                   WHERE ic.itemID=i.itemID AND cr.lastName LIKE ?))""")
        params += [f"%{query}%", f"%{query}%"]
    if collection is not None:
        cids = collection_ids(collection, recursive)
        if not cids:
            raise LookupError(f"nicio colecție cu numele {collection!r}")
        qs = ",".join("?" * len(cids))
        where.append(f"i.itemID IN (SELECT itemID FROM collectionItems WHERE collectionID IN ({qs}))")
        params += cids
    clause = ("AND " + " AND ".join(where)) if where else ""
    rows = c.execute(f"""
      SELECT DISTINCT i.itemID, i.key,
        (SELECT idv.value FROM itemData d JOIN itemDataValues idv ON idv.valueID=d.valueID
           JOIN fields f ON f.fieldID=d.fieldID WHERE d.itemID=i.itemID
           AND f.fieldName IN ('title','nameOfAct') LIMIT 1) t
      FROM items i WHERE i.itemTypeID NOT IN (SELECT itemTypeID FROM itemTypes
                                              WHERE typeName IN ('note','attachment','annotation'))
        AND i.{NOT_TRASHED}
        {clause}
    """, params).fetchall()
    out = []
    for iid, key, title in rows:
        att = c.execute(f"""SELECT ai.key, ia.path FROM itemAttachments ia
                           JOIN items ai ON ai.itemID=ia.itemID
                           WHERE ia.parentItemID=? AND ia.contentType='application/pdf'
                             AND ai.{NOT_TRASHED} LIMIT 1""",
                        (iid,)).fetchone()
        ak = att[0] if att else None
        # attachment_path resolves stored copies AND linked files, and returns None when the
        # row has no path at all — a phantom attachment (see zotero-schema.md). Building the
        # path inline here assumed 'storage:<file>' and crashed on both of the other two cases.
        path = attachment_path(ak) if ak else None
        out.append((key, title, ak, path))
    return out

def cauta_note(query, collection=None, recursive=True):
    """Cauta in NOTE, ADNOTARI si ETICHETE — locurile pe care `find` nu le atinge.

    -> [(fel, itemKey, titlul itemului-gazda, bucata de text)], unde `fel` e „nota",
    „evidentiere", „comentariu" sau „eticheta".

    **De ce e o functie separata si nu un parametru al lui `find`:** ele nu sunt campuri ale
    itemului, ci obiecte proprii, cu alta gazda. O evidentiere sta pe un atasament, care sta pe
    un item; o nota poate sta oriunde, inclusiv desprinsa. `find` intoarce iteme; asta intoarce
    locuri.

    Ce acopera, masurat pe biblioteca la 12 septembrie 2026: 1.028 de note si 3.255 de etichete,
    niciuna cautabila pana acum. Plus adnotarile — evidentierile si comentariile scrise de om in
    PDF-uri —, care sunt chiar partea cea mai scumpa de refacut daca se pierde."""
    ro = _ro()
    like = f"%{query}%"
    chei_col = None
    if collection is not None:
        cids = collection_ids(collection, recursive)
        if not cids:
            raise LookupError(f"nicio colecție cu numele {collection!r}")
        qs = ",".join("?" * len(cids))
        chei_col = {r[0] for r in ro.execute(
            f"SELECT itemID FROM collectionItems WHERE collectionID IN ({qs})", cids)}

    def titlu(item_id):
        r = ro.execute(
            "SELECT v.value FROM itemData d JOIN itemDataValues v ON v.valueID=d.valueID "
            "JOIN fields f ON f.fieldID=d.fieldID WHERE d.itemID=? "
            "AND f.fieldName IN ('title','nameOfAct') LIMIT 1", (item_id,)).fetchone()
        return r[0] if r else "(fără titlu)"

    def cheia(item_id):
        r = ro.execute("SELECT key FROM items WHERE itemID=?", (item_id,)).fetchone()
        return r[0] if r else "?"

    out = []
    # notele: se sare peste notele de cache, altfel orice cautare le-ar intoarce pe toate
    for note_id, parent_id, nota in ro.execute(
            "SELECT itemID, parentItemID, note FROM itemNotes WHERE note LIKE ?", (like,)):
        if "pdf-text-cache" in (nota or "")[:120]:
            continue
        gazda = parent_id or note_id
        if chei_col is not None and gazda not in chei_col:
            continue
        curat = re.sub(r"<[^>]+>", " ", nota or "")
        i = curat.lower().find(query.lower())
        out.append(("nota", cheia(gazda), titlu(gazda),
                    " ".join(curat[max(0, i - 120):i + 200].split())))

    # adnotarile: evidentierea are `text`, comentariul are `comment`
    for camp, fel in (("text", "evidentiere"), ("comment", "comentariu")):
        for att_id, bucata in ro.execute(
                f"SELECT parentItemID, {camp} FROM itemAnnotations WHERE {camp} LIKE ?", (like,)):
            gazda = ro.execute("SELECT parentItemID FROM itemAttachments WHERE itemID=?",
                               (att_id,)).fetchone()
            gazda = (gazda[0] if gazda and gazda[0] else att_id)
            if chei_col is not None and gazda not in chei_col:
                continue
            out.append((fel, cheia(gazda), titlu(gazda), " ".join((bucata or "").split())[:300]))

    for item_id, nume in ro.execute(
            "SELECT it.itemID, t.name FROM itemTags it JOIN tags t ON t.tagID=it.tagID "
            "WHERE t.name LIKE ?", (like,)):
        if chei_col is not None and item_id not in chei_col:
            continue
        out.append(("eticheta", cheia(item_id), titlu(item_id), nume))
    return out


# ---- direct-write helpers (Zotero CLOSED) ----
def open_rw():
    """Open zotero.sqlite for writing. Refuses if Zotero is running."""
    if zotero_running():
        raise SystemExit("ABORT: Zotero pare DESCHIS (23119 răspunde). Închide-l întâi + fă backup.")
    return sqlite3.connect(DB)

def get_value(cur, value):
    """itemDataValues.value is UNIQUE — return existing valueID or insert a new one."""
    r = cur.execute("SELECT valueID FROM itemDataValues WHERE value=?", (value,)).fetchone()
    if r: return r[0]
    nv = cur.execute("SELECT COALESCE(MAX(valueID),0) FROM itemDataValues").fetchone()[0] + 1
    cur.execute("INSERT INTO itemDataValues (valueID,value) VALUES (?,?)", (nv, value))
    return nv

def set_field(cur, item_id, field_id, value):
    """Upsert one itemData field. Returns 'UPDATE' or 'INSERT'."""
    vid = get_value(cur, value)
    n = cur.execute("UPDATE itemData SET valueID=? WHERE itemID=? AND fieldID=?",
                    (vid, item_id, field_id)).rowcount
    if n == 0:
        cur.execute("INSERT INTO itemData (itemID,fieldID,valueID) VALUES (?,?,?)",
                    (item_id, field_id, vid))
    return "UPDATE" if n else "INSERT"

def touch(cur, item_id):
    """Mark an item dirty so the next sync picks it up cleanly."""
    t = now()
    cur.execute("UPDATE items SET version=version+1, synced=0, dateModified=?, clientDateModified=? WHERE itemID=?",
                (t, t, item_id))

def field_id(cur, field_name):
    r = cur.execute("SELECT fieldID FROM fields WHERE fieldName=?", (field_name,)).fetchone()
    return r[0] if r else None

# ---- batched write cycle (do ALL writes for a task in ONE close/reopen) ----
# Every graceful close + restart of Zotero costs ~2-4 min (backup, poll-until-clean,
# API-ping budget). So: resolve every collection key you need with find_collection()
# while Zotero is still OPEN, then do a SINGLE write_session covering all items,
# attachments and collection filings. Do NOT close→write→reopen→read→close→write again.
LIBRARY_ID = 1  # personal library

def _jurnale():
    """Fisierele de jurnal ale bazei aflate acum pe disc (`-journal`, `-wal`). [] daca niciunul.

    Un `-wal` de 0 octeti nu se socoteste: n-are niciun cadru, deci nicio tranzactie necopiata in
    baza, iar fisierul principal e complet. Zotero 10 trece baza in `journal_mode=wal` si lasa un
    asemenea `-wal` gol la FIECARE inchidere gratioasa; numarat ca jurnal deschis, bloca orice
    scriere, iar reluarea ceruta de refuz il recrea (rateurile din 2 si 7 octombrie 2026).
    `-journal` ramane refuz la orice marime: e jurnal de derulare inapoi."""
    gasite = []
    for s in ("-wal", "-journal"):
        p = DB + s
        try:
            marime = os.path.getsize(p)
        except OSError:
            continue  # nu exista
        if s == "-wal" and marime == 0:
            continue
        gasite.append(p)
    return gasite


def _zotero_proces():
    """Exista un proces `zotero.exe`? True / False, sau None cand nu se poate afla.

    Portul spune doar daca Zotero SERVESTE. Un Zotero care porneste inca n-a deschis portul, iar
    unul care se inchide l-a inchis deja, dar in ambele cazuri procesul tine baza. De aceea
    „e inchis" se decide pe proces, cu portul doar ca semnal in plus.

    None pe orice sistem fara `tasklist` (modulul e portabil) sau cand comanda cade: acolo nu se
    poate sti, iar cine cheama se intoarce la ce spune portul."""
    try:
        import subprocess  # noqa: PLC0415
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq zotero.exe", "/NH", "/FO", "CSV"],
                           capture_output=True, text=True, timeout=10)
    except Exception:  # noqa: BLE001
        return None
    if r.returncode != 0 or not (r.stdout or "").strip():
        return None
    return '"zotero.exe"' in r.stdout.lower()


def wait_until_quiescent(tries=20, delay=1.0):
    """Block until it is safe to write: Zotero's API is silent, no `zotero.exe` process is left,
    AND no -journal and no NON-EMPTY -wal lingers next to the DB (see `_jurnale`). At the end of the budget it REFUSES
    (SystemExit) if any of the three still holds — it never lets a write start over a journal.

    De ce fiecare dintre cele trei:
    - portul: Zotero deschis de om;
    - procesul: un Zotero care porneste sau se inchide nu raspunde pe port, dar tine baza.
      Protocolul din runtime-optimization.md cere asteptarea pana dispar TOATE procesele; aici
      se verifica doar portul, deci un ciclu care il prindea pe Zotero pornind scria sub el;
    - jurnalul: un proces rezidual care scrie dupa noi ne anuleaza scrierea (cursa de la
      inchidere, 3 iulie 2026). In plus, un `-journal` ramas e jurnal „fierbinte": prima
      deschidere in scriere il deruleaza inapoi, iar o copie de siguranta facuta inainte e o
      baza scrisa pe jumatate.

    Capatul bugetului intorcea True cu jurnalul inca pe disc, cu motivul „backup already taken".
    Dar `write_session` face copia DUPA functia asta, deci scrierea pornea, si copia se facea
    exact in situatia de mai sus (auditul scripturilor, 13 septembrie 2026). Refuzul costa o
    reluare; scrierea peste jurnal poate costa baza."""
    for _ in range(tries):
        if not zotero_running() and _zotero_proces() is not True and not _jurnale():
            return True
        time.sleep(delay)
    if zotero_running():
        raise SystemExit("ABORT: Zotero încă răspunde pe 23119 — închide-l (taskkill /IM zotero.exe, fără /F) și reia.")
    if _zotero_proces() is True:
        raise SystemExit("ABORT: procesul zotero.exe încă există, deși portul 23119 tace — Zotero "
                         "pornește sau se închide. Așteaptă să dispară (tasklist) și reia; fără /F.")
    raise SystemExit(
        "ABORT: jurnalul bazei e încă pe disc (%s), deși Zotero e închis. Nu scriu și nu fac "
        "copie peste el. NU-l șterge de mână: e jurnalul unei tranzacții neterminate. Pornește "
        "Zotero, lasă-l să termine, închide-l grațios și reia."
        % ", ".join(os.path.basename(j) for j in _jurnale()))

def wait_until_readable(tries=30, delay=2.0):
    """Block until the DB can actually be READ. Perechea lui `wait_until_quiescent`, care
    așteaptă ca baza să devină SCRIIBILĂ; asta așteaptă să devină citibilă.

    Simptomul pentru care există, aflat scump: după mai multe cicluri de scriere într-o zi
    — peste 2000 de itemi cu `synced=0` — Zotero intră într-o tranzacție lungă de
    recuperare. Semnătura ei e derutantă: `/connector/ping` răspunde **200**, deci pare viu,
    dar `/api/users/<id>/items` atârnă, iar orice citire read-only primește
    „database is locked". Fișierul `zotero.sqlite-journal` stă pe disc, staționar.

    Ce NU se face atunci: repornit (nu ajută) și mai ales **omorât forțat** — un `taskkill /F`
    peste o tranzacție în curs e felul cel mai sigur de a strica baza. Ce ajută: oprire
    grațioasă și așteptarea până dispare jurnalul.

    Întoarce True când baza răspunde la o citire, False dacă bugetul se termină."""
    for _ in range(tries):
        try:
            _ro().execute("SELECT 1 FROM items LIMIT 1").fetchone()
            return True
        except Exception:
            time.sleep(delay)
    return False


def asteapta_api(consecutive=3, tries=60, delay=5, timeout=10):
    """Asteapta pana API-ul local raspunde la o interogare de DATE de `consecutive` ori la rand.

    **Un raspuns bun nu inseamna ca baza e gata.** Masurat, de doua ori intr-o zi: o singura
    confirmare, apoi o generare de zece minute — iar baza a cazut inapoi in tranzactia lunga
    inainte ca pasul de citari sa ajunga acolo. Raport fara aparat, cod de iesire zero, de
    aruncat. De aceea se cer mai multe raspunsuri bune la rand, la cateva secunde distanta: unul
    singur poate prinde o fereastra intre doua faze ale recuperarii.

    Si se interogheaza **date**, nu `ping`. Simptomul care induce in eroare: dupa multe scrieri
    intr-o zi, Zotero raspunde la ping, dar orice interogare de date intoarce `HTTP 500`, iar
    `zotero.sqlite-journal` sta pe disc. Un ping bun nu spune nimic despre baza.

    True daca s-au strans `consecutive` reusite la rand, False daca s-a terminat bugetul."""
    uid = detect_user_id()
    if not uid:
        return False
    url = "http://127.0.0.1:23119/api/users/%s/items?limit=1&format=json" % uid
    la_rand = 0
    for _ in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                r.read(64)
            la_rand += 1
            if la_rand >= consecutive:
                return True
        except Exception:  # noqa: BLE001
            la_rand = 0     # o cadere sterge seria: contau raspunsurile LA RAND
        time.sleep(delay)
    return False


_PORNIT_DE_AICI = False   # procesul Zotero care ruleaza acum a fost pornit de noi


def porneste_zotero(astepta=True, consecutive=3):
    """Porneste Zotero daca nu ruleaza, si asteapta pana raspunde stabil. True daca e gata.

    Daca ruleaza deja, nu porneste nimic — doar verifica."""
    global _PORNIT_DE_AICI
    if not zotero_running():
        import subprocess  # noqa: PLC0415
        for cale in (r"C:\Program Files\Zotero\zotero.exe",
                     r"C:\Program Files (x86)\Zotero\zotero.exe"):
            if os.path.exists(cale):
                subprocess.Popen([cale], close_fds=True)
                _PORNIT_DE_AICI = True
                break
        else:
            print("  nu gasesc zotero.exe; porneste-l de mana", file=sys.stderr)
            return False
    return asteapta_api(consecutive=consecutive) if astepta else True


def _forma_add_item(it):
    """Un item in forma ceruta de `add_item`, din oricare dintre cele doua forme care circula.

    Se accepta si forma conectorului — `itemType`, `title`, `creators` —, fiindca aia o au in mana
    cei care veneau de la `save_items`. Traducerea e ce face inlocuirea ieftina: cine muta un apel
    nu-si rescrie si datele."""
    if "tip" in it or "campuri" in it:
        return (it.get("tip") or it.get("itemType"), dict(it.get("campuri") or {}),
                [tuple(c) for c in (it.get("creatori") or [])])
    campuri, creatori = {}, []
    for k, v in it.items():
        if k in ("itemType", "creators", "tags", "collections", "relations", "notes", "id"):
            continue
        if isinstance(v, (str, int, float)) and v != "":
            campuri[k] = str(v)
    for c in it.get("creators") or []:
        fel = c.get("creatorType", "author")
        if c.get("name"):
            creatori.append((fel, c["name"]))
        else:
            creatori.append((fel, c.get("lastName", ""), c.get("firstName", "")))
    return it.get("itemType"), campuri, creatori


def adauga_itemi(items, colectie, asteapta=0, tag=None):
    """Creeaza itemi NOI intr-o colectie anume, facand singura tot ciclul. Intoarce lista de chei.

    **De ce exista, desi `save_items` face aproape acelasi lucru:** conectorul aseaza itemii in
    colectia selectata in interfata, nu in cea ceruta. `save_items` avea deja un avertisment in
    documentatie si un parametru-plasa care refuza cand i se cere o colectie. Plasa prinde insa
    doar pe cine STIE sa dea parametrul; cine cheama simplu `save_items(items)` — apelul natural —
    nu intalneste nici avertismentul, nici plasa. Un avertisment care trebuie citit ca sa te
    fereasca e o regula tinuta de om, iar regulile tinute de om cad: masurat de doua ori in
    aceeasi zi, si o data cu noroc bun, deci fara urma.

    Aici calea corecta costa un rand, ca si cea gresita:

        chei = zot.adauga_itemi(items, colectie="teoria-jocurilor")

    Ce face, in ordine: rezolva colectia **cat Zotero e inca deschis** (read-only, deci nu
    deranjeaza pe nimeni); inchide aplicatia gratios; scrie toti itemii intr-un singur ciclu, cu
    `collection_id` explicit; o lasa **deschisa** la final, cum cere protocolul.

    Colectia se da ca nume, cale („parinte/copil") sau cheie. **Daca nu exista, ridica eroare** —
    n-o creeaza tacut: o colectie nascuta dintr-o greseala de scriere e mai greu de gasit decat
    una care lipseste, fiindca pare munca facuta.

    **Nu inchide Zotero sub cineva care a anuntat ca lucreaza in el.** Fara `asteapta`, ridica
    `RuntimeError` si spune cine il tine; cu `asteapta=<secunde>`, asteapta bugetul ala. Cand nu
    merita asteptat, treaba se lasa in coada de scrieri amanate — vezi `scrieri_amanate.lasa`."""
    gasita = find_collection(colectie)
    if not gasita:
        raise LookupError("colectia %r nu exista. Creeaz-o intai, sau da-i calea ori cheia."
                          % (colectie,))
    cid = gasita[1]

    forme = []
    for it in items:
        tip, campuri, creatori = _forma_add_item(it)
        if not tip:
            raise ValueError("item fara tip: %r" % (it,))
        forme.append((tip, campuri, creatori))

    if zotero_running():
        d = cine_lucreaza()
        if d and not d.get("mort"):
            if not asteapta:
                raise RuntimeError(
                    "Zotero e tinut deschis de %r (PID %s, de la %s). Da `asteapta=<secunde>` "
                    "sau lasa treaba in coada de scrieri amanate."
                    % (d.get("tag"), d.get("pid"), d.get("de_la")))
            asteapta_inchiderea(timeout=asteapta)
        else:
            import subprocess  # noqa: PLC0415
            print("  inchid Zotero (gratios, fara /F) ca sa pot scrie...", file=sys.stderr)
            subprocess.run(["taskkill", "/IM", "zotero.exe"], capture_output=True)
            for _ in range(60):
                if not zotero_running():
                    break
                time.sleep(2)

    chei = []
    with write_session(tag or ("adauga-%d-iteme" % len(forme))) as cur:
        for tip, campuri, creatori in forme:
            iid = add_item(cur, tip, campuri, creatori or None, collection_id=cid)
            chei.append(key_for_item_id(cur, iid))
    return chei


def save_items(items, timeout=30, colectie=None):
    """Creează itemi prin conector, cu Zotero DESCHIS — fără niciun ciclu de închidere.

    **Pentru itemi noi într-o colecție anume, `adauga_itemi(items, colectie=...)`.** Ea face
    singură tot ciclul și pune colecția explicit. Asta rămâne pentru singurul caz în care e
    potrivită: item fără colecție-țintă, cu Zotero deschis.

    **Nu-l folosi când contează în ce colecție ajung itemii.** Conectorul îi așază în
    **colecția selectată în interfață**, iar selecția e una singură pentru tot Zotero: două
    sesiuni care lucrează în paralel își încurcă subcolecțiile fără să greșească nimeni și
    fără ca ceva să semnaleze. Agentul A cere colecția X, altcineva schimbă selecția pe Y,
    itemii lui A ajung în Y.

    Calea deterministă, când colecția contează:

        cheie, cid = zot.find_collection("proiect-exemplu")  # cu Zotero inca deschis
        with zot.write_session("iteme noi") as cur:          # ia randul singur
            zot.add_item(cur, "journalArticle", campuri, collection_id=cid)

    Costă un ciclu de închidere, dar itemii ajung unde ai cerut, indiferent ce face altcineva.

    `items` = listă de dicționare în forma conectorului (`itemType`, `title`, `creators`, ...).
    Întoarce răspunsul parsat.

    Antetul `X-Zotero-Connector-API-Version: 3` e **obligatoriu**: fără el cererea cade cu
    `HTTP 400 CONNECTOR_VERSION_OUTDATED`, iar mesajul nu spune ce versiune vrea. Asta a
    costat o încercare eșuată înainte să fie scris aici — de-aia ajutorul există, ca să nu se
    reconstruiască cererea de mână de fiecare dată.

    `colectie` e o plasă, nu un parametru de lucru: dacă îl dai, funcția refuză și te trimite
    pe calea de mai sus — mai bine o eroare la scris decât itemi rătăciți descoperiți peste o
    săptămână."""
    if colectie is not None:
        raise ValueError(
            "conectorul nu poate ținti o colecție: așază itemii în cea selectată în interfață. "
            "Pentru o colecție anume, folosește write_session + add_item(collection_id=...).")
    import urllib.request
    date = json.dumps({"items": items}).encode("utf-8")
    cerere = urllib.request.Request(
        "http://127.0.0.1:23119/connector/saveItems", data=date,
        headers={"Content-Type": "application/json",
                 "X-Zotero-Connector-API-Version": "3",
                 "User-Agent": "drafting-skill"})
    with urllib.request.urlopen(cerere, timeout=timeout) as r:
        corp = r.read().decode("utf-8", "replace")
    return json.loads(corp) if corp.strip() else {"status": "ok"}


def backup(tag):
    """Checkpoint zotero.sqlite -> zotero.sqlite.pre-<tag>.bak. Returns the path.

    Refuza (SystemExit) cand langa baza sta un `-journal` sau un `-wal` cu continut: fisierul principal e atunci
    pe jumatate scris, iar copia lui singur nu e o stare la care te poti intoarce. Refuzul sta
    aici, nu doar in `wait_until_quiescent`, fiindca `backup` se cheama si direct, pentru
    editarile de mana."""
    jurn = _jurnale()
    if jurn:
        raise SystemExit("ABORT: nu fac copie de siguranță peste o bază cu jurnalul deschis (%s). "
                         "Pornește Zotero, închide-l grațios și reia."
                         % ", ".join(os.path.basename(j) for j in jurn))
    os.makedirs(COPII, exist_ok=True)
    dst = os.path.join(COPII, "zotero.sqlite.pre-%s.bak" % tag)
    shutil.copy2(DB, dst)
    return dst


def copii_de_siguranta():
    """[(cale, octeti, mtime)] pentru copiile `zotero.sqlite.pre-*.bak` facute de `backup`, cea
    mai noua prima. Copiile automate ale lui Zotero (`zotero.sqlite.bak`, `zotero.sqlite.<n>.bak`)
    nu intra: pe acelea le tine si le roteste Zotero singur.

    Exista fiindca fiecare ciclu de scriere lasa o copie intreaga a bazei, aproape 200 MB, iar
    nimic nu spunea cand nu mai e nevoie de ea. Pe 7 octombrie 2026 se strasesera opt, 1,4 GB.
    O copie serveste pana cand scrierea e verificata, adica pana cand Zotero a pornit pe baza
    noua si a aratat ce trebuia; dupa aceea e doar o stare veche a bazei."""
    import glob  # noqa: PLC0415
    # Și cele rămase lângă bază de dinainte de mutarea copiilor pe D: (12.10.2026).
    cai = glob.glob(os.path.join(COPII, "zotero.sqlite.pre-*.bak")) + glob.glob(DB + ".pre-*.bak")
    out = [(p, os.path.getsize(p), os.path.getmtime(p)) for p in cai]
    return sorted(out, key=lambda x: x[2], reverse=True)


def muta_in_cos(cai):
    """Muta fisierele in Cosul de reciclare al Windows, de unde se pot recupera. NU sterge
    definitiv: stergerea definitiva e a omului, cand goleste cosul. Pe alt sistem refuza."""
    if os.name != "nt":
        raise SystemExit("Mutarea in cos e scrisa doar pentru Windows; muta fisierele de mana:\n  "
                         + "\n  ".join(cai))
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
    FO_DELETE, FOF_ALLOWUNDO, FOF_NOCONFIRMATION, FOF_SILENT, FOF_NOERRORUI = 3, 0x40, 0x10, 0x4, 0x400
    for cale in cai:
        op = SHFILEOPSTRUCTW(None, FO_DELETE, os.path.abspath(cale) + "\0", None,
                             FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI,
                             False, None, None)
        rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        if rc or op.fAnyOperationsAborted or os.path.exists(cale):
            raise OSError("Nu s-a putut muta in cos: %s (cod %s)" % (cale, rc))


def _spune_copiile():
    """O linie, la finalul fiecarui ciclu: cate copii stau langa baza si cat ocupa."""
    c = copii_de_siguranta()
    if len(c) > 1:
        print("  copii de siguranta ale bazei: %d, %.1f GB. Dupa ce ai verificat scrierea in "
              "Zotero, cele vechi se muta in cos cu `python zot.py copii --muta`."
              % (len(c), sum(x[1] for x in c) / 1e9))

LACAT = os.path.join(ZDIR, ".zotero-scriere.lock")
LACAT_INVECHIT = 900        # 15 minute: dupa atat se sparge un lacat al carui proces NU se poate verifica
LACAT_MORT = 30             # 30 de secunde: dupa atat se sparge un lacat al carui proces sigur nu mai exista
# Un lacat al carui proces se vede VIU nu se sparge niciodata: o scriere lunga arata identic cu
# una moarta doar pentru cine nu poate intreba lista de procese.


def _stare_proces(pid):
    """„viu", „mort", sau None cand nu se poate verifica.

    Trei stari, nu doua, fiindca lacatul le trateaza diferit (vezi `coada`). Cand verificarea
    avea doar True/False, „nu stiu" se intorcea ca „viu", deci pragul lung pentru cazul
    neverificabil nu se folosea niciodata, iar un lacat ramas pe o masina fara `tasklist` nu se
    mai spargea deloc."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None      # lacat fara PID (fisier abia creat sau stricat): nu e dovada de moarte
    try:
        import subprocess  # noqa: PLC0415
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                           capture_output=True, text=True, timeout=10)
    except Exception:  # noqa: BLE001
        return None
    if r.returncode != 0 or not (r.stdout or "").strip():
        return None
    # Masurat pe masina asta, 13 septembrie 2026: PID-ul viu da un rand CSV cu PID-ul intre
    # ghilimele; PID-ul inexistent da doar randul „INFO: No tasks are running...", cod 0.
    return "viu" if f'"{pid}"' in r.stdout else "mort"


def _proces_viu(pid):
    """Procesul mai exista? True si cand nu se poate verifica, ca sa nu se declare mort un
    proces despre care nu stim nimic. Pentru a deosebi cele doua cazuri, `_stare_proces`."""
    return _stare_proces(pid) != "mort"


ANUNT = os.path.join(ZDIR, ".zotero-in-lucru.json")


def cine_lucreaza():
    """Cine tine Zotero deschis, daca a anuntat: `{pid, tag, de_la}`. Altfel None.

    `zotero_running()` raspunde „e pornita" — si atat. Cand pe masina lucreaza sapte sesiuni,
    aia nu e o informatie: cel care asteapta nu poate afla nici cine o tine, nici cat mai
    dureaza, deci fie intreaba oamenii, fie abandoneaza sarcina. Anuntul transforma „e pornita"
    in „e a sesiunii X, de acum zece minute, pentru cutare".

    Nu e o garantie: un om care deschide Zotero din interfata nu anunta nimic, iar functia
    intoarce None. Acopera insa cazul obisnuit — sesiunile care lucreaza in biblioteca —, si
    acolo e ieftin."""
    if not os.path.exists(ANUNT):
        return None
    try:
        d = json.loads(io_open(ANUNT, encoding="utf-8").read())
    except Exception:  # noqa: BLE001
        return None
    if not _proces_viu(d.get("pid", 0)):
        # Anunt ramas de la un proces mort: se spune, dar nu se crede.
        d["mort"] = True
    return d


class lucrez_in_zotero:
    """Anunta ca tii Zotero deschis, ca ceilalti sa stie pe cine asteapta.

        with zot.lucrez_in_zotero("adaug fise in colectia de proba"):
            ...        # lucrezi in interfata sau prin API

    Se cheama de sesiunea care DESCHIDE Zotero cu intentia de a lucra in el, sau care il tine
    deschis. Nu blocheaza nimic si nu inlocuieste coada de scriere: coada serializeaza scrierile
    in baza, asta doar spune cine ocupa aplicatia. Sunt doua lucruri diferite — una e o cursa
    intre scriitori, cealalta o resursa tinuta ocupata pe termen nedefinit."""

    def __init__(self, tag):
        self.tag = tag

    def __enter__(self):
        try:
            with io_open(ANUNT, "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "tag": self.tag, "de_la": now()}, f)
        except OSError:
            pass
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            d = cine_lucreaza()
            if d and d.get("pid") == os.getpid():
                os.remove(ANUNT)
        except OSError:
            pass
        return False


def asteapta_inchiderea(timeout=600, delay=15):
    """Asteapta pana cand Zotero e inchis. True daca s-a inchis, altfel ridica TimeoutError.

    Rostul: sesiunea care are de scris si gaseste aplicatia deschisa n-are azi decat doua
    iesiri — sa intrebe omul, sau sa abandoneze sarcina. Asta e a treia: asteapta un buget
    declarat, spune pe cine asteapta daca se stie, si ridica mana cu ce a aflat.

    Se cheama INAINTE de `write_session`, nu in locul ei."""
    asteptat = 0.0
    spus = False
    while zotero_running():
        if not spus:
            d = cine_lucreaza()
            if d and not d.get("mort"):
                print("  astept sa se inchida Zotero: lucreaza %r (PID %s, de la %s)"
                      % (d.get("tag"), d.get("pid"), d.get("de_la")), file=sys.stderr)
            else:
                print("  astept sa se inchida Zotero; nimeni n-a anuntat cine il tine",
                      file=sys.stderr)
            spus = True
        if asteptat >= timeout:
            d = cine_lucreaza()
            cine = (" — tine %r (PID %s, de la %s)" % (d.get("tag"), d.get("pid"), d.get("de_la"))
                    if d and not d.get("mort") else " — nimeni n-a anuntat cine il tine")
            raise TimeoutError("Zotero e tot deschis dupa %ds%s" % (int(asteptat), cine))
        time.sleep(delay)
        asteptat += delay
    return True


class coada:
    """Coada de scriere in Zotero, intre PROCESE diferite. Se asteapta la rand, nu se esueaza.

        with zot.coada("ce fac"):
            ...          # nicio alta sesiune nu scrie in acest timp

    De ce exista: doua sesiuni care lucreaza in paralel isi incurcau subcolectiile. Cauza nu
    e o cursa in codul nostru, ci conectorul — `POST /connector/saveItems` aseaza itemii in
    **colectia selectata in interfata**, iar selectia e una singura pentru tot Zotero. Agentul
    A cere colectia X, agentul B schimba selectia pe Y, si itemii lui A ajung in Y. Nimeni nu
    greseste, si nimic nu semnaleaza.

    Coada rezolva intercalarea. Dependenta de selectie o rezolva `add_item(collection_id=...)`
    intr-un `write_session` — vezi nota de la `save_items`.

    Lacatul e un fisier creat atomic (O_EXCL) langa baza. Poarta PID-ul si eticheta, ca sa se
    poata spune CINE tine randul. Se sparge singur, altfel o sesiune cazuta ar bloca toate
    celelalte pentru totdeauna, dupa starea procesului care l-a pus:
    - proces sigur mort: dupa LACAT_MORT (30 s), cat sa nu se sparga lacatul unuia care porneste;
    - proces care nu se poate verifica: dupa LACAT_INVECHIT (15 minute);
    - proces viu: niciodata; cine asteapta primeste TimeoutError dupa `timeout`."""

    def __init__(self, tag, timeout=600, delay=3.0):
        self.tag, self.timeout, self.delay = tag, timeout, delay
        self.tinut = False

    def _citeste(self):
        try:
            return json.loads(io_open(LACAT, encoding="utf-8").read())
        except Exception:  # noqa: BLE001
            return {}

    def __enter__(self):
        asteptat = 0.0
        while True:
            try:
                fd = os.open(LACAT, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"pid": os.getpid(), "tag": self.tag, "de_la": now()}, f)
                self.tinut = True
                return self
            except FileExistsError:
                d = self._citeste()
                if d.get("pid") == os.getpid():
                    # Lacatul e al PROCESULUI de fata: un ciclu de scriere a chemat ceva care
                    # deschide alt ciclu. Asteptarea nu se poate termina cu bine — cine ar
                    # trebui sa elibereze randul e chiar cel care asteapta. Masurat: doua
                    # rulari au stat cate zece minute, iar mesajul spunea ca lacatul e al
                    # altei sesiuni, cu PID cu tot; PID-ul era al lor.
                    raise RuntimeError(
                        "blocare pe sine: randul de scriere e tinut de acest proces (PID %s, "
                        "eticheta %r), iar acum se cere din nou pentru %r. Un `write_session` "
                        "a chemat ceva care deschide al doilea — rezolva colectia si "
                        "identificatorii INAINTE de ciclu, si foloseste inauntru functiile "
                        "care primesc cursorul." % (d.get("pid"), d.get("tag"), self.tag))
                vechime = 0
                try:
                    vechime = (datetime.datetime.strptime(now(), "%Y-%m-%d %H:%M:%S")
                               - datetime.datetime.strptime(d.get("de_la", now()),
                                                            "%Y-%m-%d %H:%M:%S")).total_seconds()
                except Exception:  # noqa: BLE001
                    pass
                # Doua praguri, fiindca sunt doua situatii diferite. Daca procesul chiar
                # nu mai exista, asteptarea nu produce nicio informatie noua — se lasa doar o
                # marja scurta, cat sa nu se sparga lacatul unui proces care tocmai porneste.
                # Pragul lung ramane pentru cazul in care nu se poate verifica: acolo tacerea
                # nu e dovada, o scriere lunga arata identic cu una moarta.
                # Pana la 13 septembrie 2026 conditia avea in coada `and mort`, deci pragul lung
                # nu se folosea: un lacat neverificabil nu se spargea deloc, contrar comentariului
                # de mai sus si mesajului de commit care a introdus cele doua praguri (2.12.1).
                stare = _stare_proces(d.get("pid", 0))
                prag = LACAT_MORT if stare == "mort" else (LACAT_INVECHIT if stare is None else None)
                if prag is not None and vechime > prag:
                    print(f"  lacat invechit de la PID {d.get('pid')} ({d.get('tag')!r}; proces "
                          f"{'mort' if stare == 'mort' else 'neverificabil'}), il sparg",
                          file=sys.stderr)
                    try:
                        os.remove(LACAT)
                    except OSError:
                        pass
                    continue
                if asteptat == 0:
                    print(f"  astept randul: scrie {d.get('tag')!r} (PID {d.get('pid')})",
                          file=sys.stderr)
                if asteptat >= self.timeout:
                    raise TimeoutError(
                        f"randul n-a venit in {self.timeout}s; tine {d.get('tag')!r} "
                        f"(PID {d.get('pid')}, de la {d.get('de_la')})")
                time.sleep(self.delay)
                asteptat += self.delay

    def __exit__(self, exc_type, exc, tb):
        if self.tinut:
            try:
                os.remove(LACAT)
            except OSError:
                pass
        return False


io_open = open       # numele lung, ca sa nu se confunde cu modulul io in citirea lacatului


def _inchide_ce_am_pornit(timeout=120, delay=1.0):
    """Inchide Zotero daca l-am pornit noi, si asteapta pana cand procesul chiar a disparut.

    Nu atinge un Zotero deschis de om: acela ramane treaba lui, si `wait_until_quiescent` il
    refuza cu mesajul potrivit. Aici e vorba doar de cel pe care l-a pornit `write_session` la
    iesirea de dinainte — cine a aprins lumina o si stinge.

    Asteptarea e pe disparitia procesului, nu pe un numar de secunde: masurat, al doilea ciclu
    il prindea pe Zotero pornind si cadea cu `database is locked` la commit, iar cat dureaza
    pornirea nu se poate presupune.

    **Decizia se ia pe proces, nu pe port.** `write_session` porneste Zotero fara sa astepte
    portul. Un al doilea ciclu care intra imediat — `get_or_create_collection` urmat de scrierea
    propriu-zisa — il gaseste cu procesul pornit si portul inca inchis. Intrebat doar portul,
    raspunsul era „nu ruleaza": steagul se stergea, nimic nu se inchidea, iar scrierea pornea
    sub un Zotero care se deschidea. Era chiar cursa pe care functia exista s-o inchida
    (auditul scripturilor, 13 septembrie 2026). Masurat pe Zotero real in aceeasi zi, doua
    cicluri la rand in acelasi proces: la 12 s dupa pornire procesul exista, iar portul inca nu
    raspundea; al doilea ciclu l-a inchis in 11 s si a scris.

    `taskkill` gratios se retrimite la cateva secunde cat procesul inca exista. Motivul e o
    presupunere, nemasurata: un program aflat la pornire poate sa nu aiba inca fereastra care sa
    primeasca cererea de inchidere, iar o singura cerere s-ar pierde. Niciodata `/F`."""
    global _PORNIT_DE_AICI
    if not _PORNIT_DE_AICI:
        return False
    _PORNIT_DE_AICI = False
    if not zotero_running() and _zotero_proces() is not True:
        return False
    import subprocess  # noqa: PLC0415
    print("  inchid Zotero pornit de ciclul dinainte...", file=sys.stderr)
    asteptat, trimis_la = 0.0, None
    while zotero_running() or _zotero_proces() is True:
        if trimis_la is None or asteptat - trimis_la >= 10:
            subprocess.run(["taskkill", "/IM", "zotero.exe"], capture_output=True)
            trimis_la = asteptat
        if asteptat >= timeout:
            raise SystemExit(
                "Zotero, pornit de ciclul dinainte, n-a disparut in %ds. Inchide-l gratios "
                "(taskkill /IM zotero.exe, fara /F) si reia." % int(asteptat))
        time.sleep(delay)
        asteptat += delay
    return True


class write_session:
    """One batched DB-write cycle (Zotero CLOSED). Encapsulates the whole protocol:
    wait-until-quiescent -> backup -> open_rw -> (your writes) -> commit -> re-read
    from disk to verify the commit landed.

        with zot.write_session("acte-de-proba") as cur:
            sid = zot.add_item(cur, "statute", {"nameOfAct": "...", "codeNumber": "57/1968",
                                                "dateEnacted": "1968-00-00 1968"}, collection_id=cid)
            zot.attach_pdf(cur, sid, "/path/Legea_57_1968.pdf")
            zot.add_item(cur, "webpage", {"title": "Servicii", "url": "..."},
                         creators=[("author", "Asociatia Exemplu pentru Servicii Sociale")], collection_id=cid)

    Exceptions inside the block roll back and re-raise (nothing is written). Resolve
    collection ids with find_collection() BEFORE the block, while Zotero is open.

    **Ce face cu aplicatia.** La intrare cere Zotero INCHIS si refuza daca nu e — inchiderea o
    face omul sau agentul. La iesire, dupa un commit reusit, il PORNESTE (`redeschide=False` o
    opreste), fiindca starea implicita buna e cea de citire.

    Iar fiindca il porneste, tot ea raspunde de urmarea: **daca procesul a fost pornit de aici,
    urmatoarea intrare il inchide singura si asteapta pana cand chiar a disparut.** Fara asta,
    doua cicluri consecutive cadeau cu `database is locked` la commit — al doilea il prindea pe
    Zotero pornind, iar tranzactia cadea intreaga. Asteptarea e pe disparitia procesului, nu pe
    un numar de secunde: cat dureaza pornirea nu se poate presupune.

    Regula skill-ului ramane **un singur ciclu per sarcina** — nu fiindca doua n-ar merge, ci
    fiindca fiecare ciclu inchide si redeschide aplicatia, iar asta costa.

    Cazul care ii da masura, „adaug o sursa noua": itemul, fisierul si textul memoizat intra
    toate trei in acelasi ciclu. `ensure_cached` primeste cursorul, ca orice alta scriere:

        cheie, cid = zot.find_collection("Metoda")        # cat Zotero e inca deschis
        # ... aici se inchide Zotero ...
        with zot.write_session("sursa noua") as cur:
            iid = zot.add_item(cur, "journalArticle", campuri, creatori, collection_id=cid)
            aid = zot.attach_pdf(cur, iid, cale_pdf, title="Versiunea publicata")
            pdf_text_cache.ensure_cached(cur, zot.key_for_item_id(cur, aid), cale_pdf)
        # ... abia acum se redeschide Zotero, o singura data ...

    Impartita in doua cicluri, aceeasi sarcina costa doua inchideri si o redeschidere intre
    ele — adica exact configuratia in care al doilea ciclu refuza."""
    def __init__(self, tag, verify=True, asteapta=0, redeschide=True):
        self.tag, self.verify, self.bak = tag, verify, None
        self.coada = coada(tag)
        # `redeschide`: dupa o scriere reusita, lasa Zotero PORNIT. Regula, platita de doua ori
        # intr-o zi: scrierea cere aplicatia inchisa si e rara; citirea o cere deschisa si e
        # deasa. Cine scria o lasa inchisa, corect dupa litera, iar sesiunea urmatoare regenera
        # un raport de doua mii de randuri fara aparat — cu cod de iesire zero, deci de aruncat.
        # Starea implicita buna e cea de citire; cine are nevoie sa scrie inchide oricum singur,
        # fiindca protocolul il obliga.
        self.redeschide = redeschide
        # `asteapta` in secunde: cat sa astepte inchiderea lui Zotero inainte de a refuza.
        # Zero pastreaza purtarea de pana acum — refuz imediat, cu mesajul lui.
        self.asteapta = asteapta
    def __enter__(self):
        self.coada.__enter__()      # randul se ia INAINTE de a inchide Zotero
        try:
            _inchide_ce_am_pornit()
            if self.asteapta and zotero_running():
                asteapta_inchiderea(timeout=self.asteapta)
            wait_until_quiescent()
            self.bak = backup(self.tag)
            self.con = open_rw(); self.cur = self.con.cursor()
        except BaseException:
            # Randul luat si nedat inapoi. `__exit__` NU se cheama cand `__enter__` esueaza,
            # deci fara asta lacatul ramane pe disc dupa un refuz — iar refuzul cel mai
            # obisnuit e chiar cel asteptat: „Zotero inca raspunde". Masurat, 23.08.2026:
            # sesiunea urmatoare a asteptat un sfert de ora randul unui proces mort de cateva
            # secunde. `BaseException`, nu `Exception`: `wait_until_quiescent` ridica
            # `SystemExit`, care nu e `Exception`.
            self.coada.__exit__(None, None, None)
            raise
        return self.cur
    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type:
                self.con.rollback(); self.con.close(); return False  # re-raise
            self.con.commit(); self.con.close()
            if self.redeschide:
                porneste_zotero(astepta=False)
            if self.verify:
                n = _ro().execute("SELECT COUNT(*) FROM items WHERE synced=0").fetchone()[0]
                print(f"OK write_session '{self.tag}': committed; {n} item(s) pending sync; backup {os.path.basename(self.bak)}")
            _spune_copiile()
            return False
        finally:
            # Randul se elibereaza ORICUM — si pe exceptie, si pe iesire curata. Fara
            # `finally`, o scriere care crapa ar tine lacatul: cat procesul ei traieste, pentru
            # totdeauna (celelalte primesc TimeoutError); dupa ce moare, inca 30 de secunde.
            self.coada.__exit__(exc_type, exc, tb)

# ---- create-item building blocks (use inside write_session) ----
def item_type_id(cur, type_name):
    r = cur.execute("SELECT itemTypeID FROM itemTypes WHERE typeName=?", (type_name,)).fetchone()
    return r[0] if r else None

def find_collection(name, verifica_api=True, parent_collection_id=None):
    """(key, collectionID) pentru o colecție, sau None. Citește baza read-only, deci merge cu
    Zotero pornit.

    **Numele singur nu identifică o colecție.** Măsurat, 26 august 2026: două colecții numite
    „metodologie", amândouă de nivel întâi, iar căutarea a întors prima — șase acte normative au
    aterizat în colecția altui proiect, iar scrierea a „reușit". Nu e o imprecizie: cu doi agenți
    în aceeași bibliotecă, potrivirea după nume e nesigură prin construcție.

    Deci:

    - `parent_collection_id` dat → potrivirea se face pe **perechea (nume, părinte)**;
    - `name` poate fi o **cale** — „proiect-a/metodologie" — și atunci fiecare treaptă se caută
      sub cea dinainte;
    - `name` poate fi direct **cheia** colecției, opt caractere majuscule;
    - fără părinte și cu nume ambiguu, **ridică `LookupError`** în loc să aleagă primul rând. Un
      rezultat greșit e mai scump decât o eroare: eroarea se vede, iar itemii scriși aiurea nu.

    `verifica_api`: la miss, întreabă și API-ul viu înainte de a răspunde None. Motivul e o altă
    confuzie măsurată: o colecție creată din interfață cu câteva secunde înainte **nu e încă pe
    disc**, iar căutarea întoarce None — identic cu „numele e greșit"."""
    con = _ro()

    # o cale: fiecare treapta sub cea dinainte — DAR numai cand nicio colectie nu poarta chiar
    # numele intreg, cu bara cu tot. Masurat pe biblioteca, 13 septembrie 2026: doua din 266 de
    # colectii au „/" in nume. Citit direct ca o cale, numele lor nu mai gasea nimic, iar ele se
    # mai puteau cere doar dupa cheie. Numele exact se incearca intai; ambiguitatea lui ramane
    # tratata mai jos, ca la orice nume.
    if "/" in name and not con.execute(
            "SELECT 1 FROM collections WHERE collectionName=? LIMIT 1", (name,)).fetchone():
        parinte = parent_collection_id
        gasit = None
        for treapta in [x.strip() for x in name.split("/") if x.strip()]:
            gasit = find_collection(treapta, verifica_api=False, parent_collection_id=parinte)
            if not gasit:
                return None
            parinte = gasit[1]
        return gasit

    # cheia colectiei, nu numele
    if len(name) == 8 and name.isupper() and name.isalnum():
        r = con.execute("SELECT key, collectionID FROM collections WHERE key=?", (name,)).fetchone()
        if r:
            return (r[0], r[1])

    if parent_collection_id is not None:
        r = con.execute("SELECT key, collectionID FROM collections "
                        "WHERE collectionName=? AND parentCollectionID IS ?",
                        (name, parent_collection_id)).fetchone()
        if r:
            return (r[0], r[1])
    else:
        randuri = con.execute(
            "SELECT key, collectionID, parentCollectionID FROM collections WHERE collectionName=?",
            (name,)).fetchall()
        if len(randuri) > 1:
            unde = ", ".join("%s (parinte %s)" % (r[0], r[2] if r[2] else "-") for r in randuri)
            raise LookupError(
                "numele %r se potriveste la %d colectii: %s. Da parintele, calea "
                "(\"parinte/%s\") sau cheia." % (name, len(randuri), unde, name))
        if randuri:
            return (randuri[0][0], randuri[0][1])
        r = None
    if not verifica_api or not zotero_running():
        return None
    try:
        import urllib.request
        uid = detect_user_id()
        url = f"http://127.0.0.1:23119/api/users/{uid}/collections?limit=200"
        for c in json.loads(urllib.request.urlopen(url, timeout=10).read().decode()):
            if c.get("data", {}).get("name") == name:
                print(f"ATENTIE: colectia {name!r} exista in Zotero (cheia {c['key']}) dar NU e "
                      "inca scrisa pe disc. Nu e inexistenta, e nesincronizata.", file=sys.stderr)
                return None
    except Exception:
        pass
    return None

def chei_fixate(chei):
    """{cheie_item: citekey} pentru itemele care au cheia FIXATA in campul `Extra`.

    Se citeste direct din baza, read-only, deci merge cu Zotero pornit si nu depinde de Better
    BibTeX. E stratul care trebuie intrebat PRIMUL: fixarea exista tocmai ca sa nu se schimbe
    cheia, iar documentele citeaza ce e fixat."""
    if not chei:
        return {}
    qs = ",".join("?" * len(chei))
    r = _ro().execute(
        "SELECT i.key, v.value FROM items i "
        "JOIN itemData d ON d.itemID = i.itemID "
        "JOIN fields f ON f.fieldID = d.fieldID "
        "JOIN itemDataValues v ON v.valueID = d.valueID "
        "WHERE f.fieldName = 'extra' AND i.key IN (%s)" % qs, list(chei)).fetchall()
    import re as _re
    out = {}
    for k, extra in r:
        m = _re.search(r"(?im)^\s*Citation Key\s*:\s*(\S+)", extra or "")
        if m:
            out[k] = m.group(1)
    return out


def citekeys(chei, timeout=20, spune=True):
    """{cheie_item: citekey}, cu **cheia fixata intai**.

    Doua straturi, in ordinea asta:

    1. **cheia fixata in `Extra`**, citita din baza. Fixarea exista tocmai ca sa nu se schimbe
       cheia, iar documentele citeaza ce e fixat;
    2. **Better BibTeX**, prin JSON-RPC, pentru itemele fara fixare.

    De ce ordinea conteaza, masurat pe 26.08.2026: BBT intoarce cheia **dinamica** si cand exista
    una fixata — „AgentiaExemplu2009" fixat, dar BBT raspunde
    „AgențiaNaționalăpentruExemplulConstruitȘiSiturileImaginare2009". Din opt
    iteme cu cheie fixata, patru difereau. Intr-un caz real, 27 din 57 de surse ramaneau
    nelegate — **tacut**, fiindca lipsa unei chei nu se deosebeste de „itemul nu exista".

    Exportul CSL-JSON al lui Zotero respecta fixarea, verificat pe alta biblioteca: 26 din 26.
    Deci stratul de aici se aliniaza la ce face deja exportul, nu inventeaza altceva.

    `spune`: cand raman chei nelegate, o zice pe stderr. O lista mai scurta nu trebuie sa treaca
    drept raspuns complet — un pas care „a trecut" fara sa faca nimic se vede doar daca numara
    cu voce tare."""
    chei = list(chei)
    out = dict(chei_fixate(chei))
    ramase = [k for k in chei if k not in out]
    if ramase:
        try:
            import urllib.request  # noqa: PLC0415
            cerere = urllib.request.Request(
                "http://127.0.0.1:23119/better-bibtex/json-rpc",
                data=json.dumps({"jsonrpc": "2.0", "method": "item.citationkey",
                                 "params": [ramase]}).encode("utf-8"),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(cerere, timeout=timeout) as r:
                raspuns = json.loads(r.read().decode("utf-8"))
            for k, v in (raspuns.get("result") or {}).items():
                if v:
                    out[k] = v
        except Exception:  # noqa: BLE001
            pass
    if spune:
        lipsa = [k for k in chei if not out.get(k)]
        if lipsa:
            print("  citekeys: %d din %d legate; fara cheie: %s"
                  % (len(chei) - len(lipsa), len(chei), ", ".join(lipsa[:8])), file=sys.stderr)
    return out


def asteapta_citekeys(chei, tries=30, delay=2.0, vechi=None):
    """Asteapta pana BBT are citekey pentru TOATE cheile date. True daca a reusit in buget.

    Se cheama dupa repornirea lui Zotero, la finalul unui ciclu de scriere. Fara ea,
    randarea citarilor porneste inaintea rescanarii si raporteaza chei „negasite" care de
    fapt inca nu exista — un esec care arata exact ca o cheie gresita.

    **`vechi` e obligatoriu cand ai MODIFICAT un item existent**, nu cand ai creat unul nou.
    Masurat: la un item caruia i s-a scris data direct in baza, repornirea NU regenereaza
    cheia — BBT nu vede evenimentul de modificare cand scrierea ocoleste Zotero. Cheia veche
    ramane, iar verificarea „are cheie?" intoarce True pe exact cazul in care nu s-a
    intamplat nimic. Cu `vechi={cheie_item: citekey_dinainte}` se asteapta ca valoarea sa
    DIFERE, nu doar sa existe.

    La itemii NOI, repornirea chiar atribuie cheia, deci `vechi` nu e necesar.

    Daca expira si stii ca ai modificat un camp din formula cheii — autor, an, titlu — cheia
    se reface din interfata: clic dreapta pe item → Better BibTeX → Refresh BibTeX key. Din
    afara nu se poate declansa (vezi `citekeys`)."""
    vechi = vechi or {}
    for _ in range(tries):
        try:
            r = citekeys(chei, spune=False)
            if r and all(r.get(k) and r.get(k) != vechi.get(k) for k in chei):
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(delay)
    return False


def campuri_valide(cur, tip):
    """Numele campurilor pe care Zotero le accepta pentru un tip de item."""
    q = ("SELECT f.fieldName FROM itemTypeFields itf "
         "JOIN fields f ON f.fieldID = itf.fieldID "
         "JOIN itemTypes it ON it.itemTypeID = itf.itemTypeID "
         "WHERE it.typeName = ? ORDER BY itf.orderIndex")
    return [r[0] for r in cur.execute(q, (tip,))]


def creatori_valizi(cur, tip):
    """Tipurile de creator acceptate pentru un tip de item (author, editor, ...)."""
    q = ("SELECT ct.creatorType FROM itemTypeCreatorTypes itct "
         "JOIN creatorTypes ct ON ct.creatorTypeID = itct.creatorTypeID "
         "JOIN itemTypes it ON it.itemTypeID = itct.itemTypeID "
         "WHERE it.typeName = ? ORDER BY itct.primaryField DESC")
    return [r[0] for r in cur.execute(q, (tip,))]


def schimba_tip(cur, item_id, tip_nou, doar_arata=False):
    """Muta un item in alt tip, stergand campurile pe care tipul nou nu le are.

    Cazul care a cerut-o: un preprint capata versiune publicata, deci itemul devine articol de
    revista. Fara pasul asta, campurile tipului vechi raman agatate de tipul nou — Zotero nu le
    afiseaza, dar ele sunt in baza, si reapar daca itemul e mutat inapoi sau exportat. Masurat
    pe patru conversii, 23.08.2026: la toate patru a fost necesar.

    **Ce se sterge se afla din `itemTypeFields`, niciodata dintr-o lista scrisa dinainte.** Un
    item facut de mana si unul adus de conector n-au aceleasi campuri, iar cele in plus nu le-a
    scris nimeni intentionat, deci nu le are nimeni pe lista. O conversie care porneste de la
    campurile stiute le lasa exact pe acelea in baza.

    **Conversia rupe cheia de citare.** Anul se schimba odata cu tipul (preprintul e din alt an
    decat articolul), iar Better BibTeX recompune cheia din an. Daca lucrarea era deja citata
    intr-un manuscris, marcajul ramane la anul vechi si randarea il raporteaza negasit — o
    eroare care arata ca o cheie gresita, nu ca o conversie reusita. Deci: cauta marcajele
    lucrarii in manuscrise INAINTE de conversie, si foloseste `asteapta_citekeys(..., vechi=...)`
    dupa, ca sa astepti cheia NOUA, nu doar existenta uneia.

    Cu `doar_arata=True` nu scrie nimic si intoarce ce s-ar pierde — de folosit intai, mereu.

    Intoarce `{"pierdute": {camp: valoare}, "creatori_mutati": [(nume, tip_vechi)]}`."""
    tip_vechi = cur.execute(
        "SELECT it.typeName FROM items i JOIN itemTypes it ON it.itemTypeID = i.itemTypeID "
        "WHERE i.itemID = ?", (item_id,)).fetchone()
    if not tip_vechi:
        raise ValueError("itemID %s nu exista" % item_id)
    tip_vechi = tip_vechi[0]
    if tip_vechi == tip_nou:
        return {"pierdute": {}, "creatori_mutati": []}

    valide = set(campuri_valide(cur, tip_nou))
    pierdute = {}
    for fid, nume, valoare in cur.execute(
            "SELECT f.fieldID, f.fieldName, idv.value FROM itemData idata "
            "JOIN fields f ON f.fieldID = idata.fieldID "
            "JOIN itemDataValues idv ON idv.valueID = idata.valueID "
            "WHERE idata.itemID = ?", (item_id,)).fetchall():
        if nume not in valide:
            pierdute[nume] = valoare

    # Tipurile de creator difera si ele. Un `reviewedAuthor` ramas pe un tip care nu-l are
    # produce un rand orfan in itemCreators: autorul dispare din interfata fara sa fie sters.
    ct_valizi = creatori_valizi(cur, tip_nou)
    implicit = ct_valizi[0] if ct_valizi else "author"
    mutati = []
    for nume, ct in cur.execute(
            "SELECT c.lastName, ct.creatorType FROM itemCreators ic "
            "JOIN creators c ON c.creatorID = ic.creatorID "
            "JOIN creatorTypes ct ON ct.creatorTypeID = ic.creatorTypeID "
            "WHERE ic.itemID = ?", (item_id,)).fetchall():
        if ct not in ct_valizi:
            mutati.append((nume, ct))

    if doar_arata:
        return {"pierdute": pierdute, "creatori_mutati": mutati}

    for nume in pierdute:
        cur.execute("DELETE FROM itemData WHERE itemID = ? AND fieldID = "
                    "(SELECT fieldID FROM fields WHERE fieldName = ?)", (item_id, nume))
    if mutati:
        nou_id = cur.execute("SELECT creatorTypeID FROM creatorTypes WHERE creatorType = ?",
                             (implicit,)).fetchone()[0]
        for _nume, ct in mutati:
            cur.execute("UPDATE itemCreators SET creatorTypeID = ? WHERE itemID = ? AND "
                        "creatorTypeID = (SELECT creatorTypeID FROM creatorTypes WHERE creatorType = ?)",
                        (nou_id, item_id, ct))
    cur.execute("UPDATE items SET itemTypeID = ? WHERE itemID = ?",
                (item_type_id(cur, tip_nou), item_id))
    touch(cur, item_id)
    return {"pierdute": pierdute, "creatori_mutati": mutati}


def add_collection(cur, name, parent_collection_id=None, noua=False):
    """Inside a zot.write_session: REFOLOSEȘTE colecția cu același nume sub același părinte,
    dacă există și nu e la coș; altfel o creează. Întoarce collectionID-ul.
    Refolosirea e aceeași întrebare ca „nu crea duplicate", doar la nivel de colecție.

    `noua=True` creează una nouă chiar dacă există alta cu același nume. Măsurat pe 8 octombrie
    2026: o sesiune voia să recreeze cu chei noi două colecții, a primit înapoi rândurile vechi,
    iar ștergerea „celor vechi" de la final le-a șters de tot, cu apartenențe cu tot. Pentru o
    colecție care există local dar lipsește de pe server, calea e `reurca_colectie`, nu asta.

    Perechea lipsă a lui `find_collection`: skill-ul știa să AȘEZE itemi într-o colecție
    existentă, dar nu s-o creeze, iar asta e primul pas al oricărui proiect nou. Fără ea
    rămâneau trei ieșiri proaste — SQL de mână (interzis de protocol), un ciclu de închidere
    de 2–4 minute pentru o singură inserare, sau cerut omului s-o facă din interfață.

    parent_collection_id None => colecție de nivel întâi."""
    row = None if noua else cur.execute(
        "SELECT collectionID FROM collections WHERE collectionName=? AND "
        "collectionID NOT IN (SELECT collectionID FROM deletedCollections) AND "
        + ("parentCollectionID IS NULL" if parent_collection_id is None else "parentCollectionID=?"),
        (name,) if parent_collection_id is None else (name, parent_collection_id)).fetchone()
    if row:
        return row[0]
    cid = _next_id(cur, "collectionID", "collections")
    cur.execute("INSERT INTO collections (collectionID,collectionName,parentCollectionID,"
                "clientDateModified,libraryID,key,version,synced) VALUES (?,?,?,?,?,?,0,0)",
                (cid, name, parent_collection_id, now(), LIBRARY_ID, newkey()))
    return cid


def reurca_colectie(cur, collection_id):
    """Inside a zot.write_session: face o colecție-fantomă să urce din nou pe server.
    Întoarce cheia nouă.

    Fantoma: colecția există local, marcată `synced=1`, dar serverul n-o mai are (nici pe web).
    Apare după restaurarea unei baze dintr-o copie veche. Zotero n-o mai urcă niciodată, fiindcă
    crede că serverul o are, iar la prima sincronizare completă absența se propagă ca ștergere.

    Ce face: îi dă cheie nouă cu `version=0, synced=0`, adică Zotero o vede ca pe o colecție
    nouă și o urcă. Rândul, numele, părintele și membrii rămân pe loc, deci nu se șterge și nu
    se recreează nimic. Membrii și subcolecțiile directe se marchează `synced=0`, ca să urce cu
    trimiterea la cheia nouă.

    Numai pentru fantome confirmate: dacă serverul are încă vechea cheie, rezultatul e o a doua
    colecție cu același nume. Verificarea absenței se face pe web, înainte."""
    k = newkey()
    cur.execute("UPDATE collections SET key=?, version=0, synced=0, clientDateModified=? "
                "WHERE collectionID=?", (k, now(), collection_id))
    cur.execute("UPDATE items SET synced=0 WHERE itemID IN "
                "(SELECT itemID FROM collectionItems WHERE collectionID=?)", (collection_id,))
    cur.execute("UPDATE collections SET synced=0 WHERE parentCollectionID=?", (collection_id,))
    return k


def get_or_create_collection(name, parent_collection_id=None, tag=None):
    """(key, collectionID) pentru o colecție, creând-o dacă lipsește. Deschide singură un
    `write_session` — deci Zotero ÎNCHIS — doar când chiar trebuie creată; dacă există deja,
    întoarce fără să închidă nimic. Verificarea întâi, ciclul scump doar la miss: aceeași
    ordine ca la cache-ul de text.

    **Nu se cheamă din interiorul unui `write_session`.** Ar deschide al doilea, care cere
    rândul ținut de primul — o blocare pe sine, care nu se termină prin așteptare. Coada o
    recunoaște acum și crapă pe loc, dar locul unde se repară e chiar aici: colecția se
    rezolvă **înainte** de ciclu, iar înăuntru se folosește `add_collection(cur, ...)`, care
    primește cursorul.

    Capcana se vede greu fiindcă apare **doar când colecția lipsește**: la a doua rulare
    colecția există, funcția întoarce fără să deschidă nimic, și totul pare în regulă."""
    found = find_collection(name, parent_collection_id=parent_collection_id)
    if found:
        return found
    with write_session(tag or f"add-collection-{name}") as cur:
        cid = add_collection(cur, name, parent_collection_id)
        key = cur.execute("SELECT key FROM collections WHERE collectionID=?", (cid,)).fetchone()[0]
    return key, cid


def item_id_for_key(cur_or_ro, key):
    """itemID for an existing item's API key (works with a live write_session cursor OR
    a fresh _ro() connection). Use this to file an ALREADY-EXISTING library item into a new
    collection with add_to_collection() — see 'no duplicates' in SKILL.md §F. None if no match."""
    r = cur_or_ro.execute("SELECT itemID FROM items WHERE key=?", (key,)).fetchone()
    return r[0] if r else None

def key_for_item_id(cur_or_ro, item_id):
    """Cheia unui item, din itemID. Perechea lui `item_id_for_key`.

    Exista fiindca cele doua identitati circula in locuri diferite: scrierile in baza merg pe
    `itemID` (asa se leaga randurile intre ele), iar API-ul, cache-ul de text si citarile merg
    pe `key`. `attach_pdf` intoarce itemID-ul, `ensure_cached` cere cheia — fara punte, reteta
    care le pune in acelasi ciclu nu se poate scrie."""
    r = cur_or_ro.execute("SELECT key FROM items WHERE itemID=?", (item_id,)).fetchone()
    return r[0] if r else None


def attachment_path(key, cur=None):
    """Full filesystem path for an attachment key: resolves both an imported
    stored copy ('storage:<file>' -> storage/<key>/<file>) and a LINKED file
    (path is already absolute — no storage/ copy, and storageHash is always
    NULL for these, since Zotero doesn't own/hash a file it doesn't manage).
    Read-only; works whether Zotero is open or not. None if no such attachment.

    **In interiorul unui ciclu de scriere se da `cur`.** Calea trebuie citita in ciclu — Zotero
    deschis poate redenumi fisierul dupa parintele pe care il creeaza singur (27.09.2026) —, dar nu
    printr-o a doua conexiune: peste o tranzactie mare, aceea citeste o imagine incoerenta a bazei
    (16.09.2026)."""
    con = cur if cur is not None else _ro()
    r = con.execute("SELECT path FROM itemAttachments WHERE itemID=(SELECT itemID FROM items WHERE key=?)",
                    (key,)).fetchone()
    if not r or not r[0]:
        return None
    path = r[0]
    return os.path.join(STORAGE, key, path[len("storage:"):]) if path.startswith("storage:") else path

def _next_id(cur, col, table):
    return cur.execute(f"SELECT COALESCE(MAX({col}),0)+1 FROM {table}").fetchone()[0]

def _creator_id(cur, last, first, field_mode):
    r = cur.execute("SELECT creatorID FROM creators WHERE lastName=? AND firstName=? AND fieldMode=?",
                    (last, first, field_mode)).fetchone()
    if r: return r[0]
    nid = _next_id(cur, "creatorID", "creators")
    cur.execute("INSERT INTO creators (creatorID,firstName,lastName,fieldMode) VALUES (?,?,?,?)",
                (nid, first, last, field_mode))
    return nid

def _set_creators(cur, item_id, creators):
    """creators = [(creatorType, "Whole Institution Name")]  -> literal (fieldMode 1)
                  [(creatorType, lastName, firstName)]        -> personal (fieldMode 0)"""
    for idx, cre in enumerate(creators):
        ctype = cre[0]
        if len(cre) == 2:            # literal / institutional author
            last, first, fmode = cre[1], "", 1
        else:
            last, first, fmode = cre[1], cre[2], 0
        ctid = cur.execute("SELECT creatorTypeID FROM creatorTypes WHERE creatorType=?", (ctype,)).fetchone()
        if not ctid: raise ValueError(f"unknown creator type {ctype!r}")
        cid = _creator_id(cur, last, first, fmode)
        cur.execute("INSERT INTO itemCreators (itemID,creatorID,creatorTypeID,orderIndex) VALUES (?,?,?,?)",
                    (item_id, cid, ctid[0], idx))

def _predicat_id(cur, predicat):
    r = cur.execute("SELECT predicateID FROM relationPredicates WHERE predicate=?",
                    (predicat,)).fetchone()
    if r:
        return r[0]
    cur.execute("INSERT INTO relationPredicates (predicate) VALUES (?)", (predicat,))
    return cur.execute("SELECT predicateID FROM relationPredicates WHERE predicate=?",
                       (predicat,)).fetchone()[0]


def relateaza(cur, cheie_a, cheie_b, predicat="dc:relation"):
    """Leaga doua iteme, asa cum face butonul „Related" din interfata. True daca s-a scris ceva.

    **Relatia se scrie in AMANDOUA sensurile**, fiindca asa o tine Zotero: doua randuri, unul
    pe fiecare item. Scrisa intr-un singur sens, ea apare la un item si lipseste la celalalt —
    iar cine deschide itemul al doilea trage concluzia ca nu e legat de nimic.

    Obiectul relatiei e URI-ul itemului, `http://zotero.org/users/<uid>/items/<CHEIE>`, nu
    cheia goala. Forma se vede in bibliotecă la relatiile scrise de Zotero insusi.

    `dc:relation` e ce inseamna „Related". `dc:replaces` e altceva si nu se foloseste aici: el
    e felul in care Zotero marcheaza itemul pastrat dupa o fuziune de duplicate, deci l-ar face
    pe cititor sa creada ca una dintre forme a fost inlaturata.

    Cazul pentru care a fost scrisa: formele succesive ale aceluiasi act, care sunt iteme
    separate fiindca au texte si cifre diferite, dar trebuie sa se vada una din alta."""
    if cheie_a == cheie_b:
        return False
    uid = detect_user_id()
    if not uid:
        return False
    pid = _predicat_id(cur, predicat)
    scris = False
    for de_la, catre in ((cheie_a, cheie_b), (cheie_b, cheie_a)):
        iid = item_id_for_key(cur, de_la)
        if iid is None:
            continue
        uri = "http://zotero.org/users/%s/items/%s" % (uid, catre)
        inainte = cur.execute("SELECT COUNT(*) FROM itemRelations WHERE itemID=? AND "
                              "predicateID=? AND object=?", (iid, pid, uri)).fetchone()[0]
        cur.execute("INSERT OR IGNORE INTO itemRelations (itemID, predicateID, object) "
                    "VALUES (?,?,?)", (iid, pid, uri))
        if not inainte:
            touch(cur, iid)
            scris = True
    return scris


def dezleaga(cur, cheie_a, cheie_b, predicat="dc:relation"):
    """Sterge legatura dintre doua iteme, in AMANDOUA sensurile. True daca a sters ceva.

    Exista fiindca `relateaza` scrie doua randuri: sters unul singur, itemul al doilea ramane
    cu o legatura catre unul care nu mai stie de el. Aceeasi capcana ca la scriere, in oglinda.

    Cazul pentru care a fost scrisa: o legatura pusa pe potrivire prea larga — actele care pun
    in aplicare o lege poarta codul ei in denumire, deci ajunsesera legate ca forme ale legii."""
    uid = detect_user_id()
    if not uid:
        return False
    r = cur.execute("SELECT predicateID FROM relationPredicates WHERE predicate=?",
                    (predicat,)).fetchone()
    if not r:
        return False
    pid, sters = r[0], False
    for de_la, catre in ((cheie_a, cheie_b), (cheie_b, cheie_a)):
        iid = item_id_for_key(cur, de_la)
        if iid is None:
            continue
        uri = "http://zotero.org/users/%s/items/%s" % (uid, catre)
        cur.execute("DELETE FROM itemRelations WHERE itemID=? AND predicateID=? AND object=?",
                    (iid, pid, uri))
        if cur.rowcount:
            touch(cur, iid)
            sters = True
    return sters


def add_to_collection(cur, item_id, collection_id):
    oi = cur.execute("SELECT COALESCE(MAX(orderIndex),0)+1 FROM collectionItems WHERE collectionID=?",
                     (collection_id,)).fetchone()[0]
    cur.execute("INSERT OR IGNORE INTO collectionItems (collectionID,itemID,orderIndex) VALUES (?,?,?)",
                (collection_id, item_id, oi))

def _doi_normal(v):
    """DOI-ul fara adresa din fata, fara spatii, cu litere mici. „https://doi.org/10.1/AB" -> „10.1/ab"."""
    v = (v or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if v.startswith(prefix):
            v = v[len(prefix):]
    return v.strip()


def _isbn_normal(v):
    return "".join(c for c in (v or "").lower() if c.isalnum())


def _titlu_normal(v):
    return " ".join((v or "").lower().split())


def gaseste_duplicat(cur, type_name, fields):
    """itemID-ul unui item care e ACELASI lucru, sau None. Citeste prin cursorul primit.

    Ordinea e cea a increderii: DOI, apoi ISBN, apoi titlul exact plus acelasi tip de item. DOI-ul
    si ISBN-ul identifica lucrarea; titlul singur nu — doua rapoarte pot purta acelasi titlu —,
    de aceea la titlu se cere si tipul.

    **De ce exista.** Masurat pe 23 septembrie 2026: doua cicluri de scriere la optsprezece minute
    distanta au adaugat aceleasi patru articole de doua ori, fiindcă `add_item` crea itemul fara sa
    se uite daca exista. Dublurile au iesit la iveala doua zile mai tarziu, la o verificare ceruta
    de autor; memoizarea textului statea numai sub primele copii.

    Cautarea merge pe cursorul ciclului, nu pe o a doua conexiune: peste o tranzactie mare, o
    conexiune noua citeste o imagine incoerenta a bazei (masurat pe 16 septembrie 2026)."""
    campuri = {k.lower(): v for k, v in (fields or {}).items() if v}

    doi = _doi_normal(campuri.get("doi"))
    if doi:
        for iid, val in cur.execute(
                "SELECT i.itemID, v.value FROM items i "
                "JOIN itemData d ON d.itemID = i.itemID "
                "JOIN fields f ON f.fieldID = d.fieldID AND f.fieldName = 'DOI' "
                "JOIN itemDataValues v ON v.valueID = d.valueID "
                "WHERE v.value LIKE ? AND i." + NOT_TRASHED, ("%" + doi,)):
            if _doi_normal(val) == doi:
                return iid

    # ISBN-ul identifica o carte, iar fiecare capitol din ea il poarta si el. Fara conditia de tip,
    # o carte noua era „gasita" in primul ei capitol din biblioteca (masurat pe 8 octombrie 2026: doua
    # volume, potrivite unul cu un capitol, altul cu un capitol tipat gresit ca „book").
    isbn = _isbn_normal(campuri.get("isbn"))
    tid_isbn = item_type_id(cur, type_name)
    # La capitole ISBN-ul nu identifica nimic: toate capitolele aceleiasi carti il au. Raman DOI si titlul.
    if isbn and tid_isbn is not None and type_name != "bookSection":
        for iid, val in cur.execute(
                "SELECT i.itemID, v.value FROM items i "
                "JOIN itemData d ON d.itemID = i.itemID "
                "JOIN fields f ON f.fieldID = d.fieldID AND f.fieldName = 'ISBN' "
                "JOIN itemDataValues v ON v.valueID = d.valueID "
                "WHERE i.itemTypeID = ? AND i." + NOT_TRASHED, (tid_isbn,)):
            if _isbn_normal(val) == isbn:
                return iid

    # Actele normative: aceeasi forma = acelasi numar SI aceeasi forma. Titlul nu ajunge in niciun
    # sens. Masurat pe 9 octombrie 2026: forma 18.01.2024 a Codului fiscal a fost lipita de itemul
    # din 2015 (acelasi titlu), iar doua formulari ale titlului OUG 158/2005 n-ar fi fost
    # recunoscute ca acelasi act. Forma se citeste din pagina ei de pe Portal (`url`), apoi din
    # randul „Versiune ..." din `extra`; `dateEnacted` e data adoptarii, aceeasi la toate formele
    # (regula din citari-zotero.md, sectiunea G), deci nu deosebeste forme.
    tid = item_type_id(cur, type_name)
    if type_name == "statute":
        cod = re.sub(r"\s+", "", campuri.get("codenumber") or "")
        if not cod:
            return None

        def forma(url, extra, data):
            m = re.search(r"DetaliiDocument(?:Afis)?/(\d+)", url or "")
            if m:
                return "pagina " + m.group(1)
            m = re.search(r"^(Versiune .+|Forma publicată.+)$", extra or "", re.M)
            if m:
                return m.group(1).strip()
            return "data " + (data or "")[:10]
        ceruta = forma(campuri.get("url"), campuri.get("extra"), campuri.get("dateenacted"))
        q = ("SELECT v.value FROM itemData d JOIN fields f USING(fieldID) JOIN itemDataValues v USING(valueID) "
             "WHERE d.itemID=i.itemID AND f.fieldName='%s'")
        for iid, c2, u2, e2, d2 in cur.execute(
                "SELECT i.itemID, (%s), (%s), (%s), (%s) FROM items i WHERE i.itemTypeID = ? AND i."
                % (q % "codeNumber", q % "url", q % "extra", q % "dateEnacted") + NOT_TRASHED, (tid,)):
            if re.sub(r"\s+", "", c2 or "") == cod and forma(u2, e2, d2) == ceruta:
                return iid
        return None
    titlu = _titlu_normal(campuri.get("title") or campuri.get("nameofact"))
    if titlu and tid is not None:
        for iid, val in cur.execute(
                "SELECT i.itemID, v.value FROM items i "
                "JOIN itemData d ON d.itemID = i.itemID "
                "JOIN fields f ON f.fieldID = d.fieldID AND f.fieldName IN ('title','nameOfAct') "
                "JOIN itemDataValues v ON v.valueID = d.valueID "
                "WHERE i.itemTypeID = ? AND i." + NOT_TRASHED, (tid,)):
            if _titlu_normal(val) == titlu:
                return iid
    return None


def _completeaza_goale(cur, item_id, fields):
    """Pe un item existent: scrie campurile GOALE din `fields`, fara sa suprascrie niciunul plin.
    La `extra`, adauga randurile care lipsesc. Intoarce numele campurilor atinse.

    De ce: masurat pe 27 septembrie 2026, un al doilea ciclu a gasit itemii deja creati, nu i-a
    mai dublat — dar nici n-a pus linia „Ancoreaza:" in `extra`, fiindca intorcea itemul fara sa-l
    completeze. Linia spune ce afirmatie poarta sursa; fara ea, procurarea nu mai stie ordinea.
    Un camp plin nu se atinge: acolo valoarea existenta poate fi corectura cuiva."""
    atinse = []
    for fname, val in (fields or {}).items():
        if val is None or val == "":
            continue
        fid = field_id(cur, fname)
        if fid is None:
            continue
        r = cur.execute("SELECT v.value FROM itemData d JOIN itemDataValues v ON v.valueID = d.valueID "
                        "WHERE d.itemID = ? AND d.fieldID = ?", (item_id, fid)).fetchone()
        vechi = r[0] if r else ""
        if not vechi:
            set_field(cur, item_id, fid, str(val))
            atinse.append(fname)
        elif fname.lower() == "extra":
            lipsa = [l for l in str(val).splitlines() if l.strip() and l.strip() not in
                     {x.strip() for x in vechi.splitlines()}]
            if lipsa:
                set_field(cur, item_id, fid, vechi.rstrip("\n") + "\n" + "\n".join(lipsa))
                atinse.append(fname)
    if atinse:
        touch(cur, item_id)
    return atinse


def add_item(cur, type_name, fields, creators=None, collection_id=None, daca_exista="intoarce"):
    """Create a new item. fields = {fieldName: value} (empty/None skipped);
    creators see _set_creators; files into collection_id if given. Returns itemID.
    Tip: for pure *creation* with Zotero OPEN, POST /connector/saveItems (`zot.save_items`)
    needs no close — **but only when the target collection does not matter.** The connector
    files items into the collection SELECTED IN THE UI, not the one you ask for, and with two
    sessions in the same library that selection is nobody's (see `save_items`). For new items
    in a specific collection: `zot.adauga_itemi(items, colectie=...)`, or this function with
    `collection_id` inside a `write_session`.

    **Nu scrie de doua ori acelasi lucru.** Inainte de INSERT cauta un item cu acelasi DOI, ISBN
    sau titlu exact (`gaseste_duplicat`). `daca_exista`:

    - `"intoarce"` (implicit) — intoarce itemul gasit, il pune in colectia ceruta daca lipsea de
      acolo, si SPUNE ca nu l-a creat. Asa, un al doilea ciclu care reia aceeasi lista ataseaza
      fisierul la itemul existent, in loc sa faca o copie;
    - `"refuza"` — ridica `LookupError`, pentru cine vrea sa opreasca rularea;
    - `"creeaza"` — purtarea veche, pentru cazul in care dublura e voita (doua forme ale aceluiasi
      act se deosebesc prin `dateEnacted`, nu prin titlu, deci `hcl-asemt` cere asta explicit)."""
    tid = item_type_id(cur, type_name)
    if tid is None: raise ValueError(f"unknown item type {type_name!r}")
    if daca_exista not in ("intoarce", "refuza", "creeaza"):
        raise ValueError("daca_exista: 'intoarce', 'refuza' sau 'creeaza'")
    if daca_exista != "creeaza":
        gasit = gaseste_duplicat(cur, type_name, fields)
        if gasit is not None:
            cheie = key_for_item_id(cur, gasit)
            if daca_exista == "refuza":
                raise LookupError("itemul exista deja in biblioteca: %s (itemID %d)" % (cheie, gasit))
            print("  EXISTA deja: %s — nu creez o copie" % cheie)
            completate = _completeaza_goale(cur, gasit, fields)
            if completate:
                print("    completat pe itemul existent: %s" % ", ".join(completate))
            if collection_id is not None:
                add_to_collection(cur, gasit, collection_id)
            return gasit
    iid = _next_id(cur, "itemID", "items"); t = now()
    cur.execute("INSERT INTO items (itemID,itemTypeID,dateAdded,dateModified,clientDateModified,libraryID,key,version,synced) "
                "VALUES (?,?,?,?,?,?,?,0,0)", (iid, tid, t, t, t, LIBRARY_ID, newkey()))
    for fname, val in (fields or {}).items():
        if val is None or val == "": continue
        fid = field_id(cur, fname)
        if fid is None: raise ValueError(f"unknown field {fname!r}")
        set_field(cur, iid, fid, str(val))
    if creators: _set_creators(cur, iid, creators)
    if collection_id is not None: add_to_collection(cur, iid, collection_id)
    return iid

def attach_pdf(cur, parent_item_id, pdf_path, title=None):
    """Stored-copy PDF attachment: create the attachment item, copy the file to
    storage/<key>/, set linkMode 0 (imported_file), md5 storageHash, storageModTime.

    `title` ajunge in campul `title` al itemului-atasament — implicit numele fisierului.
    Nu e cosmetica: fara el randul-copil apare GOL in Zotero, spre deosebire de atasamentele
    facute de conector („Full Text PDF"), iar repararea ulterioara costa inca un ciclu de
    inchidere-scriere-repornire, adica 2-4 minute pentru un sir de caractere."""
    if not os.path.exists(pdf_path): raise FileNotFoundError(pdf_path)
    tid = item_type_id(cur, "attachment")
    iid = _next_id(cur, "itemID", "items"); key = newkey(); t = now()
    cur.execute("INSERT INTO items (itemID,itemTypeID,dateAdded,dateModified,clientDateModified,libraryID,key,version,synced) "
                "VALUES (?,?,?,?,?,?,?,0,0)", (iid, tid, t, t, t, LIBRARY_ID, key))
    fname = os.path.basename(pdf_path)
    dstdir = os.path.join(STORAGE, key); os.makedirs(dstdir, exist_ok=True)
    shutil.copy2(pdf_path, os.path.join(dstdir, fname))
    with open(pdf_path, "rb") as fh: md5 = hashlib.md5(fh.read()).hexdigest()
    mtime = int(os.path.getmtime(pdf_path) * 1000)
    cur.execute("INSERT INTO itemAttachments (itemID,parentItemID,linkMode,contentType,path,storageModTime,storageHash) "
                "VALUES (?,?,?,?,?,?,?)", (iid, parent_item_id, 0, "application/pdf", "storage:"+fname, mtime, md5))
    set_field(cur, iid, field_id(cur, "title"), title or fname)
    return iid

TIPURI_FISIER = {".pdf": "application/pdf", ".html": "text/html", ".htm": "text/html",
                 ".doc": "application/msword",
                 ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def ataseaza_din_storage(cur, parent_item_id, cheie, nume_fisier, title=None):
    """Leagă un fișier care stă DEJA în `storage/<cheie>/` de un item, sub cheia aceea, fără să-l
    copieze. Întoarce itemID-ul atașamentului.

    Cazul care o cere: o restaurare după pierderea bazei. Fișierele au rămas în `storage`, sub
    cheile vechilor atașamente, iar itemii lor s-au pierdut. `attach_pdf` ar copia fiecare fișier
    sub o cheie nouă, deci storage-ul s-ar dubla, iar folderul vechi ar rămâne orfan. Măsurat pe
    8 octombrie 2026: 975 de foldere fără item după moartea unui laptop. Refuză dacă cheia e deja
    folosită în bază sau dacă fișierul lipsește."""
    cale = os.path.join(STORAGE, cheie, nume_fisier)
    if not os.path.exists(cale):
        raise FileNotFoundError(cale)
    if cur.execute("SELECT 1 FROM items WHERE key=?", (cheie,)).fetchone():
        raise LookupError("cheia %s e deja folosită în bază" % cheie)
    iid = _next_id(cur, "itemID", "items"); t = now()
    cur.execute("INSERT INTO items (itemID,itemTypeID,dateAdded,dateModified,clientDateModified,libraryID,key,version,synced) "
                "VALUES (?,?,?,?,?,?,?,0,0)", (iid, item_type_id(cur, "attachment"), t, t, t, LIBRARY_ID, cheie))
    with open(cale, "rb") as fh:
        md5 = hashlib.md5(fh.read()).hexdigest()
    ext = os.path.splitext(nume_fisier)[1].lower()
    cur.execute("INSERT INTO itemAttachments (itemID,parentItemID,linkMode,contentType,path,storageModTime,storageHash) "
                "VALUES (?,?,?,?,?,?,?)", (iid, parent_item_id, 0, TIPURI_FISIER.get(ext, "application/octet-stream"),
                                          "storage:" + nume_fisier, int(os.path.getmtime(cale) * 1000), md5))
    set_field(cur, iid, field_id(cur, "title"), title or nume_fisier)
    return iid


# ---- child notes (summaries, caches) ----
# Zotero supports notes as children of an ATTACHMENT (not just of the top-level
# bibliographic item) — this is the same mechanism its PDF-reader sidebar uses
# for "add note from annotations". Write PLAIN TEXT; Zotero wraps it in its own
# <div class="zotero-note znv1"> at next startup and stably re-renders as <p>
# paragraphs from then on. Do not write HTML directly — it gets escaped/shown
# as literal tags (see zotero-schema.md "Child note").
def add_child_note(cur, parent_item_id, note_text):
    """Create a new plain-text child note. Returns the new note's itemID."""
    tid = item_type_id(cur, "note")
    iid = _next_id(cur, "itemID", "items"); t = now()
    cur.execute("INSERT INTO items (itemID,itemTypeID,dateAdded,dateModified,clientDateModified,libraryID,key,version,synced) "
                "VALUES (?,?,?,?,?,?,?,0,0)", (iid, tid, t, t, t, LIBRARY_ID, newkey()))
    cur.execute("INSERT INTO itemNotes (itemID,parentItemID,note,title) VALUES (?,?,?,?)",
                (iid, parent_item_id, note_text, ""))
    return iid

def find_child_notes(parent_item_id):
    """[(noteItemID, key, note_text)] for all child notes of a parent (item or
    attachment). Read-only — safe while Zotero is running."""
    return _ro().execute("SELECT itemID, key, note FROM items JOIN itemNotes USING(itemID) "
                          "WHERE parentItemID=?", (parent_item_id,)).fetchall()

def update_note(cur, note_item_id, note_text):
    """Overwrite an existing note's plain-text content in place (no duplicate note)."""
    cur.execute("UPDATE itemNotes SET note=? WHERE itemID=?", (note_text, note_item_id))
    touch(cur, note_item_id)

# ---- highlight-uri (adnotari native) ----
# Inainte de 7 octombrie 2026 nu exista nicio functie care sa scrie in `itemAnnotations`: regula
# si mecanismul erau descrise in SKILL.md si in zotero-schema.md, iar fiecare sesiune scria SQL-ul
# de mana, cu `position` si `sortIndex` reconstruite din memorie. Perechea de mai jos e reteta:
# `gaseste_fragment` calculeaza read-only, cu Zotero deschis; `adauga_evidentiere` scrie, intr-un
# `write_session`, cu Zotero inchis.

def _cuvant_normal(w):
    """Forma pe care se compara cuvintele: NFC, cedila -> virgula, ghilimele drepte, fara
    punctuatia de la capete. Textul fragmentului vine din HTML sau din manuscris, iar cel din PDF
    din fontul lui; diferentele astea nu schimba cuvantul."""
    import unicodedata  # noqa: PLC0415
    w = unicodedata.normalize("NFC", w)
    # Cratima: PyMuPDF o scoate ca U+00AD (soft hyphen) din PDF-urile scrise de `adu_lege.py`,
    # deci „administrativ-jurisdicțională" nu se potrivea cu el însuși (măsurat, 7.10.2026).
    w = w.translate(str.maketrans("şţŞŢ‘’‚“”„­‐‑", "șțȘȚ'''\"\"\"---"))
    return w.strip(".,;:!?()[]\"'«»…").lower()


def _normalizat(s):
    """Ce pune Zotero in `textNormalized` / `commentNormalized`: litere mici, fara diacritice,
    ghilimele drepte (citit din baza, pe adnotarile scrise de Zotero insusi)."""
    import unicodedata  # noqa: PLC0415
    if not s:
        return None
    s = s.translate(str.maketrans("‘’‚“”„", "'''\"\"\""))
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    return s.lower()


def gaseste_fragment(pdf_path, fragment, de_la_cuvantul=0):
    """Unde sta `fragment` in PDF, gata de dat lui `adauga_evidentiere`. Read-only.

    Intoarce o lista de bucati, cate una pe pagina (o adnotare Zotero sta pe o singura pagina,
    deci un fragment care trece pe pagina urmatoare devine doua highlight-uri), fiecare un dict
    cu `pagina` (indexul, de la 0), `dreptunghiuri`, `text`, `top`, `offset`, `eticheta_pagina`.
    [] cand fragmentul nu e in PDF.

    Potrivirea se face pe CUVINTE, in ordinea din PDF, nu pe caractere: un rand rupt, o cratima
    de la capat de rand sau doua spatii nu mai rup potrivirea. `de_la_cuvantul` incepe cautarea
    de la al n-lea cuvant al documentului — cine stie unde e fragmentul (de exemplu `adu_lege.py`,
    care a scris el PDF-ul) evita asa o aparitie identica mai devreme.

    Coordonatele sunt cele din zotero-schema.md: fata de CropBox, cu originea jos (`H - y`), cate
    un dreptunghi pe rand. `top` e distanta de la marginea de sus, iar `offset` pozitia primului
    caracter in textul paginii: amandoua intra in `sortIndex`, care ordoneaza adnotarile."""
    import fitz  # noqa: PLC0415
    tinta = [_cuvant_normal(w) for w in fragment.split()]
    tinta = [w for w in tinta if w] or []
    if not tinta:
        return []
    doc = fitz.open(pdf_path)
    try:
        cuvinte = []                      # (pagina, x0, y0, x1, y1, cuvant, bloc, rand, offset)
        for p in doc:
            off = 0
            for x0, y0, x1, y1, w, b, l, _ in p.get_text("words"):
                cuvinte.append((p.number, x0, y0, x1, y1, w, b, l, off))
                off += len(w) + 1
        norm = [_cuvant_normal(c[5]) for c in cuvinte]
        # Cuvintele care se golesc la normalizare (o paranteza singura) nu se compara.
        idx = [i for i, w in enumerate(norm) if w]
        start = next((k for k, i in enumerate(idx) if i >= de_la_cuvantul), len(idx))
        gasit = None
        for k in range(start, len(idx) - len(tinta) + 1):
            if all(norm[idx[k + j]] == tinta[j] for j in range(len(tinta))):
                gasit = (idx[k], idx[k + len(tinta) - 1])
                break
        if not gasit:
            return []
        bucati = []
        for i in range(gasit[0], gasit[1] + 1):
            c = cuvinte[i]
            if not bucati or bucati[-1]["pagina"] != c[0]:
                pag = doc[c[0]]
                bucati.append({"pagina": c[0], "_randuri": {}, "_cuv": [], "offset": c[8],
                               "_H": pag.rect.height,
                               "eticheta_pagina": pag.get_label() or str(c[0] + 1)})
            b = bucati[-1]
            r = b["_randuri"].setdefault((c[6], c[7]), [c[1], c[2], c[3], c[4]])
            r[0], r[1], r[2], r[3] = min(r[0], c[1]), min(r[1], c[2]), max(r[2], c[3]), max(r[3], c[4])
            b["_cuv"].append(c[5])
        for b in bucati:
            H = b.pop("_H")
            randuri = list(b.pop("_randuri").values())
            b["dreptunghiuri"] = [[round(x0, 3), round(H - y1, 3), round(x1, 3), round(H - y0, 3)]
                                  for x0, y0, x1, y1 in randuri]
            b["top"] = int(min(r[1] for r in randuri))
            b["text"] = " ".join(b.pop("_cuv")).replace("­", "-")
        return bucati
    finally:
        doc.close()


def adauga_evidentiere(cur, cheia_atasamentului, pagina, dreptunghiuri, text, comentariu,
                       culoare=CLAUDE_COLOR, autor="Claude", top=None, offset=0,
                       eticheta_pagina=None):
    """Scrie un highlight nativ pe atasamentul dat. Intoarce itemID-ul adnotarii.

    Se cheama intr-un `write_session`. Argumentele de pozitie vin dintr-o bucata intoarsa de
    `gaseste_fragment`: `adauga_evidentiere(cur, cheie, comentariu="...", **bucata)`.

    **Comentariul e obligatoriu**, fiindca regula din SKILL.md cere ca el sa spuna ce afirma sursa
    acolo si cum e folosita in textul nostru; un highlight fara el nu se poate verifica.

    **O a doua rulare nu dubleaza.** Un highlight cu aceeasi pozitie pe acelasi atasament exista
    deja: se intoarce acela, fara scriere. Altfel o rulare reluata dupa o eroare punea a doua
    oara tot ce reusise prima data."""
    if not (comentariu or "").strip():
        raise ValueError("Highlight fara comentariu: spune ce afirma sursa acolo si cum o folosim.")
    if not dreptunghiuri:
        raise ValueError("Highlight fara dreptunghiuri: fragmentul nu s-a gasit in PDF.")
    r = cur.execute("SELECT itemID FROM items WHERE key=?", (cheia_atasamentului,)).fetchone()
    if not r:
        raise LookupError("Nu exista atasamentul %r." % cheia_atasamentului)
    aid = r[0]
    pozitie = json.dumps({"pageIndex": pagina, "rects": dreptunghiuri})
    deja = cur.execute("SELECT itemID FROM itemAnnotations WHERE parentItemID=? AND position=?",
                       (aid, pozitie)).fetchone()
    if deja:
        return deja[0]
    if top is None:
        top = int(min(d[1] for d in dreptunghiuri))
    tid = item_type_id(cur, "annotation")
    iid = _next_id(cur, "itemID", "items"); t = now()
    cur.execute("INSERT INTO items (itemID,itemTypeID,dateAdded,dateModified,clientDateModified,libraryID,key,version,synced) "
                "VALUES (?,?,?,?,?,?,?,0,0)", (iid, tid, t, t, t, LIBRARY_ID, newkey()))
    cur.execute("INSERT INTO itemAnnotations (itemID,parentItemID,type,authorName,text,comment,color,"
                "pageLabel,sortIndex,position,isExternal,textNormalized,commentNormalized) "
                "VALUES (?,?,1,?,?,?,?,?,?,?,0,?,?)",
                (iid, aid, autor, text, comentariu, culoare,
                 eticheta_pagina or str(pagina + 1),
                 "%05d|%06d|%05d" % (pagina, offset, top), pozitie,
                 _normalizat(text), _normalizat(comentariu)))
    return iid

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "userid"
    if cmd == "find":
        args = sys.argv[2:]
        coll = None
        if "--in" in args:
            i = args.index("--in"); coll = args[i + 1]; args = args[:i] + args[i + 2:]
        try:
            for r in find(args[0] if args else "", collection=coll): print(r)
        except LookupError as e:
            print(e); sys.exit(1)
    elif cmd == "copii":
        # Fara `--muta` doar arata. Cea mai noua ramane si cu `--muta`: e a ultimei scrieri, care
        # poate inca nu e verificata in Zotero. `--toate` o muta si pe ea.
        c = copii_de_siguranta()
        if not c:
            print("Nicio copie `zotero.sqlite.pre-*.bak` langa baza."); sys.exit(0)
        for p, n, t in c:
            print("  %s  %5.0f MB  %s" % (datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M"),
                                         n / 1e6, os.path.basename(p)))
        de_mutat = c if "--toate" in sys.argv else c[1:]
        if "--muta" not in sys.argv:
            print("%d copii, %.1f GB. Cu `--muta` le duc in cos pe toate in afara de cea mai noua "
                  "(%d); cu `--muta --toate`, si pe ea." % (len(c), sum(x[1] for x in c) / 1e9, len(de_mutat)))
            sys.exit(0)
        muta_in_cos([p for p, _, _ in de_mutat])
        print("Mutate in Cosul de reciclare: %d, %.1f GB. Raman: %d."
              % (len(de_mutat), sum(x[1] for x in de_mutat) / 1e9, len(c) - len(de_mutat)))
    elif cmd == "userid":
        print(detect_user_id() or "(nedetectat — setează ZOTERO_USER_ID sau dă --user-id)")
    else:
        # O comanda necunoscuta iesea tacut, cu cod 0. Tacerea aia arata exact ca „am cautat si
        # n-am gasit", deci a fost citita ca „biblioteca n-are itemul" — cand de fapt comanda nu
        # exista. Un esec tacut care seamana cu un raspuns e mai rau decat o eroare.
        print(__doc__.split("CLI:")[1].strip() if "CLI:" in (__doc__ or "") else __doc__)
        print("\nzot.py e in primul rand o BIBLIOTECA de import — `import zot` din Python. "
              "Linia de comanda are doar cele doua comenzi de mai sus.")
        sys.exit(2)
