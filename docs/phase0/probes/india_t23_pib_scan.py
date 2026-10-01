"""PIB echo scan for India Tier 2/3 sources (Phase 0 probe).

Walks a contiguous PRID window on pib.gov.in (PressReleaseIframePage), records
posted-at timestamp, PIB office, ministry header and title, and writes a CSV.
Used to measure whether TRAI/DoT/MeitY/Power/Petroleum/Health/NITI/ECI/FinCom
items are echoed on PIB and with what lag. Polite: ~1 req/sec, single host.

Usage:
  uv run --project "Z:\Data Pipelines\events_bot" python docs/phase0/probes/india_t23_pib_scan.py START END OUT.csv
"""
import csv, re, sys, time
import httpx
from selectolax.parser import HTMLParser

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")
URL = "https://www.pib.gov.in/PressReleaseIframePage.aspx?PRID={}"
POSTED = re.compile(r"(\d{2} [A-Z]{3} \d{4} \d{1,2}:\d{2}[AP]M) by ([^<]+)")


def parse(html: str):
    m = POSTED.search(html)
    t = HTMLParser(html)
    mn = t.css_first(".MinistryNameSubhead")
    h = t.css_first("#Titleh2")
    txt = lambda n: " ".join(n.text().split()) if n else ""
    return (m.group(1) if m else "", m.group(2).strip() if m else "", txt(mn), txt(h)[:200])


def main(start: int, end: int, out: str):
    with httpx.Client(headers={"User-Agent": UA}, follow_redirects=True, timeout=30) as c, \
            open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["prid", "status", "posted_ist", "office", "ministry", "title"])
        for p in range(start, end + 1):
            try:
                r = c.get(URL.format(p))
                w.writerow([p, r.status_code, *parse(r.text)])
            except Exception as e:  # network blip: record and continue
                w.writerow([p, f"ERR {type(e).__name__}", "", "", "", ""])
            fh.flush()
            time.sleep(1.0)


if __name__ == "__main__":
    main(int(sys.argv[1]), int(sys.argv[2]), sys.argv[3])
