"""Grephty's assistant-friendly CLI. Run with `python -m graphify.grephty`."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from graphify.hybrid import (
    DEFAULT_RELATIONS, OUTPUT_DIR, build_index, graph_context, source_search,
)


def _positive(raw: str) -> int:
    value = int(raw)
    if not 1 <= value <= 1000:
        raise argparse.ArgumentTypeError("must be between 1 and 1000")
    return value


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(
        prog="grephty", description="Exact source search with optional, bounded graph context.")
    sub = cli.add_subparsers(dest="command", required=True)
    search = sub.add_parser("search", help="Find literal source text; optionally add graph context")
    search.add_argument("pattern")
    search.add_argument("--mode", choices=("grep", "hybrid"), default="grep")
    search.add_argument("--regex", action="store_true", help="Use ripgrep regex instead of literal text")
    search.add_argument("-i", "--ignore-case", action="store_true")
    search.add_argument("-g", "--glob", action="append", default=[], help="Limit source search files")
    search.add_argument("--symbol", help="Exact graph seed when different from the search pattern")
    trace = sub.add_parser("trace", help="Follow incoming or outgoing symbol relationships")
    trace.add_argument("symbol")
    index = sub.add_parser("index", help="Build a local code graph; no LLM or API calls")
    for command in (search, trace, index):
        command.add_argument("--root", type=Path, default=Path("."), help="Project directory")
        command.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    for command in (search, trace):
        command.add_argument("--limit", type=_positive, default=20, help="Maximum matches and graph edges")
        command.add_argument("--graph", type=Path, help="Existing graph (relative to --root)")
        command.add_argument("--direction", choices=("in", "out", "both"), default="in")
        command.add_argument("--hops", type=int, choices=range(1, 6), default=1)
        command.add_argument("--relation", action="append", help="Edge relation to follow; repeatable")
        command.add_argument("--file", help="Disambiguate graph seeds by source file")
        command.add_argument("--include-inferred", action="store_true")
        command.add_argument("--allow-stale", action="store_true")
    return cli


def _location(node: dict) -> str:
    return f"{node.get('file') or '?'}:{node.get('location') or '?'}"


def render(result: dict) -> str:
    if result["command"] == "index":
        return (f"Indexed {result['files']} files: {result['nodes']} nodes, {result['edges']} edges.\n"
                f"Graph: {result['graph']}\n"
                f"Failed/empty sources: {len(result['failed_sources'])}")
    lines = []
    if "search" in result:
        search = result["search"]
        for match in search["matches"]:
            suffix = " … [line clipped]" if match["text_truncated"] else ""
            prefix = "… " if match["text_start_column"] > 1 else ""
            lines.append(f"{match['file']}:{match['line']}: {prefix}{match['text']}{suffix}")
        if not search["matches"]:
            lines.append("No source matches within the search scope.")
        if search["truncated"]:
            lines.append("[More source matches exist; narrow the search or increase --limit.]")
    if "graph" in result:
        graph = result["graph"]
        lines.append(f"Graph: {graph['file']} ({graph['freshness']})")
        for seed in graph["seeds"]:
            lines.append(f"Seed: {seed['label']} [{_location(seed)}] id={seed['id']}")
        for edge in graph["edges"]:
            source, target = edge["source"], edge["target"]
            lines.append(f"{source['label']} [{_location(source)}] --{edge['relation']}--> "
                         f"{target['label']} [{_location(target)}] "
                         f"[{edge['confidence']}; hop {edge['hop']}]")
        if graph["seeds"] and not graph["edges"]:
            lines.append("No relationships found within the selected graph filters.")
        if graph["truncated"]:
            lines.append("[Graph result truncated; narrow the seed or increase --limit.]")
        lines.extend(f"Note: {warning}" for warning in graph["warnings"])
    lines.extend(f"Note: {warning}" for warning in result.get("warnings", []))
    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict:
    root = args.root.resolve()
    if not root.is_dir():
        raise ValueError(f"Project directory does not exist: {root}")
    result = {"schema_version": 1, "command": args.command, "root": str(root), "warnings": []}
    if args.command == "index":
        result.update(build_index(root))
        return result
    if args.command == "search":
        if not args.pattern or "\n" in args.pattern or "\r" in args.pattern:
            raise ValueError("Use a nonempty, single-line search pattern.")
        result["search"] = source_search(root, args.pattern, regex=args.regex,
                                         ignore_case=args.ignore_case, globs=tuple(args.glob),
                                         limit=args.limit)
        if args.mode == "grep":
            return result
    symbol = args.symbol if args.command == "trace" else (args.symbol or args.pattern)
    path = root / args.graph if args.graph else root / OUTPUT_DIR / "graph.json"
    if not args.graph and not path.exists():
        path = root / "graphify-out" / "graph.json"
    try:
        result["graph"] = graph_context(
            root, path, symbol, direction=args.direction, hops=args.hops, limit=args.limit,
            include_inferred=args.include_inferred,
            relations=tuple(args.relation or DEFAULT_RELATIONS), source_file=args.file,
            allow_stale=args.allow_stale,
        )
    except (OSError, ValueError) as exc:
        if args.command == "trace":
            raise ValueError(f"Cannot read graph: {exc}. Run grephty index --root {root}.") from exc
        result["warnings"].append(f"Graph unavailable: {exc}. Source matches are still usable.")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = run(args)
        print(json.dumps(result, ensure_ascii=False) if args.json else render(result))
        if args.command == "trace":
            return 0 if result["graph"]["seeds"] else 1
        if args.command == "search":
            return 0 if result["search"]["matches"] or result.get("graph", {}).get("seeds") else 1
        return 0
    except BrokenPipeError:
        return 0
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        if args.json:
            print(json.dumps({"schema_version": 1, "error": str(exc)}), file=sys.stderr)
        else:
            print(f"grephty: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
