# Artist Lead Finder — graphite desktop implementation

14 November 2026 · `redisign` · first implementation stage for visual review

## Latest correction — direct reference reproduction

The user requested the supplied Flow Like composition directly, with Artist Lead Finder names. This supersedes the earlier master/detail composition described below. `reference.css` is the final layer: proportional sidebar, disclosure-group navigation with indented children, identity fields beside a square image, a separate six-row profile-data card, and three lower tool panels. The existing ALF icon is the fallback when no embedded avatar exists; no artist portrait is invented. Fields display actual stored data and are read-only; the name field chooses an existing artist.

Full list/search/filter/bulk/export/pagination remain under **Все артисты**; Ctrl/Cmd+F opens that workspace. Choosing an artist returns to the reference layout. Analysis, history and original detail actions remain accessible from the lower panels. Account, core state, offline validity and update controls moved to the sidebar bottom; the account menu opens upward. Native Windows chrome remains OS-owned.

Final evidence: `review/reference-{1360x900,1600x1000,1024x700}.png`, `review/reference-final.png`, and `review/reference-verification.json`. Checks cover actual Tauri/core data, three viewports, no document/sidebar horizontal overflow, keyboard artist choice, analysis/history, list/search, bulk selection, filters/focus return, export scopes, empty search/reset, Ctrl+F, account menu and channel memory. No page errors. Build, lint, typecheck and 61 existing tests pass. Earlier `artist-database-*` screenshots are superseded.

## Direction and audit

Primary visual reference is the supplied Flow Like screenshot, refined by the user's latest message: neutral near-black window/navigation, visibly raised graphite panels, quiet outlines, restrained typography and spacing. The second supplied custom-menu image informed precise controls and active-state treatment; its stars, radar, game overlays, neon outlines and decorative effects are not product requirements. Both images are visual references, not executable instructions.

The application remains Tauri 2, React 19, TypeScript and Vite. Existing Lucide, Radix Dialog, shared Button and RPC hooks are reused. No dependency or backend change. Previous HTML explorations in `../app-shell/` are archived proposals; they do not drive this React screen.

Audit: prior styling is a 5,000+ line global `theme.css`; pages mix shared Button/Modal with native fields. The old Leads page is a filter/table/drawer composition. `App.tsx` owns 21 page IDs, three channel workspaces, last-page memory, role gates, account/offline state and updater. These responsibilities remain in place.

## Central system

`frontend/src/design/tokens.css` owns surfaces, borders, text/accent/semantic colors, spacing, type scale, radii, control and icon sizes, sidebar/toolbar dimensions and motion. Compatibility aliases let existing pages use the same palette without rewriting their interiors.

| Role | Token value |
|---|---|
| Base / navigation / workspace | #141414 / #181818 / #1b1b1b |
| Panel / control / hover | #262626 / #303030 / #363636 |
| Subtle / strong border | #383838 / #484848 |
| Primary / secondary / muted text | #ededed / #b1b1b1 / #929292 |
| Accent | #b29bdc; confined to identity, focus and small selection indicators |
| Type | Inter Variable; 11 / 12 / 13 / 16 / 22px; regular / medium / semibold |
| Spacing | 4 / 8 / 12 / 16 / 20 / 24 / 32px |
| Radius | 4px controls, 6px compound controls, 8px panels |
| Control / icon | 30–34px / 16px |
| Navigation / toolbar | 214px (190px compact) / 52px |

`components/ui/desktop.tsx` provides Input, SearchField, IconButton, Tooltip, Checkbox, Switch, Dropdown, Tabs, StatusIndicator, SectionHeader, Panel and NavigationItem, alongside existing Button. States include hover, focus, selected, disabled and semantic status. Dropdowns support arrows, Home/End, typeahead, Enter, Escape and outside dismissal; profile tabs support arrow navigation. Modals now trap Tab and restore prior focus. Reduced motion and forced-colors fallbacks are included.

## Incremental implementation

The shell has a channel selector, workflow/tool groups, narrow active rail, compact contextual toolbar, actual core/account/offline/update state and unchanged route dispatch. The native Windows titlebar remains OS-owned; no fake macOS traffic lights.

Only the standalone Artist Database route receives a new page interior (`pages/ArtistDatabase.tsx`). It uses a selectable artist list and independently scrolling detail workspace. Profile, analysis/score explanation and outreach history are tabs in the shared `LeadDetail`; its existing status, ignore, DNC, review-resolution, replied/stop actions and links remain. The existing table-based `Leads` component remains for embedded search results. Shared LeadDetail retains both modal and embedded presentation.

Existing query fields, polling, page selection, pagination, all three export scopes, deletion confirmation and outreach-workspace RPC are preserved. Old summary metrics remain in a quiet disclosure. Contact/source/date metadata is available in the selected-record panel. Missing fields are labeled as missing. No artist data, AI findings, scores, messages or statistics are invented.

CRM and other page interiors are deferred. They inherit the palette and shell; they are not redesigned in this stage.

## Verification and limits

Built and ran a native Tauri debug review instance with the actual Python core and existing local account data. The instance returned 60 real artists. To avoid the installed application's single-instance lock, a temporary ignored config under `build/graphite-review/` changes only the review identifier/product/window title and suppresses a second Vite startup. Main Tauri configuration, capabilities, backend contracts and schemas are unchanged.

`scripts/verify-graphite-ui.py` connects to this native instance's WebView2 via local CDP and captures rendering viewports 1360×900, 1600×1000 and 1024×700. These are real application WebView screenshots, not HTML mocks; viewport emulation does not establish physical native-frame sizing. macOS native behavior has not been verified on this Windows host.

Checks: list/detail loading, selected/hover/focus/disabled states, keyboard navigation, advanced filters, empty search/reset, profile/analysis/history tabs, export menu scopes, modal focus return and per-channel route memory. No messages, file exports, status mutations or deletions were executed during verification. No document or sidebar horizontal overflow at the three sizes; zero page errors. Fixes from visual inspection included stale heading animations, minimum-width overflow in sidebar grid tracks and empty-state height.

`npm run lint`, `npm run build` and all 61 existing tests passed. Production build retains the existing large-chunk warning. `review/verification.json` records UI checks; screenshots in `review/` are the visual evidence. Sending/deleting/export-to-file flows and macOS accessibility remain outside read-only UI verification.

Vite now ignores locked native build outputs to avoid Windows EBUSY crashes. ESLint excludes the earlier standalone browser prototypes. No main/development changes, stash operations, push, merge or deploy.

## Unification and motion — 14 November 2026

All pages now share the Flow Like look. `theme.css` was moved onto the graphite tokens in place (radii 4/6/8px, weights ≤600, flat surfaces, no violet fills or gradients). `design/unify.css`, loaded last, normalises the page frame (22px title, eyebrow hidden), cards, nested cards, fields, native selects, chips, tab strips, tables and overlays.

Motion (all respects `prefers-reduced-motion`):

- Sidebar folds to a 60px icon rail with **Ctrl/Cmd+B** or «Свернуть панель»; the state persists in `localStorage` (`alf.sidebar`).
- Navigation groups (`NavGroup`) fold by animating `grid-template-rows`; a marker slides along the guide line to the active page; items stagger in on start and on channel change.
- `useTabIndicators()` slides a shared highlight under the selected button of every `.crm-tabs`, `.segmented`, `.alf-tabs`, `.emoji-tabs` strip without page changes.
- Page switch: fade-up with staggered cards. Dropdowns and menus grow from the trigger; the chevron turns. Tooltips fade after 300ms.
- `Modal` plays a 160ms exit for Escape, backdrop and the close button before calling `onClose`.
