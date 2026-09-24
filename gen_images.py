#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_images.py — генерация картинок к постам очереди (pollinations.ai).

Для каждого поста берёт image_prompt_en (или image_prompt) и сохраняет
картинку в posts/<id>.jpg. Уже сгенерированные не трогает.

Запуск:
    python3 gen_images.py            # все посты без картинок
    python3 gen_images.py --limit 5  # первые 5
    python3 gen_images.py --id ns-2026-09-22-0900
"""

import argparse
import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(HERE, "queue.json")
POSTS = os.path.join(HERE, "posts")
API = "https://image.pollinations.ai/prompt/"


def generate(prompt, path, width=1024, height=1024, seed=0, timeout=180):
    url = "%s%s?width=%d&height=%d&nologo=true&seed=%d" % (
        API, urllib.parse.quote(prompt), width, height, seed)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    if len(data) < 5000:
        raise RuntimeError("слишком маленький ответ: %d байт" % len(data))
    image = Image.open(io.BytesIO(data)).convert("RGB")
    # обрезаем водяной знак pollinations в правом нижнем углу + выравниваем в квадрат
    image = image.crop((0, 0, image.width, int(image.height * 0.92)))
    side = min(image.width, image.height)
    left = (image.width - side) // 2
    image = image.crop((left, 0, left + side, side))
    image.save(path, "JPEG", quality=92)
    return os.path.getsize(path)


def main():
    ap = argparse.ArgumentParser(description="Генерация картинок к постам")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--id", default="")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    os.makedirs(POSTS, exist_ok=True)
    queue = json.load(open(QUEUE, encoding="utf-8"))
    made = 0
    for post in queue:
        if args.id and post["id"] != args.id:
            continue
        if args.limit and made >= args.limit:
            break
        path = os.path.join(POSTS, post["id"] + ".jpg")
        if os.path.exists(path) and not args.force:
            continue
        prompt = post.get("image_prompt_en") or post.get("image_prompt")
        if not prompt:
            continue
        try:
            size = generate(prompt, path, seed=abs(hash(post["id"])) % 100000)
            post["image"] = "posts/%s.jpg" % post["id"]
            made += 1
            print("✓ %s (%.1f КБ)" % (post["id"], size / 1024))
        except Exception as exc:
            print("✗ %s: %s: %s" % (post["id"], type(exc).__name__, str(exc)[:120]))
        time.sleep(1)
    if made:
        with open(QUEUE, "w", encoding="utf-8") as fh:
            json.dump(queue, fh, ensure_ascii=False, indent=2)
    print("сгенерировано:", made)
    return 0


if __name__ == "__main__":
    sys.exit(main())
