#!/usr/bin/env python3
import json
import os
import re
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


def sanitize_name(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9_\-. ]+", "_", s)
    s = re.sub(r"[ _]+", "_", s)
    return s.strip("_")


def ascii_path(dir: str, artist_name: str) -> str:
    return os.path.join(dir, f"{sanitize_name(artist_name)}.ascii.json")


def load_ascii_map(ascii_dir: str) -> dict:
    combined = {}
    if not os.path.isdir(ascii_dir):
        return combined
    for fn in os.listdir(ascii_dir):
        if fn.endswith(".ascii.json"):
            fp = os.path.join(ascii_dir, fn)
            with open(fp) as f:
                combined.update(json.load(f))
    return combined


def save_artist_ascii(ascii_dir: str, artist_name: str, data: dict):
    os.makedirs(ascii_dir, exist_ok=True)
    fp = ascii_path(ascii_dir, artist_name)
    with open(fp, "w") as f:
        json.dump(data, f, separators=(",", ":"))


def process_catalog(data, use_color: bool = False, workers: int = 8, ascii_dir: str = None, quiet: bool = False):
    artists = data if isinstance(data, list) else [data]
    total = count_colorways(artists)

    if total == 0:
        return artists if isinstance(data, list) else artists[0]

    ascii_map = {}
    if ascii_dir:
        ascii_map = load_ascii_map(ascii_dir)
        if not quiet:
            print(f"Resume mode: loaded {len(ascii_map)} cached ascii_art entries", file=sys.stderr)

    tasks = []
    skip_count = 0
    for ai, artist in enumerate(artists):
        for si, sculpt in enumerate(artist.get("sculpts", [])):
            for ci, cw in enumerate(sculpt.get("colorways", [])):
                cw_id = cw.get("id", "")
                existing = ascii_map.get(cw_id) if cw_id else None
                if existing is not None:
                    cw["ascii_art"] = existing
                    skip_count += 1
                    continue
                tasks.append((ai, si, ci, cw))

    if skip_count and not quiet:
        print(f"Skipping {skip_count} already-processed colorways", file=sys.stderr)

    todo = len(tasks)
    if todo == 0:
        if not quiet:
            print("Nothing to process.", file=sys.stderr)
        return artists if isinstance(data, list) else artists[0]

    if not quiet:
        print(f"Processing {todo} colorways across {len(artists)} artists with {workers} workers...", file=sys.stderr)

    artist_updates = {ai: {} for ai in range(len(artists))}
    done = 0
    reported_artists = set()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(process_colorway, cw, use_color): (ai, si, ci, cw) for ai, si, ci, cw in tasks}
        for f in as_completed(futures):
            done += 1
            ai, si, ci, cw = futures[f]
            artist = artists[ai]
            artist_name = artist['name']
            cw_id = cw.get("id", "")
            if cw.get("ascii_art") is not None:
                artist_updates[ai][cw_id] = cw["ascii_art"]
            if quiet:
                if artist_name not in reported_artists:
                    reported_artists.add(artist_name)
                    print(f"[{artist_name}]", file=sys.stderr)
            else:
                sculpt = artist["sculpts"][si]
                name = cw.get("name", "(unnamed)")
                print(f"[{done}/{todo}] {artist_name} / {sculpt['name']} / {name}", file=sys.stderr)

    if ascii_dir:
        for ai, artist in enumerate(artists):
            if artist_updates[ai]:
                save_artist_ascii(ascii_dir, artist["name"], artist_updates[ai])
        if not quiet:
            print(f"Written ascii files to {ascii_dir}/", file=sys.stderr)

    return artists if isinstance(data, list) else artists[0]


def main():
    use_color = False
    workers = 8
    incremental = False
    fetch = False
    output_path = None
    ascii_dir = None
    quiet = False
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
            output_path = argv[i]
        elif a == "--ascii-dir" and i + 1 < len(argv):
            i += 1
            ascii_dir = argv[i]
        elif a in ("--quiet", "--hide-output"):
            quiet = True
        elif a.startswith("--"):
            print(f"Unknown option: {a}", file=sys.stderr)
            sys.exit(1)
        else:
            positional.append(a)
        i += 1

    input_path = positional[0] if len(positional) > 0 else None

    if fetch:
        source = UPSTREAM_URL
    elif input_path:
        source = input_path
    else:
        source = UPSTREAM_URL

    data = load_catalog(source)

    if incremental:
        ascii_dir = ascii_dir or "db"

    data = process_catalog(data, use_color=use_color, workers=workers, ascii_dir=ascii_dir, quiet=quiet)

    if output_path:
        with open(output_path, "w") as f:
            json.dump(data, f, separators=(",", ":"))
        if not quiet:
            print(f"Written to {output_path}", file=sys.stderr)
    else:
        json.dump(data, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
