# ats_scanner

Given a list of company names, find out **whether each company officially uses
one of 27 ATS (applicant tracking system) portals**, and return the **direct
URL** of that company's career site / job listing on the portal.

```
$ python3 scan.py "Stripe" "Palantir" "Ramp"

COMPANY    ATS               STATUS      URL  [open jobs]
------------------------------------------------------------------------------------
Stripe     Greenhouse        VERIFIED    https://job-boards.greenhouse.io/stripe   [102]
Palantir   Lever             VERIFIED    https://jobs.lever.co/palantir
Ramp       Ashby             VERIFIED    https://jobs.ashbyhq.com/ramp             [17]
```

No API keys, no browser, no third-party service. Plain HTTPS + DNS.

**v2.2.0** adds the last step a human would take: every reported URL is opened
and checked for actual job postings (`page_check`), a tenant whose page is
branded for a *different* company is rejected, a URL that bounces to the
vendor's own marketing site is no longer treated as a portal, and a vendor
recruiting on its own product is reported as `SELF` instead of being hidden.
See `reference_run/RESULTS.md` for a 17-company run checked line by line against
known-correct answers.

---

## Quick start

```bash
pip install httpx                 # the only dependency

python3 scan.py --list-vendors                          # what is supported, and how
python3 scan.py --selftest                              # prove the rules still work
python3 scan.py "Airbnb" "Stripe" "Bosch"               # scan a few names
python3 scan.py --csv companies.csv -o results          # scan a list from a file
```

Outputs are written next to the `-o` prefix:

| File | Contents |
|---|---|
| `results_results.csv` | one row per (company, ATS) finding — the thing you actually want |

The CSV gives you the portal URL in two flavours plus the evidence behind it:
`career_site_url` (the company's home page inside the ATS), `job_listing_url`
(the full job-listing page where the vendor distinguishes them),
`company_careers_page` (the company's own page the fingerprint was read from),
`job_count`, `evidence`, `proof_tier` and `website_guessed`.
| `results_results.json` | full detail, including every probe tried and its raw evidence |
| `results_hits.md` | human-readable summary table |

### Input file — a list of company names is all you need

One company per line:

```
Stripe
Airbnb
Deliveroo
Woonstad Rotterdam
```

```bash
python3 scan.py --csv companies.txt -o results
```

`.csv` / `.tsv` with headers also works, and `website` / `careers_url` are
**optional** extra columns:

```csv
company,website,careers_url
Stripe,https://stripe.com,https://stripe.com/jobs/search
Sqills,https://sqills.com,https://sqills.com/careers/
Südzucker,https://www.suedzucker.de,https://www.suedzucker.de/en/career/
```

A single-column CSV whose header is just `company` is fine too.

**What happens when you supply only names.** The scanner derives the website
from the company name (`revolut` → `revolut.com`, `deliveroo` →
`deliveroo.co.uk`, `woonstad rotterdam` → `woonstadrotterdam.nl`), confirming
each candidate by DNS and then by an actual fetch, so a name that resolves to
nothing costs one cheap lookup. It then tries the conventional careers paths on
that domain — `/careers`, `/jobs`, `/vacancies`, `/werken-bij`, `/karriere`,
`/en/careers` — stopping as soon as one returns a real page.

Giving the `careers_url` explicitly still helps, in two specific situations:

* the careers site is on a host the name does not suggest
  (`join.specsavers.com`, `werkenbij.woonstadrotterdam.nl`);
* the careers page is JavaScript-only and answers with an empty body — Klarna
  returns `202` and zero bytes on every path, which no HTTP client can read
  without a browser.

Neither affects Engine A, which probes the vendors directly using name-derived
tenant slugs and needs no website at all.

---

## How it detects

Two independent engines run for every company.

### Engine A — PROBE
Build the vendor-hosted URL from the company name and prove the tenant exists.

```
boards-api.greenhouse.io/v1/boards/stripe/jobs   -> 200 with 102 jobs  => real board
api.ashbyhq.com/posting-api/job-board/ramp       -> 200 with  17 jobs  => real board
nvidia.wd5.myworkdayjobs.com/wday/cxs/.../jobs   -> POST 200 total=2000 => real tenant
qxrihnrvp.taleo.net                              -> NXDOMAIN           => no tenant
zzzqxnottreal9911.homerun.co                     -> 302 404.homerun.co => no tenant
```

### Engine B — FINGERPRINT
Fetch the company's own careers page and look for links, iframes, scripts and
query parameters that point at an ATS. This is the only engine that can find
**Zoho Recruit**, **Eploy** and **Bullhorn** (their tenant ids are opaque
tokens), every customer hiding behind a **custom domain**, and it supplies the
exact slug that Engine A then proves.

```
Palantir  careers page  ->  jobs.lever.co/palantir           => Lever
Sqills    careers page  ->  careers-analytics.recruitee.com  => Recruitee
```

Engine B runs first, and the slugs it harvests are fed back into Engine A.

### A derived website is a redirect target, not a site root

With names-only input the scanner derives each website itself, and what comes
back is wherever the redirect chain ended — which is often a localised URL
carrying a query string:

```
Salesforce -> https://www.salesforce.com/?bc=OTH      NVIDIA -> https://www.nvidia.com/en-in/
Stripe     -> https://stripe.com/in                   Rows   -> https://rows.com/product
```

Pasting a careers path onto the end of those produces
`https://www.salesforce.com/in/?ir=1/careers` — the path lands *inside the query
string*, where it is not a path at all. `careers_path_candidates()` therefore
discards the query and fragment and generates each conventional path **twice**:
at the site root and under the locale prefix the redirect landed on, root first.
A `404` also no longer counts as "the careers page" however large its body —
`rows.com/careers` answers 404 with 30 kB of site chrome, and accepting it used
to stop the remaining paths from being tried.

This is what lets Engine B reach `jobs.nvidia.com/careers`, which links directly
to `nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite` — the exact-case
Workday tenant, found by fingerprint rather than by search.

### The identity guard — "whose board is this?"

A `200` from a tenant URL proves *a* tenant exists, not that it belongs to the
company you asked about. This produced two confident false positives in
testing:

```
Woonstad Rotterdam  ->  jobs.lever.co/wr   200, "World Relief"        (an NGO)
Woonstad Rotterdam  ->  wr.join.com/       200, "Jobs at W+R GmbH"    (a German firm)
```

`wr` was generated as the initials of "Woonstad Rotterdam". Both boards are
real; neither is Woonstad's. Two guards now prevent this:

1. **Short acronym slugs are not generated.** An acronym is only tried when the
   company name has three or more words and the result is at least three
   characters — two-letter slugs are hopelessly ambiguous.
2. **Every derived slug is identity-checked.** When a probe returns `VERIFIED`
   or `PROBABLE`, at least one distinctive word of the company name (4+
   characters) must appear in the page body, or in the URL that was requested.
   Otherwise the finding is demoted to `UNVERIFIED` with the reason recorded in
   `evidence` and `note`.

The check compares **both spellings** of the name, because a German tenant
registers `suedzucker` while `Südzucker` ASCII-folds to `sudzucker` — so the
name is tokenised *before* folding, and each token is matched in its plain and
transliterated form.

**A branded title outranks everything else.** Vendor-hosted boards say whose
board they are in the `<title>`:

```
Jobs at Babbel | JOIN        Jobs at KONUX GmbH | JOIN        Careers at Sqills | ...
```

That caught a false positive the token test could not. `teamtailor.join.com`
returns `200` with 81 kB of page, the slug *is* the company name, and the word
"teamtailor" appears 13 times in the body — but the title reads
**`Jobs at InVisions | JOIN`**. It is InVisions' board. So when the title names
a company that shares no distinctive word with the one you asked about, the
finding is demoted regardless of what the rest of the page contains. Legal-form
suffixes (`GmbH`, `AB`, `B.V.`, `SE & Co. KG`) are stripped first, and the
comparison is made against the *company name*, never the page body — the brand
came out of the body, so it would always "match" itself.

It applies **only to derived slugs**. A slug harvested from the company's own
careers page is theirs by construction, and demanding the company name also
appear on the portal would wrongly demote real boards that brand themselves
with a product name instead.

`python3 scan.py --selftest` runs seventeen offline identity cases — collisions,
correct matches, umlauts, acronym collisions and branded titles — so this guard
cannot silently regress.

---

### The page check — "does that URL actually show jobs?"

Every finding ends with the obvious question: open the portal URL and confirm it
lists jobs. `page_check()` does exactly that, fetching each reported URL (up to
3 MB) and scoring the HTML for job markup — JSON-LD `JobPosting` blocks,
repeated job-card CSS classes, links to individual postings, "open roles" /
"Vacancies" / "Stellenangebote" headings, a stated count, and show-more or
pagination controls, minus not-found wording.

A page confirms a listing only when **both** hold:

- `score >= 10` (the threshold), **and**
- it names at least one concrete posting (`jobs > 0` or `links > 0`).

The second condition is what separates a job board from a page that merely talks
about jobs. Two traps found in testing:

| Trap | What it looks like | Why it fails the check |
|---|---|---|
| **Ashby / Workday SPA** | `jobs.ashbyhq.com/buena` → `200`, 21,864 B | zero job markup; a bogus slug returns the same shell |
| **BambooHR feature flags** | `lightspeed.bamboohr.com/careers` → `200`, 101 kB, "Current Openings" | the 24 "job classes" are product toggles in one attribute (`zip_recruiter_boosted_jobs`, `perf_joborg_skills`), each appearing **once**, and the page names no posting |

Both traps promoted findings to `VERIFIED` before this was tightened. A class
token that appears only once is now ignored — real card classes repeat.

Calibrated against 25 live tenant URLs: **9/9 server-rendered boards confirm**
(Lever, Greenhouse, Teamtailor, SmartRecruiters, Recruitee, Flatchr) and
**0/11 bogus or empty pages do**.

**What it can and cannot decide.** 15 of the 27 vendors render jobs entirely
client-side, so their HTML scores 0 even for a live board — Ashby returns the
same empty shell for `buena` and for a random string, and
`nvidia.wd5.myworkdayjobs.com` is 6,791 bytes of shell. For those the vendor's
own API already did the proving, so a low score is recorded as `page_note` and
changes nothing. `JS_ONLY_VENDORS` lists them:

```
ashby, workday, pinpoint, workable, icims, avature, join, beetween, zoho,
eploy, bullhorn, successfactors, taleo, softgarden, bamboohr
```

It therefore **upgrades** (a `PROBABLE`/`UNVERIFIED` page that turns out to list
jobs becomes `VERIFIED` with `via=page_signals`, and a missing job count is
filled in) and **demotes in three narrow cases only**:

1. the rendered page is branded for a different company (the identity guard);
2. the URL bounced off its tenant onto the vendor's own marketing site —
   `appodeal.bamboohr.com/careers` 302s to `www.bamboohr.com`, and
   `careers.smartrecruiters.com/forto` to `jobs.smartrecruiters.com`, so there is
   no portal at that address at all. Never applied to a tier-1 API proof;
3. the account is switched off — BambooHR sends an expired tenant to
   `/settings/account/expired.php`.

Each confirmed page's score is written to the `page_signal_score` CSV column.
`--no-page-check` skips the step entirely.

---

## Learning portal patterns from known answers

`company_ATS_seed.csv` holds 81 real `(company name, portal URL, provider,
tenant slug)` rows. It is used two ways.

### 1. As a benchmark for slug derivation

Every tenant slug in that file was compared against what `slug_candidates()`
generates from the company name alone. That turned "guess better" into a
measurable target:

| | before | after |
|---|---|---|
| overall | 78% (62/80) | **95% (72/76)** |
| Ashby | 83% | **100%** |
| Workable | 50% | **100%** |
| SmartRecruiters | 67% | **100%** |
| BambooHR | — | **100%** |
| Greenhouse | 82% | 95% |
| Workday | 83% | 92% |
| Personio | 80% | 80% |

(Denominators differ because four rows sit on a company-owned domain, where
there is no tenant slug to derive at all — those are Engine B's job.)

The shapes that were missing, each now generated, each backed by a live board:

| Company | Real slug | Shape |
|---|---|---|
| Rows | `rowspace` | name + `space`, **with the doubled letter collapsed** (`rows`+`space` → `rowspace`, not `rowsspace`) |
| Pitch | `pitch-software` | name + `-software` |
| Leonardo | `leonardocompany` | name + `company` |
| Backbase | `workatbackbase` | `workat` prefix |
| Scalable Capital | `ScalableGmbH` | first word + legal form |
| Sonio | `sonio-ag` | name + `-ag` |
| Bol.com | `bolcomen` | name + locale |
| Ecosia | `ecosia.org` | a domain used verbatim as the slug |
| Mistral AI | `mistral.ai` | the last word of the name *is* the TLD |

Generating a slug is only half the job: **it has to sit inside the probe
budget**, or it is never tried. Deriving all nine shapes for all 27 vendors at
budget 34 cost 783 requests and 289 s for a *single* company, and a 20-company
run made no progress in eight minutes. So the budget is split.

| | slug list | probes | why |
|---|---|---|---|
| Ashby, Greenhouse, Lever, Workday, Personio, Workable, SmartRecruiters | learned, ≤ 44 | **26 slugs** (`EXTENDED_PROBE_BUDGET`) | every unusual slug in the seed file belongs to one of these seven |
| the other 20 vendors | core, ≤ 10 | **24 slugs** (`--probe-budget`) | their tenants are the obvious slug; the long tail buys nothing |

26 is the smallest budget that reaches every seed slug — the deepest is
`bolcomen` at position 24. It was 40 at first, which cost 637 requests and 227 s
for one company; a company that matches *nothing* now costs 571 requests / 165 s,
against 411 / 125 s before the pattern work. The difference buys real findings
(see the Personio bug below), and `--probe-budget` still lets you trade it back.

**The budget counts slugs, not requests.** This was a live bug, not a
hypothesis. Personio probes `jobs.personio.de` *and* `.com` for each slug, so a
request budget of 40 reached only slug 20 — and Sonio's real tenant,
`sonio-ag.jobs.personio.de`, is slug 24. It was generated correctly, ranked
correctly, and never fetched. The budget is now a slug budget with a request cap
of twice that, so a two-request vendor gets full coverage while a four-request
vendor still costs less.

**Dotted slugs are dropped where they cannot resolve.** Personio, Teamtailor,
BambooHR, Workday, Recruitee and JOIN put the tenant in the *hostname*, so
`sonio.org.jobs.personio.de` is not a long shot, it is NXDOMAIN. Which vendors
those are is detected by building one throwaway probe and looking at where the
slug lands — no list to maintain. Path-tenant vendors keep them, because
`ecosia.org` and `mistral.ai` are real Ashby slugs. This moved Sonio's tenant
from position 24 to 15 and deleted four hopeless requests per company.

Shapes that match nothing in the seed file are no longer generated at all:
`tech`/`labs`/`team`/`people`/`recruiting`/`talent`, the `.de`/`.co`/`.net`/`.app`
domains, `careersat-`, and hyphenated forms of every suffix except `software` and
`space`. Derivation recall is unchanged at 95%.

Ordering inside the list matters as much as the cap. `mistral.ai` was being
generated at **position 40** and never tried; moving the domain-as-slug,
TLD-join and `workat` shapes ahead of the long suffix tail brought it to
position 4, `workatbackbase` to 6 and `ecosia.org` to 2. The `slugs` self-test
suite asserts *position*, not mere presence, precisely because that is the
difference between a derivation that exists and one that runs.

Four rows remain unreachable from a name alone, and no amount of pattern
learning fixes them: `bamboohr17` (an arbitrary number), `quantum-diamond` (an
abbreviation expanded), `wk` (two-letter initials, deliberately excluded since
`wr` once resolved to World Relief), and Teamtailor's own `career` subdomain.

### 2. As a `--seeds` lookup at run time

```bash
python3 scan.py --csv companies.txt --seeds company_ATS_seed.csv -o results
```

A seed row is treated as **knowledge, not as an answer**. It supplies the real
portal URL, so Engine B starts from the right page instead of guessing careers
paths, and the real tenant slug, so Engine A probes it as a hint. Everything
still has to verify live, so a stale seed row cannot fabricate a result — and if
the seeded portal has since been taken down, the normal rules report it as
`UNVERIFIED` or `NOT_FOUND`. Name matching folds accents and legal forms, so
`simcorp`, `SimCorp`, `Cargo.one`, `Cargo One` and `Scalable Capital GmbH` all
find their row.

### Hosts and site ids the seed file revealed

Patterns that were simply absent before:

- **Lever has two disjoint estates.** An EU-resident board is a hard 404 on the
  global host: `jobs.lever.co/innogames` → 404 (297 kB of convincing 404 page),
  `jobs.eu.lever.co/innogames` → 200, 720 kB. Both hosts and both APIs
  (`api.lever.co`, `api.eu.lever.co`) are now probed. InnoGames was invisible
  before this.
- **Workday numbering is not confined to the low digits.** EMBL is on **`wd103`**
  (`embl.wd103.myworkdayjobs.com/EMBL`, 18 live requisitions). `wd103` joins the
  data-centre list.
- **The career-site vocabulary is wider than assumed.** Added from working
  tenants: `Ext` (Autodesk, 310), `External_Career` (Broadcom, 358),
  `{n}CareerSite` (Leonardo, 851), `{n}SiteWD` (Zalando, 173),
  `jobs-and-careers` (Philips, 780).
- **Greenhouse EU needs no special case.** `job-boards.greenhouse.io/mentimeter`
  and `boards-api.greenhouse.io/v1/boards/mentimeter/jobs` both serve the
  EU-resident board fine; only `boards-api.eu.greenhouse.io` fails to resolve.
  Verified rather than assumed.
- **BambooHR has a real JSON oracle**, which had been overlooked:
  `{tenant}.bamboohr.com/careers/list` answers `{"meta":{"totalCount":N},...}`.
  Against the seed file it is exact — `astroscale` 44 jobs, `brainrocket`
  **0**, which is precisely what that row's own note says ("API reachable, 0
  open postings"), and `lightspeed` **0**, confirming the empty template this
  README warns about. BambooHR no longer has to be judged by counting CSS
  classes on a page it auto-provisions for any recognisable name.
- **Portals on the company's own domain.** `career.spendesk.com` is Teamtailor —
  there is no tenant slug to derive. The conventional careers *subdomains*
  (`careers.`, `jobs.`, `career.`, `job.`, `werkenbij.`, `karriere.`) of the
  company's own domain are now tried as seeds and fingerprinted like any other
  page. They are added last, so a company with a normal `/careers` page never
  pays for them.
- **A "Powered by" link is proof of the vendor, not of the URL.** Every hosted
  board ends with a link to the vendor's own marketing site, so
  `www.teamtailor.com` is simultaneously the most common proof pattern in
  Engine B and the most useless one: `career.spendesk.com/jobs` contains no
  Teamtailor tenant URL at all, only that footer link. Reporting it sent the
  user to the vendor's homepage. Now, when the only proof is the vendor's bare
  root and the page carrying it is a real job listing on a non-vendor domain,
  **that page is the portal**. If the page is not a listing, nothing is
  substituted — a blog post that mentions Teamtailor proves nothing. Locked by
  the `custom-domain portals` self-test suite.
- **Trust the live fingerprint over the seed file.** The seed lists
  `jobs.virtuagym.com` as Recruitee; the page itself carries Teamtailor markers,
  and `virtuagym.teamtailor.com` answers with 5 live postings. The seed is a
  snapshot with verification dates, the network is now.

---

## Read this before trusting a result: the proof tiers

This is the part that separates a useful scan from a confidently wrong one.

**"HTTP 200" is not proof.** Several multi-tenant vendors **auto-provision a
branded page for any recognisable company name**. Verified examples:

| URL | What it serves | Is that company a customer? |
|---|---|---|
| `apply.workable.com/stripe/` | `{"name":"Stripe","jobs":[]}` | No — Stripe is on Greenhouse |
| `careers.smartrecruiters.com/Zoho` | "Careers at Zoho" | No |
| `infosys.pinpointhq.com` | "Jobs at Infosys \| Infosys Careers" | No |
| `palantir.bamboohr.com/careers` | a BambooHR career page | No — Palantir is on Lever |

A random string like `qxrihnrvp` gets a clean 404 from all of them, which is
exactly why this is easy to miss when you test with made-up names.

So every vendor is classified (`python3 scan.py --list-vendors`):

- **Tier 1 — authoritative (20 vendors).** The vendor only serves a page/API
  for a real tenant and answers with a clean negative otherwise (404,
  NXDOMAIN, or a redirect to its own marketing site). A positive probe *proves*
  usage. → Greenhouse, Lever, Ashby, Workday, Recruitee, Personio, Teamtailor,
  Homerun, SAP SuccessFactors, Oracle Taleo, iCIMS, softgarden, d.vinci,
  Flatchr, Taleez, Beetween, JOIN, Coveto, rexx, Avature
- **Tier 2 — corroborated (4 vendors).** A positive probe only means "a page
  exists under this name". It is promoted to `VERIFIED` only when the page
  actually lists live jobs, or the company's own careers page links to it.
  Otherwise it is reported as `UNVERIFIED` and **hidden by default**.
  → Workable, SmartRecruiters, Pinpoint, BambooHR
- **Tier 3 — fingerprint only (3 vendors).** The tenant id is an opaque token
  that cannot be derived from a company name at all.
  → Zoho Recruit, Eploy, Bullhorn

### A vendor using its own product

Personio recruits on Personio, softgarden on softgarden, Flatchr on Flatchr and
Tellent Recruitee on Recruitee. Those rows are factually correct, but in a list
of "which ATS does this company use" they can read like noise.

They are recorded with status **`SELF`** and **reported by default**, because a
company that recruits on its own product still has an official portal, and that
portal is the answer you asked for. Teamtailor is the concrete case: the right
result for the company Teamtailor is `career.teamtailor.com` — suppressing it
made the scanner return `NOT_FOUND` for a company whose portal it had already
found. `SELF` is a distinct status rather than `VERIFIED` so nobody mistakes it
for a customer finding. Pass `--exclude-self-matches` to hide them.

One consequence is worth knowing: Teamtailor's own portal lives at
`career.teamtailor.com`, a *tenant* of Teamtailor reached from a link on
`teamtailor.com`. Engine B normally discards a vendor URL found on that same
vendor's own domain — otherwise every Greenhouse board would "prove" it runs on
Greenhouse. That self-confirmation guard is lifted when the company **is** the
vendor, which is the only way this answer can be reached.

The test is deliberately narrow: it fires only when the company name *is* the
vendor, optionally with a legal-form suffix (`Personio GmbH`, `JOIN GmbH`) or a
known former brand (`Tellent Recruitee`). Names that merely start with the same
letters are kept — `Softgarden Software`, `Workday Partners Ltd`,
`Personio Consulting` and `Global Workday Services` all still report normally.
`--selftest` covers 17 of these cases.

### Stopping early

Three separate stops keep a `NOT_FOUND` company from costing as much as a
matched one:

* **Dead host.** When three consecutive guessed careers paths come back with
  nothing readable — a challenge page or an empty body — the remaining paths for
  that host are skipped. Specsavers and Klarna cost 4 requests instead of 9.
* **Found careers page.** As soon as one guessed path returns a real
  careers-looking page, the rest are not fetched.
* **No harvestable URL.** A finding that cannot produce a portal URL is dropped
  at the reporting gate rather than carried through as a weaker status.

### Bot-challenge pages are not content

Cloudflare and friends serve an interstitial with a real HTTP status and a few
kB of plausible HTML. `www.specsavers.com/careers` answers **403 with a 5.7 kB
"Just a moment..." page**, and `www.tcs.com/careers` the same. Treating that as
the careers page both stops the search and produces garbage fingerprints, so
these are detected and skipped — and a *probe* that lands on one reports
`BLOCKED` instead of claiming the tenant.

The wording has to be unambiguous. A naive substring test for `captcha` or
`access denied` wrongly rejected two perfectly good pages: `join.specsavers.com`
(200, 260 kB, real title) and `moss.join.com` (200, 75 kB) both embed reCAPTCHA
site keys. Body markers therefore only match challenge-specific phrasing, and
the shorter title markers are trusted only when the response also failed.

### The reporting rule: a portal URL, or NOT_FOUND

**A company is reported against an ATS only when the scan can hand back a valid
portal URL for it** — a page inside the ATS that belongs to that company and can
be opened. Anything short of that is `NOT_FOUND`, and the scan stops there. No
weaker row is kept alongside it as a consolation prize.

That is enforced in one place, `reportable()`, which every output path (console
table, CSV, JSON, Markdown) goes through:

```python
def reportable(f, statuses):
    return f.get("status") in statuses and portal_url(f) is not None
```

So a finding with no harvestable URL — a fingerprint marker that proved the
vendor but contained no slug, an Avature `{slug}.avature.net` login redirect, a
Workday tenant that answers 401 on every career-site id — cannot be reported,
whatever its status.

Two consequences are worth knowing before you read a result:

* **Specsavers is `NOT_FOUND` on names-only input.** Its `www.specsavers.com` is
  behind a Cloudflare challenge, so Engine B never sees the
  `career5.successfactors.eu/career?career_company=specSF2PROD` link, and that
  tenant code is opaque — no probe can guess it. Give the careers URL
  (`join.specsavers.com/ca/jobs`) and it becomes `VERIFIED`.
* **Polestar reports Teamtailor only.** It also has a real Workday career site,
  but it holds 0 open jobs, so that is `PROBABLE` and withheld.

### Statuses

| Status | Reported? | Meaning |
|---|---|---|
| `VERIFIED` | **yes** | Proved, and a portal URL is attached. An authoritative vendor API answered, the vendor served a real tenant page, live job permalinks were found, or the company's own careers page links to that ATS URL. |
| `PROBABLE` | no — `--include-probable` | A tenant that exists but whose page could not be confirmed as a live job listing: an Avature login redirect, an empty Workday career site, a custom-domain redirect. |
| `UNVERIFIED` | no — `--include-unverified` | A page exists under this company's name but nothing proves the company uses the product. |
| `SELF` | no — `--include-self-matches` | The company *is* the vendor of that product. |
| `NOT_FOUND` | terminal | No ATS produced a valid portal URL. The `evidence` column records what was exhausted. **This does not prove the company uses no ATS** — see Limitations. |
| `BLOCKED` | no | Vendor refused this network (WAF, rate limit, IP block). |

---

## Vendor URL patterns

| Vendor | Career-site URL | Proof |
|---|---|---|
| Greenhouse | `job-boards.greenhouse.io/{slug}` | `boards-api.greenhouse.io/v1/boards/{slug}/jobs` → 200 + job count / 404 |
| Lever | `jobs.lever.co/{slug}` | 200 vs a 404 "Not found" page |
| Ashby | `jobs.ashbyhq.com/{slug}` | `api.ashbyhq.com/posting-api/job-board/{slug}` → 200 / 404 (the HTML page returns 200 for *anything* — never trust it) |
| Workday | `{slug}.wd{N}.myworkdayjobs.com/{site}` | `POST /wday/cxs/{slug}/{site}/jobs` → 200 + `total` / 422. Both `wd{N}` and the case-sensitive site id are searched |
| Workable | `apply.workable.com/{slug}/` | `/api/v1/widget/accounts/{slug}` → 404 unknown; 200 with `jobs:[]` is **not** proof |
| BambooHR | `{slug}.bamboohr.com/careers` | unknown tenants 302 → `www.bamboohr.com` |
| Recruitee (Tellent) | `{slug}.recruitee.com/` | unknown tenants 301 → `recruitee.com/careers_not_hosted` |
| Personio | `{slug}.jobs.personio.de/` or `.com/` | 200 vs 404; `/xml` is the public job feed |
| SmartRecruiters | `careers.smartrecruiters.com/{CompanyId}` | `api.smartrecruiters.com/v1/companies/{id}/postings` → `totalFound>0`; unknown names bounce to `jobs.smartrecruiters.com` |
| Teamtailor | `{slug}.teamtailor.com/` | 200 vs a "Teamtailor — 404" shell; sitemap `/jobs/` links |
| Homerun | `{slug}.homerun.co/` | unknown slugs 302 → `404.homerun.co/working_at/{slug}` |
| SAP SuccessFactors | `{slug}.jobs.sap.com`, `{slug}.jobs.successfactors.com/.eu` **and** `career{N}.successfactors.eu/career?career_company={CODE}` | dedicated host: **no wildcard DNS**, NXDOMAIN is definitive. Shared host: real code → ~180 kB "Career Opportunities", unknown code → ~3 kB |
| Oracle Taleo | `{slug}.taleo.net/careersection/{n}/jobsearch.ftl` | **no wildcard DNS** — NXDOMAIN is definitive |
| iCIMS | `careers-{slug}.icims.com/jobs` | non-tenants answer `404 <title>gone: {host}</title>` |
| softgarden | `{slug}.softgarden.io/`, `{slug}.career.softgarden.de/` | 404 "Unknown subdomain" / 500 |
| d.vinci | `{slug}.dvinci-easy.com/` | **no wildcard DNS** |
| Flatchr | `{slug}.flatchr.io/` | 404 → `/fr/404-not-found/` |
| Taleez | `{slug}.taleez.com/` (also `jobs.{company}.com`) | 404 `<title>Erreur</title>` |
| Beetween | `{slug}.betheen.com/`, `app.beetween.com/{slug}/recrutement` | NXDOMAIN; the SPA cannot be verified without JS |
| Pinpoint | `{slug}.pinpointhq.com/` | 404 shell vs **`sitemap.xml` containing `/postings/{uuid}`** |
| Zoho Recruit | `recruit.zohopublic.com/careers/{opaque-id}` | fingerprint only — the id is a random token |
| Eploy | embedded in the company's own domain (`/vacancies`, `?vacancyId=`) | fingerprint only — no vendor-hosted URL exists |
| JOIN | `{slug}.join.com/` → `join.com/companies/{slug}` | 404 shell vs `<title>Jobs at X \| JOIN</title>` |
| Coveto | `{slug}.coveto.fr/` | **no wildcard DNS** |
| rexx systems | `{slug}.rexx-systems.com/` | **no wildcard DNS** |
| Bullhorn | `(public-)rest{n}.bullhornstaffing.com/rest-services/{corpToken}/` | fingerprint only — corpToken is opaque |
| Avature | `{slug}.avature.net/` | **no wildcard DNS**, but the default page is a JS *login* redirect, not a job listing → `PROBABLE` |

---

## Options

```
--csv FILE, -i FILE     input .csv/.tsv/.txt list
--seeds FILE            CSV of known company -> portal rows, used as knowledge
-o PREFIX               output prefix (default: ats_scan)
--mode {all,probe,fingerprint}
--vendors ID [ID ...]   restrict, e.g. --vendors greenhouse lever ashby
--workers N             parallel companies (default 12)
--probe-budget N        max slugs probed per company per vendor (default 24;
                        the seven vendors with learned slug shapes use 26)
--max-career-pages N    careers pages to crawl per company (default 5)
--timeout SECONDS       per-request timeout (default 20)
--delay SECONDS         politeness delay per host (default 0.12)
--include-unverified    also report tier-2 stub pages that prove nothing
--include-probable      also report PROBABLE findings (a tenant that exists but
                        whose page could not be confirmed as a live listing)
--exclude-self-matches  hide a vendor's use of its own product (shown by default
                        with status SELF)
--no-page-check         do not open each reported URL to confirm it shows jobs
--no-verify-fingerprint do not re-probe URLs harvested from careers pages
--limit N               only the first N companies
--resume                continue an interrupted run from the existing -o JSON
--checkpoint N          rewrite the output files every N companies
--list-vendors          print the vendor table with tiers and patterns
--selftest              validate every rule: reporting, self-match, identity,
                        page signals, URL construction, then live probe calibration
--fingerprints          validate Engine B by extracting the ATS from real careers pages
--quiet                 no console table
```

Useful recipes:

```bash
# fast pass over a big list, US vendors only
python3 scan.py --csv big.csv --workers 20 --probe-budget 12 \
                --vendors greenhouse lever ashby workable workday

# the strictest possible output: proved findings only
python3 scan.py --csv big.csv --quiet && grep VERIFIED big_results.csv

# show me everything, including the auto-provisioned stubs
python3 scan.py --csv big.csv --include-unverified --include-probable

# skip the final "open the URL and check it lists jobs" pass
python3 scan.py --csv big.csv --no-page-check

# just the names, nothing else - the scanner derives each website itself
python3 scan.py --csv companies.txt -o results
```

---

## Self-test

`--selftest` runs nine suites. The first eight are **offline** — no network, no
rate limits, they cannot flake:

| Suite | Cases | What it protects |
|---|---|---|
| reportable | 10 | the reporting rule: a finding needs a valid portal URL; `PROBABLE` withheld unless opted in |
| self-match | 17 | a vendor's own board is labelled `SELF`, but `Softgarden Software` is still a customer |
| identity | 17 | a tenant that answers 200 must still look like the company searched for, including branded titles |
| page-signals | 16 | the rendered HTML must name real postings — SPA shells, JS bundles, 404s and BambooHR's feature-flag template must not |
| url-construction | 8 | a guessed careers URL must join the site root, never the end of a redirect target |
| slugs | 19 + 4 | a learned portal slug is derived **within the probe budget** that vendor actually gets; the 4 unreachable shapes are asserted unreachable, so they cannot silently start passing or failing |
| custom-domain portals | 6 | a vendor's own marketing root is never reported as the portal; the page carrying it is, only if that page is a real listing |
| probe-budget | 13 | the budget buys **slugs**, not requests, and dotted slugs are dropped exactly on the vendors that put the tenant in the hostname |
| calibration | ~50 | one known-real and one known-fake tenant through every vendor rule (**live network**) |

The calibration suite runs one known-real and one known-fake tenant through every rule.
The **"miss" rows are the important ones** — they are false-positive controls
for the tier-2 vendors that answer 200 for any recognisable company name:

```
$ python3 scan.py --selftest
  greenhouse       stripe                 ok   VERIFIED    public API 200 with 102+ open job(s)
  greenhouse       zzzqxnottreal9911      ok   MISS        API 404 - no such tenant
  workable         infosys                ok   UNVERIFIED  account exists but publishes 0 jobs
  pinpoint         sage                   ok   UNVERIFIED  real board, but sitemap.xml lists no job permalinks
  pinpoint         infosys                ok   UNVERIFIED  ... no job permalinks - auto-provisioned shell
  icims            rambus                 ok   VERIFIED    HTTP 200 iCIMS career portal
  icims            qxrihnrvp              ok   MISS        404 'gone: <host>'
  ...
All calibration cases passed.
```

Run it after any change to `vendors.py`, and periodically — vendor behaviour
drifts. A `SKIP BLOCKED` row means the vendor refused this network, not that
the rule is wrong.

`--fingerprints` does the same for Engine B, on real customer careers pages,
including two **negative controls** (companies that run their own portal and
must therefore produce *no* match):

```
$ python3 scan.py --fingerprints
  Palantir     ok   expect lever            got ['lever']
               url: https://jobs.lever.co/palantir
  Specsavers   ok   expect successfactors   got ['successfactors']
               url: https://career5.successfactors.eu/career?career_company=specSF2PROD&...
  Bullhorn     ok   expect workday          got ['workday']
               url: https://bullhorn.wd1.myworkdayjobs.com/BullhornCareers
  Sqills       ok   expect recruitee        got ['recruitee']
               marker only, no URL to harvest: 'careers-analytics.recruitee.com'
  Stripe       ok   expect greenhouse       got ['greenhouse']
               marker only, no URL to harvest: '"greenhouseId":'
  Infosys      ok   expect no ATS           got -
All fingerprint cases passed.
```

Two of those are worth understanding. **Stripe** builds its own careers UI on
top of the Greenhouse API: the page contains hundreds of `{"greenhouseId":...}`
objects and not a single `greenhouse.io` URL, so the marker proves the ATS but
Engine A has to supply the URL. **Specsavers** shows the SuccessFactors
*shared-host* form, where the tenant is a `career_company=` code
(`specSF2PROD`) rather than a subdomain — that shape is very common in
enterprise SuccessFactors and is invisible to any subdomain-only rule.

---

## Limitations (read this)

1. **`NOT_FOUND` ≠ "not a customer."** It means "no evidence found by these two
   engines". A company on a custom domain whose careers page is rendered
   client-side (React/Angular with no ATS link in the initial HTML) is
   invisible. Infosys in `example_companies.csv` is exactly this case — it runs
   its own portal and correctly reports nothing.
2. **Slug guessing is heuristic.** The tenant id is chosen by the company, not
   derived from its legal name. `Südzucker AG` lives at
   `suedzucker-gruppe.dvinci-easy.com`; the scanner tries 10 variants
   (`suedzucker`, `suedzucker-gruppe`, `suedzuckerag`, ...) but will miss
   arbitrary ones like `acme-hiring-2019`. Feeding the real careers URL fixes
   most of these, because Engine B harvests the slug from it.

   Two real misses from the reference run, both for the same reason:

   | Company | Real portal | Why names-only cannot reach it |
   |---|---|---|
   | Rows | `jobs.ashbyhq.com/rowspace` | the slug is `rowspace`, not `rows`; `rows.com/careers` is a 404, so Engine B has no page to harvest it from |
   | Pitch | `apply.workable.com/pitch-software/` | the Workable account `pitch` exists but publishes 0 jobs; the real one is `pitch-software`. **Found anyway** — Engine B harvested it from another page on `pitch.com` |

   `Rows` is the honest limit: when the slug is a rebrand artefact *and* the
   careers page is unreachable, names-only input cannot recover it. Add the
   careers URL to the CSV and it resolves immediately.
3. **Workday needs a two-step search.** Neither the tenant number (`wd1`..`wd13`)
   nor the career-site id appears in any discoverable endpoint, and the site id
   is **case-sensitive** (Workday's own tenant uses `Workday`, not `workday`).

   `run_workday_search()` therefore starts with a **tenant-existence sentinel**:
   one POST per wd number using a deliberately invalid site id
   (`__ats_probe__`). Workday answers `404` when the *tenant* exists on that
   number and `422` when it cannot resolve the tenant at all — so 8 cheap
   requests settle whether the company is on Workday before anything expensive
   happens. Only the wd numbers that answered are then searched for the real
   career-site id, most-common ids first.

   The effect is large: a non-Workday company costs ~8 probes / 1.5 s instead of
   ~150 probes / 30 s, while `salesforce` still resolves in ~2 s
   (`wd12/External_Career_Site`, 1535 jobs) and `nvidia` in ~3 s
   (`wd5/nvidiaExternalCareerSite`, 2000 jobs).

   Three outcomes are deliberately distinguished:
   * `VERIFIED` — CXS API returned 200 with live jobs.
   * `PROBABLE` — 200 with **0** jobs (a real but empty career site), or a
     tenant that throttled the search.
   * `MISS` — the sentinel found no tenant, **or** found one that answers `401`
     on every site id. The second case matters: Stripe, Airbnb and Palantir all
     have Workday tenants for internal HCM while recruiting through Greenhouse
     and Lever, so reporting those as a Workday portal would be wrong. The fact
     is kept in the `evidence` column instead of the `url` column.

   The GET status of the UI page is *not* a usable oracle —
   `nvidia.wd5/External_Career_Site` 404s while `NVIDIAExternalCareerSite`
   works, and an unknown tenant returns 500. Only the CXS POST is
   authoritative.
4. **Homerun, Taleez, Personio and iCIMS rate-limit or IP-block.** From a
   datacentre IP you will see `BLOCKED` / `429`. Re-run later, slow down with
   `--delay 0.5`, or run from a residential connection.
5. **Tier-2 vendors cannot be proved by probing alone.** If Workable's widget
   API reports `jobs:[]`, that is indistinguishable from an auto-provisioned
   stub — hence `UNVERIFIED` and hidden by default.
6. **Eploy, Zoho Recruit and Bullhorn need the careers page.** Without
   `website`/`careers_url` in your input they can never be detected.
7. **A tenant can exist without a public job listing.** `specsavers.avature.net`
   and `bosch.avature.net` both resolve and serve a login redirect: real Avature
   tenants, but the candidate-facing site is elsewhere. Reported `PROBABLE`, not
   `VERIFIED`.
8. **Some companies leave no fingerprint at all.** Südzucker recruits through
   `suedzucker-gruppe.dvinci-easy.com`, but that link appears only in its
   privacy policy, never on its careers pages — so it correctly reports
   `NOT_FOUND`. Deeper crawling would not help; the link is not in the careers
   section.
9. **This is a point-in-time HTTP scan**, not a commercial technographic
   database. Vendors change patterns; `--selftest` is how you find out.

Be polite: the scanner sends a descriptive User-Agent, keeps a per-host delay,
retries only on 429/5xx, and caps how many bytes it reads.

---

## Files

```
ats_scanner/
├── scan.py                    CLI, fetcher, both engines, reporting
├── vendors.py                 the 27-vendor registry: URL patterns, judges, fingerprints
├── requirements.txt           httpx
├── companies_template.txt     empty input template with usage notes
├── company_ATS_seed.csv       81 known company -> portal rows, for --seeds
├── reference_run/             17 companies, names only - checked against known answers
│   ├── companies.txt          the input: one company name per line
│   ├── RESULTS.md             the table, the comparison, and the false positives it removed
│   ├── user17c_results.csv
│   ├── user17c_results.json
│   └── user17c_hits.md
├── proof_run/                 5 unseen companies, pure defaults
├── example_companies.csv      16 companies, NAMES ONLY - the primary example
├── demo_results.csv           output of that names-only run
├── demo_results.json
├── demo_hits.md
├── example_companies_full.csv the same 16 with optional website/careers_url filled in
├── demo_full_results.csv      output of that run - diff it against demo_results.csv
├── demo_full_results.json
├── demo_full_hits.md
└── README.md
```

Start with **`reference_run/RESULTS.md`**. It is the clearest picture of what the
scanner does and does not recover: 17 names in, 15 matched, 13 of 14
known-correct portal URLs reproduced exactly, one honest miss (`Rows`, whose
slug is `rowspace`), and an account of the four false positives an earlier pass
produced and the specific hole each one exposed.

The two example inputs are the same 16 companies. Diffing
`demo_results.csv` against `demo_full_results.csv` shows precisely what the
optional columns buy: Engine A finds the same tenants either way, while Engine B
gains the companies whose careers pages it cannot reach by guessing — most
importantly **Specsavers**, whose `www.specsavers.com` is behind a Cloudflare
challenge and whose SuccessFactors tenant is an opaque `career_company=` code
that no probe could ever guess. Klarna stays `NOT_FOUND` in both, because its
careers site answers every path with `202` and an empty body.

`vendors.py` is the file to edit when a vendor changes. Each entry carries a
`notes=` string explaining the empirical behaviour it was written against, and
a `CALIBRATION` row so `--selftest` catches regressions.
