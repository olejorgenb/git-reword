; Capture names follow Zed's theme captures (docs/src/extensions/languages.md).

(comment) @comment
((comment) @link_uri
  (#match? @link_uri "^# +https?://"))

(commit_line "commit" @keyword)
(sha) @constant

(info_key) @property
(info_value) @string

(subject_text) @title
(overflow) @string.special

; Trailer-looking lines: "Key: value"
((text) @attribute
  (#match? @attribute "^[A-Za-z][A-Za-z0-9-]*: "))

(invalid_line) @punctuation.special
