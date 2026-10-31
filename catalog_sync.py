#!/usr/bin/env python3
"""
Populate the FULL card catalog for a game (not just owned cards) -- needed
as the reference set for live-camera hash matching. One adapter function
per game; adding a future TCG means writing one more adapter, no schema or
scanner changes.

Usage:
  ./venv/bin/python catalog_sync.py pokemon       # one game
  ./venv/bin/python catalog_sync.py --all         # every implemented adapter

Idempotent: INSERT OR IGNORE everywhere, safe to re-run (e.g. after a new set
releases) without touching already-owned cards' prices.
"""
import sqlite3, sys, time
import requests

import collection_core as core

DB = "collection.db"

def sync_pokemon(db, on_progress=lambda msg: print(msg)):
    """Full Pokemon catalog from the free static mirror PokemonTCG/pokemon-tcg-data
    (sets/en.json + cards/en/<set_id>.json via raw.githubusercontent.com) --
    no API key, no rate limit, no per-card calls. Prefer this over the live
    pokemontcg.io API (deprecated, flaky, rate-limited -- see CLAUDE.md)."""
    GAME = "pokemon"
    BASE = "https://raw.githubusercontent.com/PokemonTCG/pokemon-tcg-data/master"
    IMAGE_BASE_FALLBACK = "https://images.scrydex.com/pokemon"

    db.execute("INSERT OR IGNORE INTO games VALUES (?,?)", (GAME, "Pokémon TCG"))
    db.commit()

    sets = _get_json(f"{BASE}/sets/en.json")
    on_progress(f"[{GAME}] fetched {len(sets)} sets")
    for s in sets:
        db.execute("""INSERT OR REPLACE INTO sets (id, game_id, name, series, release_date, printed_total, total)
            VALUES (?,?,?,?,?,?,?)""",
            (f"{GAME}:{s['id']}", GAME, s["name"], s["series"], s["releaseDate"],
             s.get("printedTotal", s.get("total")), s["total"]))
    db.commit()

    total_cards = 0
    for i, s in enumerate(sets, 1):
        set_id = s["id"]
        try:
            set_cards = _get_json(f"{BASE}/cards/en/{set_id}.json")
        except requests.RequestException as e:
            on_progress(f"[{GAME}] {i}/{len(sets)} {set_id}: FAILED ({e!r}), skipping")
            continue
        for c in set_cards:
            image_url = (c.get("images") or {}).get("small") or f"{IMAGE_BASE_FALLBACK}/{c['id']}/small"
            core.add_card(db, f"{GAME}:{c['id']}", GAME, f"{GAME}:{set_id}",
                          c.get("name"), c.get("number"), c.get("rarity"), image_url)
        db.commit()
        total_cards += len(set_cards)
        on_progress(f"[{GAME}] {i}/{len(sets)} {set_id}: +{len(set_cards)} cards (running total {total_cards})")

ADAPTERS = {
    "pokemon": sync_pokemon,
    # "onepiece": sync_onepiece,  # TODO -- optcgapi.com, per ingest_optcg.py's note
    # "gundam": sync_gundam,      # TODO -- gcgapi.com weekly dump, per ingest_gcg.py's note
}

def _get_json(url, attempts=5):
    for attempt in range(attempts):
        r = requests.get(url, timeout=30)
        if r.status_code >= 500:
            time.sleep(min(5 * (attempt + 1), 20)); continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()

def main():
    if len(sys.argv) < 2:
        sys.exit(f"Usage: {sys.argv[0]} <game-id>|--all  (known games: {', '.join(ADAPTERS)})")
    db = sqlite3.connect(DB)
    core.schema(db)

    targets = list(ADAPTERS) if sys.argv[1] == "--all" else [sys.argv[1]]
    for game_id in targets:
        adapter = ADAPTERS.get(game_id)
        if not adapter:
            print(f"No adapter for {game_id!r} -- known: {', '.join(ADAPTERS)}")
            continue
        adapter(db)

if __name__ == "__main__":
    main()
