from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StructuredRequest:
    metric: str
    comparison: str
    driver_dimension: str
    source_table: str
