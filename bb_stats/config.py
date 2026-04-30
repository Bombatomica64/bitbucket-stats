"""Configuration loading and setup."""

import os
import tomllib
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "bb-stats"
CONFIG_FILE = CONFIG_DIR / "config.toml"
console = Console()


def _load_config() -> dict[str, str]:
    """Load credentials from config file, env vars, and .env (in priority order).

    Returns:
        A mapping with ``email`` and ``token`` entries.

    """
    load_dotenv(override=False)

    cfg: dict[str, str] = {}
    if CONFIG_FILE.exists():
        with CONFIG_FILE.open("rb") as f:
            data = tomllib.load(f)
        if data.get("email"):
            cfg["email"] = data["email"]
        if data.get("token"):
            cfg["token"] = data["token"]

    return {
        "email": os.environ.get("BITBUCKET_EMAIL") or cfg.get("email", ""),
        "token": os.environ.get("BITBUCKET") or cfg.get("token", ""),
    }


def _configure() -> None:
    """Interactively create the config file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    email = input("Bitbucket email: ").strip()
    token = input("Bitbucket app password: ").strip()
    CONFIG_FILE.write_text(f'email = "{email}"\ntoken = "{token}"\n')
    CONFIG_FILE.chmod(0o600)
    console.print(f"[green]Config saved to {CONFIG_FILE}[/green]")
