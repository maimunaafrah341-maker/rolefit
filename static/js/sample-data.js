/* Sample application used by the "Try a sample" button.
 *
 * Chosen so a live demo shows every feature doing real work:
 *   - Strong, quotable matches (Python, Flask, SQL, pytest, Git) so the
 *     evidence cards fill with verified quotes.
 *   - Hard gaps the resume genuinely cannot cover (Go, Kubernetes, Kafka,
 *     distributed systems) so gap severity and the 7-day plan have material.
 *   - One tempting overstatement: the resume says "Docker (basic)" while the
 *     posting wants Kubernetes and containerised deployments. That is exactly
 *     the leap the Truth Guard should catch.
 *
 * Fictional person, fictional companies. Nothing here is real data.
 */

export const SAMPLE = {
  company: "Meridian Payments",
  role: "Backend Engineering Intern",

  resume: `PRIYA RAMANATHAN
priya.ramanathan@example.edu | github.com/priya-r-example | Bengaluru, India

EDUCATION
B.Tech, Computer Science - PES University, expected May 2027
Relevant coursework: Data Structures, Operating Systems, Database Systems, Computer Networks
CGPA 8.6 / 10

EXPERIENCE

Software Engineering Intern - Zentail Logistics (Jun 2026 - Aug 2026)
- Built an internal order-tracking dashboard in Flask and PostgreSQL used daily by 12 warehouse staff
- Wrote a nightly Python reconciliation job that compared shipment records across two systems, cutting manual review from roughly 3 hours to 20 minutes
- Added pytest coverage to a legacy billing module that had none, catching 4 rounding bugs before release
- Participated in weekly code review and addressed feedback from two senior engineers

Teaching Assistant, Data Structures - PES University (Jan 2026 - May 2026)
- Ran weekly lab sections for 40 students and graded assignments
- Wrote 15 auto-graded practice problems now reused across sections

PROJECTS

StudySync - study-group scheduler (Flask, SQLite, vanilla JavaScript)
- Deployed as a single container on Google Cloud Run; serves about 200 monthly active users from my campus
- Implemented Google OAuth sign-in with per-user data isolation
- Wrote integration tests covering the scheduling conflict logic

Campus Transit Map (Python, Pandas, Leaflet.js)
- Cleaned and analysed 6 months of public bus GPS data to visualise route delays
- Presented the findings to the campus transport office

SKILLS
Languages: Python, JavaScript, SQL, C
Frameworks and tools: Flask, pytest, Pandas, Git, PostgreSQL, SQLite, Linux, Docker (basic)`,

  jd: `Backend Engineering Intern - Summer 2027
Meridian Payments | Bengaluru (Hybrid)

ABOUT THE ROLE
Meridian handles payment reconciliation for mid-market retailers across South Asia. Our backend team owns the services that ingest, validate and settle several million transactions a day. As a Backend Engineering Intern you will ship production code alongside a team of six engineers.

WHAT YOU WILL DO
- Build and maintain REST APIs in Python that other internal services depend on
- Write and extend automated tests; we do not merge code without them
- Work with PostgreSQL: write queries, reason about indexes, and help diagnose slow ones
- Instrument services with structured logging and metrics so failures are debuggable
- Participate in code review and act on feedback from senior engineers

REQUIREMENTS
- Currently pursuing a degree in Computer Science or equivalent practical experience
- Strong Python fundamentals and comfort with at least one web framework
- Working knowledge of SQL and relational database design
- Familiarity with Git-based collaboration and pull request workflows
- Experience writing unit tests

PREFERRED
- Exposure to Go, as a growing share of our newer services are written in Go
- Experience with Kubernetes and containerised deployments at scale
- Familiarity with event streaming; we use Kafka for transaction ingestion
- Understanding of distributed systems concepts such as retries, backoff and idempotent message processing
- Prior experience building CI/CD pipelines
- Interest in payments, fintech or financial compliance

We review applications on a rolling basis and reply to every applicant.`,
};
