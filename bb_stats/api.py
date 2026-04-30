"""Bitbucket API client helpers and request/session plumbing."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.adapters import HTTPAdapter
from rich.console import Console
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)
console = Console()

DEFAULT_TIMEOUT = 30
_SESSION: dict[str, requests.Session | None] = {"value": None}


def get_session() -> requests.Session:
    """Return the shared HTTP session used for API requests.

    Returns:
        The shared requests session used by all API helpers.

    """
    session = _SESSION["value"]
    if session is None:
        session = requests.Session()
        _SESSION["value"] = session
    return session


def set_session(auth: tuple[str, str]) -> requests.Session:
    """Create and store a configured session with retry and auth settings.

    Returns:
        A configured requests session with auth and retry policy applied.

    """
    session = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.auth = auth
    _SESSION["value"] = session
    return session


def _paginate(url: str, params: dict | None = None) -> list[dict]:
    results = []
    p = params or {}
    session = get_session()
    while url:
        resp = session.get(url, params=p, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        results.extend(data.get("values", []))
        url = data.get("next")
        p = {}
    return results


def fetch_workspaces() -> list[dict]:
    results = _paginate("https://api.bitbucket.org/2.0/user/workspaces", {"pagelen": 50})
    return [w.get("workspace", w) for w in results]


def fetch_repos(workspace: str) -> list[dict]:
    resp = get_session().get(
        f"https://api.bitbucket.org/2.0/repositories/{workspace}",
        params={"pagelen": 40, "sort": "-updated_on"},
        timeout=DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("values", [])


def fetch_all_prs(workspace: str, repo_slug: str, state: str) -> list[dict]:
    url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo_slug}/pullrequests"
    return _paginate(url, {"state": state, "pagelen": 50})


def fetch_pr_detail(workspace: str, repo_slug: str, pr_id: int) -> dict:
    resp = get_session().get(
        f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}",
        timeout=DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_commits(workspace: str, repo_slug: str) -> list[dict]:
    url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo_slug}/commits"
    return _paginate(
        url,
        {
            "pagelen": 100,
            "fields": "next,values.hash,values.date,values.author.raw,values.author.user.display_name",
        },
    )


def enrich_with_participants(workspace: str, repo_slug: str, prs: list[dict]) -> list[dict]:
    console.print(f"[dim]Fetching full details for {len(prs)} PRs...[/dim]")
    results: dict[int, dict] = {}
    summaries = {pr["id"]: pr for pr in prs}
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(fetch_pr_detail, workspace, repo_slug, pr["id"]): pr["id"] for pr in prs}
        for i, future in enumerate(as_completed(futures), 1):
            pr_id = futures[future]
            try:
                results[pr_id] = future.result()
            except requests.RequestException as exc:
                logger.warning("Warning: failed to fetch PR details for #%s: %s", pr_id, exc)
                results[pr_id] = summaries[pr_id]
            if i % 50 == 0 or i == len(prs):
                console.print(f"[dim]  {i}/{len(prs)}[/dim]")
    return [results[pr["id"]] for pr in prs]
