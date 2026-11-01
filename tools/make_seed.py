#!/usr/bin/env python3
"""Developer tool: export each game's card catalog + recognition fingerprints to data/catalog_<game>.json.gz.

New installs load these files in seconds (the app does it on first start) instead of downloading
gigabytes of card images and hashing them for half an hour. Re-run after `catalog_sync.py <game>` +
`build_hash_index.py` pick up a new set, then commit the refreshed file(s).

    ./venv/bin/python tools/make_seed.py              # every game in the database
    ./venv/bin/python tools/make_seed.py onepiece     # just one
    ./venv/bin/python tools/make_seed.py --db other.db onepiece
"""
import gzip, json, sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
args = sys.argv[1:]
src = ROOT / "collection.db"
if args[:1] == ["--db"]:
    src, args = Path(args[1]), args[2:]
db = sqlite3.connect(src)
games = args or [g for (g,) in db.execute(
    "SELECT DISTINCT c.game_id FROM card_hashes h JOIN cards c ON c.id = h.card_id")]
for game in games:
    name = db.execute("SELECT name FROM games WHERE id = ?", (game,)).fetchone()
    # Pokemon prices come from scans (your own copies), so only catalog-priced games ship them
    price = "c.tcg_market_normal, c.price_updated" if game != "pokemon" else "NULL, NULL"
    out = {
        "format": 2,
        "game": game,
        "game_name": name[0] if name else game,
        "sets": [list(r) for r in db.execute(
            "SELECT id, game_id, name, series, release_date, printed_total, total FROM sets WHERE game_id = ?", (game,))],
        "cards": [list(r) for r in db.execute(
            f"SELECT c.id, c.game_id, c.set_id, c.name, c.number, c.rarity, c.image_url, {price} FROM cards c WHERE c.game_id = ?", (game,))],
        "hashes": [list(r) for r in db.execute(
            "SELECT h.card_id, h.phash, h.dhash FROM card_hashes h JOIN cards c ON c.id = h.card_id WHERE c.game_id = ?", (game,))],
    }
    dest = ROOT / "data" / f"catalog_{game}.json.gz"
    dest.parent.mkdir(exist_ok=True)
    with gzip.open(dest, "wt", encoding="utf-8", compresslevel=9) as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"{dest.relative_to(ROOT)}: {len(out['sets'])} sets, {len(out['cards'])} cards, "
          f"{len(out['hashes'])} fingerprints, {dest.stat().st_size / 1e6:.1f} MB")
