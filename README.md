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

## Install

```bash
uvx bb-stats
```

## Setup

Run the interactive configuration to store your Bitbucket credentials:

```bash
bb-stats --configure
```

This saves your email and app password to `~/.config/bb-stats/config.toml` (chmod 600).

Alternatively, set environment variables (these take priority over the config file):

```bash
export BITBUCKET=your_app_password
export BITBUCKET_EMAIL=your@email.com
```

A `.env` file in the current directory is also supported as a fallback.

## Usage

```bash
# Interactive workspace → repo picker (default when no -w/-R given)
bb-stats

# Specify workspace and repo directly
bb-stats -w myworkspace -R myrepo

# Force refresh cached data
bb-stats --refresh   # or -r

# Combine flags
bb-stats -w myworkspace -R myrepo -r
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
