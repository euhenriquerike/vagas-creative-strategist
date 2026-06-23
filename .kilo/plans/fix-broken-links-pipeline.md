# Fix Broken Links Pipeline — Plan

## Problem

The generated `index.html` contains job links that lead to "Sorry, we couldn't find anything here" pages. The current validation system has **6 specific gaps** that allow dead links to pass through.

## Root Cause Analysis

### Gap 1: Ashby URLs completely skipped in validation
In `search_and_generate.py:65-66`, the `_is_dead_url()` function explicitly returns `False` for all `ashbyhq.com` URLs:
```python
if "ashbyhq.com" in url:
    return False
```
This means **all Ashby jobs bypass link validation entirely**. Currently 4 Ashby jobs in the index (`kraken.com`, `addi`, `hostinger`, `Agent`) — and these are **company-level pages, not specific job listings**. They're landing pages that show "all jobs" not a specific position.

### Gap 2: No validation at Markdown→HTML generation time
`generate_site.py` reads all `vagas_*.md` files and generates `index.html`. It filters against `broken_links.json` (lines 92-109), but **only checks URLs that are already known broken**. There is no HTTP validation during site generation. If a link died between the last `check_links.py` run and site generation, it gets through.

### Gap 3: `search_and_generate.py` validation only runs on NEW jobs
The `filter_live_vagas()` function (line 95-127) only validates jobs that are new (not in `url_history.json`). Jobs that were added to Markdown files in prior runs but have since expired are **never re-validated**.

### Gap 4: Missing dead-phrase patterns
The `DEAD_PATTERNS` list (lines 39-51) doesn't cover all common "job not found" variants. Missing patterns include:
- "page not found"
- "404"
- "this posting has been closed"
- "application is closed"
- "position is no longer accepting"
- "this role has been filled"
- "no longer available"
- Common redirect-to-error patterns (e.g., `?error=true`, `/404`)

### Gap 5: URL validation has short timeout and permissive fallback
`_is_dead_url()` (line 76) returns `False` on any network exception — treating timeouts, DNS failures, connection refused, etc. as "alive". The 8-second timeout may also be too short for some ATS platforms, causing false "alive" results.

### Gap 6: Non-ATS URLs pass without structural validation
URLs from `builtin.com`, `remoterocketship.com`, `recruitee.com`, `teamtailor.com` appear in the index but aren't from any monitored ATS. These aggregator/redirect URLs are more fragile and have no URL-structure validation.

---

## Solution — 6 Changes

### Change 1: Validate Ashby URLs via Ashby API
**File:** `search_and_generate.py`

Replace the Ashby skip with actual validation. Ashby has a public JSON API at `https://jobs.ashbyhq.com/api/non-posting-external/jobs` that returns structured job data. For each Ashby URL:
- Extract the job UUID from the URL (pattern: `/{company}/{uuid}`)
- Query the Ashby API to check if the job exists and is published
- If no UUID in URL (company page only), **reject it** — company pages are not job listings

```python
def _is_dead_ashby(url: str) -> bool:
    """Check Ashby job via their public API."""
    import urllib.request, json as _json
    uuid_match = re.search(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', url, re.I)
    if not uuid_match:
        return True  # No UUID = company page, not a job listing
    job_id = uuid_match.group(0)
    company_match = re.search(r'ashbyhq\.com/([^/]+)', url)
    if not company_match:
        return True
    company = company_match.group(1)
    api_url = f"https://jobs.ashbyhq.com/api/non-posting-external/job/{company}/{job_id}"
    try:
        req = urllib.request.Request(api_url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read().decode())
            return not data.get("jobPosting", {}).get("isPublished", False)
    except Exception:
        return True  # If API fails, assume dead
```

Update `_is_dead_url()` to call this instead of `return False`.

### Change 2: Add missing dead-phrase patterns
**File:** `search_and_generate.py`

Expand `DEAD_PATTERNS` (line 39-51) with additional patterns:
```python
DEAD_PATTERNS = [
    # ... existing patterns ...
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
]
```

Also add URL-path-based dead detection:
```python
DEAD_URL_PATTERNS = ["/404", "?error=true", "job-not-found", "posting-not-found"]
```

And check `final_url` for redirect-to-error patterns (similar to what `check_links.py` already does).

### Change 3: Add a re-validation step in `generate_site.py`
**File:** `generate_site.py`

After collecting all jobs from Markdown files and filtering against `broken_links.json`, add a **live HTTP re-check** for all remaining URLs. This catches links that died between their creation and now.

Add a new function `_validate_links_live(jobs)` that:
1. Uses `ThreadPoolExecutor` with 15 workers (same as `search_and_generate.py`)
2. Checks each URL via HTTP GET with dead-phrase detection
3. Updates `broken_links.json` with any newly found broken links
4. Returns only the live jobs

Call this function right after the existing `broken_links.json` filtering (after line 109), before building `all_jobs` and `uiux_jobs`.

### Change 4: Improve `_is_dead_url()` robustness
**File:** `search_and_generate.py`

- Increase timeout from 8s to 12s (matching `check_links.py`)
- Add redirect-to-error detection (check `resp.geturl()` for error patterns, like `check_links.py` does)
- For HTTP errors, treat **5xx** as "unknown" (return False) but **404, 410, 403** as dead

```python
def _is_dead_url(url: str) -> bool:
    if "ashbyhq.com" in url:
        return _is_dead_ashby(url)
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
        return False
```

### Change 5: Reject non-ATS URLs that lack specific job identifiers
**File:** `search_and_generate.py` (in `extract_with_regex`)

Add URL-structure validation to the regex extractor. Reject URLs that don't contain a specific job identifier (UUID, numeric ID, or slug). This filters out:
- Company-level pages (`ashbyhq.com/company` without UUID)
- Search/browse pages (`builtin.com/jobs/.../search/...`)
- Generic category pages (`greenhouse.io/company` without `/jobs/ID`)

```python
def _is_specific_job_url(url: str) -> bool:
    """Reject URLs that are company pages, search pages, or category listings."""
    # Must contain a job-specific identifier
    if re.search(r'/jobs/\d+', url): return True          # Greenhouse numeric ID
    if re.search(r'/[a-f0-9-]{36}', url): return True     # UUID (Lever, Ashby)
    if re.search(r'/view/[A-Za-z0-9]+', url): return True # Workable ID
    if re.search(r'/remote-jobs/\S+', url): return True    # WWR slug
    if re.search(r'/jobs/[a-z0-9-]+$', url): return True   # Remotive/Himalayas slug
    if re.search(r'/o/[a-z0-9-]+$', url): return True      # Recruitee slug
    return False
```

Use this in `extract_with_regex()` to skip results that don't have a specific job URL.

### Change 6: Periodic re-validation of ALL URLs in Markdown files
**File:** `search_and_generate.py`

At the beginning of `main()`, before the Tavily search, add a step that:
1. Scans all existing `vagas_*.md` files for URLs
2. Picks a random sample of ~20 URLs (or all if fewer)
3. Validates them via `_is_dead_url()`
4. Updates `broken_links.json` with any new broken ones
5. This ensures stale links are caught even on days when no new jobs are found

This is a lightweight background cleanup (20 URLs × 12s timeout = ~15s with parallelism).

---

## Files Modified

| File | Changes |
|------|---------|
| `search_and_generate.py` | Changes 1, 2, 4, 5, 6 — main pipeline hardening |
| `generate_site.py` | Change 3 — live link validation at HTML generation time |

## Testing

1. Run `python search_and_generate.py` locally and verify:
   - Ashby URLs are validated (not skipped)
   - Non-job URLs (company pages, search pages) are rejected
   - New dead phrases are detected
   - The periodic re-validation step runs and catches known-broken URLs

2. Run `python generate_site.py` and verify:
   - All URLs in the generated `index.html` pass HTTP validation
   - `broken_links.json` is updated with any newly detected broken links
   - The site renders correctly with only live jobs

3. Manually click 5-10 random job links from `index.html` to verify they lead to actual job postings (not "not found" pages).

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| HTTP validation adds ~2-3 min to pipeline | Use 15 parallel workers; only validate ~20 random historical URLs + new batch |
| Ashby API may change/become unavailable | Fallback: if API fails, reject the URL (conservative) |
| Some ATS block automated requests | 12s timeout + User-Agent header; false negatives (marking alive when dead) are acceptable since the re-check runs periodically |
| Over-aggressive filtering removes valid jobs | The `_is_specific_job_url()` check is permissive — only rejects URLs with zero job identifiers |
