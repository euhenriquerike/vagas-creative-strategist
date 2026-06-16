#!/usr/bin/env python3
"""
Pipeline autônomo: busca vagas PM via Tavily + extrai com Claude API (fallback: regex) + gera site.
"""
import os, json, re, sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

from tavily import TavilyClient

# ── Configurações ────────────────────────────────────────────────────────────
TAVILY_API_KEY    = os.environ["TAVILY_API_KEY"]
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

SCRIPT_DIR   = Path(__file__).parent
VAGAS_DIR    = SCRIPT_DIR / "vagas"
HISTORY_FILE = VAGAS_DIR / "url_history.json"
BRT = timezone(timedelta(hours=-3))

TODAY = datetime.now(BRT).date().isoformat()

tavily = TavilyClient(api_key=TAVILY_API_KEY)

# ── Histórico ─────────────────────────────────────────────────────────────────
def load_history() -> set:
    if HISTORY_FILE.exists():
        return set(json.loads(HISTORY_FILE.read_text(encoding="utf-8")))
    return set()

def save_history(history: set):
    HISTORY_FILE.write_text(
        json.dumps(sorted(history), indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

# ── Validação de links ─────────────────────────────────────────────────────────
BROKEN_FILE = SCRIPT_DIR / "broken_links.json"

DEAD_PATTERNS = [
    "the job you requested was not found",
    "job not found",
    "this job is no longer available",
    "this job listing is no longer active",
    "no longer accepting applications",
    "position has been filled",
    "job has expired",
    "this position is no longer available",
    "application is not available",
    "this role is no longer",
    "opening has been filled",
    "page not found",
    "404 not found",
    "this posting has been closed",
    "application is closed",
    "position is no longer accepting",
    "this role has been filled",
    "no longer available",
    "job listing has expired",
    "this requisition is closed",
    "we are no longer accepting",
    "the page you're looking for",
    "this job opening has been closed",
]

DEAD_URL_PATTERNS = ["/404", "?error=true", "job-not-found", "posting-not-found"]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}

def _is_dead_ashby(url: str) -> bool:
    """Check Ashby job via their public API. Rejects company pages (no UUID)."""
    import urllib.request, urllib.error
    uuid_match = re.search(
        r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', url, re.I
    )
    if not uuid_match:
        return True  # No UUID = company page, not a specific job listing
    job_id = uuid_match.group(0)
    company_match = re.search(r'ashbyhq\.com/([^/]+)', url)
    if not company_match:
        return True
    company = company_match.group(1)
    api_url = f"https://jobs.ashbyhq.com/api/non-posting-external/job/{company}/{job_id}"
    try:
        req = urllib.request.Request(api_url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            posting = data.get("jobPosting") or data.get("job") or {}
            if isinstance(posting, dict):
                return not posting.get("isPublished", True)
            return False  # got data but unknown shape — assume alive
    except urllib.error.HTTPError as e:
        return e.code in (404, 410, 403, 422)
    except Exception:
        return True  # API failure = assume dead (conservative)

def _is_dead_greenhouse_api(url: str) -> bool:
    """Check Greenhouse job via their embed JSON API. Returns True if dead."""
    import urllib.request, urllib.error
    gh_m = re.search(r'greenhouse\.io/([^/]+)/jobs/(\d+)', url, re.I)
    if not gh_m:
        return True  # company page / no job ID = dead
    company = gh_m.group(1)
    job_id = gh_m.group(2)
    api_url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs/{job_id}"
    try:
        req = urllib.request.Request(api_url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return not data.get("id")  # no id = job gone
    except urllib.error.HTTPError as e:
        return e.code in (404, 410, 403, 422)
    except Exception:
        pass  # fall through to HTTP check
    return False

def _is_dead_url(url: str) -> bool:
    """Returns True if the job link is expired/not found."""
    if "ashbyhq.com" in url:
        return _is_dead_ashby(url)
    if "greenhouse.io" in url:
        if _is_dead_greenhouse_api(url):
            return True
    import urllib.request, urllib.error
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=12) as resp:
            final_url = resp.geturl().lower()
            if any(pat in final_url for pat in DEAD_URL_PATTERNS):
                return True
            body = resp.read(12000).decode("utf-8", errors="ignore").lower()
            return any(p in body for p in DEAD_PATTERNS)
    except urllib.error.HTTPError as e:
        return e.code in (404, 410, 403)
    except Exception:
        return False  # network error = assume alive

def load_broken_cache() -> set:
    if BROKEN_FILE.exists():
        data = json.loads(BROKEN_FILE.read_text(encoding="utf-8"))
        return set(data.get("broken", []))
    return set()

def save_broken_cache(broken: set):
    existing = {}
    if BROKEN_FILE.exists():
        existing = json.loads(BROKEN_FILE.read_text(encoding="utf-8"))
    ok_set = set(existing.get("ok", [])) - broken
    BROKEN_FILE.write_text(
        json.dumps({"broken": sorted(broken), "ok": sorted(ok_set)},
                   indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

def filter_live_vagas(vagas: list[dict]) -> list[dict]:
    """Remove vagas with dead/expired links (parallel check)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    known_broken = load_broken_cache()

    # Quick filter: skip known broken
    to_check = [v for v in vagas if v.get("url") not in known_broken]
    already_dead = [v for v in vagas if v.get("url") in known_broken]

    if not to_check:
        return []

    print(f"  🔍 Validando {len(to_check)} links novos...", flush=True)
    live, newly_broken = [], set()

    with ThreadPoolExecutor(max_workers=15) as pool:
        futures = {pool.submit(_is_dead_url, v["url"]): v for v in to_check if v.get("url")}
        for future in as_completed(futures):
            v = futures[future]
            if future.result():
                newly_broken.add(v["url"])
                print(f"    💀 {v.get('company','?')} — {v['url']}", flush=True)
            else:
                live.append(v)

    if newly_broken:
        all_broken = known_broken | newly_broken
        save_broken_cache(all_broken)
        print(f"  ✅ {len(live)} links vivos | 💀 {len(newly_broken)} removidos", flush=True)
    else:
        print(f"  ✅ Todos os {len(live)} links válidos", flush=True)

    return live

# ── Buscas ────────────────────────────────────────────────────────────────────
SEARCHES = [
    ("product manager remote LATAM Brazil",   ["jobs.lever.co"],                                    15),
    ("product manager remote LATAM Brazil",   ["jobs.ashbyhq.com"],                                 15),
    ("product manager remote LATAM Brazil",   ["boards.greenhouse.io","job-boards.greenhouse.io"],  15),
    ("product manager remote LATAM",          ["jobs.smartrecruiters.com"],                         10),
    ("product manager remote LATAM Brazil",   ["weworkremotely.com"],                               10),
    ("product manager remote LATAM",          ["remotive.com","himalayas.app"],                     10),
]

def search_all() -> list[dict]:
    results = []
    for query, domains, n in SEARCHES:
        try:
            resp = tavily.search(
                query=query,
                include_domains=domains,
                max_results=n,
                search_depth="advanced",
            )
            hits = resp.get("results", [])
            for h in hits:
                h["_domains"] = domains
            results.extend(hits)
            print(f"  [{domains[0]}] {len(hits)} resultados", flush=True)
        except Exception as e:
            print(f"  ERRO [{domains[0]}]: {e}", flush=True)
    return results

# ── Detecção ATS pela URL ──────────────────────────────────────────────────────
def detect_ats(url: str) -> str:
    if "lever.co"            in url: return "Lever"
    if "ashbyhq.com"         in url: return "Ashby"
    if "greenhouse.io"       in url: return "Greenhouse"
    if "smartrecruiters.com" in url: return "SmartRecruiters"
    if "weworkremotely.com"  in url: return "WWR"
    if "remotive.com"        in url: return "Remotive"
    if "himalayas.app"       in url: return "Himalayas"
    return "Outro"

# ── Extração com Claude (opcional) ───────────────────────────────────────────
EXTRACT_PROMPT = """Você vai receber resultados de busca de vagas de emprego (título + URL + snippet).

Para CADA resultado, determine se é uma vaga de **Product Manager** remota que aceita candidatos do Brasil/LATAM.

Retorne um JSON array. Cada item deve ter:
- "company": nome da empresa
- "role": título do cargo
- "url": URL exata da vaga
- "ats": plataforma ATS (Lever / Ashby / Greenhouse / SmartRecruiters / WWR / Remotive / Himalayas / Outro)
- "latam_friendly": true se menciona LATAM, Brazil, remote-anywhere, ou não restringe a US/EU

Inclua SOMENTE vagas de PM (Product Manager, Product Owner, Head of Product). Exclua engineering, design, marketing, etc.
Se não houver vagas válidas, retorne [].

Resultados de busca:
"""

def extract_with_claude(raw_results: list[dict]) -> list[dict] | None:
    """Tenta extrair via Claude API. Retorna None se indisponível."""
    if not ANTHROPIC_API_KEY:
        return None
    try:
        from anthropic import Anthropic, BadRequestError, APIStatusError
        claude = Anthropic(api_key=ANTHROPIC_API_KEY)

        context_lines = []
        for r in raw_results:
            context_lines.append(f"Título: {r.get('title', '')}")
            context_lines.append(f"URL: {r.get('url', '')}")
            context_lines.append(f"Snippet: {r.get('content', '')[:300]}")
            context_lines.append("---")
        context = "\n".join(context_lines)

        msg = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": EXTRACT_PROMPT + context + "\n\nRetorne apenas o JSON array, sem explicações."
            }]
        )
        text = msg.content[0].text.strip()
        text = re.sub(r'^```(?:json)?\n?', '', text)
        text = re.sub(r'\n?```$', '', text)
        return json.loads(text)

    except Exception as e:
        print(f"  ⚠️  Claude indisponível ({type(e).__name__}): {e}", flush=True)
        return None

# ── Extração por regex (fallback) ─────────────────────────────────────────────
PM_KEYWORDS = re.compile(
    r'\bproduct\s+manager\b|\bproduct\s+owner\b|\bhead\s+of\s+product\b|'
    r'\bsenior\s+pm\b|\bprincipal\s+pm\b|\bgroup\s+pm\b|\bstaff\s+pm\b',
    re.IGNORECASE
)
EXCLUDE_KEYWORDS = re.compile(
    r'\bengineer\b|\bdeveloper\b|\bdesigner\b|\bmarketing\b|\bsales\b|'
    r'\bdata\s+scientist\b|\banalyst\b|\baccountant\b|\brecruiter\b',
    re.IGNORECASE
)

def extract_company_from_title(title: str, url: str) -> str:
    """Tenta extrair nome da empresa do título ou domínio."""
    # Padrão "Empresa – Cargo" ou "Empresa | Cargo"
    for sep in [" – ", " - ", " | ", " at "]:
        if sep in title:
            parts = title.split(sep)
            # Geralmente cargo vem antes em sites como WWR, empresa depois
            # Testa qual parte parece cargo de PM
            for i, part in enumerate(parts):
                if PM_KEYWORDS.search(part):
                    other = parts[1-i] if len(parts) == 2 else parts[-1]
                    return other.strip()
    # Fallback: extrair do domínio
    domain = re.search(r'(?:jobs\.|boards\.|job-boards\.)([^./]+)', url)
    if domain:
        return domain.group(1).replace("-", " ").title()
    return "?"

def _is_specific_job_url(url: str) -> bool:
    """Reject URLs that are company pages, search pages, or category listings.
    Only allow URLs that point to a specific job posting."""
    if re.search(r'/jobs/\d+', url):                            return True  # Greenhouse numeric ID
    if re.search(r'/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}', url, re.I): return True  # UUID (Lever, Ashby)
    if re.search(r'/view/[A-Za-z0-9_=-]+', url):                return True  # Workable ID
    if re.search(r'/remote-jobs/[a-z0-9-]+', url):              return True  # WWR slug
    if re.search(r'/jobs/[a-z0-9-]{10,}$', url):                return True  # Remotive/Himalayas slug
    if re.search(r'/o/[a-z0-9-]+$', url):                       return True  # Recruitee slug
    if re.search(r'/positions/\d+', url):                        return True  # Careers site numeric
    if re.search(r'/j/[A-Za-z0-9]+', url):                      return True  # Workable apply link
    return False

def extract_with_regex(raw_results: list[dict]) -> list[dict]:
    """Extração heurística sem LLM."""
    vagas = []
    for r in raw_results:
        title   = r.get("title", "")
        url     = r.get("url", "")
        content = r.get("content", "")
        full_text = f"{title} {content}"

        # Filtro: deve mencionar PM e não ser cargo excluído
        if not PM_KEYWORDS.search(full_text):
            continue
        if EXCLUDE_KEYWORDS.search(title):
            continue

        # Reject URLs that aren't specific job postings
        if not _is_specific_job_url(url):
            print(f"    ⏭️  URL não é vaga específica: {url[:80]}", flush=True)
            continue

        company = extract_company_from_title(title, url)
        ats     = detect_ats(url)

        # Limpa o role: tenta pegar a parte do título que é o cargo
        role = title
        for sep in [" – ", " - ", " | ", " at "]:
            if sep in title:
                parts = title.split(sep)
                for part in parts:
                    if PM_KEYWORDS.search(part):
                        role = part.strip()
                        break
                break

        vagas.append({
            "company": company,
            "role":    role,
            "url":     url,
            "ats":     ats,
            "latam_friendly": bool(re.search(r'LATAM|Brazil|Brasil|remote.anywhere|anywhere', full_text, re.I)),
        })
    return vagas

# ── Extração (Claude se disponível, senão regex) ──────────────────────────────
def extract_vagas(raw_results: list[dict]) -> list[dict]:
    print("  Tentando Claude API...", flush=True)
    result = extract_with_claude(raw_results)
    if result is not None:
        print(f"  ✅ Claude extraiu {len(result)} vagas", flush=True)
        return result
    print("  🔄 Fallback: extração por regex", flush=True)
    result = extract_with_regex(raw_results)
    print(f"  ✅ Regex extraiu {len(result)} vagas", flush=True)
    return result

# ── Markdown ──────────────────────────────────────────────────────────────────
ATS_EMOJI = {
    "Lever": "🔷", "Ashby": "🔶", "Greenhouse": "🟢",
    "SmartRecruiters": "🔴", "WWR": "🟡",
    "Remotive": "⚪", "Himalayas": "⚪", "Outro": "⚫",
}
ATS_LABEL = {
    "Ashby": "Ashby HQ", "WWR": "We Work Remotely",
}

def group_by_ats(vagas):
    groups = {}
    for v in vagas:
        ats = v.get("ats", "Outro")
        if "Ashby" in ats: ats = "Ashby"
        elif "Greenhouse" in ats: ats = "Greenhouse"
        elif "Smart" in ats: ats = "SmartRecruiters"
        elif "WeWork" in ats or "We Work" in ats or ats == "WWR": ats = "WWR"
        groups.setdefault(ats, []).append(v)
    return groups

def generate_markdown(vagas, prev_count) -> str:
    months_pt = ["janeiro","fevereiro","março","abril","maio","junho",
                 "julho","agosto","setembro","outubro","novembro","dezembro"]
    d = date.today()
    now = f"{d.day} de {months_pt[d.month-1]} de {d.year}"

    total  = prev_count + len(vagas)
    groups = group_by_ats(vagas)

    lines = [
        f"# 🆕 Vagas PM Internacionais – {now}",
        "",
        f"> **Execução automática** | Busca em ATS internacionais (Lever, Ashby, Greenhouse, SmartRecruiters, WWR, Remotive)",
        f"> **Histórico:** {prev_count} vagas anteriores ignoradas | **Novas encontradas:** {len(vagas)}",
        "",
        "---",
        "",
        "## ✅ NOVAS VAGAS (não encontradas em execuções anteriores)",
        "",
    ]

    if not vagas:
        lines.append("*Nenhuma vaga nova encontrada nesta execução.*")
    else:
        ATS_ORDER = ["Lever", "Ashby", "Greenhouse", "SmartRecruiters", "WWR", "Remotive", "Himalayas", "Outro"]
        for ats in ATS_ORDER:
            bucket = groups.get(ats, [])
            if not bucket:
                continue
            emoji = ATS_EMOJI.get(ats, "⚫")
            label = ATS_LABEL.get(ats, ats)
            lines += [
                f"### {emoji} {label}",
                "",
                "| Empresa | Cargo | Link |",
                "|---------|-------|------|",
            ]
            for v in bucket:
                company = v.get("company", "?").replace("|", "\\|")
                role    = v.get("role",    "?").replace("|", "\\|")
                url     = v.get("url",     "#")
                lines.append(f"| **{company}** | {role} | [Ver vaga]({url}) |")
            lines.append("")

    lines += [
        "---",
        "",
        "## 📊 Resumo desta execução",
        "",
        f"- **Data:** {TODAY}",
        f"- **Vagas no histórico (anteriores):** {prev_count}",
        f"- **Novas vagas encontradas:** {len(vagas)}",
        f"- **Total acumulado:** {total}",
        "",
        "---",
        "",
        "*Gerado automaticamente via busca em ATS internacionais*",
    ]

    return "\n".join(lines) + "\n"


# ── Main ──────────────────────────────────────────────────────────────────────
def _revalidate_historical_urls():
    """Spot-check a sample of historical URLs from existing .md files.
    Catches links that expired between creation and now."""
    import random as _random
    from concurrent.futures import ThreadPoolExecutor, as_completed

    all_urls: set = set()
    url_re = re.compile(r'\[(?:Ver vaga|Aplicar|Apply)\]\((https?://[^)]+)\)')
    for md_file in sorted(VAGAS_DIR.glob("vagas_*.md")):
        try:
            text = md_file.read_text(encoding="utf-8", errors="replace")
            all_urls.update(url_re.findall(text))
        except Exception:
            pass

    known_broken = load_broken_cache()
    candidates = [u for u in all_urls if u not in known_broken]
    if not candidates:
        return

    sample = _random.sample(candidates, min(30, len(candidates)))
    print(f"  🔁 Re-validando {len(sample)} URLs históricas...", flush=True)

    newly_broken = set()
    with ThreadPoolExecutor(max_workers=15) as pool:
        futures = {pool.submit(_is_dead_url, url): url for url in sample}
        for future in as_completed(futures):
            url = futures[future]
            if future.result():
                newly_broken.add(url)
                print(f"    💀 Histórico morto: {url[:80]}", flush=True)

    if newly_broken:
        all_broken = known_broken | newly_broken
        save_broken_cache(all_broken)
        print(f"  ✅ {len(newly_broken)} links históricos mortos adicionados ao cache", flush=True)
    else:
        print(f"  ✅ Todos os {len(sample)} links históricos válidos", flush=True)


def main():
    VAGAS_DIR.mkdir(parents=True, exist_ok=True)

    history = load_history()
    prev_count = len(history)

    print(f"📂 Histórico: {prev_count} URLs conhecidas", flush=True)

    # Periodic re-validation of historical URLs
    _revalidate_historical_urls()

    print("🔍 Buscando vagas...", flush=True)
    raw = search_all()
    print(f"📋 {len(raw)} resultados brutos obtidos", flush=True)

    print("🤖 Extraindo vagas estruturadas...", flush=True)
    all_vagas = extract_vagas(raw)

    # Deduplicate against history
    new_vagas = [v for v in all_vagas if v.get("url") and v["url"] not in history]
    # Deduplicate within this batch
    seen_urls: set = set()
    deduped: list = []
    for v in new_vagas:
        if v["url"] not in seen_urls:
            seen_urls.add(v["url"])
            deduped.append(v)
    new_vagas = deduped

    print(f"🆕 {len(new_vagas)} vagas novas (após deduplicação)", flush=True)

    if new_vagas:
        print("🔗 Validando links...", flush=True)
        new_vagas = filter_live_vagas(new_vagas)

    # Determine output filename (handle multiple runs per day)
    base = VAGAS_DIR / f"vagas_pm_{TODAY}.md"
    if base.exists():
        n = 2
        while (VAGAS_DIR / f"vagas_pm_{TODAY}_exec{n}.md").exists():
            n += 1
        out_path = VAGAS_DIR / f"vagas_pm_{TODAY}_exec{n}.md"
    else:
        out_path = base

    md = generate_markdown(new_vagas, prev_count)
    out_path.write_text(md, encoding="utf-8")
    print(f"💾 Salvo: {out_path.name}", flush=True)

    # Update history
    new_urls = {v["url"] for v in new_vagas if v.get("url")}
    history |= new_urls
    save_history(history)
    print(f"📚 Histórico atualizado: {len(history)} URLs", flush=True)

    # Regenerate site
    print("🌐 Regenerando site...", flush=True)
    import subprocess
    result = subprocess.run(
        ["python3", str(SCRIPT_DIR / "generate_site.py")],
        capture_output=True, text=True
    )
    if result.stdout:
        print(result.stdout, flush=True)
    if result.returncode != 0:
        print(f"⚠️  generate_site.py retornou código {result.returncode}", flush=True)
        if result.stderr:
            print(result.stderr, flush=True)

    print(f"✅ Concluído — {len(new_vagas)} novas vagas encontradas", flush=True)


if __name__ == "__main__":
    main()
