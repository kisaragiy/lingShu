"""CLI entry point for Agent Harness.

Usage:
    agent-harness run "任务描述"              # Multi-agent mode (default)
    agent-harness run --single "任务"         # Single-agent mode
    agent-harness run --trace "任务"          # Multi-agent + tracing
    agent-harness eval                        # Run evaluation suite
    agent-harness eval --all                  # Eval including network tasks
    agent-harness serve                       # Start FastAPI server
    agent-harness mcp                         # Start MCP server
"""

import argparse
import sys
import json
import time


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
        result = run_multi_agent(
            request,
            goal=args.goal or "",
            enable_tracing=args.trace,
        )
        print(f"\n{'='*60}")
        print(result["final_output"])
        print(f"{'='*60}")
        print(f"\n⏱ {result['elapsed_s']:.1f}s | {result['rounds']} 轮 | "
              f"workers: {list(result.get('worker_results', {}).keys())}")

        # Save trace if enabled
        if args.trace and "trace_tree" in result:
            trace_path = result["trace_tree"].to_json()
            print(f"📊 Trace saved: {trace_path}")

        # Save trace if output path specified
        if args.trace_output:
            if "trace_tree" in result:
                result["trace_tree"].to_json(args.trace_output)
                print(f"📊 Trace saved: {args.trace_output}")
            else:
                print("⚠ Use --trace to enable tracing")


def cmd_eval(args):
    """Run evaluation suite."""
    from .graph_multi import run_multi_agent
    from .eval import run_eval, save_report, EVAL_DATASET, CI_TASKS

    def runner(task_request: str) -> dict:
        return run_multi_agent(
            task_request,
            enable_tracing=True,
        )

    tasks = EVAL_DATASET if args.all else CI_TASKS
    print(f"\nRunning {len(tasks)} eval tasks...")
    t0 = time.time()

    report = run_eval(
        runner_func=runner,
        tasks=tasks,
        skip_network=not args.all,
        verbose=True,
    )

    print(report.summary())
    print(f"\nTotal eval time: {time.time() - t0:.1f}s")

    # Save report
    path = save_report(report, args.output or "")
    print(f"Report saved: {path}")

    # Exit code
    if report.failed > 0:
        sys.exit(1)


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
    run_p.add_argument("--trace", action="store_true",
                       help="Enable detailed execution tracing")
    run_p.add_argument("--trace-output", help="Save trace JSON to file")
    run_p.set_defaults(func=cmd_run)

    # eval
    eval_p = sub.add_parser("eval", help="Run evaluation suite")
    eval_p.add_argument("--all", action="store_true",
                        help="Include network-dependent tasks")
    eval_p.add_argument("--output", help="Save eval report to file")
    eval_p.set_defaults(func=cmd_eval)

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
