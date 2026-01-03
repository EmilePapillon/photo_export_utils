# Media Utility Scripts

Small helpers for repairing photo/video metadata and organizing files. All tools are written in Python and shell out to `exiftool` for EXIF access.

## Prerequisites
- Python 3.10+.
- `exiftool` installed and on PATH (macOS: `brew install exiftool`).

## Setup
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
- Or run `./venv_init.sh` to create/activate `.venv`, upgrade pip, and install dependencies in one go.

## Scripts

### extract_statistics.py
Generate an interactive Plotly histogram of capture timestamps across a media tree.
- Recursively scans `--input-dir` with `exiftool`, collecting capture dates from multiple EXIF/QuickTime tags.
- Filters to known photo/video extensions and ignores clearly bad timestamps.
- Writes a self-contained `index.html` into `--output-dir` with drill-down binning (click a bar to zoom; Back/Reset buttons to navigate).

Example:
```bash
python extract_statistics.py --input-dir ~/Pictures/archive --output-dir ~/tmp/capture-stats
open ~/tmp/capture-stats/index.html
```

### facebook_set_timestamps.py
Update EXIF DateTimeOriginal/CreateDate/ModifyDate and filesystem times for media referenced in Facebook JSON exports.
- Works on a single JSON file or every `*.json` under a directory.
- Auto-discovers iterables containing `uri` + timestamp keys; override with `--entry-path`.
- Supports custom key names via `--uri-key` and `--timestamp-key`.

Examples:
```bash
# Let the script discover entries inside a JSON export directory
python facebook_set_timestamps.py ~/Downloads/facebook-export --root ~/Downloads/facebook-export

# Explicit iterable path
python facebook_set_timestamps.py album.json --root ~/Pictures/facebook --entry-path albums.items
```

### copy_by_exif_year.py
Copy media into `<dst>/<year>/` folders using `DateTimeOriginal`; optionally drop files without EXIF dates into an `Unknown` bucket.

Key options:
- `--ext` to filter specific extensions (repeatable).
- `--unknown-folder ''` to skip files missing `DateTimeOriginal`.
- `--dry-run` to preview without copying.

Example:
```bash
python copy_by_exif_year.py ~/Pictures/raw ~/Pictures/by-year --ext .jpg --ext .mov
```

### google_extract_metadata.py
Populate missing `DateTimeOriginal` tags using sidecar JSON files from Google Takeout exports (matching stems in the same directory).

Example (run from the export root):
```bash
python google_extract_metadata.py
```

### rename.py
Rename files based on `DateTimeOriginal` (`YYYYMMDD-HHMMSS`) with a configurable prefix while avoiding collisions.

Example:
```bash
# Preview recursive renames with custom prefix
python rename.py ~/Pictures/import -r --prefix camera --dry-run
```

## Notes
- Back up media before writing EXIF data.
- `exiftool` uses in-place updates; if you prefer backup copies, remove `-overwrite_original` flags in the scripts.
