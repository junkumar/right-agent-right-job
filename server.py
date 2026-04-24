from __future__ import annotations

import json
import sqlite3
from functools import lru_cache
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from agents import DATABASE_CONTEXT, FIRM_CONTEXT, SINGLE_AGENT_CONTEXT, DatabaseNativeAgent, ExaBankFirmAgent
from agents.llm import LLMClient


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DB_PATH = ROOT / "demo.db"
HOST = "127.0.0.1"
PORT = 8000

SEED_SQL = """
DROP TABLE IF EXISTS raw_orders;
DROP TABLE IF EXISTS seed_groups;

CREATE TABLE raw_orders (
    id INTEGER PRIMARY KEY,
    order_date TEXT NOT NULL,
    fiscal_week_id TEXT NOT NULL,
    week_rank INTEGER NOT NULL,
    product_family TEXT NOT NULL,
    units INTEGER NOT NULL,
    recognized_revenue REAL NOT NULL
);

CREATE TABLE seed_groups (
    order_date TEXT NOT NULL,
    fiscal_week_id TEXT NOT NULL,
    week_rank INTEGER NOT NULL,
    product_family TEXT NOT NULL,
    units INTEGER NOT NULL,
    recognized_revenue REAL NOT NULL,
    detail_rows INTEGER NOT NULL
);

INSERT INTO seed_groups (
    order_date, fiscal_week_id, week_rank, product_family, units, recognized_revenue, detail_rows
) VALUES
    ('2026-04-14', 'FY26-W16', 2, 'Treasury Services', 1200, 1.18, 300),
    ('2026-04-15', 'FY26-W16', 2, 'Markets', 800, 0.79, 300),
    ('2026-04-16', 'FY26-W16', 2, 'Consumer Cards', 1800, 0.62, 300),
    ('2026-04-17', 'FY26-W16', 2, 'Commercial Lending', 600, 0.58, 300),
    ('2026-04-21', 'FY26-W17', 1, 'Treasury Services', 1540, 1.51, 300),
    ('2026-04-22', 'FY26-W17', 1, 'Markets', 780, 0.76, 300),
    ('2026-04-23', 'FY26-W17', 1, 'Consumer Cards', 2500, 0.81, 300),
    ('2026-04-24', 'FY26-W17', 1, 'Commercial Lending', 620, 0.60, 300);

WITH RECURSIVE prior_weeks(offset_days, week_rank_value, week_number) AS (
    SELECT 14, 3, 15
    UNION ALL
    SELECT offset_days + 7, week_rank_value + 1, week_number - 1
    FROM prior_weeks
    WHERE week_rank_value < 26
)
INSERT INTO seed_groups (
    order_date, fiscal_week_id, week_rank, product_family, units, recognized_revenue, detail_rows
)
SELECT date('2026-04-24', printf('-%d days', offset_days)),
       printf('FY26-W%02d', week_number),
       week_rank_value,
       dims.product_family,
       dims.units,
       dims.recognized_revenue,
       300
FROM prior_weeks
JOIN (
    SELECT 'Treasury Services' AS product_family, 1080 AS units, 1.06 AS recognized_revenue
    UNION ALL
    SELECT 'Markets', 760, 0.75
    UNION ALL
    SELECT 'Consumer Cards', 1600, 0.60
    UNION ALL
    SELECT 'Commercial Lending', 560, 0.56
) AS dims;

WITH RECURSIVE seq(n) AS (
    SELECT 1
    UNION ALL
    SELECT n + 1 FROM seq WHERE n < 300
)
INSERT INTO raw_orders (
    order_date, fiscal_week_id, week_rank, product_family, units, recognized_revenue
)
SELECT
    g.order_date,
    g.fiscal_week_id,
    g.week_rank,
    g.product_family,
    CASE
        WHEN seq.n <= (g.units % g.detail_rows) THEN (g.units / g.detail_rows) + 1
        ELSE (g.units / g.detail_rows)
    END,
    g.recognized_revenue / g.detail_rows
FROM seed_groups g
JOIN seq ON seq.n <= g.detail_rows;

CREATE INDEX idx_raw_orders_week_rank_product
    ON raw_orders (week_rank, product_family);
"""

TOTAL_DELTA_SQL = """
SELECT ROUND(
    SUM(CASE WHEN week_rank = 1 THEN recognized_revenue ELSE 0 END) -
    SUM(CASE WHEN week_rank = 2 THEN recognized_revenue ELSE 0 END),
    2
) AS total_delta
FROM raw_orders;
""".strip()

DATASET_SUMMARY_SQL = """
SELECT
    ROUND(SUM(CASE WHEN week_rank = 1 THEN recognized_revenue ELSE 0 END), 2) AS current_certified_revenue,
    ROUND(SUM(CASE WHEN week_rank = 2 THEN recognized_revenue ELSE 0 END), 2) AS prior_certified_revenue,
    COUNT(*) AS total_rows,
    SUM(CASE WHEN fiscal_week_id IN ('FY26-W17', 'FY26-W16') THEN 1 ELSE 0 END) AS relevant_rows
FROM raw_orders;
""".strip()


def estimate_tokens(text: str) -> int:
    return max(1, round(len(text) / 4))


def init_db() -> None:
    connection = sqlite3.connect(DB_PATH)
    try:
        connection.executescript(SEED_SQL)
        connection.commit()
    finally:
        connection.close()


def query_db(sql: str) -> list[dict[str, Any]]:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(sql).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def dataset_summary() -> dict[str, Any]:
    row = query_db(DATASET_SUMMARY_SQL)[0]
    return {
        **row,
        "certified_delta": round(
            row["current_certified_revenue"] - row["prior_certified_revenue"], 2
        ),
        "scan_reduction": round(row["total_rows"] / row["relevant_rows"]),
    }


def query_plan(sql: str) -> list[str]:
    connection = sqlite3.connect(DB_PATH)
    try:
        rows = connection.execute(f"EXPLAIN QUERY PLAN {sql}").fetchall()
        return [row[3] for row in rows]
    finally:
        connection.close()


@lru_cache(maxsize=16)
def run_demo(question: str) -> dict[str, Any]:
    llm = LLMClient()
    firm_agent = ExaBankFirmAgent(llm=llm)
    native_agent = DatabaseNativeAgent(llm=llm)
    single_sql = firm_agent.build_portable_sql(question)
    handoff = firm_agent.build_request(question)
    native_sql = native_agent.build_engine_sql(handoff)
    summary = dataset_summary()
    single_row = query_db(single_sql)[0]
    two_agent_row = query_db(native_sql)[0]
    total_delta = query_db(TOTAL_DELTA_SQL)[0]["total_delta"]
    certified_share = round((two_agent_row["revenue_delta"] / total_delta) * 100, 1)

    return {
        "question": question,
        "scenario_note": (
            "Both paths still use the database optimizer. "
            f"LLM provider: {llm.config.provider if llm.enabled else 'fallback'}. "
            "The database-native agent wins by generating a better query before optimization starts."
        ),
        "dataset_summary": {
            **summary,
            "single_context_tokens": estimate_tokens(SINGLE_AGENT_CONTEXT),
            "split_peak_tokens": max(
                estimate_tokens(FIRM_CONTEXT),
                estimate_tokens(DATABASE_CONTEXT) + estimate_tokens(json.dumps(handoff.__dict__)),
            ),
            "token_reduction": round(
                estimate_tokens(SINGLE_AGENT_CONTEXT)
                / max(
                    estimate_tokens(FIRM_CONTEXT),
                    estimate_tokens(DATABASE_CONTEXT) + estimate_tokens(json.dumps(handoff.__dict__)),
                ),
                1,
            ),
        },
        "single_agent": {
            "label": "ExaBank firm agent + DB optimizer",
            "context_tokens": estimate_tokens(SINGLE_AGENT_CONTEXT),
            "source_table": "raw_orders",
            "query_shape": "Portable date-bucket scan",
            "context_summary": (
                "One agent has to infer the business meaning and the SQL strategy at the same time."
            ),
            "assumptions": [
                "Uses ExaBank's certified recognized revenue metric.",
                "Computes week buckets from order_date inside the query.",
                "Pushes the full raw table through aggregation before filtering the result.",
                "Still relies on the optimizer, but the query shape forces a scan.",
            ],
            "rows_scanned": summary["total_rows"],
            "plan_highlight": "Full scan on raw_orders",
            "plan": query_plan(single_sql),
            "sql": single_sql,
            "top_driver": single_row,
            "answer": (
                f"It still finds {single_row['product_family']}, but it gets there by scanning "
                f"all {summary['total_rows']} rows because the query computes week buckets on the fly."
            ),
        },
        "two_agent": {
            "label": "ExaBank firm agent + database-native agent + DB optimizer",
            "firm_agent_tokens": estimate_tokens(FIRM_CONTEXT),
            "database_agent_tokens": estimate_tokens(DATABASE_CONTEXT),
            "handoff_tokens": estimate_tokens(json.dumps(handoff.__dict__)),
            "source_table": "raw_orders",
            "query_shape": "Indexed fiscal-week seek",
            "rows_scanned": summary["relevant_rows"],
            "plan_highlight": "Index seek on week_rank",
            "plan": query_plan(native_sql),
            "peak_working_tokens": max(
                estimate_tokens(FIRM_CONTEXT),
                estimate_tokens(DATABASE_CONTEXT) + estimate_tokens(json.dumps(handoff.__dict__)),
            ),
            "total_tokens": (
                estimate_tokens(FIRM_CONTEXT)
                + estimate_tokens(DATABASE_CONTEXT)
                + estimate_tokens(json.dumps(handoff.__dict__))
            ),
            "sql": native_sql,
            "top_driver": two_agent_row,
            "answer": (
                f"It gets the same driver by seeking to {summary['relevant_rows']} relevant rows "
                f"instead of scanning all {summary['total_rows']}. "
                f"{two_agent_row['product_family']} explains ${two_agent_row['revenue_delta']:.2f}M "
                f"of the ${total_delta:.2f}M increase."
            ),
            "advantage": (
                "The optimizer exists in both paths. The database-native agent helps by preserving the physical fiscal-week key in the query."
            ),
        },
    }


class DemoHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/demo":
            self._write_json(run_demo(DEFAULT_QUESTION))
            return

        target = "index.html" if self.path == "/" else self.path.lstrip("/")
        file_path = STATIC_DIR / target
        if not file_path.exists() or not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
        }.get(file_path.suffix, "application/octet-stream")

        body = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/demo":
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length) if length else b"{}"
        payload = json.loads(raw_body.decode("utf-8") or "{}")
        question = payload.get("question") or DEFAULT_QUESTION
        self._write_json(run_demo(question))

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _write_json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


DEFAULT_QUESTION = "Revenue is up this fiscal week versus last fiscal week. What was the biggest driver of the increase?"


def main() -> None:
    init_db()
    server = ThreadingHTTPServer((HOST, PORT), DemoHandler)
    print(f"Demo server running at http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
