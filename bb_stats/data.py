"""Data shaping and aggregate-stat helpers for PR/commit insights."""

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any


def commit_author(commit: dict) -> str:
    user = commit.get("author", {}).get("user")
    if user:
        return user.get("display_name", "Unknown")
    raw = commit.get("author", {}).get("raw", "Unknown")
    return raw.split("<")[0].strip() if "<" in raw else raw


def pr_author_name(pr: dict) -> str:
    return pr.get("author", {}).get("display_name", "Unknown")


def pr_branch_name(pr: dict) -> str:
    return pr.get("destination", {}).get("branch", {}).get("name", "Unknown")


def pr_title(pr: dict, max_len: int = 52) -> str:
    return pr.get("title", "Untitled")[:max_len]


def reviewer_name(participant: dict) -> str | None:
    user = participant.get("user") or {}
    return user.get("display_name")


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
    created = datetime.fromisoformat(pr["created_on"])
    if pr["state"] == "OPEN":
        end = reference_now or datetime.now(UTC)
    else:
        merge_hash = pr.get("merge_commit", {}).get("hash")
        end = _matching_commit_date(merge_commit_dates or {}, merge_hash) or datetime.fromisoformat(pr["updated_on"])
    return max(0.0, (end - created).total_seconds() / 86400)


def _build_commit_stats(commits: list[dict] | None) -> dict[str, Any]:
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


def bar_chart(data: dict[str, int], width: int = 40, last_n: int | None = None) -> str:
    if not data:
        return "[dim]No data[/dim]"
    items = list(data.items())
    if last_n:
        items = items[-last_n:]
    max_val = max(v for _, v in items)
    lines = []
    for key, val in items:
        bar_len = int((val / max_val) * width) if max_val else 0
        lines.append(f"  {key[-7:]:>7} │{'█' * bar_len:<{width}} {val}")
    return "\n".join(lines)
