# -*- coding: utf-8 -*-
"""Final content guard for reposts and publication."""

import json
import os
import re
from html import unescape

TG_URL_RE = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me)/[^\s)\]>]+", re.I)
TG_MENTION_RE = re.compile(r"(?<![\w@])@[A-Za-z0-9_]{4,32}")
MARKDOWN_TG_RE = re.compile(
    r"!\[([^\]]{1,200})\]\((?:https?://)?(?:t\.me|telegram\.me)/[^)]+\)"
    r"|\[([^\]]{1,200})\]\((?:https?://)?(?:t\.me|telegram\.me)/[^)]+\)",
    re.I,
)

PROMO_RE = re.compile(
    r"""(?:подпис(?:ывайтесь|аться|ка|ывайся|ись|итесь)|присоединяйтесь|читайте\s+нас|
    наш\s+канал|мой\s+канал|больше\s+новостей|больше\s+материалов|все\s+новости|
    читать\s+далее\s+в\s+канале|реклама|по\s+рекламе|размещение\s+рекламы|
    рекламная\s+интеграция|переходи(?:те)?\s+в\s+канал|заходи(?:те)?\s+в\s+канал|
    все\s+боты|наши\s+боты|наш\s+бот|тг\s+бот|tg\s+бот|телеграм\s+бот|
    ссылка\s+на\s+канал|источник\s*[:—-]\s*(?:@|t\.me|telegram\.me))""",
    re.I | re.X,
)

# Остаток инлайн-кнопки, скопированный текстом: «REELY AI ⬅ делала тут».
# Стрелка + дейктик (тут/здесь/сюда) в конце короткой строки.
BUTTON_LEFTOVER_RE = re.compile(
    r"[⬅➡️👉👈]\s*[\w\s]{0,20}?\s*(?:тут|здесь|сюда)\s*$",
    re.I,
)

GENERIC_SIGNATURE_RE = re.compile(
    r"""^(?:[\W_]*)(?:источник|source|original|original\s+post|via|
    подписывайтесь|присоединяйтесь|читать\s+далее|читайте|больше\s+новостей)
    (?:\W|$)""",
    re.I | re.X,
)

# Стрелка-указатель в конце строки (ссылку вырезали, стрелка осталась).
ARROW_TRAIL_RE = re.compile(r"\s*[➡️👉👈👆👇↗️➔→←↔⬆️⬇️◀️▶️👇🏻]+\s*$")

# Строка-указатель без ссылки: «в разделе», «читать далее:», «подробнее:» —
# после вырезания ссылки ей не на что указывать. Голое «источник» — только
# с двоеточием/тире (иначе сносили бы обычные фразы вида «единственный источник»).
DANGLING_PTR_RE = re.compile(
    r"(?:в\s+разделе|читать\s+далее|подробнее|по\s+ссылке|ссылка\s+ниже|"
    r"(?:источник|source)\s*[:—-])\s*[:…]?\s*$",
    re.I,
)


def _norm_source(value):
    value = (value or "").strip().lower().lstrip("@")
    value = re.sub(r"\s*\(копия\)\s*$", "", value)
    if value.startswith("ub_"):
        value = value[3:]
    return value


def source_aliases(source, aliases=()):
    out = set()
    for value in (source, *(aliases or ())):
        value = _norm_source(value)
        if value:
            out.add(value)
            out.add(value.replace("_", " "))
    return out


WATCH_PATH = os.path.expanduser("~/tguserbot/watch.json")
_WATCH_CACHE = {}

# Общие слова ниши — не алиасы (иначе сносили бы обычные строки).
ALIAS_STOP = {
    "нейросети", "нейросеть", "нейро", "промпты", "промпт", "промптер",
    "генерация", "генератор", "генераторы", "фото", "канал", "каналы",
    "новости", "новость", "бот", "боты", "работа", "искусственный",
    "интеллект", "ai", "ии", "the", "and", "for", "art", "pro",
}


def channel_aliases(source):
    """Названия канала-источника из watch.json (title/ref → слова).

    Ключ (`study24ai`) часто не совпадает с отображаемым именем («StudyAI»),
    поэтому без этого гард пропускает саморекламу. Кэш в памяти процесса.
    """
    global _WATCH_CACHE
    try:
        mtime = os.path.getmtime(WATCH_PATH)
    except OSError:
        return []
    if _WATCH_CACHE.get("mtime") == mtime:
        return _WATCH_CACHE.get(_norm_source(source), [])
    try:
        data = json.load(open(WATCH_PATH, encoding="utf-8"))
    except Exception:
        return []
    by_key = {}
    for w in data if isinstance(data, list) else []:
        if not isinstance(w, dict):
            continue
        key = _norm_source(w.get("key"))
        if not key:
            continue
        words = set()
        for field in (w.get("ref"), w.get("title")):
            for tok in re.findall(r"[a-zа-яё0-9]{4,}", (field or "").lower()):
                if tok not in ALIAS_STOP:
                    words.add(tok)
        by_key[key] = sorted(words)
        if key.startswith("ub_"):
            by_key[key[3:]] = sorted(words)
        else:
            by_key["ub_" + key] = sorted(words)
    _WATCH_CACHE = dict(by_key, mtime=mtime)
    return _WATCH_CACHE.get(_norm_source(source), [])


def sanitize_repost(text, source="", aliases=()):
    """Remove source/channel promotion while preserving the story."""
    text = unescape(text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = MARKDOWN_TG_RE.sub(
        lambda m: m.group(1) or m.group(2) or "", text
    )

    extra = list(aliases or ())
    if source:
        try:
            extra.extend(channel_aliases(source))
        except Exception:
            pass
    aliases_set = source_aliases(source, extra)
    kept = []
    removed = []

    for raw in text.split("\n"):
        line = re.sub(r"[ \t]+", " ", raw).strip()

        if not line:
            if kept and kept[-1] != "":
                kept.append("")
            continue

        low = line.lower()

        if TG_URL_RE.search(line) and (len(line) <= 240 or PROMO_RE.search(line)):
            removed.append("telegram_link")
            continue

        if PROMO_RE.search(line):
            removed.append("promo")
            continue

        if len(line) <= 80 and BUTTON_LEFTOVER_RE.search(line):
            removed.append("button_leftover")
            continue

        if GENERIC_SIGNATURE_RE.search(line) and len(line) <= 180:
            removed.append("signature")
            continue

        if aliases_set:
            tokens = re.sub(r"[^a-zа-яё0-9_ ]", " ", low)
            if any(
                re.search(
                    r"(?<![a-zа-яё0-9])" + re.escape(alias) +
                    r"(?![a-zа-яё0-9])",
                    tokens,
                )
                for alias in aliases_set
                if len(alias) >= 4
            ):
                if len(line) <= 180 and (
                    "http" in line or "@" in line or len(line.split()) <= 8
                ):
                    removed.append("source_name")
                    continue

        line = TG_URL_RE.sub("", line)
        line = TG_MENTION_RE.sub("", line)
        line = re.sub(r"[ \t]{2,}", " ", line)
        line = line.strip(" -—–·|•🔗").strip()

        # Висячая стрелка: ссылка вырезана, указатель остался («в разделе ➡️ Чат»
        # без ссылки). Внешние ссылки мы сохраняем (якорь — URL), а TG-хвосты —
        # под корень вместе со строкой-указателем.
        line = ARROW_TRAIL_RE.sub("", line).strip()
        if not line:
            removed.append("empty_after_links")
            continue
        if DANGLING_PTR_RE.search(line):
            removed.append("dangling_pointer")
            continue

        kept.append(line)

    while kept and kept[-1] == "":
        kept.pop()

    out = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return out, removed


def promo_artifacts(text):
    text = text or ""
    hits = []

    if TG_URL_RE.search(text):
        hits.append("telegram_url")
    if TG_MENTION_RE.search(text):
        hits.append("telegram_mention")
    if PROMO_RE.search(text):
        hits.append("promotion")

    return sorted(set(hits))


def contains_html_artifacts(text):
    return bool(
        re.search(r"</?(?:script|style|iframe|img|a)\b", text or "", re.I)
    )
