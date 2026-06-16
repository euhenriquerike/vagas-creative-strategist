import re, json
from pathlib import Path
from datetime import date

SITE_DIR    = Path(__file__).parent
VAGAS_SUB   = SITE_DIR / "vagas"
VAGAS_ROOT  = SITE_DIR.parent
BROKEN_PATH = SITE_DIR / "broken_links.json"
CUTOFF_DATE = date(2026, 5, 28)
BROKEN_GH_COMPANIES = {"remotecom", "nearform", "techietalent"}

ASHBY_UUID = re.compile(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}', re.I)
URL_RE     = re.compile(r'\[(?:Ver vaga|Aplicar|Apply)\]\((https?://[^)]+)\)')
GH_JOB_RE  = re.compile(r'greenhouse\.io/([^/]+)/jobs/', re.I)

MANUAL_BROKEN = {
    "https://jobs.lever.co/ciandt/2b5392b0-99a1-4bb6-9e63-a076df1d5321",
    "https://jobs.lever.co/vacancies/0f4f3758-1187-4a3b-8727-d227e8ed3018",
}

def load_existing():
    if not BROKEN_PATH.exists():
        return set(), set()
    try:
        raw = BROKEN_PATH.read_bytes().rstrip(b'\x00').decode('utf-8')
        d = json.loads(raw)
        return set(d.get('broken', [])), set(d.get('ok', []))
    except Exception:
        return set(), set()

def main():
    broken, ok = load_existing()
    broken |= MANUAL_BROKEN
    ok -= MANUAL_BROKEN
    added = 0
    for folder in [VAGAS_SUB, VAGAS_ROOT]:
        for f in list(folder.glob('vagas_pm_*.md')) + list(folder.glob('vagas_uiux_*.md')):
            m = re.search(r'(\d{4}-\d{2}-\d{2})', f.name)
            try:
                fdate = date.fromisoformat(m.group(1)) if m else date.min
            except Exception:
                fdate = date.min
            try:
                text = f.read_bytes().rstrip(b'\x00').decode('utf-8', errors='replace')
            except Exception:
                continue
            for url in URL_RE.findall(text):
                url = url.strip()
                mark = False
                if 'ashbyhq.com' in url and ASHBY_UUID.search(url):
                    mark = True
                gh_m = GH_JOB_RE.search(url)
                if gh_m:
                    company = gh_m.group(1).lower()
                    if company in BROKEN_GH_COMPANIES or fdate < CUTOFF_DATE:
                        mark = True
                if mark and url not in broken:
                    broken.add(url)
                    ok.discard(url)
                    added += 1
    data = {'broken': sorted(broken), 'ok': sorted(ok - broken)}
    tmp = BROKEN_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    tmp.replace(BROKEN_PATH)
    print(f'rebuild_broken: +{added} new. Total broken={len(broken)}, ok={len(data["ok"])}')

if __name__ == '__main__':
    main()
