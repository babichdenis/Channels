# -*- coding: utf-8 -*-
"""Smoke-тест AI-провайдеров: endpoint живой / нужен ключ / геоблок / мёртв.

Коды: 200 — полностью жив; 400/401/403 — жив, нужен ключ;
451 — геоблок; 000/таймаут — мёртв или заблокирован.

Запуск:  python3 tools/provider_check.py            # только напрямую
        python3 tools/provider_check.py --proxy    # только через прокси 13128
        python3 tools/provider_check.py --both     # оба режима (по умолчанию)
        python3 tools/provider_check.py --both --repeat 3   # 3 попытки, вердикт по большинству
"""
import json
import ssl
import sys
import time
import urllib.error
import urllib.request

PROXY = "http://127.0.0.1:13128"

PROVIDERS = [
    ("local-deepseek", "http://127.0.0.1:3000/v1/models", "OPENAI_KEY"),
    ("local-glm",      "http://127.0.0.1:3001/v1/models", "OPENAI_KEY"),
    ("ollama",         "http://127.0.0.1:11434/api/tags", "-"),
    ("groq",           "https://api.groq.com/openai/v1/models", "GROQ_API_KEY"),
    ("gemini",         "https://generativelanguage.googleapis.com/v1beta/models", "GEMINI_API_KEY"),
    ("cerebras",       "https://api.cerebras.ai/v1/models", "CEREBRAS_API_KEY"),
    ("mistral",        "https://api.mistral.ai/v1/models", "MISTRAL_API_KEY"),
    ("sambanova",      "https://api.sambanova.ai/v1/models", "SAMBANOVA_API_KEY"),
    ("cohere",         "https://api.cohere.com/v1/models", "COHERE_API_KEY"),
    ("nvidia",         "https://integrate.api.nvidia.com/v1/models", "NVIDIA_API_KEY"),
    ("github-models",  "https://models.github.ai/models", "GITHUB_TOKEN"),
    ("openrouter",     "https://openrouter.ai/api/v1/models", "OPENROUTER_API_KEY"),
    ("huggingface",    "https://huggingface.co/api/models?limit=1", "HF_TOKEN"),
    ("modelscope",     "https://api-inference.modelscope.cn/v1/models", "MODELSCOPE_TOKEN"),
    ("siliconflow",    "https://api.siliconflow.cn/v1/models", "SILICONFLOW_API_KEY"),
    ("zhipu",          "https://open.bigmodel.cn/api/paas/v4/models", "ZHIPU_API_KEY"),
]

# «Живой» = дошёл до приложения: ключа просто нет
ALIVE = (200, 400, 401, 403, 405, 422)


def probe(url, key, via_proxy, timeout=25):
    hdrs = {"User-Agent": "curl/8", "Accept": "*/*"}
    if key != "-" and not key.startswith("OPENAI_KEY"):
        hdrs["Authorization"] = "Bearer x"
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}) if via_proxy
        else urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )
    try:
        r = opener.open(urllib.request.Request(url, headers=hdrs), timeout=timeout)
        return r.status, "", r.headers.get("content-type", "")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "ignore")[:90].replace("\n", " ")
        except Exception:
            pass
        return e.code, body, ""
    except Exception as e:
        return 0, type(e).__name__ + ": " + str(e)[:70], ""


def verdict(code, body):
    if code == 0:
        return "мёртв/таймаут", "BAD"
    if code == 451:
        return "геоблок", "BAD"
    if code in ALIVE:
        if code == 200:
            return "жив, /models без ключа", "OK"
        return "жив, нужен ключ", "KEY"
    return "HTTP %d" % code, "?"


def main():
    args = set(sys.argv[1:])
    # --repeat N: N попыток на маршрут, вердикт по большинству (одиночная
    # проба врёт на флейках — видели zhipu 401 -> SSL-таймаут -> 401)
    repeat = 1
    for i, a in enumerate(sys.argv):
        if a == "--repeat" and i + 1 < len(sys.argv):
            repeat = max(1, int(sys.argv[i + 1]))
    both = not args or "--both" in args
    want_proxy = "--proxy" in args or both
    want_direct = "--direct" in args or both

    print("%-14s %-7s %-7s %-20s %s" % ("провайдер", "режим", "код", "вердикт", "деталь"))
    print("-" * 96)

    res = {}
    for name, url, key in PROVIDERS:
        # локальные адреса не проксируются: tinyproxy на VPS не знает нашу сеть
        local = url.startswith("http://127.0.0.1") or url.startswith("http://localhost")
        for mode, enabled in (("direct", want_direct), ("proxy", want_proxy)):
            if not enabled:
                continue
            if local and mode == "proxy":
                print("%-14s %-7s %-7s %-20s %s" % (name, mode, "-", "локальный, прокси не нужен", ""))
                continue
            t0 = time.time()
            seen, body = [], ""
            for _ in range(repeat):
                c, b, _ = probe(url, key, via_proxy=(mode == "proxy"))
                seen.append(c)
                if c in ALIVE and not body:
                    body = b
            # вердикт по самому частому коду; при равенстве приоритет у живых
            tally = {}
            for c in seen:
                tally[c] = tally.get(c, 0) + 1
            best = sorted(tally.items(), key=lambda kv: (-kv[1], 0 if kv[0] in ALIVE else 1))[0][0]
            v, mark = verdict(best, body)
            dt = "%.1fs" % (time.time() - t0)
            shown = str(seen[0]) if repeat == 1 else ("%s x%d" % (best, tally[best]))
            res.setdefault(name, {})[mode] = (best, mark)
            print("%-14s %-7s %-7s %-20s %s"
                  % (name, mode, shown, v, (body or dt)[:40]))

    print("\nЛегенда: OK — /models отвечает без ключа; KEY — жив, нужен бесплатный ключ; "
          "BAD — геоблок/мёртв")
    print("\n══ Итог: какой маршрут использовать ══")
    for name, modes in res.items():
        d = modes.get("direct")
        p = modes.get("proxy")
        if d and p:
            if d[1] == "BAD" and p[1] != "BAD":
                how = "ТОЛЬКО ЧЕРЕЗ ПРОКСИ (напрямую %s)" % ("геоблок" if d[0] == 451 else "отказ")
            elif d[1] != "BAD" and p[1] == "BAD":
                how = "ТОЛЬКО НАПРЯМУЮ (прокси ломает: таймаут)"
            elif d[1] != "BAD":
                how = "любой маршрут (оба живы)"
            else:
                how = "НЕДОСТУПЕН"
        elif d:
            how = "напрямую"
        elif p:
            how = "только через прокси"
        else:
            how = "не проверен"
        print("  %-14s %s" % (name, how))


if __name__ == "__main__":
    main()
