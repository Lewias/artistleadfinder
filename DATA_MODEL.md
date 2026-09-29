# Модель данных

Веб-поиск через Brave удалён 27.09.2026. У лидов, найденных им раньше, в
SearchJob.keywords и LeadAnalysis.extracted_signals.browser_capture.discovery
остаются исторические данные; приложение их больше не пишет и не показывает.
Скаутинг добавляет схему 3.

Миграция 3: `scout_sources` (url PK, enabled), `scout_runs` (job_id FK/PK,
tasks JSON, observations JSON, notices JSON), `scout_posts` (url PK, source,
caption, published_at nullable, mentions JSON, captured_at), `scout_assessments`
(lead_id FK/PK, eligible, priority, details JSON, updated_at).
Порядок шагов синхронизирован с browser_queues.urls/cursor; источники и публикации
читаются до профилей. Лимиты: 20 источников, 12 публикаций на источник,
100 уникальных кандидатов, 30 наблюдений на кандидата. Прочитанные подписи
кешируются; пропущенные/неподтверждённые публикации не помечаются прочитанными.
LeadSource получает instagram_scout/publication с URL подтверждающей публикации.
Три оценки и причины хранятся в scout_assessments, общая lead_score остаётся
совместимой с прежними фильтрами. В списке/карточке оценки различаются явно.
Миграция добавляет таблицы и не удаляет старые leads/CRM. Старый exe отклоняет
базу схемы 3. CSV пока содержит прежнюю общую оценку, без трёх сервисных оценок.

SQLite foreign keys включены на каждом соединении. UTC timestamps.
Числовые диапазоны валидируются Pydantic. JSON-поля имеют schema version.

`search_jobs`: id, name, status (queued/running/paused/completed/failed/
cancelled), seed_accounts, keywords, hashtags, genres, min_followers,
max_followers, activity_days, minimum_score, target_leads,
candidates_found, profiles_analyzed, artists_detected, qualified_leads,
created_at, started_at, completed_at, stage, errors. Index status/created_at.

`leads`: id, platform, nullable platform_user_id, normalized username,
display_name, profile_url, avatar_url, bio, followers, following,
is_verified, is_private, external_url, artist_probability,
primary_genre, genres, last_activity_at, lead_score, status
(new/reviewed/qualified/rejected/contacted), created_at, updated_at.
Unique(platform, platform_user_id) где id известен; unique(platform,
normalized username). Сопоставление id и fallback username выполняется
в транзакции; стабильный id важнее username, переименование учитывается.
Indexes: score/id, followers/id, activity/id, created_at/id, genre/status.

`lead_sources`: id, lead_id FK, search_job_id FK, source_provider,
source_type (seed/keyword/hashtag/import/profile), source_value, created_at.
Unique(lead_id, search_job_id, provider, type, value). Один lead имеет
много источников; повторное обнаружение не создаёт второй lead.

`lead_analysis`: lead_id FK unique, schema_version, classifier,
is_artist, confidence, primary_genre, genres, signals, extracted_signals,
analyzed_at. `lead_score_breakdowns`: lead_id FK, rule, points, reason,
weights_version, scored_at; unique(lead_id, rule).

Schema migration 2 adds `browser_queues`: job_id FK/PK, profile_id, urls JSON,
cursor, weights JSON (snapshot at start), last_error. Queue status/counters and
timestamps live in search_jobs. Existing leads are preserved by the additive
migration. Older executables reject schema 2.
Browser observations are stored under lead_analysis.extracted_signals.browser_capture:
captured_at, url, unknown_fields, bounded description/header and extraction method.
Unknown numeric fields retain the legacy non-null database representation but
are marked unavailable in list/detail responses and exported as empty cells.

`settings`: key PK, value JSON, updated_at. Только несекретные данные.
`provider_health`: provider PK, status, last_request_at,
last_success_at, last_error, official_rate_limit. `schema_migrations`:
version PK, applied_at. Cascade cleanup применяется только к дочерним
analysis/breakdown/source, не к leads при удалении search job.

CSV экспорт читает данные порциями. Фильтры и сортировка используют
allowlist полей; values передаются bind parameters. UI не загружает всю БД.

Миграция 5 (Lead Scout): `scout_processed_profiles` (username PK, источник,
метод, результат, причина), `scout_processed_posts` (shortcode PK),
`scout_processed_stories`, `scout_ai_cache` (username PK, категория,
уверенность, модель; удалена в миграции 8), `scout_state` (курсор ротации источников),
`scout_events` (события и журнал запусков, хранятся 30 дней) и
`lead_scout_profiles` (lead_id PK: instagram_id, posts_count, emails, phones,
profile_type/score/confidence/reasons, source_username, discovery_method,
origin_url, ai_model/ai_confidence, first/last_seen_at). В `scout_sources`
добавлены last_scanned_at, status, leads_found, added_at; в `scout_runs` — stats.

Миграция 6: в `scout_processed_posts` и `scout_processed_stories` добавлены
status (processed | unavailable | failed), attempts и last_error. Ключи:
shortcode для публикаций источника, `tagged:<source>:<shortcode>` для отмеченных,
`story:<source>:<media id>` для историй. Публикация помечается обработанной только
после завершённого разбора; сбойная повторяется до `scout_max_item_failures` раз.

Миграция 7: `scout_profile_cache` (username PK, profile — нормализованный профиль
в JSON, source api | browser | merged, resolved_at). Профиль из кэша моложе
`scout_profile_cache_hours` (по умолчанию 12 ч) не запрашивается у Instagram
повторно. Сырые ответы Instagram, cookies и заголовки не хранятся.

Миграция 8 (классификатор профилей): `scout_ai_classifications` (profile_hash PK —
sha256 нормализованных username, bio, категории, ссылок, подписей и версии
классификатора; username, category, confidence, model, classifier_version,
created_at) заменяет `scout_ai_cache`; ответы другой версии классификатора не
используются. `scout_pending_ai_jobs` (profile_hash PK, username, profile — поля для
классификатора, context — задача скаута, attempt_count, last_error, retry_at,
created_at): AI-классификации, сорвавшиеся из-за временной ошибки, повторяются с
экспоненциальной задержкой (1, 2, 4… мин, не больше часа, до 5 попыток). В
`lead_scout_profiles` добавлено profile_decided_by (local | ai | local+ai).

Миграция 9 (финальный слой Scout): `scout_lead_sources` (lead_id FK, source_username,
discovery_method, origin_id, origin_url, first_seen_at, last_seen_at, times_seen;
уникально lead + источник + метод; индексы по lead_id и source_username) — все
SMM-источники, через которые найден лид. `scout_decisions` (job_id, username,
source_username, decision lead_created | lead_updated | skipped, skip_reason,
details, category, confidence, followers, contacts_present, created_at) — журнал
решений для отладки, хранится 30 дней вместе с событиями. `scout_runs.started_at`
(индекс; статус и время окончания — в `search_jobs`). В `scout_processed_profiles`
добавлены instagram_user_id, category, confidence. В `lead_scout_profiles` —
category_name, is_business, bio_links, local_category, local_confidence,
ai_category, origin_id (первое обнаружение остаётся в source_username /
discovery_method / origin_url, следующие — в `scout_lead_sources`). В
`scout_sources` — суммарные candidates_found, profiles_resolved, profiles_skipped,
errors_count. Идентичность лида: Instagram id (`leads.platform_user_id`), иначе
username; уникальные ограничения `leads` (platform + id, platform + username)
остаются защитой от гонки: конфликт при сохранении повторяется как обновление.

Миграция 10 (рассылки): `leads.do_not_contact` (никогда не писать) и
`leads.last_contacted_at`. `outreach_templates` (name, body, enabled).
`outreach_followup_sequences` (name, steps [{delay_days, template_id}], enabled).
`outreach_campaigns` (name, status draft | scheduled | running | paused |
completed | cancelled | failed, created/started/finished_at, scheduled_at UTC,
template_id, followup_sequence_id, sender_strategy single | round_robin,
sender_ids, total_recipients, queued/sent/skipped/failed/replied_count — считаются
из получателей в той же транзакции). `campaign_recipients` (campaign_id, lead_id,
username, sender_account_id, status pending | queued | sending | sent | skipped |
failed | cancelled, skip_reason, failure_reason, reason_details, needs_review,
queued/sent/failed/replied_at, message_id, rendered_message; уникально
campaign + lead). `outbound_message_jobs` (campaign_id, recipient_id,
idempotency_key уникален, sender_account_id, scheduled_at, status pending | ready
| sending | sent | failed | cancelled, attempt_count, last_error, claim_token,
claimed_at, created_at, sent_at). `conversations` (lead_id, platform,
sender_account_id, status waiting_reply | replied | stopped, platform_thread_id,
first/last_outbound_at, last_inbound_at; уникально lead + platform + sender).
`messages` (conversation_id, direction outbound | inbound, type initial | followup
| reply, body, sender_account_id, sent_at, platform_message_id, campaign_id,
idempotency_key уникален). `outreach_followup_jobs` (conversation_id,
sequence_id, step_index, template_id, scheduled_at, status pending | sent |
cancelled | failed, cancel_reason). `outreach_senders` (profile_id браузерного
профиля, status, reason, until, last_sent_at). `outreach_events` (campaign_id,
type, payload; хранятся 90 дней). Все времена — UTC, интерфейс показывает местное.
