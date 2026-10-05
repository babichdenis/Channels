# NEWS_PIPELINE_V2.md

## Новая модель новостей

```text
raw source
→ normalized source
→ event candidate
→ evidence
→ verified event
→ writer
→ content guard
→ media guard
→ approval
→ final gate
→ publish
```

## EVENT

```json
{
  "event": "конкретное событие",
  "actor": "кто",
  "change": "что изменилось",
  "event_date": "дата",
  "impact": "что получил пользователь",
  "evidence": [
    {
      "source": "openai_blog",
      "url": "...",
      "type": "primary",
      "supports": ["release", "price"]
    }
  ],
  "verification": "verified"
}
```

## Verification policy

### VERIFIED

- официальный первичный источник;
- или два независимых источника;
- или официальный источник + независимое подтверждение.

### PARTIAL

- одно качественное вторичное сообщение;
- есть конкретное событие;
- нет первичного подтверждения.

→ `manual_review`.

### REJECT

- слух;
- мнение;
- прогноз;
- пересказ без события;
- «готовится» без официального подтверждения;
- Telegram-пересказ без доказательства;
- старое событие под видом нового.

## Важно

Количество RSS-записей не равно количеству независимых источников.

Google News results нельзя считать независимыми автоматически.
