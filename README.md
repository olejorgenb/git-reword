# git-reword

Bulk edit git commit messages in your editor.

```
git reword                  # all commits on the current branch
git reword abc123           # a single commit
git reword HEAD~5..HEAD     # a range
```

Commit messages for the range are written to a file, opened in `$EDITOR`,
and the changed ones are applied with an interactive rebase.

The file format is specified in `prose/spec/reword-format.md`.

## Development

```
uv sync
uv run pytest
uv run ruff check
uv run ty check
```
