# Meeting Agent Skill System Documentation

## Overview

The Skill system is a pluggable function extension module based on Markdown configuration, which allows the Meeting Agent to automatically trigger specific content formatting logic based on the user's intention.

**Core design concept:**
- **Prompt integration mode**: Put the Skill configuration and RAG content into Prompt, and LLM directly generates structured output
- **Markdown Configuration**: Skill definitions are stored in `.md` files for easy version control and manual editing
- **Multi-layer intent matching**: Combining keywords, semantic similarity and LLM judgment to accurately identify user intentions

---

## System architecture

```
user input
    ↓
IntentMatchingService
    ├── Layer 1: KeywordMatcher - Quick filtering
    ├── Layer 2: Semantic Similarity (SemanticMatcher) - Vector calculation
    └── Layer 3: LLM routing judgment (LLMRouter) - complex disambiguation
    ↓
Match Skill?
    ├── Yes → RAG search → [Skill configuration + content] → Prompt → LLM generation → Return structured output
    └── No → Standard RAG → Return to normal answer
```

**Advantages of Prompt integration mode**:
1. LLM sees the format requirements and meeting content at the same time, generating more coherent documents
2. LLM intelligently distributes content to corresponding chapters instead of simply splicing it together
3. LLM can clearly point out that a certain chapter information was not mentioned in the meeting
4. Single-stage generation to reduce latency

---

## Directory structure

The following **`skills/`** directories are relative to the **`backend/`** repository root (the Python package `skills` alongside `src/`, and the `backend/skills/builtin/` built-in Markdown skills).

```
skills/
├── __init__.py # Package export
├── models.py # Data model definition
├── loader.py # Markdown file loader
├── matcher.py # Intent matching service
├── executor.py # Execution engine (coordination layer)
├── builtin/ # Built-in Skill directory
│ ├── action_items/
│ │ └── skill.md
│ ├── custom_notes/
│ │ └── skill.md
│ ├── meeting_minutes/
│ │ └── skill.md
│ ├── risk_register/
│ │ └── skill.md
│ ├── stakeholder_update/
│ │ └── skill.md
│ └── tech_proposal/
│ └── skill.md
└── Documentation.md # This document
```

---

## Detailed explanation of core modules

### 1. models.py - data model layer

**Function**: Define all data structures of the Skill system and use Pydantic for verification.

**Core Class**:

| Class name             | Function                                                                                                 |
| ---------------------- | -------------------------------------------------------------------------------------------------------- |
| `SkillDefinition`      | Complete definition of Skill, including name, description, matching rules, execution configuration, etc. |
| `IntentMatchingConfig` | Intent matching configuration (methods, thresholds, keywords, examples, etc.)                            |
| `ExecutionConfig`      | Execution configuration (mode, timeout, etc.)                                                            |
| `OutputConfig`         | Output format configuration (chapter, template, post-processing)                                         |
| `SkillMatchResult`     | Intent match result                                                                                      |
| `SkillExecutionResult` | Skill execution result                                                                                   |

**Key Design**:
- All configurations can be deserialized from YAML Frontmatter
- Support additional fields (`extra = "allow"`) for easy expansion

---

### 2. loader.py - Skill loader

**Function**: Load Skill definitions from the file system and parse YAML Frontmatter in Markdown files.

**Core Class**: `SkillLoader`

**Workflow**:

```python
1. Traverse built-in definitions under `skills/builtin/` and persistent custom
   definitions under `CUSTOM_SKILLS_DIR` (`data/skills/` by default)
2. Find the skill.md file
3. Parse the file content:
   - Extract YAML configuration between ---
   - Extract --- the subsequent Markdown document
4. Construct the SkillDefinition object
5. Optional: Load the template.j2 template file in the same directory
```

**Code Example**:
```python
loader = SkillLoader("skills")
skills = loader.load_all() # Load all Skills
skill = loader.get("tech_proposal_generator") # Get a specific Skill
```

---

### 3. matcher.py - Intent matching service

**Function**: Match user input with Skill and return the best matching Skill.

**Core Class**:

#### 3.1 KeywordMatcher (Keyword Matcher)

**Principle**: Exact matching based on keywords

**Matching Rules**:
- **Required keywords** (`required`): all must appear, otherwise they will be rejected directly
- **Optional keyword** (`optional`): The more occurrences, the higher the score
- **Excluded keywords** (`excluded`): If it appears, it will be rejected directly.
- **regular expressions** (`patterns`): complex pattern matching

**Score Calculation**:
```
score = required_score * 0.4 + optional_score * 0.4 + regex_score * 0.2
```

#### 3.2 SemanticMatcher (semantic matcher)

**Principle**: Semantic similarity calculation based on vector embedding

**Workflow**:
1. **Precalculation**: When loading the Skill, calculate the embeddings of all `examples` and `description`
2. **Query time**: Calculate the embedding entered by the user
3. **Similarity**: Use cosine similarity to calculate similarity to all examples
4. **Aggregation**: Take the average of the Top-3 similarities as the final score

**Caching mechanism**:
```python
self._cache: dict[str, np.ndarray] # skill_name -> embeddings
```

#### 3.3 LLMRouter (LLM routing judge)

**Function**: When multiple Skill scores are close, use LLM to make the final decision

**Trigger conditions**:
- Score difference between Top 2 Skills < 0.1
- `llm_routing` is enabled in Skill configuration

**Prompt word design**:
```
You are a Skill Routing Assistant. Select the Skill that best suits the user's query.

User query: "xxx"
Candidate Skills:
- Skill: name / Description / Examples
...

Reply format:
SKILL: <skill_name>
CONFIDENCE: <0.0-1.0>
REASONING: <brief description>
```

#### 3.4 IntentMatchingService (main entrance of matching service)

**Coordination Process**:
```
For each Skill:
    1. Keyword matching → keyword_score
    2. Semantic matching → semantic_score
    3. Calculate the weighted total score
    4. If it is lower than the 1/2 threshold, skip it directly.

5. Sort all candidate Skills and obtain best.score
6. If the difference between Top 2 is < 0.1:
    LM routing judgment → adjust best.score score

7. When best.score is greater than the threshold, the skill corresponding to the best.score is used as the optimal match.
```

---

### 4. executor.py - Execution engine

**Role**: Coordination layer for Skill execution.

**Description**:
The actual Skill execution logic is integrated directly into the RAG process (see `chain.py`). When a Skill matches, its configuration is passed to LLM via `generate_answer(skill_definition)`, which directly generates structured output.

The `SkillExecutor` class is reserved as a coordination layer for potential extension scenarios.

---

### 5. chain/ integration point

**Modification location**: `ask()` function in `src/services/chain/_api.py` and `generate_answer()` function in `_steps_generate.py`

**Integration logic (Prompt integration mode)**:
```python
# _api.py
async def ask(question, ...):
    # 1. Skill matching starts concurrently with asyncio.create_task()
    skill_task = None
    if settings.SKILL_MATCHING_ENABLED:
        skill_task = asyncio.create_task(_do_skill_match(question))

    # 2. Create Pipeline context
    ctx = PipelineContext(...)

    # 3. Consume skill_task results in _run_pipeline (usually completed at this time)
    await _run_pipeline(ctx, skill_definition=None, skill_task=skill_task)
```

Skill matching runs in parallel with the RAG retrieval pipeline, and its result is consumed before `generate_answer`. If `rewritten_query` differs from the original query, both queries are matched and the higher-confidence result is used. Very short input (≤2 words) skips Skill matching.

```python
        return PipelineResult(
            answer=ctx.answer,
            sources=_extract_sources(ctx.docs),
            session_id=ctx.session_id,
            skill_used=match.skill.name,
            skill_confidence=match.score,
            ...
        )

    else:
        # 4. No match, use standard RAG
        await _run_pipeline(ctx, None)
        return PipelineResult(...)
```

**Key implementation details**:

Skill matching uses the singleton in the `src/services/chain/_skill_matching.py` module:
```python
from ._skill_matching import get_skill_loader, get_skill_matcher
```
- `get_skill_loader()` returns a thread-safe `SkillLoader` singleton
- `get_skill_matcher()` returns a thread-safe `IntentMatchingService` singleton

The `get_skill_prompt()` function in `src/services/llm/_prompts.py`:
```python
def get_skill_prompt(skill_definition: dict[str, Any] | None = None) -> ChatPromptTemplate:
    """Build skill-aware prompt template."""
    if skill_definition:
        # Build sections description from skill config
        sections = skill_definition.get("output", {}).get("sections", [])
        sections_desc = []
        for i, section in enumerate(sections, 1):
            title = section.get("title", f"Section {i}")
            desc = section.get("description", "")
            req = " (REQUIRED)" if section.get("required", True) else " (optional)"
            sections_desc.append(f"{i}. **{title}**{req}\n {desc}")

        formatted_system = SKILL_SYSTEM_TEMPLATE.format(
            memory_context="{memory_context}",
            skill_description=skill_definition.get("description", ...),
            skill_sections="\n\n".join(sections_desc),
        )

        return ChatPromptTemplate.from_messages([
            ("system", formatted_system),
            MessagesPlaceholder("history"),
            ("human", SKILL_RAG_TEMPLATE),
        ])

    return get_rag_prompt() # Fallback to standard RAG prompt
```

In `generate_answer()` of `_steps_generate.py`:
```python
async def generate_answer(ctx: PipelineContext, skill_definition: dict[str, Any] | None = None):
    llm = ctx.llm or get_llm()

    # Use skill-aware prompt if skill is specified
    if skill_definition:
        prompt = get_skill_prompt(skill_definition)
    else:
        prompt = get_rag_prompt()

    chain = prompt | llm | StrOutputParser()
    ctx.answer = await asyncio.to_thread(chain.invoke, {...})
```

**`ask()` Full signature**:
```python
async def ask(
    question: str,
    session_id: str | None = None,
    user_id: str = "default",
    meeting_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
    top_k: int | None = None,
    use_web_search: bool = False,
    web_search_results: int | None = None,
    file_types: list[str] | None = None,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    rag_mode: str | None = None,
) -> PipelineResult:
```

**`PipelineContext` key fields**:

| Field | Type | Description |
|------|------|------|
| `file_ids` | `list[int] \| None` | File IDs to limit the search |
| `web_search_results` | `int \| None` | Number of web search results |
| `rag_mode` | `str \| None` | RAG mode (such as `hybrid`) |
| `settings_epoch` | `int` | Set the epoch version of the snapshot |
| `settings_snapshot` | `SettingsSnapshot \| None` | Configuration snapshot at request |
| `past_session_refs` | `list[dict]` | Historical session references |
| `trace` | `TraceContext` | Structured tracing context |

**`PipelineResult` complete fields**:

> This is an internal result of the chain and is not equivalent to HTTP `ChatResponse`. `skill_used` and
> `skill_confidence` is currently used for internal orchestration/trace and is not returned as a `/api/v1/chat` top-level response field.

| Field | Type | Description |
|------|------|------|
| `answer` | `str` | Answer generated by LLM |
| `sources` | `list[dict]` | Quote sources |
| `session_id` | `str` | Session ID |
| `web_results` | `list[dict] \| None` | Web search results |
| `past_sessions` | `list[dict] \| None` | Related historical sessions |
| `extraction_failed` | `bool` | Whether accepting/enqueueing fact extraction failed; later durable-job outcome is observed through job health/metrics |
| `trace` | `dict \| None` | Serialized trace data |
| `skill_used` | `str \| None` | The name of the Skill used |
| `skill_confidence` | `float \| None` | Match confidence |

---

## Detailed explanation of Skill configuration file

### File location
Built-in: `skills/builtin/{skill_name}/skill.md`

Custom/API-created: `data/skills/{skill_name}/skill.md`

### File format
```markdown
---
# YAML Frontmatter - Skill configuration
name: skill_identifier # Unique identifier (English)
display_name: "Display name" # Chinese display name
description: "Detailed description"

intent_matching:
  method: hybrid # matching method
  threshold: 0.7 #Trigger threshold
  keywords:
    required: ["Required keywords"]
    optional: ["Optional keywords"]
  examples:
    - "Example Query 1"
    - "Example Query 2"
  llm_routing:
    enabled: true

execution:
  mode: post_rag #Execution mode
  timeout: 120

output:
  format: markdown
  sections:
    - title: "Chapter Title"
      required: true
  post_process:
    - add_header_footer
    - generate_toc
---

# Markdown content - Skill documentation description

## Function description
...
```

---

## API interface

### HTTP endpoint

| Method | Path | Description |
|------|------|------|
| GET | `/api/v1/skills` | List all Skills |
| POST | `/api/v1/skills` | Create new Skill |
| POST | `/api/v1/skills/invoke` | Manually call the specified Skill |
| POST | `/api/v1/skills/match?query=...` | Test intent matching (for debugging, query parameter) |

### MCP Tools

| Tool name | Description |
|--------|------|
| `list_skills` | List available Skills |
| `invoke_skill` | Invoke the specified Skill |

---

## Usage example

### Example 1: Trigger Tech Proposal Skill

```bash
curl -X POST http://localhost:7008/api/v1/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "question": "Please help me generate a technical plan from the Ministry of Science and Technology"
  }'
```

**Expected return**:
```json
{
  "answer": "# 1. Project background and significance\n...",
  "sources": [...],
  "session_id": "...",
  "extraction_failed": false
}
```

### Example 2: Manually calling Skill

```bash
curl -X POST http://localhost:7008/api/v1/skills/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "skill_name": "tech_proposal_generator",
    "query": "AI project technical solution"
  }'
```

### Example 3: Test intent matching

```bash
curl -X POST 'http://localhost:7008/api/v1/skills/match?query=%E7%94%9F%E6%88%90%E6%8A%80%E6%9C%AF%E6%96%B9%E6%A1%88' \
  -H "X-API-Key: your-key"
```

---

## Extension Development Guide

### Steps to create a new Skill

1. **Create directory**: `data/skills/my_skill/` for runtime-created Skills, or
   `skills/builtin/my_skill/` only when shipping a new immutable built-in Skill.

2. **Write skill.md**:
```markdown
---
name: my_skill
display_name: "My Skill"
description: "Description"
intent_matching:
  method: hybrid
  keywords:
    required: ["keyword"]
  examples:
    - "Example query"
---

## Function description
...
```

3. **(Optional) Add additional configuration**:
   - In the current Prompt integration mode, the `output.template_file` and `output.post_process` fields are not used
   - These fields are reserved for possible future extensions

4. **Restart the service**, the new Skill will be automatically loaded

---

## Debugging Tips

### Check Skill loading status
```python
from skills.loader import SkillLoader
loader = SkillLoader()
skills = loader.load_all()
print(f"Loaded {len(skills)} skills")
for s in skills:
    print(f"- {s.name}: {s.display_name}")
```

### Test intent matching
```python
from skills.loader import SkillLoader
from skills.matcher import IntentMatchingService

loader = SkillLoader()
skills = loader.load_all()
matcher = IntentMatchingService()

result = await matcher.match("Generate technical solution", skills)
if result:
    print(f"Matched: {result.skill.name} (score: {result.score})")
    print(f"Details: {result.details}")
```

### View match details
```bash
# View the matching process in the log
LOG_LEVEL=DEBUG python -m uvicorn src.main:app
```

---

## Notes

1. **Performance Considerations**:
   - SemanticMatcher will cache embedding, and the first loading will be slightly slower.
   - LLMRouter is only called when necessary to control API costs

2. **Threshold Tuning**:
   - Setting `threshold` too high may cause matching to fail
   - It is recommended to start from 0.7 and adjust according to the actual effect.

3. **Keyword Design**:
   - `required` should be precise and necessary
   - `optional` should cover multiple expressions
   - Avoid keywords that are too broad

---

## Future expansion directions

1. **Dynamic Skill Loading**: Hot update of Skill configuration during runtime
2. **Skill Combination**: Multiple Skills are executed in series
3. **User-defined Skill**: Create Skill through UI interface
4. **A/B Test**: Compare the effects of different Skill configurations

---

## Related file index

| Documentation | Description |
|------|------|
| `skills/models.py` | Data model definition |
| `skills/loader.py` | Markdown loader |
| `skills/matcher.py` | Intent matching service |
| `skills/executor.py` | Execution engine (coordination layer) |
| `src/services/chain/_api.py` | RAG integration points (`ask()`, `ask_stream()`, `_run_pipeline()`) |
| `src/services/chain/_skill_matching.py` | Skill loader/matcher singleton |
| `src/services/chain/_steps_generate.py` | `generate_answer()` — Skill-aware LLM call |
| `src/services/chain/_context.py` | `PipelineContext`, `PipelineResult` data structure |
| `src/services/llm/_prompts.py` | Prompt template, including `get_skill_prompt()` |
| `src/api/routers/skills.py` | HTTP API |
| `src/mcp.py` | MCP tool |
| `skills/builtin/tech_proposal/skill.md` | Sample Skill configuration (technical solution) |
| `skills/builtin/action_items/skill.md` | Action item extraction Skill |
| `skills/builtin/custom_notes/skill.md` | Custom notes Skill |
| `skills/builtin/meeting_minutes/skill.md` | Meeting minutes Skill |
| `skills/builtin/risk_register/skill.md` | Risk Registration Skill |
| `skills/builtin/stakeholder_update/skill.md` | Stakeholder update Skill |
