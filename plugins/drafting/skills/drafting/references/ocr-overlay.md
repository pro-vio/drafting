# OCR overlay for scanned PDFs (ask-first)

Some Zotero attachments are image-only scans (no extractable text) — every read either
extracts nothing or has to go through Claude's vision (feeding page images into context,
token cost every time). Burning a permanent, invisible OCR text layer over the scan fixes
this once: the scan looks identical, but text becomes selectable and extractable from then on.

**This is content-affecting enough to ask first, every time.** It touches the actual library
file, and the cost tradeoff (one-time compute vs. per-read token cost) is the user's call, not
a default. Never run it silently just because a scan was detected.

## When to offer it

Whenever a workflow is about to read a Zotero PDF attachment (citation verification, corpus
analysis, anything that opens the file) and the attachment turns out to be a scan:

```python
import ocr_overlay
is_scan, pages, avg = ocr_overlay.detect_scan(pdf_path)
```

If `is_scan` is `True`, stop and ask before reading further — don't silently fall back to
vision, and don't silently OCR it.

## What to ask

Compute the estimate and present both paths, letting the user pick:

```python
e = ocr_overlay.estimate(pdf_path)
```

Ask something like:

> `<title>` is a scan ({e['pages']} pages, no text layer). I can:
> 1. **Burn a permanent OCR text layer now** — ~{e['seconds_low']}–{e['seconds_high']}s of local
>    processing (RapidOCR, no API cost), one time. Every future read is then plain text, free.
> 2. **Just read it visually this time** — ~{e['vision_tokens_per_read_low']}–{e['vision_tokens_per_read_high']}
>    tokens for this read alone (rough estimate, varies with resolution/density), and the same
>    cost again on every future read since the file stays unchanged.
>
> Which do you want?

The token/time numbers are **rough bands** (`ocr_overlay.SECONDS_PER_PAGE_LOW/HIGH`,
`VISION_TOKENS_PER_PAGE_LOW/HIGH`), calibrated from one real run — don't present them as
precise. Their job is to make the one-time-vs-every-time tradeoff legible, not to be exact.

## If the user agrees

1. Run `overlay_pdf(src, dst)` — it's slow (empirically ~10–20s/page, scales with text
   density) and makes no API calls, so run it via a backgrounded Bash call and check back
   rather than blocking the turn.
2. **Verify before writing anything back**: same page count, extractable text on every page,
   original images still present, and — important — a **0-diff rendered pixmap** on a sample
   page (`page.get_pixmap()` bytes identical before/after) to confirm the overlay is truly
   invisible and didn't alter the scan's appearance.
3. **Replace the attachment file + update the DB in one cycle** (Zotero closed, one
   `write_session`):

```python
import zot, hashlib, os
with zot.write_session("ocr-overlay-<key>") as cur:
    itemID = cur.execute("SELECT itemID FROM items WHERE key=?", (attachment_key,)).fetchone()[0]
    # file swap happens OUTSIDE the DB transaction, but do it inside the same closed window:
    os.replace(new_path, original_path)          # original was already backed up (.pre-ocr.bak)
    with open(original_path, "rb") as f:
        md5 = hashlib.md5(f.read()).hexdigest()
    mtime = int(os.path.getmtime(original_path) * 1000)
    cur.execute("UPDATE itemAttachments SET storageHash=?, storageModTime=? WHERE itemID=?",
                (md5, mtime, itemID))
    zot.touch(cur, itemID)
```

Back up the **original PDF file** (`<file>.pre-ocr.bak`, under `zot.COPII` on D:, not next to the PDF) before swapping — separate from the
`zotero.sqlite` backup `write_session` already takes. Both are needed: the DB backup protects
metadata, the file backup protects the only copy of the original scan bytes.

## If the user declines

Read the scan via vision for this turn only; don't touch the file. Nothing to write back.
