#!/usr/bin/env python3
"""
Signal Detection — monitors GitHub stars and npm downloads for anomalies.

Reads watchlist.json, calculates growth deltas, and writes flagged candidates
to candidates.json. Idempotent: re-running will not produce duplicate entries.

Required: requests, python-frontmatter
Usage: python scripts/signal_detection.py
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
WATCHLIST_PATH = os.path.join(ROOT_DIR, "watchlist.json")
CANDIDATES_PATH = os.path.join(ROOT_DIR, "candidates.json")

GITHUB_API = "https://api.github.com"
NPM_API = "https://api.npmjs.org/downloads/range"


def load_json(path: str) -> object:
    with open(path, "r") as f:
        return json.load(f)


def save_json(path: str, data: object) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
        f.write("\n")


def load_candidates() -> list[dict]:
    if not os.path.exists(CANDIDATES_PATH):
        return []
    return load_json(CANDIDATES_PATH)


def github_headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_star_history(repo: str) -> list[dict]:
    """
    Fetch star timeline via GitHub timeline endpoint.
    Falls back to basic repo info if timeline is unavailable.
    Returns list of dicts with 'starred_at' keys (ISO timestamps).
    """
    repo = repo.strip("/")
    # Method 1: stargazers timeline (paginated, gives per-star timestamps)
    # This endpoint returns starred_at for each stargazer, reverse chronological
    url = f"{GITHUB_API}/repos/{repo}/stargazers"
    params = {"per_page": 100, "Accept": "application/vnd.github.star+json"}

    all_stars = []
    try:
        resp = requests.get(url, headers=github_headers(), params=params, timeout=30)
        resp.raise_for_status()
        page_data = resp.json()

        # Each item has a starred_at field
        for item in page_data:
            if isinstance(item, dict) and "starred_at" in item:
                all_stars.append(item["starred_at"])

        # Check for more pages (pagination)
        # We only grab up to 1000 stars for efficiency
        link_header = resp.headers.get("Link", "")
        while "next" in link_header and len(all_stars) < 1000:
            next_url = _extract_next_url(link_header)
            if not next_url:
                break
            resp = requests.get(next_url, headers=github_headers(), timeout=30)
            resp.raise_for_status()
            for item in resp.json():
                if isinstance(item, dict) and "starred_at" in item:
                    all_stars.append(item["starred_at"])
            link_header = resp.headers.get("Link", "")
    except requests.RequestException as e:
        print(f"[WARN] Failed to fetch stargazers for {repo}: {e}", file=sys.stderr)
        return []

    # Reverse to chronological order
    all_stars.reverse()
    return [{"starred_at": s} for s in all_stars]


def _extract_next_url(link_header: str) -> str | None:
    """Extract the 'next' page URL from GitHub Link header."""
    for part in link_header.split(","):
        if 'rel="next"' in part:
            url_part = part.split(";")[0].strip()
            return url_part.strip("<>")
    return None


def calculate_star_delta(stars: list[dict]) -> dict[str, int]:
    """
    Calculate 7-day rolling star delta and 30-day average.
    Returns {"delta_7d": int, "avg_30d": float}.
    """
    now = datetime.now(timezone.utc)
    star_dates = []
    for entry in stars:
        try:
            dt = datetime.fromisoformat(entry["starred_at"].replace("Z", "+00:00"))
            star_dates.append(dt)
        except (ValueError, KeyError):
            continue

    if not star_dates:
        return {"delta_7d": 0, "avg_30d": 0.0}

    # 7-day delta
    seven_days_ago = now - timedelta(days=7)
    delta_7d = sum(1 for d in star_dates if d >= seven_days_ago)

    # 30-day average per 7-day period
    thirty_days_ago = now - timedelta(days=30)
    delta_30d = sum(1 for d in star_dates if d >= thirty_days_ago)
    # Average 7-day window within the 30-day period
    avg_30d = delta_30d / (30 / 7) if delta_30d > 0 else 0.0

    return {"delta_7d": delta_7d, "avg_30d": avg_30d}


def is_anomaly(delta_7d: int, avg_30d: float) -> bool:
    """Flag if 7-day delta exceeds 3x the 30-day average."""
    if avg_30d == 0:
        # No baseline — flag if absolute delta is significant (>50 stars)
        return delta_7d > 50
    return delta_7d > 3 * avg_30d


def fetch_npm_downloads(package: str) -> dict[str, int]:
    """
    Fetch weekly download counts from npm registry API.
    Returns {"current_week": int, "previous_week": int}.
    """
    now = datetime.now(timezone.utc)
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
        return current > 1000  # absolute threshold for new packages
    growth = (current - previous) / previous
    return growth > 0.40


def dedup_key(candidate: dict) -> str:
    return f"{candidate['name']}|{candidate['detected_at']}"


def main() -> None:
    if not os.path.exists(WATCHLIST_PATH):
        print(f"ERROR: {WATCHLIST_PATH} not found", file=sys.stderr)
        sys.exit(1)

    watchlist = load_json(WATCHLIST_PATH)
    existing_candidates = load_candidates()
    existing_keys = {dedup_key(c) for c in existing_candidates}

    new_candidates: list[dict] = []
    detected_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for item in watchlist:
        name = item.get("name", "")
        repo = item.get("github", "")
        npm = item.get("npm")
        ecosystem = item.get("ecosystem", [])

        if not name or not repo:
            print(f"[WARN] Skipping watchlist item with missing name or github: {item}", file=sys.stderr)
            continue

        # --- GitHub stars check ---
        if repo:
            stars = fetch_star_history(repo)
            metrics = calculate_star_delta(stars)
            delta_7d = metrics["delta_7d"]
            avg_30d = metrics["avg_30d"]

            if is_anomaly(delta_7d, avg_30d):
                key = dedup_key({"name": name, "detected_at": detected_at})
                if key not in existing_keys:
                    candidate = {
                        "name": name,
                        "repo_url": f"https://github.com/{repo}",
                        "ecosystem": ecosystem,
                        "stars_delta": delta_7d,
                        "npm_downloads_delta": None,
                        "detected_at": detected_at,
                    }
                    new_candidates.append(candidate)
                    existing_keys.add(key)
                    print(f"[SIGNAL] {name}: +{delta_7d} stars in 7 days (30-day avg: {avg_30d:.1f})")

        # --- npm downloads check ---
        if npm:
            downloads = fetch_npm_downloads(npm)
            current = downloads["current_week"]
            previous = downloads["previous_week"]

            if is_npm_anomaly(current, previous):
                # Check if we already flagged this repo from GitHub stars today
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
                    print(f"[SIGNAL] {name}: npm downloads +{current - previous} WoW")
                elif new_candidates:
                    # Update existing candidate from this run with npm data
                    for c in new_candidates:
                        if c["name"] == name and c["detected_at"] == detected_at:
                            c["npm_downloads_delta"] = current - previous

    if new_candidates:
        all_candidates = existing_candidates + new_candidates
        save_json(CANDIDATES_PATH, all_candidates)
        print(f"\nDetected {len(new_candidates)} new signal(s). candidates.json updated.")
    else:
        print("\nNo new signals detected.")


if __name__ == "__main__":
    main()
