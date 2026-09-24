# PROMPTS — модульный пакет сети

Подключается поверх `00_GLOBAL_RULES.md` (Level-1). Три уровня контекста:
Level-1 — общие правила · Level-2 — модуль канала · Level-3 — runtime (seed, память, метрики).

| Файл | Что внутри |
|---|---|
| `00_GLOBAL_RULES.md` | постоянные правила (Level-1) |
| `CHANNEL_NEURO_SECRET.md` | модуль флагмана «Нейро-секреты» |
| `CHANNEL_MAGIC_PROMPTS.md` | модуль библиотеки «Волшебные промпты» |
| `CHANNEL_NEURO_IMAGE.md` | модуль «Нейро-образ» (visual) |
| `CHANNEL_NEURO_WORK.md` | модуль «Нейро-работа» (utility) |
| `CHANNEL_NEURO_FUN.md` | модуль «Нейро-приколы» (reach) |
| `CONTENT_SEED.md` | Content Seed: одна идея → разные продукты |
| `NETWORK_ORCHESTRATOR.md` | оркестратор сети: матчинг seed → каналы |
| `CROSS_CHANNEL.md` | правила взаимодействия каналов (без каннибализации) |
| `MONETIZATION.md` | естественные точки спроса по каналам |
| `QUALITY_PACK.md` | дополнительный контроль качества (saveability/shareability/fit) |
| `CHANNEL_MODULES.md` | (объединённая версия модулей — справочно) |
| `SEED_ORCHESTRATOR.md` | (объединённая версия seed+оркестратора — справочно) |

Машиночитаемые параметры каналов: `../config/CHANNELS.json`.
