# ATS scan - 5 companies

Generated 2026-10-16 08:50 by ats_scanner v2.1.0

**4 / 5** companies matched at least one of the 27 supported ATS vendors.

| Company | ATS | Status | Proof | Career site / job listing | Jobs |
|---|---|---|---|---|---|
| Shopify | - | NOT_FOUND | - | - | - |
| Atlassian | iCIMS | VERIFIED | probe (authoritative) | [https://careers-atlassian.icims.com/jobs](https://careers-atlassian.icims.com/jobs) |  |
| Doctolib | Greenhouse | VERIFIED | public API | [https://job-boards.greenhouse.io/doctolib](https://job-boards.greenhouse.io/doctolib) | 79 |
| Doctolib | Ashby | VERIFIED | public API | [https://jobs.ashbyhq.com/doctolib](https://jobs.ashbyhq.com/doctolib) | 5 |
| Zendesk | Workday Recruiting | VERIFIED | public API | [https://zendesk.wd1.myworkdayjobs.com/zendesk](https://zendesk.wd1.myworkdayjobs.com/zendesk) | 137 |
| Qonto | Lever | VERIFIED | probe (authoritative) | [https://jobs.lever.co/qonto](https://jobs.lever.co/qonto) |  |
| Qonto | Ashby | VERIFIED | public API | [https://jobs.ashbyhq.com/qonto](https://jobs.ashbyhq.com/qonto) | 4 |

## The rule this report applies

A company is reported against an ATS **only when there is a valid portal URL for it** - a page inside the ATS that belongs to that company and can be opened. Anything short of that is `NOT_FOUND`, with no weaker row kept alongside it.

## What each status means

- **VERIFIED** - proved, and a portal URL is attached. Either an authoritative vendor API answered (Greenhouse, Ashby, Workday, SmartRecruiters job counts), or the vendor served a real tenant page, or the company's own careers page links to that ATS URL.
- **PROBABLE** - *withheld by default.* A tenant that exists but whose page could not be confirmed as a live job listing: an Avature `{slug}.avature.net` login redirect, a Workday career site with 0 open jobs, a custom-domain redirect. Shown only with `--include-probable`.
- **UNVERIFIED** - *withheld by default.* A page exists under this company's name on the vendor's domain, but nothing proves the company uses the product. Shown only with `--include-unverified`. Workable, SmartRecruiters, Pinpoint and BambooHR auto-provision branded pages for any recognisable company name.
- **SELF** - the company *is* the vendor of that product, so its own board is not a customer finding. Shown only with `--include-self-matches`.
- **NOT_FOUND** - terminal. No ATS produced a valid portal URL for this company, so the scan stops here; the `evidence` column says what was exhausted. This does *not* prove the company uses no ATS: custom domains, opaque tenant ids (Zoho Recruit, Eploy, Bullhorn), bot-walled careers pages and JavaScript-only sites are invisible to a plain HTTP scan.
