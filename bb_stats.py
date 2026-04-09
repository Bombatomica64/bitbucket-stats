#!/usr/bin/env python3
# Copyright (c) 2026. All rights reserved.
"""Terminal Bitbucket pull request and commit statistics viewer."""

# /// script
# requires-python = ">=3.11"
# dependencies = ["requests", "rich", "python-dotenv", "textual>=0.80.0"]
# ///

import json
import logging
import os
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median
from typing import Any, ClassVar

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from rich.console import Console
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    LoadingIndicator,
    Static,
    TabbedContent,
    TabPane,
)
from urllib3.util.retry import Retry

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

load_dotenv()

WORKSPACE = "tbdsrl"
REPO_SLUG = "price-ninja-top-10"

TOKEN = os.environ.get("BITBUCKET")
EMAIL = os.environ.get("BITBUCKET_EMAIL")
if not TOKEN or not EMAIL:
    logger.info("Error: BITBUCKET and BITBUCKET_EMAIL env vars must be set")
    sys.exit(1)

AUTH = (EMAIL, TOKEN)
CACHE_FILE = Path(__file__).parent / "bb_cache.json"
console = Console()
DEFAULT_TIMEOUT = 30


def _build_session() -> requests.Session:
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
    session.auth = AUTH
    return session


SESSION = _build_session()


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


def _paginate(url: str, params: dict | None = None) -> list[dict]:
    results = []
    p = params or {}
    while url:
        resp = SESSION.get(url, params=p, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        results.extend(data.get("values", []))
        url = data.get("next")
        p = {}
    return results


def fetch_workspaces() -> list[dict]:
    """Return visible Bitbucket workspaces for the authenticated user.

    Returns:
        Workspace payloads from the Bitbucket API.

    """
    return _paginate("https://api.bitbucket.org/2.0/workspaces", {"pagelen": 50, "sort": "-updated_on"})


def fetch_repos(workspace: str) -> list[dict]:
    """Return the most recently updated repositories in a workspace.

    Returns:
        Repository payloads from the Bitbucket API.

    """
    resp = SESSION.get(
        f"https://api.bitbucket.org/2.0/repositories/{workspace}",
        params={"pagelen": 40, "sort": "-updated_on"},
        timeout=DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("values", [])


def fetch_all_prs(workspace: str, repo_slug: str, state: str) -> list[dict]:
    """Return pull requests for a repository filtered by state.

    Returns:
        Pull request payloads matching the requested state.

    """
    url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo_slug}/pullrequests"
    return _paginate(url, {"state": state, "pagelen": 50})


def fetch_pr_detail(workspace: str, repo_slug: str, pr_id: int) -> dict:
    """Return the full pull request payload for a single PR.

    Returns:
        The expanded pull request payload from Bitbucket.

    """
    resp = SESSION.get(
        f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}",
        timeout=DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_commits(workspace: str, repo_slug: str) -> list[dict]:
    """Return commit history with the fields needed for author and merge timing stats.

    Returns:
        Commit payloads containing hash, date, and author fields.

    """
    url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo_slug}/commits"
    return _paginate(
        url,
        {
            "pagelen": 100,
            "fields": "next,values.hash,values.date,values.author.raw,values.author.user.display_name",
        },
    )


def commit_author(commit: dict) -> str:
    """Return a stable display name for a commit author.

    Returns:
        The best available author display name.

    """
    user = commit.get("author", {}).get("user")
    if user:
        return user.get("display_name", "Unknown")
    raw = commit.get("author", {}).get("raw", "Unknown")
    return raw.split("<")[0].strip() if "<" in raw else raw


def pr_author_name(pr: dict) -> str:
    """Return a PR author's display name with a safe fallback.

    Returns:
        A display name or ``"Unknown"`` when it is missing.

    """
    return pr.get("author", {}).get("display_name", "Unknown")


def pr_branch_name(pr: dict) -> str:
    """Return a PR destination branch name with a safe fallback.

    Returns:
        A destination branch name or ``"Unknown"`` when it is missing.

    """
    return pr.get("destination", {}).get("branch", {}).get("name", "Unknown")


def pr_title(pr: dict, max_len: int = 52) -> str:
    """Return a truncated PR title with a safe fallback.

    Returns:
        A title suitable for table display.

    """
    return pr.get("title", "Untitled")[:max_len]


def reviewer_name(participant: dict) -> str | None:
    """Return a participant reviewer display name when present.

    Returns:
        The reviewer display name, or ``None`` if it is absent.

    """
    user = participant.get("user") or {}
    return user.get("display_name")


def enrich_with_participants(workspace: str, repo_slug: str, prs: list[dict]) -> list[dict]:
    """Expand PR summaries with detail payloads, falling back on transient failures.

    Returns:
        A list of PR payloads aligned to the input order.

    """
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


# ---------------------------------------------------------------------------
# Cache — per workspace/repo
# ---------------------------------------------------------------------------


def cache_path(workspace: str, repo_slug: str) -> Path:
    """Return the cache file path for a workspace/repository pair.

    Returns:
        The JSON cache path for the selected repository.

    """
    return CACHE_FILE.parent / f"bb_cache_{workspace}_{repo_slug}.json"


def load_cache(workspace: str, repo_slug: str) -> tuple[list[dict], list[dict], list[dict], str] | None:
    """Load cached PR and commit data for a repository if it exists.

    Returns:
        The cached merged PRs, open PRs, commits, and fetch timestamp, or ``None``.

    """
    path = cache_path(workspace, repo_slug)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return data["merged"], data["open"], data.get("commits", []), data.get("fetched_at", "unknown")


def save_cache(workspace: str, repo_slug: str, snapshot: dict[str, Any]) -> None:
    """Persist a repository snapshot to the local JSON cache."""
    cache_path(workspace, repo_slug).write_text(json.dumps(snapshot))


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def _matching_commit_date(commit_dates: dict[str, datetime], commit_hash: str | None) -> datetime | None:
    if not commit_hash:
        return None
    if commit_hash in commit_dates:
        return commit_dates[commit_hash]
    for known_hash, dt in commit_dates.items():
        if known_hash.startswith(commit_hash) or commit_hash.startswith(known_hash):
            return dt
    return None


def pr_age_days(
    pr: dict,
    reference_now: datetime | None = None,
    merge_commit_dates: dict[str, datetime] | None = None,
) -> float:
    """Return PR age in days using cached snapshot time for open PRs.

    Returns:
        The PR age expressed in fractional days.

    """
    created = datetime.fromisoformat(pr["created_on"])
    if pr["state"] == "OPEN":
        end = reference_now or datetime.now(UTC)
    else:
        merge_hash = pr.get("merge_commit", {}).get("hash")
        # Bitbucket's PR payload here does not expose a direct merged timestamp.
        # We infer merge time from the merge commit date when available, then fall
        # back to updated_on for older or partial payloads.
        end = _matching_commit_date(merge_commit_dates or {}, merge_hash) or datetime.fromisoformat(pr["updated_on"])
    return max(0.0, (end - created).total_seconds() / 86400)


def _build_commit_stats(commits: list[dict] | None) -> dict[str, Any]:
    """Aggregate commit-centric charts and author counts.

    Returns:
        A mapping of commit chart series and aggregate counters.

    """
    commit_authors: dict[str, int] = defaultdict(int)
    commit_weekly: dict[str, int] = defaultdict(int)
    commit_monthly: dict[str, int] = defaultdict(int)
    commit_dow: dict[str, int] = defaultdict(int)
    commit_hour: dict[int, int] = defaultdict(int)
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for commit in commits or []:
        author = commit_author(commit)
        dt = datetime.fromisoformat(commit["date"])
        commit_authors[author] += 1
        commit_weekly[dt.strftime("%Y-W%W")] += 1
        commit_monthly[dt.strftime("%Y-%m")] += 1
        commit_dow[days[dt.weekday()]] += 1
        commit_hour[dt.hour] += 1

    return {
        "commit_authors": dict(sorted(commit_authors.items(), key=lambda item: -item[1])),
        "commit_weekly": dict(sorted(commit_weekly.items())),
        "commit_monthly": dict(sorted(commit_monthly.items())),
        "commit_dow": {day: commit_dow[day] for day in days},
        "commit_hour": dict(sorted(commit_hour.items())),
        "total_commits": len(commits or []),
    }


def build_stats(
    merged: list[dict],
    open_prs: list[dict],
    commits: list[dict] | None = None,
    reference_now: datetime | None = None,
) -> dict:
    """Build the full dataset consumed by the Textual UI.

    Returns:
        A stats mapping ready for the Textual application.

    """
    commit_dates = {
        commit["hash"]: datetime.fromisoformat(commit["date"])
        for commit in commits or []
        if commit.get("hash") and commit.get("date")
    }
    weekly: dict[str, int] = defaultdict(int)
    monthly: dict[str, int] = defaultdict(int)
    for pr in merged:
        merge_dt = _matching_commit_date(commit_dates, pr.get("merge_commit", {}).get("hash"))
        dt = merge_dt or datetime.fromisoformat(pr["updated_on"])
        weekly[dt.strftime("%Y-W%W")] += 1
        monthly[dt.strftime("%Y-%m")] += 1

    author_prs: dict[str, list[dict]] = defaultdict(list)
    for pr in merged:
        author_prs[pr_author_name(pr)].append(pr)

    reviewer_counts: dict[str, dict] = defaultdict(lambda: {"reviewed": 0, "approved": 0})
    for pr in merged:
        for p in pr.get("participants", []):
            if p.get("role") == "REVIEWER":
                name = reviewer_name(p)
                if not name:
                    continue
                reviewer_counts[name]["reviewed"] += 1
                if p.get("approved"):
                    reviewer_counts[name]["approved"] += 1

    branch_counts: dict[str, list[float]] = defaultdict(list)
    for pr in merged:
        branch_counts[pr_branch_name(pr)].append(pr_age_days(pr, merge_commit_dates=commit_dates))

    stats = {
        "merged": merged,
        "open": open_prs,
        "merged_ages": [pr_age_days(pr, merge_commit_dates=commit_dates) for pr in merged],
        "weekly": dict(sorted(weekly.items())),
        "monthly": dict(sorted(monthly.items())),
        "author_prs": dict(author_prs),
        "reviewer_counts": dict(reviewer_counts),
        "branch_counts": dict(branch_counts),
        "merge_commit_dates": commit_dates,
        "reference_now": reference_now or datetime.now(UTC),
    }
    stats.update(_build_commit_stats(commits))
    return stats


def bar_chart(data: dict[str, int], width: int = 40, last_n: int = 16) -> str:
    """Render a fixed-width unicode bar chart from a numeric series.

    Returns:
        A multiline string chart for terminal display.

    """
    items = list(data.items())[-last_n:]
    if not items:
        return "No data"
    max_val = max(v for _, v in items)
    lines = []
    for key, val in items:
        bar_len = int((val / max_val) * width) if max_val else 0
        lines.append(f"  {key[-7:]:>7} │{'█' * bar_len:<{width}} {val}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Selection TUI
# ---------------------------------------------------------------------------


class ItemSelectScreen(Screen[str | None]):
    """Filterable list. Dismisses with selected value or None on Escape."""

    BINDINGS: ClassVar[tuple[Binding, ...]] = (Binding("escape", "dismiss(None)", "Back"),)

    def __init__(self, title: str, items: list[tuple[str, str]]) -> None:
        """Store the picker title and available items."""
        super().__init__()
        self._title = title
        self._all_items = items
        self._visible: list[tuple[str, str]] = list(items)

    def compose(self) -> ComposeResult:
        """Compose the filter input and selectable list.

        Yields:
            The widgets used by the filterable picker screen.

        """
        yield Header()
        yield Label(f" {self._title}", id="sel-title")
        yield Input(placeholder="Type to filter...", id="sel-input")
        yield ListView(id="sel-list")
        yield Footer()

    def on_mount(self) -> None:
        """Render the initial item set and focus the filter input."""
        self._render_list(self._all_items)
        self.query_one(Input).focus()

    def _render_list(self, items: list[tuple[str, str]]) -> None:
        self._visible = list(items)
        lv = self.query_one(ListView)
        lv.clear()
        for display, _ in items:
            lv.append(ListItem(Label(display)))

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter visible items as the query changes."""
        q = event.value.lower()
        self._render_list([(d, v) for d, v in self._all_items if q in d.lower()])

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Dismiss the screen with the selected value."""
        idx = event.list_view.index
        if idx is not None and 0 <= idx < len(self._visible):
            self.dismiss(self._visible[idx][1])


class SelectionApp(App[tuple[str, str] | None]):
    """Workspace → repo picker."""

    CSS = """
    #sel-title { padding: 1 2; color: $accent; text-style: bold; }
    #sel-input { margin: 0 1; }
    ListView { height: 1fr; }
    LoadingIndicator { height: 1fr; }
    """

    def compose(self) -> ComposeResult:  # noqa: PLR6301
        """Compose the loading shell used while fetching options.

        Yields:
            The widgets used by the selection app shell.

        """
        yield Header()
        yield LoadingIndicator()
        yield Footer()

    def on_mount(self) -> None:
        """Kick off the workspace fetch on startup."""
        self._load_workspaces()

    @work(thread=True)
    def _load_workspaces(self) -> None:
        workspaces = fetch_workspaces()
        items = [(f"{w.get('name', w['slug'])}  ({w['slug']})", w["slug"]) for w in workspaces]
        self.call_from_thread(
            self.push_screen, ItemSelectScreen("Select workspace", items), self._on_workspace_selected
        )

    def _on_workspace_selected(self, workspace: str | None) -> None:
        if workspace is None:
            self.exit(None)
            return
        self._workspace = workspace
        self._load_repos(workspace)

    @work(thread=True)
    def _load_repos(self, workspace: str) -> None:
        repos = fetch_repos(workspace)
        items = [(f"{r.get('name', r['slug'])}  ({r['slug']})", r["slug"]) for r in repos]
        self.call_from_thread(self.push_screen, ItemSelectScreen("Select repository", items), self._on_repo_selected)

    def _on_repo_selected(self, repo: str | None) -> None:
        if repo is None:
            self.exit(None)
            return
        self.exit((self._workspace, repo))


# ---------------------------------------------------------------------------
# Stats TUI
# ---------------------------------------------------------------------------


class BBStatsApp(App):
    """Display Bitbucket PR and commit statistics in a tabbed Textual UI."""

    CSS = """
    Screen { background: $surface; }
    DataTable { height: 1fr; }
    Static.chart { padding: 1 2; height: 1fr; overflow-y: auto; }
    Static.overview { padding: 2 4; }
    #commits { height: 1fr; }
    #commits > Vertical { height: 1fr; }
    #commits-charts { height: 1fr; overflow-y: auto; padding: 1 2; }
    #commits-table { height: 1fr; max-height: 12; }
    """

    BINDINGS: ClassVar[tuple[Binding, ...]] = (
        Binding("q", "quit", "Quit"),
        Binding("1", "switch_tab('overview')", "Overview"),
        Binding("2", "switch_tab('activity')", "Activity"),
        Binding("3", "switch_tab('open')", "Open PRs"),
        Binding("4", "switch_tab('authors')", "Authors"),
        Binding("5", "switch_tab('reviews')", "Reviews"),
        Binding("6", "switch_tab('branches')", "Branches"),
        Binding("7", "switch_tab('slowest')", "Slowest"),
        Binding("8", "switch_tab('commits')", "Commits"),
    )

    def __init__(self, workspace: str, repo_slug: str, stats: dict, fetched_at: str = "") -> None:
        """Store repository metadata and the precomputed stats payload."""
        super().__init__()
        self.workspace = workspace
        self.repo_slug = repo_slug
        self.stats = stats
        self.fetched_at = fetched_at

    def compose(self) -> ComposeResult:  # noqa: PLR6301
        """Compose the tabbed stats layout.

        Yields:
            The widgets used by the stats application.

        """
        yield Header(show_clock=True)
        with TabbedContent(initial="overview"):
            with TabPane("Overview [1]", id="overview"):
                yield Static(id="overview-static", classes="overview")
            with TabPane("Activity [2]", id="activity"):
                yield Static(id="activity-static", classes="chart")
            with TabPane("Open PRs [3]", id="open"):
                yield DataTable(id="open-table", zebra_stripes=True)
            with TabPane("Authors [4]", id="authors"):
                yield DataTable(id="authors-table", zebra_stripes=True)
            with TabPane("Reviews [5]", id="reviews"):
                yield DataTable(id="reviews-table", zebra_stripes=True)
            with TabPane("Branches [6]", id="branches"):
                yield DataTable(id="branches-table", zebra_stripes=True)
            with TabPane("Slowest [7]", id="slowest"):
                yield DataTable(id="slowest-table", zebra_stripes=True)
            with TabPane("Commits [8]", id="commits"), Vertical():
                yield Static(id="commits-charts", classes="chart")
                yield DataTable(id="commits-table", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        """Populate all tables and charts once the UI mounts."""
        self._populate_overview()
        self._populate_activity()
        self._populate_open_prs()
        self._populate_authors()
        self._populate_reviews()
        self._populate_branches()
        self._populate_slowest()
        self._populate_commits()

    def _populate_overview(self) -> None:
        s = self.stats
        ages = s["merged_ages"]
        open_prs = s["open"]
        open_ages = [pr_age_days(pr, reference_now=s["reference_now"]) for pr in open_prs] if open_prs else [0.0]
        top_author = max(s["author_prs"].items(), key=lambda x: len(x[1]))[0] if s["author_prs"] else "—"
        top_reviewer = (
            max(s["reviewer_counts"].items(), key=lambda x: x[1]["reviewed"])[0] if s["reviewer_counts"] else "—"
        )
        cache_line = f"  Data as of      : [dim]{self.fetched_at[:19]}[/dim]\n" if self.fetched_at else ""
        avg_age = f"{mean(ages):.1f} days" if ages else "—"
        median_age = f"{median(ages):.1f} days" if ages else "—"
        fastest_age = f"{min(ages):.1f} days" if ages else "—"
        slowest_age = f"{max(ages):.1f} days" if ages else "—"
        text = (
            f"[bold cyan]PR Statistics — {self.workspace}/{self.repo_slug}[/bold cyan]\n\n"
            + cache_line
            + "\n[bold]Merged PRs[/bold]\n"
            f"  Total          : [cyan]{len(s['merged'])}[/cyan]\n"
            f"  Avg age        : [cyan]{avg_age}[/cyan]\n"
            f"  Median age     : [cyan]{median_age}[/cyan]\n"
            f"  Fastest merge  : [green]{fastest_age}[/green]\n"
            f"  Slowest merge  : [red]{slowest_age}[/red]\n\n"
            f"[bold]Open PRs[/bold]\n"
            f"  Currently open : [yellow]{len(open_prs)}[/yellow]\n"
            f"  Oldest open    : [red]{max(open_ages):.1f} days[/red]\n\n"
            f"[bold]Team[/bold]\n"
            f"  Most PRs merged : [cyan]{top_author}[/cyan]\n"
            f"  Most reviews    : [cyan]{top_reviewer}[/cyan]\n"
        )
        self.query_one("#overview-static", Static).update(text)

    def _populate_activity(self) -> None:
        s = self.stats
        text = (
            "[bold]PRs merged per week (last 16 weeks)[/bold]\n\n"
            + bar_chart(s["weekly"], width=40, last_n=16)
            + "\n\n[bold]PRs merged per month (last 12 months)[/bold]\n\n"
            + bar_chart(s["monthly"], width=40, last_n=12)
        )
        self.query_one("#activity-static", Static).update(text)

    def _populate_open_prs(self) -> None:
        table = self.query_one("#open-table", DataTable)
        table.add_columns("Title", "Author", "Age (days)", "Target Branch", "Reviewers")
        for pr in sorted(
            self.stats["open"],
            key=lambda pr: pr_age_days(pr, reference_now=self.stats["reference_now"]),
            reverse=True,
        ):
            reviewers = (
                ", ".join(
                    name
                    for p in pr.get("participants", [])
                    if p.get("role") == "REVIEWER"
                    if (name := reviewer_name(p))
                )
                or "—"
            )
            table.add_row(
                pr_title(pr),
                pr_author_name(pr),
                f"{pr_age_days(pr, reference_now=self.stats['reference_now']):.1f}",
                pr_branch_name(pr),
                reviewers[:40],
            )

    def _populate_authors(self) -> None:
        table = self.query_one("#authors-table", DataTable)
        table.add_columns("Author", "PRs", "Avg age (days)", "Median (days)", "Avg comments")
        for author, prs in sorted(self.stats["author_prs"].items(), key=lambda x: -len(x[1])):
            ages = [pr_age_days(pr, merge_commit_dates=self.stats["merge_commit_dates"]) for pr in prs]
            avg_comments = mean(pr.get("comment_count", 0) for pr in prs)
            table.add_row(
                author,
                str(len(prs)),
                f"{mean(ages):.1f}",
                f"{median(ages):.1f}",
                f"{avg_comments:.1f}",
            )

    def _populate_reviews(self) -> None:
        table = self.query_one("#reviews-table", DataTable)
        table.add_columns("Reviewer", "PRs Reviewed", "PRs Approved", "Approval Rate")
        for name, counts in sorted(self.stats["reviewer_counts"].items(), key=lambda x: -x[1]["reviewed"]):
            rate = counts["approved"] / counts["reviewed"] * 100 if counts["reviewed"] else 0
            table.add_row(name, str(counts["reviewed"]), str(counts["approved"]), f"{rate:.0f}%")

    def _populate_branches(self) -> None:
        table = self.query_one("#branches-table", DataTable)
        table.add_columns("Target Branch", "PR Count", "Avg age (days)", "Median (days)")
        for branch, ages in sorted(self.stats["branch_counts"].items(), key=lambda x: -len(x[1])):
            table.add_row(branch, str(len(ages)), f"{mean(ages):.1f}", f"{median(ages):.1f}")

    def _populate_slowest(self) -> None:
        table = self.query_one("#slowest-table", DataTable)
        table.add_columns("Title", "Author", "Days", "Target Branch", "Comments")
        for pr in sorted(
            self.stats["merged"],
            key=lambda pr: pr_age_days(pr, merge_commit_dates=self.stats["merge_commit_dates"]),
            reverse=True,
        )[:25]:
            table.add_row(
                pr_title(pr),
                pr_author_name(pr),
                f"{pr_age_days(pr, merge_commit_dates=self.stats['merge_commit_dates']):.1f}",
                pr_branch_name(pr),
                str(pr.get("comment_count", 0)),
            )

    def _populate_commits(self) -> None:
        s = self.stats
        if not s["total_commits"]:
            self.query_one("#commits-charts", Static).update("[dim]No commit data — run with --refresh[/dim]")
            return

        dow_chart = bar_chart(s["commit_dow"], width=30, last_n=7)
        hour_chart = bar_chart({f"{h:02d}h": v for h, v in s["commit_hour"].items()}, width=30, last_n=24)
        text = (
            f"[bold]Total commits: [cyan]{s['total_commits']}[/cyan][/bold]\n\n"
            "[bold]Commits per week (last 16 weeks)[/bold]\n\n"
            + bar_chart(s["commit_weekly"], width=40, last_n=16)
            + "\n\n[bold]By day of week[/bold]\n\n"
            + dow_chart
            + "\n\n[bold]By hour of day[/bold]\n\n"
            + hour_chart
        )
        self.query_one("#commits-charts", Static).update(text)

        table = self.query_one("#commits-table", DataTable)
        table.add_columns("Author", "Commits", "% of total")
        total = s["total_commits"]
        for author, count in s["commit_authors"].items():
            table.add_row(author, str(count), f"{count / total * 100:.1f}%")

    def action_switch_tab(self, tab_id: str) -> None:
        """Switch the active stats tab from a keyboard binding."""
        self.query_one(TabbedContent).active = tab_id


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse CLI flags, load data, and launch the stats application."""
    args = set(sys.argv[1:])
    refresh = "--refresh" in args or "-r" in args
    use_list = "--list" in args or "-l" in args

    workspace, repo_slug = WORKSPACE, REPO_SLUG

    if use_list:
        result = SelectionApp().run()
        if not result:
            return
        workspace, repo_slug = result

    cached = load_cache(workspace, repo_slug) if not refresh else None

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
            f"[dim]Loaded {len(merged)} merged + {len(open_prs)} open + {len(commits)} commits. Cache saved.[/dim]"
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
