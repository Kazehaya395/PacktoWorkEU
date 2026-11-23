# PacktoWorkEU — Codebase Manual

Technical reference for the desktop application: architecture, execution paths,
data ownership, collection, persistence and packaging. Paths below are relative
to the repository root unless stated otherwise.

## Stack and entry points

- Python 3.10+, PySide6 desktop UI, SQLite local storage.
- Requests and urllib for HTTP; Playwright for supported browser collection.
- `regex` for bounded pattern matching; `truststore` for OS certificate trust.
- Runtime dependencies are declared in `pyproject.toml` and `requirements.txt`.

| Command | Purpose |
|---|---|
| `python -m packtoworkeu.main` | Launch the desktop application |
| `packtoworkeu` | Installed desktop entry point |
| `packtoworkeu-scan --help` | Show supported command-line scan options |

## Run from source

Create a virtual environment and install from the project root:

```sh
python -m venv .venv
# Linux: source .venv/bin/activate
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python -m packtoworkeu.main
```

A desktop session is required for interactive use. Configure and save a browser
through Browser setup before browser-based collection. Linux requires the native
Qt and browser libraries appropriate to the distribution.

## Runtime data

`paths.py` owns mutable locations:

| Item | Default |
|---|---|
| Windows user data | `%APPDATA%\PacktoWorkEU` (home-directory fallback if APPDATA is absent) |
| Other platforms' user data | `~/.packtoworkeu` |
| Database | `packtoworkeu.db` inside the user-data folder |
| Editable company lists | `seeds/` inside the user-data folder |
| Scan CSVs, logs and Assisted checkpoints | `scan_output/` inside the user-data folder |

`PACKTOWORKEU_DATA_DIR` overrides the data folder;
`PACKTOWORKEU_DB_PATH` independently overrides the database path. Both are read
during module import. Set them **before** launching/importing the app and prefer
absolute paths. A custom database outside the data folder needs its own backup.

Bundled defaults are `packtoworkeu/data/seeds/company_ATS_seed.csv` and
`packtoworkeu/data/seeds/company_Career_seed.csv`. The app creates editable user
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

## UI and module map

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

Supporting modules, relative to `packtoworkeu/`:

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
| `services/browser_fetcher.py` | HTTP/rendered fetching; browser identity delegated to shared policy |
| `services/browser_policy.py` | Installed Chromium-family discovery, explicit selection, isolated dual-mode validation, consent-gated single full Chromium |
| `services/data_ownership.py` | Stable per-account registry, custom app-file tracking and headless safe purge |
| `ui/browser_setup.py` | Localized setup dialog; probes/downloads run in AsyncLoader, no UI-thread network work |


## Collection architecture

`application/scan_coordinator.py` manages worker lifetimes and cooperative
pause/stop requests. `application/scan_scope.py` records selected source rows,
method and refresh options. `scanning/pipeline.py` orchestrates ATS and Career
phases and imports exported results into SQLite.

ATS adapters live under `packtoworkeu/scanning/ats/`. Career extraction lives
under `packtoworkeu/scanning/career/`, including provider detection, supported
native boards, static HTML and browser-based extraction. Preserve the configured
URL, provider, board, country and scope when dispatching a target.

Listings are revisited to discover postings. A known posting is not a reason to
stop pagination. Completion requires supported publisher totals or a reliable
terminal state; repeated page links and interrupted traversal are incomplete.
A missing posting in a partial listing does not establish expiration.

### Descriptions and persistence

`scanning/career/primary_capture.py` extracts identity-bound primary descriptions.
JobPosting metadata and supported semantic containers must belong to the requested
posting. Ambiguous identity, challenge content and incomplete bodies cannot supply
full-capture proof. Eligible full captures have a 32,000-character bound.

`core/saved_details.py` owns reuse and capture-proof policy. Normal reuse requires
an eligible saved description, matching integrity metadata and capture age under
24 hours. Last-seen time does not renew capture freshness. Observed title or
revision changes may require a new read.

`scanning/detail_state.py` holds bounded run-local detail state. Listing cards,
readable text, proven full descriptions and successfully persisted descriptions
are distinct states. `core/job_identity.py` and `core/url_normalizer.py` support
posting matching; `core/persistence.py` preserves provenance and protected manual
data during upsert. `scanning/pipeline.py` performs ingestion.

### Status model

Portal statuses include `ok`, `partial`, `empty`, `unverified_empty`, `error`
and `cancelled`. `empty` requires positive empty-result evidence; no extracted
rows alone is insufficient. Description statuses include `readable`, `thin`,
`empty`, `failed`, `blocked` and `unattempted`. Availability, listing completeness,
description readability and sponsorship evidence must remain separate.

### Resume, retry and review

Resume continues unfinished tasks from the saved campaign scope. Retry creates
a new scan of eligible incomplete routes. Review displays supported unresolved
description cases without launching collection. User edits to seed files must
not silently change the scope of a resumed campaign.

## Browser and Assisted Ingestion

`services/browser_policy.py` discovers Chromium-family installations and stores
an explicit selection. Validation uses isolated temporary profiles. Personal
profiles, cookies and extensions are not imported. An app-managed Chromium
download requires user consent; browser binaries are not bundled.

`core/blocked_portals.py` coordinates Assisted collection.
`core/assisted_workflow.py` and `core/assisted_resume.py` own continuation plans;
`core/assisted_control.py` carries attention and cancellation commands.
Browser/database work belongs to the worker thread, not GUI callbacks.

Human-verification steps are user-controlled. An unresolved access gate leaves
the posting resumable and its unread description Unknown. Other eligible hosts
can continue. Opening a site externally does not transfer browser clearance.
Maintain exact portal and posting scope in saved checkpoints.

## Evidence, geography and Insights

Support classifications are Yes/No/Unknown observations, not legal eligibility
or a guarantee of sponsorship. Missing or blocked text is not a negative verdict.
Country inference uses posting evidence; company headquarters and search filters
do not by themselves establish the posting country.

`ui/role_skills.py` provides the role-scoped skills panel; `insights/role_skills.py`
matches titles and analyzes descriptions. Matching supports role terms, wildcards
and bounded regular expressions. Counts are per eligible posting; percentages
use analyzed descriptions. Verified text is the default, with optional eligible
legacy inclusion. Identical description digests can share extraction work without
merging posting counts.

Other Insights extraction and queries live in `insights/terms.py` and
`insights/queries.py`. Term refresh/rebuild is cancellable and separate from scan
upsert. Keep chart populations, selected subsets and text coverage distinct.

## Localization and UI concurrency

Static UI strings use `_()` and explicit English/Italian entries in `i18n.py`.
Retranslation preserves values, selections and worker state. Employer descriptions
and user-entered text are not automatically translated.

Use `ui/async_load.py` and `core/db_tasks.py` for background database work.
Cancellation and widget lifetime checks belong at asynchronous boundaries.
Do not perform network collection or heavyweight analysis on the GUI thread.

## Source catalog

Bundled source CSVs live in `packtoworkeu/data/seeds/`. Their fields include
`name`, `careers_url`, `provider`, `board_slug`, `source_type`, `target_country`,
`scope_policy`, `industry`, `sponsorship_history`, `english_friendly`,
`remote_score` and `notes`.

Preserve URL query strings and country/scope distinctions. Different aliases
are not grounds to merge distinct configurations. User seed copies are editable
through Data Management; bundled files are application resources.

## Packaging

- `PacktoWorkEU.spec`: shared PyInstaller application specification.
- `build_exe.ps1` and `installer.iss`: Windows build and per-user installer.
- `build_deb.sh`: Debian-family packaging.
- `build_rpm.sh`: RPM-family packaging.
- `build_hooks/`: collection hooks for packaged dependencies.
- `MANIFEST.in`: source-distribution resources.

Install build tooling with `python -m pip install -e ".[dev]"`. Build on the
target operating system using the corresponding script. Package application
resources, icons, SQL and dependency metadata. Do not include mutable user data,
scan output, personal browser profiles or browser binaries in the distribution.

`services/data_ownership.py` tracks application-owned files for explicit removal.
Uninstall and cleanup operations must respect custom paths and unrelated files.
Back up user data before maintenance or replacement of an installed application.
