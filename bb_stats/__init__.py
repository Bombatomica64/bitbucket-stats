#!/usr/bin/env python3
# Copyright (c) 2026. All rights reserved.
"""Terminal Bitbucket pull request and commit statistics viewer."""

# /// script
# requires-python = ">=3.11"
# dependencies = ["requests", "rich", "python-dotenv", "textual>=0.80.0"]
# ///
# Can also be installed as a package: uvx bb-stats

from bb_stats.cli import main as main
