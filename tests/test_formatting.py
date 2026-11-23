"""Markdown → MAX HTML, plain fallback, tables."""

from __future__ import annotations

from max_messenger.outbound.formatting import find_tables, table_as_text, to_max_html, to_plain


def test_escapes_and_keeps_snake_case():
    assert to_max_html("use snake_case_name and 2*3*4 < 5 & ok") == "use snake_case_name and 2*3*4 &lt; 5 &amp; ok"


def test_inline_marks():
    html = to_max_html("**bold** *it* _it2_ ~~gone~~ `x<y`")
    assert html == "<b>bold</b> <i>it</i> <i>it2</i> <s>gone</s> <code>x&lt;y</code>"


def test_code_fence_becomes_pre_and_keeps_markers_inside():
    html = to_max_html("Run:\n```bash\necho **not bold** <x>\n```\ndone")
    assert html == "Run:\n<pre>echo **not bold** &lt;x&gt;</pre>\ndone"


def test_links_only_with_safe_schemes():
    assert to_max_html("[site](https://a.b/c_d)") == '<a href="https://a.b/c_d">site</a>'
    # An unsafe scheme is never a link: the Markdown stays visible as plain text.
    assert to_max_html("[x](javascript:alert(1))") == "[x](javascript:alert(1))"


def test_headings_quotes_lists():
    html = to_max_html("# Title\n> quoted\n> more\n- one\n  - two\n---")
    assert html.splitlines() == ["<b>Title</b>", "<blockquote>quoted", "more</blockquote>", "• one",
                                 "  • two", "──────────"]


def test_table_becomes_aligned_columns():
    md = "| Name | Qty |\n|---|---:|\n| apple | 3 |\n| **pear** | 12 |"
    assert find_tables(md) == [md.splitlines()]
    text = table_as_text(md.splitlines())
    assert text.splitlines()[0] == "Name   Qty"
    assert text.splitlines()[3] == "pear   12"
    assert to_max_html(md).startswith("<pre>Name   Qty")


def test_plain_fallback_strips_markers():
    assert to_plain("**a** [b](https://c) `d`\n# T") == "a b (https://c) d\nT"
