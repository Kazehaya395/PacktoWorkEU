# ATS scan - 7 companies

Generated 2026-10-20 04:42 by ats_scanner v2.2.0

**7 / 7** companies matched at least one of the 27 supported ATS vendors.

| Company | ATS | Status | Proof | Career site / job listing | Jobs |
|---|---|---|---|---|---|
| Backbase | Greenhouse | VERIFIED | public API | [https://job-boards.greenhouse.io/workatbackbase](https://job-boards.greenhouse.io/workatbackbase) | 14 |
| Ecosia | Ashby | VERIFIED | public API | [https://jobs.ashbyhq.com/ecosia.org](https://jobs.ashbyhq.com/ecosia.org) |  |
| Ecosia | JOIN | VERIFIED | probe (authoritative) | [https://ecosia.join.com/](https://ecosia.join.com/) |  |
| Sonio | Ashby | VERIFIED | public API | [https://jobs.ashbyhq.com/sonio](https://jobs.ashbyhq.com/sonio) | 5 |
| Sonio | JOIN | VERIFIED | probe (authoritative) | [https://sonio.join.com/](https://sonio.join.com/) |  |
| Mistral AI | Lever | VERIFIED | public API | [https://jobs.lever.co/mistral](https://jobs.lever.co/mistral) |  |
| Mistral AI | Ashby | VERIFIED | public API | [https://jobs.ashbyhq.com/mistral.ai](https://jobs.ashbyhq.com/mistral.ai) | 5 |
| Spendesk | Ashby | VERIFIED | public API | [https://jobs.ashbyhq.com/spendesk](https://jobs.ashbyhq.com/spendesk) |  |
| Spendesk | JOIN | VERIFIED | probe (authoritative) | [https://spendesk.join.com/](https://spendesk.join.com/) |  |
| Virtuagym | Teamtailor | VERIFIED | live jobs in sitemap | [https://virtuagym.teamtailor.com/](https://virtuagym.teamtailor.com/) | 5 |
| Bol.com | Greenhouse | VERIFIED | public API | [https://job-boards.greenhouse.io/bolcom](https://job-boards.greenhouse.io/bolcom) | 30 |

## The rule this report applies

A company is reported against an ATS **only when there is a valid portal URL for it** - a page inside the ATS that belongs to that company and can be opened. Anything short of that is `NOT_FOUND`, with no weaker row kept alongside it.

## What each status means

- **VERIFIED** - proved, and a portal URL is attached. Either an authoritative vendor API answered (Greenhouse, Ashby, Workday, SmartRecruiters job counts), or the vendor served a real tenant page, or the company's own careers page links to that ATS URL.
- **PROBABLE** - *withheld by default.* A tenant that exists but whose page could not be confirmed as a live job listing: an Avature `{slug}.avature.net` login redirect, a Workday career site with 0 open jobs, a custom-domain redirect. Shown only with `--include-probable`.
- **UNVERIFIED** - *withheld by default.* A page exists under this company's name on the vendor's domain, but nothing proves the company uses the product. Shown only with `--include-unverified`. Workable, SmartRecruiters, Pinpoint and BambooHR auto-provision branded pages for any recognisable company name.
- **SELF** - the company *is* the vendor of that product, so its own board is not a customer finding. Reported by default; hide with `--exclude-self-matches`.
- **NOT_FOUND** - terminal. No ATS produced a valid portal URL for this company, so the scan stops here; the `evidence` column says what was exhausted. This does *not* prove the company uses no ATS: custom domains, opaque tenant ids (Zoho Recruit, Eploy, Bullhorn), bot-walled careers pages and JavaScript-only sites are invisible to a plain HTTP scan.
