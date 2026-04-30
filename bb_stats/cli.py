"""CLI entrypoint and workflow orchestration for bb-stats."""

import argparse
import logging
import sys
from datetime import UTC, datetime

from rich.console import Console

from bb_stats.api import enrich_with_participants, fetch_all_prs, fetch_commits, set_session
from bb_stats.cache import load_cache, save_cache
from bb_stats.config import _configure, _load_config
from bb_stats.data import build_stats
from bb_stats.ui import BBStatsApp, SelectionApp

logger = logging.getLogger(__name__)
console = Console()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bb-stats",
        description="Terminal Bitbucket pull request and commit statistics viewer.",
    )
    parser.add_argument("-l", "--list", action="store_true", help="interactively pick workspace and repository")
    parser.add_argument("-r", "--refresh", action="store_true", help="force refresh cached data from the API")
    parser.add_argument("-w", "--workspace", default=None, help="Bitbucket workspace slug")
    parser.add_argument("-R", "--repo", default=None, help="Bitbucket repository slug")
    parser.add_argument("--configure", action="store_true", help="set up credentials in ~/.config/bb-stats/config.toml")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if args.configure:
        _configure()
        return

    cfg = _load_config()
    if not cfg["token"] or not cfg["email"]:
        logger.error(
            "Error: credentials not found. Set BITBUCKET/BITBUCKET_EMAIL env vars or run: bb-stats --configure",
        )
        sys.exit(1)

    set_session((cfg["email"], cfg["token"]))

    workspace = args.workspace
    repo_slug = args.repo

    if args.list or not workspace or not repo_slug:
        result = SelectionApp().run()
        if not result:
            return
        workspace, repo_slug = result

    cached = load_cache(workspace, repo_slug) if not args.refresh else None

    if cached:
        merged, open_prs, commits, fetched_at = cached
        console.print(f"[dim]Loaded from cache (fetched {fetched_at[:19]}). Use --refresh to update.[/dim]")
    else:
        console.print("[dim]Fetching merged PRs...[/dim]")
        merged = enrich_with_participants(workspace, repo_slug, fetch_all_prs(workspace, repo_slug, "MERGED"))
        console.print("[dim]Fetching open PRs...[/dim]")
        open_prs = enrich_with_participants(workspace, repo_slug, fetch_all_prs(workspace, repo_slug, "OPEN"))
        console.print("[dim]Fetching commits...[/dim]")
        commits = fetch_commits(workspace, repo_slug)
        fetched_at = datetime.now(UTC).isoformat()
        save_cache(
            workspace,
            repo_slug,
            {
                "fetched_at": fetched_at,
                "merged": merged,
                "open": open_prs,
                "commits": commits,
            },
        )
        console.print(
            f"[dim]Loaded {len(merged)} merged + {len(open_prs)} open + {len(commits)} commits. Cache saved.[/dim]",
        )

    try:
        reference_now = datetime.fromisoformat(fetched_at)
    except ValueError:
        reference_now = datetime.now(UTC)

    BBStatsApp(
        workspace,
        repo_slug,
        build_stats(merged, open_prs, commits, reference_now=reference_now),
        fetched_at,
    ).run()


if __name__ == "__main__":
    main()
