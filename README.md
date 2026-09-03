# git-reword

Bulk edit git commit messages in your editor.

```
git reword                  # all commits on the current branch
git reword abc123           # a single commit
git reword HEAD~5..HEAD     # a range
```

Commit messages for the range are written to `REWORD_EDITMSG` at the
worktree root, opened in `$EDITOR`, and the changed ones are applied with an
interactive rebase. The file is transient; the tool adds its name to
`.git/info/exclude` on first use unless `.gitignore` already covers it.

The file looks like `git log` output. Column 0 is structure, indented lines
are the message:

```
commit 7dcfdad1afb39b697a8632f0c450c555abe7d5b6
    test-env-cli: refactor the CLI interface

    The previous subcommand-based interface made it difficult to
    perform multiple operations in a single invocation.
```

`#` lines at column 0 are comments. Inside a message, `#` is just content.
The full format is specified in `prose/spec/reword-format.md`.

Flags: `--commit-link` adds a forge URL comment per commit, `--info` adds
`Author:` and `Date:` lines, `--stat` adds the files each commit touched
as comment lines (a summary, then one `M`/`A`/`D`/`R` line per file; merge
commits get none), `--no-abbrev` writes full shas instead of
git's abbreviations (`--abbrev`, the default), `--continue` reopens the
file from an aborted run, `--force` discards it. Abbreviated shas are
resolved against the commits of the range when the file is read back;
an ambiguous or unknown prefix is an error.

Merge commits in the rebased span are kept: git-reword rebases with
`--rebase-merges` whenever the range contains one, unless
`rebase.rebaseMerges` is explicitly set to false, in which case it warns
that the rebase would flatten them and asks before continuing. Merge
commit messages can be reworded like any other commit's.

## Language server

`git-reword-lsp` speaks LSP over stdio and finds the repository by walking up
from the file, so it works on `REWORD_EDITMSG` and on `.reword` files
anywhere inside a worktree. It provides:

- diagnostics: format errors with quick-fixes, subject length, unknown shas,
  and a hint on every commit whose message changed
- document symbols (outline): one per commit, subject plus short sha
- hover on a `commit` line: author, date, original message
- code actions: revert a commit to its original message, reflow the
  paragraph under the cursor to 72 columns, indent misindented lines, open
  the commit in the forge (from `origin`), and in Zed open it in Zed's
  commit view
- document links on shas, and formatting that normalises indentation
- folding ranges that keep the subject visible: fold the body under a
  subject, or a whole block down to its `commit` line with the subject as
  placeholder; runs of comment lines fold to their first line, so a
  `--stat` block collapses to its summary (Zed needs
  `document_folding_ranges: "on"` for the language)

Opening a commit uses `window/showDocument` when the editor supports it.
Zed does not, so there the server runs `xdg-open` (or `open` on macOS) for
forge URLs and the `zed` CLI for `zed://git/commit/...` URLs; `zed` must be
on the PATH the server sees.

## Development

```
uv sync
uv run pytest
uv run ruff check
uv run ty check
```
