from __future__ import annotations

from dataclasses import asdict

from .llm import LLMClient, LLMError, strip_code_fences
from .models import StructuredRequest


DATABASE_CONTEXT = """
Vendor database guidance. The raw_orders table is physically organized around fiscal week keys.
The engine can seek efficiently on week_rank, but only if the query preserves that key directly.
For delta analysis, do not compute week buckets from order_date when a physical fiscal-week key already exists.
The optimizer can only optimize the query shape it receives; it cannot invent the right access path from a scan-heavy formulation.
""".strip()


class DatabaseNativeAgent:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or LLMClient()

    def build_engine_sql(self, request: StructuredRequest) -> str:
        fallback = f"""
WITH weekly AS (
    SELECT week_rank,
           {request.driver_dimension},
           SUM(recognized_revenue) AS revenue
    FROM {request.source_table}
    WHERE week_rank IN (1, 2)
    GROUP BY 1, 2
)
SELECT c.{request.driver_dimension}, ROUND(c.revenue - p.revenue, 2) AS revenue_delta
FROM weekly c JOIN weekly p USING ({request.driver_dimension})
WHERE c.week_rank = 1 AND p.week_rank = 2
ORDER BY revenue_delta DESC
LIMIT 1;
""".strip()
        if not self.llm.enabled:
            return fallback

        prompt = f"""
Structured request: {asdict(request)}

Write SQLite SQL only.

Constraints:
- use raw_orders
- preserve the physical week_rank key
- filter with week_rank IN (1, 2)
- group only by {request.driver_dimension}
- return exactly two columns: {request.driver_dimension} and revenue_delta
- keep the query compact, about 10 lines
- do not use markdown fences
""".strip()
        try:
            return strip_code_fences(self.llm.complete_text(system=DATABASE_CONTEXT, prompt=prompt))
        except LLMError:
            return fallback
