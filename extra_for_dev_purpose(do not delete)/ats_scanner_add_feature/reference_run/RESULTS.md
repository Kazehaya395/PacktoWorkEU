# Reference run — 17 companies, names only

```bash
python3 scan.py --csv companies.txt -o results --workers 12 --checkpoint 4
```

One column of input: 17 company names. No website, no careers URL, no slugs.
514.1 s, `mode=all`, 27 ATS vendors.

## Result: 15/17 matched, 18 VERIFIED findings, no false positives

| Company | ATS | Status | Portal URL | Jobs |
|---|---|---|---|---|
| Lightspeed | Ashby | VERIFIED | https://jobs.ashbyhq.com/lightspeed | 11 |
| Scorewarrior | Ashby | VERIFIED | https://jobs.ashbyhq.com/scorewarrior | 12 |
| Babbel | Ashby | VERIFIED | https://jobs.ashbyhq.com/babbel | 5 |
| | JOIN | VERIFIED | https://babbel.join.com/ | |
| Buena | Ashby | VERIFIED | https://jobs.ashbyhq.com/buena | 8 |
| | softgarden | VERIFIED | https://buena.softgarden.io/ | |
| Choco | Ashby | VERIFIED | https://jobs.ashbyhq.com/choco | 1 |
| Forto | Ashby | VERIFIED | https://jobs.ashbyhq.com/forto | 6 |
| HelloFresh | Greenhouse | VERIFIED | https://job-boards.greenhouse.io/hellofresh | 80 |
| | JOIN | VERIFIED | https://hellofresh.join.com/ | |
| KONUX | Greenhouse | VERIFIED | https://job-boards.greenhouse.io/konux | 2 |
| | JOIN | VERIFIED | https://konux.join.com/ | |
| Pitch | Workable | VERIFIED | https://apply.workable.com/pitch-software/ | |
| Bynder | Ashby | VERIFIED | https://jobs.ashbyhq.com/bynder | 8 |
| Appodeal | Greenhouse | VERIFIED | https://job-boards.greenhouse.io/appodeal | 11 |
| Planhat | Ashby | VERIFIED | https://jobs.ashbyhq.com/planhat | 9 |
| Teamtailor | Teamtailor | SELF | https://career.teamtailor.com/ | 15 |
| Notion | Ashby | VERIFIED | https://jobs.ashbyhq.com/notion | 4 |
| Airbyte | Ashby | VERIFIED | https://jobs.ashbyhq.com/airbyte | 9 |
| Rows | — | NOT_FOUND | — | |
| Skyscanner | — | NOT_FOUND | — | |

## Against the known-correct answers

Every portal URL below was checked by hand beforehand. 13 of 14 recovered.

| Company | Known portal | Scanner | |
|---|---|---|---|
| Lightspeed | `jobs.ashbyhq.com/lightspeed` | same | exact |
| Scorewarrior | `jobs.ashbyhq.com/scorewarrior/` | same | exact |
| Babbel | `jobs.ashbyhq.com/babbel` | same | exact |
| Buena | `jobs.ashbyhq.com/buena` | same | exact |
| Choco | `jobs.ashbyhq.com/choco?utm_source` | same | exact |
| KONUX | `job-boards.greenhouse.io/konux` | same | exact |
| Pitch | `apply.workable.com/pitch-software/` | same | exact |
| Bynder | `jobs.ashbyhq.com/bynder` | same | exact |
| Appodeal | `job-boards.greenhouse.io/appodeal` | same | exact |
| Planhat | `jobs.ashbyhq.com/planhat` | same | exact |
| Notion | `jobs.ashbyhq.com/notion` | same | exact |
| Teamtailor | `career.teamtailor.com/jobs` | `career.teamtailor.com/` | same portal, root URL |
| Airbyte | `airbyte.com/company/careers#open-roles` (in-house) | `jobs.ashbyhq.com/airbyte` [9] | found a live ATS board the manual check missed |
| **Rows** | `jobs.ashbyhq.com/rowspace` | **NOT_FOUND** | **missed** |

`Rows` is the one genuine failure. Its slug is `rowspace`, not `rows`, and
`rows.com/careers` is a 404, so neither engine can reach it from a name alone.
Adding the careers URL to the CSV resolves it.

## Two companies previously assumed to run their own portal

Forto and Skyscanner had been set aside on the assumption that they host
careers on their own website. The scan disagrees for one of them:

- **Forto** — `jobs.ashbyhq.com/forto` is live with 6 postings, confirmed
  through Ashby's posting API. That is an Ashby customer, not an in-house page.
- **Skyscanner** — `NOT_FOUND` is correct. `www.skyscanner.com` also 302s this
  client into a PerimeterX captcha (`/sttc/px/captcha-v2/…`), which
  `is_challenge_url()` now detects so the captcha is never mistaken for the
  company website.

HelloFresh was not expected to be in scope either, and comes back on Greenhouse
with 80 postings plus a JOIN board.

## False positives this run removed

An earlier pass over the same 17 names reported 21 findings. Four were wrong,
and each exposed a distinct hole:

| Wrong finding | Reality | Fix |
|---|---|---|
| `lightspeed.bamboohr.com/careers` VERIFIED | BambooHR serves an identical ~101 kB "Current Openings" template for **any** recognisable name; the page names no posting | a class token appearing only once is a feature flag, not a card; confirming a listing now also requires `jobs > 0` or `links > 0` |
| `forto.bamboohr.com/careers` VERIFIED | same template | same |
| `appodeal.bamboohr.com/careers` VERIFIED | 302s to `www.bamboohr.com` — no such account | tenant-bounce demotion |
| `teamtailor.join.com/` VERIFIED | title is `Jobs at InVisions \| JOIN` — a different company | branded-title identity check |

The bounce rule also cleared four `careers.smartrecruiters.com/{forto,appodeal,
lightspeed,teamtailor}` rows, every one of which redirects to
`jobs.smartrecruiters.com`.

Measured across 30 BambooHR tenants, **none** renders a job link in HTML:
20 redirect to `www.bamboohr.com`, 2 to `/settings/account/expired.php`, 8 serve
the empty template. BambooHR cannot be proven from page content at all.
