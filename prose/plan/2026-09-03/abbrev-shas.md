# Plan: abbreviated shas (`--abbrev` / `--no-abbrev`)

Spec: `prose/spec/reword-format.md` (cae7d82), "Line classification",
"Writing", "Reading back".

## Steps

1. **git.py.** `Commit.short: str` (git's `%h`). `get_commit` asks for
   `%H%n%h%n...` and always stores the full sha in `sha`, so a lookup by
   abbreviation still yields a full-sha `Commit`.
2. **format.py.** `_SHA_RE` accepts `[0-9a-f]{4,64}`; the `bad-sha`
   message becomes "Not a sha". `write(..., abbrev=True)` emits
   `commit.short` when set, else the full sha. The `--commit-link` comment
   keeps the full sha.
3. **cli.py.** `--abbrev/--no-abbrev` (default abbrev) passed to `write`.
   `validate()` becomes `resolve()`: maps each block to a range commit by
   prefix, reports unknown / ambiguous / duplicate / missing / order errors,
   and returns `{full sha: message}` for `apply`. Messages keyed by block
   sha are no longer used by the CLI.
4. **Language server.** `Analysis` uses the resolved `Commit.sha` for
   links, URLs and titles when the commit is known, the written token
   otherwise. `_SHA_RE` change covers the well-formed check.
5. **Grammar.** `sha` becomes `short_sha` (4–8 hex) followed by an optional
   run of hex digits; the `commit_line` still requires end of line after
   optional whitespace, so `commit abc` stays an `invalid_line`. Regenerate
   `src/`, add a corpus case, keep queries as they are (`short_sha` is
   still the first node). Then bump `rev` in `zed-reword/extension.toml`;
   the user reinstalls the dev extension.
6. **Tests.** Format: write with and without abbrev, parse accepts
   abbreviations, 3-hex token is `bad-sha`. CLI: default run rewords through
   abbreviated shas; `--no-abbrev` writes full ones; ambiguous / unknown
   prefixes are rejected. LSP: fixture written abbreviated; links carry the
   full sha; unknown abbreviation is `unknown-sha`.
7. **Docs.** README flag list and example; extension README unchanged.

## Not doing

- Editing shas to move commits: still an error (order check).
- Resolving abbreviations against the whole repository in the CLI: the
  range is the only valid universe, and it avoids a git call per block.
