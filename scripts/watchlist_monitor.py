#!/usr/bin/env python3
"""
Watchlist Monitor — monitors GitHub stars and npm downloads for anomalies.

Reads watchlist.json, gets current star counts from GitHub API, calculates deltas
from previous run, and writes flagged candidates to candidates_watchlist.json.

This is one input into the scoring pipeline (see score_candidates.py).

Required: requests
Usage: python scripts/watchlist_monitor.py
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
WATCHLIST_PATH = os.path.join(ROOT_DIR, "watchlist.json")
CANDIDATES_PATH = os.path.join(ROOT_DIR, "candidates_watchlist.json")
STAR_COUNTS_PATH = os.path.join(ROOT_DIR, "star_counts.json")

GITHUB_API = "https://api.github.com"
NPM_API = "https://api.npmjs.org/downloads/range"

# Threshold for flagging significant star growth in a single run
STAR_ANOMALY_THRESHOLD = 50

USER_AGENT = "agent-journal-discovery/1.0 (https://github.com/Fardinak/agent-journal)"


def load_json(path: str) -> object:
    with open(path, "r") as f:
        return json.load(f)


def save_json(path: str, data: object) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
        f.write("\n")


def load_star_counts() -> dict[str, dict]:
    """Load previous star counts from last run."""
    if not os.path.exists(STAR_COUNTS_PATH):
        return {}
    return load_json(STAR_COUNTS_PATH)


def save_star_counts(counts: dict[str, dict]) -> None:
    """Save current star counts for next run comparison."""
    save_json(STAR_COUNTS_PATH, counts)


def github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_repo_info(repo: str) -> dict:
    """Fetch current star count and info from repository."""
    repo = repo.strip("/")
    url = f"{GITHUB_API}/repos/{repo}"
    
    try:
        resp = requests.get(url, headers=github_headers(), timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return {
            "stargazers_count": data.get("stargazers_count", 0),
            "updated_at": data.get("updated_at"),
            "name": data.get("name"),
            "full_name": data.get("full_name"),
            "created_at": data.get("created_at"),
            "description": data.get("description"),
        }
    except requests.RequestException as e:
        print(f"[WARN] Failed to fetch repo info for {repo}: {e}", file=sys.stderr)
        return {"stargazers_count": 0, "updated_at": None}


def fetch_npm_downloads(package: str) -> dict[str, int]:
    """Fetch weekly download counts from npm registry API."""
    now = datetime.now().replace(tzinfo=timezone.utc)
    current_end = now - timedelta(days=1)
    current_start = current_end - timedelta(days=6)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=6)

    def _fetch_range(start, end):
        url = f"{NPM_API}/{start.strftime('%Y-%m-%d')}:{end.strftime('%Y-%m-%d')}/{package}"
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        downloads_list = data.get("downloads", [])
        if isinstance(downloads_list, list):
            return sum(d["downloads"] for d in downloads_list)
        return 0

    try:
        current_week = _fetch_range(current_start, current_end)
        previous_week = _fetch_range(previous_start, previous_end)
        return {"current_week": current_week, "previous_week": previous_week}
    except requests.RequestException as e:
        print(f"[WARN] Failed to fetch npm downloads for {package}: {e}", file=sys.stderr)
        return {"current_week": 0, "previous_week": 0}


def is_npm_anomaly(current: int, previous: int) -> bool:
    """Flag if week-over-week growth exceeds 40%."""
    if previous == 0:
        return current > 1000
    growth = (current - previous) / previous
    return growth > 0.40


def main() -> None:
    print("[INFO] Starting watchlist monitor...")
    if not os.path.exists(WATCHLIST_PATH):
        print(f"ERROR: {WATCHLIST_PATH} not found", file=sys.stderr)
        sys.exit(1)

    watchlist = load_json(WATCHLIST_PATH)
    print(f"[INFO] Loaded {len(watchlist)} items from watchlist")

    # Load previous star counts for delta calculation
    previous_star_counts = load_star_counts()
    print(f"[INFO] Loaded {len(previous_star_counts)} previous star counts")

    new_candidates: list[dict] = []
    current_star_counts: dict[str, dict] = {}
    detected_at = datetime.now().replace(tzinfo=timezone.utc).strftime("%Y-%m-%d")
    print(f"[INFO] Detection date: {detected_at}")

    for idx, item in enumerate(watchlist):
        name = item.get("name", "")
        repo = item.get("github", "")
        npm = item.get("npm")
        ecosystem = item.get("ecosystem", [])
        
        print(f"[{idx+1}/{len(watchlist)}] Checking {name}...")

        if not name or not repo:
            print(f"[WARN] Skipping watchlist item with missing name or github: {item}", file=sys.stderr)
            continue

        # --- GitHub stars check ---
        stars_fetch_start = time.time()
        if repo:
            print(f"  [INFO] Fetching GitHub stars for {repo}...")
            repo_info = fetch_repo_info(repo)
            current_stars = repo_info.get("stargazers_count", 0)
            current_star_counts[repo] = {
                "count": current_stars,
                "updated_at": repo_info.get("updated_at"),
            }
            print(f"  [INFO] Current stars: {current_stars} in {time.time() - stars_fetch_start:.1f}s")
            
            # Calculate delta from previous run
            prev = previous_star_counts.get(repo, {})
            prev_count = prev.get("count", 0) if prev else 0
            delta = current_stars - prev_count
            
            if prev_count > 0 and delta > 0:
                print(f"  [INFO] Delta since last run: +{delta}")
            
            # Flag significant growth
            if delta >= STAR_ANOMALY_THRESHOLD:
                candidate = {
                    "name": name,
                    "github": repo,
                    "npm": npm,
                    "ecosystem": ecosystem,
                    "stars_delta": delta,
                    "npm_downloads_delta": None,
                    "detected_at": detected_at,
                    "detected_by": "watchlist_monitor",
                }
                new_candidates.append(candidate)
                print(f"[SIGNAL] {name}: +{delta} stars since last run")

        # --- npm downloads check ---
        npm_fetch_start = time.time()
        if npm:
            print(f"  [INFO] Fetching npm downloads for {npm}...")
            downloads = fetch_npm_downloads(npm)
            current = downloads["current_week"]
            previous = downloads["previous_week"]

            if is_npm_anomaly(current, previous):
                # Check if we already have this from GitHub
                existing_names = {c["name"] for c in new_candidates}
                if name not in existing_names:
                    candidate = {
                        "name": name,
                        "github": repo,
                        "npm": npm,
                        "ecosystem": ecosystem,
                        "stars_delta": None,
                        "npm_downloads_delta": current - previous,
                        "detected_at": detected_at,
                        "detected_by": "watchlist_monitor",
                    }
                    new_candidates.append(candidate)
                    print(f"[SIGNAL] {name}: npm downloads +{current - previous} WoW", file=sys.stderr)
                else:
                    # Update existing candidate with npm data
                    for c in new_candidates:
                        if c["name"] == name:
                            c["npm_downloads_delta"] = current - previous

    # Save current star counts for next run
    save_star_counts(current_star_counts)
    print(f"[INFO] Saved {len(current_star_counts)} star counts")

    # Write to candidates_watchlist.json for scoring pipeline
    if new_candidates:
        save_json(CANDIDATES_PATH, new_candidates)
        print(f"\n[INFO] Detected {len(new_candidates)} signal(s) from watchlist. Written to {CANDIDATES_PATH}")
    else:
        # Write empty array to indicate run completed
        save_json(CANDIDATES_PATH, [])
        print("\n[INFO] No new signals from watchlist.")


if __name__ == "__main__":
    main()