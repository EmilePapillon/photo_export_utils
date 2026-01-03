#!/usr/bin/env python3
"""
Capture-time histogram generator.

Recursively scan --input-dir, extract capture timestamps via exiftool, and generate an
interactive Plotly histogram into --output-dir/index.html.

Binning:
- Initial: 12 evenly spaced bins across [min, max] timestamp range.
- Click a bin: zoom into that interval and re-bin into 12 bins.
- Back / Reset to navigate.

Robustness:
- ExifTool is restricted to known media extensions via -ext filters.
- Python also filters by extension as a safety net.
- Uses strict EXIF parsing first (datetime.strptime) to avoid dateutil mis-parses.
- Embeds Plotly.js into the HTML (offline/self-contained).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import click
import plotly.offline as pyo
from dateutil import parser as dtparser

# Preferred order: photos (EXIF) then videos (QuickTime)
TAGS_TO_TRY = [
    "DateTimeOriginal",
    "MediaCreateDate",
    "TrackCreateDate",
    "CreateDate",
]

# Explicit list of media extensions we want to include (lowercase).
MEDIA_EXTS = {
    ".jpg", ".jpeg", ".png", ".heic",
    ".mp4", ".mov", ".m4v",
    ".avi", ".mkv", ".webm",
}

HTML_TEMPLATE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Capture Time Histogram</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <script>
__PLOTLY_JS__
  </script>
  <style>
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      margin: 16px;
    }
    .row {
      display: flex;
      gap: 12px;
      align-items: center;
      flex-wrap: wrap;
      margin-bottom: 10px;
    }
    button {
      padding: 8px 12px;
      border-radius: 8px;
      border: 1px solid #ccc;
      background: #fff;
      cursor: pointer;
    }
    button:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }
    #status {
      color: #444;
      font-size: 14px;
      white-space: pre-wrap;
    }
    #chart {
      width: 100%;
      height: 72vh;
      min-height: 420px;
    }
  </style>
</head>
<body>
  <div class="row">
    <button id="btnBack" disabled>Back</button>
    <button id="btnReset">Reset</button>
    <div id="status"></div>
  </div>
  <div id="chart"></div>

<script>
(function() {
  const DATA = __DATA_JSON__; // epoch ms (UTC), sorted

  const initial = {
    startMs: __MIN_MS__,
    endMs: __MAX_MS__,
    bins: 12
  };

  const stack = [];
  let view = {...initial};

  const elChart  = document.getElementById("chart");
  const elStatus = document.getElementById("status");
  const btnBack  = document.getElementById("btnBack");
  const btnReset = document.getElementById("btnReset");

  let eventsBound = false;

  function setStatus(msg) { elStatus.textContent = msg; }
  function msToISODate(ms) { return new Date(ms).toISOString().slice(0, 10); }

  function buildBins(curView) {
    const bins = [];
    const start = curView.startMs;
    const end = curView.endMs;
    const n = curView.bins;

    const span = end - start;
    if (!isFinite(span) || span <= 0) return bins;

    const w = span / n;
    for (let i = 0; i < n; i++) {
      const a = start + i * w;
      const b = (i === n - 1) ? end : (start + (i + 1) * w);
      bins.push([a, b]);
    }
    return bins;
  }

  function countIntoBins(edges) {
    const counts = new Array(edges.length).fill(0);

    // DATA sorted: single pass
    let j = 0;
    for (let i = 0; i < DATA.length; i++) {
      const t = DATA[i];
      if (t < view.startMs || t >= view.endMs) continue;

      while (j < edges.length && t >= edges[j][1]) j++;
      if (j < edges.length && t >= edges[j][0] && t < edges[j][1]) counts[j]++;
    }
    return counts;
  }

  function ensureEventsBound(gd) {
    if (eventsBound) return;
    eventsBound = true;

    gd.on("plotly_click", function(ev) {
      if (!ev || !ev.points || ev.points.length === 0) return;
      const cd = ev.points[0].customdata;
      if (!cd) return;

      stack.push({...view});
      view = { startMs: cd.startMs, endMs: cd.endMs, bins: 12 };
      render();
    });

    btnBack.addEventListener("click", () => {
      if (stack.length === 0) return;
      view = stack.pop();
      render();
    });

    btnReset.addEventListener("click", () => {
      stack.length = 0;
      view = {...initial};
      render();
    });
  }

  function render() {
    const edges = buildBins(view);
    if (edges.length === 0) { setStatus("Bad range (no bins)."); return; }

    const counts = countIntoBins(edges);

    const mids = edges.map(([a,b]) => (a + b) / 2);
    const widths = edges.map(([a,b]) => (b - a));

    const customdata = edges.map(([a,b]) => ({
      startMs: a,
      endMs: b,
      start: msToISODate(a),
      end: msToISODate(b)
    }));

    const title =
      `Capture time: ${msToISODate(view.startMs)} → ${msToISODate(view.endMs)}  (bins: ${view.bins})`;

    const trace = {
      type: "bar",
      x: mids.map(ms => new Date(ms)),
      y: counts,
      width: widths,
      customdata: customdata,
      hovertemplate:
        "Start: %{customdata.start}<br>"
        + "End: %{customdata.end}<br>"
        + "Count: %{y}<extra></extra>"
    };

    const layout = {
      title: {text: title},
      xaxis: { title: "Time (UTC)", type: "date" },
      yaxis: { title: "Count" },
      margin: {l: 60, r: 20, t: 60, b: 80},
      bargap: 0.02
    };

    const p = Plotly.react(elChart, [trace], layout, {displayModeBar: true, responsive: true});
    Promise.resolve(p).then((gd) => ensureEventsBound(gd || elChart));

    const inRange = counts.reduce((a,b)=>a+b, 0);
    setStatus(
      `Items: ${DATA.length}\\n` +
      `Range: ${msToISODate(view.startMs)} → ${msToISODate(view.endMs)}\\n` +
      `In-range: ${inRange}\\n` +
      `Zoom depth: ${stack.length}`
    );

    btnBack.disabled = (stack.length === 0);
  }

  try {
    if (typeof Plotly === "undefined") throw new Error("Plotly is undefined.");
    if (!Array.isArray(DATA)) throw new Error("DATA is not an array.");
    if (DATA.length === 0) { setStatus("No timestamps found (DATA empty)."); return; }
    if (__MAX_MS__ <= __MIN_MS__) { setStatus("All timestamps identical (or invalid range)."); return; }
    render();
  } catch (e) {
    console.error(e);
    setStatus("JS error:\\n" + (e && e.stack ? e.stack : String(e)));
  }
})();
</script>
</body>
</html>
"""


def _is_media_sourcefile(rec: dict) -> bool:
    src = rec.get("SourceFile", "")
    if not src:
        return False
    return Path(src).suffix.lower() in MEDIA_EXTS


def _parse_capture_datetime(s: str) -> Optional[datetime]:
    """
    Parse ExifTool date strings reliably.

    Handles:
      - "YYYY:MM:DD HH:MM:SS"               (common EXIF)
      - "YYYY:MM:DD HH:MM:SS±HH:MM"         (sometimes for QuickTime / some metadata)
      - other formats via dateutil fallback

    Returns an aware datetime normalized to UTC.
    """
    if not s or not isinstance(s, str):
        return None

    s = s.strip()

    # Strict EXIF (no timezone)
    try:
        dt = datetime.strptime(s, "%Y:%m:%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    # EXIF with timezone offset like -04:00 or +01:00
    try:
        dt = datetime.strptime(s, "%Y:%m:%d %H:%M:%S%z")
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass

    # Fallback: ISO-ish, etc.
    try:
        dt = dtparser.parse(s)
    except Exception:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def _run_exiftool_json(input_dir: Path, exiftool_path: str) -> list[dict]:
    if not shutil.which(exiftool_path):
        raise RuntimeError(f"exiftool not found on PATH (looked for '{exiftool_path}').")

    args: list[str] = [
        exiftool_path,
        "-r",
        "-json",
        "-api", "LargeFileSupport=1",
        "-api", "QuickTimeUTC=1",     # improves video time interpretation (MOV/MP4)
        "-charset", "filename=utf8",
    ]

    # Only include real media extensions at the exiftool level.
    for ext in sorted(MEDIA_EXTS):
        args += ["-ext", ext.lstrip(".")]

    # Ask for all candidate tags (some files will only have video tags).
    for t in TAGS_TO_TRY:
        args.append(f"-{t}")

    args.append(str(input_dir))

    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"exiftool failed (code {proc.returncode}).\nSTDERR:\n{proc.stderr}")

    return json.loads(proc.stdout)


def _extract_timestamps_ms(records: list[dict]) -> list[int]:
    out: list[int] = []
    now_utc = datetime.now(timezone.utc)
    max_reasonable = int((now_utc.timestamp() * 1000) + 366 * 86400_000)  # now + 1 year
    min_reasonable_year = 1990

    for rec in records:
        if not _is_media_sourcefile(rec):
            continue

        raw_val = None
        chosen_tag = None
        for tag in TAGS_TO_TRY:
            if tag in rec and rec.get(tag):
                raw_val = rec.get(tag)
                chosen_tag = tag
                break

        if not raw_val:
            continue

        candidates: Iterable[str]
        if isinstance(raw_val, list):
            candidates = (str(x) for x in raw_val if x)
        else:
            candidates = (str(raw_val),)

        dt = None
        for c in candidates:
            dt = _parse_capture_datetime(c)
            if dt:
                break

        if not dt:
            continue

        if dt.year < min_reasonable_year:
            continue

        ms = int(dt.timestamp() * 1000)
        if ms > max_reasonable:
            continue

        out.append(ms)

    out.sort()
    return out


def _write_html(output_path: Path, timestamps_ms: list[int]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    plotly_js = pyo.get_plotlyjs()

    min_ms = timestamps_ms[0]
    max_ms = timestamps_ms[-1]
    if max_ms <= min_ms:
        max_ms = min_ms + 1

    html = (
        HTML_TEMPLATE
        .replace("__PLOTLY_JS__", plotly_js)
        .replace("__DATA_JSON__", json.dumps(timestamps_ms, separators=(",", ":")))
        .replace("__MIN_MS__", str(min_ms))
        .replace("__MAX_MS__", str(max_ms))
    )

    output_path.write_text(html, encoding="utf-8")


@click.command()
@click.option("--input-dir", type=click.Path(exists=True, file_okay=False, path_type=Path), required=True)
@click.option("--output-dir", type=click.Path(file_okay=False, path_type=Path), required=True)
@click.option("--exiftool", "exiftool_path", default="exiftool", show_default=True)
def main(input_dir: Path, output_dir: Path, exiftool_path: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    records = _run_exiftool_json(input_dir, exiftool_path)
    timestamps_ms = _extract_timestamps_ms(records)

    if not timestamps_ms:
        raise click.ClickException(
            "No usable capture timestamps found after filtering.\n"
            "Sanity check:\n"
            "  exiftool -s -DateTimeOriginal -CreateDate -MediaCreateDate -TrackCreateDate somefile"
        )

    out_html = output_dir / "index.html"
    _write_html(out_html, timestamps_ms)
    click.echo(f"Wrote: {out_html}")


if __name__ == "__main__":
    main()
