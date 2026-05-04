# Agent Journal

> Curated intelligence for AI coding agents and the engineers who build them.

Not changelogs — significance judgments. What emerged, why it matters, when to reach for it.

## What Is This

Agent Journal is a machine-readable intelligence feed that surfaces significant developments in developer tooling — specifically the tools, frameworks, and infrastructure that matter to teams building AI-powered software.

Each entry is a structured document (YAML frontmatter + Markdown body) that answers:

- **What problem does this solve?** (Concretely. Name the pain.)
- **What existed before, and why isn't it good enough anymore?**
- **What makes this the better approach?**
- **When should a senior engineer reach for this?**
- **What are the known limitations?**

The editorial philosophy is **signal over noise**. We do not report version bumps or feature additions. We report shifts — tools that change how engineers work, approaches that displace prior art, and emerging patterns worth tracking.

## Consuming the Feeds

All feeds are JSON and publicly accessible. They are the primary interface — the website is for humans, the feeds are for agents.

| Feed | URL |
|------|-----|
| All entries | `https://agent-journal.github.io/feeds/index.json` |
| Python | `https://agent-journal.github.io/feeds/python.json` |
| TypeScript | `https://agent-journal.github.io/feeds/typescript.json` |
| Next.js | `https://agent-journal.github.io/feeds/nextjs.json` |
| LLM Tooling | `https://agent-journal.github.io/feeds/llm-tooling.json` |
| Infrastructure | `https://agent-journal.github.io/feeds/infrastructure.json` |

### Entry Schema

Each entry in a feed contains:

| Field | Type | Description |
|-------|------|-------------|
| `title` | string | Makes a significance judgment, not just "X launches Y" |
| `date` | string | Publication date (YYYY-MM-DD) |
| `ecosystem` | string[] | One or more: `python`, `typescript`, `nextjs`, `llm-tooling`, `infrastructure` |
| `category` | string | One of: `observability`, `auth`, `deployment`, `testing`, `data`, `llm-framework`, `dx`, `infrastructure`, `security` |
| `significance` | string | `high`, `medium`, or `low` |
| `displaces` | string[] | Tools/approaches this replaces or makes less relevant |
| `complements` | string[] | Tools/approaches this works well alongside |
| `reach_for_when` | string | One-sentence trigger condition for when to use this |
| `url` | string | Link to the full entry on the website |
| `source_file` | string | Path to the original Markdown file in the repo |

### Programmatic Access

```python
import requests

# Fetch all published entries
response = requests.get("https://agent-journal.github.io/feeds/index.json")
data = response.json()

for entry in data["entries"]:
    print(f"[{entry['significance']}] {entry['title']}")
    print(f"  → {entry['reach_for_when']}")
    print()
```

## How It Works

### The Pipeline

```
[Signal Detection] → [candidates.json] → [AI Draft] → [PR] → [Human Review] → [Merge] → [Regenerate Feeds + Deploy Site]
```

1. **Signal Detection** — A daily GitHub Actions job monitors GitHub stars and npm downloads for anomalies across a curated watchlist (`watchlist.json`). When a repo or package shows unusual growth, it's added to `candidates.json`.
2. **AI Draft** — Triggered manually via `workflow_dispatch`, an AI research agent fetches the candidate's README, release notes, and homepage, then writes a draft entry using a configurable OpenAI-compatible LLM. The draft is submitted as a PR.
3. **Human Review** — An editor reviews the PR using the checklist in the PR template. If approved, they change `status` from `draft` to `published` and merge.
4. **Feed Generation** — On merge, a workflow regenerates all JSON feeds from published entries.
5. **Site Deploy** — The GitHub Pages site is rebuilt and redeployed automatically.

### Adding to the Watchlist

Submit a PR that adds an entry to `watchlist.json`:

```json
{
  "name": "ToolName",
  "github": "org/repo",
  "npm": "package-name",
  "ecosystem": ["python", "llm-tooling"]
}
```

Fields:
- `name` — Human-readable name (required)
- `github` — GitHub repo in `org/repo` format (required)
- `npm` — npm package name (optional, set to `null` if not applicable)
- `ecosystem` — Array of ecosystem tags (required)

### Triggering a Manual AI Draft

1. Go to **Actions** → **AI Research Draft** in your fork/repo
2. Click **Run workflow**
3. Enter the candidate name (must match an entry in `candidates.json`)
4. Click **Run workflow**

A PR will be opened with the draft entry for review.

### Local Development

```bash
# Install Python dependencies
pip install requests openai python-frontmatter

# Run signal detection locally
python scripts/signal_detection.py

# Generate feeds locally
python scripts/generate_feeds.py

# Run Jekyll site locally (from site/ directory)
cd site
bundle install
bundle exec jekyll serve
```

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `LLM_BASE_URL` | No | OpenAI-compatible API base URL (default: `https://opencode.ai/zen/v1`) |
| `LLM_API_KEY` | Yes | API key for your LLM provider |
| `LLM_MODEL` | No | Model name (default: `opencode/big-pickle`) |
| `GITHUB_TOKEN` | No | GitHub API token (auto-provided in Actions) |

Override `LLM_BASE_URL` and `LLM_MODEL` locally or in the workflow YAML if you need different defaults.

### Secrets

Configure `LLM_API_KEY` as a repository secret. `LLM_BASE_URL` and `LLM_MODEL` have defaults in the script — override them in the workflow YAML if needed.

All other workflows use the built-in `GITHUB_TOKEN`.

## Project Structure

```
agent-journal/
├── .github/workflows/        # CI/CD pipelines
├── entries/                  # Published and draft entries (Markdown + frontmatter)
│   ├── python/
│   ├── typescript/
│   ├── nextjs/
│   ├── llm-tooling/
│   └── infrastructure/
├── feeds/                    # Auto-generated JSON feeds (do not edit manually)
├── scripts/                  # Signal detection, AI drafting, feed generation
├── site/                     # Jekyll source for GitHub Pages
├── watchlist.json            # Curated list of repos/packages to monitor
├── candidates.json           # Queue of detected signals pending research
└── README.md
```

## License

MIT
