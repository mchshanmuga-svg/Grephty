# Grephty

**Find the source with grep. Follow relationships with a graph.**

Grephty is a local CLI for coding assistants and developers, built on
[Graphify](https://github.com/Graphify-Labs/graphify) and
[ripgrep](https://github.com/BurntSushi/ripgrep). Use it across code, configuration,
tests, and text documentation. Exact lookups stay small; dependency questions can
follow named relationships across files.

This first version supports source search, code indexing, and bounded graph
traversal. It makes no LLM calls, requires no API key, and installs no assistant
hooks. It accepts literal strings or explicit regexes, not natural-language questions.

| Question | Tool | Result |
| --- | --- | --- |
| Where does `resolve_scope` occur? | `grephty search resolve_scope` | Matching source lines with file and line number |
| What calls this function, directly or indirectly? | `grephty trace resolve_scope --hops 2 --include-inferred` | Incoming graph relationships with provenance labels |
| Show usages plus their relationships | `grephty search resolve_scope --mode hybrid` | Source matches, followed by bounded graph context |
| Where does a document state this phrase? | `grephty search 'exact phrase' -g '*.md'` | Source text; no generated interpretation |

## Install from this fork

Requires Python 3.10+ and `rg` on PATH. Install ripgrep using your system package
manager (for example, `brew install ripgrep` on macOS).

```bash
git clone https://github.com/mchshanmuga-svg/Grephty.git
cd Grephty
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
grephty --help
```

On Windows, activate with `.venv\Scripts\Activate.ps1` in PowerShell.
The fork currently retains the upstream Python distribution name `graphifyy` and
the `graphify` command for compatibility. Install from this checkout in its own
environment; installing `graphifyy` from PyPI installs upstream, not Grephty.
`python -m graphify.grephty` is equivalent to `grephty`.

## Use it on any project

```bash
# No index required. Defaults to literal, case-sensitive source search.
grephty search resolveScope --root /path/to/project
grephty search 'resolve(Scope|Tenant)' --regex -g '*.ts' --root /path/to/project

# Build a local code graph, then follow at most two hops of incoming calls.
grephty index --root /path/to/project
grephty trace resolveScope --relation calls --hops 2 --include-inferred --root /path/to/project

# Source evidence first, then optional graph relationships; machine-readable output.
grephty search resolveScope --mode hybrid --limit 10 --json --root /path/to/project

# Outgoing dependencies; disambiguate identically named symbols.
grephty trace resolveScope --direction out --file lib/scope.ts --root /path/to/project
```

Search uses ripgrep's ignore rules, hidden/binary filtering, and regex engine.
It also reads `.grephtyignore` and `.graphifyignore` at the project root. Explicit
`--glob` filters have ripgrep's usual override semantics for ignore files.
Generated graph directories, virtual environments, and `node_modules` are always
excluded. Files above 2 MiB are excluded; symlinks are not followed. See the
[ripgrep guide](https://github.com/BurntSushi/ripgrep/blob/master/GUIDE.md).

Results default to 20 source lines and, when requested, 20 graph edges. Long
source lines are clipped around the first match to 500 characters. Truncation is
always reported. `--limit` adjusts result counts, not a tokenizer-specific token
budget. Source search has a 30-second timeout.

Graph seeds match exact labels (with or without trailing `()`) or full node IDs.
Use `--symbol` with hybrid search when the graph symbol differs from your search
pattern, for example when searching a regex. `--glob` scopes source search;
`--file` scopes graph seeds, while traversal can cross file boundaries.
Graph traversal follows `calls`, `imports`, `uses`, `inherits`, and `re_exports`
by default; repeat `--relation` to select others. `--direction` accepts `in`,
`out`, or `both`; `--hops` accepts 1–5.

## Evidence and freshness

- **Source matches** are text found in the current files. A text occurrence alone
  does not prove a call or dependency.
- **EXTRACTED** is Graphify's label for a relationship extracted from source.
  It is not human verification or proof of runtime behavior.
- **INFERRED** relationships require `--include-inferred` and retain that label.
  Code name resolution can be inferred even without an LLM.
- **AMBIGUOUS** and unlabelled relationships are excluded from this CLI.

`grephty index` parses supported code files through Graphify's AST extractors and
writes `grephty-out/graph.json`. It never runs document/media semantic extraction.
It records source hashes and reports failed/empty extractions. Rerunning `index`
refreshes the graph, reusing the AST cache, and removes deleted symbols.

Before graph queries, Grephty compares the code inventory and source hashes.
Changed, added, or deleted code makes the index stale. Stale relationships are
withheld unless you pass `--allow-stale`; live grep results still work. Freshness
checking reads the indexed code corpus, so graph queries have additional cost.

Existing Graphify graphs are also supported: use `--graph path/to/graph.json`.
Without that flag, Grephty checks `grephty-out/graph.json`, then
`graphify-out/graph.json`. Imported graphs without Grephty hashes are marked
**unverified**. Their confidence labels are inherited, including any semantic
inferences; Grephty does not validate their claims. Missing or invalid graphs do
not prevent hybrid search from returning source matches.

## Coding assistant workflow

Tell your assistant where this checkout's `.venv/bin/grephty` executable lives
(Windows: `.venv\Scripts\grephty.exe`) and give it this instruction:

> Use Grephty source search for exact names, error messages, and document quotes.
> Use trace or hybrid mode for relationship questions. Keep results bounded and
> distinguish source text from inferred edges. Read the cited source before
> changing code. Do not treat graph output as authority for regulatory claims.

Use `--json` for structured results. Exit statuses are `0` for results/success,
`1` for no matches or no eligible graph seed, and `2` for errors. JSON errors go
to stderr; indexing progress also goes to stderr. A missing graph is a warning
in hybrid mode and an error for `trace`.

The inherited `graphify install` command installs upstream's graph-first
instructions. Grephty does not need it. Its CLI can be used directly from any
assistant that can run local commands.

## Try the included example

```bash
grephty search resolve_scope --root examples/grephty-demo
grephty index --root examples/grephty-demo
grephty trace resolve_scope --hops 2 --relation calls --include-inferred --root examples/grephty-demo
```

The source search finds direct textual occurrences. The two-hop trace also
reaches `endpoint()`, which calls `approve()` and never names `resolve_scope`.
This illustrates when graph traversal adds information; it is not a performance
benchmark or a promise of token savings on every project.

## Development and upstream

```bash
python -m pip install pytest ruff
python -m pytest tests/test_grephty.py -q
ruff check graphify/grephty.py graphify/hybrid.py tests/test_grephty.py
```

See [the implementation notes](docs/GREPHTY.md) and the preserved
[Graphify README](GRAPHIFY_README.md) for upstream features, optional parsers,
visualizations, and full-suite instructions. PDF/image retrieval, natural-language
routing, and a Grephty-specific visual explorer are outside this first version.
The upstream licenses and notices remain in [LICENSE](LICENSE),
[LICENSE-MIT](LICENSE-MIT), and [NOTICE](NOTICE).
