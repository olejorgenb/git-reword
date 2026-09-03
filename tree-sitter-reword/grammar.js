// Tree-sitter grammar for the git-reword edit file.
// Format: ../prose/spec/reword-format.md
//
// Line oriented, no external scanner. `extras` is empty and every line rule
// ends in an explicit '\n', so the file must end with a newline. Column-0
// lines are structure; message lines are indented by 4 spaces or a tab.
//
// Error handling: `invalid_line` is a catch-all token with lexical precedence
// -1. Tree-sitter checks lexical precedence before match length, so it only
// wins where no other token matches. A bad line then becomes one node instead
// of derailing the rest of the block.

module.exports = grammar({
  name: 'reword',

  extras: $ => [],

  conflicts: $ => [
    [$.message_line, $.trailer_line],
  ],

  rules: {
    document: $ => seq(
      repeat(choice($.comment, $.blank_line, $.invalid_line)),
      repeat($.commit),
    ),

    commit: $ => seq(
      $.commit_line,
      repeat(choice($.comment, $.info_line, $.blank_line, $.invalid_line)),
      optional($.message),
    ),

    commit_line: $ => seq('commit', /[ \t]+/, field('sha', $.sha), optional(/[ \t]+/), '\n'),

    // The first 8 characters are a separate node so the outline can show
    // a short sha.
    sha: $ => seq(alias(/[0-9a-f]{8}/, $.short_sha), /[0-9a-f]{32}([0-9a-f]{24})?/),

    comment: $ => /#[^\n]*\n/,

    info_line: $ => seq(field('key', $.info_key), field('value', $.info_value), '\n'),
    // Key includes the colon so a bare word at column 0 never half-matches.
    info_key: $ => /[A-Za-z][A-Za-z-]*:/,
    info_value: $ => /[^\n]*/,

    // Second form: a whitespace-only line that starts with an indent. Needed
    // because `_indent` has lexical precedence and wins the first 4 spaces.
    blank_line: $ => choice(/[ \t]*\n/, seq($._indent, /[ \t]*\n/)),

    // Everything after the subject up to the next `commit` line belongs to
    // the message, comments included, so blank lines never have two possible
    // owners. The body is a sequence of paragraphs; like git, only the last
    // paragraph can be trailers. LR(1) cannot know a paragraph is the last
    // one, so `paragraph` and `trailers` are a declared GLR conflict and
    // `trailers` wins by dynamic precedence when both parses complete.
    message: $ => seq(
      $.subject,
      repeat(choice($._noise, $.paragraph)),
      optional(seq($.trailers, repeat($._noise))),
    ),

    _noise: $ => choice($.blank_line, $.comment, $.invalid_line),

    // prec.right: a comment right after a line stays inside the paragraph.
    paragraph: $ => prec.right(seq(
      $.message_line,
      repeat(choice($.message_line, $.comment, $.invalid_line)),
    )),

    trailers: $ => prec.dynamic(1, prec.right(seq(
      $.trailer_line,
      repeat(choice($.trailer_line, $.comment, $.invalid_line)),
    ))),

    // Split at 72 characters so the overflow can be highlighted.
    subject: $ => seq($._indent, $.subject_text, optional(field('overflow', $.overflow)), '\n'),
    subject_text: $ => /[^\n]{1,72}/,
    overflow: $ => /[^\n]+/,

    // A body line that looks like a trailer lexes the same way as a real
    // trailer (key + rest) so both GLR branches consume identical tokens.
    message_line: $ => seq($._indent, choice($.text, seq($.trailer_key, $.text)), '\n'),
    trailer_line: $ => seq($._indent, field('key', $.trailer_key), field('value', $.text), '\n'),

    text: $ => /[^\n]+/,
    // Precedence 1 so it beats `text` on lines that start with `Key: `.
    trailer_key: $ => token(prec(1, /[A-Za-z][A-Za-z0-9-]*:[ \t]/)),

    // Precedence 1 so the lexer stops here instead of letting `blank_line`
    // keep the DFA alive until only `invalid_line` can match. Without it a
    // line with 8 leading spaces lexes as `invalid_line`.
    _indent: $ => token(prec(1, /    |\t/)),

    invalid_line: $ => token(prec(-1, /[^\n]+\n/)),
  },
});
