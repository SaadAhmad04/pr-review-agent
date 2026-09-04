"""Two-run grounding test: does context help the reviewer catch a stale caller?"""
from src.agents.reviewer import ReviewerAgent
from src.tools.diff_fetch import PRDiff, FileDiff
from src.context_search.ripgrep_strategy import RipgrepStrategy

REPO = "C:/Users/SaadAhmad/Spring/student-management"

# The REAL diff: getAllStudents renamed to findAllStudents in the SERVICE only
patch = """@@ -28,7 +29,7 @@ public class StudentService {
-    public List<Student> getAllStudents() {
+    public List<Student> findAllStudents() {
         return studentRepository.findAll();
     }"""

pr_diff = PRDiff(
    pr_number=1,
    repository="SaadAhmad04/student-management",
    files=[FileDiff(
        filename="src/main/java/com/example/student_management/service/StudentService.java",
        status="modified", additions=1, deletions=1, patch=patch, old_filename=None,
    )],
    base_sha="base", head_sha="head",
)

# Get REAL CodeReference objects — the verified stale-caller refs
strat = RipgrepStrategy()
caller_refs = strat.find_references('getAllStudents', REPO)  # 2 real refs in StudentController
print(f"[setup] Found {len(caller_refs)} real caller refs for getAllStudents")
for r in caller_refs:
    print(f"  {r.file_path.split(chr(92))[-1]}:{r.line_number} ({r.reference_type.value})")

# Build context in the EXACT shape _format_context_references expects
context_with = {
    "changed_symbols": ["findAllStudents", "getAllStudents"],
    "level_0": {
        "findAllStudents": {
            "definition_refs": [],
            "changed_code": "public List<Student> findAllStudents() {  // renamed from getAllStudents()",
        }
    },
    "level_1": {
        "getAllStudents": caller_refs,   # REAL CodeReference objects
    },
    "level_2_summary": {
        "getAllStudents": "getAllStudents is called in StudentController (1 file)",
    },
}

agent = ReviewerAgent()

def run(label, ctx):
    print(f"\n{'='*70}\n{label}\n{'='*70}")
    findings = agent.review(
        pr_diff=pr_diff, primary_language="java",
        detected_languages=[], static_findings=[], context_references=ctx,
    )
    if not findings:
        print("  (no findings)")
    for f in findings:
        # attribute names verified from Run A output: severity, title, file_path, line, description
        print(f"  [{f.severity}] {f.title} @ {f.file_path}:{f.line}")
        print(f"     {f.description}")

run("RUN A — WITHOUT context (diff only)", {})
run("RUN B — WITH context (real caller refs)", context_with)