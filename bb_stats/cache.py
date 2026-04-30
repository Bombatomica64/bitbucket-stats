"""Cache loading and saving."""

import json
from pathlib import Path
from typing import Any

CACHE_FILE = Path(__file__).parent / "bb_cache.json"


def cache_path(workspace: str, repo_slug: str) -> Path:
    """Return the cache file path for a workspace/repository pair.

    Returns:
        Cache file path scoped by workspace and repository slug.

    """
    return CACHE_FILE.parent / f"bb_cache_{workspace}_{repo_slug}.json"


def load_cache(workspace: str, repo_slug: str) -> tuple[list[dict], list[dict], list[dict], str] | None:
    """Load cached PR and commit data for a repository if it exists.

    Returns:
        A tuple of merged PRs, open PRs, commits, and fetch timestamp, or
        ``None`` when no cache file exists.

    """
    path = cache_path(workspace, repo_slug)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return data["merged"], data["open"], data.get("commits", []), data.get("fetched_at", "unknown")


def save_cache(workspace: str, repo_slug: str, snapshot: dict[str, Any]) -> None:
    """Persist a repository snapshot to the local JSON cache."""
    cache_path(workspace, repo_slug).write_text(json.dumps(snapshot))
