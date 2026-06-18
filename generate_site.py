#!/usr/bin/env python3
"""
Gerador do site de vagas PM + UI/UX — Mastercard Design System
Tipografia: Sofia Sans (substituto oficial do MarkForMC)
"""
import re, json
from datetime import datetime, timedelta, date
from pathlib import Path
from collections import defaultdict

from link_checker import BrokenCache, check_urls_parallel

_vagas_sub  = Path(__file__).parent / "vagas"
_vagas_root = Path(__file__).parent.parent
SITE_DIR    = Path(__file__).parent

# Merge PM files from both site/vagas and root; site/vagas takes precedence for same filename
def _collect_files(pattern):
    seen = {}
    for f in sorted(_vagas_root.glob(pattern)):
        seen[f.name] = f
    for f in sorted(_vagas_sub.glob(pattern)):
        seen[f.name] = f   # site/vagas overwrites root for same name
    return sorted(seen.values(), key=lambda f: f.name)

_LATAM_KEYWORDS  = {'LATAM','LATIN AMERICA','BRASIL','BRAZIL','SOUTH AMERICA',
                    'ARGENTINA','COLOMBIA','MEXICO','PERU','CHILE'}
_EUROPE_KEYWORDS = {'EUROPA','EUROPE','EUROPEAN','EMEA','UK','GERMANY','FRANCE',
                    'SPAIN','NETHERLANDS','CET','CENTRAL EUROPEAN'}

def _infer_region(role):
    """Fallback: detect region from job title when no explicit section header is present."""
    r = role.upper()
    if any(k in r for k in _LATAM_KEYWORDS):
        return "latam"
    if any(k in r for k in _EUROPE_KEYWORDS):
        return "europe"
    return "global"

def _normalize_url(url: str) -> str:
    """Normaliza http:// para https:// para URLs de ATS conhecidos."""
    if url.startswith("http://") and any(
        d in url for d in ("greenhouse.io", "lever.co", "ashbyhq.com",
                           "smartrecruiters.com", "weworkremotely.com",
                           "remotive.com", "himalayas.app")
    ):
        return "https://" + url[7:]
    return url

def parse_md_file(filepath, prefix="vagas_pm"):
    text = filepath.read_text(encoding="utf-8", errors="replace")
    m = re.search(rf'{prefix}_(\d{{4}}-\d{{2}}-\d{{2}})', filepath.name)
    if not m:
        return None
    date_str = m.group(1)
    exec_m = re.search(r'_exec(\d+)', filepath.name)
    exec_n = exec_m.group(1) if exec_m else "1"
    novas_m = re.search(r'[Nn]ovas[^\d]*(\d+)', text)
    novas   = int(novas_m.group(1)) if novas_m else 0

    jobs, current_ats, current_region = [], "Outros", None
    for line in text.splitlines():
        # Detect region from ## headings
        h2 = re.match(r'^##\s+(.+)', line)
        if h2:
            title_up = h2.group(1).upper()
            if any(k in title_up for k in _LATAM_KEYWORDS):
                current_region = "latam"
            elif any(k in title_up for k in _EUROPE_KEYWORDS):
                current_region = "europe"
            continue
        # Detect ATS from ### headings (with or without emoji prefix)
        h3 = re.match(r'^###\s+(?:[^\s]+\s+)?(.+)', line)
        if h3:
            current_ats = h3.group(1).strip()
            continue
        # Format A: | **Company** | Role | [Ver vaga](url) |
        rm = re.match(r'\|\s*\*\*(.+?)\*\*\s*\|\s*(.+?)\s*\|\s*\[Ver vaga\]\((.+?)\)', line)
        if rm:
            role = rm.group(2).strip()
            url = _normalize_url(rm.group(3).strip())
            jobs.append({"company": rm.group(1).strip(), "role": role,
                         "url": url, "ats": current_ats,
                         "date": date_str, "exec": exec_n, "file": filepath.name,
                         "region": current_region or _infer_region(role)})
            continue
        # Format B: | Company | Role | [Aplicar/Apply/Ver](url) | (no bold)
        rm2 = re.match(r'\|\s*([^|*\[\]#<>]+?)\s*\|\s*([^|]+?)\s*\|\s*\[(?:Aplicar|Apply|Ver vaga|Ver)\]\((.+?)\)', line)
        if rm2:
            co = rm2.group(1).strip()
            if co and co not in ('Empresa', 'Company', '---', ''):
                role = rm2.group(2).strip()
                url = _normalize_url(rm2.group(3).strip())
                jobs.append({"company": co, "role": role,
                             "url": url, "ats": current_ats,
                             "date": date_str, "exec": exec_n, "file": filepath.name,
                             "region": current_region or _infer_region(role)})
    return {"date": date_str, "exec": exec_n, "file": filepath.name, "novas": novas, "jobs": jobs}

runs = [r for f in _collect_files("vagas_pm_*.md") if (r := parse_md_file(f, "vagas_pm"))]
if runs: runs[-1]["is_latest"] = True

uiux_runs = [r for f in _collect_files("vagas_uiux_*.md") if (r := parse_md_file(f, "vagas_uiux"))]
if uiux_runs: uiux_runs[-1]["is_latest"] = True

_broken_path = SITE_DIR / "broken_links.json"

cache = BrokenCache(_broken_path)
for r in runs:
    r["jobs"] = [j for j in r["jobs"] if not cache.is_broken(j.get("url", ""))]
    r["novas"] = len(r["jobs"])
for r in uiux_runs:
    r["jobs"] = [j for j in r["jobs"] if not cache.is_broken(j.get("url", ""))]
    r["novas"] = len(r["jobs"])

# ── Live re-validation: HTTP-check older URLs, trust latest run ────────────────
def _validate_all_links_live(*runs_lists):
    """HTTP-check URLs from older runs. Latest run is trusted (just validated by search_and_generate.py)."""
    from datetime import date as _date
    today_str = _date.today().isoformat()

    trusted_urls = set()   # URLs from today's latest run — already validated
    recheck_urls = set()   # Older URLs — need re-validation

    for runs_list in runs_lists:
        for r in runs_list:
            for j in r["jobs"]:
                u = j.get("url", "")
                if not u:
                    continue
                if r.get("is_latest") and r.get("date") == today_str:
                    trusted_urls.add(u)
                else:
                    recheck_urls.add(u)

    # Remove already-known broken from recheck set
    recheck_urls -= cache.broken

    if not recheck_urls:
        print(f"  Todos os links validos (latest trusted, 0 para re-checar)", flush=True)
        return

    url_list = sorted(recheck_urls)
    print(f"  Validando {len(url_list)} links antigos via HTTP (latest trusted)...", flush=True)
    results = check_urls_parallel(url_list)
    newly_dead = {u for u, dead in results.items() if dead}

    if newly_dead:
        for runs_list in runs_lists:
            for r in runs_list:
                r["jobs"] = [j for j in r["jobs"] if j.get("url", "") not in newly_dead]
                r["novas"] = len(r["jobs"])

        cache.add_broken_batch(newly_dead)
        cache.save()
        print(f"  {len(newly_dead)} links mortos removidos e salvos em broken_links.json", flush=True)
    else:
        print(f"  Todos os {len(url_list)} links antigos validos", flush=True)

_validate_all_links_live(runs, uiux_runs)

all_jobs = []
for run in runs:
    for j in run["jobs"]:
        j["is_latest"] = run.get("is_latest", False)
        all_jobs.append(j)

uiux_jobs = []
for run in uiux_runs:
    for j in run["jobs"]:
        j["is_latest"] = run.get("is_latest", False)
        uiux_jobs.append(j)

today      = date.today()
this_week  = today - timedelta(days=today.weekday())
last_week  = this_week - timedelta(weeks=1)

latam_jobs  = [j for j in all_jobs if j.get("region") == "latam"]
europe_jobs = [j for j in all_jobs if j.get("region") == "europe"]
latest_run  = runs[-1] if runs else None
latest_latam_count  = sum(1 for j in (latest_run["jobs"] if latest_run else []) if j.get("region") == "latam")
latest_europe_count = sum(1 for j in (latest_run["jobs"] if latest_run else []) if j.get("region") == "europe")

total_jobs        = len(all_jobs)
total_latam       = len(latam_jobs)
total_europe      = len(europe_jobs)
latest_count      = runs[-1]["novas"] if runs else 0
total_runs        = len(runs)
total_uiux        = len(uiux_jobs)
latest_uiux_count = uiux_runs[-1]["novas"] if uiux_runs else 0
from datetime import timezone, timedelta as _td
_BRT = timezone(_td(hours=-3))
now_str        = datetime.now(_BRT).strftime("%d %b %Y · %H:%M")
jobs_json        = json.dumps(all_jobs,   ensure_ascii=False)
latam_jobs_json  = json.dumps(latam_jobs, ensure_ascii=False)
europe_jobs_json = json.dumps(europe_jobs,ensure_ascii=False)
uiux_jobs_json   = json.dumps(uiux_jobs,  ensure_ascii=False)
runs_json        = json.dumps(runs,        ensure_ascii=False)
tw_iso           = this_week.isoformat()
lw_iso           = last_week.isoformat()

html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Jobs Internacional · Felipe Saraiva</title>
<meta name="description" content="Vagas remotas de Product Manager e UI/UX para LATAM, Brasil e internacional. Atualizado diariamente.">
<meta property="og:title" content="Jobs Internacional · Felipe Saraiva">
<meta property="og:description" content="Vagas remotas de PM e UI/UX para LATAM, Brasil e internacional. Atualizado diariamente.">
<meta property="og:url" content="https://cync.github.io/vagas-pm/">
<meta property="og:type" content="website">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="Jobs Internacional">
<meta name="twitter:description" content="Vagas remotas curadas por Felipe Saraiva.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Sofia+Sans:ital,wght@0,400;0,450;0,500;0,700;1,450&display=swap" rel="stylesheet">
<style>
:root {{
  --ink:        #141413;
  --canvas:     #F3F0EE;
  --lifted:     #FCFBFA;
  --white:      #FFFFFF;
  --slate:      #696969;
  --dust:       #D1CDC7;
  --arc-org:    #F37338;
  --clay:       #9A3A0A;
  --r-btn:  20px;
  --r-card: 40px;
  --r-pill: 999px;
  --shadow-1: rgba(0,0,0,0.04) 0px 4px 24px 0px;
  --shadow-2: rgba(0,0,0,0.08) 0px 24px 48px 0px;
}}
*,*::before,*::after {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ font-family:'Sofia Sans',SofiaSans,Arial,sans-serif; font-weight:450; background:var(--canvas); color:var(--ink); -webkit-font-smoothing:antialiased; }}

/* REGION BAR (LATAM / Europa sub-tabs under PM) */
.region-bar {{ background:#1C1B1A; border-bottom:1px solid rgba(255,255,255,.06); padding:0 48px; display:flex; gap:0; }}
.region-btn {{ display:inline-flex; align-items:center; gap:8px; padding:11px 20px; font-family:inherit; font-size:13px; font-weight:500; color:rgba(255,255,255,.4); background:transparent; border:none; border-bottom:2px solid transparent; cursor:pointer; transition:color .15s,border-color .15s; white-space:nowrap; }}
.region-btn:hover {{ color:rgba(255,255,255,.7); }}
.region-btn.active {{ color:var(--white); border-bottom-color:var(--arc-org); }}
.region-badge {{ font-size:11px; font-weight:700; background:rgba(255,255,255,.08); border-radius:var(--r-pill); padding:2px 7px; }}
.region-btn.active .region-badge {{ background:var(--arc-org); color:var(--ink); }}
@media (max-width:680px) {{ .region-bar {{ padding:0 16px; overflow-x:auto; flex-wrap:nowrap; scrollbar-width:none; }} .region-bar::-webkit-scrollbar {{ display:none; }} .region-btn {{ flex-shrink:0; padding:10px 14px; font-size:12px; }} }}

/* TAB BAR */
.tab-bar {{ background:var(--ink); border-bottom:1px solid rgba(255,255,255,.08); padding:0 48px; display:flex; gap:0; }}
.tab-btn {{ display:inline-flex; align-items:center; gap:8px; padding:14px 20px; font-family:inherit; font-size:14px; font-weight:500; color:rgba(255,255,255,.45); background:transparent; border:none; border-bottom:2px solid transparent; cursor:pointer; transition:color .15s,border-color .15s; white-space:nowrap; }}
.tab-btn:hover {{ color:rgba(255,255,255,.75); }}
.tab-btn.active {{ color:var(--white); border-bottom-color:var(--arc-org); }}
.tab-badge {{ font-size:11px; font-weight:700; background:rgba(255,255,255,.1); border-radius:var(--r-pill); padding:2px 7px; }}
.tab-btn.active .tab-badge {{ background:var(--arc-org); color:var(--ink); }}

/* HEADER */
header {{ background:var(--ink); padding:0 48px; display:flex; align-items:center; justify-content:space-between; gap:24px; flex-wrap:wrap; min-height:80px; }}
.header-brand {{ display:flex; align-items:center; gap:20px; padding:20px 0; }}
.brand-copy h1 {{ font-size:22px; font-weight:500; letter-spacing:-0.44px; line-height:28px; color:var(--white); }}
.brand-copy p {{ font-size:13px; color:var(--dust); margin-top:2px; }}
.linkedin-brand {{ display:inline-flex; align-items:center; gap:5px; margin-top:6px; text-decoration:none; color:var(--dust); font-size:12px; opacity:.7; transition:opacity .2s,color .2s; }}
.linkedin-brand:hover {{ opacity:1; color:var(--arc-org); }}
.header-right {{ display:flex; align-items:center; gap:12px; padding:20px 0; flex-wrap:wrap; }}
.stat-pair {{ display:flex; align-items:center; gap:16px; padding-right:16px; border-right:1px solid rgba(255,255,255,.1); }}
.stat-item {{ text-align:center; }}
.stat-item .num {{ display:block; font-size:20px; font-weight:500; letter-spacing:-0.4px; color:var(--arc-org); line-height:1; }}
.stat-item .lbl {{ display:block; font-size:11px; font-weight:700; letter-spacing:.44px; text-transform:uppercase; color:var(--dust); margin-top:2px; }}
.personal-link {{ display:inline-flex; align-items:center; gap:6px; text-decoration:none; background:transparent; border:1.5px solid rgba(255,255,255,.25); border-radius:var(--r-btn); padding:8px 20px; font-family:inherit; font-size:14px; font-weight:500; color:var(--white); transition:border-color .2s,color .2s; }}
.personal-link:hover {{ border-color:var(--arc-org); color:var(--arc-org); }}
.subscribe-btn {{ display:inline-flex; align-items:center; gap:6px; cursor:pointer; border:none; background:var(--arc-org); color:var(--ink); border-radius:var(--r-btn); padding:8px 20px; font-family:inherit; font-size:14px; font-weight:500; transition:opacity .2s; }}
.subscribe-btn:hover {{ opacity:.85; }}
.subscribe-btn.subscribed {{ background:transparent; border:1.5px solid rgba(255,255,255,.25); color:var(--dust); }}

/* CONTROLS */
.controls {{ background:var(--white); border-bottom:1px solid rgba(20,20,19,.1); padding:12px 48px; display:flex; gap:10px; flex-wrap:wrap; align-items:center; position:sticky; top:0; z-index:100; }}
.search-wrap {{ flex:1; min-width:200px; position:relative; }}
.search-wrap input {{ width:100%; border:1.5px solid rgba(20,20,19,.12); border-radius:var(--r-pill); padding:9px 16px 9px 38px; font-family:inherit; font-size:14px; font-weight:450; color:var(--ink); background:var(--canvas); outline:none; transition:border-color .2s; }}
.search-wrap input:focus {{ border-color:var(--arc-org); }}
.search-wrap::before {{ content:'\\2315'; position:absolute; left:13px; top:50%; transform:translateY(-50%); font-size:16px; color:var(--dust); pointer-events:none; }}
.platform-pills {{ display:flex; gap:6px; flex-wrap:wrap; align-items:center; }}
.pp {{ border:1.5px solid rgba(20,20,19,.15); border-radius:var(--r-pill); padding:7px 14px; font-size:13px; font-family:inherit; font-weight:500; color:var(--ink); background:var(--white); cursor:pointer; transition:all .15s; white-space:nowrap; }}
.pp:hover {{ border-color:var(--ink); color:var(--ink); }}
.pp.active {{ background:var(--ink); color:var(--white); border-color:var(--ink); }}
.toggle-new {{ display:inline-flex; align-items:center; gap:6px; border:1.5px solid rgba(20,20,19,.15); border-radius:var(--r-pill); padding:7px 16px; font-size:13px; font-family:inherit; font-weight:500; color:var(--ink); background:var(--white); cursor:pointer; transition:all .15s; white-space:nowrap; }}
.toggle-new input {{ display:none; }}
.toggle-new.on {{ background:#FFF3ED; border-color:var(--arc-org); color:var(--arc-org); }}
.count-pill {{ font-size:13px; font-weight:500; color:var(--ink); padding:7px 0; margin-left:auto; white-space:nowrap; }}

/* WEEK NAV */
.week-nav {{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:32px; }}
.week-btn {{ border:1.5px solid rgba(20,20,19,.15); border-radius:var(--r-pill); padding:8px 20px; font-family:inherit; font-size:14px; font-weight:500; color:var(--ink); background:var(--white); cursor:pointer; transition:all .15s; }}
.week-btn:hover:not(.active) {{ border-color:var(--ink); color:var(--ink); }}
.week-btn.active {{ background:var(--ink); color:var(--white); border-color:var(--ink); }}
.week-btn .cnt {{ font-size:12px; opacity:.6; }}

/* MAIN LAYOUT */
main {{ max-width:1040px; margin:0 auto; padding:40px 24px 80px; }}
.section-header {{ margin-bottom:16px; }}
.section-title {{ font-size:28px; font-weight:500; letter-spacing:-0.56px; color:var(--ink); }}
.section-range {{ font-size:14px; color:var(--ink); margin-top:4px; }}
.orbit-line {{ height:2px; background:var(--arc-org); border-radius:2px; margin-bottom:24px; width:48px; }}
.week-section {{ margin-bottom:48px; }}
.month-label {{ font-size:12px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; color:var(--ink); margin:24px 0 12px; }}

/* JOB CARDS */
.jobs-list {{ display:flex; flex-direction:column; gap:0; background:var(--white); border-radius:var(--r-card); overflow:hidden; box-shadow:var(--shadow-1); }}
.job-card {{ display:grid; grid-template-columns:44px 1fr auto; gap:0 16px; align-items:center; padding:16px 24px; border-bottom:1px solid rgba(20,20,19,.07); transition:background .15s; }}
.job-card:last-child {{ border-bottom:none; }}
.job-card:hover {{ background:var(--canvas); }}
.job-avatar {{ width:44px; height:44px; border-radius:12px; display:flex; align-items:center; justify-content:center; font-size:13px; font-weight:500; flex-shrink:0; }}
.job-meta {{ display:flex; flex-direction:column; gap:3px; min-width:0; }}
.job-company {{ font-size:15px; font-weight:500; color:var(--ink); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.job-role {{ font-size:13px; color:var(--ink); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.job-tags {{ display:flex; align-items:center; gap:6px; margin-top:4px; flex-wrap:wrap; }}
.ats-tag {{ font-size:11px; font-weight:700; letter-spacing:.04em; text-transform:uppercase; background:rgba(20,20,19,.06); border-radius:var(--r-pill); padding:3px 9px; color:var(--ink); }}
.date-tag {{ font-size:11px; color:var(--ink); }}
.new-badge {{ font-size:11px; font-weight:500; background:var(--arc-org); color:var(--white); border-radius:var(--r-pill); padding:2px 8px; }}
.apply-btn {{ display:inline-flex; align-items:center; gap:4px; text-decoration:none; background:transparent; border:1.5px solid var(--arc-org); border-radius:var(--r-btn); padding:8px 18px; font-family:inherit; font-size:13px; font-weight:500; color:var(--arc-org); transition:background .15s,color .15s; white-space:nowrap; flex-shrink:0; }}
.apply-btn:hover {{ background:var(--arc-org); color:var(--white); }}

/* EMPTY STATE */
.empty-state {{ text-align:center; padding:64px 24px; color:var(--ink); }}
.empty-state .empty-icon {{ font-size:40px; margin-bottom:16px; opacity:.4; }}
.empty-state h3 {{ font-size:18px; font-weight:500; color:var(--ink); margin-bottom:8px; }}
.empty-state p {{ font-size:14px; }}

/* FOOTER */
footer {{ background:var(--ink); color:var(--dust); padding:48px 48px 56px; }}
.footer-headline {{ font-size:28px; font-weight:500; letter-spacing:-0.56px; color:var(--white); margin-bottom:32px; line-height:1.3; }}
.footer-grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:32px; margin-bottom:40px; }}
.footer-col a {{ display:block; font-size:14px; color:var(--dust); text-decoration:none; margin-bottom:8px; transition:color .2s; }}
.footer-col a:hover {{ color:var(--arc-org); }}
.footer-col-header {{ font-size:11px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; color:var(--slate); margin-bottom:14px; }}
.footer-bottom {{ display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; padding-top:24px; border-top:1px solid rgba(255,255,255,.08); font-size:12px; color:var(--slate); }}
.updated-badge {{ background:rgba(243,115,56,.12); color:var(--arc-org); border-radius:var(--r-pill); padding:4px 12px; font-size:12px; }}

/* TABLET */
@media (max-width:900px) {{
  header {{ padding:0 24px; }}
  .controls {{ padding:10px 24px; }}
  footer {{ padding:36px 24px 48px; }}
  main {{ padding:28px 16px 60px; }}
  .tab-bar {{ padding:0 24px; }}
}}

/* MOBILE */
@media (max-width:680px) {{
  .tab-bar {{ padding:0 16px; overflow-x:auto; flex-wrap:nowrap; scrollbar-width:none; }}
  .tab-bar::-webkit-scrollbar {{ display:none; }}
  .tab-btn {{ flex-shrink:0; padding:12px 14px; font-size:13px; gap:6px; }}
  header {{ flex-direction:column; align-items:flex-start; padding:0 20px; gap:0; min-height:unset; }}
  .header-brand {{ padding:18px 0 10px; gap:14px; }}
  .brand-copy h1 {{ font-size:18px; line-height:22px; }}
  .brand-copy p {{ font-size:12px; }}
  .linkedin-brand {{ font-size:11px; }}
  .header-right {{ padding:0 0 14px; gap:8px; width:100%; overflow-x:auto; -webkit-overflow-scrolling:touch; scrollbar-width:none; flex-wrap:nowrap; }}
  .header-right::-webkit-scrollbar {{ display:none; }}
  .stat-pair {{ flex-shrink:0; }}
  .personal-link {{ display:none; }}
  .subscribe-btn {{ font-size:12px; padding:6px 14px; flex-shrink:0; }}
  .controls {{ padding:10px 16px; gap:8px; }}
  .search-wrap {{ min-width:100%; order:-1; }}
  .platform-pills {{ overflow-x:auto; flex-wrap:nowrap; -webkit-overflow-scrolling:touch; scrollbar-width:none; padding-bottom:2px; }}
  .platform-pills::-webkit-scrollbar {{ display:none; }}
  .pp {{ flex-shrink:0; font-size:12px; padding:6px 12px; }}
  .toggle-new {{ font-size:12px; padding:6px 12px; }}
  .count-pill {{ font-size:12px; }}
  .week-nav {{ flex-wrap:nowrap; overflow-x:auto; -webkit-overflow-scrolling:touch; scrollbar-width:none; padding-bottom:4px; margin-bottom:20px; }}
  .week-nav::-webkit-scrollbar {{ display:none; }}
  .week-btn {{ flex-shrink:0; padding:8px 18px; font-size:13px; }}
  .section-title {{ font-size:22px; }}
  .job-card {{ grid-template-columns:36px 1fr; gap:0 12px; padding:14px 16px; row-gap:8px; }}
  .job-avatar {{ width:36px; height:36px; border-radius:8px; font-size:12px; }}
  .apply-btn {{ grid-column:1/-1; justify-content:center; padding:10px 18px; font-size:13px; }}
  .job-company {{ font-size:14px; }}
  main {{ padding:20px 12px 60px; }}
  footer {{ padding:32px 20px 40px; }}
  .footer-headline {{ font-size:20px; margin-bottom:24px; }}
  .footer-grid {{ grid-template-columns:1fr 1fr; gap:24px; }}
  .footer-bottom {{ flex-direction:column; align-items:flex-start; gap:8px; }}
}}
@media (max-width:380px) {{
  .footer-grid {{ grid-template-columns:1fr; }}
}}
</style>

<!-- OneSignal -->
<script src="https://cdn.onesignal.com/sdks/web/v16/OneSignalSDK.page.js" defer></script>
<script>
window.OneSignalDeferred = window.OneSignalDeferred || [];
OneSignalDeferred.push(async function(OneSignal) {{
  await OneSignal.init({{
    appId: "fbe91485-4e45-443c-babd-4870d2bce2fe",
    serviceWorkerPath: "/vagas-pm/OneSignalSDKWorker.js",
    serviceWorkerParam: {{ scope: "/vagas-pm/" }},
  }});
  updateSubscribeBtn(OneSignal.User.PushSubscription.optedIn);
  OneSignal.User.PushSubscription.addEventListener("change", e => updateSubscribeBtn(e.current.optedIn));
}});
function updateSubscribeBtn(subscribed) {{
  document.querySelectorAll('.subscribe-btn').forEach(btn => {{
    if (subscribed) {{ btn.textContent = "\\u2713 Inscrito"; btn.classList.add("subscribed"); }}
    else {{ btn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.9 2 2 2zm6-6V11c0-3.07-1.63-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.64 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z"/></svg> Receber alertas'; btn.classList.remove("subscribed"); }}
  }});
}}
async function toggleSubscribe() {{
  OneSignalDeferred.push(async function(OS) {{
    OS.User.PushSubscription.optedIn ? await OS.User.PushSubscription.optOut() : await OS.User.PushSubscription.optIn();
  }});
}}
</script>
</head>
<body>

<div class="tab-bar">
  <button class="tab-btn active" id="tab-pm" onclick="switchTab('pm')">
    Product Manager
    <span class="tab-badge" id="tab-badge-pm">{total_jobs}</span>
  </button>
  <button class="tab-btn" id="tab-uiux" onclick="switchTab('uiux')">
    UI / UX Designer
    <span class="tab-badge" id="tab-badge-uiux">{total_uiux}</span>
  </button>
</div>

<!-- PM region sub-tabs -->
<div class="region-bar" id="region-bar">
  <button class="region-btn active" id="rbtn-latam" onclick="switchRegion('latam')">
    🌎 LATAM
    <span class="region-badge" id="rbadge-latam">{total_latam}</span>
  </button>
  <button class="region-btn" id="rbtn-europe" onclick="switchRegion('europe')">
    🇪🇺 Europe
    <span class="region-badge" id="rbadge-europe">{total_europe}</span>
  </button>
</div>

<header>
  <div class="header-brand">
    <div class="brand-copy">
      <h1 id="page-title">PM Jobs Internacional</h1>
      <p>curated by <a href="https://felipesaraiva.com" style="color:var(--arc-org);text-decoration:none" target="_blank">Felipe Saraiva</a> · atualizado diariamente</p>
      <a class="linkedin-brand" href="https://www.linkedin.com/in/felipesaraiva/" target="_blank" rel="noopener">
        <svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 0 1-2.063-2.065 2.064 2.064 0 1 1 2.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/></svg>
        linkedin/felipesaraiva
      </a>
    </div>
  </div>
  <div class="header-right">
    <div class="stat-pair">
      <div class="stat-item"><span class="num" id="vis-count">{total_latam}</span><span class="lbl">vagas</span></div>
      <div class="stat-item"><span class="num" id="today-count">{latest_latam_count}</span><span class="lbl">hoje</span></div>
    </div>
    <button class="subscribe-btn" onclick="toggleSubscribe()">
      <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.9 2 2 2zm6-6V11c0-3.07-1.63-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.64 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z"/></svg>
      Receber alertas
    </button>
    <a class="personal-link" href="https://felipesaraiva.com" target="_blank" rel="noopener">felipesaraiva.com &#x2197;</a>
  </div>
</header>

<div class="controls">
  <div class="search-wrap">
    <input type="text" id="search" placeholder="Buscar empresa, cargo, plataforma..." oninput="applyFilter()">
  </div>
  <div class="platform-pills" id="platform-pills"></div>
  <label class="toggle-new" id="lbl-new">
    <input type="checkbox" id="only-new" onchange="document.getElementById('lbl-new').classList.toggle('on',this.checked);applyFilter()">
    &#x2726; Só novas
  </label>
  <span class="count-pill" id="count-lbl">{total_latam} vagas</span>
</div>

<main>
  <div class="week-nav" id="week-nav"></div>
  <div id="content"></div>
</main>

<footer>
  <div class="footer-headline">Sempre aqui quando a vaga certa aparecer.</div>
  <div class="footer-grid">
    <div class="footer-col">
      <div class="footer-col-header">Links</div>
      <a href="https://felipesaraiva.com" target="_blank">felipesaraiva.com</a>
      <a href="https://github.com/cync/vagas-pm" target="_blank">GitHub vagas-pm</a>
      <a href="https://linkedin.com/in/felipesaraiva" target="_blank">LinkedIn</a>
    </div>
    <div class="footer-col">
      <div class="footer-col-header">Plataformas</div>
      <a href="https://greenhouse.io" target="_blank">Greenhouse</a>
      <a href="https://jobs.lever.co" target="_blank">Lever</a>
      <a href="https://remotive.com" target="_blank">Remotive</a>
      <a href="https://weworkremotely.com" target="_blank">We Work Remotely</a>
    </div>
    <div class="footer-col">
      <div class="footer-col-header">Sobre</div>
      <a href="#">Busca automatizada diaria por vagas remotas de PM e UI/UX para LATAM e internacional. Curado por Felipe Saraiva.</a>
    </div>
  </div>
  <div class="footer-bottom">
    <span>&#169; {datetime.now().year} Felipe Saraiva · Atualizado em {now_str}</span>
    <span class="updated-badge">&#x2726; {total_jobs} PM · {total_uiux} UI/UX · {total_runs} execucoes</span>
  </div>
</footer>

<script>
const ALL_JOBS    = {jobs_json};
const LATAM_JOBS  = {latam_jobs_json};
const EUROPE_JOBS = {europe_jobs_json};
const UIUX_JOBS   = {uiux_jobs_json};
const RUNS        = {runs_json};

const PM_TOTALS   = {{ total: {total_jobs},  today: {latest_count} }};
const UIUX_TOTALS = {{ total: {total_uiux}, today: {latest_uiux_count} }};
const LATAM_TOTALS  = {{ total: {total_latam},  today: {latest_latam_count} }};
const EUROPE_TOTALS = {{ total: {total_europe}, today: {latest_europe_count} }};

let currentTab    = 'pm';
let currentRegion = 'latam';  // active PM sub-tab

function getActiveJobs() {{
  if (currentTab === 'uiux') return UIUX_JOBS;
  return currentRegion === 'latam' ? LATAM_JOBS : EUROPE_JOBS;
}}

const AVATAR_COLORS = [
  ['#FFF3ED','#C04A0A'],['#EDF4FF','#1A5CB8'],['#F0FFF4','#1A7A3C'],
  ['#FFF8E1','#8B6914'],['#F3E8FF','#6B21A8'],['#FFE8EC','#9B1C2E'],
  ['#E8F5E9','#1B5E20'],['#E3F2FD','#0D47A1'],['#FBE9E7','#BF360C'],
  ['#E8EAF6','#283593']
];
function avatarColor(name) {{
  let h=0; for(let c of name) h=(h*31+c.charCodeAt(0))&0xFFFFFF;
  return AVATAR_COLORS[Math.abs(h)%AVATAR_COLORS.length];
}}
function initials(name) {{
  return name.split(/\\s+/).slice(0,2).map(w=>w[0]||'').join('').toUpperCase()||'??';
}}

const MESES_PT = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez'];
const MESES_FULL_PT = ['Janeiro','Fevereiro','Marco','Abril','Maio','Junho','Julho','Agosto','Setembro','Outubro','Novembro','Dezembro'];

function fmtDate(s) {{
  const [y,m,d]=s.split('-');
  return (parseInt(d)) + ' ' + MESES_PT[+m-1] + ' ' + y;
}}
function fmtRange(isoMon) {{
  const s=new Date(isoMon+'T12:00:00'),e=new Date(isoMon+'T12:00:00');
  e.setDate(e.getDate()+6);
  return s.getDate() + ' ' + MESES_PT[s.getMonth()] + ' - ' + e.getDate() + ' ' + MESES_PT[e.getMonth()];
}}
function monthKeyPT(dateStr) {{
  const d=new Date(dateStr+'T12:00:00');
  return MESES_FULL_PT[d.getMonth()]+' '+d.getFullYear();
}}
function monthSortKey(ptLabel) {{
  const parts=ptLabel.split(' ');
  const m=parts[0], y=parts[1];
  return y+'-'+String(MESES_FULL_PT.indexOf(m)+1).padStart(2,'0');
}}

const TW = '{tw_iso}';
const LW = '{lw_iso}';
function weekStart(s) {{ const d=new Date(s+'T12:00:00'); d.setDate(d.getDate()-((d.getDay()+6)%7)); return d.toISOString().slice(0,10); }}
function weekBucket(s) {{ const ws=weekStart(s); if(ws>=TW) return 'this_week'; if(ws>=LW) return 'last_week'; return 'earlier'; }}

function buildPills(jobs) {{
  const atsList=[...new Set(jobs.map(j=>j.ats))].sort();
  document.getElementById('platform-pills').innerHTML=['Todas',...atsList].map(function(a){{return '<button class="pp'+(a==='Todas'?' active':'')+'" data-p="'+a+'" onclick="setPlatform(this.dataset.p)">'+a+'</button>';}}).join('');
}}
buildPills(LATAM_JOBS);

let activePlatform = 'Todas';
function setPlatform(p) {{
  activePlatform=p;
  document.querySelectorAll('.pp').forEach(el=>el.classList.toggle('active',el.textContent===p));
  applyFilter();
}}

function switchRegion(region) {{
  currentRegion = region;
  document.getElementById('rbtn-latam').classList.toggle('active', region==='latam');
  document.getElementById('rbtn-europe').classList.toggle('active', region==='europe');
  const totals = region==='latam' ? LATAM_TOTALS : EUROPE_TOTALS;
  document.getElementById('today-count').textContent = totals.today;
  activeWeek = null;
  activePlatform = 'Todas';
  document.getElementById('search').value = '';
  document.getElementById('only-new').checked = false;
  document.getElementById('lbl-new').classList.remove('on');
  buildPills(getActiveJobs());
  applyFilter();
}}

let activeWeek = null;

function applyFilter() {{
  const q       = document.getElementById('search').value.toLowerCase();
  const onlyNew = document.getElementById('only-new').checked;
  const JOBS    = getActiveJobs();

  const vis = JOBS.filter(function(j) {{
    if (onlyNew && !j.is_latest) return false;
    if (activePlatform !== 'Todas' && j.ats !== activePlatform) return false;
    if (activeWeek && weekBucket(j.date) !== activeWeek) return false;
    if (q && (j.company+' '+j.role+' '+j.ats).toLowerCase().indexOf(q)===-1) return false;
    return true;
  }});

  document.getElementById('vis-count').textContent = vis.length;
  document.getElementById('count-lbl').textContent  = vis.length + ' vagas';

  const twN=JOBS.filter(j=>weekBucket(j.date)==='this_week').length;
  const lwN=JOBS.filter(j=>weekBucket(j.date)==='last_week').length;
  const olN=JOBS.filter(j=>weekBucket(j.date)==='earlier').length;

  const weekBtns = [
    {{key:'this_week',label:'Esta semana',cnt:twN}},
    {{key:'last_week',label:'Semana passada',cnt:lwN}},
    {{key:'earlier',label:'Anteriores',cnt:olN}},
  ].map(function(n) {{
    return '<button class="week-btn'+(activeWeek===n.key?' active':'')+'" data-w="'+n.key+'" onclick="setWeek(this.dataset.w)">'+n.label+' <span class="cnt">('+n.cnt+')</span></button>';
  }}).join('') + (activeWeek ? '<button class="week-btn" onclick="setWeek(null)">x Ver tudo</button>' : '');
  document.getElementById('week-nav').innerHTML = weekBtns;

  if(!vis.length) {{
    document.getElementById('content').innerHTML='<div class="empty-state"><div class="empty-icon">&#x1F50D;</div><h3>Nenhuma vaga encontrada</h3><p>Tente ajustar os filtros ou a busca.</p></div>';
    return;
  }}

  const buckets={{this_week:[],last_week:[],earlier:[]}};
  vis.forEach(function(j) {{ buckets[weekBucket(j.date)].push(j); }});

  const order=activeWeek?[activeWeek]:['this_week','last_week','earlier'];
  const labels={{
    this_week: {{title:'Esta Semana', range:fmtRange(TW)}},
    last_week: {{title:'Semana Passada', range:fmtRange(LW)}},
    earlier:   {{title:'Anteriores', range:''}}
  }};

  let html='';
  order.forEach(function(bk) {{
    const jobs=buckets[bk];
    if(!jobs||!jobs.length) return;
    jobs.sort(function(a,b) {{ return b.date.localeCompare(a.date)||b.exec.localeCompare(a.exec); }});
    const lbl=labels[bk];
    html+='<div class="week-section"><div class="section-header"><div class="section-title">'+lbl.title+'</div>'+(lbl.range?'<div class="section-range">'+lbl.range+'</div>':'')+'</div><div class="orbit-line"></div>';
    if(bk==='earlier') {{
      const byM={{}};
      jobs.forEach(function(j) {{ const mk=monthKeyPT(j.date); if(!byM[mk]) byM[mk]=[]; byM[mk].push(j); }});
      Object.keys(byM).sort(function(a,b) {{ return monthSortKey(b).localeCompare(monthSortKey(a)); }}).forEach(function(mk) {{
        html+='<div class="month-label">'+mk+'</div><div class="jobs-list">'+byM[mk].map(jobCard).join('')+'</div>';
      }});
    }} else {{
      html+='<div class="jobs-list">'+jobs.map(jobCard).join('')+'</div>';
    }}
    html+='</div>';
  }});

  document.getElementById('content').innerHTML=html;
}}

function jobCard(j) {{
  const colors=avatarColor(j.company);
  const bg=colors[0], fg=colors[1];
  const ini=initials(j.company);
  const newBadge=j.is_latest?'<span class="new-badge">nova</span>':'';
  return '<div class="job-card">'
    +'<div class="job-avatar" style="background:'+bg+';color:'+fg+'">'+ini+'</div>'
    +'<div class="job-meta">'
    +'<div class="job-company">'+j.company+'</div>'
    +'<div class="job-role">'+j.role+'</div>'
    +'<div class="job-tags"><span class="ats-tag">'+j.ats+'</span><span class="date-tag">'+fmtDate(j.date)+'</span>'+newBadge+'</div>'
    +'</div>'
    +'<a class="apply-btn" href="'+j.url+'" target="_blank" rel="noopener">Ver vaga</a>'
    +'</div>';
}}

function setWeek(w) {{ activeWeek=w; applyFilter(); }}


function switchTab(tab) {{
  currentTab = tab;
  document.getElementById('tab-pm').classList.toggle('active', tab==='pm');
  document.getElementById('tab-uiux').classList.toggle('active', tab==='uiux');
  document.getElementById('page-title').textContent = tab==='pm' ? 'PM Jobs Internacional' : 'UI/UX Jobs Internacional';
  document.getElementById('region-bar').style.display = tab==='pm' ? 'flex' : 'none';
  const totals = tab==='pm' ? (currentRegion==='latam' ? LATAM_TOTALS : EUROPE_TOTALS) : UIUX_TOTALS;
  document.getElementById('today-count').textContent = totals.today;
  activeWeek = null;
  activePlatform = 'Todas';
  document.getElementById('search').value = '';
  document.getElementById('only-new').checked = false;
  document.getElementById('lbl-new').classList.remove('on');
  buildPills(getActiveJobs());
  applyFilter();
}}

applyFilter();
</script>
</body>
</html>
"""

(SITE_DIR / "index.html").write_text(html, encoding="utf-8")
print("OK site gerado: PM=%d LATAM=%d EU=%d UIUX=%d" % (len(all_jobs), len(latam_jobs), len(europe_jobs), len(uiux_jobs)), flush=True)
