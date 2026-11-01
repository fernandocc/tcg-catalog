"""Current market prices for the cards you own.

    ./venv/bin/python prices.py            # owned cards of every game
    ./venv/bin/python prices.py --all      # every card in the catalog (slower)

Pokemon: TCGplayer market prices from TCGCSV (tcgcsv.com, a free daily mirror of TCGplayer's
price data, no key). TCGCSV groups ("ME05: Pitch Black") don't share ids or exact names with our
sets ("Pitch Black"), so each set is matched to the group, released around the same date, whose
products share the most (card number, name) pairs with our catalog. Prices land in
cards.tcg_market_normal / _holo / _reverse (TCGplayer's Normal / Holofoil / Reverse Holofoil).

One Piece: the catalog itself carries a price per printing (optcgapi.com), so this just re-runs
that catalog sync.
"""
import re
import sqlite3
import sys
import time
import unicodedata
from datetime import datetime

import requests

import collection_core as core

TCGCSV = "https://tcgcsv.com/tcgplayer"
POKEMON_CATEGORY = 3
DATE_WINDOW_DAYS = 75          # how far a TCGCSV group's publish date may sit from our set's release date
MIN_OVERLAP = 0.5              # share of the set's (number, name) pairs a group must contain to count as a match
HEADERS = {"User-Agent": "TCG-Catalog/1.1 (personal collection tracker)"}   # TCGCSV refuses the default python-requests agent
SUBTYPE_COLUMN = {"Normal": "tcg_market_normal", "Holofoil": "tcg_market_holo", "Reverse Holofoil": "tcg_market_reverse",
                  "1st Edition Normal": "tcg_market_normal", "1st Edition Holofoil": "tcg_market_holo",
                  "Unlimited": "tcg_market_normal", "Unlimited Holofoil": "tcg_market_holo"}


def _get(url, attempts=4):
    for k in range(attempts):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.json()["results"]
        except (requests.RequestException, ValueError, KeyError):
            if k == attempts - 1:
                raise
            time.sleep(2 * (k + 1))


def _fold(t):
    """Comparable card name: 'Boss's Orders [Corbeau] - 183/217' and 'Boss’s Orders' -> 'bosssorders'."""
    t = (t or "").replace("♀", " F").replace("♂", " M")
    t = "".join(ch for ch in unicodedata.normalize("NFD", t) if unicodedata.category(ch) != "Mn").lower()
    t = re.sub(r"\(.*?\)|\[.*?\]|\s-\s*[a-z]*\d+(/\S+)?$", "", t)   # (Poke Ball Pattern), [Professor Oak], " - SWSH129"
    return re.sub(r"[^a-z0-9]+", "", t)


def _num(n):
    """'062/198' -> '62', 'SWSH001' -> 'swsh1', 'TG05/TG30' -> 'tg5'."""
    n = (n or "").split("/")[0].strip().lower()
    return re.sub(r"(?<![0-9])0+(?=\d)", "", n)


def _date(s):
    try:
        return datetime.strptime(s[:10].replace("/", "-"), "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _products(group_id):
    """{(number, folded name): [productId, ...]} for one TCGCSV group. Ball-pattern / stamped
    products ('Pikachu (Poke Ball Pattern)') share a number with the plain card; keep them apart."""
    out = {}
    for p in _get(f"{TCGCSV}/{POKEMON_CATEGORY}/{group_id}/products"):
        num = next((e["value"] for e in p.get("extendedData", []) if e["name"] == "Number"), None)
        if not num:
            continue
        plain = "(" not in p["name"]
        out.setdefault((_num(num), _fold(p["name"])), []).append((plain, p["productId"]))
    return out


def update_pokemon(db, owned_only=True, on_progress=print):
    owned = "AND c.id IN (SELECT card_id FROM collection UNION SELECT card_id FROM prior_collection)" if owned_only else ""
    sets = db.execute(f"""SELECT DISTINCT s.id, s.name, s.release_date FROM cards c JOIN sets s ON s.id = c.set_id
                          WHERE c.game_id = 'pokemon' {owned} ORDER BY s.release_date""").fetchall()
    groups = _get(f"{TCGCSV}/{POKEMON_CATEGORY}/groups")
    product_cache = {}
    priced = missing = 0
    unmatched_sets = []
    stamp = time.strftime("%Y-%m-%d")
    for k, (set_id, set_name, release) in enumerate(sets, 1):
        on_progress(f"Prices {k}/{len(sets)}: {set_name}")
        cards = db.execute(f"SELECT c.id, c.number, c.name FROM cards c WHERE c.set_id = ? {owned}", (set_id,)).fetchall()
        everything = {(_num(n), _fold(nm)) for _, n, nm in db.execute("SELECT id, number, name FROM cards WHERE set_id = ?", (set_id,))}
        rel = _date(release)
        if "promo" in set_name.lower():   # promo groups run for years: compare against every promo group
            cands = [g for g in groups if "promo" in g["name"].lower()]
        else:
            cands = [g for g in groups if rel and _date(g["publishedOn"])
                     and abs((_date(g["publishedOn"]) - rel).days) <= DATE_WINDOW_DAYS]
        # several groups can sit near the same date (main set, mini tins, trainer kits): keep the best overlap
        best, best_score = [], 0
        for g in sorted(cands, key=lambda g: _fold(g["name"]).endswith(_fold(set_name)), reverse=True):
            if g["groupId"] not in product_cache:
                product_cache[g["groupId"]] = _products(g["groupId"])
            score = len(everything & product_cache[g["groupId"]].keys()) / max(1, len(everything))
            if score > best_score:
                best, best_score = [g], score
        if best_score < MIN_OVERLAP:
            unmatched_sets.append(set_name)
            missing += len(cards)
            continue
        group = best[0]
        prods = product_cache[group["groupId"]]
        prices = {}
        for p in _get(f"{TCGCSV}/{POKEMON_CATEGORY}/{group['groupId']}/prices"):
            col = SUBTYPE_COLUMN.get(p.get("subTypeName"))
            if col and p.get("marketPrice") is not None:
                prices.setdefault(p["productId"], {})[col] = p["marketPrice"]
        for card_id, number, name in cards:
            ids = sorted(prods.get((_num(number), _fold(name)), []), reverse=True)   # plain product first
            pr = next((prices[pid] for _, pid in ids if pid in prices), None)
            if not pr:
                missing += 1
                continue
            db.execute("""UPDATE cards SET tcg_market_normal = ?, tcg_market_holo = ?, tcg_market_reverse = ?,
                          price_updated = ? WHERE id = ?""",
                       (pr.get("tcg_market_normal"), pr.get("tcg_market_holo"), pr.get("tcg_market_reverse"), stamp, card_id))
            priced += 1
        db.commit()
    return {"priced": priced, "missing": missing, "unmatched_sets": unmatched_sets}


def update_onepiece(db, owned_only=True, on_progress=print):
    import catalog_sync
    if not db.execute("SELECT 1 FROM cards WHERE game_id = 'onepiece' LIMIT 1").fetchone():
        return {"priced": 0, "missing": 0, "unmatched_sets": []}
    on_progress("Prices: One Piece catalog")
    catalog_sync.sync_onepiece(db, on_progress=lambda m: None)
    owned = "AND id IN (SELECT card_id FROM collection UNION SELECT card_id FROM prior_collection)" if owned_only else ""
    n = db.execute(f"SELECT COUNT(*) FROM cards WHERE game_id = 'onepiece' AND tcg_market_normal IS NOT NULL {owned}").fetchone()[0]
    return {"priced": n, "missing": 0, "unmatched_sets": []}


UPDATERS = {"pokemon": update_pokemon, "onepiece": update_onepiece}


def update_all(db, owned_only=True, on_progress=print):
    out = {}
    for game, fn in UPDATERS.items():
        try:
            out[game] = fn(db, owned_only, on_progress)
        except requests.RequestException as e:
            out[game] = {"error": f"couldn't reach the price source ({e.__class__.__name__})"}
    return out


if __name__ == "__main__":
    import appenv
    appenv.enter_data_dir()
    from sort_viewer import _owned_db
    db = _owned_db()
    core.schema(db)
    for game, r in update_all(db, owned_only="--all" not in sys.argv).items():
        print(game, r)
