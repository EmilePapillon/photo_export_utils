#!/usr/bin/env python3
import os
import re
import shutil
import subprocess
from pathlib import Path

SRC_ROOT = Path("/Users/emile/Downloads/data/facebook_all_data/takeout_all")
DST_ROOT = Path("/Users/emile/Downloads/Facebook_Photos")

EXTS = {".jpg", ".jpeg", ".png", ".heic", ".mp4", ".mov"}  # case-insensitive
UNKNOWN_FOLDER = "Unknown"  # put files without DateTimeOriginal here (or set to None to skip)

YEAR_RE = re.compile(r"^(\d{4}):\d{2}:\d{2}\s")

def require_exiftool() -> None:
    """Exit if exiftool is not installed or not runnable."""
    try:
        subprocess.run(["exiftool", "-ver"], check=True, capture_output=True, text=True)
    except Exception:
        raise SystemExit("exiftool not found. Install with: brew install exiftool")

def get_datetimeoriginal(path: Path) -> str | None:
    """Return DateTimeOriginal for a path or None when unavailable."""
    # -s -s -s => value only
    # -api LargeFileSupport=1 helps with big videos
    res = subprocess.run(
        ["exiftool", "-api", "LargeFileSupport=1", "-s", "-s", "-s", "-DateTimeOriginal", str(path)],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        return None
    val = (res.stdout or "").strip()
    return val or None

def unique_dest_path(dest_dir: Path, filename: str) -> Path:
    """Return a destination path that avoids collisions by appending _N if needed."""
    base = Path(filename).stem
    ext = Path(filename).suffix
    candidate = dest_dir / (base + ext)
    if not candidate.exists():
        return candidate
    i = 1
    while True:
        candidate = dest_dir / f"{base}_{i}{ext}"
        if not candidate.exists():
            return candidate
        i += 1

def main() -> None:
    """Copy media into year folders derived from DateTimeOriginal, with Unknown fallback."""
    require_exiftool()
    DST_ROOT.mkdir(parents=True, exist_ok=True)

    total = 0
    copied = 0
    unknown = 0
    skipped = 0

    for p in SRC_ROOT.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in EXTS:
            continue

        total += 1
        dto = get_datetimeoriginal(p)
        year = None
        if dto:
            m = YEAR_RE.match(dto)
            if m:
                year = m.group(1)

        if not year:
            if UNKNOWN_FOLDER is None:
                skipped += 1
                print(f"[SKIP no DateTimeOriginal] {p}")
                continue
            year = UNKNOWN_FOLDER
            unknown += 1

        out_dir = DST_ROOT / year
        out_dir.mkdir(parents=True, exist_ok=True)

        dest = unique_dest_path(out_dir, p.name)
        shutil.copy2(p, dest)  # preserves file times/metadata at filesystem level
        copied += 1
        print(f"[COPY] {p} -> {dest}")

    print("\nSummary")
    print(f"  total matched extensions: {total}")
    print(f"  copied: {copied}")
    print(f"  unknown year: {unknown}")
    print(f"  skipped: {skipped}")

if __name__ == "__main__":
    main()
