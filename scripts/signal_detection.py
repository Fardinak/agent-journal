#!/usr/bin/env python3
"""
Signal Detection — monitors GitHub stars and npm downloads for anomalies.

Reads watchlist.json, gets current star counts from GitHub API, calculates deltas
from previous run, and writes flagged candidates to candidates.json.

Required: requests, python-frontmatter
Usage: python scripts/signal_detection.py
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
WATCHLIST_PATH = os.path.join(ROOT_DIR, "watchlist.json")
CANDIDATES_PATH = os.path.join(ROOT_DIR, "candidates.json")
STAR_COUNTS_PATH = os.path.join(ROOT_DIR, "star_counts.json")

GITHUB_API = "https://api.github.com"
NPM_API = "https://api.npmjs.org/downloads/range"

# Threshold for flagging significant star growth in a single run
STAR_ANOMALY_THRESHOLD = 50


def load_json(path: str) -> object:
    with open(path, "r") as f:
        return json.load(f)


def save_json(path: str, data: object) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
        f.write("\n")


def trigger_draft_workflow(candidate_name: str) -> bool:
    """Trigger the draft-entry workflow for a new candidate."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("[WARN] No GITHUB_TOKEN, skipping dispatch", file=sys.stderr)
        return False
    
    # Get owner/repo from git remote
    try:
        result = subprocess.run(
            ["git", "remote", "geturl", "origin"],
            capture_output=True, text=True, cwd=ROOT_DIR
        )
        remote_url = result.stdout.strip()
        # Parse from git@github.com:owner/repo.git or https://github.com/owner/repo
        match = re.search(r"github\.com[/:]([^/]+)/([^/.]+)", remote_url)
        if match:
            owner, repo = match.groups()
        else:
            print(f"[WARN] Could not parse owner/repo from {remote_url}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"[WARN] Could not get git remote: {e}", file=sys.stderr)
        return False
    
    url = f"{GITHUB_API}/repos/{owner}/{repo}/dispatches"
    payload = {
        "event_type": "new_candidate",
        "client_payload": {"name": candidate_name}
    }
    
    try:
        resp = requests.post(url, json=payload, headers=github_headers(), timeout=30)
        if resp.status_code == 204:
            print(f"[INFO] Triggered draft workflow for {candidate_name}")
            return True
        else:
            print(f"[WARN] Dispatch failed: {resp.status_code} {resp.text}", file=sys.stderr)
            return False
    except requests.RequestException as e:
        print(f"[WARN] Dispatch error: {e}", file=sys.stderr)
        return False


def load_candidates() -> list[dict]:
    if not os.path.exists(CANDIDATES_PATH):
        return []
    return load_json(CANDIDATES_PATH)


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
        "User-Agent": "AgentJournal/1.0 (Signal Detection Script)"
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


def dedup_key(candidate: dict) -> str:
    return f"{candidate['name']}|{candidate['detected_at']}"


def main() -> None:
    print("[INFO] Starting signal detection...")
    if not os.path.exists(WATCHLIST_PATH):
        print(f"ERROR: {WATCHLIST_PATH} not found", file=sys.stderr)
        sys.exit(1)

    watchlist = load_json(WATCHLIST_PATH)
    print(f"[INFO] Loaded {len(watchlist)} items from watchlist")
    
    existing_candidates = load_candidates()
    existing_keys = {dedup_key(c) for c in existing_candidates}
    print(f"[INFO] Loaded {len(existing_candidates)} existing candidates")

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
                key = dedup_key({"name": name, "detected_at": detected_at})
                if key not in existing_keys:
                    candidate = {
                        "name": name,
                        "repo_url": f"https://github.com/{repo}",
                        "ecosystem": ecosystem,
                        "stars_delta": delta,
                        "npm_downloads_delta": None,
                        "detected_at": detected_at,
                    }
                    new_candidates.append(candidate)
                    existing_keys.add(key)
                    print(f"[SIGNAL] {name}: +{delta} stars since last run")

        # --- npm downloads check ---
        npm_fetch_start = time.time()
        if npm:
            print(f"  [INFO] Fetching npm downloads for {npm}...")
            downloads = fetch_npm_downloads(npm)
            current = downloads["current_week"]
            previous = downloads["previous_week"]

            if is_npm_anomaly(current, previous):
                key = dedup_key({"name": name, "detected_at": detected_at})
                if key not in existing_keys:
                    candidate = {
                        "name": name,
                        "repo_url": f"https://github.com/{repo}" if repo else "",
                        "ecosystem": ecosystem,
                        "stars_delta": None,
                        "npm_downloads_delta": current - previous,
                        "detected_at": detected_at,
                    }
                    new_candidates.append(candidate)
                    existing_keys.add(key)
                    print(f"  [INFO] NPM delta: +{current - previous} WoW in {time.time() - npm_fetch_start:.1f}s")
                    print(f"[SIGNAL] {name}: npm downloads +{current - previous} WoW", file=sys.stderr)
                elif new_candidates:
                    for c in new_candidates:
                        if c["name"] == name and c["detected_at"] == detected_at:
                            c["npm_downloads_delta"] = current - previous

    # Save current star counts for next run
    save_star_counts(current_star_counts)
    print(f"[INFO] Saved {len(current_star_counts)} star counts")

    # Trigger draft workflows for new candidates
    triggered = []
    for candidate in new_candidates:
        if trigger_draft_workflow(candidate["name"]):
            triggered.append(candidate["name"])

    if new_candidates:
        all_candidates = existing_candidates + new_candidates
        save_json(CANDIDATES_PATH, all_candidates)
        print(f"\n[INFO] Detected {len(new_candidates)} new signal(s).")
        if triggered:
            print(f"[INFO] Triggered draft workflow(s) for: {', '.join(triggered)}")
    else:
        print("\n[INFO] No new signals detected.")


if __name__ == "__main__":
    main()