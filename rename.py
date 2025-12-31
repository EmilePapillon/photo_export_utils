#!/usr/bin/env python3
import argparse
import subprocess
from pathlib import Path

def exif_datetime_original(path: Path) -> str | None:
    """
    Returns DateTimeOriginal formatted as YYYYMMDD-HHMMSS, or None if missing.
    Uses exiftool for broad format support (JPG/HEIC/etc).
    """
    # -s -s -s => value only (no tag name), -d sets output format
    cmd = [
        "exiftool",
        "-s", "-s", "-s",
        "-d", "%Y%m%d-%H%M%S",
        "-DateTimeOriginal",
        str(path)
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        # exiftool error (corrupt file, unsupported, etc.)
        return None
    val = r.stdout.strip()
    return val if val else None

def next_available_name(dest_dir: Path, stem: str, suffix: str) -> Path:
    """
    Returns a non-colliding path in dest_dir with given stem and suffix,
    appending _1, _2, ... if needed.
    """
    candidate = dest_dir / f"{stem}{suffix}"
    if not candidate.exists():
        return candidate

    i = 1
    while True:
        candidate = dest_dir / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1

def rename_one(path: Path, prefix: str, dry_run: bool) -> tuple[bool, str]:
    """Rename one file using DateTimeOriginal; returns (success, message)."""
    dt = exif_datetime_original(path)
    if not dt:
        return False, f"SKIP (no DateTimeOriginal): {path}"

    # Keep original extension exactly (case preserved)
    suffix = path.suffix
    stem = f"{prefix}_{dt}"

    target = next_available_name(path.parent, stem, suffix)

    if target == path:
        return True, f"OK (already named): {path.name}"

    if dry_run:
        return True, f"DRYRUN: {path.name}  ->  {target.name}"

    path.rename(target)
    return True, f"RENAMED: {path.name}  ->  {target.name}"

def iter_files(root: Path, recursive: bool) -> list[Path]:
    """Return a list of files under root, optionally recursing."""
    if root.is_file():
        return [root]

    if recursive:
        return [p for p in root.rglob("*") if p.is_file()]
    else:
        return [p for p in root.iterdir() if p.is_file()]

def main():
    """CLI entry: rename files by EXIF DateTimeOriginal with configurable prefix."""
    ap = argparse.ArgumentParser(description="Rename files based on EXIF DateTimeOriginal.")
    ap.add_argument("path", nargs="?", default=".", help="File or directory (default: current dir)")
    ap.add_argument("--prefix", default="photo", help="Filename prefix (default: photo)")
    ap.add_argument("-r", "--recursive", action="store_true", help="Recurse into subdirectories")
    ap.add_argument("-n", "--dry-run", action="store_true", help="Show what would happen, don’t rename")
    args = ap.parse_args()

    root = Path(args.path).expanduser().resolve()
    if not root.exists():
        raise SystemExit(f"Path does not exist: {root}")

    files = iter_files(root, args.recursive)

    ok = 0
    for f in files:
        success, msg = rename_one(f, args.prefix, args.dry_run)
        print(msg)
        if success:
            ok += 1

    print(f"\nDone. Processed: {len(files)} files. Renamed/OK/Dryrun: {ok}. Skipped: {len(files)-ok}.")

if __name__ == "__main__":
    main()
