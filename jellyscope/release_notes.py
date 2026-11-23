"""Release notes from markdown to HTML - the window of a new version shows them.

The notes are GitHub's description of a release: a foreign text. So the whole text is escaped FIRST
(html.escape), and only then the markdown the release notes really use turns into our own tags - no tag
of the text can get into the page, an address only ever becomes a link with http(s).

Blocks: headings (# to ### the window's big h4 - the CHANGELOG's sections are ###, ## being the version;
#### and deeper h5), paragraphs (lines joined until an empty line), bullets (-, *, +) and numbered lists
nested by the indent (at most three levels), a bullet continued on the next line, quotes (>), code blocks
(```, their lines untouched) and rules (---).
In a line: **bold**, *italics*, ~~struck~~, `code`, [links](https://...), bare addresses, #123 and @login
the way GitHub links them; an image is shown as a link (it would load from a foreign server). Tables and
raw HTML are not converted - they stay text. Nothing here raises: what is not understood is text.

No library on purpose (requirements.txt: as few as possible) - this is the whole of it.
"""
from __future__ import annotations

import html
import re

GITHUB = "https://github.com"
LINK = ' target="_blank" rel="noopener noreferrer"'
MAX_DEPTH = 3                          # bullets and quotes nested deeper stay at the third level

# Put-aside pieces: \x01 around code (it may be a link's text), \x00 around a link or a reference (never inside
# another link). Neither can come from the text - to_html drops both characters first.
_TOKEN = re.compile("[\x00\x01](\\d+)[\x00\x01]")
_FENCE = re.compile(r"^\s{0,3}```[^`]*$")         # ```deploy``` inside a line is code, not a block
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")
_RULE = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")
_QUOTE = re.compile(r"^\s{0,3}&gt;\s?(.*)$")
_ITEM = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")
_CODE = re.compile(r"(?<!`)(`{1,3})(?!`)([^`]+)\1(?!`)")
_LABEL = r"((?:[^\[\]\x00\x01]|\x01\d+\x01)*)"                   # a link's text: code may be in it
_TARGET = r"\(([^()\s\x00\x01]*)\)"
_LINKED_IMAGE = re.compile(rf"\[!\[{_LABEL}\]{_TARGET}\]{_TARGET}")    # a badge: one link, its alt the text
_IMAGE = re.compile(rf"!\[{_LABEL}\]{_TARGET}")
_LINK = re.compile(rf"\[{_LABEL}\]{_TARGET}")
# An address ends at a space or at an escaped <, >, " or ' - they are the text around it, not the address.
_ADDRESS = r"https?://(?:(?!&(?:lt|gt|quot|#x27);)[^\s\x00])+"
_AUTOLINK = re.compile(f"&lt;({_ADDRESS})&gt;")
_BARE = re.compile(f"(?<![\\w/=]){_ADDRESS}")
_ISSUE = re.compile(r"(?<![\w&#/])#(\d{1,7})\b")
_MENTION = re.compile(r"(?<![\w.@/&;-])@([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))\b(?![@.]\w)")
_EMPHASIS = (
    (re.compile(r"\*\*(?=\S)((?:[^*]|\*(?!\*))+?)(?<=\S)\*\*"), "strong"),
    (re.compile(r"(?<!\w)__(?=\S)((?:[^_]|_(?!_))+?)(?<=\S)__(?!\w)"), "strong"),
    (re.compile(r"(?<![\w*])\*(?=\S)([^*]+?)(?<=\S)\*(?![\w*])"), "em"),
    (re.compile(r"(?<!\w)_(?=\S)([^_]+?)(?<=\S)_(?!\w)"), "em"),
    (re.compile(r"~~(?=\S)((?:[^~]|~(?!~))+?)(?<=\S)~~"), "del"),
)


def _link(address: str, text: str) -> str:
    return f'<a href="{address}"{LINK}>{text}</a>'


def _emphasis(text: str) -> str:
    for pattern, tag in _EMPHASIS:
        text = pattern.sub(lambda m, t=tag: f"<{t}>{m.group(1)}</{t}>", text)
    return text


def _bare_address(match: re.Match[str]) -> tuple[str, str]:
    """A bare address and what follows it: a full stop, a comma, an unpaired bracket or the marks of emphasis
    after it (**https://…**) are the sentence's, not the address's."""
    address, rest = match.group(0), ""
    while address and (address[-1] in ".,;:!?*_~" or (address[-1] == ")" and address.count("(") < address.count(")"))):
        address, rest = address[:-1], address[-1] + rest
    return address, rest


def _inline(text: str, repository: str) -> str:
    """One escaped piece of text: code, links and references put aside first (nothing converts inside them,
    no link gets into another one), then emphasis, then everything put back."""
    kept: list[str] = []

    def keep(value: str, mark: str = "\x00") -> str:
        kept.append(value)
        return f"{mark}{len(kept) - 1}{mark}"

    def link_or_text(label: str, address: str) -> str:
        if re.match(r"https?://", address, re.IGNORECASE):
            return keep(_link(address, _emphasis(label)))
        return label                              # javascript:, data:, a relative address: only the text

    text = _CODE.sub(lambda m: keep(f"<code>{m.group(2)}</code>", "\x01"), text)
    text = _LINKED_IMAGE.sub(lambda m: link_or_text(m.group(1) or m.group(3), m.group(3)), text)
    text = _IMAGE.sub(lambda m: link_or_text(m.group(1) or m.group(2), m.group(2)), text)
    text = _LINK.sub(lambda m: link_or_text(m.group(1), m.group(2)), text)

    def bare(match: re.Match[str]) -> str:
        address, rest = _bare_address(match)
        return keep(_link(address, address)) + rest

    text = _AUTOLINK.sub(lambda m: keep(_link(m.group(1), m.group(1))), text)
    text = _BARE.sub(bare, text)
    text = _ISSUE.sub(lambda m: keep(_link(f"{GITHUB}/{repository}/issues/{m.group(1)}", f"#{m.group(1)}")), text)
    text = _MENTION.sub(lambda m: keep(_link(f"{GITHUB}/{m.group(1)}", f"@{m.group(1)}")), text)
    text = _emphasis(text)
    for _ in range(len(kept) + 1):               # a link's text may hold a kept code
        restored = _TOKEN.sub(lambda m: kept[int(m.group(1))], text)
        if restored == text:
            break
        text = restored
    return text


class _Writer:
    """Collects the HTML of the blocks; the lists are a stack of open (tag, indent) with an item open in each."""

    def __init__(self, repository: str, depth: int) -> None:
        self.repository = repository
        self.depth = depth                         # how many quotes this one is inside
        self.out: list[str] = []
        self.paragraph: list[str] = []
        self.lists: list[tuple[str, int]] = []
        self.item: list[str] = []                  # the text of the innermost open item, written when it ends
        self.quote: list[str] = []

    def inline(self, text: str) -> str:
        return _inline(text, self.repository)

    def end_paragraph(self) -> None:
        if self.paragraph:
            self.out.append(f"<p>{self.inline(' '.join(self.paragraph))}</p>")
            self.paragraph.clear()

    def end_item_text(self) -> None:
        if self.item:
            self.out.append(self.inline(" ".join(self.item)))
            self.item.clear()

    def close_lists(self, keep: int = 0) -> None:
        self.end_item_text()
        while len(self.lists) > keep:
            tag, _ = self.lists.pop()
            self.out.append(f"</li></{tag}>")

    def end_quote(self) -> None:
        if self.quote:
            inside = _render_escaped(self.quote, self.repository, self.depth + 1)
            self.out.append(f"<blockquote>{inside}</blockquote>")
            self.quote.clear()

    def end_all(self) -> None:
        self.end_paragraph()
        self.close_lists()
        self.end_quote()

    def list_item(self, indent: int, marker: str, text: str) -> None:
        self.end_paragraph()
        self.end_quote()
        tag = "ol" if marker[0].isdigit() else "ul"
        while self.lists and indent < self.lists[-1][1]:
            self.close_lists(len(self.lists) - 1)
        if self.lists and indent > self.lists[-1][1] and len(self.lists) < MAX_DEPTH:
            self.end_item_text()                   # a nested list goes inside the open item
            self.lists.append((tag, indent))
            self.out.append(f"<{tag}><li>")
        elif self.lists and self.lists[-1][0] == tag:
            self.end_item_text()
            self.out.append("</li><li>")
        else:
            self.close_lists(len(self.lists) - 1 if self.lists else 0)
            self.lists.append((tag, indent))
            self.out.append(f"<{tag}><li>")
        self.item.append(text)


def _heading_text(text: str) -> str:
    """A heading's text without its closing #s - only when a space comes before them ("C#" keeps its #). Not a
    regex: one with a lazy text and optional spaces took seconds on a long run of spaces."""
    text = text.rstrip()
    bare = text.rstrip("#")
    return bare.rstrip() if bare != text and (not bare or bare[-1].isspace()) else text


def _render_escaped(lines: list[str], repository: str, depth: int = 0) -> str:
    """The blocks of lines that are escaped already. `depth` = inside how many quotes; deeper than
    MAX_DEPTH a > is text (2000 of them in a row would otherwise run out of the stack)."""
    writer = _Writer(repository, depth)
    code: list[str] | None = None
    for line in lines:
        if code is not None:
            if _FENCE.match(line):
                writer.out.append(f"<pre><code>{chr(10).join(code)}</code></pre>")
                code = None
            else:
                code.append(line)
            continue
        if _FENCE.match(line):
            writer.end_all()
            code = []
            continue
        if not line.strip():
            writer.end_all()
            continue
        quoted = _QUOTE.match(line) if depth < MAX_DEPTH else None
        if quoted:
            writer.end_paragraph()
            writer.close_lists()
            writer.quote.append(quoted.group(1))
            continue
        writer.end_quote()
        heading, item = _HEADING.match(line), _ITEM.match(line)
        if heading:
            writer.end_all()
            tag = "h4" if len(heading.group(1)) <= 3 else "h5"
            writer.out.append(f"<{tag}>{writer.inline(_heading_text(heading.group(2)))}</{tag}>")
        elif _RULE.match(line):
            writer.end_all()
            writer.out.append("<hr>")
        elif item:
            writer.list_item(len(item.group(1).expandtabs(4)), item.group(2), item.group(3))
        elif writer.lists:
            writer.item.append(line.strip())       # a bullet continued on the next line
        else:
            writer.paragraph.append(line.strip())
    if code is not None:                            # a code block never closed: it runs to the end
        writer.out.append(f"<pre><code>{chr(10).join(code)}</code></pre>")
    writer.end_all()
    return "".join(writer.out)


def to_html(text: str, repository: str = "SpeeDFireCZE/jellyscope") -> str:
    """The release notes (markdown) as HTML for the update window. Escaped first - see the module."""
    if not text:
        return ""
    escaped = html.escape(text.replace("\x00", "").replace("\x01", "")).replace("\r\n", "\n").replace("\r", "\n")
    return _render_escaped(escaped.split("\n"), repository)
