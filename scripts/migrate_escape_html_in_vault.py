#!/usr/bin/env python3
"""Escape HTML-like tags in markdown files for safer Obsidian rendering."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from obsitocin.topic_writer import migrate_html_like_markdown_in_vault


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Escape HTML-like tags in markdown files across an Obsidian vault."
    )
    parser.add_argument("vault_dir", help="Path to the vault root")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes to disk. Without this flag, runs in dry-run mode.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON output.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = migrate_html_like_markdown_in_vault(
        Path(args.vault_dir), dry_run=not args.apply
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        mode = "DRY RUN" if result["dry_run"] else "APPLY"
        print(f"[{mode}] files_changed={result['files_changed']}")
        for rel in result["changed_files"]:
            print(rel)
        if result["errors"]:
            print("errors:")
            for err in result["errors"]:
                print(err)

    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
