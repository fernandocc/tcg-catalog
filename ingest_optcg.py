#!/usr/bin/env python3
"""
STUB — not implemented. Tabled until the Pokémon ingest path is proven
and the scan source for One Piece cards is confirmed (unknown whether
Poke Delver / Card Slinger recognize this game).

Plan: mirror ingest_delver.py's shape once a scan source exists.
  - Data source: https://optcgapi.com/ (free, no key, daily refresh)
  - DB: separate file (e.g. collection_optcg.db) or a `game` column added
    to the shared schema — decide when building this out.
"""
API = "https://optcgapi.com"  # TODO: confirm exact base path from docs before use

if __name__ == "__main__":
    raise SystemExit("ingest_optcg.py is a stub — not implemented yet")
