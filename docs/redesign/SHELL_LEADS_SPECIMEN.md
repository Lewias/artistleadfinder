# Shell + Leads specimen

Approved scope: dark first; source navigation IDs, ordering, labels, channel grouping and role visibility remain unchanged. No IA change is proposed in this milestone. Page actions are regrouped into a persistent refresh/export toolbar, selection-only actions, and a named maintenance menu. All filter and export scopes remain available. This note precedes the visual implementation.

Initial worktree check: active `redesign/artist-lead-finder-v2`; only the three approved audit documents were untracked. These are retained. No stash command is used in this milestone.

## Evidence setup

The development-only transport is enabled explicitly with `VITE_DESIGN_FIXTURES=1`; production builds do not import it. It uses 1,606 synthetic artists, mixed statuses, long Cyrillic/Latin names, missing followers/contacts and DNC. It filters/sorts/pages in memory and does not forward unsupported methods to a real backend. Native runs use `ARTIST_LEAD_FINDER_DATA=build/redesign-native-data` to isolate the Python core as well.

Baseline captures use the existing native Windows debug host and unchanged baseline UI, with only this fixture transport installed. This is a WebView2/native-host baseline with synthetic data, not a production-account or release-binary verification. After captures use the same host/data/rendering engine. macOS is unavailable and remains unverified.

Only Shell and Leads styling is migrated. Existing other pages retain their styling and handlers; shared layout chrome changes apply around them. LeadDetail's existing RPC/actions/evidence remain intact. No backend contract, domain logic or live data is changed.

## Review gate

Stop after this specimen and its evidence. No migration of remaining screens until explicit user approval. Light appearance, navigation changes, SF Symbols integration and custom chrome are deferred.

## Implemented decisions

- Shared shell: 232px sidebar, quiet selection marker, native system font stack, compact channel toolbar. Native caption controls are untouched. Existing page IDs/order/labels and role filters are unchanged.
- Leads: compact heading and inline totals; all nine original columns; artist name above username; long values truncate in the row and remain available through title/inspector. Missing followers remain unknown rather than looking like zero, including in the inspector.
- Selection: dedicated toolbar, cross-page selection retained, partially selected page has an indeterminate checkbox. Existing outreach/delete payloads are unchanged.
- Export: filtered, selected and all-base scopes retained in a keyboard-operated menu. Refresh and reset remain available; reset is in Actions.
- Inspector: existing detail content, score evidence, status, ignore and outreach safeguards remain in the existing LeadDetail component. At 1280px and above it reserves 350px beside the list; smaller windows use a focus-contained modal sheet. Columns scroll within the table rather than shrinking below readability. Paging remains visible as available table height changes.
- Delete: a specimen-scoped Radix dialog gives initial focus to Cancel, traps focus and returns it to the invoking button. No other screen's shared Modal/Menu was rewritten.
- Error: retained data is explicitly marked stale with an inline retry message. Empty and loading states remain available.
- Existing global CSS is retained because unmigrated screens depend on it; the new semantic sheet is scoped to shell, Leads and its transient surfaces. This is a transition boundary, not a second application-wide reskin.

## Evidence and reproduction

[Evidence manifest](evidence/shell-leads/manifest.json) records dimensions, hashes and capture types.

| Evidence | Before | After |
| --- | --- | --- |
| Native Windows table | [Baseline](evidence/shell-leads/windows/before-native.png) | [Specimen](evidence/shell-leads/windows/after-native.png) |
| Native Windows inspector | [Baseline](evidence/shell-leads/windows/before-inspector-native.png) | [Specimen](evidence/shell-leads/windows/after-inspector-native.png) |
| Browser interaction states | — | [Selection](evidence/shell-leads/browser/02-selection.png), [menu](evidence/shell-leads/browser/03-export-menu.png), [dialog](evidence/shell-leads/browser/04-delete-dialog.png), [error](evidence/shell-leads/browser/06-error.png), [empty](evidence/shell-leads/browser/07-empty.png), [filters](evidence/shell-leads/browser/08-filters.png) |
| Browser size checks | — | [1024 table](evidence/shell-leads/browser/09-table-1024.png), [1024 sheet](evidence/shell-leads/browser/10-inspector-1024.png), [1920 table](evidence/shell-leads/browser/09-table-1920.png), [1920 inspector](evidence/shell-leads/browser/10-inspector-1920.png) |

Windows evidence uses the existing Tauri debug host (binary timestamp 2026-11-13 12:32 local, SHA-256 `4287D2EE63B4230EC6DE64000A066E9FF6CF8E6829B33C0896603C217CA4EF2D`) and branch frontend served by Vite. Captures are 1362×932 including native borders/caption, for approximately 1360×900 content. Host OS build: 26200.9974 / 25H2 (Windows 11; registry's legacy ProductName reports Windows 10 Home). No Rust/window changes were made. This is actual native-host/WebView2 inspection, not a newly packaged installer or a full Windows scaling certification. System DPI and exact WebView2 version were not independently measured.

Baseline frontend is commit `01d765446809bbae101286e2d5eb6a42cb81abeb` with only development fixture instrumentation added before the capture. After screenshots are from this milestone's working tree, committed alongside this report. The native screenshot tool includes the visible pointer highlight; no chrome or screenshot content has been painted over or fabricated.

To reproduce safely in PowerShell:

```powershell
$env:VITE_DESIGN_FIXTURES='1'
npm run dev
# Separate terminal; browser-only UI checks and supplemental captures:
.venv/Scripts/python scripts/check-shell-leads.py
# For native inspection, use the existing debug host with isolated core data:
$env:ARTIST_LEAD_FINDER_DATA=Join-Path (Get-Location) 'build/redesign-native-data'
& ./src-tauri/target/debug/artist-lead-finder.exe
```

The checker refuses to operate unless the fixture marker is present. Its `!offline` search value deliberately simulates failure in this fixture transport; it has no production behavior. Stop the fixture Vite before a normal development session and unset `VITE_DESIGN_FIXTURES`/the data override in that terminal. This phase has not replaced the portable or installed application.

## Validation and remaining limits

Passed: TypeScript, ESLint, production frontend build, all 61 Vitest tests (16 files), Ruff for the new UI checker, and the Playwright interaction/size suite. Production output was checked for fixture-specific markers and contains no fixture transport. Vite still reports a main chunk larger than 500kB; bundle splitting is outside this specimen.

The interaction suite exercises status filtering, paging across the 54-page dataset, selection/indeterminate state, menu arrow keys/Escape, delete-dialog focus/return, inspector open/close and missing data, stale/error recovery, empty results and 1024/1920 layouts without document-wide horizontal overflow. Screenshots were inspected; the table-height correction keeps paging visible when the inspector increases header height, and narrow inspector captures now await loaded content.

Native Windows table and inspector were visually inspected. macOS, Windows DPI variants, Narrator/VoiceOver, native export completion, real sending, backend regressions, updater/installer and full release packaging remain unverified. No claim of cross-platform completion is made. User-owned data and remote services were not modified. Remaining screens were not redesigned.
