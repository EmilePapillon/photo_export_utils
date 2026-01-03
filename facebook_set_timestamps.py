#!/usr/bin/env python3
"""
Set EXIF and filesystem timestamps for media referenced in Facebook JSON exports.

Usage options:
  - Provide a directory: all *.json files are scanned and the script discovers the
    iterable that contains items with a URI and timestamp.
  - Provide a single JSON file and optionally a dotted entry path (e.g., "videos_v2"
    or "albums.items") to explicitly choose the iterable.

By default the script looks for entries with a "uri" and "creation_timestamp".
Override with --uri-key/--timestamp-key if your JSON uses different keys.
"""
import json
import os
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from typing import Iterable, Iterator, Sequence

import click

DEFAULT_TIMESTAMP_KEYS = ["creation_timestamp", "timestamp", "taken_at", "taken_time"]


def exif_dt_from_unix(ts: int) -> str:
    """ExifTool date format: 'YYYY:MM:DD HH:MM:SS' (UTC)."""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y:%m:%d %H:%M:%S")


def run_exiftool(path: Path, exif_dt: str) -> None:
    """Write EXIF timestamps for DateTimeOriginal/CreateDate/ModifyDate using exiftool."""
    # -overwrite_original: don't keep _original backups
    # -P: preserve filesystem timestamps that exiftool might otherwise change
    cmd = [
        "exiftool",
        "-overwrite_original",
        "-P",
        f"-DateTimeOriginal={exif_dt}",
        f"-CreateDate={exif_dt}",
        f"-ModifyDate={exif_dt}",
        str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"exiftool failed for {path}\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")


def set_file_times(path: Path, ts: int) -> None:
    """Set filesystem atime/mtime to the provided unix timestamp."""
    os.utime(path, (ts, ts))


def require_exiftool() -> None:
    """Exit with a helpful message if exiftool is not available."""
    try:
        subprocess.run(["exiftool", "-ver"], check=True, capture_output=True, text=True)
    except Exception:
        raise SystemExit("exiftool not found. Install it first (e.g., `brew install exiftool`).")


def iter_json_files(target: Path) -> list[Path]:
    """Return a sorted list of JSON files under a file or directory target."""
    if target.is_file():
        return [target]
    return sorted(p for p in target.rglob("*.json") if p.is_file())


def get_by_dotted_path(doc: dict, dotted_path: str):
    """Traverse a dict by dotted path segments, raising KeyError if any segment is missing."""
    cur = doc
    for part in dotted_path.split("."):
        if not part:
            continue
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(part)
        cur = cur[part]
    return cur


def discover_iterables(doc, uri_key: str, timestamp_keys: Sequence[str]) -> list[tuple[str, list]]:
    """Return list of (path, iterable) where iterable contains dicts with uri/timestamp."""
    paths: list[tuple[str, list]] = []

    def walk(node, path_parts: list[str]) -> None:
        if isinstance(node, list):
            if any(isinstance(item, dict) and uri_key in item for item in node):
                paths.append((".".join(path_parts) or "$", node))
            for item in node:
                walk(item, path_parts)
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(v, path_parts + [k])

    walk(doc, [])
    # Keep only paths where at least one timestamp is present
    filtered = []
    for p, items in paths:
        if any(isinstance(item, dict) and extract_timestamp(item, timestamp_keys) is not None for item in items):
            filtered.append((p, items))
    return filtered


def extract_timestamp(entry: dict, timestamp_keys: Sequence[str]) -> int | None:
    """Pull the first usable timestamp value from the provided key order."""
    for key in timestamp_keys:
        if key in entry and entry[key] is not None:
            try:
                return int(entry[key])
            except (TypeError, ValueError):
                continue
    return None


def iter_entries(doc: dict, uri_key: str, timestamp_keys: Sequence[str], entry_path: str | None):
    """
    Yield (dotted_path, iterable) pairs either from a user-specified path or by
    discovering list nodes containing uri/timestamp-bearing dict entries.
    """
    if entry_path:
        try:
            iterable = get_by_dotted_path(doc, entry_path)
        except KeyError as e:
            raise ValueError(f"Entry path not found: {entry_path} (missing key {e})")
        if not isinstance(iterable, Iterable):
            raise ValueError(f"Entry path {entry_path} does not resolve to an iterable")
        yield entry_path, iterable
        return

    discovered = discover_iterables(doc, uri_key, timestamp_keys)
    if discovered:
        for path, iterable in discovered:
            yield path, iterable
        return

    raise ValueError("Could not find any iterable containing items with a URI and timestamp")


@click.command()
@click.argument(
    "path",
    type=click.Path(path_type=Path, exists=True, readable=True),
    metavar="PATH",
)
@click.option(
    "--root",
    type=click.Path(path_type=Path, file_okay=False),
    default=".",
    show_default=True,
    help="Root directory to resolve relative URIs from the JSON.",
)
@click.option(
    "--entry-path",
    help="Dotted path to the iterable within the JSON (e.g., 'videos_v2' or 'albums.items').",
)
@click.option(
    "--uri-key",
    default="uri",
    show_default=True,
    help="Key name that stores the media URI in each entry.",
)
@click.option(
    "--timestamp-key",
    default="creation_timestamp",
    show_default=True,
    help="Primary key name for the timestamp in each entry.",
)
def main(path: Path, root: Path, entry_path: str | None, uri_key: str, timestamp_key: str):
    """
    Update EXIF and filesystem timestamps for media referenced in Facebook export JSON files.

    PATH may be a single JSON file or a directory containing JSON files (searched recursively).
    """
    require_exiftool()

    timestamp_keys = [timestamp_key] + [k for k in DEFAULT_TIMESTAMP_KEYS if k != timestamp_key]

    root = root.expanduser().resolve()
    json_files = iter_json_files(path.expanduser().resolve())
    if not json_files:
        raise SystemExit(f"No JSON files found under {path}")

    total = updated = missing = errors = 0

    for jf in json_files:
        try:
            doc = json.loads(jf.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[ERROR] Could not parse JSON: {jf} ({e})")
            errors += 1
            continue

        try:
            entry_sets = list(iter_entries(doc, uri_key, timestamp_keys, entry_path))
        except ValueError as e:
            print(f"[ERROR] {jf}: {e}")
            errors += 1
            continue

        for detected_path, iterable in entry_sets:
            for entry in iterable:
                if not isinstance(entry, dict):
                    continue
                uri = entry.get(uri_key)
                ts = extract_timestamp(entry, timestamp_keys)
                if not uri or ts is None:
                    continue

                total += 1
                media_path = (root / uri).resolve()

                if not media_path.exists():
                    print(f"[MISSING] {media_path}")
                    missing += 1
                    continue

                exif_dt = exif_dt_from_unix(ts)

                try:
                    run_exiftool(media_path, exif_dt)
                    set_file_times(media_path, int(ts))
                    updated += 1
                    print(f"[OK] {media_path}  ->  {exif_dt}Z (path: {detected_path})")
                except Exception as e:
                    print(f"[ERROR] {media_path}: {e}")
                    errors += 1

    print("\nSummary")
    print(f"  JSON files : {len(json_files)}")
    print(f"  Entries    : {total}")
    print(f"  Updated    : {updated}")
    print(f"  Missing    : {missing}")
    print(f"  Errors     : {errors}")


if __name__ == "__main__":
    main()
