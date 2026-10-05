#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""watermark.py — вотермарки и вшитая реклама на картинках.

Картинки НЕ режем и НЕ выкидываем молча:
- мелкая метка в углу ( distilled handle, <3% площади) → блюр бокса, композиция цела;
- крупная вшитая реклама/бренд-шапка → картинку не берём (пост идёт текстом),
  источник помечается статистикой; если у канала ВСЕ картинки такие —
  канал выкидываем при добавлении (source_add.py) или вручную.

Детект — OCR (tesseract rus+eng). Без tesseract: только эвристика, check вернёт
{"ocr": False} — вызывающий код решает сам (source_add предупреждает и пропускает).
"""
import os
import re
import shutil
import sqlite3
import subprocess
import sys

try:
    from PIL import Image, ImageFilter
except ImportError:
    Image = None

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    from content_guard import source_aliases
except Exception:
    def source_aliases(source, aliases=()):
        return set()

TESS = shutil.which("tesseract")
VISION_BIN = os.path.join(HERE, "ocr_vision")  # swift, macOS Vision rus+eng

TG_RE = re.compile(r"(t\.me|telegram\.me)", re.I)
HANDLE_RE = re.compile(r"(?<![\w@])@[A-Za-z0-9_]{4,32}")
PROMO_RE = re.compile(
    r"подпис|присоедин|наш канал|мой канал|читайте нас|больше новостей|"
    r"реклама|по рекламе|переходи|заходи|жми|нажми",
    re.I)

# суммарная площадь затираемых боксов, выше — не трогаем (убьём кадр)
BLUR_MAX_AREA_PCT = 3.0


def ocr_available():
    return (bool(TESS) or os.path.exists(VISION_BIN)) and Image is not None


def _vision_words(path):
    """Vision (ocr_vision): строки с нормализованными боксами → пиксели."""
    words = []
    try:
        out = subprocess.run([VISION_BIN, path], capture_output=True, text=True, timeout=180)
    except Exception:
        return words
    if out.returncode != 0:
        return words
    W = H = 0
    for line in (out.stdout or "").splitlines():
        parts = line.split("\t")
        if parts[0] == "SIZE" and len(parts) >= 3:
            try:
                W, H = int(float(parts[1])), int(float(parts[2]))
            except ValueError:
                pass
            continue
        if len(parts) < 6 or W <= 0 or H <= 0:
            continue
        try:
            txt = parts[0].strip()
            x, y, w, h = float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])
            conf = float(parts[5]) * 100.0
        except ValueError:
            continue
        if conf < 30 or len(txt) < 2:
            continue
        x0 = int(x * W)
        y1 = int((1.0 - y) * H)
        x1 = int((x + w) * W)
        y0 = int((1.0 - y - h) * H)
        words.append({"text": txt, "x0": max(0, x0), "y0": max(0, y0),
                      "x1": min(W, x1), "y1": min(H, y1)})
    return words


def ocr_text(path, lang="rus+eng"):
    """Весь распознанный текст картинки одной строкой ("" если нечем/пусто)."""
    if os.path.exists(VISION_BIN):
        try:
            return re.sub(r"\s+", " ",
                          " ".join(w["text"] for w in _vision_words(path))).strip()
        except Exception:
            return ""
    if not ocr_available():
        return ""
    try:
        out = subprocess.run(
            [TESS, path, "stdout", "-l", lang, "--psm", "6"],
            capture_output=True, text=True, timeout=120)
    except Exception:
        return ""
    if out.returncode != 0:
        return ""
    return re.sub(r"\s+", " ", out.stdout or "").strip()


def ocr_words(path, lang="rus+eng"):
    """Слова с боксами: [{text, x0, y0, x1, y1}] в пикселях исходника."""
    if os.path.exists(VISION_BIN):
        try:
            return _vision_words(path)
        except Exception:
            return []
    words = []
    if not ocr_available():
        return words
    try:
        out = subprocess.run(
            [TESS, path, "stdout", "-l", lang, "--psm", "6", "tsv"],
            capture_output=True, text=True, timeout=120)
    except Exception:
        return words
    if out.returncode != 0:
        return words
    for line in (out.stdout or "").splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 12:
            continue
        try:
            conf = float(parts[10])
            txt = parts[11].strip()
            x0, y0 = int(parts[6]), int(parts[7])
            w, h = int(parts[8]), int(parts[9])
        except ValueError:
            continue
        if conf < 30 or not txt or len(txt) < 2:
            continue
        words.append({"text": txt, "x0": x0, "y0": y0, "x1": x0 + w, "y1": y0 + h})
    return words


def _norm(text):
    return re.sub(r"[^a-zа-яё0-9_ ]", " ", (text or "").lower())


def check_image(path, source=""):
    """Проверка картинки. dirty=True — вшитая реклама/метка канала.

    Возвращает dict: {ocr, dirty, reasons[], self_promo, overlay_chars, sample}.
    Без tesseract: {ocr: False, dirty: False}.
    """
    res = {"ocr": bool(ocr_available()), "dirty": False, "reasons": [],
           "self_promo": False, "overlay_chars": 0, "sample": ""}
    if not ocr_available():
        return res
    if not path or not os.path.exists(path):
        res["reasons"].append("no_file")
        return res
    try:
        text = ocr_text(path)
    except Exception:
        return res
    res["overlay_chars"] = len(text)
    res["sample"] = text[:120]
    if not text:
        return res
    low = text.lower()
    aliases = {a for a in source_aliases(source) if len(a) >= 4}
    tokens = _norm(text)
    if TG_RE.search(text) or HANDLE_RE.search(text):
        # свой handle/ссылка в углу — self_promo, чужой — promo
        mine = any(re.search(r"(?<![a-zа-яё0-9])" + re.escape(a) + r"(?![a-zа-яё0-9])",
                             tokens) for a in aliases)
        res["dirty"] = True
        res["self_promo"] = bool(mine and aliases)
        res["reasons"].append("self_handle" if res["self_promo"] else "tg_promo")
        return res
    if aliases and any(re.search(r"(?<![a-zа-яё0-9])" + re.escape(a) + r"(?![a-zа-яё0-9])",
                                 tokens) for a in aliases):
        # имя канала вшито в картинку (бренд-шапка) — реклама источника
        res["dirty"] = True
        res["self_promo"] = True
        res["reasons"].append("source_name_burned")
        return res
    if PROMO_RE.search(text):
        res["dirty"] = True
        res["reasons"].append("promo_words")
        return res
    return res


def dirty_boxes(path, source=""):
    """Боксы слов, из-за которых картинка dirty (для блюра)."""
    boxes = []
    if not ocr_available():
        return boxes
    aliases = {a for a in source_aliases(source) if len(a) >= 4}
    for w in ocr_words(path):
        t = w["text"]
        low = t.lower()
        hit = bool(TG_RE.search(t) or HANDLE_RE.search(t) or PROMO_RE.search(t))
        if not hit and aliases:
            tok = _norm(t)
            hit = any(a in tok for a in aliases if len(a) >= 4)
        if hit:
            boxes.append(w)
    return boxes


def blur_boxes(path, boxes, out=None):
    """Заблюривает боксы (мелкие метки). False — площадь большая, не трогаем."""
    if Image is None or not boxes or not os.path.exists(path):
        return False
    try:
        im = Image.open(path).convert("RGB")
    except Exception:
        return False
    w, h = im.size
    area = sum(max(0, b["x1"] - b["x0"]) * max(0, b["y1"] - b["y0"]) for b in boxes)
    if area / float(w * h) * 100.0 > BLUR_MAX_AREA_PCT:
        return False
    for b in boxes:
        pad = 3
        x0 = max(0, b["x0"] - pad)
        y0 = max(0, b["y0"] - pad)
        x1 = min(w, b["x1"] + pad)
        y1 = min(h, b["y1"] + pad)
        if x1 <= x0 or y1 <= y0:
            continue
        crop = im.crop((x0, y0, x1, y1)).filter(ImageFilter.GaussianBlur(radius=12))
        im.paste(crop, (x0, y0))
    im.save(out or path, "JPEG", quality=92)
    return True


def clean_for_post(rel_path, source="", media_dir=""):
    """Готовит картинку к посту: чистая → как есть; мелкая метка → блюр-копия;
    крупная реклама → \"\" (пост идёт текстом). Возвращает rel-путь или \"\"."""
    base = media_dir or os.path.join(HERE, "media")
    full = rel_path if os.path.isabs(rel_path) else os.path.join(HERE, rel_path)
    if not rel_path or not os.path.exists(full):
        return ""
    verdict = check_cached(rel_path if not os.path.isabs(rel_path) else rel_path, source)
    if not verdict.get("ocr", True) or not verdict.get("dirty"):
        return rel_path
    boxes = dirty_boxes(full, source)
    if boxes:
        root, _ext = os.path.splitext(full)
        cleaned = root + ".clean.jpg"
        if blur_boxes(full, boxes, cleaned):
            return os.path.relpath(cleaned, HERE) if not os.path.isabs(rel_path) else cleaned
    return ""


WM_CACHE = os.path.join(HERE, "wm_cache.json")


def _load_cache():
    try:
        import json as _json
        data = _json.load(open(WM_CACHE, encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cache(data):
    try:
        import json as _json
        tmp = WM_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            _json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, WM_CACHE)
    except Exception:
        pass


def check_cached(rel_path, source="", live=True):
    """Флаг из кэша (мгновенно) или живая проверка + запись в кэш.
    live=False — только кэш (для горячих циклов: карточки), промах = чисто."""
    cache = _load_cache()
    hit = cache.get(rel_path)
    if isinstance(hit, dict) and "dirty" in hit:
        return hit
    if not live:
        return {"dirty": False, "reasons": [], "self_promo": False,
                "overlay_chars": 0, "sample": ""}
    verdict = check_image(os.path.join(HERE, rel_path)
                          if not os.path.isabs(rel_path) else rel_path, source)
    cache[rel_path] = {"dirty": verdict["dirty"], "reasons": verdict["reasons"],
                       "self_promo": verdict["self_promo"],
                       "overlay_chars": verdict["overlay_chars"],
                       "sample": verdict["sample"][:80]}
    _save_cache(cache)
    return cache[rel_path]


def source_stats(db_path="", limit=0):
    """Доля грязных картинок по источникам (из items.wm_flag)."""
    db_path = db_path or os.path.join(HERE, "content.db")
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT source, COUNT(*), SUM(CASE WHEN wm_flag LIKE 'dirty%' THEN 1 ELSE 0 END) "
            "FROM items WHERE image_local != '' GROUP BY source ORDER BY COUNT(*) DESC").fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for source, total, dirty in rows:
        dirty = dirty or 0
        if limit and total < limit:
            continue
        out.append({"source": source, "total": total, "dirty": dirty,
                    "ratio": (dirty / total) if total else 0.0})
    return sorted(out, key=lambda r: (r["ratio"], r["total"]), reverse=True)


if __name__ == "__main__":
    import json
    if len(sys.argv) > 1 and sys.argv[1] == "--stats":
        print(json.dumps(source_stats(), ensure_ascii=False, indent=1))
    elif len(sys.argv) > 2 and sys.argv[1] == "--check":
        print(json.dumps(check_image(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else ""),
                         ensure_ascii=False, indent=1))
    else:
        print("watermark.py --check <image> [source] | --stats")
