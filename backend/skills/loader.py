"""
Skill loader with two-tier caching.

Tier 1 — *summaries*: lightweight ``SkillSummary`` objects (name, description,
intent_matching config) cached in memory and used for fast intent matching.
Cache is validated against file mtimes on every ``load_summaries()`` call.

Tier 2 — *full definitions*: ``SkillDefinition`` objects loaded lazily only
when a skill is matched. Cached per-name until ``invalidate()`` is called.
"""

import logging
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, Template

from .models import (
    ExecutionConfig,
    IntentMatchingConfig,
    OutputConfig,
    SkillDefinition,
    SkillMetadata,
    SkillSummary,
)

logger = logging.getLogger(__name__)


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Extract YAML frontmatter and body from a Markdown string."""
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    return yaml.safe_load(parts[1]) or {}, parts[2].strip()


class SkillLoader:
    """
    Cached, two-tier skill loader.

    Usage::

        loader = SkillLoader()
        summaries = loader.load_summaries()      # fast, cached
        full     = loader.get_full("my_skill")   # lazy, cached per-name
    """

    def __init__(self, skills_dir: str | Path = "skills/builtin"):
        self.skills_dir = Path(skills_dir)
        # Tier-1: summaries
        self._summaries: list[SkillSummary] = []
        self._summaries_loaded: bool = False
        self._mtimes: dict[str, float] = {}
        # Tier-2: full definitions
        self._definitions: dict[str, SkillDefinition] = {}
        self._templates: dict[str, Template] = {}

    # ------------------------------------------------------------------
    # Tier 1 — summaries (cached, mtime-validated)
    # ------------------------------------------------------------------

    def load_summaries(self) -> list[SkillSummary]:
        """Return lightweight skill summaries, using cache when valid."""
        if self._summaries_loaded and not self._mtimes_changed():
            return self._summaries
        self._reload_summaries()
        return self._summaries

    def get_summary(self, name: str) -> SkillSummary | None:
        """Look up a single summary by name (loads summaries if needed)."""
        for s in self.load_summaries():
            if s.name == name:
                return s
        return None

    # ------------------------------------------------------------------
    # Tier 2 — full definitions (lazy, cached per-name)
    # ------------------------------------------------------------------

    def get_full(self, name: str) -> SkillDefinition | None:
        """Load and cache the full definition for *name*.

        Returns ``None`` if the skill does not exist.
        """
        if name in self._definitions:
            return self._definitions[name]
        summary = self.get_summary(name)
        if summary is None or summary.base_path is None:
            return None
        definition = self._load_full(summary.base_path)
        if definition is not None:
            self._definitions[name] = definition
        return definition

    def get_template(self, skill_name: str) -> Template | None:
        """Get Jinja2 template for skill (loaded alongside full definition)."""
        return self._templates.get(skill_name)

    # ------------------------------------------------------------------
    # Cache control
    # ------------------------------------------------------------------

    def invalidate(self) -> None:
        """Clear all caches; next access will re-read from disk."""
        self._summaries.clear()
        self._summaries_loaded = False
        self._mtimes.clear()
        self._definitions.clear()
        self._templates.clear()

    # ------------------------------------------------------------------
    # Legacy compatibility
    # ------------------------------------------------------------------

    def load_all(self) -> list[SkillDefinition]:
        """Load all full definitions (legacy, prefer ``load_summaries``)."""
        summaries = self.load_summaries()
        results: list[SkillDefinition] = []
        for s in summaries:
            full = self.get_full(s.name)
            if full is not None:
                results.append(full)
        return results

    def get(self, name: str) -> SkillDefinition | None:
        """Alias for ``get_full`` (legacy)."""
        return self.get_full(name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _collect_skill_dirs(self) -> list[Path]:
        if not self.skills_dir.exists():
            return []
        return sorted(
            d for d in self.skills_dir.iterdir() if d.is_dir() and (d / "skill.md").exists()
        )

    def _current_mtimes(self) -> dict[str, float]:
        mtimes: dict[str, float] = {}
        for skill_dir in self._collect_skill_dirs():
            skill_file = skill_dir / "skill.md"
            try:
                mtimes[skill_dir.name] = skill_file.stat().st_mtime
            except OSError:
                pass
        return mtimes

    def _mtimes_changed(self) -> bool:
        current = self._current_mtimes()
        if current.keys() != self._mtimes.keys():
            return True
        return any(current[k] != self._mtimes[k] for k in current)

    def _reload_summaries(self) -> None:
        self._summaries.clear()
        self._mtimes.clear()
        for skill_dir in self._collect_skill_dirs():
            skill_file = skill_dir / "skill.md"
            try:
                content = skill_file.read_text(encoding="utf-8")
                metadata, _ = _parse_frontmatter(content)
                # H-SKILL-2: Validate required fields explicitly.
                if not metadata.get("name"):
                    raise ValueError(f"Skill file {skill_file} missing required 'name' field")
                summary = SkillSummary(
                    name=metadata["name"],
                    display_name=metadata.get("display_name", metadata["name"]),
                    description=metadata.get("description", ""),
                    intent_matching=IntentMatchingConfig(
                        **metadata.get("intent_matching", {})
                    ),
                    base_path=skill_dir,
                )
                self._summaries.append(summary)
                self._mtimes[skill_dir.name] = skill_file.stat().st_mtime
            except Exception:
                logger.exception("Failed to load skill summary from %s", skill_file)
                self._quarantine_skill(skill_dir)
        self._summaries_loaded = True
        # Invalidate tier-2 entries whose source changed or disappeared
        valid_names = {s.name for s in self._summaries}
        stale = [n for n in self._definitions if n not in valid_names]
        for name in stale:
            self._definitions.pop(name, None)
            self._templates.pop(name, None)

    def _load_full(self, skill_dir: Path) -> SkillDefinition | None:
        skill_file = skill_dir / "skill.md"
        try:
            content = skill_file.read_text(encoding="utf-8")
            metadata, documentation = _parse_frontmatter(content)
            name = metadata["name"]
            # Load optional template
            template_file = skill_dir / "template.j2"
            if template_file.exists():
                env = Environment(loader=FileSystemLoader(skill_dir))
                self._templates[name] = env.get_template("template.j2")
            return SkillDefinition(
                name=name,
                version=metadata.get("version", "1.0.0"),
                display_name=metadata.get("display_name", name),
                description=metadata.get("description", ""),
                intent_matching=IntentMatchingConfig(
                    **metadata.get("intent_matching", {})
                ),
                execution=ExecutionConfig(**metadata.get("execution", {})),
                output=OutputConfig(**metadata.get("output", {})),
                metadata=SkillMetadata(**metadata.get("metadata", {})),
                documentation=documentation,
                base_path=skill_dir,
            )
        except Exception:
            logger.exception("Failed to load full skill from %s", skill_dir)
            self._quarantine_skill(skill_dir)
            return None

    def _quarantine_skill(self, skill_dir: Path) -> None:
        """H-SKILL-2: Move a broken skill directory to _quarantine/."""
        try:
            quarantine = skill_dir.parent / "_quarantine"
            quarantine.mkdir(exist_ok=True)
            dest = quarantine / skill_dir.name
            if dest.exists():
                import shutil
                shutil.rmtree(dest)
            skill_dir.rename(dest)
            logger.warning("Quarantined broken skill: %s -> %s", skill_dir.name, dest)
        except Exception:
            logger.debug("Failed to quarantine skill %s", skill_dir.name, exc_info=True)
