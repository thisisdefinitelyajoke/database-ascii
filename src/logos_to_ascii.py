#!/usr/bin/env python3
import json
import os
import sys

from PIL import Image

from img_to_ascii import image_to_ascii

LOGOS_DIR = os.path.join(os.path.dirname(__file__), 'assets', 'img', 'logos')
OUTPUT = os.path.join(os.path.dirname(__file__), '..', 'db', 'logos.ascii.json')


def main():
    result = {}
    if not os.path.isdir(LOGOS_DIR):
        print(f"Logos dir not found: {LOGOS_DIR}", file=sys.stderr)
        sys.exit(1)
    for fn in sorted(os.listdir(LOGOS_DIR)):
        if not fn.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
            continue
        maker_id = os.path.splitext(fn)[0]
        fp = os.path.join(LOGOS_DIR, fn)
        try:
            img = Image.open(fp).convert("RGB")
            result[maker_id] = image_to_ascii(img, color=True)
        except Exception as e:
            print(f"Error processing {fn}: {e}", file=sys.stderr)

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, 'w') as f:
        json.dump(result, f, separators=(",", ":"))

    print(f"Generated logo ASCII for {len(result)} makers → {OUTPUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
