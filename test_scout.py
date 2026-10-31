"""Offline test: fakes the three job feeds and checks filters, dedup and the page."""
import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scout  # noqa: E402

now = datetime.now(timezone.utc)
iso = lambda d: (now - timedelta(days=d)).isoformat().replace("+00:00", "Z")
ms = lambda d: int((now - timedelta(days=d)).timestamp() * 1000)

GH = {"jobs": [
    {"title": "Associate Product Manager", "location": {"name": "San Francisco, CA"}, "absolute_url": "https://example.com/gh1",
     "first_published": iso(1), "content": "&lt;p&gt;0-2 years of product experience&lt;/p&gt;"},
    {"title": "Senior Product Manager", "location": {"name": "San Francisco, CA"}, "absolute_url": "https://example.com/gh2",
     "first_published": iso(1), "content": ""},
    {"title": "Program Manager", "location": {"name": "New York, NY"}, "absolute_url": "https://example.com/gh3",
     "first_published": iso(1), "content": "5+ years of program management experience"},
    {"title": "Business Operations Associate", "location": {"name": "New York, NY"}, "absolute_url": "https://example.com/gh4",
     "first_published": iso(10), "content": ""},
    {"title": "Software Engineer", "location": {"name": "San Francisco, CA"}, "absolute_url": "https://example.com/gh5",
     "first_published": iso(1), "content": ""},
    {"title": "Product Operations Analyst", "location": {"name": "Austin, TX"}, "absolute_url": "https://example.com/gh6",
     "first_published": iso(1), "content": ""},
]}
LEVER = [
    {"text": "Project Coordinator", "categories": {"location": "Irvine, CA", "allLocations": ["Irvine, CA"]},
     "hostedUrl": "https://example.com/lv1", "createdAt": ms(2), "descriptionPlain": "1+ years experience", "lists": []},
    {"text": "Product Manager", "categories": {"location": "Remote - US"}, "hostedUrl": "https://example.com/lv2",
     "createdAt": ms(1), "descriptionPlain": "", "lists": [], "workplaceType": "remote"},
]
ASHBY = {"jobs": [
    {"title": "Product Analyst", "location": "Los Angeles, CA", "jobUrl": "https://example.com/ab1", "publishedAt": iso(0),
     "descriptionPlain": "2 years of SQL experience", "isListed": True, "compensation": {"compensationTierSummary": "$90K – $110K"}},
]}


def fake_get_json(url, timeout=20):
    if "greenhouse" in url and "/gh-co/" in url:
        return GH
    if "lever" in url and "/lv-co" in url:
        return LEVER
    if "ashby" in url and "/ab-co" in url:
        return ASHBY
    raise scout.urllib.error.HTTPError(url, 404, "Not Found", None, None)


def run(tmp):
    scout.HERE = tmp
    scout.get_json = fake_get_json
    sys.argv = ["scout.py", "--companies", str(tmp / "companies.json")]
    scout.main()
    return json.loads((tmp / "new_jobs.json").read_text())


tmp = Path(tempfile.mkdtemp())
(tmp / "companies.json").write_text(json.dumps({"companies": [
    {"name": "GH Co", "tokens": ["gh-co"]},          # auto-detects Greenhouse
    {"name": "LV Co", "tokens": ["lv-co"]},          # auto-detects Lever after Greenhouse 404
    {"name": "AB Co", "tokens": ["ab-co"], "ats": "ashby"},
    {"name": "Missing Co", "tokens": ["nope"]},
]}))

first = run(tmp)
titles = sorted(d["title"] for d in first)
assert titles == ["Associate Product Manager", "Product Analyst", "Project Coordinator"], titles
by = {d["title"]: d for d in first}
assert by["Associate Product Manager"]["track"] == "B" and by["Associate Product Manager"]["region"] == "Bay Area"
assert by["Project Coordinator"]["track"] == "D" and by["Project Coordinator"]["region"] == "Southern California"
assert by["Product Analyst"]["track"] == "C" and by["Product Analyst"]["salary"] == "$90K – $110K"
assert by["Associate Product Manager"]["doc_id"] == "gh-co--associate-product-manager--san-francisco"

second = run(tmp)
assert second == [], "second run must report nothing new (dedup)"
store = json.loads((tmp / "jobs.json").read_text())
assert len(store) == 3

page = (tmp / "docs" / "index.html").read_text()
assert "Associate Product Manager" in page and "New today" in page and "<script" not in page
assert "Senior Product Manager" not in page and "Software Engineer" not in page

shutil.copy(tmp / "docs" / "index.html", HERE / "sample_page.html")
print("\nALL CHECKS PASSED")
