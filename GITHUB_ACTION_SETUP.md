# GitHub Action Setup - PR Review Agent

This document explains how the PR review agent is configured to run as a GitHub Action.

## Files Created

1. **`scripts/run_action.py`** - Entry script that runs within the Action
2. **`.github/workflows/pr-review.yml`** - Workflow definition

---

## How It Works

### A. Entry Script: `scripts/run_action.py`

**How it gets the PR number**:
```python
# 1. Read event file path from env
event_path = os.environ.get('GITHUB_EVENT_PATH')

# 2. Load the JSON event payload
with open(event_path, 'r') as f:
    event = json.load(f)

# 3. Extract PR number from event
pr_number = event['pull_request']['number']
```

**GitHub Actions automatically sets**:
- `GITHUB_EVENT_PATH`: Path to JSON file with event payload (e.g., `/home/runner/work/_temp/_github_workflow/event.json`)
- `GITHUB_REPOSITORY`: Repository in "owner/repo" format

**The script**:
1. Loads event payload from `GITHUB_EVENT_PATH`
2. Checks if it's a `pull_request` event (gracefully skips if not)
3. Extracts `pr_number` from `event['pull_request']['number']`
4. Reads `GITHUB_REPOSITORY` from env (format: "owner/repo")
5. Reads `GITHUB_TOKEN` and `ANTHROPIC_API_KEY` from env
6. Calls `run_pr_review(repository, pr_number, github_token)`
7. Prints summary to stdout (shows in Actions logs)
8. Exits 0 on success or non-fatal warnings (422 duplicate review)
9. Exits 1 only on hard failures

**Non-fatal cases** (exit 0):
- Event is not a pull_request
- 422 error (PR already reviewed)

**Hard failure cases** (exit 1):
- Missing `ANTHROPIC_API_KEY`
- Missing `GITHUB_TOKEN`
- Pipeline crashes
- Network errors (except 422)

---

### B. Workflow YAML: `.github/workflows/pr-review.yml`

**Trigger**:
```yaml
on:
  pull_request:
    types: [opened, synchronize, reopened]
```

- `opened`: New PR created
- `synchronize`: New commits pushed to existing PR
- `reopened`: Closed PR reopened

**Why these permissions?**
```yaml
permissions:
  contents: read        # Read repository code (clone, fetch files)
  pull-requests: write  # Post review comments to PR
```

- `contents: read` - Required to check out the repository and read code
- `pull-requests: write` - Required to POST review comments via GitHub API
- NO `issues: write` - Review comments are on PRs, not issues
- NO `contents: write` - Agent doesn't push code changes

**Why Python 3.11?**
- Stable LTS version widely used in GitHub Actions
- Good support in `actions/setup-python@v5`
- LangGraph/LangChain tested on 3.11
- 3.12 would also work, but 3.11 is safer choice for production

**Checkstyle installation approach**:
```yaml
# Download JAR from GitHub releases
wget https://github.com/checkstyle/checkstyle/releases/download/.../checkstyle-10.12.5-all.jar

# Create wrapper script
cat > ~/.local/bin/checkstyle << 'EOF'
#!/bin/bash
exec java -jar ~/.local/bin/checkstyle.jar "$@"
EOF
```

**Why download JAR instead of apt?**
- ✅ Faster: Direct download vs apt repo update + package install
- ✅ Version control: Pin exact Checkstyle version (10.12.5)
- ✅ Reliable: GitHub releases > Ubuntu package repos (less stale)
- ✅ Cross-distro: Same approach works on any Linux with Java

**Alternative considered**: `sudo apt-get install checkstyle`
- ❌ Slower (apt update overhead)
- ❌ May be outdated version in Ubuntu repos
- ❌ Less control over versioning

---

## Required Secrets

### 1. `ANTHROPIC_API_KEY` (MUST ADD TO REPO SECRETS)

**How to add**:
1. Go to repo Settings → Secrets and variables → Actions
2. Click "New repository secret"
3. Name: `ANTHROPIC_API_KEY`
4. Value: Your Anthropic API key (starts with `sk-ant-`)
5. Save

### 2. `GITHUB_TOKEN` (AUTO-PROVIDED)

GitHub automatically provides this — no manual setup needed.

---

## What Could Fail on First Live Run (UNTESTED)

### 🚨 Likely issues to watch in Actions logs:

#### 1. **Missing `ANTHROPIC_API_KEY` secret**
```
Error: ANTHROPIC_API_KEY environment variable not set
Add ANTHROPIC_API_KEY to repository secrets
```
**Fix**: Add the secret (see above)

#### 2. **Insufficient permissions**
```
Error: 403 Forbidden (POST /repos/owner/repo/pulls/123/comments)
```
**Fix**: Check workflow has `pull-requests: write` permission (already set)

#### 3. **Checkstyle download fails**
```
wget: unable to resolve host address 'github.com'
```
**Fix**: Network issue in Actions runner (rare; retry the workflow)

#### 4. **Checkstyle version mismatch**
```
Error: Could not find or load main class com.puppycrawl.tools.checkstyle.Main
```
**Fix**: JAR download corrupted or version URL broken — update version in workflow

#### 5. **Python dependency conflict**
```
ERROR: pip's dependency resolver does not currently take into account all the packages that are installed
```
**Fix**: Pin versions in requirements.txt more strictly

#### 6. **ripgrep not available**
```
Context search strategy not available, skipping
```
**Fix**: Check `apt-get install ripgrep` step succeeded

#### 7. **Clone failure (context search)**
```
clone_repo failed: authentication failed
```
**Fix**: GitHub token doesn't have access to target repo (private repo issue)

#### 8. **Rate limits**
```
403: API rate limit exceeded for user
```
**Fix**: GITHUB_TOKEN has higher rate limits than unauthenticated; should not hit this

#### 9. **Duplicate review comments (422)**
```
422: Validation Failed - Review comment already exists
```
**Status**: Non-fatal (exit 0) — expected when re-reviewing same commit

#### 10. **Judge/Reviewer API failures**
```
Error: Anthropic API error: rate_limit_error
```
**Fix**: Anthropic API key may have rate limits; wait and retry

---

## Testing Checklist

### Before merging this PR:
- [ ] Add `ANTHROPIC_API_KEY` to repo secrets
- [ ] Open a test PR to trigger the workflow
- [ ] Check Actions tab for workflow run
- [ ] Verify logs show "Review completed successfully!"
- [ ] Verify review comments appear on the test PR
- [ ] Check temp clone cleanup succeeded

### Expected log flow (successful run):
```
Run PR review
=== PR Review Agent - GitHub Action ===
PR Number: #123
Repository: owner/repo
Environment validated
Starting PR review...
------------------------------------------------------------
INFO: Fetching PR #123 from owner/repo
INFO: Fetched diff with 5 changed files
INFO: Cloning owner/repo at abc12345 to /tmp/pr_review_xyz
INFO: Successfully cloned to /tmp/pr_review_xyz
INFO: Detecting languages from 5 files
INFO: Detected languages: ['python', 'java']
INFO: Running static analysis for 2 languages
INFO: Context search: 3 symbols with changed code, 8 caller refs (L1), 3 summaries (L2)
INFO: Reviewer agent analyzing 5 files (primary language: python)
INFO: Reviewer agent generated 4 findings
INFO: Judge evaluating 4 findings (threshold: 0.6)
INFO: Judge verdict: 3/4 findings passed (pass rate: 75.0%)
INFO: Posting 3 findings to GitHub PR #123
INFO: Successfully posted 3 inline comments and 1 summary comment
INFO: Cleaning up cloned repository at /tmp/pr_review_xyz
INFO: Cleanup successful (after clearing read-only bits)
------------------------------------------------------------
Review completed successfully!

=== REVIEW SUMMARY ===
Reviewer findings: 4
Findings after Judge filter: 3
Posted to PR: 3
Context search: 3 symbols, 8 caller refs
Judge: 3/4 findings passed (threshold: 0.6)
Cleanup: temp clone directory removed

✅ Review posted to PR successfully
```

---

## Manual Testing (Outside Actions)

You can test the entry script locally:

```bash
# Set up environment
export GITHUB_REPOSITORY="owner/repo"
export GITHUB_TOKEN="ghp_your_token"
export ANTHROPIC_API_KEY="sk-ant-your_key"

# Create fake event file
cat > /tmp/event.json << EOF
{
  "pull_request": {
    "number": 123
  }
}
EOF

export GITHUB_EVENT_PATH=/tmp/event.json

# Run the script
python scripts/run_action.py
```

---

## Maintenance Notes

### Updating Checkstyle version:
Edit `.github/workflows/pr-review.yml` line 50:
```yaml
CHECKSTYLE_VERSION=10.12.5  # Change this
```

### Updating Python version:
Edit `.github/workflows/pr-review.yml` line 28:
```yaml
python-version: '3.11'  # Change this
```

### Adding more static analysis tools:
Add installation steps to workflow before "Run PR review" step.

---

## Security Notes

1. **Secrets exposure**: The workflow never prints `ANTHROPIC_API_KEY` to logs (only checks if it exists)
2. **Token permissions**: `GITHUB_TOKEN` is scoped to this repo only (cannot access other repos)
3. **Read-only by default**: Agent reads code but doesn't push changes
4. **PR isolation**: Each PR review runs in its own isolated Actions job

---

## Cost Estimates

**Per PR review** (rough estimates):
- **GitHub Actions minutes**: 3-5 minutes (free tier: 2000 min/month)
- **Anthropic API**: ~10k-50k tokens ($0.05-$0.25 depending on PR size)
- **Storage**: Negligible (temp clones cleaned up)

**Monthly cost** (10 PRs/week):
- GitHub Actions: Free (well under 2000 min)
- Anthropic API: ~$10-$20/month (depends on PR size and review depth)
