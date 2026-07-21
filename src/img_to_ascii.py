#!/usr/bin/env python3
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO

import requests
from PIL import Image

ASCII_CHARS = "@%#*+=-:. "
WIDTH = 72
UPSTREAM_URL = "https://raw.githubusercontent.com/keycap-archivist/database/refs/heads/master/db/catalog.json"

session = requests.Session()


def _ansi_color(r: int, g: int, b: int) -> int:
    if r == g == b:
        return 232 + int(r / 256 * 24)
    ri = round(r / 256 * 5)
    gi = round(g / 256 * 5)
    bi = round(b / 256 * 5)
    return 16 + 36 * ri + 6 * gi + bi


def download_image(url: str) -> Image.Image:
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    return Image.open(BytesIO(resp.content))


def image_to_ascii(img: Image.Image, width: int = WIDTH, color: bool = False) -> str:
    aspect = img.height / img.width
    height = max(int(aspect * width * 0.5), 1)
    img = img.resize((width, height), Image.LANCZOS)

    if color:
        lines = []
        for y in range(height):
            line = ""
            for x in range(width):
                r, g, b = img.getpixel((x, y))[:3]
                gray = int(0.299 * r + 0.587 * g + 0.114 * b)
                ch = ASCII_CHARS[min(int(gray / 256 * len(ASCII_CHARS)), len(ASCII_CHARS) - 1)]
                c = _ansi_color(r, g, b)
                line += f"\033[38;5;{c}m{ch}"
            lines.append(line + "\033[0m")
        return "\n".join(lines)

    img_gray = img.convert("L")
    img_gray = img_gray.resize((width, height), Image.LANCZOS)
    if hasattr(img_gray, "get_flattened_data"):
        pixels = list(img_gray.get_flattened_data())
    else:
        pixels = list(img_gray.getdata())
    lines = []
    for y in range(height):
        row = pixels[y * width : (y + 1) * width]
        line = "".join(
            ASCII_CHARS[min(int(p / 256 * len(ASCII_CHARS)), len(ASCII_CHARS) - 1)]
            for p in row
        )
        lines.append(line)
    return "\n".join(lines)


def process_colorway(cw: dict, use_color: bool = False) -> dict:
    img_url = cw.get("img", "")
    if not img_url:
        cw["ascii_art"] = None
        return cw
    try:
        img = download_image(img_url)
        cw["ascii_art"] = image_to_ascii(img, color=use_color)
    except Exception as e:
        print(f"  ERROR ({cw.get('name', '?')}): {e}", file=sys.stderr)
        cw["ascii_art"] = None
    return cw


def load_catalog(path_or_url: str):
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        print(f"Fetching catalog from {path_or_url} ...", file=sys.stderr)
        resp = session.get(path_or_url, timeout=60)
        resp.raise_for_status()
        return resp.json()
    with open(path_or_url) as f:
        return json.load(f)


def count_colorways(artists: list) -> int:
    return sum(
        len(s.get("colorways", []))
        for a in artists
        for s in a.get("sculpts", [])
    )


def process_catalog(data, use_color: bool = False, workers: int = 8, resume_file: str = None):
    artists = data if isinstance(data, list) else [data]
    total = count_colorways(artists)

    if total == 0:
        return artists if isinstance(data, list) else artists[0]

    # Load existing data for resume mode
    existing = None
    if resume_file and os.path.exists(resume_file):
        with open(resume_file) as f:
            existing = json.load(f)
        existing = existing if isinstance(existing, list) else [existing]
        print(f"Resume mode: loaded {count_colorways(existing)} existing colorways", file=sys.stderr)

    tasks = []
    skip_count = 0
    for ai, artist in enumerate(artists):
        for si, sculpt in enumerate(artist.get("sculpts", [])):
            for ci, cw in enumerate(sculpt.get("colorways", [])):
                # In resume mode, skip if already processed
                if resume_mode(cw, ai, si, ci, existing):
                    skip_count += 1
                    continue
                tasks.append((ai, si, ci, cw))

    if skip_count:
        print(f"Skipping {skip_count} already-processed colorways", file=sys.stderr)

    todo = len(tasks)
    if todo == 0:
        print("Nothing to process.", file=sys.stderr)
        return artists if isinstance(data, list) else artists[0]

    print(f"Processing {todo} colorways across {len(artists)} artists with {workers} workers...", file=sys.stderr)

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(process_colorway, cw, use_color): (ai, si, ci, cw) for ai, si, ci, cw in tasks}
        for f in as_completed(futures):
            done += 1
            ai, si, ci, cw = futures[f]
            artist = artists[ai]
            sculpt = artist["sculpts"][si]
            name = cw.get("name", "(unnamed)")
            print(f"[{done}/{todo}] {artist['name']} / {sculpt['name']} / {name}", file=sys.stderr)

    return artists if isinstance(data, list) else artists[0]


def resume_mode(cw: dict, ai: int, si: int, ci: int, existing: list | None) -> bool:
    if existing is None:
        return False
    if ai >= len(existing):
        return False
    artist = existing[ai]
    if si >= len(artist.get("sculpts", [])):
        return False
    sculpt = artist["sculpts"][si]
    if ci >= len(sculpt.get("colorways", [])):
        return False
    existing_cw = sculpt["colorways"][ci]
    # Only skip if ascii_art is present and not None
    return existing_cw.get("ascii_art") is not None


def main():
    use_color = False
    workers = 8
    incremental = False
    fetch = False
    positional = []

    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--color":
            use_color = True
        elif a == "--workers" and i + 1 < len(argv):
            i += 1
            workers = int(argv[i])
        elif a == "--incremental":
            incremental = True
        elif a == "--fetch":
            fetch = True
        elif a == "--output" and i + 1 < len(argv):
            i += 1
            positional.append(argv[i])
        elif a.startswith("--"):
            print(f"Unknown option: {a}", file=sys.stderr)
            sys.exit(1)
        else:
            positional.append(a)
        i += 1

    input_path = positional[0] if len(positional) > 0 else None
    output_path = positional[1] if len(positional) > 1 else None

    if fetch:
        source = UPSTREAM_URL
    elif input_path:
        source = input_path
    else:
        source = UPSTREAM_URL

    data = load_catalog(source)

    resume_file = output_path if incremental else None
    data = process_catalog(data, use_color=use_color, workers=workers, resume_file=resume_file)

    if output_path:
        with open(output_path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"Written to {output_path}", file=sys.stderr)
    else:
        json.dump(data, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
