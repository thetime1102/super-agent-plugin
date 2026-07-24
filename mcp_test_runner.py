#!/usr/bin/env python3
"""
mcp_test_runner.py -- Test MCP Server tools via stdio client.
Goi 2 tools: search_verified_patterns + search_memory, collect ket qua.
"""
import asyncio, json, os, sys, traceback

# Fix Windows cp932 encoding
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

_SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "super_agent_mcp.py",
)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from contextlib import AsyncExitStack


async def main():
    sep = "=" * 60
    dash = "-" * 60
    print(sep, flush=True)
    print("  MCP TEST RUNNER - Super Agent Memory Server", flush=True)
    print(sep, flush=True)

    server_params = StdioServerParameters(
        command="python",
        args=[_SCRIPT],
    )

    async with AsyncExitStack() as stack:
        read, write = await stack.enter_async_context(
            stdio_client(server_params)
        )
        session = await stack.enter_async_context(
            ClientSession(read, write)
        )

        # Initialize
        await session.initialize()
        print("\n[OK] MCP Session initialized\n", flush=True)

        # --- List tools ---
        tools = await session.list_tools()
        print(f"[Tools] ({len(tools.tools)} registered):")
        for t in tools.tools:
            print(f"  - {t.name}: {t.description[:80]}...")
        print(flush=True)

        # ============================================================
        # Tool 1: search_verified_patterns
        # ============================================================
        print(dash, flush=True)
        print("  TOOL 1: search_verified_patterns", flush=True)
        print("  Query: 'worker crash OR race condition'", flush=True)
        print(dash, flush=True)

        try:
            result1 = await session.call_tool(
                "search_verified_patterns",
                {
                    "error_description": "worker crash and race condition auto post worker",
                    "top_k": 3,
                    "min_score": 0.10,
                },
            )
            print("\n[Result 1]", flush=True)
            for content in result1.content:
                if content.type == "text":
                    print(content.text, flush=True)
        except Exception as e:
            print(f"!! Tool 1 error: {e}", flush=True)
            traceback.print_exc()

        # ============================================================
        # Tool 2: search_memory (hybrid)
        # ============================================================
        print("\n" + sep, flush=True)
        print("  TOOL 2: search_memory", flush=True)
        print("  Query: 'auto post worker'", flush=True)
        print("  Mode: hybrid (vector + keyword)", flush=True)
        print(sep, flush=True)

        try:
            result2 = await session.call_tool(
                "search_memory",
                {
                    "query": "auto post worker",
                    "use_vector": True,
                    "limit": 5,
                },
            )
            print("\n[Result 2]", flush=True)
            for content in result2.content:
                if content.type == "text":
                    print(content.text, flush=True)
        except Exception as e:
            print(f"!! Tool 2 error: {e}", flush=True)
            traceback.print_exc()

        # ============================================================
        # Tool 2b: search_memory (FTS5 only)
        # ============================================================
        print("\n" + sep, flush=True)
        print("  TOOL 2b: search_memory (FTS5 only)", flush=True)
        print("  Query: 'auto post'", flush=True)
        print(sep, flush=True)

        try:
            result2b = await session.call_tool(
                "search_memory",
                {
                    "query": "auto post",
                    "use_vector": False,
                    "limit": 5,
                },
            )
            print("\n[Result 2b]", flush=True)
            for content in result2b.content:
                if content.type == "text":
                    print(content.text, flush=True)
        except Exception as e:
            print(f"!! Tool 2b error: {e}", flush=True)
            traceback.print_exc()

        print("\n" + sep, flush=True)
        print("  MCP TEST COMPLETE", flush=True)
        print(sep, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
