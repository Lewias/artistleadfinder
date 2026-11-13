# Artist Lead Finder — Redesign plan v2

Date: 2026-11-13. Status: **audit approved; Shell + Leads specimen authorized, remaining screens gated**.

Approval update: dark mode first, light-ready semantic tokens only; preserve every destination and capability, document any future IA proposal before implementation. Only the next Shell + Leads milestone is authorized. The original review gates below remain applicable to later milestones.

Companions: [Project audit](PROJECT_AUDIT.md) and [UI/UX audit](UI_UX_AUDIT.md).

## 1. Outcome and scope

Deliver a commercially polished desktop workspace for discovering artists, managing relationships and operating outreach across Instagram, iMessage and Email. Preserve current capability, role rules, RPC contracts, storage, send safeguards and native integrations. Work exclusively on `redesign/artist-lead-finder-v2`; never apply or alter stashes, modify main/development, switch branches without instruction or rewrite existing commits.

This phase adds documentation only. **Do not implement until the user has reviewed and approved this audit and plan.** Approval should settle the visual direction, the scope of appearance modes, navigation continuity and platform acceptance requirements. Later milestones are committed on the same branch after their checks pass; no push, release, merge or deployment is implied.

## 2. Recommended product identity

**Direction: a quiet artist workspace.** Neutral surfaces, precise typography, artist identity and an iris selection accent. Prioritize confidence and legibility during long sessions. Show a clear progression from discovery to qualified contact to conversation to next action without inventing new backend stages.

- One page title and one dominant action per task context. Secondary actions sit beside it; rare actions belong in an accessible menu.
- Lists/tables form the main content plane. A right inspector exposes evidence and editable details without replacing the list.
- Avoid decorative gradients, universal glass, large hero introductions and card grids. Use shallow elevation for transient surfaces only.
- Brand recognition comes from row composition, measured spacing, a consistent icon vocabulary and the accent. Keep the existing product name and recognizable music association without turning the app into a media player.
- Keep current Russian UI copy; validate long Cyrillic labels. Do not promise an English localization as part of a visual refresh.

## 3. Proposed shared design system

Values are reviewable starting points, not tested specifications. Validate contrast and fit before accepting them.

| Layer | Proposed rule |
| --- | --- |
| Type | macOS `-apple-system` / system SF; Windows `Segoe UI Variable`, `Segoe UI`; system fallback for unavailable glyphs. Regular body, semibold headings, tabular numbers. No redistributed proprietary font files. |
| Type scale | 13–14px body, 12px secondary, 14–16px section headings, 22–24px page title. Avoid 10–11px essential information; respect text scaling. |
| Spacing | 4px base; primary scale 4, 8, 12, 16, 24, 32. Content padding 20–24px; compact operational toolbars. |
| Geometry | Controls 6–8px radius, transient surfaces 10–12px; pills only for small status/filter chips. No nested panel as the default group. |
| Color | Semantic `canvas`, `sidebar`, `surface`, `raised`, `text-primary`, `text-secondary`, `separator`, `accent`, `focus`, `success`, `warning`, `danger`; do not encode a channel as a health color. |
| Dark starting palette | Canvas `#141518`, sidebar `#191A1E`, surface `#1F2025`, primary text `#F1F2F4`, secondary `#A9ADB7`, accent `#A99AFF`. Validate every text/control combination; these are not certified contrast results. |
| Appearance | Recommend system/light/dark support in the same semantic system. Light starts with warm neutral canvas, near-white surfaces and a darker iris accent. Both-mode delivery requires explicit approval; otherwise preserve dark and keep tokens ready for light. |
| Density | Standard rows around 40px; dense rows around 32px only where readable. Multi-line identity rows may be taller. Measure hit targets separately from icon size. |
| Layout | Sidebar 224–240px, compact page toolbar, flexible table region, optional 320–400px inspector. At minimum width collapse the inspector to an accessible sheet rather than compressing primary columns to illegibility. |
| Motion | 100–160ms feedback, up to 180ms transient surfaces; immediate navigation content and stable live rows. Reduced motion disables transform/stagger effects. |
| Accessibility | WCAG 2.2 AA target: normal text 4.5:1, large text/non-text indicators as applicable 3:1; visible focus, named controls, non-color statuses, keyboard operation and platform screen-reader validation. Compliance remains unclaimed until tested. |

### Platform adaptation contract

| Concern | macOS | Windows 11 |
| --- | --- | --- |
| Window chrome | Keep real traffic lights, resize/full-screen and inactive states; explore unified toolbar only after native proof | Keep real caption buttons, system menu, drag/resize and Snap; validate maximize/restore and scaling |
| Typography | System SF through the system stack | Explicit Segoe UI Variable with Segoe/system fallback |
| Commands | Command modifier, native app/menu-bar conventions | Control modifier, native menu/accelerator conventions |
| Icons | Semantic mapping to SF Symbols through a verified native-compatible path, with old-OS fallback | Equivalent Fluent-style symbols with the same meanings |
| Menus/dialogs | Native system menus for app/window commands; native file pickers; accessible web content menus | Native titlebar/system menu and file pickers; same accessible content menu semantics |
| Capabilities | Supported Messages integration and clear setup states | Explicit unavailable/setup guidance for Mac-only capabilities; retain iPhone bridge features |

Implement one platform adapter around capabilities, font/icon choice and shortcuts. Do not duplicate domain pages for each OS. SF Symbols integration must be tested against the current macOS 12 minimum; do not assume current symbols exist there. Retain native decorations until a custom/unified titlebar has passed actual platform checks. No fake traffic lights or CSS Windows caption controls.

Reference basis: [Apple desktop interaction guidance](https://developer.apple.com/design/human-interface-guidelines/designing-for-macos), [Microsoft platform typography](https://learn.microsoft.com/en-us/windows/apps/design/signature-experiences/typography), [Apple symbol system](https://developer.apple.com/sf-symbols/). Exact component sizes, colors and delivery order above are project decisions, not copied platform requirements.

## 4. Component architecture

Retain React, TypeScript, Vite, Tauri and Python. Do not replace working RPC handlers, polling, database or job workers as part of styling.

```text
frontend/src/
  design/          semantic tokens, type, spacing, appearance, platform rules
  components/ui/   Button, IconButton, Field, Checkbox, Select, Tabs,
                   Badge, Tooltip, Dialog, Menu, Table, EmptyState
  components/layout/ AppShell, Sidebar, PageToolbar, SplitView, Inspector
  components/status/ ConnectionSummary, TaskStatus, InlineError, Progress
  platform/        capability detection, semantic icons, command labels
  features/        gradually extracted view components for existing workflows
  pages/           existing composition and workflow boundaries during migration
  services/        existing API facade and types retained
```

Do not move all files at once. Add a layer only when a migrated screen needs it; retain old imports through a small compatibility wrapper where useful, then remove it after consumers move. Avoid appending another theme below 5,675 lines of existing rules: migrate selectors and delete their replaced definitions incrementally.

| Component family | Required behavior |
| --- | --- |
| Buttons/fields | Semantic intent and size; explicit busy/disabled; labels, help/errors; no color-only meaning |
| Dialog/sheet | Focus containment/initial focus/return focus, Escape policy, scroll lock, accessible title; protect drafts and separate destructive confirmation |
| Menu | Keyboard arrows/Home/End/Escape, focus return and named actions; no hover-only access |
| Table/list | Semantic headers, sort state, stable identity, paging, selection and bulk toolbar; horizontal scroll only in data region |
| Inspector | Preserve list scroll/filter/selection while inspecting; clear close and next/previous semantics where supported |
| Status | Owner of failure, last update, impact and recovery action; preserve backend reason codes |
| Campaign editor | Existing recipient/variant/sequence/preview models retained; queue transitions remain backend-owned |
| Notices | Inline persistent problems, toast for short-lived outcomes; no repeated stack of identical polling errors |

Use the already-declared Radix Dialog through an application wrapper where appropriate. Any additional interaction library requires a concrete need and dependency review. Keep domain logic out of visual primitives. Visual extraction must preserve mutation parameters, confirmation requirements and the distinction between retryable, failed and uncertain delivery.

## 5. Navigation and screen-by-screen strategy

Keep the three channels and all current page IDs through the initial migration. Shared footer retains Assistant, Users and Settings according to existing roles. Reduce visual weight rather than deleting destinations. Do not introduce global search, a command palette, saved views or a new campaign engine without a separate scope decision.

| Order / surface | Proposed composition | Functional acceptance scenario |
| --- | --- | --- |
| 1. Shell and account gate | Platform typography, native chrome, sidebar/channel context, compact toolbar, connection summary; focused account form | Login/register/activation/checking/retry/offline expiry/logout and role visibility retain current behavior; window changes never trigger sends |
| 2. Leads | Toolbar/filter row, dense results table, selection actions, evidence inspector | Filter/sort/page; select/export/delete; inspect score/provenance; add selected leads to manual outreach with unchanged IDs |
| 3. CRM: Instagram, iMessage, Email | Same table framework, channel columns and owner scope, edit inspector, dedicated trash view | Edit/save/status/notes/value; import; trash/restore/purge; handoff to correct channel; email thread; staff owner selector remains restricted |
| 4. Instagram account profiles | Health-first table, primary Open action, setup/edit sheet for cookies and proxy | Create/edit/import/save; open/close; proxy validation; no false logged-in badge from cookie count; delete confirmation unchanged |
| 5. Discovery: auto | Source list, prepared messages and sender readiness in a structured workspace; activity inspector | Source rotation and settings, target per account, start/pause/continue/cancel, source detail and counters; preserve cooldown/eligibility rules |
| 6. Discovery: manual list and replies | Audience table + message editor/preview; separate reply/contact list | Add/import/remove recipients, variants, preview/start; scan replies, hide/extract/add to CRM; preserve DNC, dedupe, sender affinity and uncertain-send review |
| 7. Secondary discovery | Compact, clearly labeled advanced section | Manual browser capture, queue, demo/import jobs and progress controls remain reachable; demo results stay labeled |
| 8. Spotify | Source/filter controls, run toolbar, result table, artist inspector | Playlist/search/related flow, progress/stop, selected results to outreach/CRM; empty/loading/provider failure |
| 9. History and statistics | Run table + details; compact outcome summaries | Pause/resume/cancel supported jobs; open results; clear history retains correct semantics; totals reflect existing backend data |
| 10. iMessage campaigns | Audience and chain editor, message/file preview; separate pairing/bridge panel | Recipient import, files/sequence, preview, template insertion, QR/Shortcut/bridge, campaign controls and actual per-step status |
| 11. iMessage templates and logs | Folder/list/editor; chronological operational log | Template folders and multipart content preserved; campaign log and bridge/Shortcut errors legible |
| 12. iMessage dialogs | Conversation list + thread/composer | Waiting filter/history/reply/send state on Mac; Windows capability state; draft behavior explicitly tested |
| 13. Email campaigns | Audience, mailbox/schedule strip and text/sequence editor | Variants, preview, send window/timezone, pacing/followups, pause/cancel; replies stop followups and suppression is honored |
| 14. Email accounts and warmup | Mailbox table + setup sheet; separate pool/partner/events workspace | App-password setup/error, limits/ramp, warmup membership/partner import/discovery, placement metrics and activity |
| 15. Assistant | Conversation, draft/action inspector, quiet usage indicator | Draft/edit/reject/confirm/send, Mac guidance and Telegram linkage; no permission or automation expansion |
| 16. Admin and Settings | Compact tables and grouped preference sections | User/key/role/block actions, default/scoring/provider/AI settings, memory reset, logs and updater check/install/restart states |

Each row includes loading, empty, populated, error, busy, disabled and relevant role/capability variants. Migration acceptance uses `PROJECT_AUDIT.md` as the feature ledger; a completed row needs both a screenshot and a functional result, not only a layout mockup.

## 6. Milestones and review gates

1. **Audit approval (current gate).** Review these three documents. No implementation has started.
2. **Evidence baseline and visual specimen.** Capture current native app on Windows and a real Mac using isolated data; inventory screen/substate screenshots. Implement after approval a limited shell + leads specimen, including typography, table, inspector, error state and dialog/menu. Review before spreading the visual system.
3. **Foundation and list workflows.** Complete primitives, shell, gates, profiles, leads and all CRM variants. Commit the completed milestone only after payload parity, keyboard checks and screenshots pass.
4. **Discovery and Instagram outreach.** Migrate sources, send preparation, replies, Spotify, history and statistics. Exercise lifecycle, exclusions and error/recovery states with non-sending test fixtures and appropriate domain tests.
5. **iMessage and Email.** Migrate campaign composition, bridge/setup, templates/logs/dialogs, mailboxes and warmup. Validate capability states on each platform and preserve native integrations.
6. **Assistant, administration and settings.** Complete secondary workflows, update/error states and permission-sensitive confirmations.
7. **Native polish and release candidate.** Complete Windows/macOS visual QA, accessibility, scaling, installer/update smoke and regression review. Commit acceptance fixes; prepare a reviewable release candidate without publishing it.

No schedule is promised before baseline native evidence is available. Avoid a single giant UI commit. Before each milestone: verify active branch and inspect diff. If either platform cannot be tested, leave its status pending and continue only work that can be honestly validated; final cross-platform acceptance remains incomplete.

## 7. Validation and screenshot protocol

### Functional checks

- Baseline already run: TypeScript/ESLint without diagnostics; Vitest 16 files, 61 tests passed. These cover existing code, not the proposed UI.
- After frontend milestones: typecheck, lint, affected Vitest and production frontend build. Add meaningful interaction tests for changed selection, focus, draft or payload behavior; do not create tests that merely assert class names.
- Run relevant Python tests when changing UI contracts or exercising domain flows; run the full backend suite before release acceptance. Rust/window changes require Rust tests/checks and a native build.
- Use `ARTIST_LEAD_FINDER_DATA` with a fresh fixture directory for core tests. Confirm the launched binary/commit and actual data path. Never let fixture scripts touch the user's CRM or saved sessions.
- Do not start live sends, warmup, provider scans, account resets or server jobs as incidental design QA. Fixture transports validate layout; actual external-send verification requires an explicitly authorized test setup.
- `scripts/ui-preview.py` is a useful existing browser fixture harness, but inspect/update coverage deliberately after approval: current fixtures contain historic states and are not a native app substitute.

### Native visual matrix

| Dimension | Required evidence |
| --- | --- |
| Platforms | Actual Windows 11 build and actual macOS build; record OS version, CPU, commit, app version and WebView engine where available |
| Windows sizes/scaling | Minimum 1024 × 700, default 1360 × 900, large desktop; 100/125/150/200% scaling and maximize/restore/Snap |
| Mac sizes/scaling | Minimum/default/large, Retina and non-Retina where available; resize, traffic lights, fullscreen, menu bar and inactive windows |
| Appearance | Every approved appearance mode, selected/hover/focus/disabled, OS contrast settings and reduced motion |
| Content | Empty, one row, realistic populated lists and large paginated datasets; long Cyrillic/Latin names, addresses, notes and numeric values |
| Operational states | Offline/checking/expired pass, disconnected browser or phone, rate limit/checkpoint, paused/interrupted/cancelled job, unknown send outcome |
| Input/accessibility | Keyboard-only traversal, Narrator on Windows, VoiceOver on Mac, menus/dialog focus return, text scaling and contrast measurements |
| Native integrations | File open/save, system menu/shortcuts, app restart/update, Mac-only Messages guidance, iPhone bridge setup |

Store screenshots during implementation under an agreed evidence folder such as `docs/redesign/evidence/<milestone>/<platform>/`, with a manifest naming screen, state, window size, scaling, build/commit and fixture. Redact fixture identifiers as needed; avoid capturing real customer data. Keep before/after views at the same size. Use screenshot files from the actual app, not browser frames styled as native windows.

Review each capture for alignment, wrapping, truncation, clipping, scroll containment, focus visibility, status ambiguity, column density, destructive-action clarity and unnecessary borders. Correct defects, rebuild when necessary and recapture. Never label a platform verified from a CSS media query or CI compilation alone.

### Completion criteria

- Every current destination and nested feature in the preservation register is accounted for and functional.
- No changes to send behavior, authorization, storage or integration contracts are hidden in a visual refactor.
- Native platform evidence exists, with no unresolved blocking layout/focus/interaction defects.
- Approved appearances, minimum size and scaling matrix pass; unavailable matrix cells are explicitly recorded, not marked passed.
- Relevant checks and native packaging smoke pass. User data/session preservation and updater behavior remain intact.
- Completed milestones are committed on the dedicated branch. Only explicit approval can authorize a later merge or publication.

## 8. Review decisions

Approve or revise: (1) the quiet graphite/iris direction, (2) retaining all current channel/page destinations while improving hierarchy, (3) dark-only first delivery versus system/light/dark, and (4) the actual Windows/Mac validation gate. No extra product capabilities are assumed. After approval, the next deliverable is native baseline evidence and the shell/leads specimen, not a whole-application reskin.
