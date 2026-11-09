# ATS scan - 1 companies

Generated 2026-10-20 04:52 by ats_scanner v2.2.0

**1 / 1** companies matched at least one of the 27 supported ATS vendors.

| Company | ATS | Status | Proof | Career site / job listing | Jobs |
|---|---|---|---|---|---|
| Sonio | Ashby | VERIFIED | public API | [https://jobs.ashbyhq.com/sonio](https://jobs.ashbyhq.com/sonio) | 5 |
| Sonio | Personio | VERIFIED | probe (authoritative) | [https://sonio-ag.jobs.personio.de/](https://sonio-ag.jobs.personio.de/) | 8 |
| Sonio | JOIN | VERIFIED | probe (authoritative) | [https://sonio.join.com/](https://sonio.join.com/) |  |

## The rule this report applies

A company is reported against an ATS **only when there is a valid portal URL for it** - a page inside the ATS that belongs to that company and can be opened. Anything short of that is `NOT_FOUND`, with no weaker row kept alongside it.

## What each status means

- **VERIFIED** - proved, and a portal URL is attached. Either an authoritative vendor API answered (Greenhouse, Ashby, Workday, SmartRecruiters job counts), or the vendor served a real tenant page, or the company's own careers page links to that ATS URL.
- **PROBABLE** - *withheld by default.* A tenant that exists but whose page could not be confirmed as a live job listing: an Avature `{slug}.avature.net` login redirect, a Workday career site with 0 open jobs, a custom-domain redirect. Shown only with `--include-probable`.
- **UNVERIFIED** - *withheld by default.* A page exists under this company's name on the vendor's domain, but nothing proves the company uses the product. Shown only with `--include-unverified`. Workable, SmartRecruiters, Pinpoint and BambooHR auto-provision branded pages for any recognisable company name.
- **SELF** - the company *is* the vendor of that product, so its own board is not a customer finding. Reported by default; hide with `--exclude-self-matches`.
- **NOT_FOUND** - terminal. No ATS produced a valid portal URL for this company, so the scan stops here; the `evidence` column says what was exhausted. This does *not* prove the company uses no ATS: custom domains, opaque tenant ids (Zoho Recruit, Eploy, Bullhorn), bot-walled careers pages and JavaScript-only sites are invisible to a plain HTTP scan.
