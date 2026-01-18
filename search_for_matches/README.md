# Search for Matches (folder_set_delta.py)

Bidirectional visual delta between two photo folders. The script hashes each image once, builds light-weight SQLite indices, then finds best visual matches in both directions (A→B and B→A) using pHash prefiltering plus ORB/RANSAC verification.

## Prerequisites
- Python 3.10+.
- `exiftool` is not required for this script, but OpenCV, Pillow, rawpy, and ImageHash are; install via `pip install -r requirements.txt` from this folder (consider a virtualenv).
- Supports `.jpg/.jpeg` and `.nef` files.

## Quick start
```bash
cd search_for_matches
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Compare two folders; outputs land in set_delta_out/ by default
python folder_set_delta.py \
  --set-a /path/to/setA \
  --set-b /path/to/setB \
  --out-dir set_delta_out
```
- Indices are cached under `.set_delta_index/` so re-runs only process changed files; delete that folder to force a full rebuild.

## Outputs
- `matches.json`: best accepted matches per source file in each direction (`A_to_B` and `B_to_A`), including pHash distance, ORB good matches, and RANSAC inliers.
- `a_minus_b.json`: files in A without an accepted match in B.
- `b_minus_a.json`: files in B without an accepted match in A.
- `summary.json`: run parameters, counts, and paths to the above outputs.

## Tuning
Key knobs for recall/precision trade-offs:
- `--max-side`: decode size in pixels (larger = more detail, slower).
- `--phash-max-dist`: maximum pHash Hamming distance after chunk prefiltering.
- `--min-shared-chunks`/`--max-candidates`: tighten/loosen the pHash prefilter.
- `--orb-nfeatures`, `--orb-min-matches`, `--orb-min-inliers`: ORB and RANSAC thresholds; raise to reduce false positives, lower for more aggressive matching.
