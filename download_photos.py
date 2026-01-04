import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import click
import requests
from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright
import subprocess

DEFAULT_JSON_RELATIVE = "your_facebook_activity/activity_youre_tagged_in/photos_and_videos_you're_tagged_in.json"
DEFAULT_STATE_FILE = "storage_state.json"
DEFAULT_OUTPUT_DIR = "downloaded"

# Facebook sometimes uses these for the main image:
CANDIDATE_SELECTORS = [
    "img[data-visualcompletion='media-vc-image']",
    "img[referrerpolicy='origin-when-cross-origin']",
    "img[src*='scontent']",
    "img[src*='fbcdn']",
]


def apply_file_times(path: Path, ts: int) -> None:
    """Apply the given timestamp to both the access and modification time of the file."""
    os.utime(path, (ts, ts))


def exif_datetime_str(ts: int, use_utc: bool = True) -> str:
    """Return a DateTimeOriginal-style string from a POSIX timestamp."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc) if use_utc else datetime.fromtimestamp(ts)
    return dt.strftime("%Y:%m:%d %H:%M:%S")


def apply_exif_metadata(path: Path, ts: int, tagger_name: str = "") -> None:
    """Write basic EXIF timestamps and tagger info to the downloaded photo."""
    dt = exif_datetime_str(ts, use_utc=True)  # set False if you want local time
    cmd = [
        "exiftool",
        "-overwrite_original",
        f"-XMP:Subject+=tagged_by: {tagger_name}",
        f"-DateTimeOriginal={dt}",
        f"-CreateDate={dt}",
        f"-ModifyDate={dt}",
        str(path),
    ]

    subprocess.run(cmd, capture_output=True, text=True)


def sanitize(name: str) -> str:
    """Normalize a filename so it is filesystem-safe and reasonably short."""
    name = re.sub(r"[^\w.-]+", "_", name).strip("_")
    return name[:180] if len(name) > 180 else name


def get_photo_page_url(item: dict) -> str | None:
    """Extract the photo page URL from a Facebook export entry."""
    for lv in item.get("label_values", []):
        if lv.get("label") == "URL":
            return lv.get("value") or lv.get("href")
    return None


def get_tagger_name(item: dict) -> str | None:
    """Return the user name that tagged you, if present."""
    for lv in item.get("label_values", []):
        if lv.get("label") == "Name":
            return lv.get("value")
    return None


def extract_best_image_src(page) -> str | None:
    """Try multiple selectors to locate the highest resolution image URL on the page."""
    page.wait_for_timeout(1500)

    # Try selectors in order
    for sel in CANDIDATE_SELECTORS:
        els = page.locator(sel)
        if els.count() > 0:
            best = None
            best_w = -1
            for i in range(min(els.count(), 10)):
                handle = els.nth(i)
                try:
                    src = handle.get_attribute("src")
                    if not src:
                        continue
                    w = handle.evaluate("el => el.naturalWidth || 0")
                    if w > best_w:
                        best_w = w
                        best = src
                except Exception:
                    continue
            if best:
                return best

    # Fallback: search all img tags for fbcdn/scontent and pick longest src
    imgs = page.locator("img")
    best = None
    for i in range(min(imgs.count(), 200)):
        src = imgs.nth(i).get_attribute("src")
        if not src:
            continue
        if "fbcdn" in src or "scontent" in src:
            if best is None or len(src) > len(best):
                best = src
    return best


def download(url: str, out_path: Path) -> None:
    """Stream the image from Facebook CDN to disk."""
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.facebook.com/",
    }
    with requests.get(url, stream=True, headers=headers, timeout=60) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)


def load_items(path: Path):
    """Load Facebook export JSON and return the list of tagged photo entries."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for _, v in data.items():
            if isinstance(v, list):
                return v
    raise ValueError("Unsupported JSON structure: expected a list or dict containing a list.")


def resolve_relative(root: Path, candidate: Path) -> Path:
    """Resolve a possibly-relative path against the Facebook export root."""
    return candidate if candidate.is_absolute() else root / candidate


def download_tagged_photos(
    root: Path,
    json_path: Path,
    state_file: Path,
    output_dir: Path,
    headless: bool = True,
) -> None:
    """Download tagged Facebook photos using a saved login session."""
    json_path = resolve_relative(root, json_path)
    state_file = resolve_relative(root, state_file)
    output_dir = resolve_relative(root, output_dir)

    if not json_path.exists():
        raise FileNotFoundError(f"JSON export not found: {json_path}")
    if not state_file.exists():
        raise FileNotFoundError(f"Storage state not found (run login_save.py first): {state_file}")
    output_dir.mkdir(parents=True, exist_ok=True)

    items = load_items(json_path)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(storage_state=str(state_file))
        page = context.new_page()

        ok = 0
        fail = 0

        for idx, item in enumerate(items, 1):
            fbid = str(item.get("fbid") or item.get("id") or f"item{idx}")

            ts = item.get("timestamp")
            if ts is None:
                print(f"[{idx}] SKIP (no timestamp) fbid={fbid}")
                fail += 1
                continue
            ts = int(ts)

            page_url = get_photo_page_url(item)

            if not page_url:
                print(f"[{idx}] SKIP (no URL) fbid={fbid}")
                fail += 1
                continue

            out_name = sanitize(f"photo_{fbid}.jpg")
            out_path = output_dir / out_name
            if out_path.exists() and out_path.stat().st_size > 0:
                print(f"[{idx}] EXISTS {out_path.name}")
                ok += 1
                continue

            print(f"[{idx}] Fetching page for fbid={fbid}")
            try:
                page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
            except PWTimeout:
                print(f"   TIMEOUT loading page: {page_url}")
                fail += 1
                continue

            page.wait_for_timeout(1200)

            img_src = None
            try:
                img_src = extract_best_image_src(page)
            except Exception as e:
                print(f"   ERROR extracting img src: {e}")

            if not img_src:
                print("   FAIL: could not find image src (login expired? privacy? layout changed)")
                fail += 1
                continue

            print(f"   Found img src: {img_src[:80]}...")
            tagger_name = get_tagger_name(item)
            try:
                download(img_src, out_path)
                apply_file_times(out_path, ts)
                apply_exif_metadata(out_path, ts, tagger_name)
                print(f"   Saved -> {out_path}")
                ok += 1
            except Exception as e:
                print(f"   DOWNLOAD FAIL: {e}")
                fail += 1

            time.sleep(0.5)

        browser.close()

    print(f"\nDone. ok={ok} fail={fail} output={output_dir.resolve()}")


@click.command()
@click.option(
    "--root",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("."),
    show_default=True,
    help="Root directory of the Facebook export (contains your_facebook_activity/).",
)
@click.option(
    "--json-path",
    type=click.Path(path_type=Path),
    default=DEFAULT_JSON_RELATIVE,
    show_default=True,
    help="Path to the tagged-photos JSON, relative to --root unless absolute.",
)
@click.option(
    "--state-file",
    type=click.Path(path_type=Path),
    default=DEFAULT_STATE_FILE,
    show_default=True,
    help="Playwright storage state produced by login_save.py.",
)
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_OUTPUT_DIR,
    show_default=True,
    help="Destination folder for downloaded photos (relative to --root unless absolute).",
)
@click.option(
    "--headless/--no-headless",
    default=True,
    show_default=True,
    help="Run Chromium headlessly while scraping photo pages.",
)
def main(root: Path, json_path: Path, state_file: Path, output_dir: Path, headless: bool) -> None:
    """CLI entry point for downloading Facebook photos you were tagged in."""
    download_tagged_photos(root, json_path, state_file, output_dir, headless=headless)


if __name__ == "__main__":
    main()
