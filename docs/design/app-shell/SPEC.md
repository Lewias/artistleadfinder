# Artist Lead Finder — shell direction review

13 November 2026 · branch `redisign` · **proposal; implementation awaits approval**

## Intent and evidence

A precise desktop workspace for discovering artists and preparing outreach. Identity comes from the existing audio-line mark, restrained violet interaction accent, artist names, and well-composed information. No decorative dashboard, hero, carousel, KPI cards, or extra routes.

Inspected: `package.json`, `src-tauri/{Cargo.toml,tauri.conf.json,tauri.production.conf.json}`, `frontend/src/{App.tsx,navigation.ts,theme.css,services/api.ts,services/types.ts,pages/Leads.tsx,components/LeadDetail.tsx,components/ExportBar.tsx}`, and the existing fixture preview script. Tauri 2 + React 19 + Vite; Lucide; Radix dialogs; Python core through Rust RPC. The window is 1360×900, minimum 1024×700, with native decorations. macOS deployment minimum is 12.0. Existing shell has a 262px sidebar, a 62px channel/status toolbar, per-channel last-page memory, role-dependent navigation, account/offline state and updater. Lead details currently open as a modal drawer. Existing UI_SPEC.md references Verse; this proposal supersedes that visual reference for the shell only, pending approval.

## HIG interpretation

Official pages read on 13 November 2026:

| Source | Application to this proposal |
|---|---|
| [Design principles](https://developer.apple.com/design/human-interface-guidelines/design-principles) | Purpose, agency, familiarity, flexibility, simplicity and craft: keep the artist task central; expose state and recovery; make every control earn its space. |
| [Designing for macOS](https://developer.apple.com/design/human-interface-guidelines/designing-for-macos) | Resizable windows, keyboard workflows, menu commands and fewer modal interruptions. |
| [Layout](https://developer.apple.com/design/human-interface-guidelines/layout) | Alignment and whitespace express hierarchy; disclose complexity; retain context when resizing. |
| [Typography](https://developer.apple.com/design/human-interface-guidelines/typography) | Readable system typography, limited weights, hierarchy that survives text scaling. |
| [Materials](https://developer.apple.com/design/human-interface-guidelines/materials) | Separate controls/navigation from content; use effects sparingly. Glass is not an all-purpose content surface. |
| [Color](https://developer.apple.com/design/human-interface-guidelines/color) | Stable semantic roles, restrained accent, readable light/dark/high-contrast variants. |
| [Navigation and search](https://developer.apple.com/design/human-interface-guidelines/navigation-and-search), [Sidebars](https://developer.apple.com/design/human-interface-guidelines/sidebars), [Toolbars](https://developer.apple.com/design/human-interface-guidelines/toolbars) | Distinguish navigation from actions; group frequent actions; make sidebar and inspector optional; preserve discoverability. |

Secondary references: [Apple Music](https://www.apple.com/apple-music/) for name-first content and typographic refinement, [Raycast](https://www.raycast.com/) for compact controls, clear focus and keyboard precision. These inform composition; neither product's branding or imagery is copied.

## Shared specification

1. **Window.** Keep 1360×900 and 1024×700. Native titlebar remains OS-owned: Windows captions at right with Snap/system menu; macOS traffic lights at left, normal fullscreen and window menu behavior. The web toolbar sits below native chrome. A unified macOS titlebar is a possible later native enhancement, outside the initial implementation. Preview titlebars are illustrative, not functional OS controls.
2. **Navigation.** Preserve all 21 PageIds and all three channels; changing channels restores that channel's last page. Main grouping follows workflow, not object counts. No count on every route. Keep existing initial `discovery` route; previews use Leads solely to compare shell composition. Staff/admin gates remain exactly as today. UI hiding is never a replacement for authorization.
3. **Toolbar.** One 52px application toolbar: location, local search, then content commands. Lead search is explicitly scoped to the artist database. Export opens a menu for filtered / selected / all; selection enables Add to outreach. Refresh remains visible; reset and destructive actions move to explicit menus. Delete keeps its existing confirmation and consequences. No back button unless real navigation history exists. Service health is quiet when healthy; disconnected/offline expiry/update problems get a top-level actionable notice.
4. **Content.** A shared shell slot accepts existing pages without restyling their internals. Leads is the only page adapted after approval. Fixed shell chrome, independent content and inspector scrolling, sticky collection controls, 24px content gutter, 4px spacing base (8/12/16/24/32). Avoid nested panels, repeated headings and vertical gridlines. Preserve all collection columns through column disclosure or horizontal scrolling; narrow mode never discards data. Pagination stays available near collection controls, with a secondary position indicator below.
5. **Sidebar / inspector.** A: sidebar 220px (resizable target 200–280), inspector 300px (280–380), center fluid with at least 560px. Below 1180px hide inspector by default; opening details uses an explicit dialog with focus return. Sidebar can be hidden to reclaim width. B: no permanent sidebar; channel tabs plus section navigation; list 390px (320–480), detail fluid. At minimum size use a 340px list and fluid detail; at enlarged text use list-to-detail navigation with a clear return control. Splitter resizing is proposed, not simulated. Persist user widths and visibility locally after implementation.
6. **Typography.** Platform system fonts: `-apple-system, BlinkMacSystemFont` on macOS; `Segoe UI Variable, Segoe UI` on Windows; system fallback. No bundled SF on Windows. Body/navigation 13px, metadata 12px, toolbar title 15px/600, artist title 22px/600 maximum, row name 13px/600. Regular 400, medium 500, semibold 600 only. 1.45 body line height. Tabular figures for numeric columns only. Respect OS scaling/webview zoom; 125/150/200% acceptance checks required after implementation.
7. **Color / materials.** Light: content #fcfcfd, chrome #f2f2f5, text #25242a, secondary #66646e, accent #6550bd, selection #ece7f8. Dark: content #1b1b1f, chrome #222226, text #ecebf0, secondary #aaa7b2, accent #b9a5fa, selection #373044. Violet marks interaction, never artist quality; status has text and a symbol. Red is reserved for danger; green for a confirmed healthy state. Subtle dividers only at structural boundaries. Small 5–7px control radii, no pill-everything styling. Content opaque. Optional real OS material for navigation only if feasible, with opaque fallback; no promise that CSS blur equals native vibrancy or Liquid Glass. Theme follows system with explicit user override. Forced-colors support and reduced-transparency fallback required.
8. **Interaction.** Visible focus; 28–32px controls, 32px nav rows, 56px A collection rows / 76px B list entries. Hover does not expose essential commands exclusively. Up/Down moves active artist in the focused collection; Space toggles bulk selection; Enter opens details; Escape closes the top transient surface and returns focus. Active row and bulk selection are separate. Cmd/Ctrl+F focuses local search; Cmd/Ctrl+K opens section/action navigation; Cmd/Ctrl+A selects the collection only when that collection has focus, and retains normal text-selection behavior in inputs. Cmd+comma on macOS opens Settings; Windows uses Ctrl+comma, without overriding Alt+F4 or system shortcuts. Native macOS menus mirror toolbar commands; Windows provides an accessible application menu and native dialogs. Context-menu commands have visible menu equivalents. Show progress/errors inline without clearing selection or moving focus. Honor reduced motion; state changes 120–160ms without ornamental page transitions.

## Two directions

**A — Library.** Persistent channel selector and workflow navigation on the left; broad, scannable collection in the center; optional contextual inspector on the right. Familiar, discoverable, best for comparing many artists and bulk work. Trade-off: three columns consume width and the inspector needs disclosure for long evidence. Recommended starting direction for this multi-channel product.

**B — Focus.** Three channels in the upper bar, section navigation directly below, artist list on the left, selected profile as the primary content canvas. Channel-specific secondary routes live in a labeled More menu. Better for reviewing evidence and making decisions one artist at a time, while keeping the next artist nearby. Trade-off: less simultaneous numeric comparison, more navigation disclosure, and bulk workflows need deliberate selection mode. Other pages would occupy the full existing content slot; the split view belongs only to Leads, not to every route.

Both directions support both appearances; their distinction is structural. Screenshots include the same dark appearance for comparison. Neither direction adds a new data field, saved-list feature, album art dependency or backend behavior.

## Route preservation map

| Channel / visibility | Existing IDs | A location | B location |
|---|---|---|---|
| Instagram | discovery, spotify, leads, crm | Left workflow navigation | Section bar |
| Instagram | profiles, history, dashboard | Left tools group | More menu |
| Instagram, staff | crm-users | Left workflow navigation | More menu |
| iMessage | imessage, imessage-dialogs, imessage-crm, imessage-templates | Left workflow navigation | Section bar |
| iMessage | imessage-log | Left tools group | More menu |
| iMessage, staff | imessage-crm-users | Left workflow navigation | More menu |
| Email | email, email-crm, email-accounts, email-warmup | Left workflow navigation | Section bar |
| Global, staff | assistant | Application menu / sidebar utility | Application menu |
| Global, admin only | admin | Application menu / sidebar utility | Application menu |
| Global | settings | Sidebar utility / application menu | Application menu |

Account menu, sign-in gate, role checks, update notice/install, core retry, offline validity, all export scopes, all query fields/sorts, page selection, pagination, selected outreach, deletion, modal focus handling, status editing, DNC, ignore, service-fit evidence, classification, unknown/private fields, external/source links, outreach review resolution, message history, mark replied and stop conversation must remain. Long detail sections use disclosure, not removal. LeadStats data remain accessible through a compact collection-summary disclosure; no permanent metric cards. Exact existing RPC methods, payloads, models, polling and integration ownership remain unchanged.

## Review prototype and boundaries

`index.html` is a standalone, local design exploration, entirely outside application entrypoints. Open it to switch A/B, platform and appearance. Working local interactions: row focus/selection, search, genre/status filters, details, sidebar/inspector visibility, route menus, export scope menu, confirmation preview and keyboard navigation. All records and statuses are explicitly fictional. Non-Leads navigation shows an unchanged-page placeholder; no other page is redesigned. No network calls, RPC, file exports, outreach or deletion occur. The preview does not prove native macOS behavior, native materials or production integration.

Approval gate: choose A or B and request any composition adjustments. Only then implement the chosen shared shell and adapt Leads, leaving other page internals, backend, API/RPC, permissions and integrations intact. No push, merge, deploy, stash access or changes to main/development.

## CRM exploration — requested scope extension

The user subsequently requested a CRM tab in the same style. CRM is now included in the isolated approval prototype for both directions; this does not approve production shell or CRM implementation. Other pages retain placeholders.

Inspected `pages/Crm.tsx`, `components/crm/{CrmModals.tsx,crmDraft.ts,crmText.ts}` and CRM types. Preserve the existing All / Attention / Trash model, channel-specific CRM books, own/other-owner restrictions, multiple configurable statuses, contact channels and primary channel, last-contact date, next action/date, notes, earned/potential amounts in dollars, selection, sorting, pagination, import/merge, XLSX/CSV export, write/conversation actions, trash/restore and permanent purge.

A uses the shared sidebar, toolbar and contextual inspector. The table prioritizes contact/channel, status, next action/due date and amounts; last contact, full notes and all channels stay in the inspector/editor. B uses the same channel/section bars as Leads, with a compact contact list and a larger contact canvas. Next action and notes lead the canvas; channels and financial details form the secondary column. Existing totals appear as quiet inline values or a summary command, without KPI cards. Today and overdue dates use text plus calendar/bell symbols and restrained semantic color.

Working local review interactions include tabs, contact/status/note search, status filter, active contact, bulk selection, keyboard navigation, add/edit form, multiple labels, trash and restore. File transfer and message actions explain their existing destinations without performing external work. Records and dates are fictional; no data model, API/RPC, permission or integration is changed. Primary previews: `previews/crm-{a,b}-dark-windows-1360x900.png`; light, minimum-size, wide-window and macOS chrome variants are also rendered. Native behavior is still illustrative.
