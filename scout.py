#!/usr/bin/env python3
"""
Job Scout: finds fresh early-career PM / program / ops roles at large companies.

Reads the official public job-board feeds that companies publish through
Greenhouse, Lever and Ashby (no scraping, no login, no LinkedIn), applies the
search filters, and writes NEW jobs to new_jobs.json and new_jobs.csv.

Needs only Python 3.9+ (standard library). Run:
    python3 scout.py                 # last 3 days
    python3 scout.py --days 7        # wider window, e.g. for a first run
    python3 scout.py --region "Bay Area"

Files it uses, all in this folder:
    companies.json   which companies to check (edit freely)
    jobs.json        every match so far, keyed by company--title--city (dedup)
    web_jobs.json    roles found by the scheduled web-search Scouts (merged in)
    new_jobs.json    this run's new jobs, in the tracker's format
    new_jobs.csv     the same, for a spreadsheet
    docs/index.html  the phone-friendly listings page (served by GitHub Pages)
"""
import argparse
import csv
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
USER_AGENT = "job-scout/1.0 (personal job search)"

# ---------------------------------------------------------------- filters ---

REGIONS = {
    "Bay Area": [
        "san francisco", "south san francisco", "oakland", "berkeley", "emeryville",
        "san mateo", "redwood city", "menlo park", "palo alto", "mountain view",
        "sunnyvale", "santa clara", "san jose", "cupertino", "foster city",
        "burlingame", "san bruno", "fremont", "milpitas", "bay area", "sf, ca",
    ],
    "New York": [
        "new york", "nyc", "brooklyn", "manhattan", "queens", "jersey city", "hoboken",
    ],
    "Southern California": [
        "los angeles", "santa monica", "culver city", "playa vista", "venice, ca",
        "pasadena", "glendale", "burbank", "el segundo", "torrance", "long beach",
        "hawthorne", "irvine", "costa mesa", "newport beach", "santa ana",
        "anaheim", "orange county",
    ],
}

# (track, pattern) — first match wins, so more specific patterns come first.
TRACKS = [
    ("C", r"product operations|product ops|implementation (specialist|consultant|manager|associate)"),
    ("B", r"associate product manager|\bapm\b|product manager|product owner"),
    ("A", r"program manager|project manager|program specialist|project specialist"),
    ("D", r"project coordinator|program coordinator|\bpmo\b|business operations|bizops|biz ops|strategy (and|&) operations|business analyst|operations analyst|rotational"),
]

# She is not technical: drop roles whose title points at engineering, data or
# hardware work, and feed roles whose description asks for coding skills or a
# technical background. Edit these two lists to loosen or tighten the filter.
TECHNICAL_TITLE = re.compile(
    r"\b(technical|tpm|engineer\w*|developer|software|hardware|firmware|silicon|"
    r"infrastructure|devops|security|network\w*|cloud|data|analytics|machine learning|ml|"
    r"ai research|robotics|autonom\w*|machine vision|manufacturing|commissioning|"
    r"lab|api\w*|sdk|systems|it)\b",
    re.I,
)
TECHNICAL_REQ = re.compile(
    r"technical background|engineering background|strong technical|deeply technical|"
    r"technical degree|write code|writing code|coding (experience|skills)|programming (experience|skills)|"
    r"proficien\w* (in|with) (python|java|sql|javascript)|experience (in|with) python|"
    r"software (development|engineering) experience|experience as an? (software )?engineer|"
    r"former (software )?engineer|hands-on technical",
    re.I,
)


def is_technical(title: str, description: str = "") -> bool:
    return bool(TECHNICAL_TITLE.search(title or "") or TECHNICAL_REQ.search(description or ""))


EXCLUDE_TITLE = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|director|head of|vp|vice president|chief|"
    r"group product|manager of|intern|internship|co-?op|iii|iv|v)\b",
    re.I,
)

YEARS_RE = re.compile(
    r"(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*\d{1,2}\s*\+?\s*)?years?(?:'|’)?\s*(?:of\s+)?"
    r"(?:[a-z/&,\- ]{0,60}?)experience",
    re.I,
)


def region_of(location: str):
    loc = (location or "").lower()
    for region, cities in REGIONS.items():
        if any(c in loc for c in cities):
            return region
    return None


def track_of(title: str):
    t = title.lower()
    for track, pattern in TRACKS:
        if re.search(pattern, t):
            return track
    return None


def min_years(text: str):
    """Smallest 'N years ... experience' figure in the description, or None."""
    nums = [int(m.group(1)) for m in YEARS_RE.finditer(text or "")]
    nums = [n for n in nums if 0 < n < 25]
    return min(nums) if nums else None


def strip_html(s: str) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s)


def slug(*parts) -> str:
    """The tracker's dedup key: company--title--city, lowercase a-z0-9 and hyphens."""
    out = []
    for p in parts:
        p = re.sub(r"[^a-z0-9]+", "-", (p or "").lower()).strip("-")
        out.append(p)
    return "--".join(out)[:180]


def city_of(location: str, region: str) -> str:
    loc = (location or "").lower()
    for c in REGIONS.get(region, []):
        if c in loc:
            return c.replace(", ca", "").strip()
    return region.lower()


def parse_date(value):
    """ISO string or epoch milliseconds -> aware datetime, or None."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        v = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(v)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ------------------------------------------------------------------ feeds ---

def get_json(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_greenhouse(token):
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
    for j in data.get("jobs", []):
        yield {
            "title": j.get("title", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
            "posted": parse_date(j.get("first_published") or j.get("updated_at")),
            "description": strip_html(j.get("content", "")),
        }


def fetch_lever(token):
    data = get_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
    for j in data:
        cats = j.get("categories") or {}
        locs = cats.get("allLocations") or [cats.get("location", "")]
        yield {
            "title": j.get("text", ""),
            "location": "; ".join(l for l in locs if l),
            "url": j.get("hostedUrl", ""),
            "posted": parse_date(j.get("createdAt")),
            "description": j.get("descriptionPlain", "") + " " + " ".join(
                strip_html(l.get("content", "")) for l in j.get("lists", [])),
            "workplace": j.get("workplaceType", ""),
        }


def fetch_ashby(token):
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true")
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location", "")] + [a.get("location", "") for a in j.get("secondaryLocations", []) or []]
        comp = (j.get("compensation") or {}).get("compensationTierSummary", "")
        yield {
            "title": j.get("title", ""),
            "location": "; ".join(l for l in locs if l),
            "url": j.get("jobUrl", ""),
            "posted": parse_date(j.get("publishedAt")),
            "description": j.get("descriptionPlain", "") or strip_html(j.get("descriptionHtml", "")),
            "salary": comp or "",
            "workplace": "remote" if j.get("isRemote") and not j.get("location") else (j.get("workplaceType") or ""),
        }


FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby}


def load_company(company):
    """Returns (company, ats_used, jobs list, error). Tries each ATS if none given."""
    tokens = company.get("tokens") or [company["name"].lower().replace(" ", "")]
    systems = [company["ats"]] if company.get("ats") else ["greenhouse", "lever", "ashby"]
    last_err = "no job board found"
    for ats in systems:
        for token in tokens:
            try:
                jobs = list(FETCHERS[ats](token))
                return company, f"{ats}:{token}", jobs, None
            except urllib.error.HTTPError as e:
                last_err = f"{ats}:{token} HTTP {e.code}"
            except Exception as e:  # network error, bad JSON, timeout
                last_err = f"{ats}:{token} {type(e).__name__}"
            time.sleep(0.2)
    return company, None, [], last_err


# ------------------------------------------------------------------- page ---

TRACK_NAMES = {"A": "Program / Project", "B": "Product", "C": "Product-adjacent", "D": "Business ops"}

PAGE_CSS = """
:root{--bg:#f6f7f5;--card:#fff;--fg:#1d2622;--muted:#5c6862;--line:#dfe3df;--accent:#1f6b52;--soft:#e3efe9;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#121715;--card:#1a201d;--fg:#e5ebe7;--muted:#9aa69f;--line:#2c3530;--accent:#5fc39c;--soft:#1f3129;color-scheme:dark}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:720px;margin:0 auto;padding:20px max(16px,env(safe-area-inset-left)) 48px max(16px,env(safe-area-inset-right))}
h1{font-size:24px;margin:0 0 4px}
.sub{color:var(--muted);margin:0 0 16px;font-size:14px}
nav{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 20px}
nav a{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:6px 12px;color:var(--fg);text-decoration:none;font-size:14px}
h2{font-size:18px;margin:28px 0 10px;padding-top:8px;border-top:1px solid var(--line)}
h2 span{color:var(--muted);font-weight:400}
ul{list-style:none;margin:0;padding:0;display:grid;gap:10px}
li{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
li a.t{color:var(--fg);font-weight:600;font-size:17px;text-decoration:none;display:block}
li a.t:hover,li a.t:focus{color:var(--accent);text-decoration:underline}
.co{color:var(--muted);margin-top:2px}
.meta{display:flex;flex-wrap:wrap;gap:6px 10px;margin-top:8px;font-size:13px;color:var(--muted)}
.tag{background:var(--soft);color:var(--accent);border-radius:4px;padding:1px 6px}
.new{background:var(--accent);color:var(--card);border-radius:4px;padding:1px 6px;font-weight:600}
.empty{color:var(--muted)}
footer{margin-top:32px;color:var(--muted);font-size:13px}
"""


def render_page(jobs: dict, path: Path, today: str, keep_days: int = 3):
    esc = html.escape
    rows = sorted(jobs.values(), key=lambda d: (d["posted"], d["first_seen"]), reverse=True)
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>",
        "<meta name='robots' content='noindex'>",
        "<title>Job Listings</title>",
        f"<style>{PAGE_CSS}</style></head><body><main>",
        "<h1>Job Listings</h1>",
        f"<p class='sub'>{len(rows)} roles posted in the last {keep_days} days at 500+ employee companies, newest first. "
        f"Updated {esc(datetime.now().strftime('%b %d, %Y %I:%M %p'))}.</p>",
        "<nav>",
    ]
    regions = list(REGIONS)
    for r in regions:
        n = sum(1 for d in rows if d["region"] == r)
        parts.append(f"<a href='#{r.lower().replace(' ', '-')}'>{esc(r)} ({n})</a>")
    parts.append("</nav>")
    for r in regions:
        group = [d for d in rows if d["region"] == r]
        parts.append(f"<h2 id='{r.lower().replace(' ', '-')}'>{esc(r)} <span>{len(group)}</span></h2>")
        if not group:
            parts.append("<p class='empty'>No new roles here in the last few days.</p>")
            continue
        parts.append("<ul>")
        for d in group:
            meta = []
            if d["first_seen"] == today:
                meta.append("<span class='new'>New today</span>")
            meta.append(f"<span class='tag'>{esc(TRACK_NAMES.get(d['track'], d['track']))}</span>")
            meta.append(f"<span>Posted {esc(d['posted'])}</span>")
            meta.append(f"<span>{esc(d['location'][:60])}</span>")
            if d.get("salary"):
                meta.append(f"<span>{esc(d['salary'])}</span>")
            parts.append(
                f"<li><a class='t' href='{esc(d['url'])}' target='_blank' rel='noopener'>{esc(d['title'])}</a>"
                f"<div class='co'>{esc(d['company'])}</div><div class='meta'>{''.join(meta)}</div></li>")
        parts.append("</ul>")
    parts.append("<footer>Tap a role to open the official posting. Listings come from each company's public job board.</footer>")
    parts.append("</main></body></html>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(parts), encoding="utf-8")


# ------------------------------------------------------------------- main ---

def evaluate(company, job, cutoff, only_region):
    """Returns (doc, None) if the job passes, else (None, reason)."""
    title = job["title"].strip()
    if EXCLUDE_TITLE.search(title):
        return None, "senior or intern title"
    track = track_of(title)
    if not track:
        return None, "not a target title"
    if is_technical(title, job.get("description", "")):
        return None, "technical role"
    region = region_of(job["location"])
    if not region:
        return None, "not in a target region"
    if only_region and region != only_region:
        return None, "other region"
    if job.get("workplace", "").lower() == "remote":
        return None, "remote only"
    if not job["posted"]:
        return None, "no posted date"
    if job["posted"] < cutoff:
        return None, "older than window"
    years = min_years(job["description"])
    if years is not None and years >= 3:
        return None, f"asks for {years}+ years"
    city = city_of(job["location"], region)
    doc = {
        "company": company["name"],
        "title": title,
        "track": track,
        "region": region,
        "location": job["location"][:120],
        "url": job["url"],
        "posted": job["posted"].date().isoformat(),
        "posted_note": "from the employer's job feed",
        "salary": job.get("salary", ""),
        "experience": f"{years} years mentioned" if years is not None else "",
        "first_seen": datetime.now().date().isoformat(),
        "found_by": "Local Scout",
        "status": "new",
        "company_size": company.get("size", "500+"),
        "size_ok": "yes",
        "notes": "",
    }
    return (slug(company["name"], title, city), doc), None


def main():
    ap = argparse.ArgumentParser(description="Find fresh early-career PM / program / ops roles.")
    ap.add_argument("--days", type=int, default=3, help="posting window in days (default 3)")
    ap.add_argument("--region", choices=list(REGIONS), help="only one region")
    ap.add_argument("--companies", default=str(HERE / "companies.json"))
    ap.add_argument("--keep-days", type=int, default=3, help="show jobs posted up to this many days ago (default 3)")
    ap.add_argument("--verbose", action="store_true", help="print why each near-miss was skipped")
    args = ap.parse_args()

    companies = json.loads(Path(args.companies).read_text())["companies"]
    store_path = HERE / "jobs.json"
    store = json.loads(store_path.read_text()) if store_path.exists() else {}
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    today = datetime.now().date().isoformat()

    print(f"Checking {len(companies)} companies for roles posted since {cutoff.date()}…")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(load_company, companies))

    new, failed, total_jobs = {}, [], 0
    for company, board, jobs, err in results:
        if err:
            failed.append((company["name"], err))
            continue
        total_jobs += len(jobs)
        for job in jobs:
            hit, reason = evaluate(company, job, cutoff, args.region)
            if hit:
                key, doc = hit
                if key not in store and key not in new:
                    new[key] = doc
            elif args.verbose and reason not in ("not a target title", "not in a target region", "other region"):
                print(f"  skip  {company['name']}: {job['title']}  ({reason})")

    # Save this run's results and remember them.
    out = [{"doc_id": k, **v} for k, v in sorted(new.items(), key=lambda kv: kv[1]["posted"], reverse=True)]
    (HERE / "new_jobs.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    fields = ["doc_id", "posted", "company", "title", "track", "region", "location", "salary", "experience", "url"]
    with open(HERE / "new_jobs.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(out)
    # Merge roles the web-search Scouts found (written to web_jobs.json by the
    # scheduled Claude task). Same company--title--city keys, so no duplicates;
    # a role found by both keeps the feed version, which has the exact date.
    web_path = HERE / "web_jobs.json"
    web_added = 0
    if web_path.exists():
        try:
            web = json.loads(web_path.read_text())
        except ValueError:
            web = {}
            print("web_jobs.json could not be read; skipping it.")
        for k, v in web.items():
            if (k not in store and k not in new and v.get("posted") and v.get("url")
                    and v.get("region") in REGIONS and v.get("size_ok") == "yes"
                    and not is_technical(v.get("title", ""))):
                store[k] = v
                web_added += 1
    print(f"Web Scouts: {web_added} roles merged from web_jobs.json")

    # Remember every match, drop ones posted more than --keep-days ago, rebuild the page.
    store.update(new)
    oldest = (datetime.now(timezone.utc).date() - timedelta(days=args.keep_days)).isoformat()
    store = {k: v for k, v in store.items() if v["posted"] >= oldest and not is_technical(v["title"])}
    store_path.write_text(json.dumps(store, indent=1, sort_keys=True, ensure_ascii=False))
    render_page(store, HERE / "docs" / "index.html", today, args.keep_days)

    # Report.
    print(f"Read {total_jobs} open jobs from {len(companies) - len(failed)} companies.")
    print(f"New matches: {len(out)}  (saved to new_jobs.json and new_jobs.csv)")
    print(f"Page rebuilt with {len(store)} roles: docs/index.html\n")
    for d in out:
        print(f"  {d['posted']}  [{d['track']}] {d['company']}: {d['title']}  ({d['region']})")
        print(f"              {d['url']}")
    if failed:
        print(f"\nNo job board found for {len(failed)} companies (they may use Workday or their own site):")
        for name, err in failed:
            print(f"  {name}: {err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
