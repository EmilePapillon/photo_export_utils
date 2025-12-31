#!/usr/bin/env python3
import json, subprocess, unicodedata
from pathlib import Path
from datetime import datetime, timezone
import re

IMAGE_EXTS = {".mov", ".mp4", ".jpg", ".jpeg", ".png", ".heic"}

def norm(s: str) -> str:
    """Normalize strings for comparison by NFC and replace non-breaking spaces."""
    return unicodedata.normalize("NFC", s.replace("\u00A0", " "))

def exif_missing(img: Path) -> bool:
    """Return True if DateTimeOriginal tag is absent on the media file."""
    r = subprocess.run(
        ["exiftool", "-DateTimeOriginal", "-s", "-s", "-s", str(img)],
        capture_output=True, text=True
    )
    return not r.stdout.strip()

def find_json(img: Path):
    """Find a nearby JSON sidecar whose stem matches the image stem after normalization."""
    base = norm(img.stem)

    # strip trailing _#_ (one or more digits)
    base = re.sub(r'_\d+_$', '', base)

    # strip trailing "modifie"
    base = re.sub(r'-modifie$', '', base)

    for j in img.parent.glob("*.json"):
        if norm(j.stem).startswith(base):
            return j
    return None

def extract_ts(jpath: Path):
    """Extract timestamp from Google sidecar JSON and format for EXIF."""
    data = json.loads(jpath.read_text(errors="ignore"))
    try:
        ts = data["photoTakenTime"]["timestamp"]
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc) \
             .strftime("%Y:%m:%d %H:%M:%S")
        return dt
    except KeyError:
        return None

def write_exif(img: Path, dt: str):
    """Write DateTimeOriginal to the target image using exiftool."""
    subprocess.run([
        "exiftool", "-overwrite_original",
        f"-DateTimeOriginal={dt}",
        "-d", "%Y:%m:%d %H:%M:%S",
        str(img)
    ])

def main():
    """Populate missing DateTimeOriginal tags from nearby JSON sidecars."""
    for img in Path(".").rglob("*"):
        if img.suffix.lower() not in IMAGE_EXTS:
            continue
        if not exif_missing(img):
            continue

        j = find_json(img)
        if not j:
            continue

        ts = extract_ts(j)
        if not ts:
            continue

        print(f"Fixing: {img}")
        write_exif(img, ts)

if __name__ == "__main__":
    main()
