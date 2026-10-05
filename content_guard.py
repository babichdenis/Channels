# -*- coding: utf-8 -*-
"""Final content guard for reposts and publication."""

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
    r"""(?:подпис(?:ывайтесь|аться|ка|ывайся)|присоединяйтесь|читайте нас|
    наш канал|мой канал|больше новостей|больше материалов|все новости|
    читать далее в канале|реклама|по рекламе|размещение рекламы|
    рекламная интеграция|переходи(?:те)? в канал|заходи(?:те)? в канал|
    ссылка на канал|источник\s*[:—-]\s*(?:@|t\.me|telegram\.me))""",
    re.I | re.X,
)

GENERIC_SIGNATURE_RE = re.compile(
    r"""^(?:[\W_]*)(?:источник|source|original|original post|via|
    подписывайтесь|присоединяйтесь|читать далее|читайте|больше новостей)
    (?:\W|$)""",
    re.I | re.X,
)


def _norm_source(value):
    value = (value or "").strip().lower().lstrip("@")
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


def sanitize_repost(text, source="", aliases=()):
    """Remove source/channel promotion while preserving the story."""
    text = unescape(text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = MARKDOWN_TG_RE.sub(
        lambda m: m.group(1) or m.group(2) or "", text
    )

    aliases_set = source_aliases(source, aliases)
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

        if not line:
            removed.append("empty_after_links")
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
