# Italian career run 20261122T191847 — diagnosis and targeted repairs

## Uploaded baseline

The saved scope contains 19 Career targets, not ATS targets. Results: 15 partial,
two unverified-empty, one verified empty, one ok. The per-target errors CSV is
empty. This does not mean all job-detail reads succeeded: the final census is
885 readable + 104 empty + 23 failed + 1,076 unattempted = 2,088 accepted postings.
Those 23 are JD outcomes, not 23 scanner-thread exceptions. The 17-target retry
offer is consistent with the 15 partial and two unverified-empty targets.

The CSV has 107 unknown locations: Bulgari 77, Ikea 9, UniCredit 8, Eni 4,
Generali 4, Esselunga 3, FS 1, Lavazza 1. Blank UI cells were not evidence that
all of these locations had been read and lost. Most saved target scopes are
Global; selecting Italian employers does not restrict their postings to Italy.

## Fixed in source

### Bulgari

The generic detail reader downloaded an empty JavaScript shell, not a JD. The
ordinary Talentlink widget's public listing already supplies sectioned job
bodies, separate job titles and location fields. Added a narrow adapter for the
exact configured board, using the same published guest widget headers and
paging until distinct IDs reconcile with the publisher count. No login,
security-wall bypass, guessed job IDs, seed edit or seed-URL rewrite is used.

Live adapter check: **105/105 readable descriptions and 105 supplied location
fields**, reconciled with published total 105. The uploaded run observed 103
and missed the final two. The Northeast Visual Merchandising Specialist
posting now has a separate New York location and 3,780 characters of JD text.
The API field counts do not certify a resolved country for every city.

The configured board is Bulgari brand-wide; it is not restricted to hotel
vacancies, despite the saved seed name Bulgari Hotels & Resorts. This is now
noted in diagnostics. The user's seed name and URL were not silently changed.

### Generali

The old DOM reader synthesized fragment URLs and sometimes took a category
(e.g. Banking) as the title. Added the normal site's public ATS listing and
count endpoints for its default directional perimeter. Match distinct published
IDs to the public count, use the frontend's published real /home/job/:id/:title
route, retain both public JD sections, and carry explicit locations and hiring
company metadata. No signed-in profile, saved-job or internal-mobility endpoint
is called. The legacy public JSON names descriptionInternalHTML and
internalQualificationHTML are fields served without authentication to public
job pages, not a separate protected resource.

Live adapter check: **215/215 readable descriptions, 215 supplied location
fields**, reconciled against public count 215. This is a current inventory, not
a claim of 146 recovered identical historical jobs. First checked record:
Regulatory Reporting Maternity leave; Milano; Banca Generali S.p.A.

### Geography and display

The supplied Northeast posting had Unknown location but Iceland country because
the country resolver consumed the entire card text with Location Source=none.
Raw context without an identified geography source no longer supplies a country.
An observed New York API location resolves to United States in regression tests.
Search now displays Unknown for empty/null/whitespace locations instead of a
blank cell. This is not fake location recovery and does not mutate old records.

### Transport and Oracle routes

Career urllib requests now use the existing verified OS trust context, matching
the shared certificate policy. HTTPS verification and hostname checks remain
mandatory. Official Eni and Poste vanity /sites/.../job/:id URLs now use their
same-origin Oracle detail endpoint with the existing exact requisition-ID
verification. Errors now preserve a bounded exception explanation rather than
only the exception class. The build diagnostic includes italian-run-191847-v1.

**Eni still fails verified TLS in this environment with an issuer-chain error.**
The OS trust policy did not repair that chain here. No certificate-verification
bypass was added, and Eni descriptions are not claimed fixed by this patch.
Poste's custom Oracle route is covered by fixtures; no successful live Poste JD
capture is claimed.

### Shutdown race observed during tests

An already-closed Tools tab could receive a late ResumeLookup signal and emit
Signal source has been deleted. Discard that specific late-deletion case;
unrelated runtime errors still propagate. This removes a reproduced test warning,
not the job-detail backlog.

## Remaining incomplete work — not hidden

- Luxottica: 804 observed versus publisher total 4,532, including 747 unattempted
  descriptions and HTTP/breaker deferrals; listing cursor remains a continuation
  hint, not evidence that all pages were scanned.
- Intesa: 105 unattempted; shared detail time budget was exhausted.
- Prada: 85 unattempted and a partial listing cursor; Eni: 70 unattempted plus
  the reproduced TLS problem.
- Barilla has 10 observed against stated 51. Ferrero, FS, TIM and UniCredit have
  partial listing/completeness outcomes even where their observed JDs are readable.
- Conad/Eurospin remain unverified-empty; a selector miss is not verified zero jobs.
- Other unknown locations and failed detail reads were not certified repaired.

Do not remove safe budgets, relabel missing reads, fabricate geography or discard
failures just to display 100%. This is a targeted source fix, not a clean replay
of all 19 portals.

## Validation and applying the patch

- 144 focused tests passed; eight browser/environment-gated cases skipped.
- Coverage includes native payload/census/dispatch, TLS verification, geography,
  Search placeholders, retry/scope, budgets/backlog and late-signal handling.
- Eight-tab isolated offscreen GUI startup/close passed.
- Bulgari and Generali were checked through the real provider-dispatch methods.
- No full 19-portal integrated scan or installer build was performed.

Close the app and extract the changed-files patch into the project root
containing pyproject.toml. No new dependency or migration is required. A Custom
Scan of Bulgari and Generali refreshes their saved observations; use Search's
new run filter to inspect the new results. Rebuild Insights does not download
JDs. Historical bogus fragment records are not silently deleted or expired by
this patch, and old scan history retains its original outcomes. Existing
browser-temp recovery and ATS fixes should remain installed.
