# Code review: all Python sources

Scope: `src/git_reword/**` and `tests/**` read in full. Baseline: 53 tests
pass, ruff clean, `ty check` reports 20 errors, all in `tests/test_lsp.py`.

Goal per the user: robust but not overly defensive, readable, fully
type-hinted; tests where they make sense without heavy mocking.

Items are ordered by importance. Each says what is wrong, why it matters,
and the proposed change. Nothing is implemented yet.

## 1. Bugs (reproduced)

### 1.1 `git.get_commit` crashes on an empty commit message

`out.split("\n", 4)` yields four parts when `%B` is empty because `run`
strips trailing whitespace. `git commit --allow-empty-message` is rare but
legal, and the tool's whole point is fixing messages.

Reproduced: `ValueError: not enough values to unpack (expected 5, got 4)`.

Fix: `full, short, author, date, *rest = ...; message = rest[0] if rest
else ""`. Test with the repo fixture plus one empty-message commit.

### 1.2 Two commits with the same original message get the same new message

`apply._message_editor` keys the mapping by original message, so for two
`wip` commits reworded to `X` and `Y` both become `Y`.

Reproduced in a scratch repo.

Fix: the message editor consumes an ordered list of `(original, new)`
pairs from a JSON state file; the first pair whose original matches is
used and removed. Rebase replays oldest first, the same order as
`changed_commits`. Add an end-to-end test with two identical subjects.

## 2. Safety (check before mutate)

### 2.1 Merge commits in the rebased span are flattened silently

`apply` runs `git rebase -i <parent of first>` without `--rebase-merges`.
If `<parent>..HEAD` contains a merge (a branch that had `main` merged in,
or merged sub-branches as the project's own git conventions prescribe),
the todo list drops the merges and replays both sides linearly. The
sequence editor is scripted, so nothing is shown; the printed pre-reword
HEAD is the only way back.

Fix: a `rebase_base(commits)` helper in `apply.py` that resolves the
parent and raises `GitError` when the span contains merges (via
`git rev-list --merges`) or the first commit is the root. `reword` calls
it before opening the editor (fail early, no wasted editing) and `apply`
calls it again right before the rebase. Test: fixture repo plus a
`--no-ff` merge; `git reword` refuses and history is unchanged.

### 2.2 Sequence editor does not verify it marked every commit

If a reword sha is missing from the todo the rebase runs anyway and the
edit is lost without a word. The script should exit 1 with a message when
any sha was not marked; git then aborts the rebase.

### 2.3 Arbitrary tokens reach `git log`

`Repo.commit(sha)` is called for every block, including ones whose sha
failed validation, and passes the token as an argument to `git log`.
Return None early unless the token matches the sha regex. It also saves a
subprocess per bad line on every keystroke.

## 3. Robustness

### 3.1 `git.repo_url` only understands `git@host:path` and https

`ssh://git@gitlab.com/group/repo.git` comes back as an `ssh://` URL and
the link comment and "open in browser" are broken. A local path remote
is turned into a nonsense URL too.

Fix: one regex over `[scheme://][user@]host[:/]path[.git]` producing
`https://host/path`, None when it does not match (local paths, `file://`).
Pure function, table-driven test.

### 3.2 Prompts crash on closed stdin

`input()` raises `EOFError` when stdin is not a terminal (piped, CI). Add
a `confirm(prompt, default)` helper used by both prompts; EOF counts as
"no". This also unifies the two prompts (one accepts only `y`, the other
loops on bad input).

### 3.3 Explicit encodings

`subprocess.run(text=True)` and `read_text`/`write_text` use the locale
encoding; git output and the edit file are UTF-8. A non-ASCII subject
under a C locale is a `UnicodeDecodeError`. Pass `encoding="utf-8"` in
`git.run`, for the edit file, and inside the generated editor scripts.

### 3.4 Generated rebase scripts

- `python3 <path>` assumes a `python3` on PATH and breaks on a temp path
  with spaces. Use `sys.executable` and `shlex.quote`.
- Two `NamedTemporaryFile`s with manual unlinking; a
  `TemporaryDirectory` context does the same in fewer lines and also
  hosts the state file from 1.2.

## 4. Correctness nits

- `_check_subject`: the too-long warning column assumes a 4-space indent
  (a tab-indented subject is off by three) and counts trailing
  whitespace. Pass the indent width and measure `subject.rstrip()`.
- `cli.resolve` reports a bad sha three times (parse error, "unknown
  commit", "missing commits"). Skip blocks whose sha failed validation in
  the matching loop; the parse error and the missing report remain.
- `get_commits` runs one `git log` per commit, so the "more than 100
  commits" warning appears only after 100 subprocesses. One
  `git log --format=...%x00` over the range gives the same data in one
  call; `get_commit` becomes the `-n 1` case of the same parser.

## 5. Readability

- `analysis.py` reaches into `format.py` privates: `_SHA_RE`, `_INFO_RE`,
  `_TRAILER_RE`, `_strip_indent`. Make them public (`SHA_RE`, ...).
- `chr(10)` in two f-strings dates from before 3.12; write `"\n"`.
- `Block.subject` property, mirroring `Commit.subject`; used by symbols
  and folding.
- `open_editor`: `shlex.split(editor)` unconditionally; the `" " in
  editor` branch changes nothing.
- `git.get_commit` imports `cleanup` inside the function to dodge a
  cycle; `format.py` only needs `Commit` for annotations, so put that
  import under `TYPE_CHECKING` and import normally.
- `reword_command` ends with `sys.exit`; `raise typer.Exit(1)` like the
  other exits.
- `tests/test_cli.py::_resolver` returns a closure that takes `capsys`;
  a plain helper taking `(text, capsys)` reads better.

## 6. Typing

`ty check` fails on `tests/test_lsp.py` only: `action.edit.changes`,
`hover.contents.value`, `command.arguments`, `link.target` are Optional
or unions in lsprotocol. Add two small test helpers that assert and
narrow (`edits_of(action)`, `command_of(action)`) rather than sprinkling
`assert x is not None`. Annotate `capsys: pytest.CaptureFixture[str]`
and `tmp_path_factory: pytest.TempPathFactory`.

Source packages are clean under ty; no changes needed there.

## 7. Noted, not proposed

- Root commit in range: refusing is fine for now; `git rebase -i --root`
  would handle it and is a small follow-up (prose/idea).
- `Repo._commits` in the language server is never invalidated. Commits
  are immutable objects; only "unknown → known" could go stale, and that
  needs the user to create the commit while the file is open.
- `sha_range` finds the sha with `str.index`; safe because `commit ` has
  no run of four hex digits, so the first match is the token.
- No forge fallback to a non-origin remote (earlier loose end), unchanged.

## Order of work

Each item is its own commit, tests in the same commit as the fix:

1. 1.1 empty message, with `get_commits` single-call rewrite (4.3) since
   they touch the same parser.
2. 1.2 ordered message mapping + 2.2 sequence editor check + 3.4 script
   handling (all in `apply.py`).
3. 2.1 merge/root check.
4. 3.1 `repo_url`.
5. 3.2 `confirm` helper, 3.3 encodings.
6. 2.3, 4.1, 4.2 small correctness fixes.
7. 5 readability, 6 typing.
