"""Configuration identity tests for skill-system singletons."""

from src.services.chain import _skill_matching as skill_matching


def test_skill_loader_is_recreated_when_custom_directory_changes(monkeypatch, tmp_path):
    created: list[str] = []

    class _Loader:
        def __init__(self, *, custom_skills_dir):
            created.append(str(custom_skills_dir))

    monkeypatch.setattr(skill_matching, "SkillLoader", _Loader)
    skill_matching._skill_loader = None
    skill_matching._skill_loader_key = None
    try:
        monkeypatch.setattr(skill_matching.settings, "CUSTOM_SKILLS_DIR", tmp_path / "first")
        first = skill_matching.get_skill_loader()
        assert skill_matching.get_skill_loader() is first

        monkeypatch.setattr(skill_matching.settings, "CUSTOM_SKILLS_DIR", tmp_path / "second")
        second = skill_matching.get_skill_loader()

        assert second is not first
        assert created == [
            str((tmp_path / "first").resolve()),
            str((tmp_path / "second").resolve()),
        ]
    finally:
        skill_matching._skill_loader = None
        skill_matching._skill_loader_key = None


def test_skill_matcher_is_recreated_when_dependency_key_changes(monkeypatch):
    state = {"key": ("first",)}
    created: list[object] = []

    class _Matcher:
        def __init__(self):
            created.append(self)

    monkeypatch.setattr(skill_matching, "IntentMatchingService", _Matcher)
    monkeypatch.setattr(skill_matching, "_matcher_config_key", lambda: state["key"])
    skill_matching._skill_matcher = None
    skill_matching._skill_matcher_key = None
    try:
        first = skill_matching.get_skill_matcher()
        assert skill_matching.get_skill_matcher() is first

        state["key"] = ("second",)
        second = skill_matching.get_skill_matcher()

        assert second is not first
        assert created == [first, second]
    finally:
        skill_matching._skill_matcher = None
        skill_matching._skill_matcher_key = None
