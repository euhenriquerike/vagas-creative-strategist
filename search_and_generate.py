#!/usr/bin/env python3
"""
Pipeline autônomo: busca vagas Creative/Content Strategist via Tavily + extrai com Claude API (fallback: regex) + gera site.
"""
import os, json, re, sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

from tavily import TavilyClient

from link_checker import (
    BrokenCache, check_urls_parallel, is_dead_url, is_specific_job_url,
    normalize_url,
)

# ── Configurações ────────────────────────────────────────────────────────────
TAVILY_API_KEY    = os.environ["TAVILY_API_KEY"]
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

SCRIPT_DIR   = Path(__file__).parent
VAGAS_DIR    = SCRIPT_DIR / "vagas"
HISTORY_FILE = VAGAS_DIR / "url_history.json"
BROKEN_FILE  = SCRIPT_DIR / "broken_links.json"
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
def filter_live_vagas(vagas: list[dict]) -> list[dict]:
    """Remove vagas with dead/expired links (parallel check)."""
    from datetime import date as _date
    cache = BrokenCache(BROKEN_FILE)
    today_str = _date.today().isoformat()

    to_check = [v for v in vagas if normalize_url(v.get("url", "")) not in cache.broken]

    if not to_check:
        return []

    print(f"  🔍 Validando {len(to_check)} links novos...", flush=True)
    urls_to_check = [normalize_url(v["url"]) for v in to_check if v.get("url")]
    results = check_urls_parallel(urls_to_check)

    live = []
    newly_broken = set()
    newly_alive = set()
    for v in to_check:
        nurl = normalize_url(v["url"])
        if results.get(nurl, False):
            newly_broken.add(nurl)
            print(f"    💀 {v.get('company','?')} — {nurl}", flush=True)
        else:
            live.append(v)
            newly_alive.add(nurl)

    if newly_broken:
        cache.add_broken_batch(newly_broken)
    if newly_alive:
        cache.add_ok_batch(newly_alive, today_str)
    cache.save()

    if newly_broken:
        print(f"  ✅ {len(live)} links vivos | 💀 {len(newly_broken)} removidos", flush=True)
    else:
        print(f"  ✅ Todos os {len(live)} links válidos", flush=True)

    return live

# ── Buscas ────────────────────────────────────────────────────────────────────
SEARCHES = [
    ("creative strategist remote LATAM Brazil",              ["jobs.lever.co"],                                   20),
    ("content strategist remote LATAM Brazil",               ["jobs.lever.co"],                                   15),
    ("creative lead remote LATAM Brazil",                    ["jobs.lever.co"],                                   15),
    ("social media strategist remote LATAM Brazil",          ["jobs.lever.co"],                                   10),
    ("creative strategist remote LATAM Brazil",              ["jobs.ashbyhq.com"],                                20),
    ("content strategist remote LATAM Brazil",               ["jobs.ashbyhq.com"],                                15),
    ("creative lead remote LATAM Brazil",                    ["jobs.ashbyhq.com"],                                15),
    ("social media strategist remote LATAM Brazil",          ["jobs.ashbyhq.com"],                                10),
    ("creative strategist remote LATAM Brazil",              ["boards.greenhouse.io","job-boards.greenhouse.io"], 20),
    ("content strategist remote LATAM Brazil",               ["boards.greenhouse.io","job-boards.greenhouse.io"], 15),
    ("creative lead remote LATAM Brazil",                    ["boards.greenhouse.io","job-boards.greenhouse.io"], 10),
    ("creative strategist remote LATAM",                     ["jobs.smartrecruiters.com"],                        10),
    ("content strategist remote LATAM",                      ["jobs.smartrecruiters.com"],                        10),
    ("creative lead remote LATAM",                           ["jobs.smartrecruiters.com"],                        10),
    ("creative strategist remote LATAM Brazil",              ["weworkremotely.com"],                              10),
    ("content strategist remote",                            ["weworkremotely.com"],                              10),
    ("creative strategist remote LATAM",                     ["remotive.com","himalayas.app"],                    15),
    ("content strategist remote LATAM",                      ["remotive.com","himalayas.app"],                    15),
    ("creative lead remote LATAM",                           ["remotive.com","himalayas.app"],                    10),
    ("brand strategist remote LATAM Brazil",                 ["jobs.lever.co","jobs.ashbyhq.com"],                10),
    ("head of content remote LATAM Brazil",                  ["jobs.lever.co","jobs.ashbyhq.com"],                10),
    ("creative strategist remote worldwide OR global",       ["jobs.lever.co","jobs.ashbyhq.com","job-boards.greenhouse.io"], 20),
    ("content strategist remote worldwide OR global",        ["jobs.lever.co","jobs.ashbyhq.com","job-boards.greenhouse.io"], 20),
    ("creative lead remote worldwide OR global",             ["jobs.lever.co","jobs.ashbyhq.com","job-boards.greenhouse.io"], 15),
    ("creative strategist remote LATAM Brazil",              ["apply.workable.com"],                              10),
    ("content strategist remote LATAM Brazil",               ["apply.workable.com"],                              10),
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
                time_range="week",
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
    if "workable.com"        in url: return "Workable"
    return "Outro"

# ── Extração com Claude (opcional) ───────────────────────────────────────────
EXTRACT_PROMPT = """Você vai receber resultados de busca de vagas de emprego (título + URL + snippet).

Para CADA resultado, determine se é uma vaga de **Creative Strategist ou Content Strategist** remota que aceita candidatos do Brasil/LATAM.

Retorne um JSON array. Cada item deve ter:
- "company": nome da empresa
- "role": título do cargo
- "url": URL exata da vaga
- "ats": plataforma ATS (Lever / Ashby / Greenhouse / SmartRecruiters / WWR / Remotive / Himalayas / Workable / Outro)
- "latam_friendly": true se menciona LATAM, Brazil, remote-anywhere, ou não restringe a US/EU

Inclua SOMENTE vagas de estratégia criativa ou de conteúdo: Creative Strategist, Content Strategist, Brand Strategist, Social Media Strategist, Head of Content, Content Director, Marketing Strategist, Creative Lead, Head of Creative, Creative Director. Exclua vagas de engineering, development, data science, finance, recruiting, product management, e design puro (UX/UI Designer, Graphic Designer).
Se não houver vagas válidas, retorne [].

Resultados de busca:
"""

def extract_with_claude(raw_results: list[dict]) -> list[dict] | None:
    """Tenta extrair via Claude API. Retorna None se indisponível."""
    if not ANTHROPIC_API_KEY:
        return None
    try:
        from anthropic import Anthropic
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
JOB_KEYWORDS = re.compile(
    r'\bcreative\s+strategist\b|\bcontent\s+strategist\b|\bbrand\s+strategist\b|'
    r'\bsocial\s+media\s+strategist\b|\bhead\s+of\s+content\b|\bcontent\s+director\b|'
    r'\bmarketing\s+strategist\b|\bcreative\s+strategy\b|\bcontent\s+strategy\b|'
    r'\bcreative\s+lead\b|\bhead\s+of\s+creative\b|\bcreative\s+director\b',
    re.IGNORECASE
)
EXCLUDE_KEYWORDS = re.compile(
    r'\bengineer\b|\bdeveloper\b|\bux\s+designer\b|\bui\s+designer\b|'
    r'\bgraphic\s+designer\b|\bdata\s+scientist\b|\baccountant\b|\brecruiter\b|'
    r'\bproduct\s+manager\b|\bsoftware\b',
    re.IGNORECASE
)

def extract_company_from_title(title: str, url: str) -> str:
    """Tenta extrair nome da empresa do título ou domínio."""
    for sep in [" – ", " - ", " | ", " at "]:
        if sep in title:
            parts = title.split(sep)
            for i, part in enumerate(parts):
                if JOB_KEYWORDS.search(part):
                    other = parts[1-i] if len(parts) == 2 else parts[-1]
                    return other.strip()
    domain = re.search(r'(?:jobs\.|boards\.|job-boards\.)([^./]+)', url)
    if domain:
        return domain.group(1).replace("-", " ").title()
    return "?"

def extract_with_regex(raw_results: list[dict]) -> list[dict]:
    """Extração heurística sem LLM."""
    vagas = []
    for r in raw_results:
        title   = r.get("title", "")
        url     = r.get("url", "")
        content = r.get("content", "")
        full_text = f"{title} {content}"

        if not JOB_KEYWORDS.search(full_text):
            continue
        if EXCLUDE_KEYWORDS.search(title):
            continue

        if not is_specific_job_url(url):
            print(f"    ⏭️  URL não é vaga específica: {url[:80]}", flush=True)
            continue

        company = extract_company_from_title(title, url)
        ats     = detect_ats(url)

        role = title
        for sep in [" – ", " - ", " | ", " at "]:
            if sep in title:
                parts = title.split(sep)
                for part in parts:
                    if JOB_KEYWORDS.search(part):
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
    "Remotive": "⚪", "Himalayas": "⚪", "Workable": "⚫", "Outro": "⚫",
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
        elif "Workable" in ats: ats = "Workable"
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
        f"# 🆕 Vagas Creative & Content Strategist Internacionais – {now}",
        "",
        f"> **Execução automática** | Busca em ATS internacionais (Lever, Ashby, Greenhouse, SmartRecruiters, WWR, Remotive, Workable)",
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
        ATS_ORDER = ["Lever", "Ashby", "Greenhouse", "SmartRecruiters", "WWR", "Remotive", "Himalayas", "Workable", "Outro"]
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
    """Spot-check a sample of historical URLs from existing .md files."""
    import random as _random

    url_re = re.compile(r'\[(?:Ver vaga|Aplicar|Apply)\]\((https?://[^)]+)\)')
    all_urls: set = set()
    for md_file in sorted(VAGAS_DIR.glob("vagas_*.md")):
        try:
            text = md_file.read_text(encoding="utf-8", errors="replace")
            all_urls.update(url_re.findall(text))
        except Exception:
            pass

    cache = BrokenCache(BROKEN_FILE)
    candidates = [u for u in all_urls if u not in cache.broken]
    if not candidates:
        return

    sample = _random.sample(candidates, min(30, len(candidates)))
    print(f"  🔁 Re-validando {len(sample)} URLs históricas...", flush=True)

    results = check_urls_parallel(sample)
    newly_broken = {u for u, dead in results.items() if dead}

    if newly_broken:
        for u in newly_broken:
            print(f"    💀 Histórico morto: {u[:80]}", flush=True)
        cache.add_broken_batch(newly_broken)
        cache.save()
        print(f"  ✅ {len(newly_broken)} links históricos mortos adicionados ao cache", flush=True)
    else:
        print(f"  ✅ Todos os {len(sample)} links históricos válidos", flush=True)


def main():
    VAGAS_DIR.mkdir(parents=True, exist_ok=True)

    history = load_history()
    prev_count = len(history)

    print(f"📂 Histórico: {prev_count} URLs conhecidas", flush=True)

    _revalidate_historical_urls()

    print("🔍 Buscando vagas...", flush=True)
    raw = search_all()
    print(f"📋 {len(raw)} resultados brutos obtidos", flush=True)

    print("🤖 Extraindo vagas estruturadas...", flush=True)
    all_vagas = extract_vagas(raw)

    normalized_vagas = []
    for v in all_vagas:
        url = normalize_url(v.get("url", ""))
        if not url:
            continue
        if not is_specific_job_url(url):
            print(f"    ⏭️  URL não é vaga específica: {url[:100]}", flush=True)
            continue
        v["url"] = url
        normalized_vagas.append(v)

    normalized_history = {normalize_url(u) for u in history}
    new_vagas = [v for v in normalized_vagas if v["url"] not in normalized_history]
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

    base = VAGAS_DIR / f"vagas_cs_{TODAY}.md"
    if base.exists():
        n = 2
        while (VAGAS_DIR / f"vagas_cs_{TODAY}_exec{n}.md").exists():
            n += 1
        out_path = VAGAS_DIR / f"vagas_cs_{TODAY}_exec{n}.md"
    else:
        out_path = base

    md = generate_markdown(new_vagas, prev_count)
    out_path.write_text(md, encoding="utf-8")
    print(f"💾 Salvo: {out_path.name}", flush=True)

    new_urls = {v["url"] for v in new_vagas if v.get("url")}
    history |= new_urls
    save_history(history)
    print(f"📚 Histórico atualizado: {len(history)} URLs", flush=True)

    print("🌐 Regenerando site...", flush=True)
    import subprocess
    result = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "generate_site.py")],
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
