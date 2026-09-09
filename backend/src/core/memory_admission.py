"""Keep durable user/project state separate from reference-document knowledge."""

import json
import re
import sqlite3

REFERENCE_FAMILIES = frozenset({"topic", "course", "regulation", "program", "benchmark"})


def reference_memory_sql() -> str:
    """SQL equivalent of is_reference_memory, for candidate admission (alias m)."""
    families = (
        "substr(m.key,1,instr(m.key,'.')-1) IN ("
        + ",".join(f"'{family}'" for family in sorted(REFERENCE_FAMILIES))
        + ")"
    )
    predicates = ("owner", "deadline", "status", "risk", "dependency")
    state = " OR ".join(
        f"COALESCE(m.predicate,'')='{value}' OR "
        f"(COALESCE(m.predicate,'')='' AND (m.key='{value}' OR m.key GLOB '*.{value}'))"
        for value in predicates
    )
    legacy_family = (
        "(COALESCE(m.category,'')!='explicit_memory' AND "
        "COALESCE(m.project_id,'')='' AND COALESCE(m.assignee,'')='' "
        "AND COALESCE(m.action_status,'')='' AND NOT (" + state + ") "
        "AND COALESCE(m.source,'') IN ('auto_extracted','consolidated') "
        "AND COALESCE(m.fact_type,'fact') IN ('fact','project_fact') AND (" + families + "))"
    )
    evidence_json = (
        "CASE WHEN json_valid(COALESCE(m.evidence_refs,'[]')) "
        "THEN COALESCE(m.evidence_refs,'[]') ELSE '[]' END"
    )
    ref_object = "CASE WHEN j.type='object' THEN j.value ELSE '{}' END"
    knowledge_file = (
        "f.id IS NOT NULL AND COALESCE(f.approval_status,'unreviewed')!='rejected' AND ("
        "COALESCE(f.business_domain,'unspecified')='course' OR "
        "COALESCE(f.material_role,'attachment') NOT IN ('transcript','minutes','decision_log') OR "
        "(COALESCE(f.business_domain,'unspecified')='research' AND "
        "COALESCE(f.approval_status,'unreviewed') NOT IN ('reviewed','approved')))"
    )
    source_reference = (
        "(COALESCE(m.category,'')!='explicit_memory' AND "
        "COALESCE(m.source,'') IN ('auto_extracted','consolidated') AND "
        "EXISTS (SELECT 1 FROM json_each(" + evidence_json + ") j "
        "JOIN meeting_files f ON f.id=CAST(json_extract("
        + ref_object
        + ",'$.file_id') AS INTEGER) "
        "AND f.user_id=m.user_id WHERE " + knowledge_file + ") AND "
        "NOT EXISTS (SELECT 1 FROM json_each(" + evidence_json + ") j "
        "LEFT JOIN meeting_files f ON f.id=CAST(json_extract("
        + ref_object
        + ",'$.file_id') AS INTEGER) "
        "AND f.user_id=m.user_id WHERE json_type("
        + ref_object
        + ",'$.file_id')='integer' AND NOT ("
        + knowledge_file
        + ")))"
    )
    return f"({legacy_family} OR {source_reference})"


def is_domain_state(row: dict) -> bool:
    return bool(row.get("project_id") or row.get("assignee") or row.get("action_status")) or (
        str(row.get("predicate") or str(row.get("key") or "").rsplit(".", 1)[-1])
        in {"owner", "deadline", "status", "risk", "dependency"}
    )


def is_reference_memory(row: dict, *, conn: sqlite3.Connection | None = None) -> bool:
    if row.get("category") == "explicit_memory" or str(row.get("source") or "") not in {
        "auto_extracted",
        "consolidated",
    }:
        return False
    if conn is not None:
        raw_refs = row.get("evidence_refs")
        try:
            refs = json.loads(raw_refs) if isinstance(raw_refs, str) else raw_refs
        except (TypeError, ValueError):
            refs = []
        file_ids = sorted(
            {
                ref["file_id"]
                for ref in refs or []
                if isinstance(ref, dict) and type(ref.get("file_id")) is int
            }
        )
        if file_ids:
            placeholders = ",".join("?" * len(file_ids))
            files = [
                dict(file)
                for file in conn.execute(
                    f"SELECT * FROM meeting_files WHERE user_id=? AND id IN ({placeholders})",
                    [row.get("user_id"), *file_ids],
                ).fetchall()
            ]
            if len(files) == len(file_ids) and all(
                file_memory_policy(file) == "knowledge_only" for file in files
            ):
                return True
    return (
        not is_domain_state(row)
        and str(row.get("fact_type") or "fact") in {"fact", "project_fact"}
        and str(row.get("key") or "").split(".", 1)[0] in REFERENCE_FAMILIES
    )


def explicitly_requested_memory(question: str) -> bool:
    if re.search(
        r"\b(?:do not|don't|never)\s+(?:remember|memorize|save)\b|不要记|别记|不保存",
        question,
        re.I,
    ):
        return False
    return bool(
        re.search(
            r"\b(?:remember|memorize|save this)\b|请记住|帮我记住|记一下|保存为记忆", question, re.I
        )
    )


def file_memory_policy(file: dict, file_name: str = "") -> str:
    from .material_role import infer_material_role

    if file.get("approval_status") == "rejected":
        return "disabled"
    role = file.get("material_role") or infer_material_role(
        file_name or str(file.get("file_name") or ""), str(file.get("file_type") or "")
    )
    if file.get("business_domain") == "course":
        return "knowledge_only"
    if file.get("business_domain") == "research" and (
        role not in {"minutes", "decision_log"}
        or file.get("approval_status") not in {"reviewed", "approved"}
    ):
        return "knowledge_only"
    return (
        "project_state" if role in {"transcript", "minutes", "decision_log"} else "knowledge_only"
    )
