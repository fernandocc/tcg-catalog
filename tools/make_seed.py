#!/usr/bin/env python3
"""Developer tool: export the card catalog + recognition fingerprints to data/catalog_pokemon.json.gz.

New installs load this file in seconds (Home page -> "Install card data") instead of downloading
~3 GB of card images and hashing them for 30-40 minutes. Re-run after `catalog_sync.py` +
`build_hash_index.py` pick up a new set, then commit the refreshed file.

    ./venv/bin/python tools/make_seed.py
"""
import gzip, json, sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
db = sqlite3.connect(ROOT / "collection.db")
game = "pokemon"
out = {
    "format": 1,
    "game": game,
    "sets": [list(r) for r in db.execute(
        "SELECT id, game_id, name, series, release_date, printed_total, total FROM sets WHERE game_id = ?", (game,))],
    "cards": [list(r) for r in db.execute(
        "SELECT id, game_id, set_id, name, number, rarity, image_url FROM cards WHERE game_id = ?", (game,))],
    "hashes": [list(r) for r in db.execute(
        "SELECT h.card_id, h.phash, h.dhash FROM card_hashes h JOIN cards c ON c.id = h.card_id WHERE c.game_id = ?", (game,))],
}
dest = ROOT / "data" / f"catalog_{game}.json.gz"
dest.parent.mkdir(exist_ok=True)
with gzip.open(dest, "wt", encoding="utf-8", compresslevel=9) as f:
    json.dump(out, f, separators=(",", ":"))
print(f"{dest.relative_to(ROOT)}: {len(out['sets'])} sets, {len(out['cards'])} cards, "
      f"{len(out['hashes'])} fingerprints, {dest.stat().st_size / 1e6:.1f} MB")
