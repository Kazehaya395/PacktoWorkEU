# Final complete-source review

## Scope and outcome

Complete source delivery, not another edited-files patch or a native installer.
All eight tabs, their EN/IT text, the detailed bilingual README, technical guide,
entry points, dependencies, packaged resources and packaging manifests were
reviewed. Functional scan/access logic is unchanged by this final review.

**Fresh full regression result: 2,312 passed / 0 failures / 0 errors / 0 skips across 102 modules.**

## Documentation and screenshots

- Retained the detailed bilingual structure; README remains over 1,500 lines.
  It has matching English/Italian screenshots and step-by-step instructions for
  all eight tabs, plus Custom Scan in both languages.
- New images are actual current-source Qt captures with an isolated fictional
  four-job/one-application database. They are not image-generated mockups or
  evidence of a successful live scan. Tools/Assisted are idle and Advanced is
  collapsed. OS decoration, fonts, clipping and enabled controls can differ.
- `scanning_tool_finished.png` is the unmodified supplied Windows partial-run
  capture: 3/3 portal tasks finished, 0/163 descriptions readable, 4 blocked,
  159 unattempted. It is clearly distinguished from the demonstration captures.
- Corrected Dashboard action names, Search positive-only support filters,
  application date/activity-log semantics, explicit document Save, current
  Assisted **Collect / continue**, and the collapsed Advanced locations.
- Explained waiting Insights progress and snapshot/filter/active-job differences
  instead of inventing a diagnosis from asynchronous 193/213-job screenshots.
- Added source-first Windows/Linux venv commands. Corrected backup and privacy
  wording; this source delivery neither requires nor claims a fresh binary.
- Removed obsolete current-stage certification/counts and corrected the actual
  Assisted flow revision. Historical full README/assets remain in the workspace
  audit backup, not as misleading current instructions in this clean bundle.

## Cleanup and code changes

The cleanup inventory lists every permanent removal with its evidence.

- Removed 15 unused nested `sponsorscout/sponsorscout/` legacy copies, each with
  an active counterpart. No runtime, build or test reference used that namespace.
- Removed the empty generated root `ats_scraper_errors.log`.
- Excluded/regenerated disposable bytecode, pytest caches, build output and
  packaging metadata; added `.gitignore` to prevent their accidental inclusion.
- No active test, default seed, classifier/geo asset, SQL resource, icon, build
  script or user data was removed. All active resources retain their pre-review
  SHA-256 values, including both seeds and the skills dictionary.
- The only production Python edit corrects the stale five-tab module/class docstring
  in `sponsorscout/ui/app.py` to eight tabs. No executable behaviour changed.
- Two older regression modules had four EN/IT cases still expecting the superseded
  terminal classified-progress label/bar. Their assertions now require the approved
  static readable counts, hidden JD bar, unresolved arithmetic and retained outcome
  details. The original failure evidence is kept; both entire affected shards are
  rerun, not just these four cases. No product logic was changed to make tests pass.
- `MANIFEST.in` now includes the native build scripts, `.gitignore`, `issues.md`
  and the final review records in source
  distributions. Full-source ZIP contents are checked byte-for-byte separately.

Backups and earlier audit/delivery history remain outside the clean source tree
in the workspace. Original uploads, private databases, live user logs, virtual
environments, browser binaries, obsolete patch ZIPs and temporary diagnostics are
not part of this codebase delivery. Runtime dependencies are installed separately.

## Open findings — not disguised as fixes

**Manychat verification remains an unresolved bug.** No bypass, portal-specific
exception, browser/profile/DNS/security policy change or claim of successful live
verification was added. A partial/no-jobs outcome is not proof of no vacancies.

The review also found an **Applications note-preservation risk**: selecting a
row clears the Notes input; saving only a status with that input empty clears the
previous saved note. Current behaviour is documented, not silently changed under
a documentation/cleanup request. Copy/re-enter a note before saving to retain it.
A future approved fix needs dedicated preservation regressions.

Supplied listing-only/JD failures, metadata in some Doctolib titles, earlier
unre-certified extraction/pagination reports, mixed raw/translated technical
labels and low-end hardware/timing uncertainty remain disclosed in
[../../issues.md](../../issues.md). Passing local tests does not imply bug-free
software, all-site access, full pagination or native Windows validation.

## Verification boundaries

- Fresh regression run covers all 102 active modules in four serial subprocess
  shards, with local Chromium cases explicitly opted in and Xvfb for headed
  fixtures. Exact results and tested-source hashes are in the validation manifest.
- Python source compile/3.10-grammar checks (not a runtime matrix), local SQL initialization, all-tab real Qt captures
  in both languages, startup and browser startup self-checks, docs/link/resource
  checks and wheel/source-distribution content checks are recorded separately.
- No new whole-seed or user full scan, external verification test, native Windows
  executable/installer build or physical 8 GB i3/Pentium test was performed.
- Standalone Windows packaging/PowerShell execution and DEB/RPM installation are
  not certified by Python packaging checks or shell syntax checks.

## Safe source update

1. Close the old app and back up the whole user-data folder, plus any database
   overridden to a separate path. Do not copy only a running SQLite `.db`.
2. Extract this complete source into a fresh folder, rather than layering another
   partial patch over a mixed checkout.
3. Use the intended venv and `pip install -e .` from the extracted project folder;
   this avoids an editable install still pointing to an old checkout.
4. Install Playwright Chromium for that environment if needed; launch with
   `python -m sponsorscout.main`. Keep existing user-data locations.


### Exact full-suite reconciliation

The first fresh four-shard run had **2,308 passes and 4 failures**, all from
obsolete terminal-display expectations in two EN/IT tests. It had no skips or
collection errors. The affected tests still expected classified attempts and a
terminal JD bar, superseded by the approved static-readable-results change.
The assertions were corrected and strengthened for hidden bar, exact localized
readability, unresolved arithmetic and retained detailed outcomes. Both full
24-module and 32-module affected shards were rerun. Accepted fresh coverage is
**506 + 665 + 505 + 636 = 2,312 passes**, zero failures/errors/skips, all 102 modules.
No production or resource file changed between these initial and accepted runs.
Initial failing XML and final accepted XML are retained; this is not presented
as one initially all-green invocation. Four-case focused confirmation was also
run, but is not added again to the full-suite pass count.
