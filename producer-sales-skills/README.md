# Producer Sales Skills

Персональная система продаж для продюсера, который продаёт биты и лицензии независимым хип-хоп и R&B артистам в Instagram, iMessage и email.

Методики взяты из [Sales-Skills](https://github.com/louisblythe/Sales-Skills) (MIT) и переложены под артистов: что взяли и что выбросили — в [SOURCES.md](SOURCES.md). Скиллы готовят **только черновики и предложения**: ничего не отправляют и не меняют CRM без отдельного «да» (см. [reference/guardrails.md](reference/guardrails.md)).

## Скиллы

| Скилл | Когда срабатывает |
|---|---|
| [conversation-analysis](skills/conversation-analysis/SKILL.md) | «что с этим лидом?», разбор переписки → карточка: факты, оценка 0–100, этап, следующее действие |
| [lead-qualification](skills/lead-qualification/SKILL.md) | артист ответил; стоит ли продавать; какой вопрос задать; пора ли делать оффер |
| [cold-outreach](skills/cold-outreach/SKILL.md) | первое сообщение артисту в DM / iMessage / email и короткая цепочка касаний |
| [follow-up](skills/follow-up/SKILL.md) | «кому написать сегодня», забытые лиды, тишина после оффера, невыполненные обещания, возврат старых чатов |
| [objection-handling](skills/objection-handling/SKILL.md) | «дорого», «нет денег», «подумаю», «это скам?», «у меня есть продюсер» |
| [negotiation](skills/negotiation/SKILL.md) | торг, скидка, своя цена, рассрочка |
| [closing](skills/closing/SKILL.md) | «да», «send the link», оплата, доставка, апсейл |

## Что редактировать под себя (`reference/`)

| Файл | Что в нём |
|---|---|
| [offers.md](reference/offers.md) | офферы, цены, разрешённые уступки, оплата, **реальные кейсы** |
| [qualification.md](reference/qualification.md) | кто хороший лид, баллы за сигналы, шкала ready / hot / warm / cold / stop |
| [deal-stages.md](reference/deal-stages.md) | этапы сделки, следующее действие, через сколько дней лид «забыт», лимиты касаний |
| [voice.md](reference/voice.md) | голос, длина по каналу, запрещённые фразы |
| [message-examples.md](reference/message-examples.md) | образцы сообщений по ситуациям |
| [guardrails.md](reference/guardrails.md) | что нельзя никогда и когда спрашивать продюсера |

Все скиллы читают эти файлы при каждом запуске: поменял цену в `offers.md` — поменялось везде.

## Структура

```
producer-sales-skills/
├── .claude-plugin/
│   ├── plugin.json
│   └── marketplace.json
├── README.md
├── SOURCES.md            аудит исходных скиллов
├── reference/            редактируемые данные
│   ├── deal-stages.md
│   ├── guardrails.md
│   ├── message-examples.md
│   ├── offers.md
│   ├── qualification.md
│   └── voice.md
├── skills/
│   ├── closing/SKILL.md
│   ├── cold-outreach/SKILL.md
│   ├── conversation-analysis/SKILL.md
│   ├── follow-up/SKILL.md
│   ├── lead-qualification/SKILL.md
│   ├── negotiation/SKILL.md
│   └── objection-handling/SKILL.md
└── tests/
    ├── README.md
    ├── check.py          проверка структуры и оценка ответов
    ├── scenarios.json    10 тестовых диалогов с ожиданиями
    └── examples/         эталонные ответы на 6 сценариев
```

## Установка в Claude Code

Скиллы ссылаются на `../../reference/`, поэтому ставить надо папку целиком, как плагин, а не копировать отдельные `SKILL.md`.

Попробовать без установки (одна сессия):

```bash
claude --plugin-dir ~/artistleadfinder/producer-sales-skills
```

Установить насовсем: в интерактивном `claude` выполни две команды по очереди:

```
/plugin marketplace add ~/artistleadfinder/producer-sales-skills
/plugin install producer-sales@producer-sales-local
```

После правок в `reference/` или `skills/` перезапусти сессию. Для установленного плагина обнови его в `/plugin`.

## Как пользоваться

Вставь переписку и попроси:
- «Разбери эту переписку и скажи, что делать дальше» → conversation-analysis
- «Вот 15 чатов, кому писать сегодня?» → follow-up (режим поиска забытых лидов)
- «Он пишет "too expensive can u do 50", что ответить?» → objection-handling / negotiation
- «Напиши первое письмо артисту, вот его профиль» → cold-outreach

## Проверка

```bash
python3 tests/check.py
```

Как гонять свои ответы против сценариев — в [tests/README.md](tests/README.md).

## Перед первым использованием
1. Впиши реальные кейсы в `offers.md → Кейсы и доказательства`. Пока там пусто, скиллы на кейсы не ссылаются.
2. Подстрой пороги дней в `deal-stages.md` под свой ритм.
