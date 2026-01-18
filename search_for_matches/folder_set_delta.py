#!/usr/bin/env python3
"""
Bidirectional visual delta between two folder trees (Set A and Set B).

Outputs in --out-dir:
- a_minus_b.json      : files in A that have NO accepted match in B
- b_minus_a.json      : files in B that have NO accepted match in A
- matches.json        : accepted matches (best match per src in each direction)
- summary.json        : counts + params + paths

Matching:
- Candidate: pHash + chunk inverted index (fast)
- Confirm: ORB + RANSAC inliers (robust)

Supports: .jpg/.jpeg + .nef (via rawpy)
"""

from __future__ import annotations

import os
import json
import time
import pathlib
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional
from collections import defaultdict, Counter

import click
import numpy as np
from tqdm import tqdm
from PIL import Image, ImageOps
import imagehash
import cv2
import rawpy

SUPPORTED_EXTS = {".jpg", ".jpeg", ".nef"}


# ----------------------------
# Data model
# ----------------------------

@dataclass(frozen=True)
class Entry:
    path: str
    ext: str
    phash_hex: str


# ----------------------------
# Utilities
# ----------------------------

def ensure_dir(p: str) -> None:
    pathlib.Path(p).mkdir(parents=True, exist_ok=True)

def file_sig(path: str) -> Tuple[float, int]:
    st = os.stat(path)
    return (float(st.st_mtime), int(st.st_size))

def _pil_prepare(img: Image.Image) -> Image.Image:
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    return img


# ----------------------------
# Image decode + features
# ----------------------------

def load_image(path: str, max_side: int) -> Optional[Image.Image]:
    ext = pathlib.Path(path).suffix.lower()
    try:
        if ext in (".jpg", ".jpeg"):
            with Image.open(path) as im:
                im = _pil_prepare(im)
                im.thumbnail((max_side, max_side))
                return im.copy()

        if ext == ".nef":
            with rawpy.imread(path) as raw:
                rgb = raw.postprocess(
                    use_camera_wb=True,
                    no_auto_bright=True,
                    output_bps=8,
                    half_size=True,
                )
            im = Image.fromarray(rgb)
            im = _pil_prepare(im)
            im.thumbnail((max_side, max_side))
            return im

    except Exception:
        return None

    return None

def phash_hex(img: Image.Image) -> str:
    return str(imagehash.phash(img))

def to_gray(img: Image.Image, max_side: int) -> np.ndarray:
    img = img.copy()
    img.thumbnail((max_side, max_side))
    arr = np.array(img)  # RGB
    return cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)

def phash_chunks(h: str, chunk_len: int = 4) -> List[str]:
    return [h[i:i+chunk_len] for i in range(0, len(h), chunk_len)]

def orb_score(grayA: np.ndarray, grayB: np.ndarray, nfeatures: int) -> Tuple[int, int]:
    orb = cv2.ORB_create(nfeatures=nfeatures)
    kpa, desa = orb.detectAndCompute(grayA, None)
    kpb, desb = orb.detectAndCompute(grayB, None)

    if desa is None or desb is None or kpa is None or kpb is None:
        return (0, 0)
    if len(kpa) < 10 or len(kpb) < 10:
        return (0, 0)

    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    matches = bf.knnMatch(desa, desb, k=2)

    good = []
    for m_n in matches:
        if len(m_n) != 2:
            continue
        m, n = m_n
        if m.distance < 0.75 * n.distance:
            good.append(m)

    if len(good) < 10:
        return (len(good), 0)

    ptsA = np.float32([kpa[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    ptsB = np.float32([kpb[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)

    _, mask = cv2.findHomography(ptsA, ptsB, cv2.RANSAC, 5.0)
    inliers = int(mask.sum()) if mask is not None else 0
    return (len(good), inliers)


# ----------------------------
# SQLite indexing (per set)
# ----------------------------

def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS images (
            path TEXT PRIMARY KEY,
            ext  TEXT NOT NULL,
            phash TEXT NOT NULL,
            mtime REAL NOT NULL,
            size  INTEGER NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_images_phash ON images(phash)")
    conn.commit()
    return conn

def upsert(conn: sqlite3.Connection, path: str, ext: str, h: str, mtime: float, size: int) -> None:
    conn.execute("""
        INSERT INTO images(path, ext, phash, mtime, size)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            ext=excluded.ext,
            phash=excluded.phash,
            mtime=excluded.mtime,
            size=excluded.size
    """, (path, ext, h, mtime, size))

def load_entries(conn: sqlite3.Connection) -> List[Entry]:
    rows = conn.execute("SELECT path, ext, phash FROM images").fetchall()
    return [Entry(path=r[0], ext=r[1], phash_hex=r[2]) for r in rows]

def update_index(conn: sqlite3.Connection, root_dir: str, max_side: int, progress: bool, label: str) -> None:
    root = pathlib.Path(root_dir)

    disk: Dict[str, Tuple[str, float, int]] = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext not in SUPPORTED_EXTS:
            continue
        mtime, size = file_sig(str(p))
        disk[str(p)] = (ext, mtime, size)

    db_rows = conn.execute("SELECT path, mtime, size FROM images").fetchall()
    db_sig = {r[0]: (float(r[1]), int(r[2])) for r in db_rows}

    stale = [p for p in db_sig.keys() if p not in disk]
    if stale:
        conn.executemany("DELETE FROM images WHERE path = ?", [(p,) for p in stale])
        conn.commit()

    to_proc = []
    for path, (ext, mtime, size) in disk.items():
        old = db_sig.get(path)
        if old is None or old != (mtime, size):
            to_proc.append((path, ext, mtime, size))

    if not to_proc:
        return

    it = to_proc
    if progress:
        it = tqdm(to_proc, desc=f"Index {label}", unit="file")

    for (path, ext, mtime, size) in it:
        im = load_image(path, max_side=max_side)
        if im is None:
            continue
        h = phash_hex(im)
        upsert(conn, path, ext, h, mtime, size)

    conn.commit()


# ----------------------------
# Candidate retrieval
# ----------------------------

def build_chunk_index(entries: List[Entry], chunk_len: int = 4) -> Dict[str, List[int]]:
    idx = defaultdict(list)
    for i, e in enumerate(entries):
        for c in phash_chunks(e.phash_hex, chunk_len=chunk_len):
            idx[c].append(i)
    return idx

def candidates_for_hash(
    target_hash_hex: str,
    dst_entries: List[Entry],
    dst_chunk_index: Dict[str, List[int]],
    phash_max_dist: int,
    min_shared_chunks: int,
    max_candidates: int,
) -> List[Tuple[int, int]]:
    counts = Counter()
    for c in phash_chunks(target_hash_hex, chunk_len=4):
        for i in dst_chunk_index.get(c, []):
            counts[i] += 1

    pre = [i for i, k in counts.items() if k >= min_shared_chunks]
    if not pre:
        return []

    th = imagehash.hex_to_hash(target_hash_hex)
    out: List[Tuple[int, int]] = []
    for i in pre:
        d = th - imagehash.hex_to_hash(dst_entries[i].phash_hex)
        if d <= phash_max_dist:
            out.append((i, int(d)))

    out.sort(key=lambda x: x[1])
    return out[:max_candidates]


# ----------------------------
# Best match (one direction)
# ----------------------------

def best_match(
    src_entry: Entry,
    dst_entries: List[Entry],
    dst_index: Dict[str, List[int]],
    dst_cache: Dict[int, Image.Image],
    max_side: int,
    phash_max_dist: int,
    min_shared_chunks: int,
    max_candidates: int,
    orb_nfeatures: int,
    orb_min_matches: int,
    orb_min_inliers: int,
) -> Optional[dict]:
    src_img = load_image(src_entry.path, max_side=max_side)
    if src_img is None:
        return None

    th_hex = phash_hex(src_img)
    cand = candidates_for_hash(
        th_hex, dst_entries, dst_index,
        phash_max_dist=phash_max_dist,
        min_shared_chunks=min_shared_chunks,
        max_candidates=max_candidates,
    )
    if not cand:
        return None

    src_gray = to_gray(src_img, max_side=max_side)

    best = None
    best_key = (-1, -1, 999)  # (inliers, good, -dist) but we'll store dist separately

    for idx, dist in cand:
        if idx not in dst_cache:
            im = load_image(dst_entries[idx].path, max_side=max_side)
            if im is None:
                continue
            dst_cache[idx] = im
        else:
            im = dst_cache[idx]

        good, inliers = orb_score(src_gray, to_gray(im, max_side=max_side), nfeatures=orb_nfeatures)

        if good >= orb_min_matches and inliers >= orb_min_inliers:
            key = (inliers, good, -dist)
            if key > best_key:
                best_key = key
                best = {
                    "dstPath": dst_entries[idx].path,
                    "dstExt": dst_entries[idx].ext,
                    "phashDist": dist,
                    "orbGoodMatches": good,
                    "orbInliers": inliers,
                }

    return best

def match_direction(
    src_entries: List[Entry],
    dst_entries: List[Entry],
    dst_index: Dict[str, List[int]],
    max_side: int,
    phash_max_dist: int,
    min_shared_chunks: int,
    max_candidates: int,
    orb_nfeatures: int,
    orb_min_matches: int,
    orb_min_inliers: int,
    progress: bool,
    label: str,
) -> Tuple[List[dict], List[str]]:
    dst_cache: Dict[int, Image.Image] = {}
    matches: List[dict] = []
    unmatched: List[str] = []

    it = src_entries
    if progress:
        it = tqdm(src_entries, desc=f"Match {label}", unit="file")

    for e in it:
        m = best_match(
            src_entry=e,
            dst_entries=dst_entries,
            dst_index=dst_index,
            dst_cache=dst_cache,
            max_side=max_side,
            phash_max_dist=phash_max_dist,
            min_shared_chunks=min_shared_chunks,
            max_candidates=max_candidates,
            orb_nfeatures=orb_nfeatures,
            orb_min_matches=orb_min_matches,
            orb_min_inliers=orb_min_inliers,
        )
        if m is None:
            unmatched.append(e.path)
        else:
            matches.append({
                "srcPath": e.path,
                "srcExt": e.ext,
                **m
            })

    return matches, unmatched


# ----------------------------
# CLI
# ----------------------------

@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--set-a", required=True, type=click.Path(exists=True, file_okay=False), help="Folder for set A")
@click.option("--set-b", required=True, type=click.Path(exists=True, file_okay=False), help="Folder for set B")
@click.option("--out-dir", default="set_delta_out", show_default=True, help="Output directory")
@click.option("--db-dir", default=".set_delta_index", show_default=True, help="Directory for SQLite indices")
@click.option("--max-side", default=900, show_default=True, type=int, help="Decode size (speed/robustness)")
@click.option("--phash-max-dist", default=10, show_default=True, type=int, help="Max pHash distance")
@click.option("--min-shared-chunks", default=2, show_default=True, type=int, help="Prefilter strictness")
@click.option("--max-candidates", default=30, show_default=True, type=int, help="Max candidates per file")
@click.option("--orb-nfeatures", default=1500, show_default=True, type=int, help="ORB features")
@click.option("--orb-min-matches", default=40, show_default=True, type=int, help="Min ORB good matches")
@click.option("--orb-min-inliers", default=18, show_default=True, type=int, help="Min RANSAC inliers")
@click.option("--progress/--no-progress", default=True, show_default=True, help="Progress bars")
def main(
    set_a: str,
    set_b: str,
    out_dir: str,
    db_dir: str,
    max_side: int,
    phash_max_dist: int,
    min_shared_chunks: int,
    max_candidates: int,
    orb_nfeatures: int,
    orb_min_matches: int,
    orb_min_inliers: int,
    progress: bool,
):
    ensure_dir(out_dir)
    ensure_dir(db_dir)

    db_a = os.path.join(db_dir, "A.sqlite")
    db_b = os.path.join(db_dir, "B.sqlite")

    click.echo(f"[A] Updating index in {db_a} ...")
    conn_a = init_db(db_a)
    update_index(conn_a, set_a, max_side=max_side, progress=progress, label="A")
    entries_a = load_entries(conn_a)
    click.echo(f"[A] Indexed: {len(entries_a)}")

    click.echo(f"[B] Updating index in {db_b} ...")
    conn_b = init_db(db_b)
    update_index(conn_b, set_b, max_side=max_side, progress=progress, label="B")
    entries_b = load_entries(conn_b)
    click.echo(f"[B] Indexed: {len(entries_b)}")

    if not entries_a:
        raise SystemExit("Set A indexed 0 files. Check extensions and path.")
    if not entries_b:
        raise SystemExit("Set B indexed 0 files. Check extensions and path.")

    click.echo("[A] Building chunk index ...")
    idx_a = build_chunk_index(entries_a)

    click.echo("[B] Building chunk index ...")
    idx_b = build_chunk_index(entries_b)

    # A -> B
    matches_a2b, a_minus_b = match_direction(
        src_entries=entries_a,
        dst_entries=entries_b,
        dst_index=idx_b,
        max_side=max_side,
        phash_max_dist=phash_max_dist,
        min_shared_chunks=min_shared_chunks,
        max_candidates=max_candidates,
        orb_nfeatures=orb_nfeatures,
        orb_min_matches=orb_min_matches,
        orb_min_inliers=orb_min_inliers,
        progress=progress,
        label="A->B",
    )

    # B -> A
    matches_b2a, b_minus_a = match_direction(
        src_entries=entries_b,
        dst_entries=entries_a,
        dst_index=idx_a,
        max_side=max_side,
        phash_max_dist=phash_max_dist,
        min_shared_chunks=min_shared_chunks,
        max_candidates=max_candidates,
        orb_nfeatures=orb_nfeatures,
        orb_min_matches=orb_min_matches,
        orb_min_inliers=orb_min_inliers,
        progress=progress,
        label="B->A",
    )

    # Write outputs
    out_matches = os.path.join(out_dir, "matches.json")
    out_a_minus_b = os.path.join(out_dir, "a_minus_b.json")
    out_b_minus_a = os.path.join(out_dir, "b_minus_a.json")
    out_summary = os.path.join(out_dir, "summary.json")

    with open(out_matches, "w", encoding="utf-8") as f:
        json.dump({"A_to_B": matches_a2b, "B_to_A": matches_b2a}, f, indent=2, ensure_ascii=False)

    with open(out_a_minus_b, "w", encoding="utf-8") as f:
        json.dump(a_minus_b, f, indent=2, ensure_ascii=False)

    with open(out_b_minus_a, "w", encoding="utf-8") as f:
        json.dump(b_minus_a, f, indent=2, ensure_ascii=False)

    summary = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "setA": set_a,
        "setB": set_b,
        "counts": {
            "indexedA": len(entries_a),
            "indexedB": len(entries_b),
            "matchesAtoB": len(matches_a2b),
            "matchesBtoA": len(matches_b2a),
            "aMinusB": len(a_minus_b),
            "bMinusA": len(b_minus_a),
        },
        "params": {
            "maxSide": max_side,
            "phashMaxDist": phash_max_dist,
            "minSharedChunks": min_shared_chunks,
            "maxCandidates": max_candidates,
            "orbNFeatures": orb_nfeatures,
            "orbMinMatches": orb_min_matches,
            "orbMinInliers": orb_min_inliers,
        },
        "outputs": {
            "matches": out_matches,
            "a_minus_b": out_a_minus_b,
            "b_minus_a": out_b_minus_a,
            "summary": out_summary,
        }
    }

    with open(out_summary, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    click.echo("")
    click.echo("Done.")
    click.echo(f"A \\ B: {len(a_minus_b)}  -> {out_a_minus_b}")
    click.echo(f"B \\ A: {len(b_minus_a)}  -> {out_b_minus_a}")
    click.echo(f"Matches: {out_matches}")
    click.echo(f"Summary: {out_summary}")


if __name__ == "__main__":
    main()

