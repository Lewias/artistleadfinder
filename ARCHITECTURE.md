# Artist Lead Finder — архитектура

Локальное Windows-приложение: Tauri 2 / Rust, React + TypeScript + Vite,
обычный CSS; Python 3.12+, Playwright с комплектным Chromium, SQLAlchemy 2,
Pydantic и SQLite. UI не требует браузера. Рассылка и автоматическое
взаимодействие с аккаунтами отсутствуют.

## Границы
`frontend/src` — представление и единый шлюз `services/api.ts` для вызовов ядра,
браузера и системных действий.
`src-tauri` — окно, разрешения, запуск и завершение Python sidecar.
`backend/artist_lead_finder` — providers, normalization, analysis,
classification, scoring, repositories, jobs, RPC.
`backend/tests` — воспроизводимые проверки ядра.

Конвейер: SearchConfig → DiscoveryEngine → SourceProvider → Candidate →
Normalizer → Deduplicator → ProfileAnalyzer → RuleBasedClassifier →
GenreClassifier → LeadScorer → Repository → SQLite → ApplicationService.
Провайдеры не знают о БД, UI и оценках. Первые источники: MockProvider и
ImportedDatasetProvider. MetaInstagramProvider — недоступный адаптер до
реализации разрешённого официального API. Ошибки авторизации, квоты и 429
останавливают источник и записываются в ProviderHealth.

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
WebView2 обслуживает только интерфейс приложения; окна Instagram и Brave
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
