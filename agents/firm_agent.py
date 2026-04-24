from __future__ import annotations

from .database_native_agent import DATABASE_CONTEXT
from .llm import LLMClient, LLMError, strip_code_fences
from .models import StructuredRequest


FIRM_CONTEXT = """
ExaBank finance analytics policy. Revenue means net recognized revenue from certified finance views.
Weekly performance should use ExaBank's fiscal calendar, not ISO or Gregorian week boundaries.
Driver analysis compares current fiscal week to the prior fiscal week and ranks the contribution to the delta.
Only aggregated outputs are allowed for exploratory analytics.
""".strip()

SINGLE_AGENT_CONTEXT = FIRM_CONTEXT + "\n\n" + DATABASE_CONTEXT


class ExaBankFirmAgent:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or LLMClient()

    def build_request(self, question: str) -> StructuredRequest:
        fallback = StructuredRequest(
            metric="net_recognized_revenue",
            comparison="current_fiscal_week_to_date_vs_prior_fiscal_week_to_date",
            driver_dimension="product_family",
            source_table="raw_orders",
        )
        if not self.llm.enabled:
            return fallback

        prompt = f"""
Question: {question}

Return a JSON object with exactly these keys:
- metric
- comparison
- driver_dimension
- source_table

Constraints:
- metric must be "net_recognized_revenue"
- comparison must be "current_fiscal_week_to_date_vs_prior_fiscal_week_to_date"
- driver_dimension must be "product_family"
- source_table must be "raw_orders"
- return JSON only
""".strip()
        try:
            payload = self.llm.complete_json(system=FIRM_CONTEXT, prompt=prompt)
            return StructuredRequest(**payload)
        except (LLMError, TypeError, ValueError):
            return fallback

    def build_portable_sql(self, question: str) -> str:
        fallback = """
WITH weekly AS (
    SELECT CASE WHEN order_date >= '2026-04-21' THEN 1 ELSE 2 END AS wk,
           product_family,
           SUM(recognized_revenue) AS revenue
    FROM raw_orders
    WHERE order_date BETWEEN '2026-04-14' AND '2026-04-27'
    GROUP BY 1, 2
)
SELECT c.product_family, ROUND(c.revenue - p.revenue, 2) AS revenue_delta
FROM weekly c JOIN weekly p USING (product_family)
WHERE c.wk = 1 AND p.wk = 2
ORDER BY revenue_delta DESC
LIMIT 1;
""".strip()
        if not self.llm.enabled:
            return fallback

        prompt = f"""
Question: {question}

Write SQLite SQL only.

Constraints:
- use raw_orders
- group only by product_family
- return exactly two columns: product_family and revenue_delta
- keep the query compact, about 10 lines
- do not use markdown fences
""".strip()
        try:
            return strip_code_fences(self.llm.complete_text(system=SINGLE_AGENT_CONTEXT, prompt=prompt))
        except LLMError:
            return fallback
