# Ordinea căutării: cache, bibliotecă, internet

Când cauți o afirmație, o cifră sau un act, ordinea e fixă. Fiecare treaptă se trece **numai**
după ce cea dinainte a întors zero — iar „zero" e o cifră tipărită, nu o impresie.

Regula există fiindcă ordinea inversă s-a măsurat: pe 11 septembrie 2026, textul unui act era
memoizat, s-a deschis, primele pagini erau despre altceva, de acolo a ieșit concluzia că actul nu
conține ce se caută, iar răspunsul a fost găsit pe internet și confirmat apoi în cache, unde
stătea de la început. **Într-un text de două milioane de caractere, o citire de trei pagini nu
spune nimic despre restul.**

## Treapta 1 — textele memoizate

```bash
python scripts/cauta_in_cache.py "șirul căutat"
python scripts/cauta_in_cache.py "șirul" --colectie "proiect/subcolecție" --regex
```

**Ce caută:** textul extras din PDF-uri, pentru atașamentele memoizate. Diacriticele se ignoră
implicit.

**Ce întoarce când nu găsește nimic** — și aici e condiția de trecere: câte texte a căutat, câte
atașamente **n-au** text memoizat, și primele zece dintre ele, cu titlu și cheie.

**Cum se trece mai departe.** Nu se trece dacă lista de atașamente fără text conține sursa în care
ar fi trebuit să cauți. Atunci se memoizează întâi, cu `prefetch_collection.py`, într-un singur
ciclu de scriere. Măsurat pe biblioteca de la 12 septembrie 2026: **827 de texte memoizate față de
2.107 atașamente fără text** — deci un rezultat gol e, cel mai adesea, un cache nepopulat.

## Treapta 2 — biblioteca, fără textul PDF-urilor

Două comenzi, fiindcă sunt două feluri de obiecte.

```python
zot.find("șir")                  # câmpurile itemelor + numele autorilor
zot.cauta_note("șir")            # note, evidențieri, comentarii, etichete
```

**`zot.find` caută în TOATE câmpurile**, nu doar în titlu: denumire de act, editură, an, DOI,
`extra`, orice — plus numele de familie ale autorilor. Prima linie a descrierii ei spunea
„title/nameOfAct/creator", iar o sesiune a citit-o și a tras concluzia că nu se poate căuta după
conținutul unui câmp. Linia aia e acum adevărul, nu un rezumat.

**`zot.cauta_note` caută unde `find` nu ajunge:** notele (1.028 în bibliotecă), evidențierile și
comentariile scrise de om în PDF-uri, și etichetele (3.255). Întoarce felul fiecărei potriviri —
`nota`, `evidentiere`, `comentariu`, `eticheta` —, itemul-gazdă și bucata de text. Notele de cache
sunt sărite, altfel orice căutare le-ar întoarce pe toate.

**Ce întoarce când nu găsește:** listă goală, amândouă.

**Cum se trece mai departe.** Abia după ce **amândouă** întorc gol. `find` singur nu ajunge: o
adnotare a autorului pe marginea unui PDF e exact felul de răspuns care nu apare în niciun câmp.

## Treapta 3 — internetul

N-are comandă, și de aceea e ultima: nimic nu te oprește s-o iei de aici. Condiția de intrare e
compusă, și fiecare bucată din ea e o cifră sau o listă goală de mai sus:

1. treapta 1 a întors zero potriviri;
2. lista ei de atașamente fără text nu conține sursa în care ar fi trebuit să cauți;
3. `find` și `cauta_note` au întors amândouă gol.

Dacă a doua nu ține, nu te duci pe internet: memoizezi și reiei treapta 1.

## Ce nu acoperă niciuna dintre cele trei

Se scrie aici ca să nu fie descoperit a doua oară:

- **textul PDF-urilor nememoizate** — acolo nu caută nimeni; treapta 1 le numește, dar nu le
  citește;
- **fișierele care nu sunt PDF** — atașamentele de alt fel n-au cache de text;
- **conținutul atașamentelor legate al căror fișier lipsește de pe disc** — nota poate exista, dar
  prospețimea nu se poate verifica, iar căutarea le raportează cu avertisment;
- **notele desprinse de Zotero**, ale căror surse nu se mai pot numi: rămân căutabile la treapta 1,
  dar se raportează fără titlu.
