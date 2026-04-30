"""Textual UI screens and rendering logic for bb-stats."""

from statistics import mean, median
from typing import ClassVar

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

from bb_stats.api import fetch_repos, fetch_workspaces
from bb_stats.data import (
    bar_chart,
    pr_age_days,
    pr_author_name,
    pr_branch_name,
    pr_title,
    reviewer_name,
)


class ItemSelectScreen(Screen[str | None]):
    BINDINGS: ClassVar[tuple[Binding, ...]] = (Binding("escape", "dismiss(None)", "Back"),)

    def __init__(self, title: str, items: list[tuple[str, str]]) -> None:
        super().__init__()
        self._title = title
        self._all_items = items
        self._visible: list[tuple[str, str]] = list(items)

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label(f" {self._title}", id="sel-title")
        yield Input(placeholder="Type to filter...", id="sel-input")
        yield ListView(id="sel-list")
        yield Footer()

    def on_mount(self) -> None:
        self._render_list(self._all_items)
        self.query_one(Input).focus()

    def _render_list(self, items: list[tuple[str, str]]) -> None:
        self._visible = list(items)
        lv = self.query_one(ListView)
        lv.clear()
        for display, _ in items:
            lv.append(ListItem(Label(display)))

    def on_input_changed(self, event: Input.Changed) -> None:
        q = event.value.lower()
        self._render_list([(d, v) for d, v in self._all_items if q in d.lower()])

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        idx = event.list_view.index
        if idx is not None and 0 <= idx < len(self._visible):
            self.dismiss(self._visible[idx][1])


class SelectionApp(App[tuple[str, str] | None]):
    CSS = """
    #sel-title { padding: 1 2; color: $accent; text-style: bold; }
    #sel-input { margin: 0 1; }
    ListView { height: 1fr; }
    LoadingIndicator { height: 1fr; }
    """

    def compose(self) -> ComposeResult:  # noqa: PLR6301
        yield Header()
        yield LoadingIndicator()
        yield Footer()

    def on_mount(self) -> None:
        self._load_workspaces()

    @work(thread=True)
    def _load_workspaces(self) -> None:
        workspaces = fetch_workspaces()
        items = [(f"{w.get('name', w['slug'])}  ({w['slug']})", w["slug"]) for w in workspaces]
        self.call_from_thread(
            self.push_screen, ItemSelectScreen("Select workspace", items), self._on_workspace_selected,
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


class BBStatsApp(App):
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
        super().__init__()
        self.workspace = workspace
        self.repo_slug = repo_slug
        self.stats = stats
        self.fetched_at = fetched_at

    def compose(self) -> ComposeResult:  # noqa: PLR6301
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
        self.query_one(TabbedContent).active = tab_id
