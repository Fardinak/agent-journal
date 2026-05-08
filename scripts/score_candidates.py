#!/usr/bin/env python3
"""
Score Candidates — merges all discovery output, scores, and writes candidates.json.

Merges candidates from:
- candidates_trending.json (discover_trending.py)
- candidates_registry.json (discover_registry.py)
- candidates_dependents.json (discover_dependents.py)
- candidates_watchlist.json (watchlist_monitor.py)

Outputs scored candidates to candidates.json.

Usage: python scripts/score_candidates.py [--include-low]
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import frontmatter
import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)

CANDIDATE_SOURCES = [
    "candidates_trending.json",
    "candidates_registry.json", 
    "candidates_dependents.json",
    "candidates_watchlist.json",
]

ENTRIES_DIR = os.path.join(ROOT_DIR, "entries")
OUTPUT_PATH = os.path.join(ROOT_DIR, "candidates.json")

# Ecosystem mapping (from prompt)
ECOSYSTEM_SIGNALS = {
    "python": ["python", "pip", "pypi", "django", "flask", "fastapi", "pydantic"],
    "typescript": ["typescript", "deno", "bun", "tsx", "ts"],
    "nextjs": ["next.js", "nextjs", "next ", "vercel", "app router", "server components"],
    "llm-tooling": ["llm", "langchain", "openai", "anthropic", "embedding", "rag", "vector", "agent", "mcp", "model context"],
    "infrastructure": ["docker", "kubernetes", "k8s", "terraform", "helm", "observability", "otel", "tracing", "deployment"],
}

FEED_ECOSYSTEMS = ["python", "typescript", "nextjs", "llm-tooling", "infrastructure"]


def load_json(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        data = json.load(f)
        # Handle both array and object with 'entries' key
        if isinstance(data, dict) and "entries" in data:
            return data["entries"]
        return data


def save_json(path: str, data: object) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
        f.write("\n")


def load_existing_candidates() -> list[dict]:
    """Load existing candidates.json, preserving drafted/published ones."""
    return load_json(OUTPUT_PATH)


def load_displacement_terms() -> set[str]:
    """Extract all values from 'displaces' fields across published entries."""
    terms = set()
    
    if not os.path.exists(ENTRIES_DIR):
        return terms
    
    for root, dirs, files in os.walk(ENTRIES_DIR):
        for filename in files:
            if not filename.endswith(".md"):
                continue
            
            filepath = os.path.join(root, filename)
            try:
                post = frontmatter.load(filepath)
                metadata = post.metadata
                
                if metadata.get("status") == "published":
                    displaces = metadata.get("displaces", [])
                    if isinstance(displaces, list):
                        for term in displaces:
                            terms.add(term.lower())
                    elif isinstance(displaces, str):
                        terms.add(displaces.lower())
            except Exception as e:
                print(f"[WARN] Could not parse {filepath}: {e}")
    
    return terms


def count_published_per_ecosystem() -> dict[str, int]:
    """Count published entries per ecosystem."""
    counts = {e: 0 for e in FEED_ECOSYSTEMS}
    
    if not os.path.exists(ENTRIES_DIR):
        return counts
    
    for root, dirs, files in os.walk(ENTRIES_DIR):
        for filename in files:
            if not filename.endswith(".md"):
                continue
            
            filepath = os.path.join(root, filename)
            try:
                post = frontmatter.load(filepath)
                metadata = post.metadata
                
                if metadata.get("status") == "published":
                    ecosystems = metadata.get("ecosystem", [])
                    for e in ecosystems:
                        if e in counts:
                            counts[e] += 1
            except Exception:
                pass
    
    return counts


def readme_mentions_incumbent(github_repo: str, displacement_terms: set[str]) -> bool:
    """Check if repo README mentions any displacement terms."""
    if not github_repo or not displacement_terms:
        return False
    
    try:
        # Get default branch and readme
        repo = github_repo.strip("/")
        url = f"https://api.github.com/repos/{repo}/readme"
        
        resp = requests.get(url, timeout=15, headers={
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "agent-journal-discovery/1.0",
        })
        
        if resp.status_code != 200:
            return False
        
        data = resp.json()
        content = data.get("content", "")
        if not content:
            return False
        
        # Decode base64
        import base64
        try:
            content = base64.b64decode(content).decode("utf-8")
        except Exception:
            return False
        
        # Check first paragraph
        content = content.lower()
        first_para = content.split("\n\n")[0] if "\n\n" in content else content[:500]
        
        for term in displacement_terms:
            if term in first_para:
                return True
        
    except Exception as e:
        pass
    
    return False


def parse_date_to_age(date_str: str | None) -> float | None:
    """Parse various date formats to age in days."""
    if not date_str:
        return None
    
    for fmt in ["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"]:
        try:
            dt = datetime.strptime(date_str[:19], fmt)
            age = datetime.now(timezone.utc) - dt.replace(tzinfo=timezone.utc)
            return age.days
        except ValueError:
            continue
    
    return None


def score_source_quality(candidate: dict) -> int:
    """Score based on discovery sources (1-3)."""
    discovered_via = candidate.get("discovered_via", [])
    detected_by = candidate.get("detected_by", [])
    
    # Collect all sources
    sources = set()
    if isinstance(discovered_via, list):
        sources.update(discovered_via)
    if isinstance(detected_by, list):
        sources.update(detected_by)
    if isinstance(detected_by, str) and detected_by:
        sources.add(detected_by)
    
    count = len(sources)
    
    # Watchlist and high-signal sources are worth more
    if "watchlist_monitor" in sources:
        return 2  # Curated watchlist is reliable
    
    if count == 0:
        return 1
    return min(3, count)


def score_ecosystem_fit(ecosystems: list[str], ecosystem_counts: dict[str, int]) -> int:
    """Score ecosystem fit (more underrepresented = higher score)."""
    if not ecosystems:
        return 0
    
    # Check against feeds
    valid = [e for e in ecosystems if e in FEED_ECOSYSTEMS]
    if not valid:
        return 0
    
    # Count published ecosystem entries
    for e in sorted(valid, key=lambda x: ecosystem_counts.get(x, 999)):
        count = ecosystem_counts.get(e, 0)
        if count < 2:
            return 3  # underserved ecosystem
        if count < 5:
            return 2
    
    return 1


def score_recency(created_at: str | None) -> int:
    """Score recency of repo/package creation."""
    if not created_at:
        return 1  # assume moderate age
    
    age_days = parse_date_to_age(created_at)
    if age_days is None:
        return 1
    
    if age_days < 30:
        return 3  # < 1 month
    elif age_days < 90:
        return 2  # 1-3 months
    elif age_days < 365:
        return 1  # 3-12 months
    else:
        return 0  # > 1 year


def score_displacement_signal(github_repo: str, description: str, displacement_terms: set[str]) -> int:
    """Score whether candidate displaces known tools."""
    text = (description or "").lower()
    
    # Check README
    if github_repo and readme_mentions_incumbent(github_repo, displacement_terms):
        return 3
    
    # Check description for explicit mentions
    for term in displacement_terms:
        if term in text:
            return 3
    
    # Check for generic category mentions
    generic_categories = ["alternative to", "replacement for", "successor", "successor to", "better than"]
    for cat in generic_categories:
        if cat in text:
            return 1
    
    return 0


def merge_candidates(all_candidates: list[dict]) -> list[dict]:
    """Merge candidates by github/npm/pypi field."""
    by_key = {}
    
    for c in all_candidates:
        # Determine key
        github = c.get("github", "")
        npm = c.get("npm", "")
        pypi = c.get("pypi", "")
        
        keys = []
        if github:
            keys.append(f"github:{github.lower()}")
        if npm:
            keys.append(f"npm:{npm.lower()}")
        if pypi:
            keys.append(f"pypi:{pypi.lower()}")
        
        if not keys:
            continue
        
        # Use primary key
        primary_key = keys[0]
        
        if primary_key not in by_key:
            by_key[primary_key] = {
                "name": c.get("name"),
                "github": c.get("github"),
                "npm": c.get("npm"),
                "pypi": c.get("pypi"),
                "description": c.get("description"),
                "ecosystem": c.get("ecosystem", []),
                "created_at": c.get("created_at"),
                "discovered_via": set(),
                "stars": c.get("stars"),
                "stars_this_week": c.get("stars_this_week"),
                "hn_points": c.get("hn_points"),
                "downloads_last_week": c.get("downloads_last_week"),
            }
        
        # Merge discovered_via
        discovered_by = c.get("detected_by", "") or c.get("detected_via", [])
        if isinstance(discovered_by, str) and discovered_by:
            by_key[primary_key]["discovered_via"].add(discovered_by)
        elif isinstance(discovered_by, list):
            for d in discovered_by:
                if d:
                    by_key[primary_key]["discovered_via"].add(d)
        
        # Merge other fields
        if not by_key[primary_key]["github"] and c.get("github"):
            by_key[primary_key]["github"] = c["github"]
        if not by_key[primary_key]["npm"] and c.get("npm"):
            by_key[primary_key]["npm"] = c["npm"]
        if not by_key[primary_key]["pypi"] and c.get("pypi"):
            by_key[primary_key]["pypi"] = c["pypi"]
        
        # Take highest values (also pick up stars_delta as fallback)
        if not by_key[primary_key]["stars"] and c.get("stars"):
            by_key[primary_key]["stars"] = c["stars"]
        if not by_key[primary_key]["stars"] and c.get("stars_delta"):
            by_key[primary_key]["stars"] = c["stars_delta"]
        if not by_key[primary_key]["stars_this_week"] and c.get("stars_this_week"):
            by_key[primary_key]["stars_this_week"] = c["stars_this_week"]
        if not by_key[primary_key]["hn_points"] and c.get("hn_points"):
            by_key[primary_key]["hn_points"] = c["hn_points"]
    
    # Convert back to list
    result = []
    for key, data in by_key.items():
        data["discovered_via"] = list(data["discovered_via"])
        result.append(data)
    
    return result


def main() -> None:
    include_low = "--include-low" in sys.argv
    
    print("[INFO] Starting score_candidates...")
    
    # Load all discovery outputs
    all_candidates = []
    for source in CANDIDATE_SOURCES:
        path = os.path.join(ROOT_DIR, source)
        candidates = load_json(path)
        if candidates:
            print(f"[INFO] Loaded {len(candidates)} from {source}")
            all_candidates.extend(candidates)
        else:
            print(f"[INFO] No candidates from {source}")
    
    if not all_candidates:
        print("[INFO] No candidates found. Writing empty array.")
        save_json(OUTPUT_PATH, [])
        return
    
    # Load existing candidates (preserve drafted/published)
    existing = load_existing_candidates()
    preserved = [c for c in existing if c.get("status") in ["drafted", "published"]]
    print(f"[INFO] Preserving {len(preserved)} drafted/published candidates")
    
    # Merge and deduplicate
    merged = merge_candidates(all_candidates)
    print(f"[INFO] Merged to {len(merged)} unique candidates")
    
    # Get scoring context
    displacement_terms = load_displacement_terms()
    print(f"[INFO] Loaded {len(displacement_terms)} displacement terms")
    
    ecosystem_counts = count_published_per_ecosystem()
    print(f"[INFO] Ecosystem counts: {ecosystem_counts}")
    
    # Score each candidate
    scored = []
    detected_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    for c in merged:
        # Score components
        source_quality = score_source_quality(c)
        
        ecosystems = c.get("ecosystem", [])
        ecosystem_fit = score_ecosystem_fit(ecosystems, ecosystem_counts)
        
        created_at = c.get("created_at")
        recency = score_recency(created_at)
        
        github = c.get("github", "")
        description = c.get("description", "")
        displacement_signal = score_displacement_signal(github, description, displacement_terms)
        
        total_score = source_quality + ecosystem_fit + recency + displacement_signal
        
        # Confidence tier
        if total_score >= 10:
            confidence = "high"
        elif total_score >= 6:
            confidence = "medium"
        else:
            confidence = "low"
        
        # Skip low confidence unless requested
        if confidence == "low" and not include_low:
            print(f"[SKIP] {c.get('name', 'unknown')}: low confidence ({total_score})")
            continue
        
        scored.append({
            "name": c.get("name"),
            "github": c.get("github"),
            "npm": c.get("npm"),
            "pypi": c.get("pypi"),
            "ecosystem": ecosystems,
            "confidence": confidence,
            "score": total_score,
            "score_breakdown": {
                "source_quality": source_quality,
                "ecosystem_fit": ecosystem_fit,
                "recency": recency,
                "displacement_signal": displacement_signal,
            },
            "discovered_via": list(c.get("discovered_via", set())),
            "detected_at": detected_at,
            "status": "pending",
            # Legacy fields for backwards compatibility
            "repo_url": f"https://github.com/{c.get('github', '')}" if c.get("github") else "",
            "stars_delta": c.get("stars"),
            "npm_downloads_delta": c.get("downloads_last_week"),
            "pending_pr": True,
            "pr_url": None,
            "pr_created_at": None,
        })
    
    # Sort by score descending
    scored.sort(key=lambda x: x["score"], reverse=True)
    
    # Combine with preserved
    final_candidates = preserved + scored
    
    # Save output
    save_json(OUTPUT_PATH, final_candidates)
    
    high_count = sum(1 for c in scored if c.get("confidence") == "high")
    med_count = sum(1 for c in scored if c.get("confidence") == "medium")
    low_count = sum(1 for c in scored if c.get("confidence") == "low")
    
    print(f"\n[INFO] Score breakdown: {high_count} high, {med_count} medium, {low_count} low")
    print(f"[INFO] Total candidates: {len(final_candidates)}")
    print(f"[INFO] Written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()