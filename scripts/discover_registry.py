#!/usr/bin/env python3
"""
Discover Registry — detects anomalous growth in npm and PyPI packages.

Outputs candidates to candidates_registry.json.

Required: requests, beautifulsoup4
Usage: python scripts/discover_registry.py
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
WATCHLIST_PATH = os.path.join(ROOT_DIR, "watchlist.json")
OUTPUT_PATH = os.path.join(ROOT_DIR, "candidates_registry.json")

NPM_REGISTRY = "https://registry.npmjs.org/-/v1/search"
NPM_API = "https://api.npmjs.org/downloads"
PYPI_SEARCH = "https://pypi.org/search"
PYPISTATS = "https://pypistats.org/api"

USER_AGENT = "agent-journal-discovery/1.0 (https://github.com/Fardinak/agent-journal)"

# Keywords for discovery
NPM_KEYWORDS = ["llm", "ai-agent", "openai", "langchain", "vector", "embedding", "mcp"]
PYPI_KEYWORDS = ["llm", "agent", "embedding", "mcp", "ai-tool"]


def load_json(path: str) -> object:
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        return json.load(f)


def save_json(path: str, data: object) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
        f.write("\n")


def load_watchlist_packages() -> set[str]:
    """Get set of already-watched npm and PyPI packages."""
    watchlist = load_json(WATCHLIST_PATH)
    packages = set()
    for item in watchlist:
        npm = item.get("npm")
        if npm:
            packages.add(npm.lower())
        pypi = item.get("pypi")
        if pypi:
            packages.add(pypi.lower())
    return packages


def infer_ecosystem_from_keywords(name: str, description: str = "") -> list[str]:
    """Infer ecosystem from package name and description."""
    matches = set()
    text = (name + " " + (description or "")).lower()
    
    # Python ecosystem signals
    if any(k in text for k in ["python", "pip", "pypi", "django", "flask", "fastapi"]):
        matches.add("python")
    
    # TypeScript ecosystem signals
    if any(k in text for k in ["typescript", "deno", "bun", "npm"]):
        matches.add("typescript")
    
    # Next.js signals
    if any(k in text for k in ["next", "react", "vercel"]):
        matches.add("nextjs")
    
    # LLM tooling signals
    if any(k in text for k in ["llm", "langchain", "openai", "anthropic", "embedding", "rag", "vector", "agent", "mcp", "model context"]):
        matches.add("llm-tooling")
    
    # Infrastructure signals
    if any(k in text for k in ["docker", "kubernetes", "terraform", "observability", "otel"]):
        matches.add("infrastructure")
    
    return list(matches) if matches else ["python", "llm-tooling"]


def is_recent(published_date: str, months: int = 6) -> bool:
    """Check if package was published within the last N months."""
    if not published_date:
        return True  # assume recent if unknown
    
    try:
        # Try various date formats
        for fmt in ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"]:
            try:
                dt = datetime.strptime(published_date[:19], fmt)
                age = datetime.now(timezone.utc) - dt.replace(tzinfo=timezone.utc)
                return age.days < months * 30
            except ValueError:
                continue
        return True  # assume recent if can't parse
    except Exception:
        return True


def check_velocity(weekly: int, monthly: int) -> bool:
    """Check if weekly downloads indicate accelerating growth."""
    if monthly == 0:
        return weekly > 1000
    
    # Flag if weekly downloads would be 2x the monthly rate
    # (i.e., if this pace continued, it would be 2x last month)
    projected_monthly = weekly * 4
    return projected_monthly > monthly * 1.5


def discover_npm() -> list[dict]:
    """Discover npm packages with accelerating growth."""
    candidates = []
    watched = load_watchlist_packages()
    
    for keyword in NPM_KEYWORDS:
        print(f"[INFO] Searching npm for: {keyword}")
        
        params = {
            "text": keyword,
            "size": 20,
            "quality": 0.1,
            "popularity": 0.1,
        }
        
        try:
            resp = requests.get(NPM_REGISTRY, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            
            for pkg in data.get("packages", []):
                name = pkg.get("name", "")
                if not name:
                    continue
                
                # Skip scoped packages for simplicity
                if name.startswith("@"):
                    continue
                
                if name.lower() in watched:
                    print(f"  [SKIP] {name} in watchlist")
                    continue
                
                description = pkg.get("description", "")
                date = pkg.get("date", "")
                
                # Only consider recent packages
                if not is_recent(date, months=6):
                    continue
                
                # Get download counts
                try:
                    weekly_resp = requests.get(f"{NPM_API}/point/last-week/{name}", timeout=15)
                    monthly_resp = requests.get(f"{NPM_API}/point/last-month/{name}", timeout=15)
                    
                    if weekly_resp.status_code == 200 and monthly_resp.status_code == 200:
                        weekly = weekly_resp.json().get("downloads", 0)
                        monthly = monthly_resp.json().get("downloads", 0)
                        
                        if check_velocity(weekly, monthly):
                            ecosystem = infer_ecosystem_from_keywords(name, description)
                            
                            candidates.append({
                                "name": name,
                                "npm": name,
                                "description": description,
                                "downloads_last_week": weekly,
                                "downloads_last_month": monthly,
                                "ecosystem": ecosystem,
                                "detected_by": "npm_registry",
                            })
                            print(f"  [FOUND] {name}: {weekly} weekly, {monthly} monthly (accelerating)")
                        else:
                            print(f"  [SKIP] {name}: {weekly} weekly, no velocity signal")
                    else:
                        print(f"  [SKIP] {name}: no download data")
                        
                except requests.RequestException as e:
                    print(f"  [WARN] Could not fetch downloads for {name}: {e}")
                
                time.sleep(0.5)  # rate limiting
                
        except requests.RequestException as e:
            print(f"[WARN] Failed to search npm for {keyword}: {e}", file=sys.stderr)
        
        time.sleep(1)
    
    return candidates


def discover_pypi() -> list[dict]:
    """Discover PyPI packages with accelerating growth."""
    candidates = []
    watched = load_watchlist_packages()
    
    for keyword in PYPI_KEYWORDS:
        print(f"[INFO] Searching PyPI for: {keyword}")
        
        params = {
            "q": keyword,
            "o": "-created",  # sort by newest
        }
        
        try:
            resp = requests.get(PYPI_SEARCH, params=params, headers={"User-Agent": USER_AGENT}, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            
            # Find package links
            for item in soup.select("a.package-snippet"):
                name = item.get("href", "").strip("/").split("/")[-1]
                if not name or name in watched:
                    continue
                
                if name.lower() in watched:
                    print(f"  [SKIP] {name} in watchlist")
                    continue
                
                description = item.select_one("p")
                desc_text = description.get_text(strip=True) if description else ""
                
                date_elem = item.select_one("p > span:contains('(')")
                # Get the package's created date from the page
                try:
                    detail_resp = requests.get(f"https://pypi.org/pypi/{name}/json", timeout=15)
                    if detail_resp.status_code != 200:
                        continue
                    
                    info = detail_resp.json().get("info", {})
                    created = info.get("created", "")
                    
                    if not is_recent(created, months=6):
                        continue
                    
                    # Get recent download stats
                    stats_resp = requests.get(f"{PYPISTATS}/packages/{name}/recent", timeout=15)
                    
                    if stats_resp.status_code == 200:
                        stats = stats_resp.json()
                        last_week = stats.get("last_week", 0)
                        last_month = sum(
                            stats.get(k, 0) 
                            for k in ["last_week", "last_two_weeks", "last_month"]
                            if stats.get(k)
                        )
                        
                        if check_velocity(last_week, last_month):
                            ecosystem = infer_ecosystem_from_keywords(name, desc_text)
                            
                            candidates.append({
                                "name": name,
                                "pypi": name,
                                "description": desc_text,
                                "downloads_last_week": last_week,
                                "downloads_last_month": last_month,
                                "ecosystem": ecosystem,
                                "detected_by": "pypi_registry",
                            })
                            print(f"  [FOUND] {name}: {last_week} weekly (accelerating)")
                        else:
                            print(f"  [SKIP] {name}: no velocity signal")
                    else:
                        print(f"  [SKIP] {name}: no stats available")
                        
                except requests.RequestException as e:
                    print(f"  [WARN] Could not fetch details for {name}: {e}")
                
                time.sleep(0.5)
                
        except requests.RequestException as e:
            print(f"[WARN] Failed to search PyPI for {keyword}: {e}", file=sys.stderr)
        
        time.sleep(1)
    
    return candidates


def main() -> None:
    print("[INFO] Starting discover_registry...")
    
    npm_candidates = discover_npm()
    print(f"[INFO] npm found {len(npm_candidates)} candidates")
    
    pypi_candidates = discover_pypi()
    print(f"[INFO] PyPI found {len(pypi_candidates)} candidates")
    
    # Combine
    all_candidates = npm_candidates + pypi_candidates
    
    # Deduplicate by npm or pypi name
    seen = {}
    for c in all_candidates:
        key = (c.get("npm") or c.get("pypi") or "").lower()
        if key and key not in seen:
            seen[key] = c
        elif key in seen:
            # Merge detected_by
            existing = seen[key]
            existing["detected_by"] = existing.get("detected_by", "") + "," + c["detected_by"]
    
    unique_candidates = list(seen.values())
    detected_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    for c in unique_candidates:
        c["detected_at"] = detected_at
    
    # Save output
    save_json(OUTPUT_PATH, unique_candidates)
    
    print(f"\n[INFO] Total unique candidates: {len(unique_candidates)}")
    print(f"[INFO] Written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()