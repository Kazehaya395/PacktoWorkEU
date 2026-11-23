# Installed validation 20261122T223903 — diagnosis and repair

The original evidence audit below is preserved as historical findings. The implementation/delivery update at the end describes the subsequent repair and its validation.

Evidence: screenshot, scope/jobs/log/errors/quarantine CSVs, two saved listing cursors, baseline JSON and scan-logs.txt uploaded by user. Zero ATS targets; three Career targets. No runtime repair is asserted by this audit.

| Portal | Accepted | Readable | Failed | Unattempted | Listing |
|---|---:|---:|---:|---:|---|
| Poste | 2 | 2 | 0 | 0 | Complete, status ok |
| Intesa | 50 | 46 | 2 | 2 | Partial; source states 248; next offset 50 unavailable |
| Prada | 168 | 86 | 10 | 72 | Partial; source states 284; next offset 168 unavailable |
| Total | 220 | 134 | 12 | 74 | Two partial portals |

There are 314 additional source-stated postings outside the 220 accepted rows (198 Intesa +116 Prada), assuming those published counts remain stable. Thus 60.9% describes only the accepted subset, not full-board coverage. Both listing cursors record the unavailable next page, not a completed census. Browser fallback repeated already observed cards and failed to close the census.

Intesa static phase50.251s; detail wire400.273+319.895s across enrichment/scope HTTP. Prada static phase470.784s; detail wire1720.220+72.363s; shared detail budget exhausted after about922elapsed seconds; company1441.7s. Wire timings are summed concurrent worker time, not wall-clock duration. User host2CPU, approximately4613MB available; HTTP global3/per-host2; one browser session. No429, no active wall classification. Several read/handshake timeouts and two Intesa browser timeouts are explicit.

Critical independent metric: screenshot records2 fresh confirmations,0 reused,218 without confirmed reuse/fresh capture, despite134 readable descriptions. Readability does not prove durable fresh capture. The files do not contain SQLite descriptions/capture hashes, so actual saved-description contents cannot be independently certified from these artifacts. Source inspection finds generic `_remember_detail_html` uses one parser for returned text, then requires exact equality to a second `parse_detail` parser for capture proof. This is a concrete integration risk to reproduce on SuccessFactors fixtures; not yet proven to be the sole installed persistence cause.

Errors/quarantine CSVs have zero rows. That does not negate the12 per-posting failures and74 unattempted rows in the jobs CSV. Scan-log duplicate counts70+18 are repeated observations during traversal, not88 removed seed rows or necessarily88 database duplicates. Seed URLs remain unchanged.

Next investigation: reproduce intermittent next-page failures with bounded transport fixtures, preserve exception/deadline detail rather than collapsing it to blank HTML, test static-to-browser continuation from verified frontier instead of starting again, and verify the exact returned description is the proven/persisted description end-to-end. Do not simply raise budgets, disable TLS, mark partial complete or request another unchanged retry. Local source-level success did not establish installed-app success. No runtime changes, tests or replacement patch were made as part of this evidence audit.


## Implementation / delivery update — 2026-11-22T23:22:18+05:30

Recorded-at is the observed workspace clock (Asia/Kolkata), not an assertion of the current user calendar date.

### Reproduced capture defect

Two live official posting pages reproduced the parser mismatch before editing:

| Page | Legacy returned text | Separate semantic parser | Fresh capture proof |
|---|---:|---:|---|
| Intesa sample | 6,956 chars | 2,644 chars | Absent |
| Prada sample | 5,970 chars | 3,378 chars | Absent |

The generic parser and proof parser selected different text. Longer generic text must not win merely because it includes more page material. The repair does not grant proof to the old generic output or weaken the hash check. It reads only the exact canonical-matched SuccessFactors description container and uses that same text for the returned tuple, capture store, proof and ingestion.

### Changes

- Extend the existing strict Luxottica posting reader to the two explicitly named SuccessFactors hosts (Intesa and Prada), retaining HTTPS job-route, final-destination, canonical URL, exactly-one complete description-container and 200–32,000 character guards. Ambiguous, missing, redirected and oversized descriptions do not gain proof.
- Extract the page's explicit streetAddress and hiringOrganization microdata where supplied. Do not infer location from navigation or the whole page.
- Permit that proven body to replace a longer unproven cached body. Prefer a hash-proven exact/identity-alias capture over longer unproven legacy variants.
- Use the same strict path for browser detail responses, with the actual final page URL. Unsupported/ambiguous named-board HTML is not relabeled readable via the generic whole-page fallback.
- Preserve listing transport exception class/message instead of converting every failed next page into an unexplained empty string.
- Retry a transient next-page failure once at the same observed offset, with at most three retry attempts across the entire listing. Check cancellation/deadline before each attempt and clamp the socket timeout to remaining listing time. Do not retry HTTP429/access walls/certificate-verification failures this way. No configured scan budget, page cap, HTTP concurrency or TLS setting was raised; the small bounded retry allowance is new.
- If a validated table traversal still stops on a recorded transport failure or deadline, keep its accepted prefix and saved frontier cursor, report `complete=False`, and do not spend browser time starting the same validated prefix again. A later Retry can use the existing same-run/parent-run cursor validation. Structure/identity/count failures still allow normal DOM fallback; no completeness guard is removed.
- Diagnostic build marker: **installed-223903-v1**.

### Validation actually executed

- Selected 13-module regression run: **165 passed, 1 skipped**, 8.72 seconds. New tests include canonical/redirect/multiple-container/oversize rejection, exact returned/captured proof, replacement of longer legacy text, identity variants, SQLite persisted text/hash/fresh-counter checks for both hosts, timeout recovery at the exact frontier, retained incomplete census/cursor, deadline/cancellation, retry allowance, and no retry for certificate/429 failures.
- First test run encountered missing system Qt libraries in this fresh test environment; temporary libraries were downloaded/extracted under `/tmp`. Subsequent checks used those libraries. Two new database assertions were corrected to respect ingestion's existing URL normalization, and the browser reader tolerates test doubles without a `.url` property. No application runtime dependency or schema was added.
- Eight-tab isolated Qt GUI startup/close: OK. This did not launch a browser or scan and is not a rebuilt installer certification.
- Live exact-seed source listing checks: Intesa248distinct/10pages in21.65s; Prada284distinct/48pages in81.81s.
- Six detail samples per portal (two each from the uploaded run's readable, failed, and unattempted categories): **12/12** returned text exactly matched the confirmed capture; all12 had explicit nonempty microdata locations. Intesa sample body lengths2462/4155/3069/2856/4607/1779; Prada3188/2161/2596/3167/287/4559. The287-character body is a complete source container within the existing minimum; no claim is made that every source description is long.
- These live checks are not a full532-description replay, not an installed-user-device test, and not proof that transport timeouts cannot recur on the user's network. The retry/no-restart behavior was exercised with deterministic failure fixtures; source listing success alone is not evidence that a live retry occurred.

### Apply and validate

1. Close the app. Apply `PacktoWorkEU-Installed-223903-repair.zip` on top of the previous Italian continuation patch, preserving relative paths. The ZIP contains only five edited/new files: scanner, large-board reader, new regression test, this review, and changelog.
2. Run from the updated source, or rebuild the packaged application. This is not a replacement executable. No dependency installation, migration, seed edit, deleting jobs, or clearing the database is required.
3. Custom Scan: **Career only, ATS disabled, Intesa Sanpaolo and Prada Group only**. Do not include Poste/Bulgari/Generali merely to retest this repair.
4. Confirm the log includes `installed-223903-v1`. Review both listing completion and description capture/fresh-reuse confirmation; a readable counter alone remains insufficient. Live posting totals can change.
5. Export scope/jobs/scan-log/errors/quarantine files and any saved cursors. Persisting timeouts must remain partial, not be called fixed because local source checks succeeded.

Other Italian portals and previously documented Eni/Eurospin/Luxottica limits remain outside this repair's sign-off. Original seed URLs and rows remain untouched.

### Local evidence retention

Original uploads are retained under `evidence/italian-installed-223903/`; new local page samples, before/after proof measurements, live source results and test logs under `.audit-223903/`. These are not included in the edited-source patch. Generated default-app scratch data is moved into that audit directory. No uploaded evidence is deleted.
