# Open issues and evidence limits

This register describes the reviewed source delivery. Test fixtures are not live
website certification. Package version remains 0.2.0.

## OPEN — Manychat human-verification loop

**Status: unresolved bug. Do not mark resolved.**

The supplied Windows runs still show verification/access failure for Manychat.
A regular automatic scan exhausted its bounded passive observation window;
that is a different flow from the Assisted human-verification session. Earlier
sessions and their closure/Stop cause are not fully established. Repeated page
samples are observations, **not successful verifications**. This review neither
attributes the failure to the user nor claims that Stop/window-close recovery
has been proved against the live Manychat loop.

- A Manychat row under Dashboard's **No Jobs Found (last run)** means that run
  saved no jobs for the recorded outcome, not that the employer has no vacancies.
- A partial/finished queue state does not certify access or solve verification.
- The finished-JD display fix and incremental refresh are separate from access.
- Ordinary **Open website** browsing may be used manually; it does not import
  cookies, share the external browser's session or ingest jobs automatically.
- This delivery makes no portal-specific exception, browser/profile switch,
  security-policy relaxation, stealth addition, CAPTCHA solver or access bypass.

A resolution needs explicit approval for a proposed shared change, reproducible
current evidence, retained safeguards and fresh successful live verification.
Local tests alone cannot close this issue.

**Italiano:** la verifica Manychat resta un **bug irrisolto**. Le letture della
pagina non sono verifiche riuscite; esito parziale/terminato e test locali non
provano accesso al sito. Nessun aggiramento e nessuna colpa attribuita all'utente.

## OPEN — Listing/JD access and extraction-quality limitations

The supplied finished Windows run had 163 observed listing jobs and **zero
readable JDs**: 4 blocked, 159 unattempted. The portal tasks settled, but full
text access was not achieved. An Assisted harvest can save additional listing
rows without proving that every description or every page is readable.

Some supplied Doctolib Search titles contain listing metadata such as contract
labels. **This final review does not claim to repair that extraction issue.**
The rows are not silently rewritten or discarded. Unknown sponsorship, remote
attributes and countries must not be inferred from their presence in Search.

Older reported first-page/pagination cases (the earlier E2b reports), the
Work in Lithuania → cvbankas.lt external-detail recognition report and older
partial McHire/McDonald's descriptions were not re-certified on live sites in
this review. Do not infer that they are fixed from the fresh local test count.
No new whole-seed scan or retrospective user-data repair was performed.

## OPEN — Applications notes preservation risk

Source review found that selecting an application clears the Notes input even
when **Latest notes** contains saved text. Saving a status change writes that
input as the new stored note; leaving it blank therefore clears the prior note.
The current screenshots show the real empty-input state. This is not an
implemented notes-history system, nor a guarantee that status-only saves preserve
notes. Copy/re-enter any existing note before Save to retain it.

This documentation/cleanup review does **not** silently alter application editing
behaviour. The issue remains open for a separately approved correction and
permanent preservation regressions. Other data is not rewritten to hide it.

**Italiano:** la selezione lascia Note vuoto; Salva può cancellare l’ultima nota.
Ricopiala prima di salvare solo lo stato. Rischio documentato, non corretto qui.

## OPEN / not reproduced as a guaranteed fix — Low-end hardware and timing

An earlier intermittent GUI heartbeat failure remains unexplained. Local Linux
Qt/browser tests and an in-memory benchmark do not guarantee native Windows
behaviour or smoothness on a physical old i3/Pentium with 8 GB RAM. Concurrency
and cooperative lifecycle safeguards remain; no universal speed guarantee.

## Known UI limits, not hidden by the documentation

- Some technical/raw labels and diagnostics remain English in Italian mode;
  website/job text is not automatically translated.
- Window size, OS fonts and scaling can clip compact controls. Tables/charts
  and Tools/Insights have scroll areas; not every panel fits in one screenshot.
- Insights may wait while ingestion runs, retaining a previous chart snapshot.
  A waiting `0/0 processed` is not a zero-job database. Scope and active/expired
  filters must be checked before comparing Search, Dashboard and Insights.
- Portal-task completion is not full-JD or complete-pagination success. A
  readable description does not automatically imply sponsorship support.

## Completed separately — Finished-JD results presentation

Completed/partial/cancelled/error campaigns now hide the live JD bar and show
static final description results when coverage is known. Missing coverage is
unavailable, not fabricated zero. Running/paused/stopping states retain their
real progress and a later campaign restores the live bar. Permanent regressions
remain in `sponsorscout/tests/test_finished_jd_results.py`.

This presentation correction **does not resolve Manychat or unread descriptions**.
See [final review](Changelogs/final_review/report.md) for exact validation limits.
