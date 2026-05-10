"""Entry point for running the MCP server via python -m src.mcp"""

from .mcp import mcp

if __name__ == "__main__":
    mcp.run()
