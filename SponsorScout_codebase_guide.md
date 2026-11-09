# SponsorScout — Codebase Guide

This is the technical companion to the [plain-language README](README.md).
It describes the current working tree, including incremental JD refresh,
Assisted continuation, the simplified EN/IT UI and contextual Insights.
It is not a release announcement or a promise that every portal is accessible.

## 1. Project status and boundaries

- The active Python package is `sponsorscout/`. The unused nested legacy
  `sponsorscout/sponsorscout/` copies were removed during final cleanup; all 15
  files had active counterparts. The cleanup inventory records every removal.
- `pyproject.toml` still declares version **0.2.0**. Local source revisions do
  not automatically update published installers or create a new release.
- The desktop UI uses PySide6; HTTP collection uses Requests/urllib and
  Playwright Chromium where needed. SQLite stores application data locally.
- English and Italian are supported. Browser and job-site content is not
  automatically translated.
- The experimental extension/pairing server, browser bridge and pilot workflow
  are not active features. Assisted collection uses an app-controlled browser;
  it does not copy external browser cookies or solve access challenges.
- Keep discovery, listing completeness, JD readability, benefit evidence,
  availability and persistence outcomes distinct. They are not interchangeable.

## 2. Running from source

Use **Python 3.10 or newer** and a virtual environment. Runtime dependencies
are declared in `pyproject.toml`: Requests, truststore, Playwright and
PySide6-Essentials. The `dev` extra adds pytest and PyInstaller;
`requirements.txt` also includes PyInstaller for the build scripts.

Run these commands from the folder containing `pyproject.toml`.

### Windows PowerShell

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m sponsorscout.main
```

### Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
.venv/bin/python -m playwright install chromium
.venv/bin/python -m sponsorscout.main
```

Linux may require `python3-venv`, native Qt libraries and Chromium system
libraries. Playwright's supported Linux dependency installer is:

```bash
.venv/bin/python -m playwright install --with-deps chromium
```

That step can require administrator privileges and downloads; it is not an
application scan. A desktop session is needed for ordinary interactive use.
No macOS installer or native Windows validation is implied by these commands.

Other supported entry points:

| Entry point | Purpose |
|---|---|
| `python -m sponsorscout.main` | Desktop GUI |
| `python sponsorscout/main.py` | Loose-source GUI bootstrap |
| `sponsorscout` | Installed console entry point for the GUI |
| `python -m sponsorscout.scripts.run_scan` / `sponsorscout-scan` | CLI campaign scan |
| `python -m sponsorscout.main --self-check` | Startup/dependency smoke check |
| `python -m sponsorscout.main --self-check-browser` | Smoke check including browser startup |

An installed console entry point may still point to an older editable checkout.
Use the intended environment and reinstall `-e .` when moving the source folder.

### CLI options

```bash
python -m sponsorscout.scripts.run_scan --company "Company name"
python -m sponsorscout.scripts.run_scan --quick
```

`--company` searches configured company names; it is not the GUI's exact
per-phase target picker. Full detail enrichment is the default. `--quick` is a
developer option that skips the additional detail-enrichment pass, not a second
everyday GUI mode. `--full` is a deprecated alias. `--dedup` explicitly requests
backed-up, audited alias cleanup after scanning; it is not ordinary scan behaviour.

The CLI currently does **not** expose `--resume` or `--refresh-descriptions`.
Those are GUI/API capabilities, not valid CLI flags.

## 3. Runtime data and updates

`paths.py` owns mutable locations:

| Item | Default |
|---|---|
| Windows user data | `%APPDATA%\SponsorScout` (home-directory fallback if APPDATA is absent) |
| Other platforms' user data | `~/.sponsorscout` |
| Database | `sponsorscout.db` inside the user-data folder |
| Editable company lists | `seeds/` inside the user-data folder |
| Scan CSVs, logs and Assisted checkpoints | `scan_output/` inside the user-data folder |

`SPONSORSCOUT_DATA_DIR` overrides the data folder;
`SPONSORSCOUT_DB_PATH` independently overrides the database path. Both are read
during module import. Set them **before** launching/importing the app and prefer
absolute paths. A custom database outside the data folder needs its own backup.

Bundled defaults are `sponsorscout/data/seeds/company_ATS_seed.csv` and
`sponsorscout/data/seeds/company_Career_seed.csv`. The app creates editable user
copies; Data Management edits those copies, not the installed resources.
Resetting to bundled defaults is an explicit user action.

Before updating source or running maintenance, stop the app and back up the
user-data folder plus any custom database. SQLite uses WAL mode: do not assume
copying only a live `.db` file is a complete backup. Preserve applications,
manual country corrections, company lists, tailoring documents and scan history.
Do not delete user data to troubleshoot an import error or apply a source update.

The AI Prompt Kit stores explicitly saved prompt/CV/cover-letter text locally;
its JD editor is session-only. It assembles clipboard text rather than calling
an AI service. Scanning and JD loading still make network requests to job sites;
opening a posting uses the external browser. Local storage does not mean offline operation.

## 4. Eight-tab UI and module map

The tab order is defined in `ui/app.py`:

| Tab | Implementation | Responsibility |
|---|---|---|
| Dashboard | `ui/tabs/dashboard.py` | Saved-job overview and summaries |
| Search | `ui/tabs/search.py` | Filters, paginated results, original links and saving to Applications |
| Applications | `ui/tabs/applications.py` | Local saved/applied/interview/offer/rejected states and notes |
| Tools | `ui/tabs/tools.py` | Campaign controls, progress, history and collapsed advanced operations |
| Data Management | `ui/tabs/data_management.py` | Editable ATS/Career company lists and explicit save/reset actions |
| AI Prompt Kit | `ui/tabs/tailoring.py` | Session JD, saved text documents and clipboard assembly |
| Insights | `ui/tabs/insights.py` | Filtered charts, term refresh/rebuild and cancellation |
| Assisted Ingestion | `ui/tabs/assisted_ingestion.py` | Portal queue, app-browser collection and saved continuation |

Supporting modules, relative to `sponsorscout/`:

| Path | Responsibility |
|---|---|
| `main.py`, `paths.py`, `i18n.py` | Startup/smoke checks, runtime paths and localization |
| `application/scan_coordinator.py` | Worker lifecycle, pause/stop signals and scan start |
| `application/scan_scope.py` | Frozen target rows, saved campaign plans and remaining targets |
| `application/scan_progress.py` | Separate portal/JD work accounting |
| `application/seed_manager.py` | Bundled/user company-list handling |
| `scanning/pipeline.py` | ATS then Career orchestration, live/final ingestion and summaries |
| `scanning/ats/ats_scanner.py` | ATS-provider adapters and captured provider descriptions |
| `scanning/career/career_scanner.py` | Career collection, provider/DOM extraction and detail reads |
| `scanning/detail_state.py` | Shared per-run detail cache, admission, outcomes and budgets |
| `scanning/response_evidence.py`, `scanning/listing_completion.py` | Access/error evidence and listing termination evidence |
| `scanning/jd_support.py` | Shared sponsorship, relocation and Blue Card evidence rules |
| `core/saved_details.py` | Proven-capture validation, refresh policy, observation priority and counters |
| `core/session_details.py`, `core/browser_evidence.py` | Assisted HTTP/rendered primary-detail evidence |
| `core/blocked_portals.py` | Assisted collection, persistence and outcome reporting |
| `core/assisted_workflow.py`, `core/assisted_resume.py` | Continuation planning and exact-scope checkpoints |
| `core/assisted_control.py` | Thread-safe attention, continue, show and save/stop commands |
| `core/job_identity.py`, `core/url_normalizer.py` | Posting identity and safe URL handling |
| `core/persistence.py`, `core/integrity.py` | Upsert, provenance/manual-data preservation and integrity guards |
| `core/support_application.py` | Shared support rules applied to observed text |
| `core/location_country.py`, `core/location_evidence.py`, `core/country_details.py`, `core/place_index.py` | Conservative geographic evidence |
| `core/jd_text.py`, `insights/terms.py`, `insights/queries.py` | Text handling, contextual term extraction and analysis queries |
| `core/verification.py`, `core/verification_service.py` | Availability-check bookkeeping and service |
| `core/maintenance.py`, `core/data_review.py`, `ui/data_review.py` | Explicit preview/backup/audited maintenance |
| `db/database.py`, `db/schema.sql` | SQLite access, initialization and migrations |
| `ui/async_load.py`, `core/db_tasks.py` | Background DB work, cancellation and lifetime management |
| `ui/refresh_options.py` | Shared EN/IT refresh confirmation and result formatting |
| `services/browser_fetcher.py` | Existing browser-fetching service, not an extension bridge |

## 5. Campaign and incremental-refresh contracts

### Discovery and scope

A normal full campaign scans ATS boards and Career targets selected from the
company lists. Custom Scan can select source phases and individual companies.
`application/scan_scope.py` records the original rows, method and refresh flag; Resume uses
that frozen scope rather than re-reading edited company lists into the campaign.
A Career-only campaign must not acquire ATS work on resume.

Pause and Stop are cooperative: in-flight network/browser operations may need
time to return. Campaign Resume continues remaining portal tasks; a settled
partial/error target is not automatically a request to retry every unread JD.
Assisted provides separate saved-listing and unread-description continuation.

Listings are revisited to discover new postings. Known jobs are not a reason to
stop pagination. Partial traversal or failure to see an old job does not, by
itself, prove that the job expired. Progress totals can grow during discovery;
settled work is not synonymous with readable descriptions.

### Saved-description policy

`SavedDetailReader` is a bounded, per-campaign read-only policy/observation
object shared by Career and live/final ingestion. It validates active eligible
rows, readable status, matching SHA-256 and original capture time. Normal reuse
requires a capture younger than **24 hours**. Scan/last-seen times are not JD
capture times and must not renew freshness.

Observe credible titles and real per-posting revision values before upsert can
replace the previous baseline. Comparable changed values reject old reuse;
previously unbound revisions require a read rather than inventing proof of a
change. Supported revision keys include `listing_revision`, `revision`,
`dateModified`, `updatedAt`, `updated_at` and `lastModified`; adapters do not
universally expose them. Do not derive revisions from page clocks or list order.
Read-needed jobs receive priority without counting eligibility as successful reuse.

`remember_capture()` / `capture_proof()` retain bounded hash/time metadata from
actual complete-detail/provider-description reads. Cards, teasers, clipped text
and semantically unproven HTTP bodies must not become readable JD proof merely
because they are long. Authentic unproven card text can still be retained at
ingestion without treating it as a finished description.

With the shared reader attached, a policy miss cannot fall through to the
legacy **14-day JD disk cache**. Saved-derived memory/state aliases are
invalidated on rejection or proof expiry. Provider-identification caching is
separate and remains intact. Direct low-level scanner use without the campaign
reader retains legacy cache behaviour; do not generalize campaign guarantees to it.

### Deliberate refresh and persistence

Tools and Assisted place **Read saved descriptions again (next new run only)**
in collapsed Advanced controls. It defaults off, requires confirmation and is
consumed for the new run. It rejects prior saved captures but can share proven
current-campaign reads; it does not delete saved data or bypass access/budget rules.
A current API response containing the complete JD already counts as fresh work;
no redundant detail HTTP request is required just to increase a counter.

The campaign plan freezes `refresh_descriptions`; legacy plans without it default
to ordinary reuse. Assisted checkpoints retain the refresh flag and campaign
start so resumed work can share proven reads from that campaign.

Persistence can replace a longer old JD with a **proven newer complete JD**.
Older incoming proof cannot overwrite a newer description/support proof merely
because its text is longer. Preserve manual country corrections and authentic
saved text when a refresh fails. Identity, not title or text equality, controls merges.

### Result counters

The completion summary and scan-event evidence contain:

- `jobs_new`, `jobs_existing`: first observed posting identities, not within-run CSV duplicates.
- `jd_reused`, `jd_fresh`: unique identities with successful reuse or proven fresh capture.
- `jd_pending`: observed descriptions not resolved as reused/fresh.
- `jd_blocked`, `jd_inconclusive`: subsets of unresolved descriptions, not additive totals.
- `tracking_complete`: false if database/tracking constraints prevent complete accounting.

Observation tracking is bounded at 20,000 identities. Do not fabricate new-job
counts when the database is unavailable or claim an inaccessible listing's full
inventory. Unattempted work is not a blocked result. A provider that silently
changes content without exposing a changed title/revision cannot be detected
universally within the reuse window; deliberate refresh requests a read sooner.

### Availability is separate

**Job availability** checks saved posting availability, skipping jobs checked
within the last **seven days**. It does not force JD refresh. Inconclusive checks
must not mark an offer expired or convert Unknown support to No.

Request/time/browser limits still apply to both ordinary and deliberate reads.
Career defaults include 1,500 details per company, 20,000 shared details and a
900-second detail-work budget; these are configurable limits, not completion or
runtime guarantees. Consult scanner configuration for provider-specific limits.

## 6. Assisted workflow and access

At idle, Assisted shows company/source/URL, **Collect / continue**, **Stop**,
**Open website**, status and the persistent queue. Collection can plan a saved
continuation. Advanced contains saved batches, separate unread-JD/listing resume,
refresh, resolved filtering and run details. A deliberate new refresh does not
silently become an unrelated old continuation.

Verification controls appear only when an active worker needs attention.
Continue asks the app to recheck the actual content; a checkbox click is not
proof of successful verification. Open website is view-only in the external
browser and does not transfer clearance. Some sites remain inaccessible.

`AssistedWorker` owns browser/database work on its thread. GUI callbacks send
commands/cancellation rather than executing Playwright work. Preserve bounded
shutdown, saved checkpoints and queue reconciliation. A language change, queue
refresh or Advanced toggle must never start collection. A closed browser is not
proof the user deliberately closed it or that verification succeeded.

Saved jobs, readable JDs, complete listings and resolved queue access are
separate outcomes. Maintain exact portal/posting scope during continuation and
never resurrect missing, inactive or protected records merely to satisfy a count.

## 7. Evidence, Insights and localization

Support verdicts are Yes/No/Unknown evidence classifications, not immigration
advice, legal eligibility or guaranteed employer sponsorship. Missing, blocked,
thin or truncated text is not an employer refusal. Job duties involving permits
or relocation are not automatically employee benefits. Public location filters
and employer headquarters do not establish a posting's job country.

Insights separates **technical skills/tools**, **professional/domain skills**
and **soft skills**, excluding benefit language from competency extraction.
Employer industry and job function are distinct. Full chart totals, selected-job
subsets and text/term coverage can differ; do not present them as the same denominator.
Term refresh/rebuild is a separate, cancellable action rather than heavy analysis
inside every scan upsert. Unknown benefits remain Unknown.

New static UI text uses `_()` and explicit EN/IT entries in `i18n.py`.
Retranslation must preserve values, selections, worker state and source combo
item data. Regenerate known status/result templates from their stored data rather
than replacing widgets or guessing ambiguous reverse translations. Do not pretend
to translate user-entered text, employer text or arbitrary native diagnostics.

## 8. Testing and validation evidence

Install test tooling in the chosen virtual environment:

```bash
python -m pip install -e ".[dev]"
python -m pytest sponsorscout/tests -q
```

Target `sponsorscout/tests` for regression discovery. Use temporary
user data/databases, controlled responses and local fixtures. Do not launch a
whole-seed or live all-portal scan to validate documentation or UI changes.

Focused incremental tests:

```bash
python -m pytest -q sponsorscout/tests/test_incremental_refresh_policy.py sponsorscout/tests/test_incremental_refresh_integration.py
```

On a Linux headless test host, Qt can use `QT_QPA_PLATFORM=offscreen`. Browser
cases require installed Chromium and explicitly opt in with
`SPONSORSCOUT_TEST_BROWSER=1`; headed fixtures also need a display/Xvfb.
The optional benchmark-browser tests use `SPONSORSCOUT_BENCH_BROWSER=1`.

```bash
xvfb-run -a env QT_QPA_PLATFORM=offscreen SPONSORSCOUT_TEST_BROWSER=1 SPONSORSCOUT_BENCH_BROWSER=1 python -m pytest sponsorscout/tests -q
```

Do not count an opt-in skip as executed coverage. Dependency/display failures
are environment failures, not proof of a product defect or a successful test.

**Final source validation:** 2,312 passed / 0 failures / 0 errors / 0 skips across 102 modules. This is a fresh run of
all 102 active test modules in four serial subprocess shards, with real local
Chromium cases opted in. Exact outcomes, source/resource hashes, cleanup inventory
and screenshot provenance are bundled under `Changelogs/final_review/`.
See [the report](Changelogs/final_review/report.md) and
[open issues](issues.md). Earlier 2,287-pass and 168-pass counts are historical
results for earlier validation scopes, not substitutes for this fresh run.

The full workspace audit history remains separately preserved in the sibling
`../Changelogs/`; only the final review’s relevant records are in this source
bundle. No original user uploads, private data, old patch ZIPs, legacy copies,
virtual environment, browser binaries or generated package builds are bundled.

An earlier intermittent GUI heartbeat failure remains unexplained. Passing
Linux fixtures does not guarantee Windows behaviour, every live website or
responsiveness on a physical 8 GB i3/Pentium system.

## 9. Packaging and maintenance

Build definitions are `SponsorScout.spec`, `build_exe.ps1`, `installer.iss`,
`build_deb.sh` and `build_rpm.sh`. They target Windows and Linux bundles and
include Chromium under `_playwright`; `paths.py` resolves a bundled browser while
honouring an explicit `PLAYWRIGHT_BROWSERS_PATH` override. Source installs need
their own browser installation. Automatic runtime browser download is opt-in
through `SPONSORSCOUT_AUTO_INSTALL_BROWSERS=1`, not the default.

Use the matching OS/toolchain for a native build. Do not run the Debian build
script with sudo. Build scripts in the repository are not evidence that a fresh
installer has been built, published or tested on the user's machine. This guide
does not promise a currently available release artifact or archive layout.

Maintenance rules:

1. Edit the active package; keep user data outside source/build directories.
2. Preserve company lists, manual corrections, applications and authentic evidence.
3. Use explicit previews, backups and audits for supported cleanup/data-review operations;
   do not make destructive maintenance an automatic scan step.
4. Keep browser/DB work off the GUI thread and honour existing worker/loader lifetimes.
5. Preserve frozen scan/continuation scope, identity and budget/security checks.
6. Add permanent focused regressions for behaviour changes and preserve original failure evidence.
7. Keep user wording clear without stripping the detailed bilingual structure;
   match screenshots and labels to the actual UI and keep evidence boundaries explicit.
8. Do not claim access bypass, universal delta detection, guaranteed runtime or a new release without evidence.


## 10. Final documentation and cleanup review

The README contains a matching current-source Qt screenshot for every tab in
both English and Italian, plus Custom Scan in both languages. Captures use an
isolated fictional four-job/one-application database and idle workers, not an
invented successful scrape. `assets/scanning_tool_finished.png` is separately
labelled as the real supplied Windows partial-run capture (0/163 readable).
The captured visible labels are retained in `ui_capture_manifest.json`.

Tools hides terminal JD progress and retains static coverage after completion,
partial results, cancellation or error. Running/paused/stopping states remain
live; checked/classified counts must never be relabelled as readable counts.
No-observation and absent-coverage cases are separate. The next run restores the
live JD bar. The permanent finished-results regression module remains included.

**Manychat verification remains an unresolved bug.** The final review changes no
access, identity, resolver, scope or pacing policy. Other observed extraction and
live-access limitations remain explicitly disclosed in `issues.md`. Do not call
the code bug-free or infer that a local fixture solved a vendor challenge.

Clean source includes all active modules, tests, seed files, classifier assets,
SQL, icons, packaging scripts and documentation. Generated caches, empty logs,
unused nested copies and old incremental delivery ZIPs are not application
resources. Do not remove user data or permanent tests as “cleanup”. The only
production Python edit during this final review corrects a stale five-tab docstring
in `ui/app.py` to describe the actual eight tabs; it changes no behaviour.

Two regression modules also update four stale EN/IT terminal-result assertions
to the approved static-readable-results contract. Their entire affected shards
are rerun; no production logic or access rule is weakened to satisfy a test.
