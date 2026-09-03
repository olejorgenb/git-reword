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

; Trailers: "Key: value" lines in the last paragraph only, like git.
(trailer_line
  key: (trailer_key) @attribute
  value: (text) @string)

(invalid_line) @punctuation.special
