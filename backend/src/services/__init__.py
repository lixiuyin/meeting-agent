"""Service layer - heavy dependencies are loaded on first use via singletons.

Access services through their respective modules:
    from src.services.llm import get_llm
    from src.services.rag import retrieve, index_meeting
    from src.services.chain import ask
    from src.services.memory import memory_service
"""
