# bitbucket-stats

Terminal-based Bitbucket pull request and commit statistics viewer built with [Textual](https://textual.textualize.io/).

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)

## Features

- Interactive workspace and repository selector (`--list`)
- Tabbed TUI with keyboard navigation (keys `1`–`8`)
- **Overview** — merged/open PR counts, age stats, top author and reviewer
- **Activity** — weekly and monthly merge bar charts
- **Open PRs** — sortable table of currently open pull requests
- **Authors** — per-author PR count, avg/median age, comment stats
- **Reviews** — reviewer leaderboard with approval rates
- **Branches** — target branch merge frequency and age
- **Slowest** — the 25 longest-lived merged PRs
- **Commits** — weekly/monthly/day-of-week/hour-of-day charts and author breakdown
- Local JSON caching per workspace/repo to avoid redundant API calls

## Setup

### Environment variables

Create a `.env` file (or export the variables) with your Bitbucket **app password** and account email:

```
BITBUCKET=your_app_password
BITBUCKET_EMAIL=your@email.com
```

### Install

```bash
uv sync
```

Or run directly as a script without installing:

```bash
uv run bb_stats.py
```

## Usage

```bash
# Run with default workspace/repo (configured in bb_stats.py)
bb-stats

# Interactive workspace → repo picker
bb-stats --list   # or -l

# Force refresh cached data
bb-stats --refresh   # or -r

# Combine flags
bb-stats -l -r
```

### Keyboard shortcuts

| Key | Tab        |
|-----|------------|
| `1` | Overview   |
| `2` | Activity   |
| `3` | Open PRs   |
| `4` | Authors    |
| `5` | Reviews    |
| `6` | Branches   |
| `7` | Slowest    |
| `8` | Commits    |
| `q` | Quit       |

## Development

```bash
# Lint
uvx ruff check bb_stats.py

# Format
uvx ruff format bb_stats.py

# Type check
uvx ty check bb_stats.py
```
