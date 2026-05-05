#!/usr/bin/env python3
"""
AI Research Draft — takes a candidate from candidates.json and produces a
draft journal entry using an OpenAI-compatible LLM provider.

Environment variables:
  LLM_BASE_URL  — API base URL (default: https://api.openai.com/v1)
  LLM_API_KEY   — API key (required)
  LLM_MODEL     — Model name (default: gpt-4o)

Usage: python scripts/ai_draft.py --candidate "LangSmith"

WARNING: During free periods, some providers may use interaction data
to improve their models. This is acceptable for MVP but should be reviewed
before production use with proprietary signals.
"""

import argparse
import json
import os
import re
import sys
import unicodedata

import requests
from openai import OpenAI

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
CANDIDATES_PATH = os.path.join(ROOT_DIR, "candidates.json")

GITHUB_API = "https://api.github.com"

LLM_BASE_URL = os.environ.get("LLM_BASE_URL") or "https://api.openai.com/v1"
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL") or "gpt-4o"

if not LLM_API_KEY:
    print("ERROR: Set LLM_API_KEY environment variable", file=sys.stderr)
    sys.exit(1)

client = OpenAI(
    api_key=LLM_API_KEY,
    base_url=LLM_BASE_URL,
)


def load_candidates() -> list[dict]:
    if not os.path.exists(CANDIDATES_PATH):
        print(f"ERROR: {CANDIDATES_PATH} not found", file=sys.stderr)
        sys.exit(1)
    with open(CANDIDATES_PATH, "r") as f:
        return json.load(f)


def find_candidate(name: str) -> dict | None:
    candidates = load_candidates()
    for c in candidates:
        if c.get("name", "").lower() == name.lower():
            return c
    return None


def github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "AgentJournal/1.0 (AI Research Draft Script)"
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_readme(repo: str) -> str:
    """Fetch the README content from a GitHub repo."""
    repo = repo.strip("/").replace("https://github.com/", "")
    url = f"{GITHUB_API}/repos/{repo}/readme"
    try:
        resp = requests.get(url, headers=github_headers(), timeout=15)
        resp.raise_for_status()
        data = resp.json()
        # GitHub API returns base64-encoded content
        import base64

        content = base64.b64decode(data["content"]).decode("utf-8")
        return content[:8000]  # Truncate to avoid token bloat
    except requests.RequestException as e:
        print(f"[WARN] Could not fetch README for {repo}: {e}", file=sys.stderr)
        return ""
    except (KeyError, ValueError) as e:
        print(f"[WARN] Could not parse README response for {repo}: {e}", file=sys.stderr)
        return ""


def fetch_releases(repo: str) -> str:
    """Fetch the latest release notes from a GitHub repo."""
    repo = repo.strip("/").replace("https://github.com/", "")
    url = f"{GITHUB_API}/repos/{repo}/releases?per_page=3"
    try:
        resp = requests.get(url, headers=github_headers(), timeout=15)
        resp.raise_for_status()
        releases = resp.json()
        if not releases:
            return ""
        parts = []
        for r in releases[:3]:
            parts.append(f"## {r.get('name', r.get('tag_name', 'untagged'))}\n{r.get('body', '')[:500]}")
        return "\n\n".join(parts)
    except requests.RequestException as e:
        print(f"[WARN] Could not fetch releases for {repo}: {e}", file=sys.stderr)
        return ""
    except (KeyError, ValueError) as e:
        print(f"[WARN] Could not parse releases response for {repo}: {e}", file=sys.stderr)
        return ""


def fetch_homepage(repo: str) -> str:
    """Fetch the homepage URL from repo metadata and scrape it."""
    repo = repo.strip("/").replace("https://github.com/", "")
    url = f"{GITHUB_API}/repos/{repo}"
    try:
        resp = requests.get(url, headers=github_headers(), timeout=15)
        resp.raise_for_status()
        data = resp.json()
        homepage = data.get("homepage")
        if homepage:
            # Simple scrape — just grab the title and meta description
            page_resp = requests.get(homepage, timeout=10, allow_redirects=True)
            page_resp.raise_for_status()
            text = page_resp.text[:3000]
            return text
        return ""
    except requests.RequestException as e:
        print(f"[WARN] Could not fetch homepage for {repo}: {e}", file=sys.stderr)
        return ""
    except (KeyError, ValueError) as e:
        print(f"[WARN] Could not parse homepage response for {repo}: {e}", file=sys.stderr)
        return ""


import unicodedata


def slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    # Normalize unicode characters (NFC)
    text = unicodedata.normalize("NFC", text)
    text = text.lower().strip()
    # Keep only word characters, spaces, and hyphens (including unicode word chars)
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def build_prompt(candidate: dict, readme: str, releases: str, homepage: str) -> str:
    github_url = candidate.get("repo_url", "")
    ecosystem = ", ".join(candidate.get("ecosystem", []))
    stars_delta = candidate.get("stars_delta", "N/A")

    signal_str = f"{stars_delta} GitHub stars gained in the past 7 days" if stars_delta else "significant npm download growth detected"

    return f"""You are a senior engineer writing for Agent Journal — a curated intelligence feed for AI coding agents and the engineers who build them.

Your task is to write a journal entry for the following tool/library:

Name: {candidate["name"]}
GitHub: {github_url}
Ecosystem: {ecosystem}
Signal: {signal_str}

Here is the raw context you have gathered:
<readme>{readme}</readme>
<release_notes>{releases}</release_notes>

Write a structured entry that answers these questions precisely:
1. What specific problem does this solve? (Be concrete. Name the pain.)
2. What did engineers use before this, and why is that no longer good enough?
3. What makes this the better approach? (Architecture, DX, performance — be specific.)
4. When would a senior engineer reach for this? Give a one-sentence trigger condition.
5. What are the known limitations or early caveats?

Rules:
- Do not summarize the README. Make significance judgments.
- Do not use marketing language from the source material.
- Write 2-4 paragraphs of body text. Dense. No fluff.
- Also output the YAML frontmatter fields: title, category, significance,
  displaces (list), complements (list), reach_for_when (one sentence).

Respond ONLY with the complete markdown file including frontmatter. No preamble."""


def validate_response(content: str) -> bool:
    """Validate that the LLM response has proper frontmatter and body."""
    if not content:
        print("ERROR: Empty response from API", file=sys.stderr)
        return False
    if "---" not in content:
        print("ERROR: Response missing frontmatter delimiter ---", file=sys.stderr)
        return False
    parts = content.split("---", 2)
    if len(parts) < 3:
        print("ERROR: Response missing frontmatter or body", file=sys.stderr)
        return False
    frontmatter = parts[1]
    required_fields = ["title:", "date:", "ecosystem:", "category:", "significance:", "reach_for_when:"]
    missing = [f for f in required_fields if f not in frontmatter]
    if missing:
        print(f"ERROR: Response missing frontmatter fields: {', '.join(missing)}", file=sys.stderr)
        return False
    return True
    """
    Write the draft entry to the correct ecosystem directory.
    Returns the file path.
    """
    ecosystem = candidate.get("ecosystem", ["llm-tooling"])
    primary_ecosystem = ecosystem[0] if ecosystem else "llm-tooling"

    # Ensure the YAML frontmatter has status: draft
    # Parse the content to inject status if missing
    if "---" in content:
        parts = content.split("---", 2)
        if len(parts) >= 3:
            frontmatter = parts[1].strip()
            body = parts[2].strip()
            # Check if status field exists
            if "status:" not in frontmatter:
                # Add status before the closing ---
                frontmatter = frontmatter.rstrip() + "\nstatus: draft"
            content = f"---\n{frontmatter}\n---\n\n{body}"

    # Extract title from frontmatter for filename
    title_match = re.search(r"title:\s*[\"']?([^\"'\n]+)[\"']?", content)
    title = title_match.group(1) if title_match else candidate["name"]
    slug = slugify(title)

    # Extract date — use today if not found
    date_match = re.search(r"date:\s*(\d{4}-\d{2}-\d{2})", content)
    date_str = date_match.group(1) if date_match else ""
    if not date_str:
        from datetime import datetime

        date_str = datetime.now().strftime("%Y-%m-%d")

    filename = f"{date_str}-{slug}.md"
    ecosystem_dir = os.path.join(ROOT_DIR, "entries", primary_ecosystem)
    os.makedirs(ecosystem_dir, exist_ok=True)

    filepath = os.path.join(ecosystem_dir, filename)
    with open(filepath, "w") as f:
        f.write(content)
        f.write("\n")

    return filepath


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a draft journal entry from a candidate")
    parser.add_argument("--candidate", required=True, help="Name of the candidate from candidates.json")
    args = parser.parse_args()

    candidate = find_candidate(args.candidate)
    if candidate is None:
        print(f"ERROR: Candidate '{args.candidate}' not found in candidates.json", file=sys.stderr)
        sys.exit(1)

    repo = candidate.get("repo_url", "")
    if not repo:
        print("ERROR: Candidate has no repo_url", file=sys.stderr)
        sys.exit(1)

    print(f"Fetching context for {candidate['name']}...")
    readme = fetch_readme(repo)
    releases = fetch_releases(repo)
    homepage = fetch_homepage(repo)

    if not readme and not releases:
        print("ERROR: Could not fetch any context (README or releases)", file=sys.stderr)
        sys.exit(1)

    print("Calling LLM API...")
    prompt = build_prompt(candidate, readme, releases, homepage)

    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content
        if not validate_response(content):
            print("ERROR: Invalid response from API", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"ERROR: API call failed: {e}", file=sys.stderr)
        sys.exit(1)

    filepath = write_entry(candidate, content)
    print(f"Draft entry written to: {filepath}")


if __name__ == "__main__":
    main()
