# Plan: sha references to rewritten commits, and shas in messages

Spec: `reword-format.md`, "References to rewritten commits" (applying), and
`lsp-code-actions.md`, "Shas in messages" (server), both from commit
6c6c582. Three parts, each usable on its own: the rewrite when applying,
then links and hover in the server, then the server's hints.

## Shared: the token

`format.py` gets the token pattern, since the writer's rule is the spec's
and the server uses the same one:

```python
# A sha named in message text: 7-64 lowercase hex, not part of a longer word.
SHA_REF_RE = re.compile(r"(?<![0-9A-Za-z_])[0-9a-f]{7,64}(?![0-9A-Za-z_])")
```

Checks for the pattern:
- it matches `revert` text: `This reverts commit <40 hex>.`;
- it matches a forge URL tail: `/-/commit/<sha>` and `/commit/<sha>`;
- it does not match `defaced` inside `undefaced`, a 6-digit run, or a run
  inside an identifier such as `x1234567` or `1234567_a`.

A bare hex word such as `defaced` *does* match the pattern. That's
intended: the pattern only finds candidates. What filters them is the
lookup: a prefix of a re-minted commit when applying, a commit git knows
in the server. Don't try to exclude hex words in the regex.

## Part 1: rewriting when applying

### `git.py`

`short(sha, length, cwd) -> str` runs `git rev-parse --short=<length>
--end-of-options <sha>`. It returns at least `length` characters, more
when the prefix is ambiguous.

### `apply.py`

The re-mint loop decides which commits are re-minted as it goes. The
summary needs that set *before* anything is written, so split it out:

- `reminted(plan, changes) -> list[Commit]` gives, in history order,
  every changed commit and every commit with a re-minted parent. It is
  the same rule the loop uses now (`commit.sha in changed or any parent
  in mapped`), with a set in place of `mapped`. `apply()` then loops over
  `reminted(...)`, so the two can't drift apart.
- `Reference` is a dataclass: `commit` (the commit holding the
  reference), `start` and `end` in its message, `token` (as written),
  and `target` (the full old sha it names).
- `references(plan, changes, edits) -> tuple[list[Reference],
  list[Reference]]` returns the references to update, and the ambiguous
  ones. For each re-minted commit, in order, it runs `SHA_REF_RE` over
  the message that commit will get (`edits.get(sha) or Edit(message)`),
  as `apply()` builds it. A match counts only against commits re-minted
  *earlier* in the list. One prefix match is a reference; two or more is
  ambiguous.
- `apply(plan, changes, edits, *, rewrite_shas=True)`: just before
  `commit_tree`, if `rewrite_shas`, it replaces each reference in that
  message from the end backwards, so offsets stay valid. A full-length
  token becomes the full new sha. A shorter one becomes
  `git.short(new, len(token))`, asked once per (target, length). It
  prints one line per replacement:
  `  <holder new short> <old token> -> <new token>`.

The message passed to `commit_tree` changes, but `changes` and the
"Detected N changed commit(s)" count don't. That is the spec's "does not
count as a change".

### `cli.py`

- Add `--sha-rewrite/--no-sha-rewrite`, default on, help: "Update shas
  of rewritten commits named in messages". Typer spells the off form
  `--no-sha-rewrite`, as in the spec.
- After the per-commit diffs and before "Apply these changes?", when the
  flag is on and there are references, print:

  ```
  References to rewritten commits, updated on apply:
    1234abcd  a1b2c3d  (Fix parser)
  ```

  Each line gives the holder's short sha, the token as written, and the
  subject of the commit it names. Holders after the range are included;
  their sha is enough to identify them. Ambiguous ones follow under
  "Left as is, matches more than one rewritten commit:".
- `README.md`: one line for `--no-sha-rewrite` in the flag list.

### Tests (`tests/test_cli.py`, end to end)

The fixture repo is Base, First, Second, Third. The editor script
rewords First and appends to Third's body a reference to Second, plus
lines that must stay as they are:

```
    Reverts <Second full sha>, see <Second sha[:7]> and
    https://gitlab.com/group/repo/-/commit/<Second full sha>.
    Not shas: defaced, <Second sha[:6]>, x<Second sha[:7]>.
```

Second has to be re-minted because First changed, so its sha changes.
Assert:
- after the apply, Third's message names Second's *new* sha: full where
  full was written, 7 or more digits where 7 were written, and in the
  URL;
- the "not shas" line is unchanged;
- the summary lists the three references before the prompt, and the
  output after the apply has the `old -> new` lines;
- "Detected 2 changed commit(s)", since the rewrite isn't a change;
- the number of commits is unchanged.

Further tests:
- **`--no-sha-rewrite`:** the same edit leaves the old shas, and no
  references section is printed.
- **A descendant after the range:** run on `HEAD~3..HEAD~1` with Third
  outside the range. Third's original message (set up in the test with
  `git commit --amend` naming Second) is updated, and the summary lists
  Third.
- **A reference to a commit that isn't re-minted:** Base's sha in a
  message is left alone, because Base is never re-minted.
- **Unit tests for `references`** with fake commits, which reach what a
  repo can't easily:
  - two re-minted commits sharing a 7-digit prefix give an ambiguous
    reference;
  - a token naming a *later* re-minted commit is not a reference.
- **`git.short`** returns a prefix of the sha, at least as long as asked.
  A real collision isn't practical to construct; the lengthening is git's
  own behaviour.

## Part 2: links and hover in the server

In `analysis.py`:

- `message_shas() -> list[tuple[int, re.Match, Commit]]` gives (line,
  match, commit) for every token in a message line that names a commit.
  The lines come from each block's `message_lines` range, skipping
  comment lines: those with `#` at column 0. An indented `    # …` line
  is message text and is scanned. The commit comes from `Repo.commit`,
  which is cached per token, including misses. The method is cached on
  the `Analysis`, like `result`.
- `links()` adds a link for each one, with the same target and tooltip
  as a `commit` line (`link_target(commit.sha)`).
- `hover()` currently returns None off a `commit` line. It now first
  checks whether the position is inside one of these tokens, and if so
  answers: `` `<short>` <subject> ``, then `Author: … · <date>`, with the
  token as the range.

Tests (`tests/test_lsp.py`): in the test's own copy of the text (passed
to `analyse(path, text)`, leaving the shared `edit_file` fixture as it
is), add a message line naming the Second commit by its 7-digit and full
sha, plus "defaced". Assert:
- two links, targeting the forge URL, or the Zed URL with a Zed client;
- the hover on each token gives Second's subject;
- no link or hover on "defaced" or on a comment line naming the sha.

## Part 3: hints in the server

- `Repo.on_head(sha) -> bool` runs `git merge-base --is-ancestor <sha>
  HEAD` (exit 0 means yes, 1 means no, anything else counts as yes so a
  failure stays quiet). It is cached per sha for the life of the
  server, as the spec allows.
- `Analysis.reminted_blocks() -> set[str]` gives the shas of the blocks
  from the first changed block to the end of the file.
- `diagnostics()` (the half that uses git) adds, for each entry of
  `message_shas()`:
  - `sha-rewritten`, severity Hint, when the commit's sha is in
    `reminted_blocks()`: "Updated to the new sha on apply (unless
    --no-sha-rewrite)";
  - `sha-not-on-branch`, severity Hint, when `not on_head(sha)`: "Not on
    this branch; rewritten or dropped?".

  The range is the token. Both can't apply at once: a block's commit is
  on HEAD.

Tests:
- **Rewritten:** edit First's message, and a reference to Second in
  Third gets `sha-rewritten`. Revert the edit and the hint goes away.
- **Not on the branch:** make a commit on a side branch and name its sha
  in a message, which gets `sha-not-on-branch`. A commit that is on HEAD
  but outside the file gets neither.
- **Existing diagnostics tests:** the fixture's clean file must still
  have no diagnostics, so its text names no shas.

## Steps (one commit each)

Work on a branch, `sha-references`. Run the tests, `ruff check`,
`ruff format --check` and `ty check src` after each step; ty is clean on
`src`, while `tests` has older errors that don't need fixing. Run `uv`
with `--offline`.

1. `format.py`: `SHA_REF_RE`, with tests for the pattern.
2. `git.py`: `short`, with a test.
3. `apply.py` and `cli.py`: `reminted`, `references`, the rewrite, the
   summary, the flag and the README line, with the Part 1 tests.
4. The server: links and hover on shas in messages (Part 2).
5. The server: the hints (Part 3).

## Open points

- **Uppercase hex.** The token is lowercase only, as git writes shas.
  Someone pasting an uppercase sha won't get an update. Accepted; the
  spec says lowercase.
- **Whether to tell the user about stale shas they can't fix.** A message
  naming a commit that was rewritten earlier, outside this run, gets
  `sha-not-on-branch` in the server. The remapping action (matching
  author and author date, from the discussion before this plan) would
  hang there. It is not part of this plan.
