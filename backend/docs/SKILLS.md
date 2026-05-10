# Meeting Agent Skill 系统说明文档

## 概述

Skill系统是一个基于Markdown配置的可插拔功能扩展模块，它允许Meeting Agent根据用户的意图自动触发特定的内容格式化逻辑。

**核心设计理念：**
- **Prompt集成模式**：将Skill配置与RAG内容一起放入Prompt，由LLM直接生成结构化输出
- **Markdown配置**：Skill定义存储在`.md`文件中，便于版本控制和人工编辑
- **多层意图匹配**：结合关键词、语义相似度和LLM判断，精准识别用户意图

---

## 系统架构

```
用户输入
    ↓
意图匹配服务 (IntentMatchingService)
    ├── Layer 1: 关键词匹配 (KeywordMatcher) - 快速过滤
    ├── Layer 2: 语义相似度 (SemanticMatcher) - 向量计算
    └── Layer 3: LLM路由判断 (LLMRouter) - 复杂消歧
    ↓
匹配Skill?
    ├── 是 → RAG检索 → 【Skill配置+内容】→ Prompt → LLM生成 → 返回结构化输出
    └── 否 → 标准RAG → 返回普通回答
```

**Prompt集成模式优势**：
1. LLM同时看到格式要求和会议内容，生成更连贯的文档
2. LLM智能分配内容到对应章节，而非简单拼接
3. LLM可以明确指出某章节信息在会议中未提及
4. 单阶段生成，降低延迟

---

## 目录结构

下文 **`skills/`** 目录均相对于 **`backend/`** 仓库根（与 `src/` 并列的 Python 包 `skills`，以及 `backend/skills/builtin/` 内置 Markdown 技能）。

```
skills/
├── __init__.py              # 包导出
├── models.py                # 数据模型定义
├── loader.py                # Markdown文件加载器
├── matcher.py               # 意图匹配服务
├── executor.py              # 执行引擎（协调层）
├── builtin/                 # 内置Skill目录
│   ├── action_items/
│   │   └── skill.md
│   ├── custom_notes/
│   │   └── skill.md
│   ├── meeting_minutes/
│   │   └── skill.md
│   ├── risk_register/
│   │   └── skill.md
│   ├── stakeholder_update/
│   │   └── skill.md
│   └── tech_proposal/
│       └── skill.md
└── 说明文档.md              # 本文档
```

---

## 核心模块详解

### 1. models.py - 数据模型层

**作用**：定义Skill系统的所有数据结构，使用Pydantic进行验证。

**核心类**：

| 类名 | 作用 |
|------|------|
| `SkillDefinition` | Skill的完整定义，包含名称、描述、匹配规则、执行配置等 |
| `IntentMatchingConfig` | 意图匹配配置（方法、阈值、关键词、示例等） |
| `ExecutionConfig` | 执行配置（模式、超时等） |
| `OutputConfig` | 输出格式配置（章节、模板、后处理） |
| `SkillMatchResult` | 意图匹配结果 |
| `SkillExecutionResult` | Skill执行结果 |

**关键设计**：
- 所有配置均可从YAML Frontmatter反序列化
- 支持额外字段（`extra = "allow"`），便于扩展

---

### 2. loader.py - Skill加载器

**作用**：从文件系统加载Skill定义，解析Markdown文件中的YAML Frontmatter。

**核心类**：`SkillLoader`

**工作流程**：

```python
1. 遍历 skills/builtin/ 下的所有子目录
2. 查找 skill.md 文件
3. 解析文件内容：
   - 提取 --- 之间的YAML配置
   - 提取 --- 之后的Markdown文档
4. 构建 SkillDefinition 对象
5. 可选：加载同目录下的 template.j2 模板文件
```

**代码示例**：
```python
loader = SkillLoader("skills")
skills = loader.load_all()  # 加载所有Skill
skill = loader.get("tech_proposal_generator")  # 获取特定Skill
```

---

### 3. matcher.py - 意图匹配服务

**作用**：将用户输入与Skill进行匹配，返回最佳匹配的Skill。

**核心类**：

#### 3.1 KeywordMatcher（关键词匹配器）

**原理**：基于关键词的精确匹配

**匹配规则**：
- **必须关键词**（`required`）：必须全部出现，否则直接拒绝
- **可选关键词**（`optional`）：出现越多分数越高
- **排除关键词**（`excluded`）：出现则直接拒绝
- **正则表达式**（`patterns`）：复杂模式匹配

**分数计算**：
```
score = required_score * 0.4 + optional_score * 0.4 + regex_score * 0.2
```

#### 3.2 SemanticMatcher（语义匹配器）

**原理**：基于向量嵌入的语义相似度计算

**工作流程**：
1. **预计算**：加载Skill时，计算所有`examples`和`description`的embedding
2. **查询时**：计算用户输入的embedding
3. **相似度**：使用余弦相似度计算与所有示例的相似度
4. **聚合**：取Top-3相似度的平均值作为最终分数

**缓存机制**：
```python
self._cache: dict[str, np.ndarray]  # skill_name -> embeddings
```

#### 3.3 LLMRouter（LLM路由判断器）

**作用**：当多个Skill分数接近时，使用LLM做最终决策

**触发条件**：
- Top 2 Skill的分数差距 < 0.1
- Skill配置中启用了`llm_routing`

**提示词设计**：
```
你是一个Skill路由助手。选择最适合用户查询的Skill。

用户查询: "xxx"
候选Skill:
- Skill: name / Description / Examples
...

回复格式:
SKILL: <skill_name>
CONFIDENCE: <0.0-1.0>
REASONING: <简要说明>
```

#### 3.4 IntentMatchingService（匹配服务总入口）

**协调流程**：
```
对每个Skill:
    1. 关键词匹配 → keyword_score
    2. 语义匹配 → semantic_score
    3. 计算加权总分
    4. 低于1/2阈值则直接跳过

5.对所有候选Skill排序，获得best.score
6.如果Top 2差距 < 0.1:
    LM路由判断 → 调整best.score分数

7.当best.score大于阈值时，该best.score对应的skill作为最优匹配
```

---

### 4. executor.py - 执行引擎

**作用**：Skill执行的协调层。

**说明**：
实际Skill执行逻辑已直接集成到RAG流程中（见`chain.py`）。当Skill匹配时，其配置通过`generate_answer(skill_definition)`传递给LLM，由LLM直接生成结构化输出。

`SkillExecutor`类作为协调层保留，用于潜在的扩展场景。

---

### 5. chain/ 集成点

**修改位置**：`src/services/chain/_api.py`中的`ask()`函数和`_steps_generate.py`中的`generate_answer()`函数

**集成逻辑（Prompt集成模式）**：
```python
# _api.py
async def ask(question, ...):
    # 1. Skill 匹配以 asyncio.create_task() 并发启动
    skill_task = None
    if settings.SKILL_MATCHING_ENABLED:
        skill_task = asyncio.create_task(_do_skill_match(question))

    # 2. 创建Pipeline上下文
    ctx = PipelineContext(...)

    # 3. _run_pipeline 中消费 skill_task 结果（通常此时已完成）
    await _run_pipeline(ctx, skill_definition=None, skill_task=skill_task)
```

Skill 匹配与 RAG 检索管线**并行执行**，匹配结果在 `generate_answer` 前消费。双查询匹配：若 `rewritten_query` 与原查询不同，会对两个查询分别做匹配，取更高置信度结果。超短输入（≤2 个词）跳过 skill 匹配。

        return PipelineResult(
            answer=ctx.answer,
            sources=_extract_sources(ctx.docs),
            session_id=ctx.session_id,
            skill_used=match.skill.name,
            skill_confidence=match.score,
            ...
        )

    else:
        # 4. 无匹配，走标准RAG
        await _run_pipeline(ctx, None)
        return PipelineResult(...)
```

**关键实现细节**：

Skill匹配使用 `src/services/chain/_skill_matching.py` 模块中的单例：
```python
from ._skill_matching import get_skill_loader, get_skill_matcher
```
- `get_skill_loader()` 返回线程安全的 `SkillLoader` 单例
- `get_skill_matcher()` 返回线程安全的 `IntentMatchingService` 单例

在 `src/services/llm/_prompts.py` 中的 `get_skill_prompt()` 函数：
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
            sections_desc.append(f"{i}. **{title}**{req}\n   {desc}")

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

    return get_rag_prompt()  # Fallback to standard RAG prompt
```

在 `_steps_generate.py` 的 `generate_answer()` 中：
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

**`ask()` 完整签名**：
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

**`PipelineContext` 关键字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `file_ids` | `list[int] \| None` | 限定检索的文件ID |
| `web_search_results` | `int \| None` | Web搜索结果数量 |
| `rag_mode` | `str \| None` | RAG模式（如 `hybrid`） |
| `settings_epoch` | `int` | 设置快照的epoch版本 |
| `settings_snapshot` | `SettingsSnapshot \| None` | 请求时的配置快照 |
| `past_session_refs` | `list[dict]` | 历史会话引用 |
| `trace` | `TraceContext` | 结构化追踪上下文 |

**`PipelineResult` 完整字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `answer` | `str` | LLM生成的回答 |
| `sources` | `list[dict]` | 引用来源 |
| `session_id` | `str` | 会话ID |
| `web_results` | `list[dict] \| None` | Web搜索结果 |
| `past_sessions` | `list[dict] \| None` | 相关历史会话 |
| `extraction_failed` | `bool` | 后台事实提取是否失败 |
| `trace` | `dict \| None` | 序列化的追踪数据 |
| `skill_used` | `str \| None` | 使用的Skill名称 |
| `skill_confidence` | `float \| None` | 匹配置信度 |

---

## Skill配置文件详解

### 文件位置
`skills/builtin/{skill_name}/skill.md`

### 文件格式
```markdown
---
# YAML Frontmatter - Skill配置
name: skill_identifier          # 唯一标识符（英文）
display_name: "显示名称"         # 中文显示名
description: "详细描述"

intent_matching:
  method: hybrid               # 匹配方法
  threshold: 0.7               # 触发阈值
  keywords:
    required: ["必须关键词"]
    optional: ["可选关键词"]
  examples:
    - "示例查询1"
    - "示例查询2"
  llm_routing:
    enabled: true

execution:
  mode: post_rag               # 执行模式
  timeout: 120

output:
  format: markdown
  sections:
    - title: "章节标题"
      required: true
  post_process:
    - add_header_footer
    - generate_toc
---

# Markdown内容 - Skill文档说明

## 功能说明
...
```

---

## API接口

### HTTP端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/skills` | 列出所有Skill |
| POST | `/api/v1/skills` | 创建新Skill |
| POST | `/api/v1/skills/invoke` | 手动调用指定Skill |
| POST | `/api/v1/skills/match` | 测试意图匹配（调试用，JSON body）|

### MCP工具

| 工具名 | 说明 |
|--------|------|
| `list_skills` | 列出可用Skill |
| `invoke_skill` | 调用指定Skill |

---

## 使用示例

### 示例1：触发Tech Proposal Skill

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "question": "请帮我生成科技部的技术方案"
  }'
```

**预期返回**：
```json
{
  "answer": "# 一、项目背景与意义\n...",
  "sources": [...],
  "skill_used": "tech_proposal_generator",
  "skill_confidence": 0.85
}
```

### 示例2：手动调用Skill

```bash
curl -X POST http://localhost:8000/api/v1/skills/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "skill_name": "tech_proposal_generator",
    "query": "AI项目技术方案"
  }'
```

### 示例3：测试意图匹配

```bash
curl -X POST http://localhost:8000/api/v1/skills/match \
  -H "Content-Type: application/json" \
  -d '{"query": "生成技术方案"}'
```

---

## 扩展开发指南

### 创建新Skill的步骤

1. **创建目录**：`skills/builtin/my_skill/`

2. **编写skill.md**：
```markdown
---
name: my_skill
display_name: "我的Skill"
description: "描述"
intent_matching:
  method: hybrid
  keywords:
    required: ["关键词"]
  examples:
    - "示例查询"
---

## 功能说明
...
```

3. **（可选）添加额外的配置**：
   - 在当前Prompt集成模式下，`output.template_file` 和 `output.post_process` 字段不被使用
   - 这些字段保留用于未来可能的扩展

4. **重启服务**，新Skill会自动加载

---

## 调试技巧

### 查看Skill加载情况
```python
from skills.loader import SkillLoader
loader = SkillLoader()
skills = loader.load_all()
print(f"Loaded {len(skills)} skills")
for s in skills:
    print(f"- {s.name}: {s.display_name}")
```

### 测试意图匹配
```python
from skills.loader import SkillLoader
from skills.matcher import IntentMatchingService

loader = SkillLoader()
skills = loader.load_all()
matcher = IntentMatchingService()

result = await matcher.match("生成技术方案", skills)
if result:
    print(f"Matched: {result.skill.name} (score: {result.score})")
    print(f"Details: {result.details}")
```

### 查看匹配细节
```bash
# 在日志中查看匹配过程
LOG_LEVEL=DEBUG python -m uvicorn src.main:app
```

---

## 注意事项

1. **性能考虑**：
   - SemanticMatcher会缓存embedding，首次加载稍慢
   - LLMRouter只在必要时调用，控制API成本

2. **阈值调优**：
   - `threshold`设置过高可能导致匹配失败
   - 建议从0.7开始，根据实际效果调整

3. **关键词设计**：
   - `required`应精确且必要
   - `optional`应覆盖多种表达方式
   - 避免过于宽泛的关键词

---

## 未来扩展方向

1. **动态Skill加载**：运行时热更新Skill配置
2. **Skill组合**：多个Skill串联执行
3. **用户自定义Skill**：通过UI界面创建Skill
4. **A/B测试**：对比不同Skill配置的效果

---

## 相关文件索引

| 文件 | 说明 |
|------|------|
| `skills/models.py` | 数据模型定义 |
| `skills/loader.py` | Markdown加载器 |
| `skills/matcher.py` | 意图匹配服务 |
| `skills/executor.py` | 执行引擎（协调层） |
| `src/services/chain/_api.py` | RAG集成点（`ask()`、`ask_stream()`、`_run_pipeline()`） |
| `src/services/chain/_skill_matching.py` | Skill加载器/匹配器单例 |
| `src/services/chain/_steps_generate.py` | `generate_answer()` — Skill-aware LLM调用 |
| `src/services/chain/_context.py` | `PipelineContext`、`PipelineResult` 数据结构 |
| `src/services/llm/_prompts.py` | Prompt模板，包含 `get_skill_prompt()` |
| `src/api/routers/skills.py` | HTTP API |
| `src/mcp.py` | MCP工具 |
| `skills/builtin/tech_proposal/skill.md` | 示例Skill配置（技术方案） |
| `skills/builtin/action_items/skill.md` | 行动项提取 Skill |
| `skills/builtin/custom_notes/skill.md` | 自定义笔记 Skill |
| `skills/builtin/meeting_minutes/skill.md` | 会议纪要 Skill |
| `skills/builtin/risk_register/skill.md` | 风险登记 Skill |
| `skills/builtin/stakeholder_update/skill.md` | 干系人更新 Skill |
