#!/usr/bin/env python3
"""
link_checker.py -- Módulo compartilhado de validação de links de vagas.
Usado por: generate_site.py, search_and_generate.py, check_links.py, clean_ashby.py.

Fornece:
  - is_dead_url(url)         -> bool (checagem individual)
  - check_urls_parallel(urls) -> dict {url: bool} (checagem em lote)
  - BrokenCache              -> classe para ler/escritar broken_links.json
"""
import json
import re
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ── Configuração ──────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}

DEAD_PHRASES = [
    "the job you requested was not found",
    "job not found",
    "job posting not found",
    "position not found",
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
    "vaga nao encontrada",
]

DEAD_URL_PATTERNS = ["/404", "?error=true", "job-not-found", "posting-not-found"]

_ASHBY_UUID_RE = re.compile(
    r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", re.I
)
_GH_JOB_RE = re.compile(r"greenhouse\.io/([^/]+)/jobs/(\d+)", re.I)
_GH_COMPANY_RE = re.compile(r"greenhouse\.io/([^/]+)", re.I)


# ── Ashby API check ───────────────────────────────────────────────────────────

def _is_dead_ashby(url: str) -> bool:
    uuid_m = _ASHBY_UUID_RE.search(url)
    if not uuid_m:
        return True  # company page = dead
    comp_m = re.search(r"ashbyhq\.com/([^/]+)", url)
    if not comp_m:
        return True
    api = (
        f"https://jobs.ashbyhq.com/api/non-posting-external/job/"
        f"{comp_m.group(1)}/{uuid_m.group(0)}"
    )
    try:
        req = urllib.request.Request(api, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            p = data.get("jobPosting") or data.get("job") or {}
            if isinstance(p, dict):
                return not p.get("isPublished", True)
            return False
    except urllib.error.HTTPError:
        pass  # API 404 doesn't mean the job page is dead — fall through to HTTP check
    except Exception:
        pass  # fall through to HTTP check
    return _is_dead_http(url)


# ── Greenhouse API check ──────────────────────────────────────────────────────

def _is_dead_greenhouse_api(url: str) -> bool:
    gh_m = _GH_JOB_RE.search(url)
    if not gh_m:
        return True  # company page / no job ID = dead
    company, job_id = gh_m.group(1), gh_m.group(2)
    api = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs/{job_id}"
    try:
        req = urllib.request.Request(api, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return not data.get("id")
    except urllib.error.HTTPError as e:
        return e.code in (404, 410, 403, 422)
    except Exception:
        pass  # fall through to HTTP check
    return False


# ── HTTP body/redirect check ──────────────────────────────────────────────────

def _is_dead_http(url: str) -> bool:
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=12) as resp:
            final = resp.geturl().lower()
            if any(p in final for p in DEAD_URL_PATTERNS):
                return True
            body = resp.read(12000).decode("utf-8", errors="ignore").lower()
            return any(p in body for p in DEAD_PHRASES)
    except urllib.error.HTTPError as e:
        return e.code in (404, 410, 403)
    except Exception:
        return False  # network error = assume alive


# ── API pública ────────────────────────────────────────────────────────────────

def is_dead_url(url: str) -> bool:
    """Retorna True se o link da vaga está morto/expirado."""
    if "ashbyhq.com" in url:
        return _is_dead_ashby(url)
    if "greenhouse.io" in url:
        if _is_dead_greenhouse_api(url):
            return True
    return _is_dead_http(url)


def check_urls_parallel(urls: list[str], max_workers: int = 15) -> dict[str, bool]:
    """Checa URLs em paralelo. Retorna {url: is_dead}."""
    results = {}
    if not urls:
        return results
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(is_dead_url, u): u for u in urls}
        for future in as_completed(futures):
            u = futures[future]
            try:
                results[u] = future.result()
            except Exception:
                results[u] = False  # assume alive on error
    return results


def is_specific_job_url(url: str) -> bool:
    """Retorna True se a URL aponta para uma vaga específica (não página de empresa)."""
    if re.search(r"/jobs/\d+", url):
        return True  # Greenhouse numeric ID
    if re.search(r"/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}", url, re.I):
        return True  # UUID (Lever, Ashby)
    if re.search(r"/view/[A-Za-z0-9_=-]+", url):
        return True  # Workable ID
    if re.search(r"/remote-jobs/[a-z0-9-]+", url):
        return True  # WWR slug
    if re.search(r"/jobs/[a-z0-9-]{10,}$", url):
        return True  # Remotive/Himalayas slug
    if re.search(r"/o/[a-z0-9-]+$", url):
        return True  # Recruitee slug
    if re.search(r"/positions/\d+", url):
        return True  # Careers site numeric
    if re.search(r"/j/[A-Za-z0-9]+", url):
        return True  # Workable apply link
    return False


# ── Cache de links quebrados ──────────────────────────────────────────────────

class BrokenCache:
    """Interface para broken_links.json. Garante atomicidade e consistência."""

    def __init__(self, path: Path):
        self.path = path
        self._broken: set[str] = set()
        self._ok: set[str] = set()
        self._checked_at: dict[str, str] = {}
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            raw = self.path.read_bytes().rstrip(b"\x00").decode("utf-8")
            data = json.loads(raw)
            self._broken = set(data.get("broken", []))
            self._ok = set(data.get("ok", []))
            self._checked_at = data.get("checked_at", {})
        except Exception:
            pass

    def save(self):
        data = {
            "broken": sorted(self._broken),
            "ok": sorted(self._ok - self._broken),
        }
        if self._checked_at:
            data["checked_at"] = dict(sorted(self._checked_at.items()))
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        tmp.replace(self.path)

    @property
    def broken(self) -> set[str]:
        return self._broken

    @property
    def ok(self) -> set[str]:
        return self._ok

    def is_broken(self, url: str) -> bool:
        from urllib.parse import unquote
        return url in self._broken or unquote(url) in self._broken

    def mark_broken(self, url: str):
        self._broken.add(url)
        self._ok.discard(url)
        self._checked_at.pop(url, None)

    def mark_ok(self, url: str, date_str: str):
        self._ok.add(url)
        self._broken.discard(url)
        self._checked_at[url] = date_str

    def add_broken_batch(self, urls: set[str]):
        for u in urls:
            self.mark_broken(u)

    def prune_stale(self, live_urls: set[str]):
        """Remove entries that no longer appear in any .md file."""
        self._ok &= live_urls
        self._checked_at = {
            u: v for u, v in self._checked_at.items() if u in live_urls
        }
