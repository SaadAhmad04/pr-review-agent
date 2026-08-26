"""
Seeded bugs for Java Spring Boot (student-management-system).

Each bug represents a realistic issue that:
1. Might pass compilation
2. Could slip through code review
3. Represents a real-world pattern we want the agent to catch

Categories:
- PERFORMANCE: N+1 queries, inefficient algorithms
- SECURITY: SQL injection, XSS, unvalidated input
- BUG: NPE, off-by-one, logic errors
- BREAKING_CHANGE: API changes that break callers
- DATA_INTEGRITY: Missing @Transactional, race conditions
"""

from dataclasses import dataclass
from typing import List


@dataclass
class SeededBug:
    """Metadata about a seeded bug."""
    id: str  # Unique identifier
    category: str  # PERFORMANCE, SECURITY, BUG, BREAKING_CHANGE, DATA_INTEGRITY
    severity: str  # critical, major, minor
    title: str  # Short description
    description: str  # What's wrong and why it's a problem
    file_path: str  # Where it's located
    line_start: int  # Start line of the bug
    line_end: int  # End line (inclusive)
    code_before: str  # Original correct code
    code_after: str  # Buggy code to introduce
    detection_hints: List[str]  # What should tip off the agent
    static_analysis_detects: bool  # Would static analysis catch this?


# Repository: https://github.com/example/student-management-system
# A typical Spring Boot CRUD app with Student/Course entities

JAVA_SEEDED_BUGS = [
    # Bug 1: N+1 Query Problem
    SeededBug(
        id="java-001-n+1",
        category="PERFORMANCE",
        severity="major",
        title="N+1 Query in getAllStudentsWithCourses",
        description=(
            "The method fetches all students, then for each student fetches their courses "
            "in a separate query. With 100 students, this creates 101 queries instead of 1-2. "
            "Should use JOIN FETCH or @EntityGraph."
        ),
        file_path="src/main/java/com/example/student/service/StudentService.java",
        line_start=45,
        line_end=52,
        code_before="""
    @Transactional(readOnly = true)
    public List<StudentDTO> getAllStudentsWithCourses() {
        return studentRepository.findAllWithCourses().stream()
            .map(this::convertToDTO)
            .collect(Collectors.toList());
    }
""",
        code_after="""
    @Transactional(readOnly = true)
    public List<StudentDTO> getAllStudentsWithCourses() {
        // SEEDED BUG: Changed from findAllWithCourses() to findAll()
        // This removes the JOIN FETCH, causing N+1 queries
        return studentRepository.findAll().stream()
            .map(student -> {
                StudentDTO dto = convertToDTO(student);
                // Lazy load courses - triggers one query per student!
                dto.setCourses(student.getCourses());
                return dto;
            })
            .collect(Collectors.toList());
    }
""",
        detection_hints=[
            "Pattern: findAll() followed by lazy-loaded relationships",
            "Inside @Transactional, accessing lazy collections",
            "Missing JOIN FETCH or @EntityGraph",
            "Loop over results accessing related entities",
        ],
        static_analysis_detects=False  # Requires runtime analysis or JPA expertise
    ),

    # Bug 2: Missing Null Check
    SeededBug(
        id="java-002-npe",
        category="BUG",
        severity="major",
        title="NullPointerException in updateStudent",
        description=(
            "The updateStudent method doesn't check if the student exists before updating. "
            "If studentRepository.findById() returns Optional.empty(), calling .get() throws NPE. "
            "Should use .orElseThrow() or .isPresent() check."
        ),
        file_path="src/main/java/com/example/student/service/StudentService.java",
        line_start=65,
        line_end=72,
        code_before="""
    public StudentDTO updateStudent(Long id, StudentDTO studentDTO) {
        Student student = studentRepository.findById(id)
            .orElseThrow(() -> new ResourceNotFoundException("Student not found: " + id));

        student.setName(studentDTO.getName());
        student.setEmail(studentDTO.getEmail());

        return convertToDTO(studentRepository.save(student));
    }
""",
        code_after="""
    public StudentDTO updateStudent(Long id, StudentDTO studentDTO) {
        // SEEDED BUG: Removed null check, using .get() directly
        Student student = studentRepository.findById(id).get();

        student.setName(studentDTO.getName());
        student.setEmail(studentDTO.getEmail());

        return convertToDTO(studentRepository.save(student));
    }
""",
        detection_hints=[
            "Optional.get() without isPresent() check",
            "No orElseThrow() or orElse()",
            "Repository findById returns Optional",
            "Can throw NoSuchElementException at runtime",
        ],
        static_analysis_detects=True  # SpotBugs, Checkstyle can catch this
    ),

    # Bug 3: SQL Injection
    SeededBug(
        id="java-003-sqli",
        category="SECURITY",
        severity="critical",
        title="SQL Injection in searchStudents",
        description=(
            "The search method concatenates user input directly into SQL query. "
            "An attacker could inject SQL like: '; DROP TABLE students; -- "
            "Should use parameterized queries or Spring Data JPA query methods."
        ),
        file_path="src/main/java/com/example/student/repository/StudentRepository.java",
        line_start=28,
        line_end=32,
        code_before="""
    @Query("SELECT s FROM Student s WHERE s.name LIKE :searchTerm OR s.email LIKE :searchTerm")
    List<Student> searchStudents(@Param("searchTerm") String searchTerm);
""",
        code_after="""
    // SEEDED BUG: Changed to native query with string concatenation
    @Query(value = "SELECT * FROM students WHERE name LIKE '%" +
                   "' OR email LIKE '%", nativeQuery = true)
    default List<Student> searchStudents(String searchTerm) {
        // Simulating manual query construction (actual implementation would use EntityManager)
        String query = "SELECT * FROM students WHERE name LIKE '%" + searchTerm +
                      "%' OR email LIKE '%" + searchTerm + "%'";
        // This concatenates user input directly into SQL!
        return findByNativeQuery(query);
    }
""",
        detection_hints=[
            "String concatenation with user input in SQL",
            "No PreparedStatement or named parameters",
            "Direct concatenation using + operator",
            "nativeQuery = true with dynamic string building",
        ],
        static_analysis_detects=True  # SpotBugs, FindSecBugs can detect
    ),

    # Bug 4: Breaking API Change
    SeededBug(
        id="java-004-breaking",
        category="BREAKING_CHANGE",
        severity="major",
        title="Method signature changed but caller not updated",
        description=(
            "The enrollStudentInCourse method signature was changed to add a 'semester' parameter, "
            "but the REST controller still calls it with the old signature. "
            "This will cause a compilation error, but represents a common refactoring mistake."
        ),
        file_path="src/main/java/com/example/student/service/EnrollmentService.java",
        line_start=40,
        line_end=48,
        code_before="""
    public EnrollmentDTO enrollStudentInCourse(Long studentId, Long courseId) {
        Student student = studentRepository.findById(studentId)
            .orElseThrow(() -> new ResourceNotFoundException("Student not found"));
        Course course = courseRepository.findById(courseId)
            .orElseThrow(() -> new ResourceNotFoundException("Course not found"));

        Enrollment enrollment = new Enrollment(student, course);
        return convertToDTO(enrollmentRepository.save(enrollment));
    }
""",
        code_after="""
    // SEEDED BUG: Added 'semester' parameter but didn't update caller
    public EnrollmentDTO enrollStudentInCourse(Long studentId, Long courseId, String semester) {
        Student student = studentRepository.findById(studentId)
            .orElseThrow(() -> new ResourceNotFoundException("Student not found"));
        Course course = courseRepository.findById(courseId)
            .orElseThrow(() -> new ResourceNotFoundException("Course not found"));

        Enrollment enrollment = new Enrollment(student, course, semester);
        return convertToDTO(enrollmentRepository.save(enrollment));
    }
""",
        detection_hints=[
            "Method signature changed (added parameter)",
            "Caller in EnrollmentController still uses old signature",
            "Would cause compilation error",
            "grep for calls to enrollStudentInCourse shows mismatch",
        ],
        static_analysis_detects=True  # Compiler would catch this
    ),

    # Bug 5: Missing @Transactional
    SeededBug(
        id="java-005-transaction",
        category="DATA_INTEGRITY",
        severity="major",
        title="Missing @Transactional on multi-step operation",
        description=(
            "The deleteStudentAndEnrollments method deletes a student and their enrollments "
            "in two separate calls. Without @Transactional, if the second delete fails, "
            "the student is deleted but enrollments remain (orphaned data). "
            "Should be wrapped in a transaction for atomicity."
        ),
        file_path="src/main/java/com/example/student/service/StudentService.java",
        line_start=85,
        line_end=93,
        code_before="""
    @Transactional
    public void deleteStudentAndEnrollments(Long studentId) {
        // Delete enrollments first (foreign key constraint)
        enrollmentRepository.deleteByStudentId(studentId);

        // Then delete student
        studentRepository.deleteById(studentId);
    }
""",
        code_after="""
    // SEEDED BUG: Removed @Transactional annotation
    public void deleteStudentAndEnrollments(Long studentId) {
        // Delete enrollments first (foreign key constraint)
        enrollmentRepository.deleteByStudentId(studentId);

        // Then delete student
        // If this fails, enrollments are deleted but student remains!
        studentRepository.deleteById(studentId);
    }
""",
        detection_hints=[
            "Multiple database writes without @Transactional",
            "Delete operations that should be atomic",
            "No transaction boundary around multi-step operation",
            "Risk of partial completion if second operation fails",
        ],
        static_analysis_detects=False  # Requires domain knowledge
    ),
]


def get_java_bugs() -> List[SeededBug]:
    """Return all Java seeded bugs."""
    return JAVA_SEEDED_BUGS


def get_bug_by_id(bug_id: str) -> SeededBug:
    """Get a specific bug by ID."""
    for bug in JAVA_SEEDED_BUGS:
        if bug.id == bug_id:
            return bug
    raise ValueError(f"Bug not found: {bug_id}")
