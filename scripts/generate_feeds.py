#!/usr/bin/env python3
"""
Generate Feeds — reads all published entries and produces JSON feed files.

Reads entries/**/*.md with status: published, extracts YAML frontmatter,
and writes JSON feeds to feeds/.

- feeds/index.json — all published entries, sorted by date descending
- feeds/<ecosystem>.json — entries filtered by ecosystem

Idempotent: safe to re-run.
"""

import glob
import json
import os
import sys
from datetime import datetime, timezone

import frontmatter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
ENTRIES_DIR = os.path.join(ROOT_DIR, "entries")
FEEDS_DIR = os.path.join(ROOT_DIR, "feeds")

REQUIRED_FIELDS = {"title", "date", "ecosystem", "category", "significance", "reach_for_when"}


def load_entry(filepath: str) -> dict | None:
    """Parse a markdown file with YAML frontmatter. Returns None on failure."""
    try:
        post = frontmatter.load(filepath)
    except Exception as e:
        print(f"[WARN] Could not parse {filepath}: {e}", file=sys.stderr)
        return None

    metadata = dict(post.metadata)

    # Validate required fields
    missing = REQUIRED_FIELDS - set(metadata.keys())
    if missing:
        print(
            f"[WARN] Skipping {filepath}: missing required fields: {', '.join(sorted(missing))}",
            file=sys.stderr,
        )
        return None

    # Only include published entries
    if metadata.get("status") != "published":
        return None

    # Build the relative source path (e.g., entries/python/2026-04-15-langsmith.md)
    rel_path = os.path.relpath(filepath, ROOT_DIR)
    # Convert to the GitHub Pages URL
    # entries/python/2026-04-15-langsmith.md -> https://agent-journal.github.io/entries/python/2026-04-15-langsmith.html
    url_path = rel_path.replace(".md", ".html")
    base_url = os.environ.get("PAGES_BASE_URL", "https://agent-journal.github.io")
    url = f"{base_url}/{url_path}"

    # Ensure ecosystem is a list
    ecosystem = metadata.get("ecosystem", [])
    if isinstance(ecosystem, str):
        ecosystem = [ecosystem]

    return {
        "title": metadata["title"],
        "date": str(metadata["date"]),
        "ecosystem": ecosystem,
        "category": metadata["category"],
        "significance": metadata["significance"],
        "displaces": metadata.get("displaces", []),
        "complements": metadata.get("complements", []),
        "reach_for_when": metadata["reach_for_when"],
        "url": url,
        "source_file": rel_path,
    }


def main() -> None:
    os.makedirs(FEEDS_DIR, exist_ok=True)

    # Find all markdown files in entries/
    md_files = glob.glob(os.path.join(ENTRIES_DIR, "**", "*.md"), recursive=True)

    entries = []
    for filepath in sorted(md_files):
        entry = load_entry(filepath)
        if entry:
            entries.append(entry)

    # Sort by date descending
    entries.sort(key=lambda e: e["date"], reverse=True)

    # Write index.json
    index_data = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total": len(entries),
        "entries": entries,
    }

    index_path = os.path.join(FEEDS_DIR, "index.json")
    with open(index_path, "w") as f:
        json.dump(index_data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"feeds/index.json: {len(entries)} entries")

    # Collect all unique ecosystems
    ecosystems: set[str] = set()
    for entry in entries:
        for eco in entry["ecosystem"]:
            ecosystems.add(eco)

    # Write per-ecosystem feeds
    for eco in sorted(ecosystems):
        eco_entries = [e for e in entries if eco in e["ecosystem"]]
        eco_data = {
            "generated_at": index_data["generated_at"],
            "total": len(eco_entries),
            "entries": eco_entries,
        }
        eco_path = os.path.join(FEEDS_DIR, f"{eco}.json")
        with open(eco_path, "w") as f:
            json.dump(eco_data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f"feeds/{eco}.json: {len(eco_entries)} entries")

    print(f"\nGenerated feeds for {len(ecosystems)} ecosystem(s).")


if __name__ == "__main__":
    main()
