# Quiet Premium · Leads

The primary task is scanning artists and deciding which record to inspect. The existing view spends vertical space on three counters, an idle selection prompt and permanently expanded primary filters. Nine fixed-width columns cause horizontal scrolling when the inspector opens.

- Keep the shell routes and labels. Use a neutral active sidebar row with a violet icon.
- Open with the title and filtered count. Keep refresh, export and reset available. Put search first on the next line, beside one filter disclosure with the number of active conditions. Keep global qualified/today totals in the footer.
- Use one unframed list. Give identity the most space, followed by genre/type, audience, score and status. Use names and handles alone, without image or initial avatars. Keep contact, discovery and date information in the inspector. Preserve the explicit do-not-contact warning.
- Open the inspector from a row. Keep checkboxes independent and show the bulk toolbar only while selected. Wide windows reflow the list; smaller windows use the existing modal sheet.
- Use system type, graphite surfaces and restrained violet for interaction. Preserve native window chrome, service methods, permissions, outreach and storage. Scope sidebar updates to Leads.

## Review evidence

The four PNGs in `evidence/quiet-leads/` show the production frontend in Windows Chromium at 1360×900 and 1024×700. An isolated read-only review transport called the existing lead list, detail and dashboard queries against a local snapshot of 60 Instagram records. The host and account indicators in these images are test harness state. Native release builds were not verified in this revision.

The fixture-only UI suite additionally exercised pagination over 1,606 records, filtering, menu keyboard navigation, selection, inspector return focus, status changes, confirmed deletion, error recovery and empty results. Export menu navigation was tested; native file saving and live outreach were not exercised.
