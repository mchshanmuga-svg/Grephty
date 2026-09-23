# Grephty

Grephty adds source-first retrieval to the forked Graphify codebase.
Read README.md for the CLI and docs/GREPHTY.md for implementation notes.

- Use grep or `grephty search` for exact names, errors, and document quotes.
- Use `grephty trace` or hybrid search for relationship questions; keep the
  output bounded and retain confidence and freshness labels.
- Never present inferred edges as human-verified requirements.
- Grephty's index command must stay local and code-only, without LLM calls.
- Run `python -m pytest tests/test_grephty.py -q` after changes to Grephty.

## Inherited Graphify architecture workflow

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- After modifying code files in this session, run `graphify update .` to keep the graph current (AST-only, no API cost)
