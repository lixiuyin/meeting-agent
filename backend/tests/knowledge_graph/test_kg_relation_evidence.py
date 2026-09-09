from src.services.knowledge_graph._storage import _relation_is_supported


def test_directional_lead_relation_requires_matching_owner_and_target() -> None:
    evidence = "Omar replaced Nina as owner of the incident review."

    assert _relation_is_supported(
        {"subject": "Omar", "predicate": "leads", "object": "incident review"},
        evidence,
    )
    assert not _relation_is_supported(
        {"subject": "Omar", "predicate": "leads", "object": "Nina"},
        evidence,
    )
    assert not _relation_is_supported(
        {"subject": "Nina", "predicate": "leads", "object": "incident review"},
        evidence,
    )


def test_relation_endpoints_must_both_be_present_in_evidence() -> None:
    assert not _relation_is_supported(
        {"subject": "Alice", "predicate": "uses", "object": "Postgres"},
        "Alice uses SQLite.",
    )


def test_relation_requires_one_assertive_clause() -> None:
    relation = {"subject": "Alice", "predicate": "uses", "object": "Postgres"}

    assert not _relation_is_supported(relation, "")
    assert not _relation_is_supported(relation, "If approved, Alice uses Postgres.")
    assert not _relation_is_supported(relation, "Alice never uses Postgres.")
    assert not _relation_is_supported(relation, "Does Alice use Postgres?")
    assert not _relation_is_supported(relation, "Alice uses Postgres?")
    assert not _relation_is_supported(relation, "Alice will use Postgres.")
    assert not _relation_is_supported(
        relation,
        "Alice uses SQLite. Bob uses Postgres.",
    )
    assert not _relation_is_supported(
        relation,
        "Alice uses SQLite and Bob uses Postgres.",
    )
    assert not _relation_is_supported(
        relation,
        "Alice discussed migration risks with the team; Postgres was mentioned later.",
    )
    assert _relation_is_supported(relation, "Alice uses Postgres.")
