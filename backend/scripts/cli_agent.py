"""
Interactive CLI Agent for Meeting Agent

A terminal-based interface to interact with the Meeting Agent RAG system
and diagnose MCP server connectivity.

Usage:
    cd backend
    python -m scripts.cli_agent

Commands:
    /help           - Show available commands
    /status         - Check system component status
    /meetings       - List uploaded meetings
    /search         - Search meeting content
    /skills         - List available skills
    /skill_invoke   - Manually invoke a skill
    /skill_match    - Test intent matching
    /mcp            - Test MCP tool calls
    /memory         - Manage user memories
    /clear          - Clear conversation history
    /quit           - Exit the CLI

Examples:
    > What was discussed in the last meeting?
    > /search project timeline
    > /skills
    > /skill_invoke tech_proposal_generator
"""

import asyncio
import hashlib
import json
import os
import shlex
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add parent directory to Python path to allow importing from src
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

console = Console()


class MeetingAgentCLI:
    """Interactive CLI for Meeting Agent with diagnostic capabilities."""

    def __init__(self):
        self.session_id: str | None = None
        self.user_id = "cli_user"
        self.conversation_history: list[dict] = []
        self.running = True
        self._background_tasks: list[asyncio.Task] = []

    def _track_background_task(self, task: asyncio.Task) -> None:
        self._background_tasks.append(task)

        def _cleanup(done_task: asyncio.Task) -> None:
            if done_task in self._background_tasks:
                self._background_tasks.remove(done_task)

        task.add_done_callback(_cleanup)

    def print_banner(self):
        """Display the welcome banner."""
        banner = """
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   ███╗   ███╗███████╗███████╗████████╗██╗███╗   ██╗ ██████╗ │
│   ████╗ ████║██╔════╝██╔════╝╚══██╔══╝██║████╗  ██║██╔════╝ │
│   ██╔████╔██║█████╗  █████╗     ██║   ██║██╔██╗ ██║██║  ███╗│
│   ██║╚██╔╝██║██╔══╝  ██╔══╝     ██║   ██║██║╚██╗██║██║   ██║│
│   ██║ ╚═╝ ██║███████╗███████╗   ██║   ██║██║ ╚████║╚██████╔╝│
│   ╚═╝     ╚═╝╚══════╝╚══════╝   ╚═╝   ╚═╝╚═╝  ╚═══╝ ╚═════╝ │
│                                                             │
│              Interactive CLI Agent v1.0                     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
        """
        console.print(Panel(banner, border_style="blue", padding=(0, 2)))
        console.print("\n[cyan]Type /help for available commands or ask a question directly.[/cyan]\n")

    async def check_database(self) -> tuple[bool, str]:
        """Check database connectivity and return status with details."""
        try:
            from src.core.database import get_connection, init_db

            init_db()
            with get_connection() as conn:
                meeting_count = conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
                session_count = conn.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0]
                memory_count = conn.execute("SELECT COUNT(*) FROM user_memories").fetchone()[0]

            details = f"{meeting_count} meetings, {session_count} sessions, {memory_count} memories"
            return True, details
        except Exception as e:
            return False, str(e)

    async def check_vector_store(self) -> tuple[bool, str]:
        """Check Chroma vector store connectivity."""
        try:
            from src.services.rag import get_vectorstore

            vectorstore = get_vectorstore()
            count = vectorstore._collection.count()
            return True, f"{count} documents indexed"
        except Exception as e:
            return False, str(e)

    async def check_llm(self) -> tuple[bool, str]:
        """Check LLM service connectivity."""
        try:
            from src.core.config import settings
            from src.services.llm import get_llm

            llm = get_llm()
            # Quick test invocation
            response = llm.invoke("Hi")
            return True, f"{settings.LLM_BINDING} ({settings.LLM_MODEL})"
        except Exception as e:
            return False, str(e)

    async def check_embedding(self) -> tuple[bool, str]:
        """Check embedding service connectivity."""
        try:
            from src.core.config import settings
            from src.services.embedder import get_embeddings

            embedder = get_embeddings()
            result = embedder.embed_query("test")
            return True, f"{settings.EMBEDDING_BINDING} (dim: {len(result)})"
        except Exception as e:
            return False, str(e)

    async def check_skills(self) -> tuple[bool, str]:
        """Check skill system status."""
        try:
            from skills.loader import SkillLoader

            loader = SkillLoader()
            skills = loader.load_all()
            return True, f"{len(skills)} skills loaded"
        except Exception as e:
            return False, str(e)

    async def check_mcp_tools(self) -> dict[str, tuple[bool, str]]:
        """Test all MCP tools and return results."""
        results = {}

        # Test list_skills
        try:
            from src.mcp import list_skills

            result = list_skills()
            results["list_skills"] = (True, "OK")
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            results["list_skills"] = (False, error_msg)

        # Test list_meetings
        try:
            from src.mcp import list_meetings

            result = list_meetings(limit=1)
            results["list_meetings"] = (True, "OK")
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            results["list_meetings"] = (False, error_msg)

        # Test manage_memory
        try:
            from src.mcp import manage_memory

            manage_memory("set", key="_cli_test", value="test", user_id=self.user_id)
            manage_memory("delete", key="_cli_test", user_id=self.user_id)
            results["manage_memory"] = (True, "OK")
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            results["manage_memory"] = (False, error_msg)

        # Test search_meetings
        try:
            from src.mcp import search_meetings

            result = search_meetings("test", top_k=1)
            results["search_meetings"] = (True, "OK")
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            results["search_meetings"] = (False, error_msg)

        # Test ask_about_meetings
        try:
            from src.mcp import ask_about_meetings

            result = await ask_about_meetings("Hello", user_id=self.user_id)
            results["ask_about_meetings"] = (True, "OK")
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            results["ask_about_meetings"] = (False, error_msg)

        return results

    async def display_status(self):
        """Display comprehensive system status."""
        console.print("\n[bold cyan]System Diagnostic Report[/bold cyan]\n")

        # Create status table
        table = Table(box=box.ROUNDED, show_header=True)
        table.add_column("Component", style="cyan", width=20)
        table.add_column("Status", style="bold", width=10)
        table.add_column("Details", style="white")

        components = [
            ("Database", self.check_database()),
            ("Vector Store", self.check_vector_store()),
            ("LLM Service", self.check_llm()),
            ("Embedding", self.check_embedding()),
            ("Skills", self.check_skills()),
        ]

        for name, coro in components:
            with console.status(f"[yellow]Checking {name}...[/yellow]"):
                success, details = await coro

            if success:
                table.add_row(name, "[green]✓ OK[/green]", details)
            else:
                table.add_row(name, "[red]✗ FAIL[/red]", f"[red]{details}[/red]")

        console.print(table)

        # Check MCP tools
        console.print("\n[bold cyan]MCP Server Tools[/bold cyan]\n")

        mcp_table = Table(box=box.ROUNDED, show_header=True)
        mcp_table.add_column("Tool", style="cyan", width=25)
        mcp_table.add_column("Status", style="bold", width=10)
        mcp_table.add_column("Details", style="white")

        with console.status("[yellow]Testing MCP tools...[/yellow]"):
            mcp_results = await self.check_mcp_tools()

        for tool_name, (success, details) in mcp_results.items():
            if success:
                mcp_table.add_row(tool_name, "[green]✓ OK[/green]", details)
            else:
                mcp_table.add_row(tool_name, "[red]✗ FAIL[/red]", f"[red]{details}[/red]")

        console.print(mcp_table)
        console.print()

    async def list_meetings(self, limit: int = 20, offset: int = 0):
        """Display list of meetings."""
        try:
            from src.core import database as db

            def _fetch():
                with db.get_connection() as conn:
                    meetings = db.list_meetings(conn, limit=limit + 1, offset=offset)
                    rows = conn.execute(
                        "SELECT meeting_id, COUNT(*) AS cnt FROM meeting_files GROUP BY meeting_id"
                    ).fetchall()
                counts = {row["meeting_id"]: row["cnt"] for row in rows}
                return meetings, counts

            meetings, file_counts = await asyncio.to_thread(_fetch)

            if not meetings:
                console.print("[yellow]No meetings found. Upload some files first![/yellow]\n")
                return
            has_more = len(meetings) > limit
            page = meetings[:limit]

            table = Table(box=box.ROUNDED, show_header=True, title="Uploaded Meetings")
            table.add_column("ID", style="cyan", width=6)
            table.add_column("Title", style="white", width=30)
            table.add_column("Files", style="blue", width=8)
            table.add_column("Status", style="bold", width=12)
            table.add_column("Created", style="dim", width=20)

            for m in page:
                status_color = {
                    "ready": "green",
                    "processing": "yellow",
                    "failed": "red",
                    "uploading": "blue",
                }.get(m["status"], "white")
                file_count = file_counts.get(m["id"], 0)

                table.add_row(
                    str(m["id"]),
                    m["title"][:30],
                    str(file_count),
                    f"[{status_color}]{m['status']}[/{status_color}]",
                    m["created_at"],
                )

            console.print(table)
            if has_more:
                console.print(
                    f"[dim]More available. Try: /meetings --limit {limit} --offset {offset + limit}[/dim]"
                )
            console.print()

        except Exception as e:
            console.print(f"[red]Error listing meetings: {e}[/red]\n")

    @staticmethod
    def _split_args_options(
        tokens: list[str], value_options: set[str], flag_options: set[str]
    ) -> tuple[list[str], dict[str, Any]]:
        positional: list[str] = []
        options: dict[str, Any] = {}
        idx = 0
        while idx < len(tokens):
            token = tokens[idx]
            if token in value_options:
                if idx + 1 >= len(tokens):
                    raise ValueError(f"Missing value for option: {token}")
                options[token] = tokens[idx + 1]
                idx += 2
                continue
            if token in flag_options:
                options[token] = True
                idx += 1
                continue
            positional.append(token)
            idx += 1
        return positional, options

    @staticmethod
    def _parse_int_csv(raw: str | None) -> list[int] | None:
        if not raw:
            return None
        try:
            values = [int(part.strip()) for part in raw.split(",") if part.strip()]
        except ValueError as exc:
            raise ValueError("Expected comma-separated integers") from exc
        return values or None

    @staticmethod
    def _parse_int_option(value: str | None, default: int) -> int:
        if value is None:
            return default
        parsed = int(value)
        return parsed if parsed > 0 else default

    @staticmethod
    def _parse_non_negative_option(value: str | None, default: int) -> int:
        if value is None:
            return default
        parsed = int(value)
        return parsed if parsed >= 0 else default

    @staticmethod
    def _prompt_int(prompt: str, default: int | None = None) -> int:
        while True:
            raw = Prompt.ask(prompt, default=str(default) if default is not None else None).strip()
            try:
                return int(raw)
            except ValueError:
                console.print("[yellow]Please enter an integer.[/yellow]")

    async def show_meeting_detail(self, meeting_id: int):
        try:
            from src.core import database as db

            def _fetch():
                with db.get_connection() as conn:
                    meeting = db.get_meeting(conn, meeting_id)
                    files = db.list_meeting_files(conn, meeting_id)
                return meeting, files

            meeting, files = await asyncio.to_thread(_fetch)
            if not meeting:
                console.print(f"[red]Meeting {meeting_id} not found.[/red]\n")
                return

            details = [
                f"[bold]Title:[/bold] {meeting['title']}",
                f"[bold]Status:[/bold] {meeting['status']}",
                f"[bold]Description:[/bold] {meeting.get('description') or '-'}",
                f"[bold]Date:[/bold] {meeting.get('meeting_date') or '-'}",
                f"[bold]Created:[/bold] {meeting.get('created_at') or '-'}",
                f"[bold]Files:[/bold] {len(files)}",
            ]
            console.print(Panel("\n".join(details), title=f"Meeting #{meeting_id}", border_style="cyan"))

            if files:
                table = Table(box=box.ROUNDED, show_header=True, title="Meeting Files")
                table.add_column("File ID", style="cyan", width=8)
                table.add_column("Name", style="white", width=35)
                table.add_column("Type", style="blue", width=8)
                table.add_column("Status", style="bold", width=12)
                for f in files:
                    table.add_row(str(f["id"]), f["file_name"][:35], f["file_type"], f["status"])
                console.print(table)
            console.print()
        except Exception as e:
            console.print(f"[red]Error showing meeting detail: {e}[/red]\n")

    async def list_meeting_files(self, meeting_id: int, limit: int = 20, offset: int = 0):
        try:
            from src.core import database as db

            def _fetch():
                with db.get_connection() as conn:
                    meeting = db.get_meeting(conn, meeting_id)
                    files = db.list_meeting_files(conn, meeting_id)
                return meeting, files

            meeting, files = await asyncio.to_thread(_fetch)
            if not meeting:
                console.print(f"[red]Meeting {meeting_id} not found.[/red]\n")
                return
            if not files:
                console.print(f"[yellow]Meeting {meeting_id} has no files.[/yellow]\n")
                return

            has_more = len(files) > offset + limit
            page = files[offset: offset + limit]

            table = Table(box=box.ROUNDED, show_header=True, title=f"Files in Meeting #{meeting_id}")
            table.add_column("File ID", style="cyan", width=8)
            table.add_column("Name", style="white", width=35)
            table.add_column("Type", style="blue", width=8)
            table.add_column("Status", style="bold", width=12)
            table.add_column("Created", style="dim", width=20)
            for f in page:
                table.add_row(
                    str(f["id"]),
                    f["file_name"][:35],
                    f["file_type"],
                    f["status"],
                    f.get("created_at", "-"),
                )
            console.print(table)
            if has_more:
                console.print(
                    f"[dim]More available. Try: /files {meeting_id} --limit {limit} --offset {offset + limit}[/dim]"
                )
            console.print()
        except Exception as e:
            console.print(f"[red]Error listing files: {e}[/red]\n")

    async def list_sessions_cli(self, limit: int = 20, offset: int = 0):
        try:
            from src.core import database as db

            def _fetch():
                with db.get_connection() as conn:
                    return db.list_sessions(conn, user_id=self.user_id, limit=limit + 1, offset=offset)

            sessions = await asyncio.to_thread(_fetch)
            if not sessions:
                console.print("[yellow]No chat sessions found.[/yellow]\n")
                return

            has_more = len(sessions) > limit
            page = sessions[:limit]
            table = Table(box=box.ROUNDED, show_header=True, title=f"Sessions ({self.user_id})")
            table.add_column("Session ID", style="cyan", width=20)
            table.add_column("Title", style="white", width=25)
            table.add_column("Updated", style="dim", width=20)
            table.add_column("Access", style="blue", width=8)
            for s in page:
                table.add_row(
                    s["id"][:20],
                    (s.get("title") or "-")[:25],
                    s.get("updated_at", "-"),
                    str(s.get("access_count", 0)),
                )
            console.print(table)
            if has_more:
                console.print(
                    f"[dim]More available. Try: /sessions --limit {limit} --offset {offset + limit}[/dim]"
                )
            console.print()
        except Exception as e:
            console.print(f"[red]Error listing sessions: {e}[/red]\n")

    async def show_session_messages(self, session_id: str):
        try:
            from src.core import database as db

            def _fetch():
                with db.get_connection() as conn:
                    session = db.get_session(conn, session_id)
                    messages = db.get_messages(conn, session_id)
                return session, messages

            session, messages = await asyncio.to_thread(_fetch)
            if not session:
                console.print(f"[red]Session {session_id} not found.[/red]\n")
                return
            if not messages:
                console.print("[yellow]No messages in this session.[/yellow]\n")
                return

            console.print(Panel(f"Session: {session_id}", border_style="cyan"))
            for msg in messages[-30:]:
                role = msg["role"]
                style = "green" if role == "agent" else "blue"
                console.print(f"[{style}]{role}[/{style}]: {msg['content']}")
            console.print()
        except Exception as e:
            console.print(f"[red]Error showing session messages: {e}[/red]\n")

    async def use_session(self, session_id: str):
        try:
            from src.core import database as db

            def _fetch():
                with db.get_connection() as conn:
                    return db.get_session(conn, session_id)

            session = await asyncio.to_thread(_fetch)
            if not session:
                console.print(f"[red]Session {session_id} not found.[/red]\n")
                return
            self.session_id = session_id
            console.print(f"[green]Using session: {session_id}[/green]\n")
        except Exception as e:
            console.print(f"[red]Error setting session: {e}[/red]\n")

    async def delete_session_cli(self, session_id: str):
        try:
            from src.core import database as db

            def _delete():
                with db.get_write_connection() as conn:
                    if not db.get_session(conn, session_id):
                        return False
                    db.delete_session(conn, session_id)
                    return True

            deleted = await asyncio.to_thread(_delete)
            if not deleted:
                console.print(f"[red]Session {session_id} not found.[/red]\n")
                return
            if self.session_id == session_id:
                self.session_id = None
            console.print(f"[green]Session deleted: {session_id}[/green]\n")
        except Exception as e:
            console.print(f"[red]Error deleting session: {e}[/red]\n")

    async def retrieve_only(self, query: str, meeting_ids: list[int] | None = None, top_k: int = 5):
        try:
            from src.services.rag import retrieve

            with console.status("[yellow]Retrieving relevant chunks...[/yellow]"):
                docs = await asyncio.to_thread(retrieve, query, meeting_ids=meeting_ids, top_k=top_k)

            if not docs:
                console.print("[yellow]No relevant chunks found.[/yellow]\n")
                return

            for idx, doc in enumerate(docs, start=1):
                meta = doc.get("metadata", {})
                title = meta.get("title", f"meeting#{meta.get('meeting_id', '?')}")
                score = doc.get("score", 0.0)
                snippet = doc.get("content", "")[:400]
                console.print(
                    Panel(
                        snippet,
                        title=f"Result {idx} | {title} | score={score:.3f}",
                        border_style="blue",
                    )
                )
            console.print()
        except Exception as e:
            console.print(f"[red]Retrieve failed: {e}[/red]\n")

    async def ask_question_stream(
        self,
        question: str,
        meeting_ids: list[int] | None = None,
        top_k: int | None = None,
        use_web_search: bool = False,
    ):
        try:
            from src.services.chain import ask_stream

            console.print("[cyan]Streaming answer:[/cyan]")
            final_session_id = self.session_id
            sources: list[dict[str, Any]] = []
            async for event in ask_stream(
                question=question,
                session_id=self.session_id,
                user_id=self.user_id,
                meeting_ids=meeting_ids,
                top_k=top_k,
                use_web_search=use_web_search,
            ):
                event_type = event.get("type")
                if event_type == "token":
                    console.print(event.get("content", ""), end="")
                elif event_type == "sources":
                    sources = event.get("items", [])
                elif event_type == "done":
                    final_session_id = event.get("session_id", final_session_id)
                elif event_type == "error":
                    console.print(f"\n[red]{event.get('message', 'stream error')}[/red]")
            console.print()
            if final_session_id:
                self.session_id = final_session_id
                console.print(f"[dim]Session: {self.session_id}[/dim]")
            if sources:
                src_lines = [
                    f"• {s.get('meeting_title', 'unknown')} (score: {s.get('score', 0):.3f})"
                    for s in sources
                ]
                console.print(Panel("\n".join(src_lines), title="Sources", border_style="blue"))
            console.print()
        except Exception as e:
            console.print(f"[red]Streaming chat failed: {e}[/red]\n")

    async def upload_file_cli(
        self,
        file_path: str,
        meeting_id: int | None = None,
        title: str | None = None,
        description: str | None = None,
        wait: bool = False,
    ):
        try:
            from src.api.routers.meetings._common import (
                FILE_TYPE_MAP,
                _sanitize_filename,
                _validate_file_content,
            )
            from src.core import database as db
            from src.core.config import settings
            from src.services.processor import process_meeting_file

            path = Path(file_path).expanduser().resolve()
            if not path.exists() or not path.is_file():
                console.print(f"[red]File not found: {path}[/red]\n")
                return
            suffix = path.suffix.lower()
            if suffix not in FILE_TYPE_MAP:
                console.print(f"[red]Unsupported file format: {suffix}[/red]\n")
                return

            # Reuse upload router validation behavior.
            with open(path, "rb") as f:
                first_chunk = f.read(4096)
            _validate_file_content(first_chunk, suffix)

            hasher = hashlib.sha256()
            total = 0
            with open(path, "rb") as f:
                while chunk := f.read(1024 * 1024):
                    hasher.update(chunk)
                    total += len(chunk)
                    if total > settings.MAX_UPLOAD_BYTES:
                        console.print(
                            f"[red]File too large. Maximum: {settings.MAX_UPLOAD_SIZE_MB}MB[/red]\n"
                        )
                        return
            content_hash = hasher.hexdigest()

            if meeting_id is None:
                meeting_title = title or path.stem

                def _create_meeting():
                    with db.get_write_connection() as conn:
                        return db.create_meeting(
                            conn,
                            title=meeting_title,
                            description=description,
                        )

                meeting_id = await asyncio.to_thread(_create_meeting)
            else:
                def _ensure_meeting():
                    with db.get_connection() as conn:
                        return db.get_meeting(conn, meeting_id)

                if not await asyncio.to_thread(_ensure_meeting):
                    console.print(f"[red]Meeting {meeting_id} not found.[/red]\n")
                    return

            safe_name = _sanitize_filename(path.name)
            file_tag = uuid.uuid4().hex[:8]
            save_name = f"{file_tag}_{safe_name}"
            save_path = settings.UPLOAD_DIR / save_name
            settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

            file_type = FILE_TYPE_MAP[suffix].value

            def _reserve():
                with db.get_write_connection() as conn:
                    existing = db.get_meeting_file_by_hash(conn, content_hash, meeting_id)
                    if existing:
                        return None, existing
                    new_id = db.create_meeting_file_if_absent(
                        conn,
                        meeting_id=meeting_id,
                        file_type=file_type,
                        file_name=safe_name,
                        file_path=str(save_path),
                        content_hash=content_hash,
                    )
                    return new_id, None

            file_id, existing = await asyncio.to_thread(_reserve)
            if existing:
                console.print(
                    f"[yellow]File already exists in meeting #{meeting_id} (file_id={existing['id']}).[/yellow]\n"
                )
                return
            if file_id is None:
                console.print("[red]Failed to create meeting file record.[/red]\n")
                return

            try:
                await asyncio.to_thread(shutil.copy2, path, save_path)
            except Exception:
                def _cleanup_reserved_record():
                    with db.get_write_connection() as conn:
                        db.delete_meeting_file(conn, file_id)

                await asyncio.to_thread(_cleanup_reserved_record)
                raise
            console.print(
                f"[green]Uploaded to meeting #{meeting_id} as file #{file_id}. Processing started.[/green]"
            )

            if wait:
                await process_meeting_file(file_id)
                console.print("[green]Processing completed.[/green]\n")
            else:
                task = asyncio.create_task(process_meeting_file(file_id))
                self._track_background_task(task)
                console.print("[dim]Use /meeting or /files to poll processing status.[/dim]\n")
        except Exception as e:
            console.print(f"[red]Upload failed: {e}[/red]\n")

    async def reprocess_meeting_cli(self, meeting_id: int, wait: bool = False):
        try:
            from src.core import database as db
            from src.services.processor import process_meeting_file
            from src.services.rag import delete_meeting_chunks

            def _fetch():
                with db.get_write_connection() as conn:
                    meeting = db.get_meeting(conn, meeting_id)
                    if not meeting:
                        return None, []
                    db.update_meeting_status(conn, meeting_id, "processing")
                with db.get_connection() as conn:
                    files = db.list_meeting_files(conn, meeting_id)
                return meeting, files

            meeting, files = await asyncio.to_thread(_fetch)
            if not meeting:
                console.print(f"[red]Meeting {meeting_id} not found.[/red]\n")
                return
            if not files:
                console.print("[yellow]No files in meeting; nothing to reprocess.[/yellow]\n")
                return

            await asyncio.to_thread(delete_meeting_chunks, meeting_id)
            tasks = [asyncio.create_task(process_meeting_file(f["id"])) for f in files]
            for task in tasks:
                self._track_background_task(task)
            if wait:
                await asyncio.gather(*tasks)
                console.print("[green]Reprocessing completed.[/green]\n")
            else:
                console.print("[green]Reprocessing scheduled in background.[/green]\n")
        except Exception as e:
            console.print(f"[red]Reprocess failed: {e}[/red]\n")

    async def show_transcript_cli(self, meeting_id: int, file_id: int | None = None):
        try:
            from src.core import database as db

            def _fetch_file():
                with db.get_connection() as conn:
                    meeting = db.get_meeting(conn, meeting_id)
                    if not meeting:
                        return None
                    file_record = db.get_meeting_file(conn, file_id) if file_id else None
                    if file_id:
                        if file_record is None:
                            return meeting, "__MISSING__", None
                        if file_record["meeting_id"] != meeting_id:
                            return meeting, "__MISMATCH__", None
                    if file_record:
                        return meeting, file_record, file_record.get("transcript") or ""
                    return meeting, None, db.get_meeting_transcripts(conn, meeting_id)

            result = await asyncio.to_thread(_fetch_file)
            if result is None:
                console.print(f"[red]Meeting {meeting_id} not found.[/red]\n")
                return
            _meeting, file_record, transcript = result
            if file_record == "__MISSING__":
                console.print(f"[red]File {file_id} not found.[/red]\n")
                return
            if file_record == "__MISMATCH__":
                console.print(f"[red]File {file_id} not found in meeting {meeting_id}.[/red]\n")
                return
            if not transcript:
                console.print("[yellow]No transcript available.[/yellow]\n")
                return
            title = f"Transcript (Meeting #{meeting_id})"
            if file_record:
                title = f"Transcript (Meeting #{meeting_id}, File #{file_record['id']})"
            console.print(Panel(transcript[:8000], title=title, border_style="green"))
            if len(transcript) > 8000:
                console.print("[dim](Truncated to first 8000 characters)[/dim]")
            console.print()
        except Exception as e:
            console.print(f"[red]Transcript query failed: {e}[/red]\n")

    async def summarize_meeting_cli(self, meeting_id: int):
        try:
            from src.api.routers.meetings._summary import generate_summary

            with console.status("[yellow]Generating summary...[/yellow]"):
                result = await generate_summary(meeting_id, principal={"user_id": "default"})
            console.print(Panel(result.summary, title=f"Meeting #{meeting_id} Summary", border_style="green"))
            if result.per_file_summaries:
                lines = [f"• {f.file_name}: {len(f.key_points)} key points" for f in result.per_file_summaries]
                console.print(Panel("\n".join(lines), title="Per-file Coverage", border_style="blue"))
            console.print()
        except Exception as e:
            console.print(f"[red]Summary generation failed: {e}[/red]\n")

    async def export_meeting_cli(
        self,
        meeting_id: int,
        fmt: str = "markdown",
        output_path: str | None = None,
    ):
        try:
            from src.api.routers.meetings._export import export_meeting
            from src.models.schemas import ExportFormat

            format_map = {
                "markdown": ExportFormat.MARKDOWN,
                "md": ExportFormat.MARKDOWN,
                "json": ExportFormat.JSON,
                "txt": ExportFormat.TXT,
            }
            export_format = format_map.get(fmt.lower())
            if export_format is None:
                console.print("[red]Unsupported format. Use markdown/json/txt.[/red]\n")
                return

            with console.status("[yellow]Exporting meeting...[/yellow]"):
                response = await export_meeting(
                    meeting_id=meeting_id,
                    format=export_format,
                    principal={"user_id": "default"},
                )

            if output_path:
                out = Path(output_path).expanduser()
            else:
                export_dir = Path(os.getcwd()) / "exports"
                export_dir.mkdir(parents=True, exist_ok=True)
                out = export_dir / response.filename

            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(response.content, encoding="utf-8")
            console.print(
                f"[green]Exported meeting #{meeting_id} to {out} ({response.format.value}).[/green]\n"
            )
        except Exception as e:
            console.print(f"[red]Export failed: {e}[/red]\n")

    async def settings_get_cli(self, dotted_key: str | None = None):
        try:
            from src.api.routers.settings import _get_current_settings

            payload = _get_current_settings().model_dump(mode="json")
            if dotted_key:
                cursor: Any = payload
                for key in dotted_key.split("."):
                    if not isinstance(cursor, dict) or key not in cursor:
                        console.print(f"[red]Unknown settings path: {dotted_key}[/red]\n")
                        return
                    cursor = cursor[key]
                console.print(
                    Panel(
                        json.dumps(cursor, indent=2, ensure_ascii=False),
                        title=f"Setting: {dotted_key}",
                        border_style="cyan",
                    )
                )
            else:
                console.print(
                    Panel(
                        json.dumps(payload, indent=2, ensure_ascii=False),
                        title="Runtime Settings",
                        border_style="cyan",
                    )
                )
            console.print()
        except Exception as e:
            console.print(f"[red]Failed to read settings: {e}[/red]\n")

    async def settings_set_cli(self, dotted_key: str, raw_value: str):
        try:
            from src.api.routers.settings import _get_current_settings, _update_settings_in_memory
            from src.models.schemas import SettingsUpdateRequest

            payload = _get_current_settings().model_dump(mode="json")
            keys = dotted_key.split(".")
            target = payload
            for key in keys[:-1]:
                if key not in target or not isinstance(target[key], dict):
                    console.print(f"[red]Unknown settings path: {dotted_key}[/red]\n")
                    return
                target = target[key]
            leaf = keys[-1]
            if leaf not in target:
                console.print(f"[red]Unknown settings key: {dotted_key}[/red]\n")
                return

            try:
                parsed_value = json.loads(raw_value)
            except json.JSONDecodeError:
                parsed_value = raw_value
            target[leaf] = parsed_value

            if not keys:
                console.print(f"[red]Invalid settings path: {dotted_key}[/red]\n")
                return

            section = keys[0]
            section_payload = payload.get(section)
            if not isinstance(section_payload, dict):
                console.print(f"[red]Unknown settings section: {section}[/red]\n")
                return

            # Apply a minimal patch payload to avoid unnecessary singleton resets.
            req = SettingsUpdateRequest(**{section: section_payload})
            _update_settings_in_memory(req)
            console.print(f"[green]Updated setting: {dotted_key} = {parsed_value!r}[/green]\n")
        except Exception as e:
            console.print(f"[red]Failed to update settings: {e}[/red]\n")

    async def settings_keys_cli(self, prefix: str | None = None):
        try:
            from src.api.routers.settings import _get_current_settings

            payload = _get_current_settings().model_dump(mode="json")
            keys: list[str] = []

            def _walk(node: dict[str, Any], base: str = "") -> None:
                for k, v in node.items():
                    path = f"{base}.{k}" if base else k
                    if isinstance(v, dict):
                        _walk(v, path)
                    else:
                        keys.append(path)

            _walk(payload)
            keys = sorted(keys)
            if prefix:
                keys = [k for k in keys if k.startswith(prefix)]
            if not keys:
                console.print("[yellow]No matching settings keys.[/yellow]\n")
                return
            console.print(Panel("\n".join(keys), title="Settings Keys", border_style="blue"))
            console.print()
        except Exception as e:
            console.print(f"[red]Failed to list settings keys: {e}[/red]\n")

    async def settings_bindings_cli(self):
        try:
            from src.api.routers.settings import get_available_bindings

            bindings = await get_available_bindings()
            console.print(
                Panel(
                    json.dumps(bindings, indent=2, ensure_ascii=False),
                    title="Available Bindings",
                    border_style="blue",
                )
            )
            console.print()
        except Exception as e:
            console.print(f"[red]Failed to fetch bindings: {e}[/red]\n")

    async def settings_reload_cli(self):
        try:
            from src.api.routers.settings import reload_config

            result = await reload_config()
            console.print(f"[green]{result.get('message', 'Config reloaded')}[/green]\n")
        except Exception as e:
            console.print(f"[red]Failed to reload config: {e}[/red]\n")

    async def settings_rebuild_vectors_cli(self):
        try:
            from src.api.routers import settings as settings_router

            if settings_router._rebuild_running:  # noqa: SLF001
                console.print("[yellow]Vector rebuild already running.[/yellow]\n")
                return
            settings_router._rebuild_running = True  # noqa: SLF001
            task = asyncio.create_task(settings_router._rebuild_vectors_task())  # noqa: SLF001
            task.add_done_callback(settings_router._reset_rebuild_flag)  # noqa: SLF001
            self._track_background_task(task)
            console.print("[green]Vector rebuild started in background.[/green]\n")
        except Exception as e:
            console.print(f"[red]Failed to trigger vector rebuild: {e}[/red]\n")

    async def settings_wizard_cli(self, section: str):
        try:
            from src.api.routers.settings import _get_current_settings

            payload = _get_current_settings().model_dump(mode="json")
            section_payload = payload.get(section)
            if not isinstance(section_payload, dict):
                console.print(
                    "[red]Unknown section. Try one of: llm, embedding, rag, memory, search, upload[/red]\n"
                )
                return
            console.print(
                f"[cyan]Settings wizard for section '{section}'. Press Enter to keep current value.[/cyan]"
            )
            for key, value in section_payload.items():
                raw = Prompt.ask(f"{section}.{key}", default=str(value))
                if raw == str(value):
                    continue
                await self.settings_set_cli(f"{section}.{key}", raw)
            console.print("[green]Settings wizard complete.[/green]\n")
        except Exception as e:
            console.print(f"[red]Settings wizard failed: {e}[/red]\n")

    async def search_meetings(self, query: str):
        """Search meeting content."""
        if not query:
            query = Prompt.ask("[cyan]Enter search query[/cyan]")

        try:
            from src.mcp import search_meetings

            with console.status("[yellow]Searching...[/yellow]"):
                results = search_meetings(query, top_k=5)

            console.print(f"\n[bold cyan]Search Results for:[/bold cyan] {query}\n")

            if "No relevant" in results or not results.strip():
                console.print("[yellow]No relevant content found.[/yellow]\n")
                return

            console.print(Panel(results, border_style="blue", title="Results"))
            console.print()

        except Exception as e:
            console.print(f"[red]Search error: {e}[/red]\n")

    async def test_mcp_tools(self):
        """Interactive MCP tool testing."""
        console.print("\n[bold cyan]MCP Tool Testing[/bold cyan]\n")

        tools = {
            "1": ("list_meetings", self._test_list_meetings),
            "2": ("search_meetings", self._test_search_meetings),
            "3": ("manage_memory", self._test_manage_memory),
            "4": ("ask_about_meetings", self._test_ask_about_meetings),
            "5": ("list_skills", self._test_list_skills),
            "6": ("invoke_skill", self._test_invoke_skill),
        }

        while True:
            console.print("[cyan]Available tests:[/cyan]")
            for key, (name, _) in tools.items():
                console.print(f"  {key}. {name}")
            console.print("  7. Run all tests")
            console.print("  0. Back to main menu")

            choice = Prompt.ask("[cyan]Select test[/cyan]", choices=["0", "1", "2", "3", "4", "5", "6", "7"])

            if choice == "0":
                break
            elif choice == "7":
                for key, (_, test_func) in tools.items():
                    await test_func()
            else:
                _, test_func = tools[choice]
                await test_func()

            console.print()

    async def _test_list_meetings(self):
        """Test list_meetings MCP tool."""
        console.print("\n[dim]Testing list_meetings...[/dim]")
        try:
            from src.mcp import list_meetings

            result = list_meetings(limit=5)
            console.print(Panel(result, border_style="green", title="list_meetings result"))
        except Exception as e:
            console.print(f"[red]Failed: {e}[/red]")

    async def _test_search_meetings(self):
        """Test search_meetings MCP tool."""
        console.print("\n[dim]Testing search_meetings...[/dim]")
        try:
            from src.mcp import search_meetings

            query = Prompt.ask("[cyan]Enter search query[/cyan]", default="meeting")
            result = search_meetings(query, top_k=3)
            console.print(Panel(result, border_style="green", title="search_meetings result"))
        except Exception as e:
            console.print(f"[red]Failed: {e}[/red]")

    async def _test_manage_memory(self):
        """Test manage_memory MCP tool."""
        console.print("\n[dim]Testing manage_memory...[/dim]")
        try:
            from src.mcp import manage_memory

            # Test set
            result = manage_memory("set", key="cli_test", value="Hello from CLI", user_id=self.user_id)
            console.print(f"[green]Set:[/green] {result}")

            # Test get
            result = manage_memory("get", key="cli_test", user_id=self.user_id)
            console.print(f"[green]Get:[/green] {result}")

            # Test list
            result = manage_memory("list", user_id=self.user_id)
            console.print(Panel(result, border_style="green", title="list memories"))

            # Test delete
            result = manage_memory("delete", key="cli_test", user_id=self.user_id)
            console.print(f"[green]Delete:[/green] {result}")

        except Exception as e:
            console.print(f"[red]Failed: {e}[/red]")

    async def _test_ask_about_meetings(self):
        """Test ask_about_meetings MCP tool."""
        console.print("\n[dim]Testing ask_about_meetings...[/dim]")
        try:
            from src.mcp import ask_about_meetings

            question = Prompt.ask("[cyan]Enter question[/cyan]", default="What meetings do we have?")

            with console.status("[yellow]Thinking...[/yellow]"):
                result = await ask_about_meetings(question, user_id=self.user_id)

            # Parse and display JSON result
            try:
                parsed = json.loads(result)
                console.print(Panel(
                    parsed.get("answer", "No answer"),
                    border_style="green",
                    title="Answer"
                ))
                if parsed.get("sources"):
                    console.print(f"[dim]Sources: {', '.join(parsed['sources'])}[/dim]")
            except json.JSONDecodeError:
                console.print(Panel(result, border_style="green", title="Raw result"))

        except Exception as e:
            console.print(f"[red]Failed: {e}[/red]")

    async def _test_list_skills(self):
        """Test list_skills MCP tool."""
        console.print("\n[dim]Testing list_skills...[/dim]")
        try:
            from src.mcp import list_skills

            result = list_skills()
            console.print(Panel(result, border_style="green", title="list_skills result"))
        except Exception as e:
            console.print(f"[red]Failed: {e}[/red]")

    async def _test_invoke_skill(self):
        """Test invoke_skill MCP tool."""
        console.print("\n[dim]Testing invoke_skill...[/dim]")
        try:
            from src.mcp import invoke_skill

            query = Prompt.ask("[cyan]Enter query for skill invocation[/cyan]", default="Generate a project proposal")

            with console.status("[yellow]Invoking skill...[/yellow]"):
                result = await invoke_skill("tech_proposal_generator", query, user_id=self.user_id)

            try:
                parsed = json.loads(result)
                console.print(Panel(
                    parsed.get("output", "No output"),
                    border_style="green",
                    title=f"Skill: {parsed.get('skill', 'unknown')}"
                ))
            except json.JSONDecodeError:
                console.print(Panel(result, border_style="green", title="Raw result"))
        except Exception as e:
            console.print(f"[red]Failed: {e}[/red]")

    async def list_skills(self):
        """Display list of available skills."""
        try:
            from skills.loader import SkillLoader

            loader = SkillLoader()
            skills = loader.load_all()

            if not skills:
                console.print("[yellow]No skills found.[/yellow]\n")
                return

            table = Table(box=box.ROUNDED, show_header=True, title="Available Skills")
            table.add_column("Name", style="cyan", width=25)
            table.add_column("Display Name", style="white", width=25)
            table.add_column("Category", style="blue", width=15)
            table.add_column("Examples", style="dim", width=30)

            for skill in skills:
                examples = ", ".join(skill.intent_matching.examples[:2])
                table.add_row(
                    skill.name,
                    skill.display_name[:25],
                    skill.metadata.category or "-",
                    examples[:30]
                )

            console.print(table)
            console.print()

        except Exception as e:
            console.print(f"[red]Error listing skills: {e}[/red]\n")

    async def invoke_skill(self, skill_name: str = "", query: str = ""):
        """Manually invoke a specific skill."""
        try:
            from skills.loader import SkillLoader
            from src.services.chain import PipelineContext, _extract_sources
            from src.services.chain._api import _run_pipeline

            loader = SkillLoader()

            if not skill_name:
                # Show available skills
                skills = loader.load_all()
                if skills:
                    console.print("[cyan]Available skills:[/cyan]")
                    for i, skill in enumerate(skills, 1):
                        console.print(f"  {i}. {skill.name} - {skill.display_name}")
                    console.print()

                skill_name = Prompt.ask("[cyan]Enter skill name[/cyan]")

            skill = loader.get(skill_name)
            if not skill:
                available = [s.name for s in loader.load_all()]
                console.print(f"[red]Skill '{skill_name}' not found. Available: {', '.join(available)}[/red]\n")
                return

            if not query:
                query = Prompt.ask("[cyan]Enter your query[/cyan]")

            console.print(f"[dim]Invoking skill '{skill_name}'...[/dim]")

            ctx = PipelineContext(
                question=query,
                session_id=self.session_id,
                user_id=self.user_id,
            )

            # Execute RAG with skill configuration
            await _run_pipeline(ctx, skill.model_dump())

            self.session_id = ctx.session_id

            # Display result
            answer_panel = Panel(
                ctx.answer,
                border_style="green",
                title=f"Skill: {skill.display_name}"
            )
            console.print(answer_panel)

            # Display sources
            sources = _extract_sources(ctx.docs)
            if sources:
                sources_text = "\n".join([
                    f"• {s['meeting_title']} (score: {s['score']:.3f})"
                    for s in sources
                ])
                console.print(Panel(sources_text, border_style="blue", title="Sources"))

            # Save to history
            self.conversation_history.append({
                "role": "user",
                "content": f"[{skill_name}] {query}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            self.conversation_history.append({
                "role": "agent",
                "content": ctx.answer,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        except Exception as e:
            console.print(f"[red]Error invoking skill: {e}[/red]\n")

    async def match_skill_intent(self, query: str = ""):
        """Test intent matching for a query."""
        if not query:
            query = Prompt.ask("[cyan]Enter query to test[/cyan]")

        try:
            from skills.loader import SkillLoader
            from skills.matcher import IntentMatchingService

            loader = SkillLoader()
            skills = loader.load_all()
            matcher = IntentMatchingService()

            result = await matcher.match(query, skills)

            if not result or not result.matched:
                console.print("[yellow]No skill matched for this query.[/yellow]\n")
                return

            # Display match result
            table = Table(box=box.ROUNDED, title="Intent Matching Result")
            table.add_column("Field", style="cyan")
            table.add_column("Value", style="white")

            table.add_row("Matched Skill", result.skill.display_name)
            table.add_row("Skill Name", result.skill.name)
            table.add_row("Confidence", f"{result.score:.3f}")
            table.add_row("Description", result.skill.description[:50] + "...")

            if result.details:
                details_text = "\n".join([
                    f"{k}: {v}" for k, v in result.details.items()
                    if isinstance(v, (str, int, float))
                ])
                if details_text:
                    table.add_row("Details", details_text)

            console.print(table)

            # Show if ambiguous
            if result.ambiguous and result.alternatives:
                console.print("\n[yellow]Ambiguous match. Alternatives:[/yellow]")
                for alt in result.alternatives:
                    console.print(f"  - {alt.display_name} ({alt.name})")

            console.print()

        except Exception as e:
            console.print(f"[red]Error testing intent matching: {e}[/red]\n")

    async def manage_memory(self):
        """Interactive memory management."""
        console.print("\n[bold cyan]Memory Management[/bold cyan]\n")

        while True:
            action = Prompt.ask(
                "[cyan]Action[/cyan] (list/set/get/delete/quit)",
                choices=["list", "set", "get", "delete", "quit"]
            )

            if action == "quit":
                break

            try:
                from src.services.memory import memory_service

                if action == "list":
                    memories = memory_service.list_all(self.user_id)
                    if not memories:
                        console.print("[yellow]No memories found.[/yellow]\n")
                    else:
                        table = Table(box=box.ROUNDED)
                        table.add_column("Key", style="cyan")
                        table.add_column("Value", style="white")
                        table.add_column("Source", style="dim")
                        table.add_column("Updated", style="dim")

                        for m in memories:
                            table.add_row(m["key"], m["value"], m["source"], m["updated_at"])

                        console.print(table)

                elif action == "set":
                    key = Prompt.ask("[cyan]Key[/cyan]")
                    value = Prompt.ask("[cyan]Value[/cyan]")
                    memory_service.set(self.user_id, key, value, source="manual")
                    console.print("[green]Memory saved.[/green]")

                elif action == "get":
                    key = Prompt.ask("[cyan]Key[/cyan]")
                    value = memory_service.get(self.user_id, key)
                    if value:
                        console.print(f"[green]{key}:[/green] {value}")
                    else:
                        console.print(f"[yellow]Key '{key}' not found.[/yellow]")

                elif action == "delete":
                    key = Prompt.ask("[cyan]Key[/cyan]")
                    memory_service.delete(self.user_id, key)
                    console.print("[green]Memory deleted.[/green]")

            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")

            console.print()

    async def ask_question(self, question: str):
        """Send a question to the RAG system."""
        try:
            from src.services.chain import ask

            with console.status("[yellow]Thinking...[/yellow]"):
                result = await ask(
                    question=question,
                    session_id=self.session_id,
                    user_id=self.user_id,
                )

            self.session_id = result.session_id

            # Build title with skill info if applicable
            title = f"Answer (Session: {self.session_id[:8]}...)"
            if result.skill_used:
                title += f" [Skill: {result.skill_used}]"
                border_style = "magenta"  # Use different color for skill responses
            else:
                border_style = "green"

            # Display answer
            answer_panel = Panel(
                result.answer,
                border_style=border_style,
                title=title,
            )
            console.print(answer_panel)

            # Display skill confidence if applicable
            if result.skill_confidence:
                console.print(f"[dim]Skill confidence: {result.skill_confidence:.2f}[/dim]")

            # Display sources if any
            if result.sources:
                sources_text = "\n".join([
                    f"• {s['meeting_title']} (score: {s['score']:.3f})"
                    for s in result.sources
                ])
                console.print(Panel(sources_text, border_style="blue", title="Sources"))

            # Save to history
            self.conversation_history.append({
                "role": "user",
                "content": question,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            self.conversation_history.append({
                "role": "agent",
                "content": result.answer,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

        console.print()

    def clear_history(self):
        """Clear conversation history."""
        self.conversation_history.clear()
        self.session_id = None
        console.print("[green]Conversation history cleared.[/green]\n")

    def print_help(self):
        """Display help information."""
        help_text = """
[bold cyan]Available Commands:[/bold cyan]

  [green]/help[/green]          - Show this help message
  [green]/status[/green]        - Run system diagnostic checks
  [green]/meetings[/green]      - List all uploaded meetings
  [green]/meetings --limit --offset[/green] - Paginate meetings
  [green]/meeting <id>[/green]  - Show a meeting detail
  [green]/files <id>[/green]    - List files of a meeting (supports pagination)
  [green]/upload <path>[/green] - Upload file from local disk
  [green]/reprocess <id>[/green]- Reprocess all files in a meeting
  [green]/transcript <id>[/green]- Show transcript (supports --file)
  [green]/summary <id>[/green]  - Generate meeting summary
  [green]/export <id>[/green]   - Export meeting to markdown/json/txt
  [green]/retrieve <q>[/green]  - Retrieval-only search via vector store
  [green]/chat_stream <q>[/green]- Stream answer tokens
  [green]/sessions[/green]      - List chat sessions (supports pagination)
  [green]/session <id>[/green]  - Show session messages
  [green]/session_use <id>[/green] - Use session for follow-up chat
  [green]/session_delete <id>[/green] - Delete a session
  [green]/settings ...[/green]  - get/set/keys/bindings/reload/rebuild/wizard
  [green]/settings_get[/green]  - Alias of /settings get
  [green]/settings_set k v[/green] - Alias of /settings set
  [green]/search[/green]        - Search meeting content
  [green]/skills[/green]        - List available skills
  [green]/skill_invoke[/green]  - Manually invoke a skill
  [green]/skill_match[/green]   - Test intent matching
  [green]/mcp[/green]           - Test MCP tool calls
  [green]/memory[/green]        - Manage user memories
  [green]/clear[/green]         - Clear conversation history
  [green]/quit[/green]          - Exit the CLI

[bold cyan]Usage Examples:[/bold cyan]

  Ask a question (auto skill matching):
    > What was discussed in yesterday's meeting?
    > Please generate a technical proposal

  List and invoke skills:
    > /skills
    > /skill_invoke tech_proposal_generator

  Search without LLM:
    > /search budget allocation
    > /retrieve "action items from project kickoff" --meeting 12 --top-k 8

  Pagination:
    > /meetings --limit 10 --offset 0
    > /files 12 --limit 5 --offset 5
    > /sessions --limit 10 --offset 10

  Export:
    > /export 12 --format json --output ./exports/meeting-12.json

  Check system status:
    > /status

  Stream answer:
    > /chat_stream "What are the top risks?" --meeting 12 --top-k 6 --web

  Upload and wait:
    > /upload "~/Downloads/spec.pdf" --title "Spec Review" --wait

  Settings:
    > /settings keys rag
    > /settings get rag.top_k
    > /settings set rag.top_k 8
    > /settings bindings
    > /settings wizard rag

[dim]Press Ctrl+C to exit at any time.[/dim]
        """
        console.print(help_text)

    async def _handle_slash_command(self, user_input: str) -> None:
        try:
            tokens = shlex.split(user_input[1:])
        except ValueError as e:
            console.print(f"[red]Command parse error: {e}[/red]\n")
            return
        if not tokens:
            return

        command = tokens[0].lower()
        args = tokens[1:]

        if command == "help":
            self.print_help()
        elif command == "status":
            await self.display_status()
        elif command == "meetings":
            positional, options = self._split_args_options(
                args, {"--limit", "--offset"}, set()
            )
            if positional:
                console.print("[yellow]Ignoring extra positional args for /meetings.[/yellow]")
            limit = self._parse_int_option(options.get("--limit"), 20)
            offset = self._parse_non_negative_option(options.get("--offset"), 0)
            await self.list_meetings(limit=limit, offset=offset)
        elif command == "meeting":
            meeting_id = int(args[0]) if args else self._prompt_int("Meeting ID", 1)
            await self.show_meeting_detail(meeting_id)
        elif command == "files":
            positional, options = self._split_args_options(
                args, {"--limit", "--offset"}, set()
            )
            meeting_id = int(positional[0]) if positional else self._prompt_int("Meeting ID", 1)
            limit = self._parse_int_option(options.get("--limit"), 20)
            offset = self._parse_non_negative_option(options.get("--offset"), 0)
            await self.list_meeting_files(meeting_id, limit=limit, offset=offset)
        elif command == "upload":
            value_opts = {"--meeting", "--title", "--description"}
            flag_opts = {"--wait"}
            positional, options = self._split_args_options(args, value_opts, flag_opts)
            if positional:
                file_path = positional[0]
            else:
                file_path = Prompt.ask("[cyan]Path to local file[/cyan]").strip()
            meeting_id = int(options["--meeting"]) if "--meeting" in options else None
            if meeting_id is None and Confirm.ask("[cyan]Upload into an existing meeting?[/cyan]", default=False):
                meeting_id = self._prompt_int("Meeting ID", 1)
            title = options.get("--title")
            description = options.get("--description")
            if meeting_id is None and not title:
                title = Prompt.ask("[cyan]Meeting title (new meeting)[/cyan]")
            wait_flag = bool(options.get("--wait"))
            if not wait_flag:
                wait_flag = Confirm.ask("[cyan]Wait until processing finishes?[/cyan]", default=False)
            await self.upload_file_cli(
                file_path,
                meeting_id=meeting_id,
                title=title,
                description=description,
                wait=wait_flag,
            )
        elif command == "reprocess":
            value_opts: set[str] = set()
            flag_opts = {"--wait"}
            positional, options = self._split_args_options(args, value_opts, flag_opts)
            meeting_id = int(positional[0]) if positional else self._prompt_int("Meeting ID", 1)
            wait_flag = bool(options.get("--wait"))
            if not wait_flag:
                wait_flag = Confirm.ask("[cyan]Wait for all files to finish?[/cyan]", default=False)
            await self.reprocess_meeting_cli(meeting_id, wait=wait_flag)
        elif command == "transcript":
            value_opts = {"--file"}
            flag_opts: set[str] = set()
            positional, options = self._split_args_options(args, value_opts, flag_opts)
            meeting_id = int(positional[0]) if positional else self._prompt_int("Meeting ID", 1)
            file_id = int(options["--file"]) if "--file" in options else None
            if file_id is None and Confirm.ask("[cyan]View transcript of a specific file?[/cyan]", default=False):
                file_id = self._prompt_int("File ID", 1)
            await self.show_transcript_cli(meeting_id, file_id=file_id)
        elif command == "summary":
            meeting_id = int(args[0]) if args else self._prompt_int("Meeting ID", 1)
            await self.summarize_meeting_cli(meeting_id)
        elif command == "export":
            value_opts = {"--format", "--output"}
            flag_opts: set[str] = set()
            positional, options = self._split_args_options(args, value_opts, flag_opts)
            meeting_id = int(positional[0]) if positional else self._prompt_int("Meeting ID", 1)
            fmt = options.get("--format")
            if not fmt:
                fmt = Prompt.ask(
                    "[cyan]Format[/cyan]",
                    choices=["markdown", "json", "txt"],
                    default="markdown",
                )
            output_path = options.get("--output")
            if output_path is None and Confirm.ask(
                "[cyan]Specify output file path manually?[/cyan]", default=False
            ):
                output_path = Prompt.ask("[cyan]Output file path[/cyan]").strip()
            await self.export_meeting_cli(meeting_id, fmt=fmt, output_path=output_path)
        elif command == "retrieve":
            value_opts = {"--meeting", "--top-k"}
            flag_opts: set[str] = set()
            positional, options = self._split_args_options(args, value_opts, flag_opts)
            query = " ".join(positional).strip()
            if not query:
                query = Prompt.ask("[cyan]Retrieve query[/cyan]").strip()
            if not query:
                console.print("[red]Query cannot be empty.[/red]\n")
                return
            meeting_ids = self._parse_int_csv(options.get("--meeting"))
            top_k = self._parse_int_option(options.get("--top-k"), 5)
            await self.retrieve_only(query, meeting_ids=meeting_ids, top_k=top_k)
        elif command == "chat_stream":
            value_opts = {"--meeting", "--top-k"}
            flag_opts = {"--web"}
            positional, options = self._split_args_options(args, value_opts, flag_opts)
            question = " ".join(positional).strip()
            if not question:
                question = Prompt.ask("[cyan]Question[/cyan]").strip()
            if not question:
                console.print("[red]Question cannot be empty.[/red]\n")
                return
            meeting_ids = self._parse_int_csv(options.get("--meeting"))
            top_k = int(options["--top-k"]) if "--top-k" in options else None
            if top_k is None and Confirm.ask("[cyan]Customize top-k?[/cyan]", default=False):
                top_k = self._prompt_int("Top-K", 5)
            use_web = bool(options.get("--web"))
            if not use_web:
                use_web = Confirm.ask("[cyan]Enable web search?[/cyan]", default=False)
            await self.ask_question_stream(
                question,
                meeting_ids=meeting_ids,
                top_k=top_k,
                use_web_search=use_web,
            )
        elif command == "sessions":
            positional, options = self._split_args_options(
                args, {"--limit", "--offset"}, set()
            )
            if positional:
                console.print("[yellow]Ignoring extra positional args for /sessions.[/yellow]")
            limit = self._parse_int_option(options.get("--limit"), 20)
            offset = self._parse_non_negative_option(options.get("--offset"), 0)
            await self.list_sessions_cli(limit=limit, offset=offset)
        elif command == "session":
            session_id = args[0] if args else Prompt.ask("[cyan]Session ID[/cyan]").strip()
            if not session_id:
                console.print("[red]Session ID is required.[/red]\n")
                return
            await self.show_session_messages(session_id)
        elif command == "session_use":
            session_id = args[0] if args else Prompt.ask("[cyan]Session ID[/cyan]").strip()
            if not session_id:
                console.print("[red]Session ID is required.[/red]\n")
                return
            await self.use_session(session_id)
        elif command == "session_delete":
            session_id = args[0] if args else Prompt.ask("[cyan]Session ID[/cyan]").strip()
            if not session_id:
                console.print("[red]Session ID is required.[/red]\n")
                return
            if not Confirm.ask(f"[cyan]Delete session {session_id}?[/cyan]", default=False):
                console.print("[yellow]Cancelled.[/yellow]\n")
                return
            await self.delete_session_cli(session_id)
        elif command == "settings":
            sub = args[0].lower() if args else "get"
            rest = args[1:] if args else []
            if sub == "get":
                key = rest[0] if rest else None
                await self.settings_get_cli(key)
            elif sub == "set":
                if len(rest) >= 2:
                    key = rest[0]
                    value = " ".join(rest[1:])
                else:
                    key = Prompt.ask("[cyan]Dotted key[/cyan]")
                    value = Prompt.ask("[cyan]Value (JSON/string)[/cyan]")
                await self.settings_set_cli(key, value)
            elif sub == "keys":
                prefix = rest[0] if rest else None
                await self.settings_keys_cli(prefix)
            elif sub == "bindings":
                await self.settings_bindings_cli()
            elif sub == "reload":
                await self.settings_reload_cli()
            elif sub == "rebuild":
                await self.settings_rebuild_vectors_cli()
            elif sub == "wizard":
                section = rest[0] if rest else Prompt.ask(
                    "[cyan]Section[/cyan]",
                    choices=["llm", "embedding", "rag", "memory", "search", "upload"],
                    default="rag",
                )
                await self.settings_wizard_cli(section)
            else:
                console.print(
                    "[red]Usage: /settings [get|set|keys|bindings|reload|rebuild|wizard][/red]\n"
                )
        elif command == "settings_get":
            await self.settings_get_cli(args[0] if args else None)
        elif command == "settings_set":
            if len(args) >= 2:
                await self.settings_set_cli(args[0], " ".join(args[1:]))
            else:
                key = Prompt.ask("[cyan]Dotted key[/cyan]")
                value = Prompt.ask("[cyan]Value (JSON/string)[/cyan]")
                await self.settings_set_cli(key, value)
        elif command == "search":
            query = " ".join(args).strip() if args else ""
            await self.search_meetings(query)
        elif command == "skills":
            await self.list_skills()
        elif command == "skill_invoke":
            skill_name = args[0] if len(args) > 0 else ""
            query = " ".join(args[1:]) if len(args) > 1 else ""
            await self.invoke_skill(skill_name, query)
        elif command == "skill_match":
            await self.match_skill_intent(" ".join(args) if args else "")
        elif command == "mcp":
            await self.test_mcp_tools()
        elif command == "memory":
            await self.manage_memory()
        elif command == "clear":
            self.clear_history()
        elif command in ("quit", "exit", "q"):
            console.print("[yellow]Goodbye![/yellow]")
            self.running = False
        else:
            console.print(
                f"[red]Unknown command: {command}. Type /help for available commands.[/red]\n"
            )

    async def run(self):
        """Main event loop."""
        self.print_banner()

        while self.running:
            try:
                # Get user input
                user_input = Prompt.ask("[bold blue]>[/bold blue]").strip()

                if not user_input:
                    continue

                if user_input.startswith("/"):
                    await self._handle_slash_command(user_input)
                else:
                    # Treat as question
                    await self.ask_question(user_input)

            except KeyboardInterrupt:
                console.print("\n[yellow]Goodbye![/yellow]")
                break
            except EOFError:
                console.print("\n[yellow]Input closed. Goodbye![/yellow]")
                break
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]\n")


def main():
    """Entry point."""
    try:
        cli = MeetingAgentCLI()
        asyncio.run(cli.run())
    except Exception as e:
        console.print(f"[red]Fatal error: {e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
