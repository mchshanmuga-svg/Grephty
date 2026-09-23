# Working on Grephty

Read [AGENTS.md](AGENTS.md) for repository instructions and [README.md](README.md)
for the Grephty commands. The upstream Graphify documentation is preserved in
[GRAPHIFY_README.md](GRAPHIFY_README.md).

For exact source names, errors, and document quotes, use grep or `grephty search`.
Use `grephty trace` or hybrid search when relationships across files are relevant.
Keep graph results bounded and distinguish source evidence from inferred edges.
Verify the cited source before making code changes. Do not treat graph output
as authority for regulatory claims.

Run the local CLI with `.venv/bin/grephty` or
`.venv/bin/python -m graphify.grephty`. The first version calls no LLM APIs and
does not require an assistant integration or installed hooks.
