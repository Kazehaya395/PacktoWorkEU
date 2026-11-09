# ATS scan - 20 companies

Generated 2026-10-20 04:37 by ats_scanner v2.2.0

**18 / 20** companies matched at least one of the 27 supported ATS vendors.

| Company | ATS | Status | Proof | Career site / job listing | Jobs |
|---|---|---|---|---|---|
| InnoGames | Lever | VERIFIED | public API | [https://jobs.eu.lever.co/innogames](https://jobs.eu.lever.co/innogames) | 12 |
| Mentimeter | Greenhouse | VERIFIED | public API | [https://job-boards.greenhouse.io/mentimeter](https://job-boards.greenhouse.io/mentimeter) | 14 |
| EMBL | Workday Recruiting | VERIFIED | public API | [https://embl.wd103.myworkdayjobs.com/embl](https://embl.wd103.myworkdayjobs.com/embl) | 18 |
| Autodesk | Workday Recruiting | VERIFIED | public API | [https://autodesk.wd1.myworkdayjobs.com/Ext](https://autodesk.wd1.myworkdayjobs.com/Ext) | 310 |
| Astroscale | BambooHR | VERIFIED | public API | [https://astroscale.bamboohr.com/careers](https://astroscale.bamboohr.com/careers) | 44 |
| Brain Rocket | Recruitee (Tellent) | VERIFIED | probe (authoritative) | [https://brain.recruitee.com/](https://brain.recruitee.com/) | 7 |
| Spendesk | Ashby | VERIFIED | public API | [https://jobs.ashbyhq.com/spendesk](https://jobs.ashbyhq.com/spendesk) |  |
| Spendesk | JOIN | VERIFIED | probe (authoritative) | [https://spendesk.join.com/](https://spendesk.join.com/) |  |
| Virtuagym | Teamtailor | VERIFIED | live jobs in sitemap | [https://virtuagym.teamtailor.com/](https://virtuagym.teamtailor.com/) | 5 |
| Cargo.one | Ashby | VERIFIED | public API | [https://jobs.ashbyhq.com/cargo-one](https://jobs.ashbyhq.com/cargo-one) |  |
| Mistral AI | Lever | VERIFIED | public API | [https://jobs.lever.co/mistral](https://jobs.lever.co/mistral) |  |
| Mistral AI | Ashby | VERIFIED | probe (authoritative) | [https://jobs.ashbyhq.com/mistral](https://jobs.ashbyhq.com/mistral) |  |
| Ecosia | JOIN | VERIFIED | probe (authoritative) | [https://ecosia.join.com/](https://ecosia.join.com/) |  |
| Rows | Ashby | VERIFIED | public API | [https://jobs.ashbyhq.com/rowspace](https://jobs.ashbyhq.com/rowspace) | 30 |
| Bol.com | Greenhouse | VERIFIED | public API | [https://job-boards.greenhouse.io/bolcom](https://job-boards.greenhouse.io/bolcom) | 30 |
| Sonio | Ashby | VERIFIED | public API | [https://jobs.ashbyhq.com/sonio](https://jobs.ashbyhq.com/sonio) | 5 |
| Sonio | JOIN | VERIFIED | probe (authoritative) | [https://sonio.join.com/](https://sonio.join.com/) |  |
| Scalable Capital | SmartRecruiters | VERIFIED | public API | [https://careers.smartrecruiters.com/ScalableGmbH](https://careers.smartrecruiters.com/ScalableGmbH) | 109 |
| Backbase | - | NOT_FOUND | - | - | - |
| Leonardo | Workday Recruiting | VERIFIED | probe (authoritative) | [https://leonardocompany.wd3.myworkdayjobs.com/LeonardoCareerSite](https://leonardocompany.wd3.myworkdayjobs.com/LeonardoCareerSite) |  |
| Zalando | Workday Recruiting | VERIFIED | public API | [https://zalando.wd3.myworkdayjobs.com/zalandoSiteWD](https://zalando.wd3.myworkdayjobs.com/zalandoSiteWD) | 173 |
| Wolters Kluwer | - | NOT_FOUND | - | - | - |
| Pitch | Workable | VERIFIED | careers page link | [https://apply.workable.com/pitch-software/](https://apply.workable.com/pitch-software/) |  |

## The rule this report applies

A company is reported against an ATS **only when there is a valid portal URL for it** - a page inside the ATS that belongs to that company and can be opened. Anything short of that is `NOT_FOUND`, with no weaker row kept alongside it.

## What each status means

- **VERIFIED** - proved, and a portal URL is attached. Either an authoritative vendor API answered (Greenhouse, Ashby, Workday, SmartRecruiters job counts), or the vendor served a real tenant page, or the company's own careers page links to that ATS URL.
- **PROBABLE** - *withheld by default.* A tenant that exists but whose page could not be confirmed as a live job listing: an Avature `{slug}.avature.net` login redirect, a Workday career site with 0 open jobs, a custom-domain redirect. Shown only with `--include-probable`.
- **UNVERIFIED** - *withheld by default.* A page exists under this company's name on the vendor's domain, but nothing proves the company uses the product. Shown only with `--include-unverified`. Workable, SmartRecruiters, Pinpoint and BambooHR auto-provision branded pages for any recognisable company name.
- **SELF** - the company *is* the vendor of that product, so its own board is not a customer finding. Reported by default; hide with `--exclude-self-matches`.
- **NOT_FOUND** - terminal. No ATS produced a valid portal URL for this company, so the scan stops here; the `evidence` column says what was exhausted. This does *not* prove the company uses no ATS: custom domains, opaque tenant ids (Zoho Recruit, Eploy, Bullhorn), bot-walled careers pages and JavaScript-only sites are invisible to a plain HTTP scan.
