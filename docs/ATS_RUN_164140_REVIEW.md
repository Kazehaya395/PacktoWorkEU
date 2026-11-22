# ATS run 20261122T164140: source repair and validation

## Scope and baseline

Reproduced the uploaded run in an isolated SQLite database by loading all 104
scan-log rows and both saved scope CSVs. It has **104 ATS targets and zero career
targets**. The retry planner selects exactly eight ATS targets and zero career
targets: Bol.com, Jobandtalent, Leonardo, NXP Semiconductors, Kion Group,
IT-tronics (IT Infrastructure), askui GmbH, Personio SE & Co. KG.

The eight are six partial outcomes plus two errors. Portal tasks at 100% means
all portals settled, not all descriptions are complete. Uploaded final states:
87 ok, nine empty, six partial, two errors. 17,001 accepted source observations
reconcile to 14,896 retained rows plus 2,105 merged source/alias observations.
There are three quarantine rows. No historical artifacts were rewritten.

## Repairs

- **Lever**: assemble introduction/opening, body, each named duties/requirements
  list and closing/additional text. Previously descriptionPlain alone discarded
  major portions of Jobandtalent JDs and their sponsorship/skill evidence.
- **Personio**: use the configured Personio host, including .com, rather than
  building a different host from stale metadata. Prefer sectioned XML JDs;
  retain short XML text if an HTML fallback fails. askui's configured host works;
  the old inferred askui.jobs.personio.de returns 404.
- **Bol.com**: most Greenhouse content is literally a placeholder (x). Resolve
  IDs through the official careers.bol.com sitemap, then accept only JobPosting
  JSON-LD matching both the Greenhouse ID and title. No guessed slug or related
  posting is used. Keep unresolved source text short if this fallback fails.
- **Legacy routes included**: Brain Rocket routes to Greenhouse, Virtuagym to
  Teamtailor; seed URLs remain untouched. This consolidated archive includes
  verified_routes.py, unlike a skills-panel-only patch.
- **QA**: accept the literal two-letter title only with a detail-shaped Workday
  requisition URL. Generic QA/HR navigation remains rejected.
- **Non-vacancies**: Career Day events retain quarantine with a specific reason;
  literal Test Job/Test Posting/Test Vacancy titles are also quarantined, without
  rejecting Test Engineer. No event is fabricated into a vacancy.
- **Workday census**: compare observed listing entries with the provider total;
  a premature empty/short page now records listing incompleteness.
- **Repeated retries**: new Workday logs distinguish reconciled listings with
  nonempty but short source descriptions from failed/unattempted reads. The
  former appear in Review unresolved descriptions, not an endless portal retry.
  They remain short in coverage; the 200-character threshold is not weakened.
  Missing proof, errors, incomplete census and failed reads remain retryable.
- **Retry dialog**: phase labels, up to 12 visible entries (all eight here), and
  an explanation that the number is portals, not descriptions. ATS-only saved
  scopes never expand into career scans.
- Prior CSV repair is included: only complete quoted CSV records are tailed;
  scan-log cursor/seen state commits after DB insertion; malformed durations
  retain an explicit log error instead of causing that conversion exception.

## Live adapter checks (not a full 104-portal campaign)

| Portal | Accepted | Readable | Short | Result |
|---|---:|---:|---:|---|
| Bol.com | 21 | 21 | 0 | Official, ID/title-matched fallback |
| Jobandtalent | 31 | 31 | 0 | All Lever JD sections retained |
| Brain Rocket | 8 | 8 | 0 | Greenhouse compatibility route |
| Virtuagym | 16 | 16 | 0 | Teamtailor compatibility route |
| askui | 2 | 2 | 0 | Configured .com XML feed |
| Personio | 1 | 1 | 0 | One additional literal Test Job quarantined |
| Leonardo | 827 | 824 | 3 | Direct Workday replay, 190 seconds |
| NXP | 822 | 814 | 8 | Direct Workday replay, 178 seconds |
| Kion | 967 | 961 | 6 | Direct Workday replay, 242 seconds |

Workday extraction was replayed before the final census/review-routing changes;
those subsequent changes have offline regression coverage. The table counts
adapter-accepted observations, not final cross-source merged DB rows. Personio
was checked again after literal test-posting rejection. No installer was built.

### What the 17 Workday short descriptions contain

- Leonardo: two descriptions contain only primary location/contract headings;
  one says Internal posting and a few headings.
- Kion: source bodies include N/A, punctuation, or repeated job titles.
- NXP: a mix of genuinely short Chinese requirements, short assembler/technician
  duties, and one description starting tbd. These were successful API reads.

Repeated downloads cannot create absent employer prose. The new review action
keeps this visible without treating completed reads as an unfinished crawl.

## Remaining configuration blocker — not concealed

The seed **IT-tronics (IT Infrastructure)** points to
`https://itonics-gmbh.jobs.personio.de/?language=en`, which identifies **ITONICS**,
not IT-Tronics. The identity conflict remains explicit. No URL, employer name,
country scope or enablement flag was silently changed. A fresh scan cannot be
certified error-free with this contradictory target unchanged. Importing that
other employer under the selected name would corrupt the results.

## Tests and boundaries

- 207 focused ATS, scope/retry, CSV, JD, Insights and i18n tests passed; one opt-in test skipped.
- The old test requiring bundled seed edits was updated to assert the effective
  compatibility routes while preserving the manually checked seed URLs.
- Eight-tab Qt startup/close smoke passed offscreen with isolated data.
- Exact uploaded-run retry reproduction: eight ATS, zero career.
- Nine targeted live adapter checks as above; no full 104-target integrated run,
  no new installer and no claim that every upstream source is complete.

## Applying the consolidated source

Close the app. Back up your project folder and per-user data. Extract the
consolidated source into the project root containing pyproject.toml, not into
its inner packtoworkeu package folder. It includes earlier source repairs, the
new Bol helper, routes, tests, and documentation; it is not a tiny panel patch.
Use the same functioning virtual environment for pip and application startup:

```bash
python3 -m pip install -r requirements.txt
python3 -m packtoworkeu.main
```

The existing user database and saved scan-output files are not bundled or
modified. Existing history stays historical. To update saved JD bodies and QA
acceptance, run ATS-only again with the updated source; Rebuild Insights only
re-extracts already saved bodies and does not repair a truncated source JD.
