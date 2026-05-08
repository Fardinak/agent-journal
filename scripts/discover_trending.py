#!/usr/bin/env python3
"""
Discover Trending — finds emerging repos from GitHub Trending and HackerNews.

Outputs candidates to candidates_trending.json.

Required: beautifulsoup4, requests
Usage: python scripts/discover_trending.py
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
WATCHLIST_PATH = os.path.join(ROOT_DIR, "watchlist.json")
OUTPUT_PATH = os.path.join(ROOT_DIR, "candidates_trending.json")

GITHUB_API = "https://api.github.com"
HN_API = "https://hn.algolia.com/api/v1/search"
USER_AGENT = "agent-journal-discovery/1.0 (https://github.com/Fardinak/agent-journal)"

TRENDING_LANGUAGES = ["python", "typescript", "javascript", "ruby"]

# Ecosystem mapping from prompt
ECOSYSTEM_SIGNALS = {
    "python": ["python", "pip", "pypi", "django", "flask", "fastapi", "pydantic"],
    "typescript": ["typescript", "deno", "bun", "tsx", "ts"],
    "nextjs": ["next.js", "nextjs", "next ", "vercel", "app router", "server components"],
    "llm-tooling": ["llm", "langchain", "openai", "anthropic", "embedding", "rag", "vector", "agent", "mcp", "model context"],
    "infrastructure": ["docker", "kubernetes", "k8s", "terraform", "helm", "observability", "otel", "tracing", "deployment"],
}


def load_json(path: str) -> object:
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        return json.load(f)


def save_json(path: str, data: object) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
        f.write("\n")


def load_watchlist_github_repos() -> set[str]:
    """Get set of already-watched GitHub repos."""
    watchlist = load_json(WATCHLIST_PATH)
    repos = set()
    for item in watchlist:
        github = item.get("github", "")
        if github:
            repos.add(github.lower())
    return repos


def infer_ecosystem(description: str, language: str = None) -> list[str]:
    """Infer ecosystem from description and language."""
    matches = set()
    text = (description or "").lower()
    
    # Check language first
    if language:
        lang_lower = language.lower()
        if lang_lower in ["python"]:
            matches.add("python")
        elif lang_lower in ["typescript", "javascript"]:
            matches.add("typescript")
            # Check if it's next.js related
            if "next" in text or "react" in text:
                matches.add("nextjs")
    
    # Check keyword signals
    for ecosystem, keywords in ECOSYSTEM_SIGNALS.items():
        for keyword in keywords:
            if keyword in text:
                matches.add(ecosystem)
    
    return list(matches) if matches else ["llm-tooling"]  # default


def parse_github_trending() -> list[dict]:
    """Scrape GitHub Trending for repos."""
    candidates = []
    watched_repos = load_watchlist_github_repos()
    
    for lang in TRENDING_LANGUAGES:
        print(f"[INFO] Fetching GitHub Trending for {lang}...")
        
        url = f"https://github.com/trending/{lang}?since=weekly"
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            
            # Find article elements for repos
            articles = soup.select("article Box-row")
            if not articles:
                articles = soup.select("div.Box-row")
            
            for article in articles:
                # Get repo name
                repo_link = article.select_one("a[data-hovercard-type='repository']")
                if not repo_link:
                    continue
                
                repo_full = repo_link.get("href", "").strip("/")
                if not repo_full or "/" not in repo_full:
                    continue
                
                # Skip if already watched
                if repo_full.lower() in watched_repos:
                    print(f"  [SKIP] {repo_full} in watchlist")
                    continue
                
                # Get description
                desc_elem = article.select_one("p")
                description = desc_elem.get_text(strip=True) if desc_elem else ""
                
                # Get stars this week
                stars_elem = article.select_one("a[href$='/stargazers']")
                stars_this_week = 0
                if stars_elem:
                    stars_text = stars_elem.get_text(strip=True)
                    match = re.search(r"([\d,]+)\s+stars?", stars_text)
                    if match:
                        stars_this_week = int(match.group(1).replace(",", ""))
                
                if stars_this_week < 20:  # minimum threshold
                    continue
                
                ecosystem = infer_ecosystem(description, lang)
                
                candidates.append({
                    "name": repo_full.split("/")[-1],
                    "github": repo_full,
                    "description": description,
                    "stars_this_week": stars_this_week,
                    "ecosystem": ecosystem,
                    "detected_by": "github_trending",
                })
                print(f"  [FOUND] {repo_full}: {stars_this_week} stars this week")
                
        except requests.RequestException as e:
            print(f"[WARN] Failed to fetch GitHub Trending for {lang}: {e}", file=sys.stderr)
        
        time.sleep(1)  # rate limit
    
    return candidates


def extract_url_from_text(text: str) -> str | None:
    """Extract URL from text."""
    url_match = re.search(r'https?://[^\s<>"\']+', text)
    return url_match.group(0) if url_match else None


def resolve_github_link(url: str) -> str | None:
    """Convert GitHub URL to org/repo format."""
    # Various GitHub URL formats
    patterns = [
        r'github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$',
        r'github\.com/([^/]+)/([^/]+)/tree/[^/]+',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return f"{match.group(1)}/{match.group(2)}"
    
    return None


def parse_hn_show() -> list[dict]:
    """Query HN Show HN for repos/packages."""
    candidates = []
    watched_repos = load_watchlist_github_repos()
    
    # Calculate timestamp 7 days ago
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    ts = int(cutoff.timestamp())
    
    print(f"[INFO] Querying HN Show HN (since {cutoff.date()})...")
    
    params = {
        "query": "show hn",
        "tags": "story,show_hn",
        "numericFilters": f"created_at_i>{ts},points>30",
        "hitsPerPage": 50,
    }
    
    try:
        resp = requests.get(HN_API, params=params, headers={"User-Agent": USER_AGENT}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        
        for hit in data.get("hits", []):
            title = hit.get("title", "")
            url = hit.get("url", "")
            text = hit.get("text", "")
            
            github_repo = None
            
            # Try to get GitHub link from post URL
            if url:
                github_repo = resolve_github_link(url)
            
            # Try to extract from HN text content
            if not github_repo and text:
                extracted_url = extract_url_from_text(text)
                if extracted_url:
                    github_repo = resolve_github_link(extracted_url)
            
            if not github_repo:
                continue
            
            if github_repo.lower() in watched_repos:
                print(f"  [SKIP] {github_repo} in watchlist")
                continue
            
            # Get points to gauge interest
            points = hit.get("points", 0)
            
            ecosystem = infer_ecosystem(title)
            
            candidates.append({
                "name": github_repo.split("/")[-1],
                "github": github_repo,
                "description": title,
                "hn_points": points,
                "ecosystem": ecosystem,
                "detected_by": "hn_show",
            })
            print(f"  [FOUND] {github_repo}: {points} HN points")
            
    except requests.RequestException as e:
        print(f"[WARN] Failed to query HN: {e}", file=sys.stderr)
    
    return candidates


def main() -> None:
    print("[INFO] Starting discover_trending...")
    
    gh_candidates = parse_github_trending()
    print(f"[INFO] GitHub Trending found {len(gh_candidates)} candidates")
    
    # Reset watched_repos to allow HN to find different items
    # (don't skip repos already found by GH Trending)
    
    hn_candidates = parse_hn_show()
    print(f"[INFO] HN Show HN found {len(hn_candidates)} candidates")
    
    # Combine candidates
    all_candidates = gh_candidates + hn_candidates
    
    # Deduplicate by github
    seen = {}
    for c in all_candidates:
        key = c["github"].lower()
        if key not in seen:
            seen[key] = c
        else:
            # Merge discovered_by
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