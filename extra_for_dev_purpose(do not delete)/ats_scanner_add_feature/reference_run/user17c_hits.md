# ATS scan - 17 companies

Generated 2026-10-18 17:46 by ats_scanner v2.1.0

**15 / 17** companies matched at least one of the 27 supported ATS vendors.

| Company | ATS | Status | Proof | Career site / job listing | Jobs |
|---|---|---|---|---|---|
| Lightspeed | Ashby | VERIFIED | public API | [https://jobs.ashbyhq.com/lightspeed](https://jobs.ashbyhq.com/lightspeed) | 11 |
| Scorewarrior | Ashby | VERIFIED | public API | [https://jobs.ashbyhq.com/scorewarrior](https://jobs.ashbyhq.com/scorewarrior) | 12 |
| Babbel | Ashby | VERIFIED | public API | [https://jobs.ashbyhq.com/babbel](https://jobs.ashbyhq.com/babbel) | 5 |
| Babbel | JOIN | VERIFIED | probe (authoritative) | [https://babbel.join.com/](https://babbel.join.com/) |  |
| Buena | Ashby | VERIFIED | public API | [https://jobs.ashbyhq.com/buena](https://jobs.ashbyhq.com/buena) | 8 |
| Buena | softgarden | VERIFIED | probe (authoritative) | [https://buena.softgarden.io/](https://buena.softgarden.io/) |  |
| Choco | Ashby | VERIFIED | public API | [https://jobs.ashbyhq.com/choco](https://jobs.ashbyhq.com/choco) | 1 |
| Forto | Ashby | VERIFIED | public API | [https://jobs.ashbyhq.com/forto](https://jobs.ashbyhq.com/forto) | 6 |
| HelloFresh | Greenhouse | VERIFIED | public API | [https://job-boards.greenhouse.io/hellofresh](https://job-boards.greenhouse.io/hellofresh) | 80 |
| HelloFresh | JOIN | VERIFIED | probe (authoritative) | [https://hellofresh.join.com/](https://hellofresh.join.com/) |  |
| KONUX | Greenhouse | VERIFIED | public API | [https://job-boards.greenhouse.io/konux](https://job-boards.greenhouse.io/konux) | 2 |
| KONUX | JOIN | VERIFIED | probe (authoritative) | [https://konux.join.com/](https://konux.join.com/) |  |
| Pitch | Workable | VERIFIED | careers page link | [https://apply.workable.com/pitch-software/](https://apply.workable.com/pitch-software/) |  |
| Bynder | Ashby | VERIFIED | public API | [https://jobs.ashbyhq.com/bynder](https://jobs.ashbyhq.com/bynder) | 8 |
| Rows | - | NOT_FOUND | - | - | - |
| Appodeal | Greenhouse | VERIFIED | public API | [https://job-boards.greenhouse.io/appodeal](https://job-boards.greenhouse.io/appodeal) | 11 |
| Planhat | Ashby | VERIFIED | public API | [https://jobs.ashbyhq.com/planhat](https://jobs.ashbyhq.com/planhat) | 9 |
| Teamtailor | Teamtailor | SELF | probe (authoritative) | [https://career.teamtailor.com/](https://career.teamtailor.com/) | 15 |
| Skyscanner | - | NOT_FOUND | - | - | - |
| Notion | Ashby | VERIFIED | public API | [https://jobs.ashbyhq.com/notion](https://jobs.ashbyhq.com/notion) | 4 |
| Airbyte | Ashby | VERIFIED | public API | [https://jobs.ashbyhq.com/airbyte](https://jobs.ashbyhq.com/airbyte) | 9 |

## The rule this report applies

A company is reported against an ATS **only when there is a valid portal URL for it** - a page inside the ATS that belongs to that company and can be opened. Anything short of that is `NOT_FOUND`, with no weaker row kept alongside it.

## What each status means

- **VERIFIED** - proved, and a portal URL is attached. Either an authoritative vendor API answered (Greenhouse, Ashby, Workday, SmartRecruiters job counts), or the vendor served a real tenant page, or the company's own careers page links to that ATS URL.
- **PROBABLE** - *withheld by default.* A tenant that exists but whose page could not be confirmed as a live job listing: an Avature `{slug}.avature.net` login redirect, a Workday career site with 0 open jobs, a custom-domain redirect. Shown only with `--include-probable`.
- **UNVERIFIED** - *withheld by default.* A page exists under this company's name on the vendor's domain, but nothing proves the company uses the product. Shown only with `--include-unverified`. Workable, SmartRecruiters, Pinpoint and BambooHR auto-provision branded pages for any recognisable company name.
- **SELF** - the company *is* the vendor of that product, so its own board is not a customer finding. Reported by default; hide with `--exclude-self-matches`.
- **NOT_FOUND** - terminal. No ATS produced a valid portal URL for this company, so the scan stops here; the `evidence` column says what was exhausted. This does *not* prove the company uses no ATS: custom domains, opaque tenant ids (Zoho Recruit, Eploy, Bullhorn), bot-walled careers pages and JavaScript-only sites are invisible to a plain HTTP scan.
