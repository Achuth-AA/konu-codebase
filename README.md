# konu-codebase

Crawlers for 20 Hyderabad / Telangana real-estate data sources, and the code that
combines everything they collect into **one CSV file**.

The sources are government registers (TS-RERA, HMDA, GHMC, IGRS, Bhu Bharati), the
major listing portals, and classifieds sites. Every crawler yields the same
normalised `Record` (price in INR, area in sq ft, BHK, locality, lat/long, poster,
RERA id, and so on) and keeps the untouched source payload in `raw`.

## Quick start

```bash
pip install -r requirements.txt          # requests, bs4, lxml (+ playwright for Quikr)
playwright install chromium              # only needed for Quikr / --render

python -m konu_crawlers list                                       # sources and --type values
python -m konu_crawlers crawl magicbricks --type rent --max-pages 5 --out data/mb_rent.jsonl
python -m konu_crawlers crawl hmda --type layouts --out data/hmda_layouts.jsonl
python -m konu_crawlers crawl all --max-pages 3 --out "data/{source}.jsonl"   # every source

python -m konu_crawlers export "data/*.jsonl" --out data/hyderabad_records.csv  # combine into one CSV
```

```python
from konu_crawlers import get_crawler
for rec in get_crawler("squareyards").crawl(city="hyderabad", transaction="sale", max_pages=2):
    print(rec.kind, rec.title, rec.price, rec.locality)
```

## The combined CSV: every record, one file

Every record from every source goes into the same CSV, one row per record. That
includes listings for sale and rent, projects, agents and builders, HMDA layouts and
building permissions, GHMC permissions, IGRS market values, and Bhu Bharati land
records. The `Record_Type` column says what each row is.

There are two ways to build it:

* **`export`** builds the CSV once from the JSONL files written by `crawl`. Records are
  de-duplicated by the site's own id, or by URL when there is no id. For listings it
  also fetches the detail page for facts that search pages lack (floor, facing,
  balconies, pincode, …). Those pages are cached in `data/details_cache`; use
  `--no-details` to skip them.
* **`schedule`** runs forever (config: `config/schedule.json`). Each source/type is a
  job with its own interval and a hard timeout. Results are upserted into a SQLite store
  (`data/konu.db`), new listings get their detail pages, and
  `data/hyderabad_records.csv` is rewritten atomically after each cycle. Sites that
  block us are retried after 12 h instead of every cycle. `--once` runs every due job
  once and exits. `ingest` loads existing JSONL files into the same store.

Columns, in order:

* **Analysis columns** (filled mainly for listings): `URL, YearMonth, Year, Month,
  Price_INR, Price_Cr, PricePerSqft, BHK, Area_Sqft, Bathrooms, Balconies, Car_Parking,
  Floor_Number, Total_Floors, Property_Age_Years, Age_Group, Locality, Pincode, Society,
  Furnishing, Facing, Ownership, Overlooking, Transaction, Construction_Status,
  Cement_Price_Rs_per_bag_50kg, Steel_TMT_Price_Rs_per_tonne, Latitude, Longitude,
  Full_Address`
* **Record columns** (every kind): `Source, Record_Type, Source_ID, Title, Property_Type,
  Project_Name, Builder, Posted_By, Contact_Name, RERA_ID, Price_Max, Price_Text,
  Area_Text, City, Posted_At, Description, Image_URL, Other_Details, Scraped_At`

`Other_Details` keeps the rest of a government-register or RERA table row as
`column: value | …`, so nothing those registers publish is lost. Cement and steel
prices are not on any property site. They come from `config/materials.csv` (`export
--materials`) or from flat `--cement-price` / `--steel-price` values.

## Diagnosing a site: `doctor`

```bash
python -m konu_crawlers doctor                  # every source, one page of each --type
python -m konu_crawlers doctor 99acres housing  # only some
```

`doctor` saves every raw response each crawler received, the parsed records, and a
`report.json`, then zips the folder. Run it from the network you will crawl from: sites
that block non-Indian IPs can then be fixed against their real pages.

## Sources

| # | Source | `--type` | How it gets the data | Live check, 2026-10-24* |
|---|--------|----------|----------------------|------------------------------|
| 1 | **TS-RERA** `ts_rera` | projects, agents | `rerait.telangana.gov.in/SearchList/Search` form; operator types the captcha | form + captcha reached; results need a human |
| 2 | **HMDA** `hmda` | layouts, permissions | `masterplan.hmda.gov.in/planning/{LO,ExtLO,Bpermission,ExtBP}.aspx` registers; `--opt year=2024` | ✅ 2,238 layouts, 4,869 permissions |
| 3 | **GHMC** `ghmc` | permissions | discovers permission-register pages from ghmc.gov.in, dumps their tables | ⛔ F5 WAF (non-Indian IP) |
| 4 | **IGRS Telangana** `igrs` | market_values, statistics | discovers market-value / statistics pages; form fields via `--opt form_…` | ⛔ connection reset (non-Indian IP) |
| 5 | **Bhu Bharati** `bhubharati` | land_records, prohibited | discovers lookup pages; district/mandal/village/survey via `--opt form_…`; operator types captcha | ⛔ connection reset (non-Indian IP) |
| 6 | **NoBroker** `nobroker` | sale, rent | sale: `window.nb.appState` JSON; rent: SSR cards; walks locality pages | ✅ |
| 7 | **MagicBricks** `magicbricks` | sale, rent, projects | `window.SERVER_PRELOADED_STATE_` (30/page, `?page=N`) | ✅ rent; sale/projects show a maintenance wall to non-Indian IPs |
| 8 | **99acres** `99acres` | sale, rent, projects, agents | `window.__initialData__` (`-ffid-page-N`) | ⛔ Akamai |
| 9 | **Housing.com** `housing` | sale, rent, projects | `window.__INITIAL_STATE__` + JSON-LD | ⛔ Akamai |
| 10 | **Square Yards** `squareyards` | sale, rent, projects | card `data-*` attributes merged with JSON-LD | ✅ |
| 11 | **Makaan** `makaan` | sale, rent | now 301-redirects to Housing.com → delegates to the Housing crawler | ⛔ (via Housing.com) |
| 12 | **PropTiger** `proptiger` | projects | `text/x-config` JSON per project; snowballs through project pages | ✅ |
| 13 | **RoofandFloor** `roofandfloor` | sale, rent, projects | SSR cards + the site's own `fetch_pages.php` "load more" fragments | ✅ |
| 14 | **IndiaProperty** `indiaproperty` | sale, rent | JSON-LD → card heuristics | ⛔ CloudFront |
| 15 | **CommonFloor** `commonfloor` | sale, rent | JSON → JSON-LD → card heuristics | ⛔ Akamai |
| 16 | **OLX** `olx` | sale, rent | `window.__APP` ad objects; `--opt location_id=` for other cities | ⛔ Akamai |
| 17 | **Quikr Homes** `quikr` | sale, rent | headless Chromium; JSON-LD merged with cards | ✅ |
| 18 | **Sulekha Property** `sulekha` | sale, rent, latest, agents | ad cards / JSON-LD; `latest` = city landing page | ✅ `latest`; search pages geo-redirect to /403 |
| 19 | **JustDial** `justdial` | agents, builders | JSON-LD `RealEstateAgent`/`LocalBusiness` or `__NEXT_DATA__` | ⛔ Akamai |
| 20 | **RealEstateIndia** `realestateindia` | sale, rent, projects, agents | SSR cards + the site's own "View More Results" POST (`property_fetch_results.php`); agents first page only (load-more disallowed by robots.txt) | ✅ |

\* Checked on 2026-10-24 from a US cloud IP. "⛔" means the **site** refused that IP
(Akamai / F5 / CloudFront / TCP reset), not a crawler error. Those crawlers are built
from each site's published page structure and are covered by tests with
schema-shaped pages, but have not been exercised against the live site yet:
run them from an Indian network (or `--proxy http://user:pass@in-proxy:port`)
and check the first page before scheduling them. The CLI reports blocks
explicitly (`BLOCKED: … Akamai 'Access Denied'`) instead of writing empty files.

## Behaviour and etiquette

* **robots.txt is honoured** by default (e.g. NoBroker's `/api/*` is disallowed, so the
  crawler reads the server-rendered pages instead). `--ignore-robots` exists for sites you
  have permission to crawl.
* **Rate-limited**: one request per host every 1.5 s (`--delay`), retries with back-off on
  429/5xx, and a single retry on connection resets.
* **Captchas are never solved automatically.** TS-RERA, IGRS and Bhu Bharati save the
  captcha image to a temp file and ask the operator to type it (or take `--captcha TEXT`).
  In non-interactive runs (`KONU_NONINTERACTIVE=1`, cron) they stop with `CAPTCHA: …`.
* **No bot-protection bypassing.** No fingerprint spoofing, UA rotation or challenge
  solving; geo-blocked sites need a legitimate Indian network path.
* TLS: `rerait.telangana.gov.in` and `*.hmda.gov.in` serve an incomplete certificate
  chain. The missing emSign intermediate (+ root) ships in `konu_crawlers/certs/` and is
  appended to the normal trust store, so verification stays on.

## Layout

```
konu_crawlers/
  sources/       one crawler per website (+ _common mappers, _portal base classes)
  http.py        Fetcher: retries, rate limit, robots.txt, block detection, Playwright render
  extract.py     embedded-JSON harvester, JSON-LD, card finder, table/form helpers, INR parsing
  models.py      Record dataclass
  details.py     listing detail pages → extra facts (floor, facing, pincode, ...)
  schema.py      Record (+ details) → one row of the combined CSV
  export.py      JSONL files → the combined CSV
  scheduler.py   continuous crawling into SQLite + the combined CSV
  doctor.py      diagnostic dump of what each site returns
  captcha.py     human-in-the-loop captcha prompt
  cli.py         `python -m konu_crawlers`
config/          schedule.json (jobs, intervals, timeouts), materials.csv (cement / steel prices)
tests/           parser tests on pages captured 2026-10-24, flow, CSV and scheduler tests
```

Run the tests with `python -m pytest`.
