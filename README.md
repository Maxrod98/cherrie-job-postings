# Job Scout

Every morning, GitHub runs `scout.py`. It reads the public job feeds of about 50 large companies (Greenhouse, Lever and Ashby), keeps early-career product, program, product-ops and business-ops roles in the Bay Area, New York and Southern California posted in the last 3 days, and rebuilds a phone-friendly page of listings.

No servers, no accounts for her, no cost. Only the Python standard library is used.

## Set it up (about 10 minutes)

1. **Create a repo** on GitHub, for example `job-listings`. Make it **public**: free GitHub Pages needs a public repo. The listings are public job posts anyway, and the page asks search engines not to index it.
2. **Add these files** to the repo, keeping the folders: `scout.py`, `companies.json`, `README.md`, `.github/workflows/scout.yml`. Either `git push`, or on github.com use **Add file → Upload files** and drag the whole folder in.
3. **Let the workflow save results:** Settings → Actions → General → Workflow permissions → **Read and write permissions** → Save.
4. **First run:** Actions tab → **Daily job scout** → **Run workflow**, and set days to `14` so the first page has a full list. It takes about a minute.
5. **Turn on the page:** Settings → Pages → Source: **Deploy from a branch** → Branch `main`, folder **/docs** → Save. After a minute or two the page is live at
   `https://<your-github-username>.github.io/job-listings/`
6. **Send her the link.** On iPhone: open it in Safari → Share → **Add to Home Screen**. On Android: Chrome menu → **Add to Home screen**. It then opens like an app.

From then on it runs every day at about 7 AM Pacific. Run it any time from the Actions tab.

## Change what it looks for

- **Companies:** edit `companies.json`. Only add companies with 500+ employees. The run log (Actions tab → latest run → "Run the scout") lists companies it couldn't find; most of those use Workday or their own careers site, which this script doesn't read.
- **Titles, cities, years:** edit `TRACKS`, `REGIONS`, `EXCLUDE_TITLE` near the top of `scout.py`.
- **How long a job stays on the page:** `--keep-days` (default 30).

## Run it on your own computer

```
python3 scout.py --days 7 --verbose
```

`--verbose` shows why near-misses were skipped. Results go to `new_jobs.json` (for the shared Claude tracker), `new_jobs.csv` and `docs/index.html`.

## Files

| File | What it is |
| --- | --- |
| `scout.py` | The Scout: fetch feeds, filter, dedup, build the page |
| `companies.json` | Companies to check |
| `jobs.json` | Every match so far (created by the first run) |
| `docs/index.html` | The listings page (created by the first run) |
| `test_scout.py` | Offline test with fake feeds: `python3 test_scout.py` |
