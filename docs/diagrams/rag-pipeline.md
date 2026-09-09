# RAG Pipeline Architecture

**Verified against implementation:** 2026-09-09. This diagram separates
request-scoped orchestration from durable post-response extraction and keeps
web fallback after local retrieval confidence is known.

## Query Path

```mermaid
flowchart TD
    Start([POST /api/v1/chat or /chat/stream]) --> Req[ChatRequest<br/>question · session_id · meeting_ids<br/>top_k · rag_mode · use_web_search]
    Req --> Ask["ask / ask_stream<br/>chain/_api.py"]
    Ask --> Ctx["Create PipelineContext<br/>chain/_context.py"]
    Ctx --> Classify{"_classify_intent<br/>_routing.py"}

    Classify -->|greeting / smalltalk| Casual["_casual_response"]
    Casual --> Save1["save_messages"]
    Save1 --> Ret1(["Return PipelineResult"])

    Classify -->|question / retrieval| Skill{"Skill matching<br/>_skill_matching.py"}
    Skill -->|matched| SkillDef["Load Skill definition + prompt"]
    Skill -->|no match| Session

    SkillDef --> Session

    subgraph SessionSetup["Session setup"]
        Session["ensure_session<br/>_steps_session.py<br/>create/validate in SQLite"]
    end

    Session --> Rewrite["rewrite_query_step<br/>_resolver.py (multi-turn)<br/>_query.py (single-turn)<br/>+ adaptive top_k"]

    Rewrite --> Prewarm["_prewarm_query_embedding<br/>populate LRU cache for<br/>parallel branches"]

    Prewarm --> Parallel{"Parallel branches"}

    subgraph RetrievalBranch["Retrieval branch"]
        direction TB
        P1["retrieve_documents<br/>_steps_retrieve.py"]
        P1 --> Scope{"file_ids<br/>specified?"}

        Scope -->|no: broad recall| Broad["_retrieve_broad_recall<br/>_retrieve_broad.py"]
        Scope -->|yes: file-scoped| Scoped["_retrieve_scoped<br/>_retrieve_broad.py"]

        Broad --> MRouter{"Meeting-summary<br/>router enabled?"}
        MRouter -->|yes| MRoute["route_meetings_by_summary<br/>_meeting_summary_vectorstore.py"]
        MRouter -->|no| FileScope
        MRoute --> Prior["soft meeting priors<br/>reserve global exploration quota"]
        Prior --> FileScope

        FileScope["File-scoping strategy<br/>_scoping_strategies.py"]
        FileScope --> Funnel["Funnel: summary router +<br/>wide fetch → RRF merge<br/>_summary_router.py + _funnel_narrow.py"]
        Funnel --> Fair["fair_retrieve_per_file<br/>_fair_retriever.py<br/>per-file budget + concurrency"]
        Fair --> Filters

        Scoped --> Retrieve
        Fair --> Retrieve

        Retrieve["retrieve<br/>_retriever.py"]
        Retrieve --> Strategy{"Select strategy<br/>_strategies.py"}

        Strategy -->|vector| Vec["_vector_retrieve<br/>Chroma similarity search"]
        Strategy -->|hybrid| Hybrid["_hybrid_retrieve<br/>vector + BM25 → RRF merge"]
        Strategy -->|multimodal| MM["_run_multimodal_strategy<br/>RAGAnything<br/>↓ fallback to vector"]
        Strategy -->|hybrid_multimodal| HMM["vector + RAGAnything<br/>RRF merge"]

        Vec --> Filters
        Hybrid --> Filters
        MM --> Filters
        HMM --> Filters

        Filters["Post-retrieval filters<br/>_retrieve_filters.py<br/>speaker · temporal · content-type bias"]
        Filters --> PreDedup["pre_rerank_dedup<br/>_retrieve_post.py"]
        PreDedup --> Rerank

        Rerank["rerank_documents<br/>_retrieve_post.py<br/>Cohere / BGE cross-encoder"]
        Rerank --> Dedup["suppress_near_duplicates<br/>4-gram overlap ≥ 0.85 filtered"]
        Dedup --> Docs["ctx.docs"]
    end

    subgraph ContextBranches["Context branches"]
        direction TB
        P2["load_memories<br/>_steps_context.py<br/>MemoryService<br/>profile + semantic search"]
        P3["load_session_context<br/>_steps_context.py<br/>SessionSummaryService<br/>past session summaries"]
        P4["load_entity_context<br/>_steps_context.py<br/>KnowledgeGraphService<br/>entity relationships"]
        P5["load_history<br/>_steps_context.py<br/>SQLiteChatMessageHistory<br/>summarize if oversized"]
    end

    Parallel --> P1
    Parallel --> P2
    Parallel --> P3
    Parallel --> P4
    Parallel --> P5

    P2 --> LocalReady
    P3 --> LocalReady
    P4 --> LocalReady
    P5 --> LocalReady{"local evidence ready"}
    Docs --> LocalReady
    LocalReady --> WebMode{"web mode"}
    WebMode -->|off| Build
    WebMode -->|always| Web["perform_web_search<br/>DuckDuckGo / SerpAPI / Tavily"]
    WebMode -->|fallback + low local confidence| Web
    WebMode -->|fallback + sufficient confidence| Build
    Web --> Build

    subgraph ContextGeneration["Context assembly and generation"]
        Build["build_context<br/>_steps_generate.py"]
        Build --> Truncate["Truncate each source to token budget"]
        Truncate --> Format["_format_docs → numbered citations<br/>_build_system_context → tagged sections"]
        Format --> Budget["Enforce total token budget<br/>drop lowest-ranked docs if over"]
        Budget --> Combined["ctx.combined_context"]

        Combined --> Generate["generate_answer<br/>LCEL: prompt → llm → StrOutputParser<br/>optional fast-path latency guard"]
        Generate --> CacheCheck{"Anthropic<br/>prompt caching?"}
        CacheCheck -->|yes| Cached["Apply cache_control<br/>to system message<br/>_anthropic_cache.py"]
        CacheCheck -->|no| MMCheck
        Cached --> MMCheck
        MMCheck{"Docs contain<br/>images?"}
        MMCheck -->|yes| B64["Inject base64 images<br/>into HumanMessage"]
        MMCheck -->|no| Invoke
        B64 --> Invoke["_invoke_chain_with_retry<br/>_generate_helpers.py<br/>traffic controller + circuit breaker"]
        Invoke --> Strip["Strip thinking blocks<br/>→ ctx.answer"]
    end

    Strip --> Save2["save_messages<br/>HumanMessage + AIMessage<br/>→ SQLite chat_messages"]
    Save2 --> Extract["schedule_fact_extraction<br/>commit durable fact_extraction job"]

    Extract --> Durable["durable_jobs<br/>lease · retry · source/revision fences"]
    Extract --> Ret2

    Durable --> ComboCheck{"Combined<br/>extraction?"}
    ComboCheck -->|yes| ComboExtract["combined_extract<br/>single LLM call: facts + entities"]
    ComboCheck -->|no| SepExtract["separate calls"]
    SepExtract --> FactExtract["memory_service.auto_extract_facts<br/>→ key-value memories"]
    SepExtract --> EntityExtract["kg_service.extract_entities<br/>→ knowledge graph entities"]
    ComboExtract --> Ledger["revisioned memory + entity writes"]
    FactExtract --> Ledger
    EntityExtract --> Ledger

    Ret2(["Return PipelineResult<br/>answer · sources · session_id<br/>web_results · trace · skill_used"])

    style Start fill:#4CAF50,color:#fff
    style Ret1 fill:#2196F3,color:#fff
    style Ret2 fill:#2196F3,color:#fff
    style Parallel fill:#FF9800,color:#fff
    style Strategy fill:#9C27B0,color:#fff
    style Classify fill:#FF9800,color:#fff
    style Skill fill:#FF9800,color:#fff
    style Scope fill:#9C27B0,color:#fff
    style CacheCheck fill:#FF9800,color:#fff
    style MMCheck fill:#FF9800,color:#fff
    style ComboCheck fill:#FF9800,color:#fff
```

## Indexing Path

```mermaid
flowchart TD
    Upload([Uploaded File]) --> Type{File Type?}

    Type -->|video / audio| Transcribe[Transcribe<br/>AssemblyAI ASR<br/>speaker diarization]
    Type -->|PDF / Office / image| Convert{Conversion<br/>applicable?}
    Type -->|text / CSV / Markdown<br/>HTML / JSON / XML / RTF| TextParse[text_parsers.py<br/>local text extraction]

    Convert -->|yes| Libre[converters.py<br/>LibreOffice conversion]
    Convert -->|no| Parse
    Libre --> Parse

    Parse[Parse<br/>parser/cascade.py<br/>profile → ordered cloud providers]
    Parse --> QA{Quality Check<br/>_quality.py}

    Transcribe --> Segments[Segments with<br/>timestamps + speakers]
    QA -->|provider passes| Parsed[ParsedDocument<br/>pages: text · tables · images]
    QA -->|all fail, original PDF| PDFLocal[PyMuPDF text-only fallback]
    PDFLocal --> Parsed
    QA -->|all fail, other routed format| ParseError[AllParsersFailedError<br/>file status → error]
    TextParse --> ParsedText[ParsedDocument<br/>plain text content]

    Segments --> IndexSeg[index_meeting_segments<br/>_indexer.py<br/>group consecutive segments]
    Parsed --> IndexPages[index_meeting_pages<br/>_indexer.py]
    ParsedText --> IndexText[index_meeting<br/>_indexer.py]

    IndexSeg --> Chunk
    IndexPages --> Chunk
    IndexText --> Chunk

    subgraph Chunking & Indexing
        Chunk[Chunking Strategy]
        Chunk --> Flat{Chunking Mode?}
        Flat -->|flat| Recursive[RecursiveCharacterTextSplitter<br/>or _split_by_structure]
        Flat -->|parent-child| TwoLevel[Two-level splitter<br/>parent: context / child: retrieval]

        Recursive --> TextChunks[Text chunks]
        Recursive --> TableChunks[Table chunks → markdown]
        Recursive --> ImageChunks[Image chunks<br/>caption / OCR / combined]
        TwoLevel --> TextChunks

        TextChunks --> Dedup
        TableChunks --> Dedup
        ImageChunks --> Dedup

        Dedup[Deduplicate<br/>content hash vs existing]
        Dedup --> Embed[Embed<br/>get_embeddings<br/>OpenAI / Ollama / HF / ...]
        Dedup --> Bm25[Add to FTS5 BM25 index<br/>if hybrid enabled]
        Embed --> Upsert[Upsert to Chroma<br/>meetings collection]
    end

    Upsert --> RA{RAGAnything<br/>enabled?}
    Bm25 --> RA

    RA -->|yes| RAIndex[index_with_raganything<br/>scope preambles for filtering]
    RA -->|no| Summary
    RAIndex --> Summary

    Summary[Generate per-file summary<br/>_per_file_summary.py<br/>embed into summary collection]

    Summary --> Done

    Done([Indexed & Searchable])

    style Upload fill:#4CAF50,color:#fff
    style Done fill:#2196F3,color:#fff
    style Type fill:#FF9800,color:#fff
    style Flat fill:#FF9800,color:#fff
    style RA fill:#FF9800,color:#fff
    style Convert fill:#9C27B0,color:#fff
```

## Memory Layer

```mermaid
flowchart LR
    subgraph Short-term
        SH[Session History<br/>memory/_history.py<br/>SQLite chat_messages<br/>TTL cache 30min<br/>auto-summarize if oversized]
    end

    subgraph Long-term
        LM[User Memories<br/>memory/_service/<br/>_crud.py · _search.py · _extraction.py<br/>Chroma: user_memories<br/>semantic · freshness · salience<br/>confidence · usefulness]
    end

    subgraph Episodic
        ES[Session Summaries<br/>memory/_summary_service.py<br/>memory/_summary_vectorstore.py<br/>Chroma: session_summaries<br/>LLM-generated per session]
    end

    subgraph Knowledge Graph
        KG[Entities & Relations<br/>knowledge_graph/<br/>_service.py · _storage.py · _vectorstore.py<br/>SQLite: memory_entities<br/>memory_relations]
    end

    SH --> |load_history| Ctx[PipelineContext]
    LM --> |load_memories| Ctx
    ES --> |load_session_context| Ctx
    KG --> |load_entity_context| Ctx

    Ctx --> |enqueue fact_extraction<br/>durable_jobs| LM
    Ctx --> |enqueue fact_extraction<br/>durable_jobs| KG
```
