"""Validate migration definitions are correctly ordered and well-formed."""

from src.core.database._migration_definitions import _MIGRATIONS


def test_migrations_sorted_by_version():
    versions = [v for v, _, _ in _MIGRATIONS]
    assert versions == sorted(versions), (
        f"Migration versions are not in ascending order: {versions}"
    )


def test_migration_versions_are_unique():
    versions = [v for v, _, _ in _MIGRATIONS]
    assert len(versions) == len(set(versions)), f"Duplicate migration versions found: {versions}"


def test_migration_versions_are_contiguous():
    versions = [v for v, _, _ in _MIGRATIONS]
    assert versions == list(range(1, len(versions) + 1)), (
        f"Migration versions are not contiguous starting from 1: {versions}"
    )
