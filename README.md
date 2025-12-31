# Utility Scripts

- `set_timestamps.py`: Uses `exiftool` to apply EXIF and filesystem timestamps to media referenced in Facebook export JSON files. Accepts a JSON file or directory of JSONs, auto-discovers iterable entries containing URIs and timestamps, and supports explicit entry paths plus custom URI/timestamp key names.
- `copy_by_exif_year.py`: Copies media from a source tree into year-based folders (optionally an `Unknown` bucket) using `DateTimeOriginal` read via `exiftool`, avoiding name collisions when duplicates exist.
- `google_extract_metadata.py`: Scans for media files missing `DateTimeOriginal`, finds matching sidecar JSON files in the same folder, and writes the timestamp into EXIF using `exiftool`. Intended for use with Google exported images.
- `rename.py`: Renames files based on `DateTimeOriginal` (format `YYYYMMDD-HHMMSS`) with a configurable prefix, preserving extensions and avoiding collisions; supports dry-run and recursive modes.
