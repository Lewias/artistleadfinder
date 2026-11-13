# Artist Lead Finder — Project audit

Date: 2026-11-13. Phase: repository audit only; implementation awaits review and approval.

## 1. Git safety and audit boundary

- Starting branch: `main`; starting commit: `01d765446809bbae101286e2d5eb6a42cb81abeb`.
- Both `git status --porcelain=v1` and the short status confirmed a clean tree before branch creation. The local branch inventory contained only `main`.
- Created and verified `redesign/artist-lead-finder-v2`. The first sandboxed attempt could not write Git refs; the authorized retry succeeded.
- `stash@{0}` (previous macOS redesign) and `stash@{1}` (previous studio redesign) were listed only. Neither was read, applied, popped, removed or modified.
- No application source, dependencies, configuration, database, installed application, server or release has been changed in this phase. The only intended tracked additions are this document, `UI_UX_AUDIT.md` and `REDESIGN_PLAN.md`.
- Remain on this branch. Any unexpected concurrent changes require inspection before proceeding; never reset, discard or stash them automatically. Branch isolation does not protect ignored build outputs or live user data, which must also be kept untouched.

## 2. Evidence and limits

The audit inventories all 769 tracked paths, examines the application entry points, navigation, page modules, shared primitives, styles, service boundaries, database definitions, desktop configuration, packaging scripts and CI/release workflows. Generated assets, dependencies and every line of every backend module were not individually reviewed. This is an architecture and functionality inventory, not a security audit.

Current source version is **0.11.0** in the application manifests. Existing documentation contains earlier MVP descriptions and should not override current owning source. In particular, the product now includes AI, real browser-driven discovery, three messaging channels and server-backed accounts. A cloud-parser navigation destination is not present in the current `navigation.ts`; retained server/worker files do not establish a current desktop feature.

No current desktop screenshots were captured and neither native platform build was visually verified in this phase. Findings about spacing and composition in the companion UI audit are source-based hypotheses until runtime inspection.

## 3. Repository map

| Area | Tracked paths | Responsibility |
| --- | ---: | --- |
| `frontend/` | 482 | React application, page modules, components, types, CSS, tests and bundled emoji assets |
| `backend/` | 179 | Python core, services, data models, browser runtime, provider integrations and tests |
| `src-tauri/` | 21 | Rust host, privileged desktop bridge, browser drivers, icons and packaging configuration |
| `supabase/` | 27 | Account/CRM SQL migrations, edge functions, VDS operations and retained worker assets |
| `producer-sales-skills/` | 27 | Assistant skill content and related tooling; separate from desktop navigation |
| `scripts/` | 13 | Freeze, smoke checks, Windows packaging, versioning and fixture UI previews |
| `.github/` | 2 | CI and release workflows |
| `docs/` | 2 | iPhone Shortcut/bridge documentation and artifact |
| Repository root | 16 | Manifests, tooling, architecture, data model and packaging documentation |

Local `.venv`, `node_modules`, `build`, `dist`, `artifacts` and `src-tauri/target` are development/build outputs, not alternative source trees. Do not use an old portable binary as proof of the branch's current UI.

## 4. Runtime architecture

```text
React + TypeScript pages
  -> services/api.ts (typed request/browser/openProfile/openLogs facade)
  -> Tauri invoke: core_request / browser_action / native actions
  -> Rust host + background browser drivers
  -> Python child process, JSON Lines over stdin/stdout
  -> account Runtime -> ApplicationService -> domain services
       -> local SQLite / encrypted files / isolated Chromium / remote integrations
```

**Frontend:** React 19, TypeScript 5.7, Vite 6. No general router or global state library is declared. `App.tsx` owns channel/page state; `navigation.ts` defines 21 destinations across Instagram, iMessage and Email plus shared footer pages. Page changes remount the page subtree. `useResource` provides polling, error/loading flags and refresh; page components also own mutation and draft state. Public RPC results and parameters are represented in `services/types.ts` and related helpers.

**Desktop:** Tauri 2 with Rust 2021, single-instance handling, dialogs, opener, updater and process plugins. Main window: 1360 × 900 logical units, minimum 1024 × 700. Default system decorations have not been replaced in the checked configuration. CSP confines the frontend to local resources and IPC; remote network access belongs to the core. Capabilities are intentionally narrow. Do not broaden them to make styling easier.

**Core:** Python 3.12+, SQLAlchemy 2, Pydantic 2, Playwright 1.57, cryptography, phonenumbers, certifi and Anthropic SDK. `rpc.py` starts logging, account runtime and the request loop; `account/runtime.py` controls account readiness, role-sensitive services and local account data; `service.py` wires discovery, outreach, CRM, inbox, assistant, Spotify and email. Long-running work continues in service/driver threads, not React components.

**Browser work:** WebView renders the app; separate Playwright Chromium contexts handle Instagram and Spotify. Session import/save, proxies, browser availability, active Scout runs and send allocation are functional constraints. A visual rewrite must not reroute these operations through the frontend.

## 5. Data and integration inventory

SQLite uses WAL, foreign keys and a 30-second busy timeout. `database.py` recognizes schema versions through **27** and rejects a newer database. `models.py` owns leads, score explanations, sources, search/Scout history and memory, sender health, outreach campaigns/jobs/conversations, iMessage templates/attachments/jobs, CRM contacts/statuses/tombstones, inbox findings, assistant drafts/actions/usage, Spotify runs/artists/playlists, and email accounts/campaigns/messages/suppression/warmup partners. Account-specific data and encrypted session/key files live outside the repo. A UI redesign does not require a schema migration.

| Integration | Current implementation and UI implications |
| --- | --- |
| Account server | Self-hosted Supabase Auth, database RPC/REST, edge functions, roles, activation keys, signed offline passes and CRM sync. Distinguish server reachability from local-core and Instagram health. |
| Instagram | Browser-driven discovery, classification inputs, Direct outreach and reply/contact scanning. Preserve login/checkpoint/rate-limit states, cooldowns and ambiguous-send handling. |
| Classification | Local rule engine, OpenRouter, and Cloudflare-related configuration in the core. Present evidence, confidence, pending/error states and costs without changing provider logic. |
| Spotify | Hidden Chromium web-player integration learns anonymous request headers/query hashes; search/playlists/related artists feed Instagram outreach and CRM. It is not a frontend Spotify OAuth flow. |
| Gmail | SMTP/IMAP with app passwords, send limits, recipient-time scheduling, followups, replies/bounces and suppression. This is not Gmail OAuth in the current product. |
| Email warmup | Mailbox pool plus Nstbrowser partners; track placement and reply steps independently from campaign delivery. |
| iPhone | LAN bridge, pairing/QR and Apple Shortcut, message chains and attachments. Pairing and delivery need separate visible states. |
| macOS Messages | Local Messages history and native send integration for supported Mac flows. Windows must show capability guidance rather than imitate unavailable features. |
| Assistant / Telegram | Claude tools, drafts/action confirmation, usage/configuration and Telegram linkage. Preserve existing permission and confirmation boundaries. |
| Distribution | GitHub release feed and Tauri signed updater; installation, restart and failure states are product surfaces. |

## 6. Functionality preservation register

The authoritative starting point for navigation is `frontend/src/navigation.ts` plus routing in `App.tsx`. Nested workflows must survive even if their layout changes.

| Surface | Existing capability to preserve | Main owner |
| --- | --- | --- |
| Sign-in and account gate | Register/login, activation, checking/retry, offline expiry, account menu and logout | `components/account/AccountGate.tsx` |
| Parser and outreach | Auto find-and-send, source management, account selection/targets, Scout activity/settings, manual audience list, message variants, replies/contact extraction | `ScoutDiscovery`, `ScoutBoard`, `ScoutAccounts`, `components/outreach/*` |
| Secondary discovery | Manual browser collection, demo/import provider jobs and progress controls | `pages/Discovery.tsx`, `BrowserDiscovery`, `JobProgress` |
| Instagram accounts | Create/edit profiles, cookies, proxies, open/save browser and delete session | `components/BrowserProfiles.tsx`, `ProxyFields` |
| Spotify | Source/search settings, run progress/results and handoff to outreach | `pages/Spotify.tsx` |
| Artist database | Search, filters/sort/pagination, selection, lead details/evidence, delete/export and outreach handoff | `pages/Leads.tsx`, `LeadDetail`, `ExportBar` |
| Search history | Run state/detail/results, pause/resume/cancel where supported, history cleanup | `pages/SearchHistory.tsx` |
| Statistics | Aggregates, provider health, recent jobs and navigation to working areas | `pages/Dashboard.tsx` |
| CRM × 3 | Contacts, filters/sort, editable statuses, notes/next actions/value, selection, import, trash/restore/purge and channel handoff; email thread where supported | `pages/Crm.tsx`, `components/crm/*`, `email/EmailThreadModal.tsx` |
| Other users' CRM × 2 | Instagram/iMessage owner selection and staff-only visibility | `Crm` with `others`, `App.tsx` |
| iMessage campaigns | Recipient input/import, message chains/files, preview, templates, iPhone setup, bridge and campaign controls | `pages/IMessageCampaigns.tsx`, `components/imessage/*` |
| iMessage templates | Shared templates/folders, multipart messages/files and insertion into campaigns | `pages/IMessageTemplates.tsx` |
| iMessage logs | Campaign/bridge/Shortcut status history | `pages/IMessageLog.tsx` |
| iMessage dialogs | Mac conversation list, waiting filter, thread history and supported CRM-contact reply | `pages/Dialogs.tsx` |
| Email campaigns | Audience, text variants, scheduling/pacing/followups and campaign state | `pages/Email.tsx` |
| Email accounts | Gmail app-password setup, health, limits and ramp configuration | `EmailAccounts` in `pages/Email.tsx` |
| Email warmup | Pool membership, metrics, partner import/discovery and event history | `pages/EmailWarmup.tsx` |
| Assistant | Chat, drafts, action/send review, settings, Mac support and Telegram panel | `pages/Assistant.tsx`, `components/assistant/*` |
| Administration | User/role/block management and access-key lifecycle | `pages/Admin.tsx` |
| Settings/support | Defaults, scoring, providers/import, AI configuration, memory reset, logs, updates | `pages/Settings.tsx`, `WorkSettings`, `MemoryReset`, `UpdateNotice` |

Role visibility is not uniform: admin destination is admin-only; assistant and other-user CRM pages are staff-only in the shell. Backend authorization must remain authoritative. Preserve DNC, already-contacted exclusions, followup cancellation, uncertain-send review and sender affinity; these cannot become cosmetic switches.

## 7. Styling and reusable UI

`main.tsx` loads bundled Inter Variable and one global `theme.css` (5,675 lines at baseline). Tokens cover graphite surfaces, violet accents, status colors, radii and motion; page-specific selectors share the same cascade. Lucide supplies icons. There is a small shared Button, PageHeader, DataState/StatusBadge, Toaster/ErrorBoundary, Modal, Menu and SectionTransition. Radix Dialog is declared, but the examined shared Modal uses its own DOM and Escape/backdrop handling.

Large page modules mix rendering, local interaction state and RPC mutations (Email 957 lines; iMessage campaigns 940; CRM 774 at baseline). Keep services and workflows intact while extracting presentation incrementally. Do not replace the frontend framework or adopt a broad component kit as a prerequisite.

## 8. Build, distribution and baseline checks

- `npm run dev`: frontend only; does not establish desktop functionality.
- `npm run desktop:dev`: Tauri with development core; `npm run build`: TypeScript project build plus Vite.
- `scripts/build-core.py`: PyInstaller one-folder core plus sidecar smoke. Windows bundles Chromium; Mac downloads it on first use. Retain runtime certificate support assets when freezing.
- `npm run package:windows`: Python/frontend checks, core freeze, Tauri/NSIS, desktop smoke and portable copies/hash checks.
- `.github/workflows/ci.yml`: Windows and macOS Python tests/Ruff, TypeScript, ESLint and Vitest. Branch push filters target main; PRs trigger CI. Do not change CI just to claim checks on this branch.
- Release workflow: tag/manual build matrix, signed updater artifacts and draft GitHub release, plus bundled-core smoke on Mac. Current Mac packaging uses ad-hoc signing; notarization/commercial distribution readiness is a separate release concern.

Audit baseline: `npm run typecheck` and `npm run lint` completed without diagnostics; Vitest **16 files / 61 tests passed**. No backend suite, Rust checks, native build, external integration exercise or visual verification was run for this documentation-only phase. Existing pure helper tests do not prove keyboard accessibility or rendered layout.

## 9. Decisions required before implementation

Review the direction, navigation continuity, platform adaptations, staged delivery and acceptance matrix in `REDESIGN_PLAN.md`. Approval authorizes incremental implementation; it does not authorize changing workflows, live data, remote services or publishing a release. New product capabilities identified there remain explicit scope decisions.
