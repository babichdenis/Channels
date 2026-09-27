# -*- coding: utf-8 -*-
"""Вырезает рекламные блоки из текста поста.

Режет solicitation-блок «по поводу рекламы» вместе со всем, что после него,
строки-приглашения написать в личку и приватные инвайт-ссылки t.me/+…
Названия публичных нейросетей (Perplexity, Nano Banana, ChatGPT) НЕ трогаем.
"""
import re

# Заголовок рекламного блока. Всё после него — реклама, режем до конца.
AD_TAIL_RE = re.compile(
    r"(?:^|\n)[^\n]{0,60}?"
    r"(?:по\s+поводу\s+рекламы|относительно\s+рекламы|нас[её]т\s+рекламы|"
    r"про\s+рекламу|по\s+рекламе|реклам(?:а|у|е)\s+в\s+этом\s+канале)"
    r"[^\n]*", re.I)

# Отдельные рекламные строки, которые встречаются и без заголовка.
AD_LINE_RE = re.compile(
    r"(?:пишите?\s+в\s+(?:лс|личку|личные\s+сообщения|dm|директ)"
    r"|размещени[ея]\s+реклам"
    r"|реклам(?:а|у|е)\s+у\s+нас"
    r"|по\s+поводу\s+реклам"
    r"|https?://t\.me/\+[A-Za-z0-9_-]{8,}"
    r"|https?://t\.me/joinchat/[A-Za-z0-9_-]{8,})", re.I)


# Короткая соц-подпись в подвале: «Мы в VK», «Мы в MAX», «Мы в Telegram».
# Требуем короткую строку, иначе срезали бы обычный текст вида
# «Мы видим, что в VK мало кто пишет…».
SOCIAL_RE = re.compile(
    r"^[\W_]{0,6}(?:мы|наш\s+канал|наша\s+группа)\s+"
    r"(?:в|на|тут|здесь)\s+"
    r"(?:vk|вк|max|макс|telegram|телеграм(?:е|а|у|ом)?|"
    r"дзен(?:е|а|у|ом)?|dzen|tiktok|тикток(?:е|а)?|"
    r"youtube|ютуб(?:е|а)?|instagram|инстаграм(?:е|а)?)"
    r"[\W_]{0,6}$", re.I)


def strip_ad_blocks(text):
    """Возвращает текст без рекламных блоков. Текст без рекламы не меняется."""
    if not text:
        return ""
    m = AD_TAIL_RE.search(text)
    if m:
        text = text[: m.start()]
    kept = [ln for ln in text.split("\n")
            if not AD_LINE_RE.search(ln) and not SOCIAL_RE.match(ln.strip())]
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(kept))
    return out.strip()


# ── 1. снятие HTML ─────────────────────────────────────────────────────
EMOJI_RE = re.compile(r'<tg-emoji emoji-id="\d+">(.*?)</tg-emoji>', re.S)
TAG_RE = re.compile(r"<[^>]+>")
# блоки, которые Telegram не любит: expandable и т.п. оставляем как <blockquote>
BLOCKQUOTE_RE = re.compile(r"<blockquote[^>]*>", re.I)
ENTITY_RE = re.compile(r"&(nbsp|amp|lt|gt|quot|#39);")
ENT = {"nbsp": " ", "amp": "&", "lt": "<", "gt": ">", "quot": '"', "#39": "'"}


def strip_html(text):
    """HTML → читаемый текст. Telegram-эмодзи превращаем в обычные символы."""
    if not text:
        return ""
    t = EMOJI_RE.sub(r"\1", text)
    t = BLOCKQUOTE_RE.sub("<blockquote>", t)
    t = TAG_RE.sub(" ", t)
    t = ENTITY_RE.sub(lambda m: ENT.get(m.group(1), " "), t)
    t = t.replace("ㅤ", " ").replace("", "")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


# ── 2. рекламные блоки (многострочные, по якорям) ───────────────────────
PROMO_RE = re.compile(
    r"(?is)"
    r"(?:"
    r"\+?\s*\d+\s*(?:бесплатн\w+\s*)?кредит\w*"
    r"|за\s+подписк\w*"
    r"|подписк\w*\s+на\s+канал"
    r"|по\s+подписке"
    r"|за\s+подписку"
    r"|промокод"
    r"|в\s+1\s*клик"
    r"|одним\s+кликом"
    r"|в\s+один\s+клик"
    r"|бонус\w*\s+(?:кредит|токен|генерац)"
    r"|перв\w*\s+дн\w*\s+бесплатн\w*"
    r"|пробн\w*\s+период"
    r"|действу\w*\s+до\s+\d"
    r"|успей\w*\s+(?:купить|подписаться|попробовать)"
    r"|скидк\w*\s+\d+\s*%"
    r"|читте?р?\w*\s*/\s*@\w+"
    r"|пишите?\s+в\s+л[сc]\w*"
    r"|\bи\s+web[\s\W]*ресурс"
    r"|\bweb[\s\W]*ресурс"
    r"|\bв\s+комментариях\b"
    r"|\bкомментариях\s*[:!]?\s*$"
    r"|\bбельё\s+для\s+промпта\b"
    r")")

# Строки-CTA: «Заходим в Syntx …», «Пользуйтесь😉», «Сгенерировать можно в …».
# В начале допускаем эмодзи/стрелки/буллеты — они часто стоят перед текстом.
CTA_LINE_RE = re.compile(
    r"(?i)^[\W_]{0,8}(?:"
    r"(?:заходите|зайди|заходим|заходи|переходите|перейди|идите)\b.*"
    r"|.*\bсгенерировать\s+можно\b.*"
    r"|.*\bсгенерируй(те)?\b.*"
    r"|.*\bпопробуй(те)?\s+(?:сам|его|их|е[её])\b.*"
    r"|пользуйтесь[\s\W]*"
    r"|.*\bработает\s+без\s+впн\b.*"
    r"|.*\bбез\s+впн\b.*"
    r")\s*$")

# Шелуха: разделители, футеры каналов, реакции под постом.
SEP_RE = re.compile(r"^[➗➖─━=\-—_·•\.\s]{3,}$")
CHANNEL_FOOTER_RE = re.compile(
    r"(?i)^[\W_]{0,8}(?:"
    r".*\bии\s+лайфхак\w*"
    r"|.*\byoutube\s+канал\w*"
    r"|.*\bboot\s?camp\w*"
    r"|.*\bboost\w*"
    r"|.*\bтариф\w*\s+премиум\w*"
    r"|(?:наш\s+|наша\s+)?(?:канал|чат|бот|сообщество|группа)\b.*"
    r"|ии\s+лайфхак\w*"
    r"|youtube\s+канал\w*"
    r"|дзен\w*"
    r"|boost\w*"
    r"|bootcamp\w*"
    r"|подписывайтесь\w*"
    r"|подпишись\w*"
    r"|жми(те)?\s+сюда\w*"
    r"|ссылка\s+в\s+описании\w*"
    r")[\W_]{0,8}$")
REACTION_RE = re.compile(
    r"^[\W_]{0,6}[🔥❤️💙💚💜💛🖤🤍🤎😈😍🥰👍🙏💔🤔😉😄🥳🎉✨⚡]{1,4}\s*[,\-–—]\s*\S")


def strip_promo(text):
    """Реклама и шелуха из ЧИСТОГО текста (результат strip_html)."""
    out = []
    for ln in (text or "").split("\n"):
        raw = ln.strip()
        if not raw:
            out.append("")
            continue
        if SEP_RE.match(raw):
            continue
        if CTA_LINE_RE.match(raw):
            continue
        if CHANNEL_FOOTER_RE.match(raw):
            continue
        if REACTION_RE.match(raw):
            continue
        if PROMO_RE.search(raw):
            # «Генерация в 1 клик — 25 кредитов на 24 часа» — это реклама
            # целиком, даже если формально строка не только про рекламу.
            if re.search(r"\d+\s*кредит", raw, re.I) or "в 1 клик" in raw.lower() \
               or "одним кликом" in raw.lower():
                continue
            continue
        out.append(raw)
    t = "\n".join(out)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def escape(text):
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def clean_post_text(text, keep_html=True):
    """Полная очистка поста для карточки/публикации.

    Возвращает текст, безопасный для parse_mode=HTML: промпт и суть
    сохраняются, реклама/кредиты/футеры/реакции вырезаются.
    """
    plain = strip_html(text)
    clean = strip_promo(plain)
    if not clean.strip():
        # Агрессивная очистка съела всё — не откатываем её (иначе реклама
        # вернётся в карточку), а вырезаем рекламные ПРЕДЛОЖЕНИЯ.
        kept = []
        for sent in re.split(r"(?<=[.!?…])\s+|\n+", plain):
            sent = sent.strip()
            if not sent:
                continue
            if PROMO_RE.search(sent) or CTA_LINE_RE.match(sent) \
               or CHANNEL_FOOTER_RE.match(sent) or REACTION_RE.match(sent) \
               or SEP_RE.match(sent):
                continue
            kept.append(sent)
        clean = "\n".join(kept)
    if keep_html:
        return escape(clean)
    return clean
