"""Tests for skills.loader module."""

from skills.loader import SkillLoader
from skills.models import SkillDefinition


class TestSkillLoader:
    def test_load_all_includes_builtin_tech_proposal(self):
        loader = SkillLoader()
        skills = loader.load_all()
        assert len(skills) >= 1
        names = {s.name for s in skills}
        assert "tech_proposal_generator" in names

    def test_tech_proposal_shape(self):
        loader = SkillLoader()
        loader.load_all()
        skill = loader.get("tech_proposal_generator")
        assert skill is not None
        assert isinstance(skill, SkillDefinition)
        assert skill.display_name == "MOST (PRC) Technical Proposal Generator"
        assert skill.intent_matching.method == "hybrid"
        assert "technical proposal" in skill.intent_matching.keywords.get("required", [])
        assert len(skill.output.sections) >= 1

    def test_get_missing_returns_none(self):
        loader = SkillLoader()
        loader.load_all()
        assert loader.get("nonexistent_skill_12345") is None

    def test_load_all_ignores_missing_dir(self, tmp_path):
        loader = SkillLoader(tmp_path / "no_such_dir")
        skills = loader.load_all()
        assert skills == []

    def test_skill_base_path_set(self):
        loader = SkillLoader()
        loader.load_all()
        skill = loader.get("tech_proposal_generator")
        assert skill is not None
        assert skill.base_path is not None
        assert (skill.base_path / "skill.md").exists()
