"""Behavior tests for the grep/graph boundary, using real ripgrep and AST extraction."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from graphify.grephty import main
from graphify.hybrid import build_index, graph_context, source_search


@pytest.fixture(autouse=True)
def require_rg():
    if not shutil.which("rg"):
        pytest.skip("ripgrep is required for Grephty integration tests")


@pytest.fixture
def corpus(tmp_path):
    (tmp_path / "lib.py").write_text("def resolve_scope():\n    return 'small'\n")
    (tmp_path / "service.py").write_text(
        "from lib import resolve_scope\n\ndef approve():\n    return resolve_scope()\n")
    (tmp_path / "api.py").write_text(
        "from service import approve\n\ndef endpoint():\n    return approve()\n")
    (tmp_path / "policy.md").write_text("Policy quote: resolve_scope must be reviewed.\n")
    return tmp_path


@pytest.fixture
def graph(corpus):
    # Parallel edges, a cycle, and an undirected flag must not reverse calls.
    data = {
        "directed": False,
        "nodes": [
            {"id": "scope", "label": "resolve_scope()", "source_file": "lib.py", "source_location": "L1"},
            {"id": "approve", "label": "approve()", "source_file": "service.py", "source_location": "L3"},
            {"id": "api", "label": "endpoint()", "source_file": "api.py", "source_location": "L3"},
            {"id": "policy", "label": "policy", "source_file": "policy.md"},
        ],
        "links": [
            {"source": "approve", "target": "scope", "relation": "calls", "confidence": "EXTRACTED"},
            {"source": "approve", "target": "scope", "relation": "imports", "confidence": "EXTRACTED"},
            {"source": "api", "target": "approve", "relation": "calls", "confidence": "EXTRACTED"},
            {"source": "scope", "target": "api", "relation": "calls", "confidence": "EXTRACTED"},
            {"source": "policy", "target": "scope", "relation": "uses", "confidence": "INFERRED"},
            {"source": "policy", "target": "scope", "relation": "calls", "confidence": "AMBIGUOUS"},
            {"source": "policy", "target": "scope", "relation": "calls"},
        ],
    }
    output = corpus / "graphify-out" / "graph.json"
    output.parent.mkdir()
    output.write_text(json.dumps(data))
    return output


def invoke(capsys, *args):
    code = main([*args, "--json"])
    streams = capsys.readouterr()
    return code, json.loads(streams.out or streams.err)


def test_literal_search_works_without_graph_or_ast_dependencies(corpus):
    code = """
import sys
from graphify.grephty import main
result = main(sys.argv[1:])
assert 'graphify.extract' not in sys.modules
assert 'networkx' not in sys.modules
raise SystemExit(result)
"""
    result = subprocess.run([sys.executable, "-c", code, "search", "resolve_scope()",
                             "--root", str(corpus), "--json"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    matches = json.loads(result.stdout)["search"]["matches"]
    assert [(m["file"], m["line"]) for m in matches] == [("lib.py", 1), ("service.py", 4)]
    assert not (corpus / "graphify-out").exists()


def test_text_document_search_returns_quote(corpus):
    result = source_search(corpus, "resolve_scope", globs=("*.md",))
    assert result["matches"][0]["text"] == "Policy quote: resolve_scope must be reviewed."


def test_search_honors_ignore_files_hidden_binary_and_generated_output(corpus):
    (corpus / ".gitignore").write_text("ignored.py\n")
    (corpus / ".grephtyignore").write_text("private.py\n")
    (corpus / ".graphifyignore").write_text("excluded.py\n")
    for name in ("ignored.py", "private.py", "excluded.py", ".hidden.py"):
        (corpus / name).write_text("resolve_scope")
    for directory in ("graphify-out", "grephty-out", "node_modules", ".venv"):
        (corpus / directory).mkdir()
        (corpus / directory / "generated.py").write_text("resolve_scope")
    (corpus / "binary.dat").write_bytes(b"\x00resolve_scope\x00")
    result = source_search(corpus, "resolve_scope")
    assert {m["file"] for m in result["matches"]} == {"lib.py", "service.py", "policy.md"}
    result = source_search(corpus, "resolve_scope", globs=("*.py",))
    # Explicit rg globs override .gitignore by design, but never generated output.
    assert not any("generated.py" in m["file"] for m in result["matches"])


def test_limit_is_global_and_truncation_is_explicit(corpus):
    result = source_search(corpus, "resolve_scope", limit=1)
    assert len(result["matches"]) == 1
    assert result["truncated"]
    assert not source_search(corpus, "Policy", limit=1)["truncated"]


def test_regex_ignore_case_and_shell_metacharacters(corpus):
    (corpus / "literal.txt").write_text('$(touch PWNED) [literal]\nUPPER\n')
    assert source_search(corpus, "$(touch PWNED)")["matches"]
    assert not (corpus / "PWNED").exists()
    assert source_search(corpus, "upper", ignore_case=True)["matches"]
    assert source_search(corpus, r"resolve_\w+", regex=True)["matches"]
    assert not source_search(corpus, r"resolve_\w+")["matches"]


def test_long_lines_clipped_and_annotated(corpus):
    (corpus / "large.txt").write_text("x" * 1000)
    match = source_search(corpus, "xxx")["matches"][0]
    assert len(match["text"]) == 500
    assert match["text_truncated"]


def test_clipped_unicode_line_keeps_the_actual_match(corpus):
    (corpus / "large.txt").write_text("é" * 1000 + "needle" + "z" * 1000)
    match = source_search(corpus, "needle")["matches"][0]
    assert "needle" in match["text"]
    assert match["text_start_column"] == 901


def test_large_sources_are_outside_search_and_index_scope(corpus):
    (corpus / "large.py").write_text("# needle\n" + "x" * (2 * 1024 * 1024))
    assert not source_search(corpus, "needle")["matches"]
    result = build_index(corpus)
    assert result["files"] == 3


def test_empty_project_and_documents_only_index(tmp_path):
    assert not source_search(tmp_path, "anything")["matches"]
    (tmp_path / "notes.md").write_text("Evidence stays text.\n")
    result = build_index(tmp_path)
    assert result["files"] == result["nodes"] == result["edges"] == 0


def test_search_does_not_follow_symlinks(corpus, tmp_path_factory, requires_symlinks):
    outside = tmp_path_factory.mktemp("outside") / "external.py"
    outside.write_text("resolve_scope")
    (corpus / "linked.py").symlink_to(outside)
    assert not any(m["file"] == "linked.py" for m in source_search(corpus, "resolve_scope")["matches"])


def test_grep_mode_ignores_even_a_corrupt_graph(corpus, graph, capsys):
    graph.write_text("invalid")
    code, result = invoke(capsys, "search", "resolve_scope", "--root", str(corpus))
    assert code == 0
    assert "graph" not in result
    assert not result["warnings"]


@pytest.mark.parametrize("graph_contents", [None, "invalid", "[]", '{"nodes": [], "edges": 5}'])
def test_hybrid_keeps_source_results_when_graph_is_unavailable(corpus, capsys, graph_contents):
    if graph_contents is not None:
        output = corpus / "grephty-out"
        output.mkdir()
        (output / "graph.json").write_text(graph_contents)
    code, result = invoke(capsys, "search", "resolve_scope", "--mode", "hybrid", "--root", str(corpus))
    assert code == 0
    assert result["search"]["matches"]
    assert result["warnings"]


def test_graph_only_trace_requires_index(corpus, capsys):
    code, result = invoke(capsys, "trace", "resolve_scope", "--root", str(corpus))
    assert code == 2
    assert "grephty index" in result["error"]


def test_incoming_trace_preserves_direction_parallel_edges_and_hop_limit(corpus, graph):
    one = graph_context(corpus, graph, "resolve_scope", hops=1)
    assert {(e["source"]["id"], e["target"]["id"]) for e in one["edges"]} == {("approve", "scope")}
    assert len(one["edges"]) == 2
    two = graph_context(corpus, graph, "resolve_scope", hops=2, relations=("calls",))
    assert [(e["source"]["id"], e["target"]["id"], e["hop"]) for e in two["edges"]] == [
        ("approve", "scope", 1), ("api", "approve", 2)]


def test_outgoing_and_both_directions_terminate_on_cycles(corpus, graph):
    result = graph_context(corpus, graph, "approve", direction="out", hops=5)
    assert len(result["edges"]) == 4
    result = graph_context(corpus, graph, "scope", direction="both", hops=5)
    assert len(result["edges"]) == 4


def test_inferred_opt_in_does_not_promote_ambiguous_or_unknown_edges(corpus, graph):
    result = graph_context(corpus, graph, "scope", include_inferred=True)
    assert {e["confidence"] for e in result["edges"]} == {"EXTRACTED", "INFERRED"}
    assert len(result["edges"]) == 3
    assert result["freshness"] == "unverified"


def test_trace_limits_and_exact_symbol_matching(corpus, graph):
    result = graph_context(corpus, graph, "scope", limit=1)
    assert len(result["edges"]) == 1
    assert result["truncated"]
    result = graph_context(corpus, graph, "resolv")
    assert not result["seeds"]


def test_ambiguous_symbols_can_be_scoped_to_file(corpus, graph):
    data = json.loads(graph.read_text())
    data["nodes"].append({"id": "other", "label": "resolve_scope()", "source_file": "other.py"})
    graph.write_text(json.dumps(data))
    assert len(graph_context(corpus, graph, "resolve_scope")["seeds"]) == 2
    result = graph_context(corpus, graph, "resolve_scope", source_file="lib.py")
    assert [n["id"] for n in result["seeds"]] == ["scope"]


def test_hybrid_can_seed_graph_separately_from_regex(corpus, graph, capsys):
    code, result = invoke(capsys, "search", r"resolve_\w+", "--regex", "--mode", "hybrid",
                          "--symbol", "scope", "--root", str(corpus))
    assert code == 0
    assert result["search"]["matches"]
    assert result["graph"]["edges"]


def test_real_index_is_code_only_and_trace_finds_indirect_caller(corpus, capsys):
    code, result = invoke(capsys, "index", "--root", str(corpus))
    assert code == 0
    assert result["files"] == 3
    data = json.loads(Path(result["graph"]).read_text())
    assert data["input_tokens"] == data["output_tokens"] == 0
    assert not any(n.get("source_file") == "policy.md" for n in data["nodes"])
    code, traced = invoke(capsys, "trace", "resolve_scope", "--include-inferred", "--hops", "2",
                          "--relation", "calls", "--root", str(corpus))
    assert code == 0
    assert traced["graph"]["freshness"] == "fresh"
    labels = {e["source"]["label"] for e in traced["graph"]["edges"]}
    assert "approve()" in labels
    assert "endpoint()" in labels


@pytest.mark.parametrize("change", ["edit", "add", "delete"])
def test_index_detects_changed_added_and_deleted_code(corpus, change):
    result = build_index(corpus)
    if change == "edit":
        (corpus / "lib.py").write_text("def renamed(): pass\n")
    elif change == "add":
        (corpus / "new.py").write_text("def new(): pass\n")
    else:
        (corpus / "lib.py").unlink()
    traced = graph_context(corpus, Path(result["graph"]), "resolve_scope")
    assert traced["freshness"] == "stale"
    assert not traced["edges"]
    assert not traced["seeds"]
    opted_in = graph_context(corpus, Path(result["graph"]), "resolve_scope", allow_stale=True)
    assert opted_in["freshness"] == "stale"
    assert opted_in["seeds"]


def test_reindex_removes_deleted_symbols(corpus):
    build_index(corpus)
    (corpus / "lib.py").write_text("def renamed(): pass\n")
    result = build_index(corpus)
    assert not graph_context(corpus, Path(result["graph"]), "resolve_scope")["seeds"]


def test_failed_extraction_is_reported_in_index_and_trace(corpus, monkeypatch):
    import importlib
    extraction = importlib.import_module("graphify.extract")
    actual = extraction.extract

    def partial(*args, **kwargs):
        result = actual(*args, **kwargs)
        result["failed_sources"] = ["lib.py"]
        return result

    monkeypatch.setattr(extraction, "extract", partial)
    result = build_index(corpus)
    assert result["failed_sources"] == ["lib.py"]
    context = graph_context(corpus, Path(result["graph"]), "approve")
    assert any("incomplete" in w for w in context["warnings"])


def test_index_refuses_to_certify_sources_changed_during_build(corpus, monkeypatch):
    import importlib
    extraction = importlib.import_module("graphify.extract")
    actual = extraction.extract

    def changed(*args, **kwargs):
        result = actual(*args, **kwargs)
        (corpus / "new.py").write_text("def surprise(): pass\n")
        return result

    monkeypatch.setattr(extraction, "extract", changed)
    with pytest.raises(ValueError, match="changed during indexing"):
        build_index(corpus)
    assert not (corpus / "grephty-out" / "graph.json").exists()


def test_graph_size_cap(corpus, graph, monkeypatch):
    monkeypatch.setattr("graphify.security._MAX_GRAPH_FILE_BYTES", 1)
    with pytest.raises(ValueError, match="byte cap"):
        graph_context(corpus, graph, "scope")


def test_exit_statuses_and_invalid_regex(corpus, capsys):
    assert invoke(capsys, "search", "not_here", "--root", str(corpus))[0] == 1
    assert invoke(capsys, "search", "[", "--regex", "--root", str(corpus))[0] == 2
    assert invoke(capsys, "search", "", "--root", str(corpus))[0] == 2


def test_missing_rg_is_actionable(corpus, monkeypatch, capsys):
    monkeypatch.setattr("graphify.hybrid.shutil.which", lambda _: None)
    code, result = invoke(capsys, "search", "scope", "--root", str(corpus))
    assert code == 2
    assert "ripgrep" in result["error"]
