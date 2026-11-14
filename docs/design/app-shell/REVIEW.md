# Review package

Open `index.html` locally. The bottom strip switches shell direction, platform illustration and appearance. It is review tooling, not part of the proposed application shell. No server, core or network access is needed.

- `SPEC.md`: rationale, shared dimensions, typography/material rules, platform behavior, complete route map and feature-preservation constraints.
- `previews/`: rendered desktop concepts; both directions at 1360×900, 1024×700 and 1920×1080, opposite appearance variants, and illustrative macOS chrome.
- `previews/verification.json`: completed local checks and rendering inventory.
- `render-review.py`: reproduce using the repository Python environment and its installed Chromium. Windows execution here required permission to launch the browser outside the restricted sandbox.
- `build-icons.mjs`: rebuild `icons.js` from the project's existing React/Lucide packages. No new dependencies.

Validation: rendered ten combinations; checked collection/detail synchronization, arrow navigation, Space selection, search and empty state, genre filtering, reset, export scopes, per-channel navigation memory, inspector visibility and narrow-window detail dialog. No JavaScript page errors or remote requests in the isolated prototype. Visually inspected the primary directions, minimum-size layouts, and appearance/platform variants.

Current application inspection used owning source files listed in SPEC.md. An additional attempt to render the existing Vite application with its existing RPC fixtures timed out loading localhost; no baseline screenshot or running-app verification is claimed. The isolated concepts rendered successfully. Native macOS behavior, real materials, screen-reader operation, production data and OS scaling are implementation acceptance checks, not established by these concepts.

CRM extension: the user subsequently requested the CRM tab in the same style. Both shells now include CRM, using `crm-preview.js` and `crm-preview.css`. Open CRM from navigation, or append `?page=crm` to the local preview URL. `render-crm-review.py` produces CRM previews and `previews/crm-verification.json`. The approved scope remains visual exploration; other pages and production files are unchanged.

All additions are confined to `docs/design/app-shell/` on `redisign`. Existing application code and configuration remain unchanged. Awaiting visual-direction approval before shell implementation.
