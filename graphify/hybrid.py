"""Local source retrieval and bounded, provenance-labelled graph traversal.

Search does not import the AST stack or read a graph. Indexing reuses Graphify's
deterministic extractors; it never invokes a semantic backend or an installer.
"""
from __future__ import annotations

from collections import defaultdict, deque
from contextlib import redirect_stdout
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from threading import Timer


OUTPUT_DIR = "grephty-out"
DEFAULT_RELATIONS = ("calls", "imports", "uses", "inherits", "re_exports")
EXCLUDED_DIRS = (".git", "node_modules", ".venv", "venv", "__pycache__",
                 "graphify-out", OUTPUT_DIR)


def _rg(root: Path, globs: tuple[str, ...] = ()) -> list[str]:
    executable = shutil.which("rg")
    if not executable:
        raise ValueError("ripgrep (rg) is required. Install it and make rg available on PATH.")
    command = [executable, "--no-config", "--no-follow", "--no-require-git", "--sort", "path",
               "--max-filesize", "2M"]
    for glob in globs:
        command += ["--glob", glob]
    for directory in EXCLUDED_DIRS:
        command += ["--glob", f"!**/{directory}/**"]
    for name in (".grephtyignore", ".graphifyignore"):
        if (root / name).is_file():
            command += ["--ignore-file", str(root / name)]
    return command


def source_search(root: Path, pattern: str, *, regex: bool = False,
                  ignore_case: bool = False, globs: tuple[str, ...] = (),
                  limit: int = 20) -> dict:
    """Stream rg JSON, stopping after limit+1 matches, without shell evaluation."""
    command = _rg(root, globs) + ["--json", "--line-number"]
    if not regex:
        command.append("--fixed-strings")
    if ignore_case:
        command.append("--ignore-case")
    command += ["--", pattern, "."]
    matches = []
    truncated = False
    # A file prevents stderr pipe deadlocks while stdout is streamed.
    with tempfile.TemporaryFile() as errors:
        with subprocess.Popen(command, cwd=root, stdout=subprocess.PIPE,
                              stderr=errors, text=True, encoding="utf-8",
                              errors="replace") as process:
            timeout = Timer(30, process.kill)
            timeout.start()
            try:
                for line in process.stdout:
                    event = json.loads(line)
                    if event["type"] != "match":
                        continue
                    if len(matches) == limit:
                        truncated = True
                        process.terminate()
                        break
                    data = event["data"]
                    # rg uses base64 for non-UTF8 data. Don't invent source text.
                    if "text" not in data["path"] or "text" not in data["lines"]:
                        continue
                    snippet = data["lines"]["text"].rstrip("\r\n")
                    offset = data["submatches"][0]["start"]
                    column = len(snippet.encode("utf-8")[:offset].decode("utf-8", errors="replace"))
                    start = max(0, column - 100) if len(snippet) > 500 else 0
                    matches.append({
                        "file": Path(data["path"]["text"]).as_posix(),
                        "line": data["line_number"], "text": snippet[start:start + 500],
                        "text_start_column": start + 1,
                        "text_truncated": len(snippet) > 500,
                    })
                code = process.wait()
            finally:
                timeout.cancel()
                if process.poll() is None:
                    process.kill()
                    process.wait()
            errors.seek(0)
            error = errors.read().decode("utf-8", errors="replace").strip()
    if not truncated and code not in (0, 1):
        raise ValueError(error or "Source search failed or exceeded the 30 second timeout.")
    if error:
        raise ValueError(error)
    return {"matches": matches, "truncated": truncated}


def _source_files(root: Path) -> list[Path]:
    from graphify.detect import CODE_EXTENSIONS

    result = subprocess.run(_rg(root) + ["--files", "--null", "."], cwd=root,
                            capture_output=True, timeout=30)
    if result.returncode not in (0, 1):
        raise ValueError(result.stderr.decode("utf-8", errors="replace"))
    files = []
    extensions = {ext.lower() for ext in CODE_EXTENSIONS}
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        path = root / raw.decode("utf-8")
        if path.suffix.lower() in extensions:
            if path.resolve().is_relative_to(root) and not path.is_symlink():
                files.append(path)
    return files


def _snapshot(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in _source_files(root)}


def build_index(root: Path) -> dict:
    from graphify.extract import extract
    from graphify.paths import write_json_atomic

    before = _snapshot(root)
    # Extractor progress belongs on stderr, so --json remains machine readable.
    with redirect_stdout(sys.stderr):
        graph = extract([root / name for name in before], root=root,
                        cache_root=root / OUTPUT_DIR, parallel=False)
    if before != _snapshot(root):
        raise ValueError("Sources changed during indexing; rerun grephty index.")
    graph["grephty"] = {"version": 1, "root": str(root), "file_hashes": before}
    output = root / OUTPUT_DIR / "graph.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(output, graph, indent=2)
    return {"graph": str(output), "files": len(before), "nodes": len(graph["nodes"]),
            "edges": len(graph["edges"]), "failed_sources": graph.get("failed_sources", [])}


def load_graph(path: Path) -> dict:
    from graphify.security import check_graph_file_size_cap

    check_graph_file_size_cap(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        raise ValueError("Graph must contain a nodes array.")
    edges = data.get("edges", data.get("links"))
    if not isinstance(edges, list):
        raise ValueError("Graph must contain an edges or links array.")
    if any(not isinstance(n, dict) or not isinstance(n.get("id"), (str, int))
           for n in data["nodes"]):
        raise ValueError("Every graph node must have a string or integer id.")
    if any(not isinstance(e, dict) or
           not isinstance(e.get("source"), (str, int)) or
           not isinstance(e.get("target"), (str, int)) for e in edges):
        raise ValueError("Every graph edge must have source and target ids.")
    data["edges"] = edges
    return data


def _symbol(value: str) -> str:
    return value.removesuffix("()")


def graph_context(root: Path, path: Path, symbol: str, *, direction: str = "in",
                  hops: int = 1, limit: int = 20, include_inferred: bool = False,
                  relations: tuple[str, ...] = DEFAULT_RELATIONS,
                  source_file: str | None = None, allow_stale: bool = False) -> dict:
    data = load_graph(path)
    warnings = []
    metadata = data.get("grephty")
    freshness = "unverified"
    if isinstance(metadata, dict) and isinstance(metadata.get("file_hashes"), dict):
        freshness = "fresh" if metadata["file_hashes"] == _snapshot(root) else "stale"
        if freshness == "stale":
            warnings.append("Index differs from current sources; run grephty index.")
    else:
        warnings.append("Graph has no Grephty source hashes; freshness is unverified.")
    if data.get("failed_sources"):
        warnings.append(f"Index is incomplete: {len(data['failed_sources'])} source(s) failed extraction.")
    result = {"file": str(path), "freshness": freshness, "seeds": [], "edges": [],
              "truncated": False, "warnings": warnings}
    if freshness == "stale" and not allow_stale:
        warnings.append("Stale relationships withheld; --allow-stale displays them explicitly.")
        return result

    nodes = {str(node["id"]): node for node in data["nodes"]}

    def describe(node_id: str) -> dict:
        node = nodes[node_id]
        return {"id": node_id, "label": str(node.get("label", node_id)),
                "file": node.get("source_file"), "location": node.get("source_location")}

    def same_file(node: dict) -> bool:
        if source_file is None:
            return True
        file = node.get("source_file")
        return isinstance(file, str) and (root / file).resolve() == (root / source_file).resolve()

    seeds = sorted(node_id for node_id, node in nodes.items()
                   if (node_id == symbol or _symbol(str(node.get("label", ""))) == _symbol(symbol))
                   and same_file(node))
    if not seeds:
        warnings.append("No exact graph symbol matched; use its label or full node id.")
        return result
    if len(seeds) > 1:
        warnings.append(f"{len(seeds)} symbols matched; use --file or a full node id to disambiguate.")
    result["truncated"] = len(seeds) > limit
    seeds = seeds[:limit]
    result["seeds"] = [describe(seed) for seed in seeds]
    adjacency = defaultdict(list)
    allowed = {"EXTRACTED", "INFERRED"} if include_inferred else {"EXTRACTED"}
    for i, edge in enumerate(data["edges"]):
        if not isinstance(edge.get("confidence"), str) or edge["confidence"] not in allowed:
            continue
        if edge.get("relation") not in relations:
            continue
        source, target = str(edge["source"]), str(edge["target"])
        if source not in nodes or target not in nodes:
            continue
        if direction in ("in", "both"):
            adjacency[target].append((source, i))
        if direction in ("out", "both"):
            adjacency[source].append((target, i))
    queue = deque((seed, 0) for seed in seeds)
    visited = set(seeds)
    emitted = set()
    while queue:
        node_id, depth = queue.popleft()
        if depth >= hops:
            continue
        for neighbor, i in adjacency[node_id]:
            if i not in emitted:
                if len(result["edges"]) >= limit:
                    result["truncated"] = True
                    return result
                edge = data["edges"][i]
                result["edges"].append({
                    "source": describe(str(edge["source"])),
                    "target": describe(str(edge["target"])),
                    "relation": edge["relation"], "confidence": edge["confidence"],
                    "hop": depth + 1, "file": edge.get("source_file"),
                    "location": edge.get("source_location"),
                })
                emitted.add(i)
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, depth + 1))
    return result
