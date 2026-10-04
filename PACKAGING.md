# Windows packaging

Решение принято до реализации упаковки: Python — отдельный подписываемый
sidecar, React — статические assets внутри Tauri, установщик — штатный NSIS.
Нет HTTP-сервера в production; JSON Lines передаются по stdin/stdout.

## Сборка
`npm run package:windows` на Windows x64 с MSVC, Rust и `.venv`.
Скрипт сначала запускает проверки, затем PyInstaller 6 в режиме onefile
с console-subsystem (это необходимо для IPC). Rust запускает процесс с
CREATE_NO_WINDOW, поэтому терминал у пользователя не появляется.
PyInstaller включает Python, sqlite3, SQLAlchemy, Pydantic, Playwright,
комплектный Chromium и native DLL. Перед сборкой установите браузер командой
`$env:PLAYWRIGHT_BROWSERS_PATH='0'; .venv/Scripts/python -m playwright install chromium --no-shell`.
Сборка проверяет запуск Chromium из замороженного sidecar без обращения к сети.
`externalBin` использует target suffix x86_64-pc-windows-msvc. Tauri копирует
sidecar в каталог приложения под именем `artist-core.exe`. Release-шлюз
разрешает путь относительно собственного exe; от cwd пользователя не зависит.
Dev-шлюз использует только виртуальное окружение репозитория.

PyInstaller onefile выбран ради простой и атомарной комплектации externalBin;
runtime распаковывается в системный temp. Для больших зависимостей возможна
замена на onedir + Tauri resources без изменения IPC или UI.

NSIS включает WebView2 bootstrapper для интерфейса Tauri. На Windows без WebView2 его первичная
установка может требовать интернет; установка Python/Node/SQLite или отдельного браузера не нужна.
База, журналы и импорт остаются в LocalAppData/ArtistLeadFinder.

Результат: `artifacts/ArtistLeadFinder-Setup.exe`. Это локальная неподписанная
MVP-сборка. Для публичного выпуска нужны code signing, чистая Windows VM,
проверка install/update/uninstall, crash/antivirus compatibility и лицензии
зависимостей. Автообновление: Tauri updater, пакеты подписываются ключом
`TAURI_SIGNING_PRIVATE_KEY` (см. README «Релизы и автообновление»); без ключа локальная
сборка пропускает пакеты обновления.
Сборка установщика не означает, что эти release-проверки пройдены.

Скрипт также обновляет `artifacts/ArtistLeadFinder-Portable`: основной EXE и
`artist-core.exe` всегда копируются из той же сборки, что и установщик.
Если существуют прежние папки `Portable-Update`, `Portable-Fixed` и `Portable-Ready`,
их EXE также обновляются. Контрольные суммы всех копий сверяются с результатом сборки.
`scripts/smoke-scout.py` проверяет путь источник → публикация/reel → артист → лид
на локальных HTML-страницах в настоящем Chromium без сетевых запросов. При передаче
пути к EXE проверяет, что встроенные обработчики `scout.js` и `capture.js` совпадают
с исходниками. Несовпадение останавливает упаковку, чтобы не выдать старый portable EXE.

## Проверка
Актуальная сборка проверена 22.09.2026: скаут-источники, автоматический сбор
упомянутых исполнителей и три оценки услуг; браузерные профили и веб-поиск сохранены.
Её SHA256: `5648F354172C10BEFE6DEA5665F24B7F5534FB2C20B1F9284F696AFF8303CA99`.
Проверки: 31 pytest, 7 Vitest, Ruff, TS, ESLint, Clippy, frozen-core smoke.
В финальном release UI проверены подключение ядра, новый основной экран,
пустой список источников и заблокированный запуск без источника. Схема 3, база 1001/36.
Полный скаутинг через авторизованную Instagram-сессию пока не проверен.
В предыдущей сборке поиск independent rapper London вернул 12 профилей;
после снятия одного флажка 11 ссылок переданы в очередь.
В предыдущей проверке release UI подтверждена пауза 0/1 при требовании входа Instagram; база 1001/36.
Файл checksum также создаётся рядом с установщиком. Приведённый ниже размер
и hash относятся к первоначальной сборке 19.09.2026.
Локально выполнены: frozen-core stdio smoke, release запуск при выключенном
Vite, сохранность базы 1000/36, single-instance и завершение обоих компонентов.
NSIS x64 создан, 22672951 байт. SHA256:
`04BA7EE7C6B3865F914908B19371E1B01D1612CA204E3DF3977C6AED241C00E3`.
Чистая Windows VM в этой среде отсутствует; установка/обновление/удаление на
ней остаются обязательной проверкой перед публичным релизом.

1. Остановить dev-server и dev-окно.
2. Запустить release exe с включённым sidecar рядом: UI и ядро должны работать.
3. Перезапустить и проверить SQLite/историю.
4. На чистой Windows проверить установщик и shortcuts без dev tools.
5. При обновлении и удалении не удалять пользовательскую базу автоматически.

Документация: https://v2.tauri.app/distribute/windows-installer/,
https://v2.tauri.app/develop/sidecar/,
https://pyinstaller.org/en/stable/operating-mode.html.

## Сборка после редизайна (23.09.2026)

Светлая тема по референсу verse.ai, основной экран «Скаутинг», обновлённые обзор, база и карточки лидов. Полный сценарий `scripts/package-windows.ps1` прошёл: 31 pytest, 7 Vitest, Ruff, TypeScript, ESLint, frozen-core smoke и Tauri release/NSIS. На release exe визуально проверены скаутинг, обзор и база артистов при подключённом локальном ядре; запуск без источника заблокирован.

Установщик: `artifacts/ArtistLeadFinder-Setup.exe`.
SHA256: `4D7976670869452A5F7147459E36D7DD11E14EAC17F783F37A095A712820DF7E`.

## Исправление переполнения (23.09.2026)

Подпись бренда теперь переносится внутри боковой панели; ширина элементов интерфейса ограничена в узких колонках. Проверены 31 pytest, 7 Vitest, TypeScript, ESLint, Ruff, frozen-core smoke, release-сборка и NSIS. В release-окне проверено, что подпись больше не пересекает границу панели.

SHA256 актуального `artifacts/ArtistLeadFinder-Setup.exe`: `74A89E6601D3A80811538B9C3A28A8DBADD1F41993322710CCB535F51EC3AAF3`.

## Полировка интерфейса (24.09.2026)

По проектным навыкам `emil-design-eng`, `find-animation-opportunities`, `animate` и `review-animations` уточнена иерархия текста, контраст форм и карточек. В базе основные фильтры остались на первом экране, подписчики/оценка/активность/сортировка перенесены в доступный с клавиатуры раскрываемый блок. На экране скаутинга добавлен счётчик источников с ограничением запуска при числе больше 20. Для кнопок добавлен короткий отклик на нажатие (160 мс, только transform); при reduced motion движение выключается. Таблицы и постоянная навигация без движения.

После правок прошли 31 pytest, 7 Vitest, Ruff, TypeScript, ESLint, frozen-core smoke и NSIS сборка. В release-окне проверены скаутинг, база, раскрытие фильтров и карточка артиста. SHA256 установщика: `E07A28140AD5FDDE2240953B07582647AB8DF4913ADFF6D2965D46580DE752D9`.

## Упрощение интерфейсного стека (25.09.2026)

Все вызовы Tauri из React теперь проходят через `frontend/src/services/api.ts`; компоненты не содержат собственных обёрток `invoke`. Общие типы браузерного профиля и очереди находятся в `frontend/src/services/types.ts`. Удалены неиспользуемые Tailwind и Vite-плагин Tailwind, а также `class-variance-authority`, `clsx` и `tailwind-merge`, применявшиеся только к двум вариантам кнопки. Стили теперь обычный CSS с небольшим локальным сбросом. Tauri, Rust, Python, SQLite и границы доступа к браузеру сохранены.

Проверки: 31 pytest, 7 Vitest, Ruff, TypeScript, ESLint, frozen-core smoke и NSIS release. В собранном приложении проверены скаутинг, база артистов и загрузка настроек/браузерных профилей. Размер основного JS bundle уменьшился с 361,44 до 332,31 kB (до gzip).

SHA256 актуального `artifacts/ArtistLeadFinder-Setup.exe`: `6E2E1171C49BB5C3007AD582833FB8A9FE4379D905983B70F76D8101829194E3`.

## Chromium для браузерных окон (25.09.2026)

Окна Instagram-профилей и поиска Brave переведены с WebView2 на комплектный
Chromium 143 через Playwright 1.57. Основное окно Tauri по-прежнему использует
WebView2. Профили открываются в отдельных непостоянных контекстах Chromium;
cookies сохраняются в DPAPI-хранилище только по кнопке. Прокси с авторизацией
по-прежнему проходят через локальный посредник на 127.0.0.1.

Проверены 39 pytest, 7 Vitest, Ruff, TypeScript, ESLint, два теста прокси,
Rust Clippy и запуск Chromium из замороженного sidecar. Отдельно открыта
страница Instagram в видимом Chromium на машине сборки, сохранена тестовая
cookie через DPAPI и прочитаны 4 результата Brave Search. Публичную сборку
ещё нужно проверить на чистой Windows, в том числе реальный вход в Instagram.

Установщик: `artifacts/ArtistLeadFinder-Setup.exe`, 238889710 байт.
SHA256: `B845226FE057A71673C210DDC53F2E5646E03E772D1A004A865321AB096DE4FD`.

## Релизы: Windows + macOS в GitHub Actions (29.09.2026)
Сборка ядра вынесена в `scripts/build-core.py`: PyInstaller onefile с суффиксом target triple
из `rustc -vV` (`x86_64-pc-windows-msvc`, `aarch64-apple-darwin`) и smoke замороженного ядра
с запуском комплектного Chromium. Его вызывают `package-windows.ps1` и `release.yml`.

`release.yml` на тег `v*`: `windows-latest` → NSIS, `macos-14` (arm64) → `.app` и `.dmg`.
Перед сборкой — pytest, Ruff, tsc, ESLint, Vitest. `tauri-action` собирает бандлы с
`createUpdaterArtifacts`, подписывает `.nsis.zip`/`.exe` и `.app.tar.gz` (`.sig`), создаёт
черновик релиза и общий `latest.json` (`windows-x86_64`, `darwin-aarch64`). Endpoint
обновлений: `https://github.com/Lewias/artistleadfinder/releases/latest/download/latest.json`,
поэтому обновление видно только после публикации черновика. На Windows установка идёт в
режиме `passive` (окно прогресса без вопросов).

macOS: ядро собирается PyInstaller в режиме `--onedir` и лежит в
`Contents/Resources/core/artist-core` (конфиг `src-tauri/tauri.macos-core.conf.json`
добавляется к сборке в CI). Режим `--onefile` на macOS синхронизирует загрузчик с
процессом ядра через семафор System V; на части Mac (MDM-профили, защитные агенты)
`semctl` запрещён — «Failed to initialize sync semaphore», ядро не стартует. dev-режим
использует `.venv/bin/python`. Секреты шифруются AES-GCM ключом из login Keychain
(`security`, сервис `ArtistLeadFinder`); файлы с префиксом `ALF1`. Windows по-прежнему DPAPI.
Бандл не подписан и не нотаризован (нет Apple Developer ID); при появлении добавить в
workflow `APPLE_CERTIFICATE`, `APPLE_CERTIFICATE_PASSWORD`, `APPLE_SIGNING_IDENTITY`,
`APPLE_ID`, `APPLE_PASSWORD`, `APPLE_TEAM_ID` — tauri-action подпишет и нотаризует сам.

Chromium на macOS не вшивается в ядро: PyInstaller переподписывает каждый Mach-O отдельно,
а `Google Chrome for Testing.app` подписывается только целым бандлом (`codesign` падает с
«bundle format unrecognized»). Ядро при старте в фоне запускает установщик Playwright
(`install chromium --no-shell`) с `PLAYWRIGHT_BROWSERS_PATH` =
`~/Library/Application Support/ArtistLeadFinder/ms-playwright`; повторный запуск — no-op.
Папка переживает обновления, поэтому `.app.tar.gz` остаётся маленьким. Пока загрузка идёт,
открытие окна профиля отвечает «Chromium скачивается…». В CI Chromium ставится с
`PLAYWRIGHT_BROWSERS_PATH=0` только на Windows; `build-core.py` на Mac отказывается
собирать, если он лежит в пакете Playwright. Smoke на Mac проверяет и загрузку.
Сборка macOS в этой среде не запускалась: проверяется первым прогоном workflow и на
настоящем Mac (установка, Gatekeeper, ядро, Chromium, импорт сессии).
