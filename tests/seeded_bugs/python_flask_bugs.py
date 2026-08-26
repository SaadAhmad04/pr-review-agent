"""
Seeded bugs for Python Flask/FastAPI (blog-api).

Repository: Simple blog API with posts, comments, and users.

Categories same as Java: PERFORMANCE, SECURITY, BUG, BREAKING_CHANGE, DATA_INTEGRITY
"""

from dataclasses import dataclass
from typing import List


@dataclass
class SeededBug:
    """Metadata about a seeded bug."""
    id: str
    category: str
    severity: str
    title: str
    description: str
    file_path: str
    line_start: int
    line_end: int
    code_before: str
    code_after: str
    detection_hints: List[str]
    static_analysis_detects: bool


# Repository: https://github.com/example/blog-api
# A Flask REST API for blog posts

PYTHON_SEEDED_BUGS = [
    # Bug 1: Race Condition in Cache
    SeededBug(
        id="python-001-race",
        category="DATA_INTEGRITY",
        severity="major",
        title="Race condition in cache update",
        description=(
            "The increment_view_count function reads, modifies, and writes the view count "
            "without locking. Two concurrent requests can read the same value, both increment it, "
            "and write back, losing one increment. Should use atomic operations or locking."
        ),
        file_path="app/services/post_service.py",
        line_start=55,
        line_end=62,
        code_before="""
def increment_view_count(post_id: int) -> int:
    key = f"post:{post_id}:views"

    # Atomic increment
    return redis_client.incr(key)
""",
        code_after="""
def increment_view_count(post_id: int) -> int:
    # SEEDED BUG: Changed from atomic incr() to read-modify-write
    key = f"post:{post_id}:views"

    # Race condition: two threads can read same value
    current = redis_client.get(key) or 0
    new_value = int(current) + 1
    redis_client.set(key, new_value)

    return new_value
""",
        detection_hints=[
            "Read-modify-write pattern without locking",
            "Non-atomic operation on shared state",
            "Cache read followed by write without transaction",
            "Risk of lost updates in concurrent requests",
        ],
        static_analysis_detects=False  # Requires concurrency analysis
    ),

    # Bug 2: SQL Injection (Python version)
    SeededBug(
        id="python-002-sqli",
        category="SECURITY",
        severity="critical",
        title="SQL injection in search_posts",
        description=(
            "The search function uses string formatting to build SQL query, allowing injection. "
            "Attacker could inject: '; DROP TABLE posts; -- "
            "Should use parameterized queries with placeholders."
        ),
        file_path="app/models/post.py",
        line_start=78,
        line_end=85,
        code_before="""
@staticmethod
def search(query: str) -> List['Post']:
    # Safe: using parameterized query
    sql = "SELECT * FROM posts WHERE title LIKE ? OR content LIKE ?"
    params = (f"%{query}%", f"%{query}%")
    return db.session.execute(sql, params).fetchall()
""",
        code_after="""
@staticmethod
def search(query: str) -> List['Post']:
    # SEEDED BUG: String formatting allows SQL injection
    sql = f"SELECT * FROM posts WHERE title LIKE '%{query}%' OR content LIKE '%{query}%'"
    # Direct string interpolation - DANGEROUS!
    return db.session.execute(sql).fetchall()
""",
        detection_hints=[
            "f-string or % formatting in SQL query",
            "User input directly interpolated into query",
            "No parameterized query or placeholders",
            "execute() without separate params argument",
        ],
        static_analysis_detects=True  # Bandit can detect this
    ),

    # Bug 3: Resource Leak
    SeededBug(
        id="python-003-leak",
        category="BUG",
        severity="major",
        title="File handle not closed in export_posts",
        description=(
            "The export_posts function opens a file but doesn't close it if an exception occurs. "
            "Over time, this leaks file handles and can exhaust system resources. "
            "Should use 'with' statement for automatic cleanup."
        ),
        file_path="app/services/export_service.py",
        line_start=25,
        line_end=35,
        code_before="""
def export_posts_to_csv(filename: str) -> str:
    with open(filename, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['ID', 'Title', 'Content', 'Author'])

        posts = Post.query.all()
        for post in posts:
            writer.writerow([post.id, post.title, post.content, post.author.username])

    return filename
""",
        code_after="""
def export_posts_to_csv(filename: str) -> str:
    # SEEDED BUG: Not using 'with' statement
    csvfile = open(filename, 'w', newline='')
    writer = csv.writer(csvfile)
    writer.writerow(['ID', 'Title', 'Content', 'Author'])

    posts = Post.query.all()
    for post in posts:
        writer.writerow([post.id, post.title, post.content, post.author.username])

    # File never closed if exception occurs above!
    csvfile.close()
    return filename
""",
        detection_hints=[
            "open() without 'with' statement",
            "Manual .close() call instead of context manager",
            "File handle not closed on exception path",
            "Resource leak risk",
        ],
        static_analysis_detects=True  # Pylint can catch this
    ),

    # Bug 4: Incorrect Exception Handling
    SeededBug(
        id="python-004-exception",
        category="BUG",
        severity="major",
        title="Bare except hides errors in create_post",
        description=(
            "The create_post function uses 'except:' which catches ALL exceptions, "
            "including KeyboardInterrupt and SystemExit. This hides real errors and makes "
            "debugging impossible. Should catch specific exceptions or at minimum use 'except Exception:'."
        ),
        file_path="app/routes/posts.py",
        line_start=45,
        line_end=58,
        code_before="""
@posts_bp.route('/', methods=['POST'])
@login_required
def create_post():
    try:
        data = request.get_json()
        post = Post(
            title=data['title'],
            content=data['content'],
            author_id=current_user.id
        )
        db.session.add(post)
        db.session.commit()
        return jsonify(post.to_dict()), 201
    except KeyError as e:
        return jsonify({'error': f'Missing field: {e}'}), 400
    except SQLAlchemyError as e:
        db.session.rollback()
        return jsonify({'error': 'Database error'}), 500
""",
        code_after="""
@posts_bp.route('/', methods=['POST'])
@login_required
def create_post():
    try:
        data = request.get_json()
        post = Post(
            title=data['title'],
            content=data['content'],
            author_id=current_user.id
        )
        db.session.add(post)
        db.session.commit()
        return jsonify(post.to_dict()), 201
    except:  # SEEDED BUG: Bare except catches EVERYTHING
        # This hides KeyError, SQLAlchemyError, and even SystemExit!
        return jsonify({'error': 'Something went wrong'}), 500
""",
        detection_hints=[
            "Bare 'except:' clause",
            "Catches all exceptions including system exits",
            "No specific exception type",
            "Makes debugging impossible",
        ],
        static_analysis_detects=True  # Pylint warns about this
    ),

    # Bug 5: Type Error After Refactor
    SeededBug(
        id="python-005-type",
        category="BREAKING_CHANGE",
        severity="major",
        title="Type mismatch after refactoring get_post_stats",
        description=(
            "The get_post_stats function was refactored to return a dict instead of a tuple, "
            "but the caller still unpacks it as a tuple. This will cause a ValueError at runtime. "
            "Should update caller to use dict access or update docstring/type hints."
        ),
        file_path="app/services/stats_service.py",
        line_start=68,
        line_end=75,
        code_before="""
def get_post_stats(post_id: int) -> tuple[int, int, int]:
    '''Returns (view_count, like_count, comment_count)'''
    post = Post.query.get_or_404(post_id)
    return (
        post.view_count,
        post.like_count,
        post.comments.count()
    )
""",
        code_after="""
def get_post_stats(post_id: int) -> dict:
    # SEEDED BUG: Changed return type from tuple to dict
    # But caller still does: views, likes, comments = get_post_stats(id)
    '''Returns stats dictionary'''
    post = Post.query.get_or_404(post_id)
    return {
        'views': post.view_count,
        'likes': post.like_count,
        'comments': post.comments.count()
    }
""",
        detection_hints=[
            "Return type changed from tuple to dict",
            "Caller uses tuple unpacking on dict",
            "Would cause ValueError: too many values to unpack",
            "Docstring and type hint don't match usage",
        ],
        static_analysis_detects=False  # Requires type checking (mypy)
    ),
]


def get_python_bugs() -> List[SeededBug]:
    """Return all Python seeded bugs."""
    return PYTHON_SEEDED_BUGS


def get_bug_by_id(bug_id: str) -> SeededBug:
    """Get a specific bug by ID."""
    for bug in PYTHON_SEEDED_BUGS:
        if bug.id == bug_id:
            return bug
    raise ValueError(f"Bug not found: {bug_id}")
