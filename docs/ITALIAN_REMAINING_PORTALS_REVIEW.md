# Italian portals — continuation patch

## Apply

Apply `PacktoWorkEU-Italian-continuation-patch.zip` on top of the previously supplied Italian career patch, preserving the relative paths. This is an edited/new-files-only source patch, not a full codebase or a rebuilt Windows installer. Stop the app before replacing source files. A packaged executable must be rebuilt from the patched source; copying Python files beside an existing executable is not sufficient.

No saved seed URL, source CSV, dependency, database schema, request cap, company budget, detail budget, browser quota or concurrency limit was changed. Do not replace the saved Eni seed with the published board used in the controlled adapter checks below. No duplicate seed rows were removed.

## Runtime changes

- `packtoworkeu/scanning/common.py`: optional URL-aware verified TLS context. Complete the chain only for the exact HTTPS hosts `jobs.eni.com` and `carriere.posteitaliane.it`, with no credentials and no nonstandard port. Existing no-argument callers remain supported. Preserve OS root trust, CERT_REQUIRED and hostname verification; disable partial-chain trust when adding the intermediate.
- `packtoworkeu/scanning/tls_intermediates.py` (new): two public intermediate certificates, obtained over verified HTTPS from their issuers. Source URLs, SHA-256 fingerprints, issuers and expiry dates are documented beside the certificates. They are not root replacements, are never downloaded dynamically, and are Python data so the existing frozen-build collection includes them.
- `packtoworkeu/scanning/career/oracle_public.py` (new): require exactly one published HTML base, an HTTPS Oracle Cloud API origin with no credentials/nonstandard port/path/query/fragment, and a site number consistent with the published base path. Reject ambiguous or unsupported configuration rather than guessing a tenant.
- `packtoworkeu/scanning/career/career_scanner.py`: pass the request URL into verified TLS construction. For the two named Oracle vanity domains, resolve the public API from their official shell instead of assuming `/hcmRestApi` is proxied on the vanity host. Cache successful detail-base resolution per scan. Retain exact requisition identity checks; support the observed `IRC224927` identity as well as numeric IDs. For vanity listings reconcile site, offset, distinct IDs and total count, and refuse to drop search filters. Zero is verified only from the public zero count; shortfall is incomplete. Generic Oracle behavior remains otherwise unchanged.
- `packtoworkeu/scanning/career/large_boards.py`: explicit reasons for SuccessFactors incomplete stops, including changing totals, missing ranges, repeated postings/ranges, unavailable pages and missing next links. Pagination acceptance, cursor reconciliation, budgets and completeness guards are not loosened.

## Validation actually performed

These are controlled source-level listing and detail-reader checks, not a complete installed-app replay of all 19 portals. No user database was ingested.

| Portal | Listing check | Description check | Limits |
|---|---|---|---|
| Intesa Sanpaolo | 248 distinct posting URLs reconciled across 10 pages, 21.05 s | All 248 returned captured description text; 245 new readable outcomes plus 3 reused from immediately preceding live checks. 230.02 s for the batch. | All detail results had a nonempty location; this does not independently certify every country's normalization. |
| Prada | 284 distinct posting URLs reconciled across 48 pages, 96.47 s | All 284 returned captured description text; 281 new readable outcomes plus 3 reused from immediately preceding live checks. 344.50 s for the batch. | 218/284 detail results supplied a location; listing-card location evidence remains separate. No claim of 284 fully normalized countries. |
| Poste Italiane | Saved seed's dispatch reconciled 2 distinct requisitions, 2.00 s | Both live details captured: IDs 486 and 418, 3,155 and 2,636 characters; location Italia. | Not an installed-app end-to-end replay. |
| Eni | Published board's adapter reconciled 110 distinct requisitions after adding the observed IRC identity. | Batch returned text for 109/110. ID 30001, Speculative application, had no complete external JD and remains unreadable. The initial IRC generic fallback was only 985 characters; the final Oracle-specific retest superseded it with a 4,466-character capture and Wurzburg, Bayern, Germany. ID 34159 separately captured 4,708 characters and Abidjan, Cote d'Ivoire. | Saved `www.eni.com/en-IT/careers.html` returned 403 in this environment. The API-board check is NOT a successful replay from that saved seed, and the saved seed was not changed. Speculative applications can separately be filtered/quarantined by normal acceptance policy. |
| Luxottica | First test retained 60 distinct rows of the then-stated 4,532; stopped incomplete when the live total changed. A subsequent probe observed 4,531 with valid consecutive 30-row pages and no duplicate URLs in the first three pages. | Three sampled live details captured 6,003 / 3,993 / 4,125 characters. | No full 4,531/4,532 census or full description replay claimed. A changing source count must not become a false complete result. |

The batch detail audit used two workers per host and at most two hosts concurrently. Existing scanner request scheduling stayed in place. The three reused descriptions per Prada/Intesa came from this same investigation's preceding live sample checks, not from a user-installed scan. Returned/captured text and scanner outcomes were checked; there was no independent semantic full-body audit of every posting.

Final selected 12-module offline regression suite: **143 passed, 1 skipped**, 10.61 s. Includes Italian adapters, Oracle persistence, TLS, geography, recovery/resume, large-board/cursor and static pagination, stop latency and browser-temp ownership tests. A wider first run exposed an outdated static-pagination network mock lacking the verified `context` argument; the fixture now accepts it and asserts CERT_REQUIRED plus hostname verification. No production TLS behavior was weakened to accommodate tests.

Eight-tab Qt GUI startup/close smoke: OK, isolated data, no browser or live scan. This is not a Windows installer/build certification.

## Other seed-page probes and remaining work

These are transport observations only, not completeness sign-offs:

- Angelini, Barilla, Conad, Esselunga, Ferrero, Lavazza, Lidl, TIM and UniCredit returned HTTP 200. UniCredit redirected to its published `en_GB/jobsuche` page; the saved seed remains untouched.
- Barilla's first HTML page states 51 jobs but displays 10: pagination still needs end-to-end validation.
- Conad publishes a JS loader and a no-items container; a generic HTTP 200 is not proof of a completed empty search. Loader investigation was inconclusive; no new empty-verification rule was added.
- Eurospin's exact saved URL returned HTTP 410 Gone. It was not silently replaced, and no clean-empty claim is made.
- FS returned a 676-byte JavaScript redirect shell that appends a `source` parameter. This is not a verified vacancy census.
- Eni's main website returned 403; its public jobs vanity server and Oracle API were separately readable after the chain repair.
- Poste had a distinct missing GlobalSign intermediate, now covered by the exact-host chain repair.
- IKEA was not included in the continuation seed probes; its earlier issues remain open.
- Previously installed Bulgari/Generali success remains valid; this continuation does not require repeating those two just to validate the new Oracle changes. Generali's one prior quarantine still needs its quarantine artifact for a reason-specific audit.

**Not all remaining 17 portals, and not the whole 19-portal group, are signed off.** No request-budget increases, certificate bypasses, substituted seed URLs or fabricated completeness were used to obtain these results.

## Evidence and tracking

Local controlled-check artifacts and test logs are retained under `.remaining-audit/` in the main project folder and are not bundled into the edited-source patch. The uploaded installed two-portal evidence is retained under `evidence/italian-installed-221752/` after workspace organization. Generated default-app scratch data is retained under the audit directory, not mixed into the deliverable. `CHANGELOG.md` and the workspace `CHANGELOG.txt` are synchronized. Logged execution times explicitly identify the workspace clock; historical run IDs are not edit timestamps.
