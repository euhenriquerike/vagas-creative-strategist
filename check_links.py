#!/usr/bin/env python3
"""check_links.py -- Verifica URLs de vagas e atualiza broken_links.json.
Detecta: HTTP 404/410, redirect para ?error=true, e frases de "job not found".
Re-verifica URLs "ok" com mais de 14 dias.
"""
import re, json, sys, time
from datetime import date, timedelta
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

SITE_DIR    = Path(__file__).parent
VAGAS_SUB   = SITE_DIR / "vagas"
VAGAS_ROOT  = SITE_DIR.parent
BROKEN_PATH = SITE_DIR / "broken_links.json"
STALE_DAYS  = 14

NOT_FOUND_PHRASES = [
    "job not found", "job posting not found", "position not found",
    "this job is no longer available", "this position has been filled",
    "this job has expired", "no longer accepting applications",
    "page not found", "vaga nao encontrada",
    "this job is no longer accepting", "this position is no longer available",
    "this role is no longer", "opening has been filled",
    "application is closed", "this posting has been closed",
    "this job listing is no longer active",
    "the job you requested was not found",
]
BROKEN_URL_PATTERNS = ["error=true", "job-not-found", "posting-not-found"]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}

def collect_urls():
    urls = set()
    for pattern in ["vagas_pm_*.md", "vagas_uiux_*.md"]:
        for root in [VAGAS_ROOT, VAGAS_SUB]:
            for f in root.glob(pattern):
                try:
                    text = f.read_bytes().rstrip(b'\x00').decode("utf-8", errors="replace")
                    urls.update(re.findall(r'\[(?:Ver vaga|Aplicar|Apply)\]\((https?://[^)]+)\)', text))
                except Exception:
                    pass
    return urls

def is_broken(url):
    if "ashbyhq.com" in url and re.search(r'[a-f0-9]{8}-[a-f0-9]{4}', url, re.I):
        return True
    if "greenhouse.io" in url:
        gh_m = re.search(r'greenhouse\.io/([^/]+)/jobs/(\d+)', url, re.I)
        if gh_m:
            company, job_id = gh_m.group(1), gh_m.group(2)
            api_url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs/{job_id}"
            try:
                req = Request(api_url, headers=HEADERS)
                resp = urlopen(req, timeout=10)
                data = json.loads(resp.read().decode())
                if not data.get("id"):
                    return True
            except HTTPError as e:
                if e.code in (404, 410, 403, 422):
                    return True
            except Exception:
                pass  # fall through to HTTP check
        else:
            return True  # company page without /jobs/ID = dead
    try:
        req = Request(url, headers=HEADERS)
        resp = urlopen(req, timeout=12)
        final_url = resp.geturl()
        if any(pat in final_url.lower() for pat in BROKEN_URL_PATTERNS):
            return True
        body = resp.read(16384).decode("utf-8", errors="replace").lower()
        return any(phrase in body for phrase in NOT_FOUND_PHRASES)
    except HTTPError as e:
        return e.code in (404, 410)
    except (URLError, Exception):
        return False

def load_data():
    if BROKEN_PATH.exists():
        try:
            raw = BROKEN_PATH.read_bytes().rstrip(b'\x00').decode("utf-8")
            return json.loads(raw)
        except Exception:
            pass
    return {"broken": [], "ok": [], "checked_at": {}}

def save_data(data):
    tmp = BROKEN_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(BROKEN_PATH)

def main():
    recheck_all = "--all" in sys.argv
    today_str   = date.today().isoformat()
    cutoff      = (date.today() - timedelta(days=STALE_DAYS)).isoformat()
    data        = load_data()
    known_broken = set(data.get("broken", []))
    known_ok     = set(data.get("ok", []))
    checked_at   = data.get("checked_at", {})
    all_urls = collect_urls()
    stale_ok = known_ok & all_urls if recheck_all else {
        u for u in known_ok if u in all_urls and checked_at.get(u, "0000-00-00") < cutoff
    }
    to_check = (all_urls - known_broken - known_ok) | stale_ok
    print(f"URLs: {len(all_urls)} total | {len(known_broken)} broken | {len(to_check)} to check")
    newly_broken = []
    for i, url in enumerate(sorted(to_check), 1):
        print(f"  [{i}/{len(to_check)}] {url[:75]}", end=" ... ", flush=True)
        broken = is_broken(url)
        if broken:
            print("QUEBRADO")
            known_broken.add(url); known_ok.discard(url)
            checked_at.pop(url, None); newly_broken.append(url)
        else:
            print("ok")
            known_ok.add(url); known_broken.discard(url)
            checked_at[url] = today_str
        time.sleep(0.3)
    checked_at = {u: v for u, v in checked_at.items() if u in all_urls}
    data["broken"]     = sorted(known_broken)
    data["ok"]         = sorted(known_ok)
    data["checked_at"] = dict(sorted(checked_at.items()))
    save_data(data)
    print(f"\nResultado: {len(newly_broken)} novos quebrados")
    for u in newly_broken:
        print("  QUEBRADO:", u)

if __name__ == "__main__":
    main()
