; Zed outline: one item per commit, "commit <short sha> <subject>".

(commit
  (commit_line
    "commit" @context
    (sha (short_sha) @context))
  (message (subject (subject_text) @name))?) @item
