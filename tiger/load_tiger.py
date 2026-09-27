"""Load the six hackUMBC CSVs into Tiger Data (TimescaleDB on Tiger Cloud).

Usage:  python tiger/load_tiger.py            # first load into an empty database
        python tiger/load_tiger.py --reset    # drop this project's tables and views, then reload

Needs TIGER_URL (the "Service URL" from the Tiger Cloud credentials file) in .env or the environment.

Tables keep the CSV names and columns, so the notebook can read them the same way it read the CSVs.
Profiles (students_current, alumni, course_catalog) stay regular Postgres tables; activity streams
(student_experience, transcripts, employment_history) become hypertables stored in the columnstore,
with continuous aggregates on top for instant dashboard queries.
"""
import csv
import os
import re
import sys
import time

import psycopg
from dotenv import load_dotenv

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
NULLS = {"", "Not Applicable"}
TERM_START = {"Spring": "01-15", "Summer": "06-01", "Fall": "08-25"}   # sortable date for "Fall 2023"-style terms

PROFILES = {"students_current": "campus_id", "alumni": "campus_id", "course_catalog": "course_id"}
# table -> (primary key, time column, columnstore segmentby, columnstore orderby)
STREAMS = {
    "student_experience": ("record_id, term_start", "term_start", "experience_type", "term_start, organization"),
    "transcripts": ("campus_id, course_id, term_start", "term_start", "course_id", "term_start, campus_id"),
    "employment_history": ("job_id, start_date", "start_date", "job_family", "start_date, employer"),
}
CAGGS = ["experience_yearly", "grades_by_term", "salary_by_year"]


def read(table):
    with open(os.path.join(DATA, f"{table}.csv"), newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    header, rows = rows[0], [[None if v in NULLS else v for v in r] for r in rows[1:]]
    if "term" in header:
        i = header.index("term")
        header = header + ["term_start"]
        rows = [r + [f"{r[i][-4:]}-{TERM_START[r[i].split()[0]]}"] for r in rows]
    return header, rows


def pg_type(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return "text"
    if all(v in ("TRUE", "FALSE") for v in vals):
        return "boolean"
    if all(re.fullmatch(r"-?(0|[1-9]\d*)", v) for v in vals):   # no leading zeros, so nothing is lost
        return "integer" if max(abs(int(v)) for v in vals) < 2**31 else "bigint"
    if all(re.fullmatch(r"-?\d+\.\d+|-?\d+", v) for v in vals):
        return "double precision"
    if all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) for v in vals):
        return "date"
    return "text"


def create_and_load(conn, table, pk):
    header, rows = read(table)
    cols = ", ".join(f"{c} {pg_type([r[i] for r in rows])}" for i, c in enumerate(header))
    conn.execute(f"CREATE TABLE {table} ({cols}, PRIMARY KEY ({pk}))")
    if table in STREAMS:
        _, time_col, segment, order = STREAMS[table]
        conn.execute(f"SELECT create_hypertable('{table}', by_range('{time_col}', INTERVAL '5 years'))")
        conn.execute(f"""ALTER TABLE {table} SET (timescaledb.enable_columnstore = true,
                         timescaledb.segmentby = '{segment}', timescaledb.orderby = '{order}')""")
    with conn.cursor().copy(f"COPY {table} ({', '.join(header)}) FROM STDIN") as cp:
        for r in rows:
            cp.write_row(r)
    print(f"  {table:20s} {len(rows):>7} rows")


INDEXES = """
ALTER TABLE transcripts ADD FOREIGN KEY (course_id) REFERENCES course_catalog (course_id);
CREATE INDEX ON student_experience (campus_id);
CREATE INDEX ON student_experience (organization, term_start);
CREATE INDEX ON transcripts (campus_id);
CREATE INDEX ON employment_history (campus_id);
CREATE INDEX ON employment_history (employer, start_date);
"""

# Continuous aggregates: pre-computed rollups the dashboard reads instead of scanning raw rows.
# hyperloglog / percentile_agg (timescaledb_toolkit) store mergeable sketches, so buckets can be
# rolled up into any period later, e.g. distinct people over 2018-2020 = rollup of three yearly sketches.
CAGG_SQL = """
CREATE MATERIALIZED VIEW experience_yearly WITH (timescaledb.continuous) AS
SELECT time_bucket('1 year', term_start) AS year, experience_type, organization,
       count(*) AS records,
       hyperloglog(4096, campus_id) AS people_hll
FROM student_experience GROUP BY 1, 2, 3 WITH NO DATA;

CREATE MATERIALIZED VIEW grades_by_term WITH (timescaledb.continuous) AS
SELECT time_bucket('1 month', term_start) AS term, subject,
       count(*) AS attempts,
       avg(grade_points) AS avg_grade_points,
       sum(CASE WHEN grade = 'W' THEN 1 ELSE 0 END) AS withdrawals,
       sum(CASE WHEN grade IN ('D', 'F') THEN 1 ELSE 0 END) AS d_or_f
FROM transcripts GROUP BY 1, 2 WITH NO DATA;

CREATE MATERIALIZED VIEW salary_by_year WITH (timescaledb.continuous) AS
SELECT time_bucket('1 year', start_date) AS year, job_family, seniority_level,
       count(*) AS jobs,
       avg(annual_salary_usd) AS avg_salary,
       avg(annual_salary_usd * 100.0 / cost_of_living_index) AS avg_salary_col_adjusted,
       percentile_agg(annual_salary_usd) AS salary_pct
FROM employment_history GROUP BY 1, 2, 3 WITH NO DATA;
"""


def main():
    load_dotenv()
    url = os.environ["TIGER_URL"]
    with psycopg.connect(url, autocommit=True) as conn:
        existing = [t for (t,) in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = ANY(%s)",
            [list(PROFILES) + list(STREAMS)])]
        if existing and "--reset" not in sys.argv:
            sys.exit(f"Tables already exist: {existing}. Re-run with --reset to drop and reload them.")
        for v in CAGGS:
            conn.execute(f"DROP MATERIALIZED VIEW IF EXISTS {v} CASCADE")
        for t in list(STREAMS) + list(PROFILES):   # streams first: transcripts references course_catalog
            conn.execute(f"DROP TABLE IF EXISTS {t} CASCADE")

        t0 = time.time()
        print("Loading:")
        for table, pk in PROFILES.items():
            create_and_load(conn, table, pk)
        for table, (pk, *_) in STREAMS.items():
            create_and_load(conn, table, pk)
        conn.execute(INDEXES)

        print("Converting chunks to the columnstore...")
        for table in STREAMS:
            for (chunk,) in conn.execute(f"SELECT show_chunks('{table}')").fetchall():
                conn.execute("CALL convert_to_columnstore(%s)", [chunk])

        print("Building continuous aggregates...")
        conn.execute(CAGG_SQL)
        for v in CAGGS:
            conn.execute(f"CALL refresh_continuous_aggregate('{v}', NULL, NULL)")
        conn.execute("ANALYZE")

        for table in STREAMS:
            before, after = conn.execute(
                "SELECT sum(before_compression_total_bytes)::bigint, sum(after_compression_total_bytes)::bigint "
                "FROM chunk_columnstore_stats(%s)", [table]).fetchone()
            print(f"  {table:20s} {before / 1e6:6.2f} MB -> {after / 1e6:5.2f} MB  ({1 - after / before:.0%} smaller)")
        print(f"Done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
