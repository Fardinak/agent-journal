#!/usr/bin/env python3
"""
Discover Dependents — finds repos adopting tools already in the journal.

Uses GitHub dependents graph to find new repos that depend on published entries.
Requires GITHUB_TOKEN environment variable.

Outputs candidates to candidates_dependents.json.

Usage: python scripts/discover_dependents.py
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup
import frontmatter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
WATCHLIST_PATH = os.path.join(ROOT_DIR, "watchlist.json")
ENTRIES_DIR = os.path.join(ROOT_DIR, "entries")
OUTPUT_PATH = os.path.join(ROOT_DIR, "candidates_dependents.json")

GITHUB_API = "https://api.github.com"
USER_AGENT = "agent-journal-discovery/1.0 (https://github.com/Fardinak/agent-journal)"


def load_json(path: str) -> object:
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        return json.load(f)


def save_json(path: str, data: object) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
        f.write("\n")


def load_watchlist() -> list[dict]:
    return load_json(WATCHLIST_PATH)


def load_published_entries_github_repos() -> list[str]:
    """Extract github field from all published entries."""
    repos = []
    
    if not os.path.exists(ENTRIES_DIR):
        return repos
    
    for root, dirs, files in os.walk(ENTRIES_DIR):
        for filename in files:
            if not filename.endswith(".md"):
                continue
            
            filepath = os.path.join(root, filename)
            try:
                post = frontmatter.load(filepath)
                metadata = post.metadata
                
                if metadata.get("status") == "published":
                    github = metadata.get("github")
                    if github:
                        repos.append(github)
            except Exception as e:
                print(f"[WARN] Could not parse {filepath}: {e}")
    
    return repos


def github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_dependents(github_repo: str) -> list[dict]:
    """Fetch recent dependents from GitHub."""
    dependents = []
    
    # Clean repo name
    repo = github_repo.strip("/")
    
    # Use the network/dependents page (parsed as HTML)
    url = f"https://github.com/{repo}/network/dependents"
    
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # Find dependent packages/repos
        # The page shows packages in a table
        for row in soup.select("div[data-dependencies-grid] a"):
            link = row.get("href", "")
            if not link or "//" not in link:
                continue
            
            dep_repo = link.strip("/")
            dependents.append(dep_repo)
        
        # Alternative parsing if data-dependencies-grid not found
        if not dependents:
            for link in soup.select("a[data-octo-dimensions='link']"):
                href = link.get("href", "")
                if href and "/" in href and not href.startswith("#"):
                    dependents.append(href.strip("/"))
        
    except requests.RequestException as e:
        print(f"[WARN] Failed to fetch dependents for {repo}: {e}")
    
    return dependents


def fetch_repo_details(github_repo: str) -> dict | None:
    """Get repo details (stars, created date, language)."""
    repo = github_repo.strip("/")
    url = f"{GITHUB_API}/repos/{repo}"
    
    try:
        resp = requests.get(url, headers=github_headers(), timeout=30)
        resp.raise_for_status()
        data = resp.json()
        
        return {
            "name": data.get("name"),
            "full_name": data.get("full_name"),
            "description": data.get("description"),
            "stargazers_count": data.get("stargazers_count", 0),
            "language": data.get("language"),
            "created_at": data.get("created_at"),
        }
    except requests.RequestException as e:
        print(f"[WARN] Failed to fetch repo {repo}: {e}")
        return None


def infer_ecosystem(language: str = None, name: str = "") -> list[str]:
    """Infer ecosystem from language and name."""
    matches = set()
    
    if language:
        lang = language.lower()
        if lang == "python":
            matches.add("python")
        elif lang in ["typescript", "javascript"]:
            matches.add("typescript")
    
    text = (name or "").lower()
    
    # Next.js signals
    if any(k in text for k in ["next", "react", "vercel"]):
        matches.add("nextjs")
    
    # LLM tooling signals
    if any(k in text for k in ["llm", "langchain", "openai", "anthropic", "agent", "mcp", "embedding"]):
        matches.add("llm-tooling")
    
    # Infrastructure signals  
    if any(k in text for k in ["docker", "k8s", "kubernetes", "terraform", "observability", "otel"]):
        matches.add("infrastructure")
    
    # Default to llm-tooling if nothing matched but has some signal
    if not matches:
        matches.add("llm-tooling")
    
    return list(matches)


def is_recent(created_at: str, days: int = 90) -> bool:
    """Check if repo was created in the last N days."""
    if not created_at:
        return False
    
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - created.replace(tzinfo=timezone.utc)
        return age.days < days
    except Exception:
        return False


def main() -> None:
    print("[INFO] Starting discover_dependents...")
    
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("[WARN] GITHUB_TOKEN not set. Skipping dependent discovery.")
        save_json(OUTPUT_PATH, [])
        return
    
    # Get repos being covered in the journal
    entry_repos = load_published_entries_github_repos()
    print(f"[INFO] Found {len(entry_repos)} published entry repos to check")
    
    # Also include from watchlist as fallback
    watchlist = load_watchlist()
    for item in watchlist:
        github = item.get("github")
        if github and github not in entry_repos:
            entry_repos.append(github)
    
    print(f"[INFO] Total repos to check: {len(entry_repos)}")
    
    # Get watched repos to exclude
    watched = {item.get("github", "").lower() for item in watchlist}
    
    candidates = []
    
    for repo in entry_repos:
        print(f"[INFO] Checking dependents of {repo}...")
        
        dependents = fetch_dependents(repo)
        print(f"  [INFO] Found {len(dependents)} dependents")
        
        for dep in dependents[:20]:  # limit checks
            if dep.lower() in watched:
                continue
            
            # Get repo details
            details = fetch_repo_details(dep)
            if not details:
                continue
            
            stars = details.get("stargazers_count", 0)
            if stars < 10:
                continue
            
            created = details.get("created_at", "")
            if not is_recent(created, days=90):
                continue
            
            ecosystem = infer_ecosystem(details.get("language"), details.get("name"))
            
            candidates.append({
                "name": details.get("name"),
                "github": details.get("full_name"),
                "description": details.get("description"),
                "stars": stars,
                "ecosystem": ecosystem,
                "detected_by": "dependents",
            })
            print(f"  [FOUND] {dep}: {stars} stars, created {created[:10]}")
            
            time.sleep(1)  # rate limit between individual repo fetches
        
        time.sleep(2)  # rate limit between dependent page fetches
    
    # Save output
    detected_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for c in candidates:
        c["detected_at"] = detected_at
    
    save_json(OUTPUT_PATH, candidates)
    
    print(f"\n[INFO] Total candidates: {len(candidates)}")
    print(f"[INFO] Written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()