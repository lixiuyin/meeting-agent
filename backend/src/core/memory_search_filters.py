"""Management search filters, shared by SQL candidate admission and race rechecks."""

import sqlite3
from dataclasses import dataclass

from .memory_admission import is_reference_memory, reference_memory_sql


@dataclass(frozen=True)
class MemorySearchFilters:
    memory_kind: str = "all"
    fact_type: str | None = None
    assertion_status: str | None = None

    def matches(self, row: dict, *, conn: sqlite3.Connection | None = None) -> bool:
        if self.fact_type and row.get("fact_type", "fact") != self.fact_type:
            return False
        if (
            self.assertion_status
            and row.get("assertion_status", "confirmed") != self.assertion_status
        ):
            return False
        reference = bool(row.get("_reference_memory")) or is_reference_memory(row, conn=conn)
        return self.memory_kind == "all" or reference == (self.memory_kind == "reference")

    def sql(self) -> tuple[list[str], list[str]]:
        clauses, values = [], []
        for field, value in (
            ("fact_type", self.fact_type),
            ("assertion_status", self.assertion_status),
        ):
            if value:
                clauses.append(f"m.{field}=?")
                values.append(value)
        if self.memory_kind != "all":
            clauses.append(
                ("NOT " if self.memory_kind == "personal" else "") + reference_memory_sql()
            )
        return clauses, values
