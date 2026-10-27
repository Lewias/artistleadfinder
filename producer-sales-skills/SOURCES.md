# Источники и аудит

Основа — [louisblythe/Sales-Skills](https://github.com/louisblythe/Sales-Skills) (коммит `e0f13a6`, 24.01.2026, лицензия MIT, © Apparate). Исходный репозиторий не изменялся. Тексты не копировались: методики пересказаны и переложены под независимых артистов. Цены и правила взяты из Джарвиса (`~/Jarvis/knowledge/offers.md`).

## Что было в источнике
122 скилла примерно трёх типов:
- **~20 базовых навыков продаж** (qualifying-leads, objection-handling, closing, negotiation…). Классические B2B-методики: BANT/MEDDIC/CHAMP, LAER, SPIN, техники закрытия.
- **~20 про процесс** (copywriting, email-sequence, pricing-negotiation, deal-upselling…). Шаблоны писем, каденции, переговоры с procurement.
- **~80 «AI SDR bot»**. В основном Python-псевдокод: скоринг, роутинг, CRM-поля, звонки, войсмейлы, TCPA/ACMA, A/B-тесты, таймзоны.

Пересечения большие. Квалификация описана в 4–5 скиллах, follow-up в 5, цена и срочность в 5.

## Что взято и куда

| Наш скилл | Исходные скиллы | Что взяли | Что выбросили |
|---|---|---|---|
| conversation-analysis | conversation-summarization, entity-extraction, intent-detection, sentiment-analysis, conversation-pause-intelligence | типы намерений, мульти-намерения, «тишина думает vs остыл», структура карточки | ML-классификаторы, confidence-пороги в цифрах, CRM-интеграции |
| lead-qualification | qualifying-leads, lead-qualification-logic, budget-extraction-qualification, asking-effective-questions, ideal-customer-profile-matching, disqualification-messaging, buying-signal-amplification | BANT и SPIN, балльная шкала + светофор, косвенные вопросы о бюджете, вежливая дисквалификация | MEDDIC, buying committee, firmographics, роутинг |
| cold-outreach | copywriting, social-selling, personalization-at-scale, tone-matching, response-length-calibration, email-sequence | «про него, а не про себя», один вопрос, длина по каналу, подстройка тона, каждое касание с новым углом | LinkedIn, cold calls, подписи, B2B-триггеры (funding, найм) |
| follow-up | follow-up-discipline, ghost-recovery-sequences, conversation-resurrection, re-engagement-sequencing, prospect-fatigue-detection, drip-pacing-intelligence | ghost vs dead, повод для касания, лимит касаний и пауза, закрывающее сообщение, возобновление с новым поводом | статистика без источника («80% sales require 5+ follow-ups», «44% give up»), мультиканальные боты |
| objection-handling | objection-handling, objection-recognition, competitor-mention-handling, negative-sentiment-de-escalation, active-listening | LAER, «названное возражение ≠ настоящее», не принижать конкурентов, деэскалация | корпоративные возражения (вендор-контракты, пилоты) |
| negotiation | negotiation, pricing-negotiation, pricing-discussion-logic, scarcity-urgency-calibration, urgency-creation | ценность до цены, обмен вместо уступки, когда называть цену, только реальная срочность, право уйти | procurement, flinch, good cop/bad cop, anchoring на ROI |
| closing | closing, micro-commitment-stacking, cross-sell-upsell-detection, referral-request-timing | сигналы готовности, прямой вопрос / выбор / trial close, маленькие «да», апсейл по поводу | демо, пропозалы, puppy dog close |
| reference/guardrails | compliance-handling, legal-compliance-phrase-avoidance, handoff-detection | opt-out прежде всего, не обещать результат, когда звать человека | TCPA/ACMA/CAN-SPAM-специфика, время звонков |

## Что не взяли совсем
A/B-тесты, аналитика и прогнозы, territory planning, onboarding, enablement-инструменты, презентации, войсмейлы, звонки и календари, дедупликация каналов, доставляемость, мультиязычность, обучение бота на фидбеке. Для одного продюсера в переписке это лишнее или требует инфраструктуры.

## Что добавлено сверх источника
- Стадии сделки и пороги «забыт» под ритм продажи битов (`deal-stages.md`).
- Голос продюсера: американский английский без корпоратива и без перебора сленга (`voice.md`).
- Запрет выдумывать кейсы и цифры, работа только с черновиками (`guardrails.md`).
- Тестовые диалоги и проверка (`tests/`).
