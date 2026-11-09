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
.\.venv\Scripts\python.exe -m sponsorscout.main
```

### Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
.venv/bin/python -m sponsorscout.main
```

After launch, configure the browser through Tools → Advanced maintenance and
diagnostics → Browser setup. Reuse a detected installation, validate both modes,
and save. The optional app-private full Chromium download requires explicit GUI
confirmation. Installing into a shared SDK cache alone does not configure the app.
Linux may require native Qt/Chromium libraries. An administrator can explicitly
run `.venv/bin/python -m playwright install-deps chromium` on supported systems;
this installs OS libraries, not browser binaries. It is never run by the app.
A desktop session is needed for ordinary interactive use.
No macOS installer or native Windows validation is implied by these commands.

Other supported entry points:

| Entry point | Purpose |
|---|---|
| `python -m sponsorscout.main` | Desktop GUI |
| `python sponsorscout/main.py` | Loose-source GUI bootstrap |
| `sponsorscout` | Installed console entry point for the GUI |
| `python -m sponsorscout.scripts.run_scan` / `sponsorscout-scan` | CLI campaign scan |
| `python -m sponsorscout.main --self-check` | Startup/dependency smoke check |
| `python -m sponsorscout.main --self-check-browser` | Explicit selected-browser check: headless and visible (needs a display) |
| `python -m sponsorscout.main --self-check-gui` | Packaging-only offline GUI smoke (builders set `SPONSORSCOUT_SELF_CHECK_LOG`) |
| `python -m sponsorscout.main --purge-user-data` | Deliberate per-account data cleanup (packaged uninstallers) |

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
| `services/browser_fetcher.py` | HTTP/rendered fetching; browser identity delegated to shared policy |
| `services/browser_policy.py` | Installed Chromium-family discovery, explicit selection, isolated dual-mode validation, consent-gated single full Chromium |
| `services/data_ownership.py` | Stable per-account registry, custom app-file tracking and headless safe purge |
| `ui/browser_setup.py` | Localized setup dialog; probes/downloads run in AsyncLoader, no UI-thread network work |

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
Detail verification is skip-and-continue (`FLOW_REVISION 20261109-skip-walled-origin-v6`):
an unresolved wall — including a looping automatic check whose detail tab the
user closes — blocks only that posting (Unknown, resumable `access_gate`);
same-host postings defer without reopening the wall, other hosts keep reading,
and only Stop/Save or loss of the whole browser context stops the batch. This
never bypasses a wall or uses the personal browser profile.

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

**Browser/storage policy snapshot before the packaging-only corrections:** 2,395 passed / 0 failures / 0 errors /
0 skips across all 104 modules, using four serial subprocess shards with real local
Chromium opted in. The code/build/asset snapshot stayed unchanged throughout that
run. Focused CLI/UI checks, an actual managed download, Linux frozen dual-mode
launch/cleanup and wheel/sdist checks were recorded in the developer's
workspace changelog at the time (not bundled with this source snapshot).
Native Windows and installed DEB/RPM transactions were not executed; fixture
success must not be presented as native certification. Those results apply only to that earlier snapshot. The later packaging-only
corrections have their own validation record below.

The earlier 2,312-pass/102-module snapshot remains historical,
not certification of the new browser/storage policy.
The larger workspace audit history is preserved outside this source bundle
in the developer's `Changelogs/` folder.
No original user uploads/private runtime data, old patch ZIPs, virtual environment,
browser binaries or generated package builds are bundled.

An earlier intermittent GUI heartbeat failure remains unexplained. Passing
Linux fixtures does not guarantee Windows behaviour, every live website or
responsiveness on a physical 8 GB i3/Pentium system.

## 9. Packaging and maintenance

Build definitions are `SponsorScout.spec`, `build_exe.ps1`, `installer.iss`,
`build_deb.sh` and `build_rpm.sh`. A shared `build_hooks/hook-playwright.py` collects Playwright’s Node/CLI and resources,
excluding `.local-browsers` caches even if the builder previously used hermetic installs.
The three CLI builders use this hook rather than unfiltered `--collect-all playwright`;
`SponsorScout.spec` uses the same hook (including sync/async SDK-hook overrides,
so an upstream hook cannot reintroduce the cache). They do **not** ship browser
binaries or a headless shell. Linux postinstall never downloads one.
Default build `--self-check` checks Qt/Python imports, driver files and driver startup
without launching a browser; optional
`SPONSORSCOUT_VALIDATE_BROWSER=1` runs `--self-check-browser` against an already
saved selection in both modes. A successful lean build is not proof of site access
or even of a configured browser on the target machine.

### Packaging-only build corrections

All builders consume the maintained `SponsorScout.spec` with explicit owned
work/output directories. They never generate a replacement spec at the source
root. The spec explicitly adds its source path for `python -I`, filters
`sponsorscout.tests` before collection, collects only the three used Qt modules
and disables UPX/binary stripping. Qt hooks retain required platform/image/TLS
plugins and package metadata; there is no blanket post-check pruning.

Windows does not refuse effective administrator execution: `build_exe.ps1`, the
per-user installer and the uninstaller run on standard and full-access
Administrator accounts alike. SponsorScout.spec resolves datas from the source tree itself (`_collect_local_datas`/`_collect_local_submodules`) because builders invoke it with `python -I` while the package may be uninstalled. It resolves the active venv
bootstrap before a Python-launcher fallback and creates `.build/windows-venv`.
No wildcard pip repair or package uninstall touches an arbitrary runtime/global
environment. Linux keeps its separate existing build venvs. All builders record
`pip freeze` and check dependency consistency; version ranges are not a lockfile,
so these are auditable builds, not promised bit-reproducible artifacts.

`--self-check-gui` dispatches only in explicit packaging-check mode, before normal
startup. It creates all eight actual tabs and closes the window using temporary
HOME/data paths and explicit QSettings INI storage (never native HKCU). The
named `QSettings(org, app)` overload ignores `setDefaultFormat`: the check mode
therefore redirects the Assisted module's named-settings factory explicitly; a
regression guards against new unhandled named-settings consumers. First-run scanning
is disabled in this mode only. It does not open a browser or a portal. This is an
offscreen startup smoke, not a full physical desktop/installer certification.

`build_hooks/verify_payload.py` rejects browser caches/binaries and WebEngine,
checks link containment and can write per-file hashes/regular-file byte totals.
Windows audits and driver/GUI-checks both the final app folder and exact staged
installer payload after icon copying. Linux checks follow final permission
normalization and any filesystem-fallback staging, against the exact install tree.
Linux compressed artifact reporting uses file bytes/SHA256, not allocated `du` blocks.
Both Linux packagers retain the previous valid artifact until a new one is ready. Generated frozen/work folders are outside the DEB install tree, avoiding
accidental duplicate payloads and build files in the package.

Default Windows mode requires Inno and a fresh nonempty setup executable; explicit
`-BundleOnly` reports no installer. The compiler writes into a fresh owned staging directory; only validated fresh
output is published to dist, preserving the previous setup if compilation fails. Compressed installer MiB/SHA256,
uncompressed folder MiB and dependency/spec inventory are separate measurements.
The build report never labels compilation as native installer execution.

Build shutdown checks compare the exact output executable path. Inno uninstall
uses a WMI executable-path check and refuses active/unknown instances; no blanket
`taskkill` or force-close is used. Linux scriptlets compare `/proc/*/exe` to the
installed executable (including its deleted spelling) and ask users to close it
before removal/upgrade. No different same-name copy is terminated. The former
effective-administrator guard (`IsUserAnAdmin` in installer, uninstaller and
`--purge-user-data`) was removed; per-user install, uninstall and data purge on a
full-access Administrator account were native-verified on Windows on 2026-11-09.
`--self-check-gui` diagnostics go to `SPONSORSCOUT_SELF_CHECK_LOG` when the
builders set it, so a failed GUI smoke leaves a log instead of only an exit code.

Current patch evidence and remaining native-testing limits are recorded in
the developer's workspace changelog (not bundled). Earlier full-suite
browser-policy results above are historical for that snapshot, not certification
of these later packaging changes.

### Shared browser policy

All nine production launch sites use `launch_chromium`: two in browser_fetcher,
one ATS fallback, four Career paths, headed Assisted and the optional self-check.
Chrome/Edge use `chrome`/`msedge` channel parameters with the detected executable;
Brave/Opera/Vivaldi/Chromium use explicitly detected executables and `chromium`.
No portal-specific policy, profile attachment, Firefox automation or silent engine
fallback is added. `browser_type.launch` creates ephemeral profiles; no persistent
context or personal cookies/extensions are reused. Validation probes both modes
with a local page and checks CDP command-line evidence for the temporary profile.
It is compatibility screening, not a guarantee for every fork, policy or website.

`managed_playwright` covers every browser-launching driver lifecycle, including
`.start()`/`.stop()` paths. A short lock captures app-private TMP values when
starting Node and immediately restores Python’s environment, even on failure.
Browser child HOME/XDG/APPDATA caches, crash dumps and downloads are directed into
account storage; no personal browser cache is cleaned. Linux uses a short
`/tmp/sponsorscout-<uid>-<state-hash>` directory for profiles/sockets/download ZIPs
to avoid Unix socket-length failures for long home paths. Creation/purge verifies
UID, mode 0700, marker and link boundaries; purge derives this exact directory
instead of trusting an arbitrary recursive-delete manifest entry. The normal
Playwright temporary-profile lifecycle is retained, not a persistent profile.

`browser.json`, `owned_paths.jsonl` and `browser/` live in the standard per-account
SponsorScout folder regardless of DATA_DIR/DB overrides. Selection stores family,
kind, absolute executable path, validation result, versions and a size/mtime/path
fingerprint. A missing/changed executable invalidates selection; wrapper-based
browser updates may still need manual revalidation. The GUI gates new/Custom/Resume
and Assisted browser work on setup without changing saved campaign scope. CLI
run_scan fails with setup guidance if no selection exists.

Only the explicit download button calls `install_managed(consent=True)`. It runs
the **bundled Playwright Node/CLI**, not `sys.executable -m playwright` (which would
relaunch a frozen SponsorScout executable), with a private cache and
`install chromium --no-shell`. One full Chromium serves both modes. Older private
Chromium builds are pruned after a new managed selection is validated and saved;
locked leftovers are logged rather than deleting a browser still in use. Shared
SDK caches, personal installations and PLAYWRIGHT_BROWSERS_PATH are not redirected
or cleaned by the application. The old auto-install environment flag is ignored.
UI operations use a worker, show errors and never silently retry a download.
Ordinary installed Firefox/LibreWolf remain external manual viewing only because
Playwright Firefox automation requires its patched build.

### Ownership and native uninstall

`register_runtime` runs at GUI/CLI startup. The append-only account registry tracks
custom database paths plus sidecars, locale/caches/seeds, known legacy outputs,
seed backup pairs and maintenance pairs. Creation sites register new scan outputs,
seed backups and maintenance backups/audits immediately, including custom artifact
directories. CV/prompt content is inside the database. Qt settings are app-scoped:
Linux .config/XDG paths are tracked; Windows cleanup removes only the app's HKCU
settings subtree. No cleanup interprets raw uninstall-time DATA_DIR/DB overrides.

Custom directories are **not** recursively claimed: tracked files are removed and
containers removed only when empty. Default dedicated SponsorScout profile folders
(including the private browser) are removed recursively, without following links.
Roots, homes, known personal browser locations, shared Playwright caches and
symlink/junction ancestors are refused. A malformed/unsafe registry or permission
failure reports incomplete cleanup and leaves retry information where possible.
Manual exports/copies and older custom locations never registered by this version
cannot be discovered safely: users must review those themselves.

`--purge-user-data` is dispatched before Qt/locale/database startup and needs no
display. Run it only deliberately after backups, as the data-owning account.
Linux DEB prerm purges only on `remove`; RPM preun only on `$1=0`. They require
the installed app to be closed, then invoke the trusted installed binary with `runuser ... env -i` separately for
root, ordinary accounts and lower-UID accounts with SponsorScout state. A user
manifest is never parsed by the package script as root. Cleanup failures stop
removal before the executable disappears. Postrm/postun do not execute broad
root deletions. Upgrades preserve data.

Windows is now **per-user** (`PrivilegesRequired=lowest`), under LOCALAPPDATA/Programs/SponsorScout,
on standard and full-access Administrator accounts alike; no elevation check runs
at install, uninstall or `--purge-user-data` startup.
The installer refuses an existing HKLM/machine-wide installation: backup/removal/
restore migration is explicit, not a destructive implicit upgrade. Updates within
the new per-user format preserve data. Cleanup runs only at `usUninstall`, **after confirmation**, before file removal;
a cleanup exception aborts uninstall. Only Inno-tracked installed files are removed,
not an unconditional recursive `{app}` deletion. Each account uninstalls its own
copy; other Windows users and personal browsers are untouched.

### Local developer tests

The permanent policy/uninstall suite includes mocked installed-family discovery,
both launch modes/channels, profile checks, consent, bundled-driver invocation,
unsafe/corrupt records, custom data/sidecars/artifacts, link boundaries, protected
browser/cache paths, shell-hook upgrade/failure paths and CLI purge. The pytest
fixture isolates the home/settings folder and supplies an explicit test selection;
legacy SDK fixtures also use the full executable, not a separate headless shell.
Lean unit tests use a dummy executable only at mocked launch sites. Opt-in actual
browser tests require a full test Chromium (`python -m playwright install chromium
--no-shell`, a deliberate **developer** download into the SDK cache), or an explicit
`SPONSORSCOUT_TEST_BROWSER_EXECUTABLE` path, plus a display/Xvfb. This test-only setup
does not select a browser in the deployed application or certify native installers.

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

Tools hides terminal JD progress and retains static coverage after completion,
partial results, cancellation or error. Running/paused/stopping states remain
live; checked/classified counts must never be relabelled as readable counts.
No-observation and absent-coverage cases are separate. The next run restores the
live JD bar. The permanent finished-results regression module remains included.

The final review changes no
access, identity, resolver, scope or pacing policy. Some portals' verification
walls and other observed extraction and live-access limitations remain
unresolved and are disclosed in the README's Troubleshooting section. Do not call
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
