# Validation against the seed file (20 companies, names only)

Run: `python3 scan.py --csv companies_20.txt -o seed20_results --workers 12`
Input was **company names only** — no website, no careers URL. 606 s for 20
companies, plus three targeted re-runs after the two bugs below were fixed.

Every company is from `company_ATS_seed.csv`, so each row has a known-correct
answer to compare against. "Exact" means the URL we reported is the URL the seed
file says the company's portal is.

| # | Company | Reported | Portal URL | Jobs | Seed expects | Verdict |
|---|---|---|---|---|---|---|
| 1 | Cargo.one | Ashby | `jobs.ashbyhq.com/cargo-one` | – | ashby `cargo-one` | exact |
| 2 | Ecosia | Ashby, JOIN | `jobs.ashbyhq.com/ecosia.org` | – | ashby `ecosia.org` | exact |
| 3 | EMBL | Workday | `embl.wd103.myworkdayjobs.com/embl` | 18 | `embl.wd103…/EMBL` | exact |
| 4 | InnoGames | Lever | `jobs.eu.lever.co/innogames` | 12 | `jobs.eu.lever.co/innogames` | exact |
| 5 | Mistral AI | Ashby, Lever | `jobs.ashbyhq.com/mistral.ai` | 5 | ashby `mistral.ai` | exact |
| 6 | Brain Rocket | Recruitee | `brain.recruitee.com/` | 7 | bamboohr `brainrocket` | **different vendor** |
| 7 | Virtuagym | Teamtailor | `virtuagym.teamtailor.com/` | 5 | recruitee `jobs.virtuagym.com` | **different vendor** |
| 8 | Spendesk | Teamtailor, Ashby, JOIN | `career.spendesk.com/jobs` | 27 | teamtailor `career.spendesk.com` | exact |
| 9 | Mentimeter | Greenhouse | `job-boards.greenhouse.io/mentimeter` | 14 | `job-boards.eu.greenhouse.io/…` | same board, global host |
| 10 | Rows | Ashby | `jobs.ashbyhq.com/rowspace` | 30 | ashby `rowspace` | exact |
| 11 | Autodesk | Workday | `autodesk.wd1.myworkdayjobs.com/Ext` | 310 | `autodesk.wd1…/Ext` | exact |
| 12 | Astroscale | BambooHR | `astroscale.bamboohr.com/careers` | 44 | bamboohr `astroscale` | exact |
| 13 | Sonio | Personio, Ashby, JOIN | `sonio-ag.jobs.personio.de/` | 8 | personio `sonio-ag` | exact |
| 14 | Scalable Capital | SmartRecruiters | `careers.smartrecruiters.com/ScalableGmbH` | 109 | `…/ScalableGmbH` | exact |
| 15 | Bol.com | Greenhouse | `job-boards.greenhouse.io/bolcom` | 30 | greenhouse `bolcomen` | same board, locale variant |
| 16 | Backbase | Greenhouse | `job-boards.greenhouse.io/workatbackbase` | 14 | greenhouse `workatbackbase` | exact |
| 17 | Pitch | Workable | `apply.workable.com/pitch-software/` | – | workable `pitch-software` | exact |
| 18 | Leonardo | Workday | `leonardocompany.wd3.myworkdayjobs.com/LeonardoCareerSite` | – | same | exact |
| 19 | Zalando | Workday | `zalando.wd3.myworkdayjobs.com/zalandoSiteWD` | 173 | same | exact |
| 20 | Wolters Kluwer | NOT_FOUND | – | – | workday `wk` | **documented limit** |

**19 of 20 companies returned a live, verified portal URL. 16 match the seed
row exactly.** The three that do not:

- **Brain Rocket** — the seed's BambooHR tenant publishes **0** postings
  (`brainrocket.bamboohr.com/careers/list` returns a 37-byte
  `{"meta":{"totalCount":0}}`), which the reporting rule correctly refuses to
  call a finding. Its Recruitee board has 7 live jobs.
- **Virtuagym** — the seed says Recruitee, but `jobs.virtuagym.com` carries
  Teamtailor markers and `virtuagym.teamtailor.com` serves 5 live postings. The
  seed is a dated snapshot; the network is now.
- **Wolters Kluwer** — its tenant is `wk`, two-letter initials. Those are
  excluded on purpose: `wr` once resolved to World Relief when the company
  searched was Woonstad Rotterdam. This is a deliberate false-positive trade,
  asserted unreachable by the `slugs` self-test so it cannot silently change.

## Two real bugs this validation found

Neither was visible in the offline slug benchmark, which measures whether a slug
is *generated*. Both were about whether it is *reached* and whether the URL
reported is the company's.

**1. The probe budget counted requests, not slugs.** Personio probes
`jobs.personio.de` and `.com` for every slug, so a budget of 40 requests reached
only slug 20 — and Sonio's real tenant `sonio-ag` is slug 24. Generated
correctly, ranked correctly, never fetched. Fix: the budget is a slug budget,
with requests capped at twice it. Related: dotted slugs are now dropped on
vendors that put the tenant in the *hostname* (`sonio.org.jobs.personio.de` can
only be NXDOMAIN), which moved `sonio-ag` to position 15. Locked by the
`probe-budget` self-test suite (13 cases).

**2. A "Powered by" link became the answer.** `career.spendesk.com/jobs` is a
Teamtailor board on Spendesk's own domain and contains no Teamtailor tenant URL
at all — only a footer link to `www.teamtailor.com`. That link was recorded as
the portal, sending the user to the vendor's homepage. Fix: when the only proof
is the vendor's bare marketing root, the portal is the page carrying it, and only
if that page is a real job listing. Locked by the `custom-domain portals` suite
(6 cases), including a negative case for a blog post that merely mentions a
vendor.

## Cost

| | requests | wall time |
|---|---|---|
| one company that matches nothing (Wolters Kluwer) | 571 | 165 s |
| one company that matches on slug 1 (Cargo.one) | ~411 | ~125 s |
| 20 companies, `--workers 12` | — | 606 s |

`probe_vendor` returns at the first hit, so a matched company is cheaper than
these numbers. `--probe-budget` trades recall for speed in either direction.

## Files here

- `companies_20.txt` — the input list (names only)
- `seed20_results.csv` / `.json` / `seed20_hits.md` — the main run
- `companies_recheck_7.txt`, `seed7_results.*` — re-run of the budget-limited cases
- `spendesk_custom_domain.csv` — Spendesk after the "Powered by" fix
- `sonio_results.*` — Sonio after the slug-budget fix
