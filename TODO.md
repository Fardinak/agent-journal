# Agent Journal - TODO

## Recently Completed (pushed to origin/main)

### 1. Critical: Timezone bug in signal_detection.py
- **Issue**: `datetime.now(timezone.utc)` returns naive datetime in Python 3.12
- **Fix**: Changed to `datetime.now().replace(tzinfo=timezone.utc)`
- **Commit**: f8e6849

### 2. Signal Detection - Logging
- **Issue**: Ran for 20 minutes with no output
- **Fix**: Added progress indicators, fetch timing, star delta metrics
- **Commit**: f8e6849

### 3. Workflow Fixes
- Add `ref: main` to checkouts (d47f454)
- PAGES_BASE_URL env var (3ce8475)
- deploy-pages runs after generate-feeds via workflow_run (d47f454)

### 4. ai_draft.py
- User-Agent header (c03f342)
- Unicode slugify (c03f342)
- Response validation (c03f342)

### 5. Jekyll Site
- Entries collection path config (6588a08)
- Copy entries in workflow (6588a08)
- CSS was already in place ✅

---

## Not Yet Implemented
- GitHub API rate limit backoff (low priority - token helps)

---

## Push Log
All commits pushed via `git push origin main`