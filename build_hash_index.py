#!/usr/bin/env python3
"""
For every card in the catalog (any game, populated by catalog_sync.py) that
doesn't yet have a reference hash, download its image and compute pHash +
dHash (hash_size=16 -- see CLAUDE.md's "Fallback if the app route fails"
decision) for live-camera matching in live_scanner.py.

Fully resumable: images are cached to image_cache/<card_id>.jpg (skipped if
already present), and card_hashes is committed every ~50 rows, so Ctrl-C
loses at most a few rows of progress, not the whole run. This is a one-time,
network- and CPU-bound job for ~20k cards -- expect it to take a while; safe
to run in the background and resume later.

Usage:
  ./venv/bin/python build_hash_index.py
"""
import sqlite3, sys, time
from pathlib import Path

import imagehash
import requests
from PIL import Image

import collection_core as core

DB = "collection.db"
IMAGE_CACHE = Path("image_cache")
HASH_SIZE = 16
COMMIT_EVERY = 50

FALLBACK_IMAGE = "https://images.scrydex.com/pokemon/{}/small"   # second source when the catalog's link is dead
MISSING = IMAGE_CACHE / "missing.txt"                             # cards with no image anywhere: don't retry every run


def download(url, dest, attempts=3):
    for attempt in range(attempts):
        try:
            r = requests.get(url, timeout=20)
            if r.status_code == 404:
                return False                  # gone for good -- retrying won't help
            r.raise_for_status()
            dest.write_bytes(r.content)
            return True
        except requests.RequestException as e:
            if attempt == attempts - 1:
                print(f"  download failed ({e!r}): {url}")
                return False
            time.sleep(2 * (attempt + 1))


def fetch_image(card_id, image_url, dest):
    """Catalog image link first, then the scrydex CDN (same <set>-<number> naming)."""
    if image_url and download(image_url, dest):
        return True
    raw_id = card_id.split(":", 1)[-1]
    return download(FALLBACK_IMAGE.format(raw_id), dest)


def build(db, on_progress=lambda done, total, msg: None, should_stop=lambda: False):
    """Hash every catalog card that has no fingerprint yet. Returns the number hashed.
    on_progress(done, total, message) is called as it goes (used by the app's setup page)."""
    IMAGE_CACHE.mkdir(exist_ok=True)
    missing = set(MISSING.read_text().split()) if MISSING.exists() else set()
    rows = [r for r in db.execute("""
        SELECT c.id, c.image_url FROM cards c
        LEFT JOIN card_hashes h ON h.card_id = c.id
        WHERE h.card_id IS NULL""").fetchall() if r[0] not in missing]
    total = len(rows)
    on_progress(0, total, f"{total} cards need fingerprints")
    done = 0
    for i, (card_id, image_url) in enumerate(rows, 1):
        if should_stop():
            break
        safe_name = card_id.replace(":", "_").replace("/", "_")
        img_path = IMAGE_CACHE / f"{safe_name}.jpg"
        if not img_path.exists():
            if not fetch_image(card_id, image_url, img_path):
                with open(MISSING, "a") as f:
                    f.write(card_id + "\n")
                on_progress(i, total, f"no image anywhere for {card_id} (skipped)")
                continue
        try:
            img = Image.open(img_path)
            phash = imagehash.phash(img, hash_size=HASH_SIZE)
            dhash = imagehash.dhash(img, hash_size=HASH_SIZE)
        except Exception as e:
            print(f"  hash failed for {card_id} ({e!r}), removing cached file")
            img_path.unlink(missing_ok=True)
            on_progress(i, total, f"couldn't read the image for {card_id} (skipped)")
            continue
        db.execute("INSERT OR REPLACE INTO card_hashes VALUES (?,?,?)",
                   (card_id, str(phash), str(dhash)))
        done += 1
        if done % COMMIT_EVERY == 0:
            db.commit()
        on_progress(i, total, card_id)
    db.commit()
    return done


def main():
    db = sqlite3.connect(DB)
    core.schema(db)
    last = [0.0]
    def progress(i, total, msg):
        if time.time() - last[0] > 2 or i == total:
            last[0] = time.time()
            print(f"{i}/{total} {msg}")
    done = build(db, progress)
    print(f"Done: {done} cards hashed this run.")

if __name__ == "__main__":
    main()
