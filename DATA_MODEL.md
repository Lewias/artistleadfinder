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
