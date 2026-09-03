# git-reword

Bulk edit git commit messages in your editor.

```
git reword                  # all commits on the current branch
git reword abc123           # a single commit
git reword HEAD~5..HEAD     # a range
```

Commit messages for the range are written to `.git/REWORD_EDITMSG`, opened
in `$EDITOR`, and the changed ones are applied with an interactive rebase.

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

## Development

```
uv sync
uv run pytest
uv run ruff check
uv run ty check
```
