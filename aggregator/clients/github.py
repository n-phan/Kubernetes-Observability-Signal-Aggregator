"""
GitHub client for code search and direct file linking.

Two strategies run in sequence:
  1. Stack frame linking — if log lines contain file:line references,
     construct direct blob URLs without needing to search.
  2. Code search — use the GitHub Search API to find files matching
     the LLM-provided search terms (function names, error strings, etc.)

The GitHub Search API has a rate limit of 30 req/min unauthenticated,
10 req/s authenticated. Always use a token in production.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import quote

import httpx

from aggregator.core.rca_analyzer import extract_stack_frames
from aggregator.models.rca import CodeReference, RCAResult
from aggregator.models.signals import LogsSignal

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
GITHUB_WEB = "https://github.com"

# Only search for these file types — prevents noise from docs/configs
SEARCHABLE_EXTENSIONS = {
    ".py", ".go", ".js", ".ts", ".java", ".rb", ".rs", ".cpp", ".cs", ".kt"
}


class GitHubLinker:
    """
    Enriches an RCAResult with direct links to GitHub source code.
    """

    def __init__(
        self,
        token: str | None = None,
        repo: str | None = None,
        default_branch: str = "main",
        path_prefix: str | None = None,
    ) -> None:
        """
        token          — GitHub personal access token (strongly recommended)
        repo           — "owner/repo" slug, e.g. "my-org/payment-service"
        default_branch — branch to link to (default: "main")
        path_prefix    — optional prefix to prepend to extracted file paths, e.g. "demo/service-b"
        """
        self._repo = repo
        self._branch = default_branch
        self._path_prefix = path_prefix.rstrip("/") + "/" if path_prefix else ""
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(
            base_url=GITHUB_API,
            headers=headers,
            timeout=15.0,
        )

    async def enrich(
        self,
        rca: RCAResult,
        logs: LogsSignal,
        path_prefix: str | None = None,
    ) -> RCAResult:
        """
        Attach CodeReferences to an RCAResult using two strategies.
        Modifies rca in-place and returns it.

        path_prefix — per-call override for the file path prefix, e.g. "demo/service-d".
                      Takes precedence over the instance-level prefix set at construction.
        """
        if not self._repo:
            logger.debug("No GitHub repo configured — skipping code linking")
            return rca

        # Resolve the prefix to use for this call
        resolved_prefix = (
            path_prefix.rstrip("/") + "/"
            if path_prefix is not None
            else self._path_prefix
        )

        refs: list[CodeReference] = []

        # Strategy 1: direct stack frame links (no API call needed)
        stack_refs = self._link_stack_frames(logs, resolved_prefix)
        refs.extend(stack_refs)
        logger.info("GitHub linker — stack frame refs: %d", len(stack_refs))

        # Strategy 2: GitHub code search for LLM-provided terms
        if rca.github_search_terms:
            search_refs = await self._search_code(rca.github_search_terms)
            # Deduplicate by URL
            existing_urls = {r.url for r in refs}
            for ref in search_refs:
                if ref.url not in existing_urls:
                    refs.append(ref)
                    existing_urls.add(ref.url)
            logger.debug("Code search: %d new refs", len(search_refs))

        rca.code_references = refs
        return rca

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Strategy 1: stack frame → direct blob URL
    # ------------------------------------------------------------------

    def _link_stack_frames(
        self, logs: LogsSignal, path_prefix: str = ""
    ) -> list[CodeReference]:
        """
        Parse stack traces from log lines and turn each file reference
        into a direct GitHub blob URL.

        path_prefix — already-resolved prefix string (with trailing slash) to
                      prepend to each extracted filename.
        """
        if not self._repo:
            return []

        frames = extract_stack_frames(logs.lines)
        refs: list[CodeReference] = []

        for filepath, lineno in frames:
            # Normalise the path — strip leading slashes or go module prefixes
            clean = _normalise_path(filepath)
            if not _has_searchable_ext(clean):
                continue

            full_path = f"{path_prefix}{clean}" if path_prefix else clean
            url = _blob_url(self._repo, self._branch, full_path, lineno)
            refs.append(
                CodeReference(
                    repo=self._repo,
                    path=full_path,
                    url=url,
                    line_number=lineno,
                    relevance=f"Line {lineno} in {clean} — referenced in error stack trace",
                )
            )

        return refs

    # ------------------------------------------------------------------
    # Strategy 2: GitHub code search API
    # ------------------------------------------------------------------

    async def _search_code(self, terms: list[str]) -> list[CodeReference]:
        """
        Run up to 3 of the most specific search terms through the
        GitHub code search API and return matching file references.
        """
        refs: list[CodeReference] = []
        # Prefer longer terms — they're more specific
        sorted_terms = sorted(terms, key=len, reverse=True)[:3]

        for term in sorted_terms:
            try:
                new_refs = await self._search_one(term)
                refs.extend(new_refs)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 403:
                    logger.warning("GitHub rate limit hit — stopping code search")
                    break
                logger.warning("GitHub search error for '%s': %s", term, exc)
            except Exception as exc:
                logger.warning("GitHub search error for '%s': %s", term, exc)

        logger.info("GitHub linker — code search refs: %d", len(refs))
        return refs

    async def _search_one(self, term: str) -> list[CodeReference]:
        """Execute one GitHub code search query."""
        query = f"{term} repo:{self._repo}"
        resp = await self._client.get(
            "/search/code",
            params={"q": query, "per_page": 5},
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        logger.info("GitHub search '%s': %d item(s)", term, len(items))

        refs: list[CodeReference] = []
        for item in items:
            path: str = item.get("path", "")
            if not _has_searchable_ext(path):
                continue

            # Get a small snippet from the text_matches if available
            snippet: str | None = None
            for match in item.get("text_matches", []):
                fragment = match.get("fragment", "")
                if fragment:
                    snippet = fragment[:200].strip()
                    break

            url = _blob_url(self._repo, self._branch, path)
            refs.append(
                CodeReference(
                    repo=self._repo,
                    path=path,
                    url=url,
                    snippet=snippet,
                    relevance=f"Matched GitHub code search: '{term}'",
                )
            )

        return refs


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _blob_url(repo: str, branch: str, path: str, line: int | None = None) -> str:
    """Construct a GitHub blob URL, optionally anchored to a line number."""
    base = f"{GITHUB_WEB}/{repo}/blob/{branch}/{path}"
    return f"{base}#L{line}" if line else base


def _normalise_path(raw: str) -> str:
    """Strip leading slashes, workspace prefixes, and Go module paths."""
    path = raw.lstrip("/")
    # Remove common build system prefixes: /workspace/, /app/, /home/runner/...
    for prefix in ("workspace/", "app/", "home/runner/work/", "github/workspace/"):
        if path.startswith(prefix):
            path = path[len(prefix):]
            break
    # For Go: strip the module path up to the first slash that looks like a package
    if ".go" in path and "/" in path:
        parts = path.split("/")
        # Heuristic: drop initial segments that look like module domain (contain dots)
        while parts and ("." in parts[0]):
            parts.pop(0)
        path = "/".join(parts)
    return path


def _has_searchable_ext(path: str) -> bool:
    return any(path.endswith(ext) for ext in SEARCHABLE_EXTENSIONS)