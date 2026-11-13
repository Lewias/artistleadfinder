# Artist Lead Finder — UI/UX audit

Date: 2026-11-13. Baseline: 0.11.0, commit `01d765446809bbae101286e2d5eb6a42cb81abeb`.

## Evidence standard

This is a source-based interface audit. No current application build was visually inspected on Windows or macOS. Confirmed implementation facts are distinguished from layout risks and proposed changes. Previously captured screenshots or historical log incidents are not current-platform verification.

## Product strengths to retain

- Channel-oriented navigation already separates Instagram, iMessage and Email and remembers the last destination per channel during the session.
- Common workflows connect discovery, qualification, outreach, replies and CRM rather than ending at a decorative overview.
- Existing surfaces expose operational constraints: sender health, limits, pauses, run progress, offline access and role-specific capabilities.
- Dense CRM/lead lists, provenance and classification explanations are valuable product assets.
- Shared tokens, common page headers/buttons, focus-visible outlines, reduced-motion rules, error boundaries and explicit loading states provide a useful base.
- Native file dialogs and a constrained desktop bridge are already available; preserve them.

## Findings and recommended response

Severity describes user impact if the risk is confirmed, not a measured defect count.

| Priority | Evidence / finding | User consequence | Recommended treatment and verification |
| --- | --- | --- | --- |
| High | Shared `Modal.tsx` declares a modal and handles Escape/backdrop, but contains no focus trap, initial-focus or restore-focus handling | Keyboard focus can escape the task; destructive or draft workflows can lose context | Adopt the installed accessible dialog primitive through one wrapper; test Tab/Shift+Tab, Escape, nesting, scroll lock and return focus |
| High | `Menu.tsx` handles outside-click/Escape but supplies no arrow-key navigation or managed menu focus | A menu role alone does not deliver expected keyboard behavior | Standardize menu interaction, Home/End/arrows and focus return; test screen-reader names and disabled actions |
| High | `SectionTransition` keys and remounts each destination; form drafts/selection live in page-local state | Navigation can discard local work or context; current behavior needs runtime confirmation per page | Inventory draft ownership first; preserve existing semantics during visual migration, then approve any persistence/unsaved-work behavior explicitly |
| High | Core, server, browser session, individual sender and bridge are independent state domains (`App`, account runtime and domain services) | A generic connection badge can suggest the wrong recovery action | A compact status summary with labeled detail: local core, account server, channel account, active task; action only when the owning layer is known |
| Medium | `theme.css` is one 5,675-line global cascade with 80 literal hex occurrences, multiple responsive sections and page-specific rules | Cross-screen regressions become hard to attribute | Introduce semantic tokens and scoped component/feature styles gradually; remove replaced rules as each page migrates |
| Medium | Current type stack prioritizes Inter on both platforms | Shared appearance dominates native typography | System SF on macOS, Segoe UI Variable on Windows; keep a fallback stack and measure wrapping on both engines |
| Medium | `.page-header` includes eyebrow, title/count, description and actions; CSS defines a 38px heading size | Repeated large introductions may consume working space, particularly at the minimum height | Test a 22–24px title and compact toolbar; move explanatory paragraphs to contextual help/setup states |
| Medium | Sidebar is fixed at 262px, topbar 62px; main window minimum is 1024 × 700 | Small-window tables and multi-board layouts have limited usable space | Test the true minimum first; propose a 224–240px sidebar and responsive secondary panes without hiding essential actions |
| Medium | Gradients, blur, 16–22px panels and pill controls recur across CSS; many pages use `panel board` | Surface hierarchy may flatten into nested cards and equal-weight controls | Flat work surfaces, restrained separators, a single action hierarchy and small functional groups; verify against actual screenshots |
| Medium | Page entry motion lasts 380ms with staggered panel/text reveals | Frequent switching may feel slower and distract from changing operational data | Short opacity transitions with no stagger on tables/live counts; honor reduced motion and never animate row positions during interaction |
| Medium | Email, iMessage campaigns and CRM combine substantial workflow state with presentation | A full-page replacement can silently remove a secondary action or change delivery behavior | Use the preservation register; migrate UI around existing handlers and verify payload/state parity |
| Medium | Navigation has 21 destinations and shared CRM rendering across channels/roles | Labels alone do not clarify channel or contact ownership in every context | Keep channel context persistent and show owner where relevant; retain current destinations until approved IA refinements |
| Medium | Several controls use icons and dense metadata; focus styles exist, but no full rendered accessibility audit is present | Naming, contrast, zoom and click targets remain unproven | Validate accessible names, selected/disabled states, non-color status cues, text scaling, Narrator and VoiceOver |
| Medium | Large tables and polling are present; `useResource` retains data while fetching | Selection, scrolling and stale/error messaging need behavior-level validation | Keep rows stable, show refresh/stale states unobtrusively, preserve paging; virtualize only after profiling and accessibility review |

## Screen-level audit and target hierarchy

| Surface | What must be easy to understand | Design intervention |
| --- | --- | --- |
| Account gate | Checking vs server offline vs login vs activation; next valid action | Compact form with specific recovery guidance and visible progress; preserve offline expiry |
| Find and send | Selected sender, sources, prepared messages and whether automation is running | One primary task toolbar; ordered source/message workspace; persistent activity and explicit start/pause/stop |
| Manual outreach | Audience eligibility, exclusions, message variants and preview | Table plus contextual editor; selected count and preview before existing start action |
| Instagram replies | New findings, contact extraction and CRM destination | Thread/finding list and inspector, with read/scan state separate from sending |
| Accounts | Session identity, proxy, open browser and authentication health | Operational table with explicit health and row actions; a cookie count must not imply valid login |
| Spotify | Discovery inputs, progress, artist eligibility and Instagram handoff | Filtered results table and selected-artist inspector; retain separate run controls |
| Leads | Qualification evidence, contact data and next action | Quiet table, sticky column headings, selection toolbar and detail inspector |
| CRM variants | Owner/channel, status, next action, money and communication history | Shared column system with channel adapters; contextual edit and dedicated trash state |
| History | Active/paused/interrupted state and associated results | Chronological run list with compact detail; distinguish hiding history from deleting leads |
| Statistics | Outcomes and bottlenecks | Compact totals and meaningful summaries; charts only where they clarify a decision |
| iMessage campaigns | Recipient/message preparation vs iPhone pairing and actual delivery | Split composition from connection setup; show step/attachment preview and delivery state |
| Templates/logs | Reuse and diagnose | Folder/list/editor for templates; readable chronological log with status filtering |
| Mac dialogs | Who needs a reply, conversation context and send state | List/thread composition with capability-specific guidance on Windows |
| Email campaigns | Audience, mailbox, recipient-time schedule, variants and followups | Consistent campaign workspace with visible sender and preview; maintain plain-text constraints |
| Email mailboxes/warmup | Sending health vs warmup placement and pool activity | Separate table/workspace for each; expose app-password failures without revealing credentials |
| Assistant | Proposed action vs executed action; usage and channel context | Conversation plus draft/action inspector; preserve explicit send/execute confirmation |
| Admin/settings | Scope and consequence of changes | Grouped settings and compact administrative tables; precise confirmation for destructive/permission actions |

## Visual direction for review: a quiet artist workspace

Use a neutral graphite workspace with a restrained iris accent and a warm, readable text hierarchy. The product identity should come from consistent artist rows, carefully aligned metrics, the source-to-contact workflow and understated typography. Avoid audio meters, waveform decorations, large welcome cards, glowing gradients or ubiquitous rounded panels. Music context belongs in artist identity and evidence, not decorative studio controls.

Tables and lists are the default working surfaces. Use whitespace and alignment to group content; use a border when it separates different interactions. Panels are appropriate for inspectors, setup tasks and isolated editors, not every group of numbers. Status colors identify operational meaning and always have a text label.

Reference principles: Apple Music for content identity, Finder for lists and inspectors, Raycast for concise actions, Things for calm hierarchy, Linear for dense scanning, and Fluent for Windows interaction. These are interpretation targets, not claims of a measured comparison or permission to clone their layouts.

## Platform considerations

Keep one shared product architecture and adapt typography, command modifiers, menu placement, window chrome, icon mapping and capability messaging. macOS needs real menu-bar commands and native window behavior; Windows needs native caption buttons, system menu and snap behavior. Start with native decorations on both. A custom titlebar is a separate proof point, not a CSS requirement.

Use system fonts without copying SF font files into Windows bundles. Introduce a semantic icon wrapper: native SF Symbols integration on macOS requires a verified rendering/distribution path and symbols compatible with the minimum supported OS; Fluent-compatible equivalents serve Windows. Keep the current Lucide set during early layout migration until this path is validated. An icon swap must not become a platform-verification claim.

Platform guidance consulted: [Apple macOS HIG](https://developer.apple.com/design/human-interface-guidelines/designing-for-macos), [Microsoft Windows typography](https://learn.microsoft.com/en-us/windows/apps/design/signature-experiences/typography), and [Apple SF Symbols](https://developer.apple.com/sf-symbols/). These support keyboard-first desktop work, system integration, platform typography and symbol alignment; exact sizes and architecture below are project recommendations.

## Runtime evidence still required

Before changing visual foundations, capture current native builds on each platform with known fixture data and build/commit identity. Inspect 1024 × 700, default 1360 × 900 and a large desktop window; Windows 100/125/150/200% scaling and macOS Retina/non-Retina where available. Include long Russian labels, long names/addresses, empty and large lists, offline/error/loading, selected rows and modal/menu states. Capture light/dark if both are approved for delivery.

A browser preview is useful for layout iteration but cannot validate native titlebars, system menus, permissions, file dialogs, WebKit rendering, Windows snapping, installer behavior or Mac-only Messages. Current status for both platforms: **not visually verified in this phase**.
