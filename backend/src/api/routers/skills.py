"""Skills API router - exposes skill management and invocation endpoints."""

import asyncio
import logging
from datetime import UTC, datetime
from time import monotonic
from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from skills.loader import SkillLoader
from skills.matcher import IntentMatchingService

from ...api.middleware import limiter
from ...core.config import settings
from ...core.security import _derive_user_id_from_api_key, verify_api_key
from ...services.chain import PipelineContext, _extract_sources
from ...services.chain._api import _run_pipeline

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skills", tags=["skills"], dependencies=[Depends(verify_api_key)])

SKILL_MATCH_TIMEOUT_S = 15.0
SKILL_INVOKE_TIMEOUT_S = 120.0

_loader = SkillLoader(custom_skills_dir=settings.CUSTOM_SKILLS_DIR)
# H-SKILL-1: Serialize skill mutations to prevent cache race windows.
_skill_mutation_lock = asyncio.Lock()


class SkillInvokeRequest(BaseModel):
    """Request model for skill invocation."""

    skill_name: str = Field(..., min_length=1, max_length=64, description="Name of the skill")
    query: str = Field(..., min_length=1, max_length=10_000, description="User query or context")
    user_id: str = Field(default="default", min_length=1, max_length=200)
    meeting_ids: list[int] | None = Field(
        default=None, max_length=50, description="Optional meeting IDs to restrict search"
    )


class SkillInvokeResponse(BaseModel):
    """Response model for skill invocation."""

    skill_name: str
    content: str
    format: str
    sources: list[dict[str, Any]]
    execution_time_ms: int


class SkillListResponse(BaseModel):
    """Response model for skill list."""

    skills: list[dict[str, Any]]
    total: int


class SkillSectionCreateRequest(BaseModel):
    """Request model for a custom output section."""

    title: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    required: bool = Field(default=True)


class SkillMatchSkillRef(BaseModel):
    name: str
    display_name: str


class SkillMatchResponse(BaseModel):
    matched: bool
    skill: SkillMatchSkillRef | None = None
    score: float = 0.0
    details: dict[str, Any] | None = None
    ambiguous: bool = False
    reason: str | None = None


class SkillCreateRequest(BaseModel):
    """Request model for creating a new skill definition file."""

    name: str = Field(
        ...,
        pattern=r"^[a-z][a-z0-9_]{2,62}$",
        description="Skill identifier in snake_case, e.g. custom_report_generator",
    )
    display_name: str = Field(..., min_length=3, max_length=120)
    description: str = Field(..., min_length=10, max_length=2000)
    required_keywords: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        default_factory=list, max_length=50
    )
    optional_keywords: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        default_factory=list, max_length=50
    )
    examples: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list, max_length=20
    )
    threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    sections: list[SkillSectionCreateRequest] = Field(default_factory=list, max_length=20)
    category: str = Field(default="custom", min_length=1, max_length=80)


class SkillCreateResponse(BaseModel):
    """Response model for created skill."""

    message: str
    file_path: str
    skill: dict[str, Any]


def _normalize_keywords(values: list[str]) -> list[str]:
    return [v.strip() for v in values if v.strip()]


def _default_sections(display_name: str) -> list[dict[str, Any]]:
    return [
        {
            "title": "1. Summary",
            "required": True,
            "description": f"Executive summary for {display_name}",
        },
        {
            "title": "2. Key Insights",
            "required": True,
            "description": "Most important findings from the selected meetings",
        },
        {
            "title": "3. Recommendations",
            "required": True,
            "description": "Actionable recommendations and next steps",
        },
    ]


@router.post("", response_model=SkillCreateResponse, status_code=201)
async def create_skill(request: SkillCreateRequest):
    """Create a new skill definition under skills/builtin.

    This endpoint creates a new `skill.md` file and makes the skill available to
    `/api/v1/skills` and skill invocation flows.
    """
    async with _skill_mutation_lock:
        return await _create_skill_locked(request)


async def _create_skill_locked(request: SkillCreateRequest):
    _loader.invalidate()
    existing_skills = _loader.load_summaries()
    existing_names = {s.name for s in existing_skills}
    if request.name in existing_names:
        raise HTTPException(status_code=409, detail="A skill with this name already exists")

    skill_dir_name = request.name.removesuffix("_generator")
    skill_dir = _loader.skills_dir / skill_dir_name
    skill_file = skill_dir / "skill.md"
    if skill_file.exists():
        raise HTTPException(
            status_code=409,
            detail="Skill file already exists for this name",
        )

    required_keywords = _normalize_keywords(request.required_keywords)
    optional_keywords = _normalize_keywords(request.optional_keywords)
    examples = [v.strip() for v in request.examples if v.strip()]
    sections = [
        {
            "title": s.title.strip(),
            "required": s.required,
            "description": s.description.strip(),
        }
        for s in request.sections
        if s.title.strip()
    ] or _default_sections(request.display_name)

    frontmatter: dict[str, Any] = {
        "name": request.name,
        "version": "1.0.0",
        "display_name": request.display_name.strip(),
        "description": request.description.strip(),
        "intent_matching": {
            "method": "hybrid",
            "threshold": request.threshold,
            "keywords": {
                "required": required_keywords,
                "optional": optional_keywords,
                "weight": 0.5,
                "semantic_weight": 0.5,
            },
            "examples": examples,
            "llm_routing": {"enabled": True, "weight": 0.2},
        },
        "execution": {"mode": "prompt_integrated", "timeout": 120},
        "output": {
            "format": "markdown",
            "sections": sections,
            "post_process": ["add_header_footer", "generate_toc"],
        },
        "metadata": {
            "author": "Meeting Agent Team",
            "created_at": datetime.now(UTC).date().isoformat(),
            "tags": ["custom", "generated"],
            "category": request.category.strip(),
            "use_cases": ["custom workflow"],
        },
    }

    body = (
        f"# {request.display_name.strip()}\n\n"
        "## Overview\n\n"
        f"{request.description.strip()}\n\n"
        "## Trigger Conditions\n\n"
        f"Trigger when users ask for outputs related to {request.display_name.strip()}.\n\n"
        "## Output Format\n\n"
        "Output follows the configured markdown section structure.\n"
    )
    yaml_content = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).strip()
    markdown_content = f"---\n{yaml_content}\n---\n\n{body}"

    try:
        skill_dir.mkdir(parents=True, exist_ok=False)
        skill_file.write_text(markdown_content, encoding="utf-8")
    except FileExistsError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Skill directory already exists: {skill_dir.name}",
        ) from exc
    except OSError:
        logger.exception("Failed to create skill file for skill=%s", request.name)
        raise HTTPException(status_code=500, detail="Failed to create skill file") from None

    _loader.invalidate()
    created = _loader.get_full(request.name)
    if created is None:
        raise HTTPException(status_code=500, detail="Skill created but failed to load")

    return SkillCreateResponse(
        message="Skill created",
        file_path=str(skill_file),
        skill={
            "name": created.name,
            "display_name": created.display_name,
            "description": created.description,
            "examples": created.intent_matching.examples,
            "category": created.metadata.category,
            "version": created.version,
        },
    )


@router.get("", response_model=SkillListResponse)
async def list_skills():
    """List all available skills with their definitions.

    Returns a list of all skills configured in the system,
    including their metadata and intent matching configuration.
    """
    skills = _loader.load_summaries()

    return SkillListResponse(
        skills=[
            {
                "name": s.name,
                "display_name": s.display_name,
                "description": s.description,
                "examples": s.intent_matching.examples,
            }
            for s in skills
        ],
        total=len(skills),
    )


@router.post("/invoke", response_model=SkillInvokeResponse)
@limiter.limit("20/minute")
async def invoke_skill(
    request: Request,
    body: SkillInvokeRequest,
    principal: dict[str, str] = Depends(verify_api_key),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
):
    """Manually invoke a specific skill by name.

    This endpoint executes the skill using prompt-integrated generation:
    1. Loads the skill definition (sections, format requirements)
    2. Retrieves meeting content via RAG
    3. Passes skill configuration to the LLM prompt
    4. LLM generates structured output following the skill format

    Args:
        body: Skill invocation request with skill name and query

    Returns:
        Skill-generated output with sources

    Raises:
        HTTPException: If skill not found or execution fails
    """
    skill = _loader.get_full(body.skill_name)

    if not skill:
        available = [s.name for s in _loader.load_summaries()]
        raise HTTPException(
            status_code=404,
            detail=f"Skill '{body.skill_name}' not found. Available: {', '.join(available)}",
        )

    started_at = monotonic()
    try:
        if body.user_id != principal["user_id"]:
            logger.warning(
                "Ignoring overridden user_id in skills invoke request: requested=%s principal=%s",
                body.user_id,
                principal["user_id"],
            )
        resolved_user_id = (
            _derive_user_id_from_api_key(x_api_key)
            if principal["user_id"] == "default" and x_api_key
            else principal["user_id"]
        )
        ctx = PipelineContext(
            question=body.query,
            user_id=resolved_user_id,
            meeting_ids=body.meeting_ids,
        )

        try:
            await asyncio.wait_for(
                _run_pipeline(ctx, skill.model_dump()),
                timeout=SKILL_INVOKE_TIMEOUT_S,
            )
        except TimeoutError:
            raise HTTPException(
                status_code=504,
                detail=f"Skill execution timed out after {SKILL_INVOKE_TIMEOUT_S}s",
            ) from None

        return SkillInvokeResponse(
            skill_name=skill.name,
            content=ctx.answer,
            format=skill.output.format,
            sources=_extract_sources(ctx.docs),
            execution_time_ms=max(0, round((monotonic() - started_at) * 1000)),
        )

    except HTTPException:
        raise
    except Exception:
        logger.exception("Skill execution failed for skill=%s", body.skill_name)
        raise HTTPException(status_code=500, detail="Skill execution failed") from None


@router.post("/match", response_model=SkillMatchResponse)
@limiter.limit("60/minute")
async def match_intent(
    request: Request,
    query: str = Query(..., min_length=1, max_length=10_000),
):
    """Test intent matching for a query without executing.

    Useful for debugging intent matching configuration.

    Args:
        query: User query to match

    Returns:
        Best matching skill with confidence scores
    """
    skills = _loader.load_summaries()

    matcher = IntentMatchingService()
    try:
        result = await asyncio.wait_for(
            matcher.match(query, skills),
            timeout=SKILL_MATCH_TIMEOUT_S,
        )
    except TimeoutError:
        return SkillMatchResponse(
            matched=False,
            reason=f"Skill matching timed out after {SKILL_MATCH_TIMEOUT_S}s",
        )

    if not result:
        return SkillMatchResponse(matched=False, reason="No skill matched")

    return SkillMatchResponse(
        matched=result.matched,
        skill=SkillMatchSkillRef(
            name=result.skill.name,
            display_name=result.skill.display_name,
        ),
        score=result.score,
        details=result.details,
        ambiguous=result.ambiguous,
    )
