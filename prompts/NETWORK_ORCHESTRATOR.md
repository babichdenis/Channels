# Оркестратор (шаги)

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
