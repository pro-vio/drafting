# -*- coding: utf-8 -*-
"""
ocr_overlay.py — detect scanned PDFs and burn a searchable, invisible OCR text
layer over the existing page images (RapidOCR + PyMuPDF, no ocrmypdf/tesseract
needed). The scan's visual appearance is untouched; only text becomes selectable
and extractable.

Ask-first workflow (see SKILL.md "Optional: OCR overlay for scanned PDFs"):
  1. detect_scan(path)   -> is it actually a scan? (cheap, no OCR run yet)
  2. estimate(path)      -> rough time + token-tradeoff numbers to show the user
  3. user says yes        -> overlay_pdf(src, dst) in the background
  4. replace the file in Zotero storage + update storageHash/storageModTime
     in ONE write_session (see references/zotero-schema.md "Replace an
     attachment's file"), Zotero closed.

CLI:
  python ocr_overlay.py detect <pdf>
  python ocr_overlay.py estimate <pdf>
  python ocr_overlay.py run <src.pdf> <dst.pdf>
"""
try: import sys; sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
import time
import fitz

ZOOM = 3.0  # ~216 dpi render for OCR accuracy
CHAR_THRESHOLD = 30  # avg extractable chars/page below this = scan, not text-native

# Empirical, from one real run (17-page journal article, mixed text density):
# 329s total, page times ranged 4.6s (sparse title page) to 12.3s+ (dense body text).
# Treat as a rough band, not a precise model — density varies a lot by document.
SECONDS_PER_PAGE_LOW, SECONDS_PER_PAGE_HIGH = 10, 20

# Rough, hedged band for what a vision-based read of one scanned page costs in
# API tokens (varies with render resolution and page density) — used only to
# frame the one-time-OCR vs read-with-vision-every-time tradeoff for the user.
VISION_TOKENS_PER_PAGE_LOW, VISION_TOKENS_PER_PAGE_HIGH = 1500, 3000


def detect_scan(path, sample_pages=5, char_threshold=CHAR_THRESHOLD):
    """Cheap check, no OCR: (is_scan, page_count, avg_chars_sampled).
    Samples the first `sample_pages` pages — good enough to flag a scan;
    for the final verdict before spending OCR time, check every page (see CLI)."""
    doc = fitz.open(path)
    n = len(doc)
    sample = list(range(min(n, sample_pages)))
    chars = sum(len(doc[p].get_text().strip()) for p in sample)
    avg = chars / max(1, len(sample))
    doc.close()
    return avg < char_threshold, n, avg


def estimate(path):
    """Rough time + token-tradeoff numbers to present to the user before OCR'ing."""
    is_scan, n, avg = detect_scan(path)
    return {
        "is_scan": is_scan, "pages": n, "avg_chars_sampled": avg,
        "seconds_low": n * SECONDS_PER_PAGE_LOW, "seconds_high": n * SECONDS_PER_PAGE_HIGH,
        "vision_tokens_per_read_low": n * VISION_TOKENS_PER_PAGE_LOW,
        "vision_tokens_per_read_high": n * VISION_TOKENS_PER_PAGE_HIGH,
    }


def overlay_pdf(src_path, dst_path, min_conf=0.5, zoom=ZOOM):
    """Render each page -> RapidOCR -> insert invisible text (render_mode=3) at
    each detected word's position, scaled back to PDF point space. Original
    page content (images) is untouched; only a hidden, searchable text layer
    is added. Tested: 0-pixel rendered diff vs. the original."""
    from rapidocr_onnxruntime import RapidOCR
    import numpy as np
    ocr = RapidOCR()
    doc = fitz.open(src_path)
    t0 = time.time()
    total_words = 0
    for pno in range(len(doc)):
        page = doc[pno]
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csRGB)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        result, _ = ocr(img)
        if not result:
            continue
        for box, text, conf in result:
            if float(conf) < min_conf or not text.strip():
                continue
            xs = [p[0] for p in box]; ys = [p[1] for p in box]
            x0, x1 = min(xs) / zoom, max(xs) / zoom
            y0, y1 = min(ys) / zoom, max(ys) / zoom
            h = y1 - y0
            if h <= 0:
                continue
            try:
                page.insert_text((x0, y1 - h * 0.15), text, fontsize=h * 0.85,
                                  fontname="helv", render_mode=3)
            except Exception:
                pass
            total_words += 1
    doc.save(dst_path, garbage=3, deflate=True)
    doc.close()
    return total_words, time.time() - t0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else None
    if cmd == "detect":
        is_scan, n, avg = detect_scan(sys.argv[2])
        print(f"is_scan={is_scan}  pages={n}  avg_chars_sampled={avg:.0f}")
    elif cmd == "estimate":
        e = estimate(sys.argv[2])
        print(e)
    elif cmd == "run":
        n, secs = overlay_pdf(sys.argv[2], sys.argv[3])
        print(f"OK: {n} text blocks inserted in {secs:.1f}s -> {sys.argv[3]}")
    else:
        print(__doc__)
