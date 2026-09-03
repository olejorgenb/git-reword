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
`Author:` and `Date:` lines, `--continue` reopens the file from an aborted
run, `--force` discards it.

## Language server

`git-reword-lsp` speaks LSP over stdio and finds the repository by walking up
from the file, so it works on `REWORD_EDITMSG` and on `.reword` files
anywhere inside a worktree. It provides:

- diagnostics: format errors with quick-fixes, subject length, unknown shas,
  and a hint on every commit whose message changed
- document symbols (outline): one per commit, subject plus short sha
- hover on a `commit` line: author, date, original message
- code actions: revert a commit to its original message, open it in the
  forge, indent misindented lines
- document links on shas, and formatting that normalises indentation

## Development

```
uv sync
uv run pytest
uv run ruff check
uv run ty check
```
