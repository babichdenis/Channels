# SOURCE VERIFIER

Каждому материалу присваиваем источник и уровень доверия.

```json
{"source_type": "reddit|telegram|vk|direct|telemetr|official|docs|media",
 "source_url": "", "source_author": "", "published_at": "",
 "evidence_type": "official_feature|official_documentation|real_user_report|demonstrated_workflow|expert_test|media_report|community_claim|unverified_claim",
 "trust_level": 0, "verification_required": true}
```

## Trust levels
| Уровень | Что это |
|---|---|
| 5 | официальная документация / официальный источник |
| 4 | надёжный специализированный источник + демонстрация |
| 3 | реальный пользовательский кейс |
| 2 | соцпост без достаточных доказательств |
| 1 | пересказ пересказа |
| 0 | непроверенный AI-generated контент |

## Правила
- Утверждения о **конкретных возможностях моделей** подтверждаем официальным источником
  (блог/документация), а не соцпостом.
- Соцсети — сигнал спроса и пользовательского опыта, **не окончательное доказательство**.
- trust < 3 и это фактическое утверждение → `manual_review` или переформулировать без факта.
