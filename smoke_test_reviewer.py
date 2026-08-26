"""
Smoke test for the Reviewer LLM call.
ONE real API call. Feeds a tiny diff with an OBVIOUS bug, prints findings.
Purpose: confirm the real Claude call works end-to-end before spending
credits on the full 10-bug eval.

Run (with venv active):  python smoke_test_reviewer.py
"""

import logging
logging.basicConfig(level=logging.INFO)

from src.tools.diff_fetch import PRDiff, FileDiff  # adjust names if yours differ
from src.agents.reviewer import ReviewerAgent

# A tiny, fake diff with a GLARING SQL-injection bug on a known line.
# Small on purpose = one cheap call.
patch = """@@ -1,3 +1,5 @@
 def get_user(user_id):
-    return None
+    query = "SELECT * FROM users WHERE id = " + user_id
+    return db.execute(query)
"""

pr_diff = PRDiff(
    repository="test/repo",
    pr_number=1,
    base_sha="aaaaaaa",
    head_sha="bbbbbbb",
    files=[
        FileDiff(
            filename="app/users.py",
            status="modified",
            additions=2,
            deletions=1,
            patch=patch,
        )
    ],
)

agent = ReviewerAgent()

findings = agent.review(
    pr_diff=pr_diff,
    primary_language="python",
    detected_languages=[],          # empty is fine for smoke test
    static_findings=[],             # empty is fine
    context_references={},          # empty levels dict is fine
)

print("\n" + "=" * 60)
print(f"REVIEWER RETURNED {len(findings)} FINDING(S)")
print("=" * 60)
for i, f in enumerate(findings, 1):
    print(f"\n{i}. [{f.severity}] {f.title}")
    print(f"   {f.file_path}:{f.line}  ({f.category}, conf={f.confidence})")
    print(f"   {f.description[:200]}")
    if f.suggestion:
        print(f"   FIX: {f.suggestion[:200]}")