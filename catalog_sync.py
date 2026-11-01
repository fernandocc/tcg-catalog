#!/usr/bin/env python3
"""
Populate the FULL card catalog for a game (not just owned cards) -- needed
as the reference set for live-camera hash matching. One adapter function
per game; adding a future TCG means writing one more adapter, no schema or
scanner changes.

Usage:
  ./venv/bin/python catalog_sync.py pokemon       # one game (or: onepiece)
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

def sync_onepiece(db, on_progress=lambda msg: print(msg)):
    """Full English One Piece catalog from optcgapi.com (free, no key, refreshed daily).
    One catalog card per PRINTING, keyed by the API's image id (OP01-006, OP01-006_p1 parallel,
    OP01-006_r1 reprint, ...) so every printing has its own picture, fingerprint and price.
    `number` is the printed card number (OP01-006) -- shared by all printings of a card; the
    scanner and the sorter's "Printing" picker use that to group look-alikes. A printing is filed
    under the set the API lists it in (a reprint in PRB-01 sorts into PRB-01). Promos and DON!!
    cards get one set each. The API has no release dates, so sets keep the API's own order in
    `series` ("One Piece 01", ...) and release_date stays empty. Prices (market) are refreshed on
    every sync -- unlike Pokemon, they come from the catalog, not from a scan."""
    GAME = "onepiece"
    API = "https://optcgapi.com/api"
    db.execute("INSERT OR IGNORE INTO games VALUES (?,?)", (GAME, "One Piece Card Game"))

    sets = [(s["set_id"], s["set_name"]) for s in _get_json(f"{API}/allSets/")]
    sets += [(d["structure_deck_id"], d["structure_deck_name"]) for d in _get_json(f"{API}/allDecks/")]
    sets += [("P", "Promotion Cards"), ("DON", "DON!! Cards")]
    on_progress(f"[{GAME}] fetched {len(sets)} sets")

    sources = [("allSetCards", None), ("allSTCards", None), ("allPromos", "P"), ("allDonCards", "DON")]
    rows = []
    for i, (endpoint, force_set) in enumerate(sources, 1):
        got = _get_json(f"{API}/{endpoint}/")
        rows += [(c, force_set) for c in got]
        on_progress(f"[{GAME}] {i}/{len(sources)} {endpoint}: {len(got)} printings")

    known_sets = {sid for sid, _ in sets}
    counts, seen, today = {}, set(), time.strftime("%Y-%m-%d")
    for c, force_set in rows:
        image_id, image = c.get("card_image_id"), c.get("card_image")
        if not image_id or image_id in seen:   # a few printings share one image id in the API: keep the first
            continue
        seen.add(image_id)
        set_code = force_set or c.get("set_id")
        if set_code not in known_sets:
            sets.append((set_code, c.get("set_name") or set_code)); known_sets.add(set_code)
        card_id = f"{GAME}:{image_id}"
        number = c.get("card_set_id") or ("DON!!" if force_set == "DON" else image_id)
        core.add_card(db, card_id, GAME, f"{GAME}:{set_code}", c.get("card_name"), number, c.get("rarity"), image)
        price = c.get("market_price") or c.get("inventory_price") or None   # 0.0 = no price known
        db.execute("UPDATE cards SET name = ?, rarity = ?, image_url = COALESCE(?, image_url), tcg_market_normal = ?, price_updated = ? WHERE id = ?",
                   (c.get("card_name"), c.get("rarity"), image, price, c.get("date_scraped") or today, card_id))
        counts[set_code] = counts.get(set_code, 0) + 1
    added = _onepiece_from_tcgcsv(db, sets, counts, on_progress)
    for order, (sid, name) in enumerate(sets, 1):
        db.execute("""INSERT OR REPLACE INTO sets (id, game_id, name, series, release_date, printed_total, total)
            VALUES (?,?,?,?,NULL,?,?)""", (f"{GAME}:{sid}", GAME, name, f"One Piece {order:02d}", counts.get(sid), counts.get(sid)))
    db.commit()
    on_progress(f"[{GAME}] {len(seen) + added} printings in {len(sets)} sets")


def _onepiece_from_tcgcsv(db, sets, counts, on_progress):
    """Top up One Piece with card numbers optcgapi doesn't list yet (it lags new releases by days or
    weeks: EB-05 was on TCGplayer the day it came out). Source: TCGCSV, TCGplayer's catalog mirror
    (category 68). The plain product of a number becomes the base printing `onepiece:<number>` --
    the same id optcgapi will use, so it simply takes over later; other products (parallels, alt
    art...) get `onepiece:<number>_tcg<productId>`, and once optcgapi covers that number (with its own
    _p1.. ids) those stand-ins are dropped unless you own one. Returns the number of printings added."""
    import re
    import prices
    GAME = "onepiece"
    have = {n for (n,) in db.execute("SELECT number FROM cards WHERE game_id = ? AND id NOT LIKE '%\\_tcg%' ESCAPE '\\'", (GAME,))}
    owned = "SELECT card_id FROM collection UNION SELECT card_id FROM prior_collection" \
        if db.execute("SELECT 1 FROM sqlite_master WHERE name = 'prior_collection'").fetchone() else "SELECT card_id FROM collection"
    stale = [i for (i,) in db.execute(f"""SELECT id FROM cards WHERE game_id = ? AND id LIKE '%\\_tcg%' ESCAPE '\\'
                                          AND id NOT IN ({owned})""", (GAME,)) if i.split(":", 1)[1].split("_tcg")[0] in have]
    for i in stale:
        db.execute("DELETE FROM card_hashes WHERE card_id = ?", (i,))
        db.execute("DELETE FROM cards WHERE id = ?", (i,))
    try:
        groups = prices._get(f"{prices.TCGCSV}/68/groups")
    except Exception as e:
        on_progress(f"[{GAME}] TCGCSV unreachable, skipping new-release top-up ({e.__class__.__name__})")
        return 0
    known = {sid for sid, _ in sets}
    today, added = time.strftime("%Y-%m-%d"), 0
    for g in sorted(groups, key=lambda g: g["publishedOn"]):
        try:
            products = prices._get(f"{prices.TCGCSV}/68/{g['groupId']}/products")
        except Exception:
            continue
        by_num = {}
        for pr in products:
            ext = {e["name"]: e["value"] for e in pr.get("extendedData", [])}
            num = ext.get("Number", "")
            if re.fullmatch(r"[A-Z]+\d*-\d+", num) and num not in have:
                by_num.setdefault(num, []).append((pr, ext))
        if not by_num:
            continue
        if "promotion" in g["name"].lower():
            set_code = "P"
        else:
            abbr = (g.get("abbreviation") or "").split()[0] if g.get("abbreviation") else ""
            set_code = re.sub(r"^([A-Z]+)(\d+)$", r"\1-\2", abbr) or str(g["groupId"])
        if set_code not in known:
            sets.append((set_code, g["name"])); known.add(set_code)
        try:
            market = {p["productId"]: p["marketPrice"] for p in prices._get(f"{prices.TCGCSV}/68/{g['groupId']}/prices") if p.get("marketPrice")}
        except Exception:
            market = {}
        for num, items in by_num.items():
            items.sort(key=lambda t: ("(" in t[0]["name"], t[0]["productId"]))   # plain name first = base printing
            for k, (pr, ext) in enumerate(items):
                card_id = f"{GAME}:{num}" if k == 0 else f"{GAME}:{num}_tcg{pr['productId']}"
                image = (pr.get("imageUrl") or "").replace("_200w.", "_400w.") or None
                core.add_card(db, card_id, GAME, f"{GAME}:{set_code}", pr["name"], num, ext.get("Rarity"), image)
                db.execute("UPDATE cards SET tcg_market_normal = ?, price_updated = ? WHERE id = ?",
                           (market.get(pr["productId"]), today, card_id))
                counts[set_code] = counts.get(set_code, 0) + 1
                added += 1
            have.add(num)
        on_progress(f"[{GAME}] TCGCSV {g['name']}: +{sum(len(v) for v in by_num.values())} printings optcgapi doesn't list yet")
    return added

ADAPTERS = {
    "pokemon": sync_pokemon,
    "onepiece": sync_onepiece,
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
