"""CLI entry point for Agent Harness.

Usage:
    agent-harness run "搜索2026年AI新闻并总结"    # Multi-agent mode (default)
    agent-harness run --single "搜索新闻"           # Single-agent mode
    agent-harness serve                             # Start FastAPI server
    agent-harness mcp                               # Start MCP server
"""

import argparse
import sys


def cmd_run(args):
    """Execute a task through the agent harness."""
    request = args.request

    if args.single:
        print(f"[Single-Agent] {request}")
        from .graph import run as run_single
        result = run_single(request)
        print(f"\n{'='*60}")
        print(result)
        print(f"{'='*60}")
    else:
        from .graph_multi import run_multi_agent
        result = run_multi_agent(request, goal=args.goal or "")
        print(f"\n{'='*60}")
        print(result["final_output"])
        print(f"{'='*60}")
        print(f"\n⏱ {result['elapsed_s']:.1f}s | {result['rounds']} 轮 | "
              f"workers: {list(result.get('worker_results', {}).keys())}")


def cmd_serve(args):
    """Start FastAPI server."""
    from .api_fastapi import main
    main()


def cmd_mcp(args):
    """Start MCP server."""
    from .mcp_server import main
    main()


def main():
    parser = argparse.ArgumentParser(
        description="Agent Harness — LangGraph Multi-Agent Orchestration",
    )
    sub = parser.add_subparsers(dest="command")

    # run
    run_p = sub.add_parser("run", help="Execute a task")
    run_p.add_argument("request", help="Task description")
    run_p.add_argument("--goal", help="Optional goal")
    run_p.add_argument("--single", action="store_true",
                       help="Use single-agent mode (default: multi-agent)")
    run_p.set_defaults(func=cmd_run)

    # serve
    serve_p = sub.add_parser("serve", help="Start FastAPI server")
    serve_p.set_defaults(func=cmd_serve)

    # mcp
    mcp_p = sub.add_parser("mcp", help="Start MCP stdio server")
    mcp_p.set_defaults(func=cmd_mcp)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
