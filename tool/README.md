# FastLocations Data Refresh Tool

Keeps the data behind **Where to Expand: My Project** current.

`scorer.py` loads about 35 runtime JSON files. Most are produced by a
`build_*.py` script in `Projects/` that takes one source file and writes one
JSON. Running those by hand means remembering which script goes with which
download, where the download lives this year, and what a good result looks
like. This tool does that part.

    python refresh_web.py     ->  http://localhost:5003

Port 5003, so it sits alongside the Incentives Search Tool (5001) and the
Organizations editor (5002).

    pip install flask requests

---

## What a refresh actually does

    acquire  ->  build  ->  validate  ->  keep, or roll back

1. **Acquire** — download the source, or pick up the file you staged.
2. **Build** — run the registered `build_*.py` with that file as `argv[1]`,
   streaming its output into the log.
3. **Validate** — parse what it wrote, count the records, and count how many of
   its keys the scorer can actually **use**.
4. **Keep or roll back** — a rebuild is **rejected**, and the backup taken in
   step 2 restored, if it lands under its floor, loses more than a third of its
   records, or loses a quarter of its usable keys.

Step 4 is the reason the wrapper exists. Running a builder by hand against a
source that has quietly changed shape gives you a twelve-record
`county_nri.json` and a model that silently stops screening on hazard. Here
that outcome is refused and the previous file comes back.

**Usable keys, not record counts.** The count on its own is not a safety check.
Rebuilding `ca_livability` from an approved URL that turned out to point at the
**2016** CIMD produced *more* records than before — 283 → 699 — while its usable
coverage collapsed from 283 census divisions to 36, because the 2016
dissemination areas roll up to a different code space than the 2021 divisions
the model is keyed on. The count went up, so a floor check waved it through.
Datasets therefore declare a `key_space` (`county_features.json` or
`ca_features.json`), and validation counts the overlap. A file can gain records
and still lose the scorer.

Backups land in `Projects/_data_backups/<dataset>/`, five deep.

---

## Only update newer files

On by default. Before rebuilding anything, the tool asks whether there is
something newer to build **from** — and skips the dataset if there is not. The
evidence differs by kind:

| Kind | How "newer" is judged |
|---|---|
| Auto download | The server's `Last-Modified` / `ETag` / length, against the signature recorded after the last successful build |
| Manual download | mtime of whatever you dropped in `staging/<dataset>/` |
| Derived locally | mtime of the local inputs in the registry's `depends_on` |
| Live API / Census API | No version to inspect, so the publication cadence stands in |

Two deliberate exceptions:

- A file that is **below its own record floor** is never "current". It needs
  rebuilding whatever the source says.
- A dataset pulled in as a **dependant** is always rebuilt. If
  `build_utility_territories.py` rewrote the EDO master table, the routing
  indexes are rebuilt from it even though nothing about *their* inputs looks
  newer to a timestamp check.

Anything the check cannot answer confidently returns "refresh", not "skip", so
an unknown never silently blocks an update.

Untick the box in the UI, or pass `--force` on the command line, to rebuild
regardless.

---

## The five kinds of dataset

The table groups by how a refresh is obtained. This is the honest state of the
data layer, not an aspiration.

**Automatable — download or API (11).** Five fetch a zip from a stable URL (BEA,
EIA-861, and three StatCan tables); two — `county_drought` and `county_power` —
query public APIs directly and need nothing configured; three need the free
Census API key in `refresh_config.json`: `county_occupation`,
`county_innovation` and `county_workforce`.

Census is worth a warning. It answers a *missing* key with an HTML "Missing Key"
page under **HTTP 200**, and an *unactivated* key with an HTML "Invalid Key" page,
also under HTTP 200 — so both look like a parse failure rather than an auth
failure unless you read the body, and a plain status check calls them success.
"Check sources only" therefore runs a real one-row query and reports what Census
actually said. A newly signed-up key stays "Invalid" until you click the
activation link in the confirmation email.

**Derived locally (7).** No network. They read files already on disk —
`build_edo_indexes.py` turns the EDO master table into the two routing indexes,
`build_ca_cma.py` derives metro labels from the crime file, and
`build_us_catchment.py` computes market catchment from county centroids.

**Manual download into staging (6).** The source sits behind bot protection or
a form. FEMA's `hazards.fema.gov` answers 403 to every non-browser request;
StatsAmerica, USDA NASS, CIMD and the ODI GeoPackages come off catalogue pages.
Download the file, drop it in `staging/<dataset>/`, then refresh — the tool
finds it, unzips it if needed, and builds. It never deletes anything you put
there by hand.

**Researched — Brave + Claude (1).** `state_infrastructure_grades.json`. ASCE
grades each state on its own four-year cycle, so this is never uniformly
current.

**No builder — legacy (9).** Loaded by the scorer, but nothing in the repo
rebuilds them. They are listed anyway, with dates and record counts, because
leaving them out would make the inventory look complete when it is not. These
are not files anyone chose to leave alone — each was produced once by an ad-hoc
script that was not kept, so there is no command that regenerates it and no way
to tell from the file whether it is still right. `county_features.json` is the
single largest input to the US model — income, education, unemployment, crime,
property tax, health outcomes — and nothing here can rebuild it. Refreshing one
of these means writing a new `build_*.py` and registering it, which is what
happened to `county_drought`.

### county_drought, and why the default is a five-year mean

`county_drought.json` used to be one of the legacy files: a single US Drought
Monitor map from 2026-07-05, never refreshed. `build_county_drought.py` replaces
it from the USDM county statistics API.

A single weekly map is weather, not water security, and the scorer does not
treat it gently — `not_in_drought >= 50` is a **hard exclusion** when a project
sets `drought="required"`. Bullock County, Alabama, on three consecutive maps:

    2026-06-23   11.5     excluded
    2026-06-30   89.6     included
    2026-07-07  100.0     included

Same county, same aquifer, three weeks apart, and the answer to "can I put a
plant here" turned on which Thursday the file was built. The default now
averages every weekly map over five years (~261 maps). The old snapshot was
effectively binary — median 9.0, with 1072 counties pinned at exactly 100; the
mean is a genuine gradient (median 48.8, one county at 100) and excludes
slightly *fewer* counties than before, 52.6% against 56.5%.

`--mode latest` reproduces the old point-in-time behaviour. `--severity d1`
scores on D1+ drought only, ignoring D0 "abnormally dry"; the default counts D0,
which is what the file it replaced did.

It also fixed a silent hole: Connecticut replaced its counties with planning
regions in 2022, USDM moved to the new FIPS and `county_features.json` did not,
so **all eight Connecticut counties were getting no drought data at all**. US
coverage is now 3143 of 3143 counties.

### county_workforce

Rebuilt by `build_county_workforce.py` from ACS `B23025` (employment status) and
`B23020` (mean usual hours). The original script was not kept, so the derived
fields were recovered from the file itself and verified against `County_1.csv`:
**zero differences across 3,222 counties and all five fields.** The formula worth
knowing is

    recruitable = unemployed + 0.25 x not-in-labor-force

— a judgement about latent labour supply, not a measurement, exposed as
`--nilf-share`. The obvious objection is that 16+ "not in labor force" includes
retirees, so retirement counties should look falsely hirable. Measured across
3,135 counties that is mostly not so: the correlation with non-working-age share
is 0.04. It bites only in the tail, where you would expect — Sumter County FL
(The Villages) scores 0.80 availability against a national median of 0.22.

It closed the same Connecticut hole as the drought builder, and the same way for
a different reason. The ACS reports planning regions; `county_features.json`
does not; the state scored blank. Counts cannot be averaged across
non-nesting geographies, so the bridge takes **rates** from the regions and
**levels** from each county's own labour force already in `county_features`.
Coverage went 3135 → 3143 of 3143, and the bridged counties land inside the
normal range rather than as outliers. Bridged FIPS are named in `_meta.ct_bridged`
so they are never mistaken for directly measured ones.

`--csv County_1.csv` rebuilds from the original ESRI export. That reproduces the
file rather than refreshing it — `County_1.csv` is a fixed old extract — so it is
a verification path, not a substitute for the API key.

### county_power

Rebuilt by `build_county_power.py` from the EIA power-plant layer published as an
ArcGIS feature service — public, no key, paged.

This was the stalest thing in the model. The file it replaced came from a static
export stamped **201705**: EIA-860, May 2017. That snapshot holds 1,887 US plants
of 100 MW or more; there are 2,459 today, and almost all the growth is solar and
wind. `renew_share` — the number a project screens on when it asks for a greener
grid — was answering a question about 2017. Refreshing moved the median county
from 2,168 to 4,179 MW of capacity in range, and median renewable share from
13.2% to 19.6%, with the largest gains in the South Dakota wind counties.

Three things about the method were recovered rather than assumed, by scanning
the stored file:

- **Radius is 95 km, not 60 miles.** `scorer.py`'s comment says "~60mi". 95.0 km
  reproduces the stored file on 147 of 150 sampled counties against 68 of 80 for
  60 miles — the original used a round metric number.
- **Pumped storage counts as renewable** (matching 2,264 of 2,266 rows against
  2,241 if excluded). Arguable — it is storage, not generation — but preserved,
  and `--no-pumped-storage` turns it off.
- **Canada and Mexico were never used**, despite the export being titled "North
  American Power Plants" and carrying 379 of their plants. US plants alone
  reproduce the stored file on **2,891 of 2,891** counties; adding Canada drops
  that to 98.4%, and the misses are exactly the border counties. US-only stays
  the default, which is also right going forward — the live EIA layer is US-only,
  and splicing a 2025 US inventory onto a 2017 hand-assembled Canadian one would
  mix vintages inside one number. `--include-foreign` opts in.

Counties with nothing in range are **omitted, not written as zero** — again what
the stored file did, and it matters: an absent key leaves the scorer's field null
and null weights renormalise, while a zero would actively score the county worst
in class. `--emit-zero` changes that, and changes scores.

### county_features — the partial one

`county_features.json` is the largest input to the US model: 41 fields, 3,143
counties, feeding every US dimension. `build_county_features.py` is the only
builder here that **updates its output in place** instead of rewriting it, and
the only one that cannot recreate its file from scratch. Delete
`county_features.json` and nothing here brings it back.

The reason is licensing. Much of the file is ESRI Business Analyst data — the
`_CY` "current year" fields — which is not public. Two fields have no public
equivalent at any price point: `WLTHINDXCY` (ESRI Wealth Index) and `SEI_CY`
(Socioeconomic Status Index). So the script works in blocks, refreshes what it
can source, carries the rest forward untouched, and prints exactly which is
which. Provenance goes to a **sidecar** `county_features_meta.json`, never
inside the output — the scorer loads this file *as* its county table and
iterates it directly, so a `_meta` key inside would become a phantom 3,144th
county.

| Block | What it does | Wired to the button |
|---|---|---|
| `chr` | 6 fields from County Health Rankings. Same measures, newer vintage — median change **0.0%** on four of the five health fields, 1.2% on population. Verified by exact digit match against the stored file. | **yes** |
| `acs` | 14 ESRI `_CY` fields from Census ACS. The only sustainable public path for them — but a methodology change, not a refresh. | no |
| `growth` | `POPGRW20CY` from the 2020 Decennial count. Median move 0.31 percentage points. | no |

The `acs` block is deliberately **not** on the button. Swapping ESRI current-year
projections for a five-year survey average moves the education split hard —
median 12.8% on `HSGRAD_CY`, 10.7% on `ASSCDEG_CY`, 25% on unemployment — and
`education_attainment` carries a 2x metric weight. Measured on bachelor's-or-
higher share, the old-vs-new **Spearman rank correlation is 0.9696**: rankings
shift materially. It also cannot cover Connecticut, so running it would leave 8
counties on ESRI values while the other 3,135 moved to ACS.

Decide it with the numbers in front of you:

```bash
python build_county_features.py YOUR_CENSUS_KEY --dry-run
```

That writes nothing and prints every field's median change, how many counties
move more than 10%, the median absolute move, and the ranking impact.

### Connecticut — migrated (done)

CT abolished its counties as statistical geography in 2022. Every live source
followed; `county_features.json` — which *is* the scorer's county table — did
not. Because the scorer looks each of its own FIPS up in the other tables, that
one mismatch silently blanked Connecticut in **six** datasets at once, four of
them completely: `county_nri`, `county_bea`, `county_occupation` and
`county_innovation` had all moved to planning regions and matched nothing.

`migrate_ct_geography.py` moved the key space to the nine planning regions
(09110–09190). **The crosswalk is derived, not hand-assembled.** Connecticut's
169 towns nest cleanly inside both the old counties and the new regions, and
their COUSUB codes did not change, so joining ACS 2021 (town → old county)
against ACS 2023 (town → new region) gives an exact mapping: 169 towns on both
sides, identical codes, zero name mismatches. Town population supplies the
weights.

Counts are apportioned by each town's share of its old county; rates are
population-weighted; labels and `infra` (physical asset counts, which cannot be
split into fractional airports) come from the dominant contributing county.
Where a source already publishes the regions, that authoritative value wins and
the apportionment is only a fallback.

Checks that it worked: apportioned population sums to 3,610,650 against
Connecticut's actual ~3.61M; the wealth gradient is right (Fairfield-area regions
at index 181–183, the northeast at 89); and all nine regions now carry a value in
every scored dimension. A CT-restricted run returns five ranked regions where it
previously had nothing to rank.

`lat`/`lon` are the one approximation — population-weighted means of the
contributing county centroids, feeding distance work. Connecticut is ~110 miles
across, so the error is small, but it is an error.

The two per-builder bridges are now **self-retiring**: each checks whether the
legacy FIPS are still live in `county_features.json` and skips itself if not, so
they stop writing stale keys but come back automatically if the migration is
rolled back. Pre-migration copies of all ten affected files are in
`_data_backups/ct_migration/`.

Still missing for CT, and missing before the migration too: `property_tax_rate`
(Tax Foundation publishes on old geography) and `hh_poverty`.

### us_catchment

`build_us_catchment.py` rebuilds the market-catchment population with no network
at all — it needs only the centroids and populations already in
`county_features.json`. That is why it is wired as a **dependant**: refreshing
the feature table drags it along automatically, because a catchment computed
from last year's populations describes a country that no longer exists.

It exists to stop county lines deciding the market_size dimension. New York
County is 1.6 million people and Los Angeles County is 9.7 million, so raw
population ranks Manhattan at a fraction of LA. Summing the population reachable
from each county, decayed with distance, reverses that to **12.2 million against
10.7 million** — which is the honest answer.

The formula was recovered by fitting the stored file:

    catchment(i) = SUM over j within 110 km of  pop(j) * exp(-d(i,j) / 55km)

Exponential decay is unambiguous — flat sum misses by 62%, linear taper 9.3%,
Gaussian 8.5%, inverse-square 4.6%, this 0.72% — and the minimum is sharp, with
R=105 or 115 tripling the error and lambda=54 or 56 making it ten times worse.

On the residual: it is **not** population drift, and I checked rather than
assumed — rebuilding against pre-refresh populations gives the same 0.71%. At
lambda=55 the median *signed* error is -0.19%, so the parameters are unbiased
and what remains is about 1% per-county scatter from inputs that differed when
the original was built. This reproduces the measure to within a percent, not to
the digit, and should not be described as more than that.

### incentives_index — connecting work already done

The old `build_incentives_index.py` could not run: it carried hard-coded sandbox
paths (`/sessions/modest-upbeat-mendel/mnt/...`). So the index sat frozen at
**1,557 programs** while the Incentives Search Tool on port 5001 kept collecting,
reaching **6,930**. The scorer was ranking the incentives dimension on 22% of the
evidence you had already paid to gather.

Rebuilt against the tool's master (which carries Canada; the published
`all_states_*.json` is split by country and is US-only). Registered as `derive`
with the master as its `depends_on`, so it rebuilds after an incentives run and
skips otherwise.

**Coverage:** 61 → 65 jurisdictions. DC, Puerto Rico, Northwest Territories,
Nunavut and Yukon had no record at all and scored null on the whole dimension.

**Detection:** `fast_track_permitting` 3 → 32 jurisdictions, `foreign_trade_zone`
3 → 24, `property_tax_abatement` 34 → 61, `payroll_rebate` 21 → 49.

`tif` went the other way, 42 → 30, and that is the bug being fixed: matching
"tif" as a substring hit "cer**tif**ied" and "mul**tif**amily", and **347 of 396
matches were false positives**. Short acronyms (`tif`, `ftz`, `r&d`) now match on
word boundaries. `land_writedown` stays at 3 programs — checked, not assumed:
land discounts are local site-specific deals and this master covers the
state/provincial and utility tiers.

`max_value_usd` is now capped at $10B. The old index held entries of **$500
trillion** from misparsed free text, and that field feeds `value_target_fit`
directly, so the garbage silently maxed the metric out.

### The scorer constant that had to move with it

More data changed how an existing constant behaved. `priority_match` capped each
type at 3 programs — calibrated when the median jurisdiction had 26. At a median
of 120, the common types saturated: `cash_grant`, `tax_credit`,
`job_training_grant` and `low_interest_loan` all hit the cap in **65 of 65**
jurisdictions, so picking any of them scored 100 everywhere and the metric stopped
discriminating.

`scorer.PRIORITY_DEPTH` is now **10** (was an inline `3`), which puts the median
at 92.7 with a 35-point spread. All 39 scorer tests still pass.

One caveat recorded next to the constant: 60 of 65 jurisdictions sit exactly at
the incentives tool's collection ceiling (120, or 90 for jurisdictions it treats
as small — which includes most Canadian provinces), so type depth is a sample,
not a census. Checked rather than assumed: normalising for that ceiling closes
only 1.4 points of the 11.3-point US/Canada gap, so it is a minor artefact and
the rest is a real difference in programme mix.

### Staleness is measured on the data, not the file

A file's mtime says when it was *written*, not how old the data inside it is.
Those are different questions and only the second one matters. The Connecticut
migration rewrote ten files in an afternoon and every one then reported "0 days
old" while carrying data from 2022 and 2023 — a column that calls a 2017 power
inventory fresh is worse than no column.

Vintage is now read from the data, in this order, and the UI says which was used
(hover the value):

| | Source |
|---|---|
| 1 | a sidecar `<stem>_meta.json` — `county_features`, which cannot hold `_meta` inside it |
| 2 | a `_meta` block in the file — `"Period 202502"`, `"Census ACS 2024 5-year"` |
| 3 | a per-record field named by the registry's `vintage_field` — `"CBP2023"`, `"USDA NASS 2022"` |
| 4 | the source `Last-Modified` recorded in `state/last_run.json` |
| 5 | **derived files inherit the oldest vintage they are derived FROM** |
| 6 | file mtime, labelled *unknown* rather than quoted as if it meant something |

Rule 5 matters: `us_catchment` rebuilt today from 2025 populations is 2025 data,
and reporting "0 days" would be the original bug in a new place. `ca_cma`
correctly inherits its 370-day staleness from `ca_csi_2025.json`.

Parsing is anchored on **digit** boundaries, not word boundaries — `CBP2023` has
no word boundary between the `P` and the `2`. A bare year resolves to 31
December, because data labelled 2023 describes 2023 and is published later.

### "stale" and "newest" are different claims

Judging on real vintage immediately flagged six datasets, and three of them
could not be improved by refreshing — ACS 2024 describes a period ending 609 days
ago but is the newest vintage that exists. Calling that "stale" invites a rebuild
that re-fetches the same year forever.

So when the last successful rebuild already landed on the vintage the file has,
the status is **newest**: past its publication cycle, but the source has nothing
better. "Refresh what is stale" skips those, and the freshness gate reports
*"the last rebuild produced this same vintage — the source has nothing newer"*.

### What it found

Three ACS/CBP builders pinned their year, so a staleness flag could never be
resolved — they would re-fetch the same vintage on every run. They now probe for
the newest published vintage, which moved `county_workforce` and
`county_occupation` from **ACS 2023 to ACS 2024**. `county_innovation` stays on
CBP 2023 because that is genuinely the latest, and now says so.

`ca_crime` was the last genuinely stale dataset and is now fixed (below).
Current state: **33 ok, 4 newest, 0 stale**.

### ca_crime — and why a URL can be the wrong kind of address

`ca_crime` pointed at a csv inside a StatCan *Daily* article,
`/daily-quotidien/250722/t004a-eng.csv`. The obvious fix was to bump the date to
the 2026 release. **That would have loaded Consumer Price Index figures into the
crime file** — the Daily names its attachments POSITIONALLY, so `t004a` means
only "table 4 of that day's release", and 2026-07-20's table 4 is inflation.
Nothing about the URL says what it contains.

It now reads StatCan table **35-10-0026** instead: a stable annual URL, addressed
by what it *is* rather than where it appeared, which the builder unzips itself.
The Daily format is still auto-detected if a file in that shape is passed, since
it carries a crime-rate column the statistical table does not (nothing scores on
that column — the scorer reads only `csi`).

Two things the move surfaced:

- The reference year advanced **2024 → 2025**, with real movement: Regina −12.9,
  Winnipeg −11.0, Moncton +11.8, Saint John +11.6.
- **Ottawa and Gatineau were being dropped.** Ottawa-Gatineau is Canada's only
  interprovincial CMA and the table lists it three ways — an Ontario part, a
  Quebec part, and a combined row. The parts now map to their separate census
  divisions and the combined row is discarded, which would otherwise double-count
  the capital region. Coverage went 38 → 40 CDs.

The builder writes its own vintage marker (`reference year 2025`) so staleness is
judged on the crime year rather than on when StatCan happened to publish.

### The Canadian block

The Canadian side rested on the 2021 Census. Most of it had already been
overlaid by current sources — participation and unemployment from the Labour
Force Survey, growth from the population estimates, livability from CIMD,
occupation from the Census occupation table, infrastructure from ODI. Checking
field by field rather than assuming, what was still running on raw 2021 values
came down to **five fields and the non-metro crime index**.

**Population was the big one.** `ca_features.json` held the 2021 Census count:
36.99M against StatCan's ~41.5M for 2025. The Canadian model was scoring a
country **4.7 million people short**, and unevenly — Toronto CD understated by
17%, Westmorland NB by 24%, one division by 94%. `TOTPOP_CY` drives market_size
and the staffability sufficiency ratios, so that distorted rankings, not just
labels.

`build_ca_features.py` fixes it from `ca_popgrowth.json`, which already carries a
current `pop` per census division — no new download, and the two files cannot
disagree about the same population. It also scales `POPDENS_CY` (land area is
constant) and `labour_force` (19.31M → 21.77M, against Canada's actual ~22.3M),
keeping the level consistent with the LFS *rates* already overlaid rather than
leaving a 2021 count beside 2026 rates.

**Non-metro crime.** 253 census divisions still took their CSI from
`ca_regional.json`. That looked like a granularity problem until I checked: those
253 CDs shared **13 distinct values** — one per province. So refreshing them to
the current provincial CSI from the table `build_ca_crime.py` already downloads
loses nothing and gains four years. Those entries are marked
`basis: "provincial"` so a stand-in is never read as a measured metro figure.
CSI coverage is now 293/293, all reference year 2025.

**What cannot be fixed, and why it was checked rather than assumed.** `income`,
`dwelling_value` and `bachelor_share` stay on 2021 Census values. StatCan does
publish annual income — tables 11-10-0005, 11-10-0012, 11-10-0135, 11-10-0190 —
but every one stops at the province or the CMA: their DGUIDs carry schema `0002`
(province) and `S0503` (CMA), never `0003` (census division). Dwelling value and
attainment by census division are Census products outright. These move when the
2026 Census releases in 2027-28, and not before.

Provenance goes to `ca_features_meta.json`, not inside the file — `ca_features.json`
IS the Canadian feature table and a `_meta` key would become a phantom 294th
census division. Same lesson as `county_features`.

### us_places, and the encoding bug it uncovered

`build_us_places.py` rebuilds the market-proximity gazetteer from the Census
Gazetteer place and cousubs files. Coverage went **64,003 → 102,242** keys: both
the published Census name and the name without its type word are indexed, so
"Detroit" and "Detroit city" both resolve.

Three things worth recording:

- **The 2025 vintage switched from tab- to pipe-delimited** and added a column.
  Assuming the old format produced an empty file — silently, until the record
  floor rejected it. The parser now detects the delimiter and indexes columns by
  name.
- **Strip the type suffix once, not repeatedly.** Looping turned "Arctic Village
  CDP" into "Arctic" and "Mountain Village city" into "Mountain". That cost
  1,003 places before it showed up in the comparison.
- Of 198 keys that disappeared, **83 were encoding-damaged in the old file** —
  `CA|lacaaadaflintridge`, with "aaa" where the ñ of La Cañada should be. Those
  lookups could never have matched what a user typed. 65 are genuine Census
  renames.

That third point led somewhere bigger. `scorer._load` opened every file with
`json.load(open(path))` — **no encoding**, so it used the platform default,
cp1252 on Windows. Four runtime files already contain non-ASCII bytes, so
Montréal and Memphrémagog had been decoding to mojibake all along, and the new
gazetteer contained a byte cp1252 has no mapping for, which raised outright.
That silent corruption is the likely origin of `lacaaadaflintridge` in the first
place: a name normalised *after* being mis-decoded. `_load` now specifies
`encoding="utf-8"`. All 39 tests still pass, and La Cañada Flintridge, Mountain
Village and Arctic Village now resolve where they previously could not.

### What is left, and why

Eleven files still have no builder, and they are not one category:

| | |
|---|---|
| **Curated by hand** — a builder would be fabrication | `county_groundwater` (29 aquifer hotspots), `ca_water_stress` (7, each with a source note), `ca_innovation` (13 provincial inputs) |
| **Superseded** — every field now overlaid by a current source, kept only for `ca_infra_pts` as a coarse fallback | `ca_regional` |
| **Not a scorer input at all** — verified, zero references in `scorer.py` | `metrics_extra` (1.2 MB build input, not runtime data) |
| **Owned by another tool** | `organizations` (the editor on port 5002) |
| **Census-only until 2027-28** | `ca_socio`'s income, dwelling_value, bachelor_share |
| **Investigated and deliberately not built** | `county_groundwater_trend` — see below |
| **Static and verified** | `ca_cd_centroids` — 293/293 census divisions, every point inside Canada, all 13 provinces. 2021 CD geometry does not move until boundaries are redrawn, and no live source would improve it, so a builder would add provenance and no currency. |

### fips_to_msa

Rebuilt from the Census CBSA delineation file, List 1, newest year probed.
Metropolitan only — a micropolitan area is a town of 10,000–50,000, and calling
that a "metro" in a result would overstate the market (`--include-micro` adds
them).

The labels were several years stale. The 2023 OMB revision **relabelled 289
counties** (Birmingham-Hoover → Birmingham, Anniston-Oxford-Jacksonville →
Anniston-Oxford) and moved **29 out of metro status entirely** — Pine Bluff, AR
is now micropolitan. 73 counties gained a label, 58 lost one, all legitimate
reclassifications. This is presentation only; nothing scores on it, but a wrong
metro label makes a correct match look wrong.

### county_groundwater_trend — investigated, deliberately not built

Worth recording so nobody repeats the work.

The source is **USGS data release v4.0** — a static, versioned research product,
not a feed. A builder could not keep it current, which was the entire point of
having one.

It is also not reproducible without guesswork. The stored file holds 21,105
wells across 685 counties, and that matches **no** natural filter of the 834 MB
long-format trends csv: the closest slice, `annual/mean/water/sub_period` with
`longest_record`, gives 88,442 sites, and the next gives 12,620. It matches
neither well list either (NGWMN 17,006, NWIS 914,280). Pinning the original's
filter means guessing among hundreds of trend-type / metric / year-type /
period / aquifer combinations.

And neither file carries a county code, so assignment needs spatial work with no
boundary file. Nearest-centroid would misassign exactly in the large western
counties where groundwater decline actually matters.

A builder writing different numbers under the same filename would look like a
refresh while quietly changing a screen input. Better to leave it and say why.
Revisit if USGS publishes v5.

### ca_places — broken in production only

This is the one worth reading about, because it could not be seen from a
developer machine.

`scorer.geocode_place` tries the bundled Canadian table and, when a name is not
in it, falls through to Nominatim — which the code's own comment says is
**blocked on the host**. So Canadian market proximity worked for exactly the
names in the file and silently failed for everything else in production, while
every test lookup on a laptop succeeded because Nominatim answered. That is why
it survived: the fallback masked the gap precisely where you would test it.

The old file held 590 keys, and 280 of those were not places at all — they were
census-division centroids filed under the division's name ("Division No. 1").
Roughly 155 real Canadian towns were bundled.

`build_ca_places.py` rebuilds it from NRCan's Canadian Geographical Names
service: **590 → 30,202 keys**, all 590 original keys preserved. Squamish,
Whistler, Timmins, Yorkton and Quebec City now resolve from the bundle, to within
a kilometre, with no external call.

Three traps, all found by checking rather than assuming:

- **The service ignores `start`.** Requesting offsets 0, 1000 and 2000 returns
  the same rows — 960 of 1000 identical — and caps a result set near 1000.
  Paging it produced 61,000 rows that collapsed to 8,634 unique keys. It is
  queried **per province** instead, which keeps every result set under the cap.
- **`MUN1` is not optional.** British Columbia incorporates much of itself as
  district municipalities, so Squamish, Whistler and Langley are `MUN1` rather
  than `CITY` or `TOWN`, and were missing from the first build. Checking one
  known town against the output caught it.
- **A blanket carry-forward compounds.** Copying every key the previous file had
  made the output depend on its own history: 30,202 became 31,295 rebuilding
  from an identical source. It is now an explicit two-entry alias list
  ("Quebec City" → the official "Québec").

---

## When a download URL dies

**Research sources** runs a Brave search for the dataset, hands the results to
Claude, and asks which one is the actual download. Claude answers with a URL, a
confidence, and one sentence of reasoning; it is told never to invent a URL
that is not in the results, so "no direct file, here is the landing page" is a
valid and common answer.

The tool then fetches the first kilobyte of any proposed URL to check it really
serves bytes, and shows you the result. **Nothing is saved until you click
"Use this URL"** on the proposal card, which writes it to `url_overrides` in
`refresh_config.json`. The registry stays a readable description of the data;
the config holds the things that rot.

Approving a URL for a manual-download dataset also promotes it to **Auto
download** — that is the point, and it is how `ca_crime` and `ca_labour` stopped
needing a staged file.

**An approved URL is a claim, not a fact.** Claude picks the most plausible link
in the search results; it cannot tell a 2021 dataset from the 2016 edition of
the same series sitting at a near-identical URL. That is exactly what happened
to `ca_livability`. Approve, then run a forced refresh of that one dataset and
read the `keys usable` figure in the log before trusting it — the coverage check
will reject an outright mismatch, but a *plausible* wrong vintage is worth your
own eye. If a proposal turns out bad, delete its entry from `url_overrides` and
the dataset reverts to whatever the registry says.

---

## Command line

Everything the UI does works headless.

```bash
python refresh_engine.py list
python refresh_engine.py check all
python refresh_engine.py refresh auto
python refresh_engine.py refresh ca_labour county_bea
python refresh_engine.py refresh county_bea --force
python refresh_engine.py research county_nri
```

`auto` means everything automatable, `stale` everything automatable that is not
healthy, `all` every registered dataset.

---

## Files

| File | What it is |
|---|---|
| `refresh_registry.py` | One entry per runtime JSON the scorer reads: its builder, source, floor, key space, cadence and dependants. **Edit this when the scorer starts reading a new file** — nothing else knows about it. |
| `refresh_engine.py` | Acquire, build, validate, roll back, research. Also the CLI. |
| `refresh_web.py` | The local UI. |
| `refresh_config.json` | Keys and paths. Not part of the published site. |
| `staging/` | Downloaded and manually placed sources. |
| `state/last_run.json` | Per-dataset outcome and source signature from the last run. |
| `../_data_backups/` | Pre-refresh copies. |

---

## Two things to know

**Staging cleanup.** After a successful build the tool deletes what it
extracted from an archive — `ca_occupation` unpacks to 3.3 GB — and keeps the
archive itself only while it is under 100 MB, so a rebuild is not always a
re-download. Files you staged by hand are never touched.

**TLS.** This machine runs Norton, which intercepts HTTPS and presents its own
root CA that `certifi` does not carry. Every python HTTPS call fails until that
root is merged into the bundle. The engine reuses the merged bundle the
Incentives tool already built, and falls back to merging its own.
