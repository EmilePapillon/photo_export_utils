#!/usr/bin/env python3
"""
Copy media into year-based folders using EXIF DateTimeOriginal.

You supply a source tree and destination root. Files with extensions you choose
are copied into <dst>/<year>/, using DateTimeOriginal parsed by exiftool.
Files missing DateTimeOriginal can be skipped or dropped into a configurable
fallback folder (default: Unknown).
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import click

DEFAULT_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".mp4", ".mov"}  # case-insensitive
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

@click.command()
@click.argument("src_root", type=click.Path(path_type=Path, exists=True, readable=True), metavar="SRC")
@click.argument("dst_root", type=click.Path(path_type=Path), metavar="DST")
@click.option(
    "--ext",
    "exts",
    multiple=True,
    help="File extension to include (repeatable, e.g., --ext .jpg --ext .mov). Defaults to common photo/video types.",
)
@click.option(
    "--unknown-folder",
    default="Unknown",
    show_default=True,
    help="Folder name for files lacking DateTimeOriginal. Use '' to skip those files.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be copied without writing files.",
)
def main(src_root: Path, dst_root: Path, exts: tuple[str, ...], unknown_folder: str, dry_run: bool) -> None:
    """Copy media into year folders derived from DateTimeOriginal, with configurable filters and fallbacks."""
    require_exiftool()

    src_root = src_root.expanduser().resolve()
    dst_root = dst_root.expanduser().resolve()
    dst_root.mkdir(parents=True, exist_ok=True)

    chosen_exts = {e.lower() for e in exts} if exts else set(DEFAULT_EXTS)
    unknown_name = unknown_folder if unknown_folder != "" else None

    total = copied = unknown = skipped = 0

    for p in src_root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in chosen_exts:
            continue

        total += 1
        dto = get_datetimeoriginal(p)
        year = None
        if dto:
            m = YEAR_RE.match(dto)
            if m:
                year = m.group(1)

        if not year:
            if unknown_name is None:
                skipped += 1
                print(f"[SKIP no DateTimeOriginal] {p}")
                continue
            year = unknown_name
            unknown += 1

        out_dir = dst_root / year
        out_dir.mkdir(parents=True, exist_ok=True)

        dest = unique_dest_path(out_dir, p.name)
        if dry_run:
            copied += 1
            print(f"[DRYRUN COPY] {p} -> {dest}")
            continue

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
