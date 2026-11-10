"""Fetch Google Scholar citation stats and write them to results/.

Two sources, tried in order:
  1. SerpAPI (if the SERPAPI_KEY secret is set) - reliable, free tier is 100 calls/month.
  2. A single direct request to the public profile page. Google Scholar often answers
     datacenter IPs (like GitHub Actions) with a CAPTCHA; in that case we exit with an
     error instead of hanging, and the existing data on the stats branch is left alone.
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from html import unescape

import requests

SCHOLAR_ID = os.environ["GOOGLE_SCHOLAR_ID"].strip()
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "").strip()
TIMEOUT = 30
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


def from_serpapi():
    r = requests.get(
        "https://serpapi.com/search.json",
        params={"engine": "google_scholar_author", "author_id": SCHOLAR_ID,
                "num": 100, "hl": "en", "api_key": SERPAPI_KEY},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    d = r.json()
    if "error" in d:
        raise RuntimeError(f"SerpAPI error: {d['error']}")
    table = d.get("cited_by", {}).get("table", [])
    citedby = int(table[0]["citations"]["all"])
    pubs = {}
    for a in d.get("articles", []):
        pid = a.get("citation_id")
        if pid:
            pubs[pid] = {"title": a.get("title", ""),
                         "num_citations": int((a.get("cited_by") or {}).get("value") or 0)}
    return {"name": d.get("author", {}).get("name", ""), "citedby": citedby, "publications": pubs}


def from_profile_page():
    url = f"https://scholar.google.com/citations?user={SCHOLAR_ID}&hl=en&pagesize=100"
    last = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            html = r.text
            if r.status_code == 429 or "gsc_captcha_ccl" in html or "/sorry/" in r.url:
                raise RuntimeError(f"blocked by Google Scholar (HTTP {r.status_code}, CAPTCHA)")
            r.raise_for_status()
            stats = re.findall(r'class="gsc_rsb_std">(\d+)<', html)
            if not stats:
                raise RuntimeError("could not find the citation table on the profile page")
            name = re.search(r'id="gsc_prf_in">([^<]+)<', html)
            pubs = {}
            for row in re.findall(r'<tr class="gsc_a_tr">(.*?)</tr>', html, re.S):
                pid = re.search(r"citation_for_view=([^&\"]+)", row)
                title = re.search(r'class="gsc_a_at">([^<]*)<', row)
                cites = re.search(r'class="gsc_a_ac gs_ibl">(\d*)<', row)
                if pid:
                    pubs[unescape(pid.group(1))] = {
                        "title": unescape(title.group(1)) if title else "",
                        "num_citations": int(cites.group(1) or 0) if cites else 0}
            return {"name": unescape(name.group(1)) if name else "",
                    "citedby": int(stats[0]), "publications": pubs}
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"attempt {attempt + 1}: {e}", file=sys.stderr)
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(last)


def main():
    sources = ([("SerpAPI", from_serpapi)] if SERPAPI_KEY else []) + [("profile page", from_profile_page)]
    data = None
    for label, fn in sources:
        try:
            data = fn()
            print(f"Got data from {label}")
            break
        except Exception as e:  # noqa: BLE001
            print(f"{label} failed: {e}", file=sys.stderr)
    if data is None:
        sys.exit("No source succeeded; leaving the existing citation data unchanged.")

    data["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(json.dumps({k: data[k] for k in ("name", "citedby", "updated")}, indent=2))

    os.makedirs("results", exist_ok=True)
    with open("results/gs_data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    with open("results/gs_data_shieldsio.json", "w", encoding="utf-8") as f:
        json.dump({"schemaVersion": 1, "label": "citations", "message": str(data["citedby"])},
                  f, ensure_ascii=False)


if __name__ == "__main__":
    main()
