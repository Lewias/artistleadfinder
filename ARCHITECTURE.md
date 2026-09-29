# Artist Lead Finder — архитектура

Локальное Windows-приложение: Tauri 2 / Rust, React + TypeScript + Vite,
обычный CSS; Python 3.12+, Playwright с комплектным Chromium, SQLAlchemy 2,
Pydantic и SQLite. UI не требует браузера. Единственное действие от имени
аккаунта — первое сообщение лиду из запущенной пользователем кампании (модуль
outreach); Lead Scout никому не пишет.

## Границы
`frontend/src` — представление и единый шлюз `services/api.ts` для вызовов ядра,
браузера и системных действий.
`src-tauri` — окно, разрешения, запуск и завершение Python sidecar.
`backend/artist_lead_finder` — providers, normalization, analysis,
classification, scoring, repositories, jobs, RPC.
`backend/tests` — воспроизводимые проверки ядра.

Lead Scout (`backend/artist_lead_finder/lead_scout/`): candidates (единый
кандидат), discovery (провайдеры по типу страницы), profiles (Profile Resolver),
contacts (ContactExtractor), classification (LocalMusicClassifier с RuleRegistry,
ClassificationService: режимы AI, кэш AI по хэшу профиля, отложенные AI-задачи),
ai (OpenRouter: схема ответа, один repair-запрос, таймаут), decision (единый
упорядоченный конвейер фильтров: `LeadFilterDecision`, причины `LeadSkipReason`),
leads (идентичность лида, история источников, метрики запуска и источника),
memory, events (типизированные события `scout:*`). `ScoutService` ведёт этапы по
страницам очереди браузера: source → discovery → candidate → фильтры кандидата
(источник, игнор, уже обработан — до открытия профиля) → profile resolver →
фильтры профиля (дубль, данные, подписчики, контакты — до AI) → classification →
AI при необходимости → тип и уверенность → одна транзакция SQLite: лид в CRM
(`BrowserCaptureService.store`), история источников, обработанный профиль, метрики,
журнал решений, событие и шаг очереди. Ошибка одного профиля считается и
пропускается, ошибка страницы источника — пропуск страницы, ошибка БД, вход,
checkpoint и 429 останавливают запуск (429 — через перерыв планировщика). Rust выбирает скрипт
страницы по шагу (grid.js, scout.js, story.js, follow.js, capture.js,
profile_api.js) и способ доступа: открыть страницу, выполнить скрипт в уже
открытой вкладке (запрос профиля к web API) или ничего не открывать (профиль из кэша).

Profile Resolver (`lead_scout/profiles/`): кэш → InstagramApiProfileProvider
(адаптер эндпоинта web_profile_info; запрос выполняется во вкладке приложения,
cookies добавляет браузер) → достаточно данных? → BrowserProfileProvider
(capture.js) → mergeProfileData → нормализация (единый слой алиасов полей) →
ContactExtractor. Между шагами браузера состояние хранится в задаче очереди
(ResolveStep); `resolve()` прогоняет ту же цепочку синхронно через адаптер
загрузки. Ошибки типизированы (NOT_FOUND, PRIVATE, LOGIN_REQUIRED, CHECKPOINT,
RATE_LIMITED, NETWORK_ERROR, PARSER_ERROR); повторяются только сетевые.

Конвейер демо/импорта: SearchConfig → DiscoveryEngine → SourceProvider → Candidate →
Normalizer → Deduplicator → ProfileAnalyzer → RuleBasedClassifier →
GenreClassifier → LeadScorer → Repository → SQLite → ApplicationService.
Провайдеры не знают о БД, UI и оценках. Первые источники: MockProvider и
ImportedDatasetProvider. MetaInstagramProvider — недоступный адаптер до
реализации разрешённого официального API. Ошибки авторизации, квоты и 429
останавливают источник и записываются в ProviderHealth.

Outreach (`backend/artist_lead_finder/outreach/`): renderer
(`MessageTemplateRenderer`: переменные, fallback, длина ≤ 1000), eligibility
(`can_send_initial_outreach` → allowed / reason по глобальной истории сообщений и
CRM), senders (health аккаунта active / paused / auth_required / checkpoint /
rate_limited / disabled, темп: пауза между сообщениями и лимит за 24 ч,
`SenderAllocator` — round robin с сохранением отправителя диалога), followups
(`FollowUpScheduler`: план шагов после отправки, отмена при ответе), campaigns
(аудитория, preview, жизненный цикл, ответы, DNC, блок CRM), worker
(`OutreachWorker`), events (`campaign:*`, `recipient:*`, `sender:unavailable` +
аудит в журнал без текста сообщений), workspace (список «Первичной рассылки» →
кампания с вариантами сообщений). Отправка никогда не идёт из UI: кампания →
получатели → `outbound_message_jobs` → worker → окно аккаунта. Rust-поток
`run_outreach_driver` раз в 3 с спрашивает у ядра `outreach.next_internal`; ядро
в одной транзакции повторно проверяет кампанию, получателя, аккаунт (health,
открытое окно, нет запущенного Scout в этом окне, темп) и забирает задание
условным UPDATE ready → sending с токеном. Rust выполняет `send.js` в открытой
вкладке аккаунта (один запрос web-клиента Instagram, cookies добавляет браузер,
CSRF берётся из страницы и не возвращается) и отдаёт результат в
`outreach.commit_internal`. Успех — одна транзакция: получатель sent, сообщение,
диалог, лид (last_contacted_at, статус «Написали»), счётчики кампании, план
follow-up. Идемпотентность: ключ `initial-outreach:<campaign>:<lead>` уникален и
у задания, и у сообщения; результат принимается только с токеном текущей
выдачи; задание, оставшееся в sending (сбой, потерянный ответ, тайм-аут), не
отправляется повторно — без записи сообщения оно становится ошибкой с пометкой
«проверьте вручную», а лид блокируется до решения пользователя. Повторяются
только сетевые ошибки до отправки запроса. Вход, checkpoint и ограничение
Instagram останавливают аккаунт; задания ждут его и не переносятся на другие
аккаунты.

## Связь и фоновые задания
Production: JSON Lines RPC по stdin/stdout дочернего процесса. Никаких
локальных сетевых портов. Rust предоставляет один типизированный шлюз;
React использует только ApplicationService. Запрос содержит id, method,
params; ответ — id, result либо безопасную error. stdout только для RPC,
логи отдельно. Рабочие потоки выполняют задания, SQLite-сессии принадлежат
потокам. Pause/cancel проверяются между кандидатами. На старте running и
queued задания помечаются interrupted/paused с понятной причиной.

SQLite хранится в LocalAppData/ArtistLeadFinder, WAL + foreign_keys,
короткие транзакции, индексы, серверная пагинация. Миграции версионируются.
Секреты в будущем — Windows Credential Manager; AI не требуется.

## Windows distribution
Сначала PyInstaller собирает Python sidecar вместе с зависимостями;
Tauri включает его через externalBin с суффиксом target triple.
NSIS формирует установщик, WebView2 включается через штатный механизм Tauri.
WebView2 обслуживает только интерфейс приложения; окна Instagram
запускаются отдельными процессами комплектного Chromium.
Пользователю не нужны Python, Node или Rust. Проверять установку нужно на
чистой Windows VM, включая обновление, удаление и сохранность базы.
Подписание и Tauri updater — отдельный этап, самодельного updater нет.

Источники решения:
- https://v2.tauri.app/develop/sidecar/
- https://v2.tauri.app/distribute/windows-installer/
- https://pyinstaller.org/en/stable/operating-mode.html

## Риски и допущения
Неизвестна доступность MSVC/Rust/WebView2 на рабочей машине. Нельзя считать
desktop запуск подтверждённым только по Vite build. Freeze Python требует
проверки native dependencies и shutdown. Правила классификации объяснимы,
но не являются доказательством независимости артиста. Mock-данные всегда
обозначаются как демонстрационные. Для cloud замены достаточно другого
ApplicationService и репозитория; core остаётся независимым.
