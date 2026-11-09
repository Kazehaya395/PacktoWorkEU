# ATS scan - 16 companies

Generated 2026-10-11 13:51 by ats_scanner v2.1.0

**12 / 16** companies matched at least one of the 27 supported ATS vendors.

| Company | ATS | Status | Proof | Career site / job listing | Jobs |
|---|---|---|---|---|---|
| Stripe | Greenhouse | VERIFIED | public API | [https://job-boards.greenhouse.io/stripe](https://job-boards.greenhouse.io/stripe) | 102 |
| Airbnb | Greenhouse | VERIFIED | public API | [https://job-boards.greenhouse.io/airbnb](https://job-boards.greenhouse.io/airbnb) | 84 |
| Palantir | Lever | VERIFIED | probe (authoritative) | [https://jobs.lever.co/palantir](https://jobs.lever.co/palantir) |  |
| Palantir | SmartRecruiters | VERIFIED | public API | [https://careers.smartrecruiters.com/palantir](https://careers.smartrecruiters.com/palantir) | 10 |
| Ramp | Ashby | VERIFIED | public API | [https://jobs.ashbyhq.com/ramp](https://jobs.ashbyhq.com/ramp) | 17 |
| NVIDIA | Workday Recruiting | VERIFIED | public API | [https://nvidia.wd5.myworkdayjobs.com/nvidiaExternalCareerSite](https://nvidia.wd5.myworkdayjobs.com/nvidiaExternalCareerSite) | 2000 |
| Salesforce | Workday Recruiting | VERIFIED | public API | [https://salesforce.wd12.myworkdayjobs.com/External_Career_Site](https://salesforce.wd12.myworkdayjobs.com/External_Career_Site) | 1535 |
| Deliveroo | Greenhouse | VERIFIED | public API | [https://job-boards.greenhouse.io/deliveroo](https://job-boards.greenhouse.io/deliveroo) | 25 |
| Deliveroo | Ashby | VERIFIED | public API | [https://jobs.ashbyhq.com/deliveroo](https://jobs.ashbyhq.com/deliveroo) | 5 |
| Monzo | Greenhouse | VERIFIED | public API | [https://job-boards.greenhouse.io/monzo](https://job-boards.greenhouse.io/monzo) | 68 |
| Sqills | Recruitee (Tellent) | VERIFIED | probe (authoritative) | [https://sqills.recruitee.com/](https://sqills.recruitee.com/) |  |
| Polestar | Teamtailor | VERIFIED | live jobs in sitemap | [https://polestar.teamtailor.com/](https://polestar.teamtailor.com/) | 22 |
| Sage | Pinpoint | VERIFIED | live jobs in sitemap | [https://sage.pinpointhq.com/](https://sage.pinpointhq.com/) | 5 |
| Specsavers | SAP SuccessFactors | VERIFIED | probe (authoritative) | [https://career5.successfactors.eu/career?career_company=specSF2PROD&lang=en_GB&company=specSF2PROD&site=&loginFlowRequired=true](https://career5.successfactors.eu/career?career_company=specSF2PROD&lang=en_GB&company=specSF2PROD&site=&loginFlowRequired=true) |  |
| Klarna | - | NOT_FOUND | - | - | - |
| Revolut | - | NOT_FOUND | - | - | - |
| Woonstad Rotterdam | - | NOT_FOUND | - | - | - |
| Infosys | - | NOT_FOUND | - | - | - |

## The rule this report applies

A company is reported against an ATS **only when there is a valid portal URL for it** - a page inside the ATS that belongs to that company and can be opened. Anything short of that is `NOT_FOUND`, with no weaker row kept alongside it.

## What each status means

- **VERIFIED** - proved, and a portal URL is attached. Either an authoritative vendor API answered (Greenhouse, Ashby, Workday, SmartRecruiters job counts), or the vendor served a real tenant page, or the company's own careers page links to that ATS URL.
- **PROBABLE** - *withheld by default.* A tenant that exists but whose page could not be confirmed as a live job listing: an Avature `{slug}.avature.net` login redirect, a Workday career site with 0 open jobs, a custom-domain redirect. Shown only with `--include-probable`.
- **UNVERIFIED** - *withheld by default.* A page exists under this company's name on the vendor's domain, but nothing proves the company uses the product. Shown only with `--include-unverified`. Workable, SmartRecruiters, Pinpoint and BambooHR auto-provision branded pages for any recognisable company name.
- **SELF** - the company *is* the vendor of that product, so its own board is not a customer finding. Shown only with `--include-self-matches`.
- **NOT_FOUND** - terminal. No ATS produced a valid portal URL for this company, so the scan stops here; the `evidence` column says what was exhausted. This does *not* prove the company uses no ATS: custom domains, opaque tenant ids (Zoho Recruit, Eploy, Bullhorn), bot-walled careers pages and JavaScript-only sites are invisible to a plain HTTP scan.
