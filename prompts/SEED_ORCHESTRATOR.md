# CONTENT SEED + ORCHESTRATOR (Level-2/3)

## Content Seed

Seed — единица идеи, из которой сеть делает разные материалы.
Начинать не с «что написать в каждый канал», а с **«какую полезную идею донести»**.

```json
{
  "seed_id": "seed-001",
  "core_idea": "ИИ помогает собрать гардероб из вещей, которые уже есть",
  "audience_problem": "не знаю, что надеть",
  "desired_result": "получить несколько готовых образов",
  "category": "style",
  "evergreen": true,
  "risk_level": "low",
  "visual_potential": 8,
  "prompt_potential": 7,
  "humor_potential": 6,
  "work_potential": 1,
  "channels": ["neuro_secrets", "magic_prompts", "neuro_image", "neuro_fun"]
}
```

**Оценка seed:** usefulness, novelty, reusability, channel_fit, visual_potential, prompt_potential,
shareability, evergreen_score.

**Правило:** одна идея не обязана идти во все каналы. Адаптируем только там, где смена формата
создаёт самостоятельную ценность.

## Оркестратор (шаги)

1. **Анализ seed:** primary_problem, desired_result, content_type, novelty, потенциалы.
2. **Матчинг каналов:** для каждого — fit (0–10), reason, adaptation, priority (primary/secondary/none).
3. **Каннибализация:** не похоже ли на недавние посты; не занята ли тема другим каналом.
4. **План адаптаций:** {channel, format, hook, value, visual, cta} — у каждой свой hook/формат/CTA.
5. **Приоритет:** PRIMARY / SECONDARY / SKIP.
6. **Эксклюзивность:** если идея сильна для одного канала → `exclusive: true` (другим нельзя).

**Выход оркестратора:**

```json
{
  "seed_id": "seed-001",
  "decision": "use",
  "primary_channel": "neuro_secrets",
  "secondary_channels": ["magic_prompts", "neuro_image"],
  "excluded_channels": ["neuro_work"],
  "adaptations": [
    {"channel": "neuro_secrets", "format": "secret", "hook": "Не знаете, что надеть?...",
     "value": "5 образов из своих вещей", "cta": "попробуй", "visual": "коллаж образов"},
    {"channel": "magic_prompts", "format": "prompt", "hook": "...", "value": "готовый промпт",
     "cta": "скопируй", "visual": "пример"}
  ],
  "reasoning": ["..."],
  "risks": [],
  "experiment": null
}
```

## Cross-channel правила

1. Один seed не обязан идти во все каналы.
2. **Одинаковый текст — не адаптация.** Каждая версия: другой hook, формат, ценность, CTA, визуал.
3. Каждый канал имеет собственную функцию (discovery / library / visual / utility / reach).
4. Не заполнять контент-план ради плана.
5. Перелинковка — естественная и редкая: «Если хотите именно готовый промпт — он здесь...».
6. У seed есть `primary_channel` (самая полная версия), secondary и excluded.

## Монетизация (естественные точки спроса)

| Канал | Что монетизируется |
|---|---|
| НейроХитрости | реклама, подборки, партнёрки, premium |
| ПромптКлад | prompt packs, PDF/Notion, боты, подписка |
| Нейро-образ | наборы визуальных промптов, photo/style packs |
| Нейропомощник | шаблоны, workflow packs, мини-продукты |
| Нейро-приколы | только охват и перелив |

**Правило:** сначала полезный контент и доверие, потом монетизация. Реклама не ломает формат
и не маскируется под независимый совет.

## Quality Pack (дополнение к QC)

Критические провалы: нарушение safety; сомнительное утверждение без проверки; повтор недавнего;
несоответствие каналу; нет ценности; AI-filler; выдуманные факты; гарантии результата; кликбейт;
нарушение evergreen.

- **Human-like check:** «Написал бы такое живой редактор?» — если слишком гладко/шаблонно → в редактуру.
- **Saveability test:** есть ли причина сохранить (промпт, список, инструкция, шаблон, reference)?
- **Shareability test:** есть ли причина отправить другому (пригодится / смешно / неожиданно)?
- **Channel fit test:** качество ≠ соответствие каналу — проверяются оба.

## Три уровня контекста (не передавать всё сразу)

1. **System:** `00_GLOBAL_RULES.md`
2. **Channel:** модуль канала из `CHANNEL_MODULES.md` (+ `config/CHANNELS.json`)
3. **Runtime:** seed, recent_posts, content_memory, analytics, current_task
