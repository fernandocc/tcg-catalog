#!/usr/bin/env python3
"""
Point a camera at a card and it gets identified and added to the collection
in real time -- no photo-taking step. Works across every game that's been
through catalog_sync.py + build_hash_index.py at once (no game-selection
step needed: whichever card_id's hash is the closest match wins, and its
game_id comes along for free).

Usage:
  ./venv/bin/python live_scanner.py 0                               # USB webcam, device index 0
  ./venv/bin/python live_scanner.py "http://<phone-ip>:8080/video"  # phone IP-camera stream, same code path

Press 'q' in the preview window to quit.

Pipeline per frame: detect the card's quadrilateral by contour, perspective-
warp it flat, hash it (pHash+dHash, hash_size=16 -- matching build_hash_index.py),
match against every reference hash loaded at startup, and only commit once the
same card is the best match for several consecutive frames (debounced so
holding one card steady doesn't add it repeatedly).

Variant is always committed as "normal" -- hashing the card front can't
reliably tell foil/holo apart. Hand-flag it afterward in sort_viewer.html,
exactly like Delver-sourced scans already require.
"""
import json
import os
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import cv2
import numpy as np
from PIL import Image

import collection_core as core
from build_hash_index import MASKED_GAMES, fingerprint

LIVE_FRAME_PATH = Path("live_frame.jpg")
LIVE_CAM_PATH = Path("live_cam.jpg")   # camera view only (with the detector outline) for the web pages
LIVE_STATUS_PATH = Path("live_status.json")
LIVE_FRAME_INTERVAL = 0.15  # seconds -- throttles disk writes for the web preview, not the scan loop itself
# Persists the last-committed card_id across restarts -- debounce state used to
# live only in memory, so restarting the script while the same physical card
# was still in view would re-scan and double-count it (caught live 2026-10-05).
LAST_COMMITTED_PATH = Path("live_last_committed.json")

DB = "collection.db"
HASH_SIZE = 16
N_BITS = HASH_SIZE * HASH_SIZE  # phash/dhash at hash_size=16 are 256-bit hashes, NOT 64-bit
CARD_ASPECT = 63 / 88  # width:height of a standard TCG card
# Tight on purpose: live testing found a loose tolerance (0.25) let a malformed
# contour (card merged with an adjacent shadow/edge by the dilate step, aspect
# ~0.48) outscore the true card contour (aspect ~0.74, close to the 0.716
# target) purely on area, producing a badly-cropped warp that didn't hash-match
# even its own correct reference image at all.
ASPECT_TOLERANCE = 0.08
WELL_ASPECT_TOLERANCE = 0.10  # looser: the bright-blob mask is clean, so aspect is a sanity check only
REARM_MISS_FRAMES = 30  # consecutive no-match frames before the same card may commit again
MIN_AREA_FRACTION = 0.06  # detected card must cover at least this fraction of the frame -- raised
# from 0.05 after live testing showed the card's inner artwork box (which also
# happens to be roughly card-shaped) was winning over the full card outline,
# since its printed border has cleaner contrast than the card's own edge
# against a light background. The full card should dominate the frame by far
# more than the artwork sub-box does, so this filters the sub-box out.
# Combined phash+dhash Hamming distance (out of 2*N_BITS=512 bits). Real-camera
# tested 2026-10-05 (Card Slinger, dark background, IP Webcam phone stream):
# the correct card scored 132 against its true reference, while every wrong
# rotation/candidate in that same test scored 192+. 160 leaves margin above
# the real true-match score while staying well below the wrong-match floor.
# Torch assist: when a card shape is detected but nothing matches for a while, pulse the phone's
# flashlight on/off (a quick flash often gets a card recognised that wasn't straight away). Only
# works with the phone_cam_server.py source (port 8081); set TORCH_PULSE=0 in the environment to disable.
TORCH_PULSE = os.environ.get("TORCH_PULSE", "1") != "0"
TORCH_AFTER_FRAMES = 10      # consecutive detected-but-unmatched frames before pulsing
TORCH_PULSE_SECONDS = 0.9    # how long the torch stays on (also turns off the moment a match is found)
TORCH_COOLDOWN = 5.0         # minimum seconds between pulses (heat, and don't strobe)
MATCH_DISTANCE_THRESHOLD = 160
# Best-of-frames voting (foil/glare tolerance): glare shifts frame to frame, so a
# foil that misses on most frames still matches cleanly on some. Each frame's
# top-1 candidate casts a vote (even above-threshold ones); a card commits once it
# wins BEST_MIN_VOTES of the last BEST_WINDOW frames AND its best distance in that
# window is within MATCH_DISTANCE_THRESHOLD.
BEST_WINDOW = 12
BEST_MIN_VOTES = 5
STABLE_FRAMES = 8  # consecutive frames the same card must win before committing
WARP_SIZE = (245, 342)  # matches the reference images' own aspect/size
# Games whose catalog has one card per PRINTING (One Piece: base, reprint, Jolly Roger foil, parallel...).
# Printings of the same card number whose pictures are this close are the same artwork -- the camera
# can't tell them apart, so votes would split between them. They're folded into one candidate (the
# base printing: no _suffix in its id) and the right printing is picked in the sorter afterwards.
LOOKALIKE_GAMES = ("onepiece",)
LOOKALIKE_DISTANCE = 100


def _hex_to_bits(hex_str):
    """Decode a hex-encoded hash string into a flat uint8 0/1 bit array.
    Both sides of every comparison go through this same function (reference
    hashes stored as hex in card_hashes, and live query hashes re-encoded to
    hex before decoding too) so bit ordering is always self-consistent,
    regardless of imagehash's internal bit-packing details."""
    raw = bytes.fromhex(hex_str)
    return np.unpackbits(np.frombuffer(raw, dtype=np.uint8))[:N_BITS]


def load_hash_index(db):
    rows = db.execute("""
        SELECT h.card_id, h.phash, h.dhash, c.name, s.name, g.name, c.game_id, c.number
        FROM card_hashes h
        JOIN cards c ON c.id = h.card_id
        LEFT JOIN sets s ON s.id = c.set_id
        LEFT JOIN games g ON g.id = c.game_id""").fetchall()
    if not rows:
        sys.exit("No reference hashes found -- run build_hash_index.py first.")
    card_ids, phashes, dhashes, names, sets, games, groups = [], [], [], [], [], [], {}
    for k, (card_id, phash_hex, dhash_hex, name, set_name, game_name, game_id, number) in enumerate(rows):
        if game_id in LOOKALIKE_GAMES and number:
            groups.setdefault((game_id, number), []).append(k)
        card_ids.append(card_id)
        phashes.append(_hex_to_bits(phash_hex))
        dhashes.append(_hex_to_bits(dhash_hex))
        names.append(name)
        sets.append(set_name)
        games.append(game_name)
    index = {
        "card_ids": card_ids,
        "phashes": np.array(phashes, dtype=np.uint8),
        "dhashes": np.array(dhashes, dtype=np.uint8),
        "names": names,
        "sets": sets,
        "games": games,
    }
    index["canon"] = _lookalike_canon(index, groups)
    # rows fingerprinted with the watermark band masked (see build_hash_index.MASKED_GAMES)
    index["masked"] = np.flatnonzero([cid.split(":", 1)[0] in MASKED_GAMES for cid in card_ids])
    return index


def _lookalike_canon(index, groups):
    """canon[i] = the index that candidate i votes as (itself, unless it's a look-alike printing)."""
    canon = np.arange(len(index["card_ids"]))
    for members in groups.values():
        if len(members) < 2:
            continue
        # base printing first (OP01-006 before OP01-006_p1 / _r1), then by id
        members = sorted(members, key=lambda k: ("_" in index["card_ids"][k], index["card_ids"][k]))
        for pos, k in enumerate(members):
            if canon[k] != k:
                continue
            for j in members[pos + 1:]:
                if canon[j] == j:
                    d = int(np.sum(index["phashes"][k] != index["phashes"][j]) + np.sum(index["dhashes"][k] != index["dhashes"][j]))
                    if d <= LOOKALIKE_DISTANCE:
                        canon[j] = k
    return canon


def hamming_distance(bits_matrix, query_bits):
    """Vectorized Hamming distance of one query bit-vector against every row."""
    return np.sum(bits_matrix != query_bits, axis=1)


def _distances(index, img, masked, rows=slice(None)):
    phash_hex, dhash_hex = fingerprint(img, masked)
    return (hamming_distance(index["phashes"][rows], _hex_to_bits(phash_hex))
            + hamming_distance(index["dhashes"][rows], _hex_to_bits(dhash_hex)))


def best_match(index, img):
    """Best catalog candidate for an upright card picture: (index row, combined distance).
    Rows of watermark-masked games are compared against the masked picture."""
    combined = _distances(index, img, False)
    rows = index["masked"]
    if len(rows):
        combined[rows] = _distances(index, img, True, rows)
    i = int(np.argmin(combined))
    return int(index["canon"][i]), int(combined[i])


def order_corners(pts):
    pts = pts.reshape(4, 2).astype("float32")
    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).flatten()
    tl, br = pts[np.argmin(s)], pts[np.argmax(s)]
    tr, bl = pts[np.argmin(diff)], pts[np.argmax(diff)]
    return np.array([tl, tr, br, bl], dtype="float32")


_last_well = None
_well_misses = 0


def _find_well_raw(frame):
    """Largest dark region that doesn't touch the frame border, as (x, y, w, h).
    Tries several darkness thresholds and keeps the biggest result, because the
    phone's auto-exposure shifts how dark the well looks (a bright card makes it
    meter the well brighter, which used to shrink the match to a thin shadow
    strip on one wall). Works on a 1/4-scale copy."""
    small = cv2.resize(frame, None, fx=0.25, fy=0.25)
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    H, W = gray.shape
    best, best_area = None, 0
    for thr in (50, 65, 80):
        dark = (gray < thr).astype(np.uint8) * 255
        dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        contours, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            # Dark background outside the Slinger touches 2+ frame edges; the well
            # itself may run off ONE edge when the phone sits close/low, so allow that.
            touching = (x <= 1) + (y <= 1) + (x + w >= W - 1) + (y + h >= H - 1)
            if touching >= 2:
                continue
            if w * h < 0.04 * W * H or w * h <= best_area:
                continue
            if cv2.contourArea(c) < 0.55 * w * h:  # the well is a solid box, not a ragged shadow
                continue
            best, best_area = (x * 4, y * 4, w * 4, h * 4), w * h
    return best


WELL_MISS_LIMIT = 15      # frames to keep trusting the last well before declaring it lost
well_missing_run = 0      # consecutive frames with no well found (drives the "framing" warning in live_status.json)


def find_well(frame):
    """Sticky wrapper: a result much smaller than the last good well is treated as a glitch and
    ignored for up to 30 frames (so a lighting flicker can't shrink the search area). If NO well
    is found, the last one is only reused for WELL_MISS_LIMIT frames -- after that it is dropped,
    so the preview shows the whole camera image (and warns) instead of a stale zoomed-in crop."""
    global _last_well, _well_misses, well_missing_run
    new = _find_well_raw(frame)
    if new is None:
        well_missing_run += 1
        if well_missing_run > WELL_MISS_LIMIT:
            _last_well = None
        return _last_well
    well_missing_run = 0
    if _last_well is not None and new[2] * new[3] < 0.6 * _last_well[2] * _last_well[3] and _well_misses < 30:
        _well_misses += 1
        return _last_well
    _last_well, _well_misses = new, 0
    return new


def detect_card(frame):
    """Find the card inside the dark well: crop to the well, threshold the bright
    card against the dark interior, take the largest card-shaped blob. Falls back
    to the older whole-frame edge detector if no well is found."""
    well = find_well(frame)
    if well is None:
        return detect_card_edges(frame), None
    x, y, w, h = well
    m = int(0.03 * min(w, h))  # stay inside the well's own walls
    ox, oy = x + m, y + m
    crop = frame[oy:y + h - m, ox:x + w - m]
    gray = cv2.GaussianBlur(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    # Card = clearly brighter than the box (threshold relative to the box's own
    # brightness, sampled at the crop's edge strip) OR colorful: the box is neutral
    # black/gray, so saturated pixels catch dark card art that brightness alone
    # misses (Otsu split dark art from the box and broke the card blob apart).
    k = max(4, int(0.04 * min(gray.shape)))
    edge = np.concatenate([gray[:k].ravel(), gray[-k:].ravel(), gray[:, :k].ravel(), gray[:, -k:].ravel()])
    base = float(np.median(edge))
    sat = cv2.cvtColor(cv2.GaussianBlur(crop, (5, 5), 0), cv2.COLOR_BGR2HSV)[:, :, 1]
    mask = (((gray > base + 40) | ((sat > 70) & (gray > base + 8))) * 255).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best, best_area = None, 0
    for c in contours:
        area = cv2.contourArea(c)
        if area < 0.2 * crop.shape[0] * crop.shape[1] or area <= best_area:
            continue
        rect = cv2.minAreaRect(c)
        (_, _), (rw, rh), _ = rect
        if rw == 0 or rh == 0:
            continue
        if abs(min(rw, rh) / max(rw, rh) - CARD_ASPECT) > WELL_ASPECT_TOLERANCE:
            continue
        box = cv2.boxPoints(rect) + np.array([ox, oy], dtype=np.float32)
        best, best_area = box.reshape(4, 1, 2).astype(np.float32), area
    return best, well


def _fit(img, w, h):
    """Letterbox img into a w x h black canvas, preserving aspect ratio."""
    canvas = np.zeros((h, w, 3), np.uint8)
    if img is None:
        return canvas
    scale = min(w / img.shape[1], h / img.shape[0])
    r = cv2.resize(img, (int(img.shape[1] * scale), int(img.shape[0] * scale)))
    y0, x0 = (h - r.shape[0]) // 2, (w - r.shape[1]) // 2
    canvas[y0:y0 + r.shape[0], x0:x0 + r.shape[1]] = r
    return canvas


def _put_fit(canvas, text, x, y, max_w, color, scale=0.9, thickness=2):
    """Draw text at (x, y), shrinking the font until it fits max_w (never cut off)."""
    while scale > 0.3:
        (tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
        if tw <= max_w:
            break
        scale -= 0.05
    cv2.putText(canvas, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness)


def camera_view(frame, well):
    """Camera image cropped to the black well (the detector outline is already drawn on `frame`)."""
    if well is None:
        return frame
    x, y, w, h = well
    pad = int(0.08 * max(w, h))
    return frame[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad]


_card_img_cache = {}


def card_image(card_id):
    """Reference image of a matched card from image_cache/ (built by build_hash_index.py)."""
    if card_id not in _card_img_cache:
        _card_img_cache[card_id] = cv2.imread(str(Path("image_cache") / (card_id.replace(":", "_") + ".jpg")))
    return _card_img_cache[card_id]


def compose_display(frame, well, card_img, title="", subtitle="", color=(0, 255, 0)):
    """Two equal panels: the camera view (cropped to the black well, with the detector
    outline already drawn on it) on the left, and the detected card's reference image on
    the right (blank until something matches). The caption under the right panel is the
    matched card's name/set -- no separate match / no-match text."""
    PW, PH, CAP = 540, 720, 90
    if well is not None:
        x, y, w, h = well
        pad = int(0.08 * max(w, h))
        view = frame[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad]
    else:
        view = frame
    left, right = _fit(view, PW, PH), _fit(card_img, PW, PH)
    cap = np.zeros((CAP, PW * 2 + 6, 3), np.uint8)
    _put_fit(cap, "Camera", 12, 38, PW - 24, (200, 200, 200), 0.9)
    if title:
        _put_fit(cap, title, PW + 18, 38, PW - 24, color)
        if subtitle:
            _put_fit(cap, subtitle, PW + 18, 72, PW - 24, (200, 200, 200), 0.7, 1)
    body = np.hstack([left, np.full((PH, 6, 3), 60, np.uint8), right])
    return np.vstack([body, cap])


def detect_card_edges(frame):
    """Find the largest contour with a plausible card aspect ratio, rotation-
    tolerant (minAreaRect, not an axis-aligned box). Uses minAreaRect's own
    box points as the corners directly, rather than requiring approxPolyDP to
    simplify down to exactly 4 vertices -- real-world contours (rounded card
    corners, art detail near the edges, uneven lighting) rarely simplify that
    cleanly, even when the card itself is clearly the dominant shape."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)
    edges = cv2.dilate(edges, None, iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    frame_area = frame.shape[0] * frame.shape[1]
    best = None
    best_area = 0
    for c in contours:
        area = cv2.contourArea(c)
        if area < MIN_AREA_FRACTION * frame_area or area <= best_area:
            continue
        rect = cv2.minAreaRect(c)
        (_, _), (w, h), _ = rect
        if w == 0 or h == 0:
            continue
        aspect = min(w, h) / max(w, h)
        if abs(aspect - CARD_ASPECT) > ASPECT_TOLERANCE:
            continue
        box = cv2.boxPoints(rect)
        best, best_area = box.reshape(4, 1, 2).astype(np.float32), area
    return best


SMOOTH_DEADBAND_PX = 7.0  # mean corner shift below this = noise, hold the previous corners
_smooth_prev = None


def smooth_corners(corners):
    """Deadband filter: if the new corners are within a few px of the previous
    frame's (after aligning cyclic order, which minAreaRect can rotate), keep
    the previous ones so the crop is rock steady on a still card; any real
    movement snaps straight to the new detection with no lag. Pass None when
    nothing was detected to reset."""
    global _smooth_prev
    if corners is None:
        _smooth_prev = None
        return None
    c = corners.reshape(4, 2).astype("float32")
    if _smooth_prev is not None:
        shifts = [np.roll(c, k, axis=0) for k in range(4)]
        dists = [np.linalg.norm(x - _smooth_prev, axis=1).mean() for x in shifts]
        k = int(np.argmin(dists))
        if dists[k] < SMOOTH_DEADBAND_PX:
            return _smooth_prev
        # past the deadband: follow quickly but not instantly, so borderline
        # detections don't flip the crop back and forth; big jumps (new card) snap
        c = shifts[k] if dists[k] > 4 * SMOOTH_DEADBAND_PX else 0.6 * shifts[k] + 0.4 * _smooth_prev
    _smooth_prev = c
    return c


_closeup_prev = None


def smooth_closeup(warped):
    """Display-only temporal blend of the closeup panel (matching still uses the
    raw warp). Resets when the card changes or disappears so it never ghosts."""
    global _closeup_prev
    if warped is None:
        _closeup_prev = None
        return None
    cur = warped.astype("float32")
    if _closeup_prev is not None and np.abs(cur - _closeup_prev).mean() < 25:
        cur = 0.12 * cur + 0.88 * _closeup_prev
    _closeup_prev = cur
    return cur.astype("uint8")


def warp_card(frame, corners):
    src = order_corners(corners)
    w, h = WARP_SIZE
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype="float32")
    matrix = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(frame, matrix, (w, h))


def _load_last_committed():
    try:
        return json.loads(LAST_COMMITTED_PATH.read_text()).get("card_id")
    except (FileNotFoundError, json.JSONDecodeError):
        return None

def preferred_printing(card_id, name, set_name):
    """Starter deck mode (Scan & Sort page -> scan_prefs.json, re-read on every add): if the
    matched card also has a printing in the chosen set, file the scan under that printing --
    a starter deck's reprints look exactly like the originals to the camera."""
    try:
        prefer = json.loads(Path("scan_prefs.json").read_text()).get("prefer_set")
    except (OSError, ValueError):
        prefer = None
    if not prefer:
        return card_id, name, set_name
    db = sqlite3.connect(DB)
    try:
        row = db.execute("""SELECT p.id, p.name, s.name FROM cards c
            JOIN cards p ON p.game_id = c.game_id AND p.number = c.number AND p.set_id = ?
            JOIN sets s ON s.id = p.set_id WHERE c.id = ? ORDER BY p.id LIMIT 1""", (prefer, card_id)).fetchone()
    finally:
        db.close()
    return tuple(row) if row else (card_id, name, set_name)


def commit_card(card_id, name, set_name):
    db = sqlite3.connect(DB)
    try:
        action = core.ingest_live_scan(db, card_id, variant="normal")
        core.report(db)
        from sort_viewer import build_data
        cards = build_data(db)
        Path("viewer_data.json").write_text(__import__("json").dumps(cards))
        is_dup = bool(cards and cards[-1]["duplicate"])
    finally:
        db.close()
    print(f"[{action}] {name} ({set_name})" + ("  DUPLICATE" if is_dup else ""))
    return is_dup


def send_ping(base, is_dup):
    """Tell the phone page to play its success tone (see phone_cam_server.py /ping)."""
    try:
        urllib.request.urlopen(urllib.request.Request(f"{base}/ping?dup={int(is_dup)}", method="POST"), timeout=0.5).read()
    except Exception:
        pass


def set_torch(base, on):
    try:
        urllib.request.urlopen(urllib.request.Request(f"{base}/torch?on={int(on)}", method="POST"), timeout=0.5).read()
    except Exception:
        pass  # torch is a nice-to-have; never let it interrupt scanning


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else 0
    try:
        source = int(source)
    except ValueError:
        pass  # a URL string -- cv2.VideoCapture handles both int and str

    db = sqlite3.connect(DB)
    index = load_hash_index(db)
    db.close()
    print(f"Loaded {len(index['card_ids'])} reference hashes.")

    cap = cv2.VideoCapture(source)
    if isinstance(source, str):
        # A stream URL (the phone page may not have started yet): keep retrying instead of exiting.
        while not cap.isOpened():
            print(f"Waiting for the camera stream at {source} (tap Start on the phone page)...", flush=True)
            time.sleep(3)
            cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        sys.exit(f"Could not open camera source {source!r}")

    miss_streak = 0
    votes = []            # recent (index, distance) top-1 per frame, for best-of-frames voting
    last_committed = _load_last_committed()  # survives restarts -- see LAST_COMMITTED_PATH note above
    flash_until = 0
    flash_text = ""
    flash_sub = ""
    # The web pages (Scan & Sort / Scanner) show the live view, so the Qt preview window is redundant: runs
    # headless unless SCANNER_WINDOW=1 is set (then 'q' in the window quits).
    gui_ok = os.environ.get("SCANNER_WINDOW") == "1"
    last_status = None
    last_added = None     # {"name", "set", "at"} for the web preview's "last added" line
    last_web_write = 0
    torch_base = None     # set when the source is phone_cam_server.py (it relays torch commands to the phone)
    cam_base = None       # same server: also relays the success ping
    if isinstance(source, str) and urlparse(source).port == 8081:
        cam_base = f"http://{urlparse(source).hostname}:8081"
        if TORCH_PULSE:
            torch_base = cam_base
    torch_on, nomatch_run, last_pulse = False, 0, 0.0
    if torch_base:
        set_torch(torch_base, False)
        print("Torch assist on: will pulse the flashlight when a detected card isn't matching.", flush=True)

    while True:
        ok, frame = cap.read()
        if not ok:
            print("Frame read failed, stopping.")
            break
        frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)

        corners, well = detect_card(frame)
        corners = smooth_corners(corners)
        warped = None
        frame_top = (None, None)
        if corners is not None:
            warped = warp_card(frame, corners)
            pil_img = Image.fromarray(cv2.cvtColor(warped, cv2.COLOR_BGR2RGB))
            # Perceptual hashes aren't rotation-invariant, but the card can land
            # in the frame at any of the 4 orientations (phone held sideways,
            # card laid sideways, etc.) -- try all 4 and keep the best match.
            i, dist = None, None
            for rotation in (None, Image.ROTATE_90, Image.ROTATE_180, Image.ROTATE_270):
                rotated = pil_img if rotation is None else pil_img.transpose(rotation)
                i2, dist2 = best_match(index, rotated)
                if dist is None or dist2 < dist:
                    i, dist = i2, dist2
            frame_top = (i, dist)

        votes.append(frame_top)
        votes = votes[-BEST_WINDOW:]
        # Consensus: the card that is top-1 on the most recent frames, with the
        # best (lowest) distance it achieved in the window.
        match_card_id = match_i = None
        tally = {}
        for vi, vd in votes:
            if vi is not None:
                t = tally.setdefault(vi, [0, vd])
                t[0] += 1
                t[1] = min(t[1], vd)
        if tally:
            ci, (n_votes, best_d) = max(tally.items(), key=lambda kv: kv[1][0])
            if n_votes >= BEST_MIN_VOTES and best_d <= MATCH_DISTANCE_THRESHOLD:
                match_i, match_card_id = ci, index["card_ids"][ci]
        if corners is not None:
            cv2.drawContours(frame, [corners.astype(np.int32)], -1, (0, 255, 0) if match_card_id else (0, 0, 255), 2)

        if match_card_id and match_card_id != last_committed:
            add_id, name, set_name = preferred_printing(match_card_id, index["names"][match_i], index["sets"][match_i])
            is_dup = commit_card(add_id, name, set_name)
            if cam_base:
                send_ping(cam_base, is_dup)
            last_committed = match_card_id
            LAST_COMMITTED_PATH.write_text(json.dumps({"card_id": last_committed}))
            last_added = {"name": name, "set": set_name, "at": time.strftime("%H:%M:%S"), "card_id": add_id}
            flash_text, flash_sub = f"ADDED: {name}", set_name
            flash_until = time.time() + 2

        # Torch assist (see TORCH_* constants)
        if torch_base:
            nomatch_run = nomatch_run + 1 if (corners is not None and match_card_id is None) else 0
            t_now = time.time()
            if not torch_on and nomatch_run >= TORCH_AFTER_FRAMES and t_now - last_pulse >= TORCH_COOLDOWN:
                set_torch(torch_base, True)
                torch_on, last_pulse = True, t_now
            elif torch_on and (t_now - last_pulse >= TORCH_PULSE_SECONDS or match_card_id):
                set_torch(torch_base, False)
                torch_on, nomatch_run = False, 0

        # Re-arm only after a sustained run of no-match frames (card really
        # removed), not a single glare/jitter frame -- otherwise one dropped
        # frame lets the same card commit again.
        miss_streak = miss_streak + 1 if match_card_id is None else 0
        if miss_streak >= REARM_MISS_FRAMES:
            last_committed = None

        if time.time() < flash_until:
            title, subtitle, color = flash_text, flash_sub, (0, 255, 255)
        elif match_card_id:
            title, subtitle, color = index["names"][match_i], index["sets"][match_i], (0, 255, 0)
        else:
            title, subtitle, color = ("No match", "", (0, 0, 255)) if corners is not None else ("No card", "", (160, 160, 160))

        # Always print status to the terminal on change -- the preview window
        # may silently fail to render in some display environments without
        # raising a Python exception, so this is the reliable observability
        # path regardless of whether the GUI actually works.
        status = match_card_id and (index["names"][match_i], index["sets"][match_i])
        if status != last_status:
            print(f"seeing: {status[0]} ({status[1]})" if status else "no card detected", flush=True)
            last_status = status

        # Web preview for sort_viewer's browser page -- throttled separately
        # from the scan loop's own frame rate, since the loop runs as fast as
        # the camera allows but a JPEG write + JSON write every single frame
        # would be wasted disk I/O for something only polled a few times/sec.
        if match_card_id:
            display = compose_display(frame, well, card_image(match_card_id), index["names"][match_i], index["sets"][match_i], (0, 255, 0))
        else:
            display = compose_display(frame, well, None)
        now = time.time()
        if now - last_web_write >= LIVE_FRAME_INTERVAL:
            cv2.imwrite(str(LIVE_FRAME_PATH), display)
            cv2.imwrite(str(LIVE_CAM_PATH), camera_view(frame, well), [cv2.IMWRITE_JPEG_QUALITY, 85])
            LIVE_STATUS_PATH.write_text(json.dumps({
                "seeing": {"name": status[0], "set": status[1], "card_id": match_card_id} if status else None,
                "framing": "ok" if well_missing_run <= WELL_MISS_LIMIT else "no_well",
                "last_added": last_added,
            }))
            last_web_write = now

        if gui_ok:
            try:
                cv2.imshow("live_scanner.py -- press q to quit", display)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            except cv2.error as e:
                print(f"Preview window unavailable ({e!r}) -- continuing headless, Ctrl-C to quit.")
                gui_ok = False

    if torch_base:
        set_torch(torch_base, False)
    cap.release()
    if gui_ok:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
