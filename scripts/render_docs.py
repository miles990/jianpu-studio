#!/usr/bin/env python3
"""Render the offline user guide using only the Python standard library.

Run ``python scripts/render_docs.py`` after editing docs/user-guide.md, or add
``--check`` to verify the checked-in HTML. This intentionally supports only the
guide's Markdown subset: headings 1–3, paragraphs, flat lists, pipe tables,
fenced text, inline code, bold text, and links. Unsupported markup is an error.
"""
from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "user-guide.md"
DESTINATION = SOURCE.with_suffix(".html")
INLINE = re.compile(r"`([^`\n]+)`|\*\*([^*\n]+)\*\*|\[([^\[\]\n]+)\]\(([^\s()]+)\)")
LIST_ITEM = re.compile(r"(-|\d+\.) (.+)")
STYLE = """
:root { color-scheme: light; font-family: 'Microsoft JhengHei', 'Noto Sans CJK TC', sans-serif; }
* { box-sizing: border-box; }
body { margin: 0; color: #20343e; background: #f2f5f4; font-size: 17px; line-height: 1.85; }
main { max-width: 960px; margin: 32px auto; padding: 36px 48px; background: white; border-radius: 16px; }
h1, h2, h3 { color: #176b59; line-height: 1.4; }
h1 { margin-top: 0; font-size: 2rem; }
h2 { margin-top: 2.4em; padding-bottom: .35em; border-bottom: 1px solid #dce6e2; font-size: 1.45rem; }
h3 { margin-top: 1.7em; font-size: 1.12rem; }
p, ul, ol { margin: 1em 0; }
li { margin: .45em 0; }
a { color: #146d83; text-underline-offset: 3px; overflow-wrap: anywhere; }
strong { font-weight: 700; }
code { font-family: Consolas, monospace; font-size: .92em; background: #eff4f2; padding: .15em .3em; border-radius: 4px; overflow-wrap: anywhere; }
pre { padding: 18px 22px; background: #eff4f2; border-left: 4px solid #3b8f76; overflow: auto; line-height: 1.6; }
pre code { padding: 0; background: none; overflow-wrap: normal; }
nav { padding: 16px 22px; margin: 24px 0; background: #f2f7f5; border-radius: 10px; }
nav h2 { margin: 0; border: 0; font-size: 1rem; }
nav ul { columns: 2; padding-left: 1.3em; margin: .6em 0 0; }
nav li { break-inside: avoid; margin: .25em 0; }
.table-wrap { overflow-x: auto; margin: 1em 0; }
table { border-collapse: collapse; width: 100%; font-size: .96em; }
th, td { border: 1px solid #d9e3df; padding: 10px 14px; vertical-align: top; text-align: left; }
th { background: #eaf3ef; color: #205e50; }
tbody tr:nth-child(even) { background: #f8faf9; }
td:first-child { min-width: 9em; }
footer { margin-top: 40px; padding-top: 16px; border-top: 1px solid #dce6e2; color: #5d6c72; font-size: .9em; }
@media (max-width: 700px) {
  main { margin: 0; padding: 22px 18px; border-radius: 0; }
  h1 { font-size: 1.65rem; }
  nav ul { columns: 1; }
  th, td { padding: 8px 10px; }
}
@media print {
  body { background: white; font-size: 11pt; }
  main { margin: 0; padding: 0; max-width: none; }
  nav, footer { display: none; }
  h2, h3 { break-after: avoid; }
  tr, pre { break-inside: avoid; }
  a { color: inherit; }
  .table-wrap { overflow: visible; }
}
"""


def inline(text: str, line: int) -> str:
    def plain(value: str) -> str:
        if re.search(r"[`*_\[\]]|<[/A-Za-z!]|~~", value):
            raise ValueError(f"line {line}: unsupported or unclosed inline markup: {value!r}")
        return html.escape(value)

    parts, end = [], 0
    for match in INLINE.finditer(text):
        parts.append(plain(text[end:match.start()]))
        code, bold, label, target = match.groups()
        if code is not None:
            parts.append(f"<code>{html.escape(code)}</code>")
        elif bold is not None:
            parts.append(f"<strong>{inline(bold, line)}</strong>")
        else:
            if text[max(0, match.start()-1):match.start()] == "!":
                raise ValueError(f"line {line}: images are not supported by the guide renderer")
            if not re.match(r"https?://", target) and (":" in target or target.startswith("//")):
                raise ValueError(f"line {line}: unsupported link target: {target}")
            parts.append(f'<a href="{html.escape(target, quote=True)}">{inline(label, line)}</a>')
        end = match.end()
    parts.append(plain(text[end:]))
    return "".join(parts)


def table_cells(text: str, line: int) -> list[str]:
    if not text.startswith("|") or not text.endswith("|"):
        raise ValueError(f"line {line}: table rows must start and end with a pipe")
    return [cell.strip() for cell in text[1:-1].split("|")]


def render(markdown: str) -> str:
    lines, blocks, contents = markdown.splitlines(), [], []
    i, title = 0, None
    while i < len(lines):
        text, number = lines[i], i + 1
        if not text.strip():
            i += 1
            continue
        if text != text.strip():
            raise ValueError(f"line {number}: indentation and trailing whitespace are not supported")
        if text.startswith("```"):
            if text not in ("```", "```text"):
                raise ValueError(f"line {number}: only plain-text code fences are supported")
            start = i = i + 1
            while i < len(lines) and lines[i] != "```":
                i += 1
            if i == len(lines):
                raise ValueError(f"line {number}: unclosed code fence")
            blocks.append("<pre><code>" + html.escape("\n".join(lines[start:i])) + "</code></pre>")
            i += 1
            continue
        if heading := re.fullmatch(r"(#{1,3}) (.+)", text):
            level, value = len(heading[1]), inline(heading[2], number)
            if level == 1:
                if title is not None or blocks:
                    raise ValueError(f"line {number}: exactly one title must appear first")
                title = heading[2]
                blocks.extend([f"<h1>{value}</h1>", "<!-- CONTENTS -->"])
            else:
                anchor = f"section-{number}"
                blocks.append(f'<h{level} id="{anchor}">{value}</h{level}>')
                if level == 2:
                    contents.append(f'<li><a href="#{anchor}">{value}</a></li>')
            i += 1
            continue
        if text.startswith("|"):
            headers = table_cells(text, number)
            if i + 1 == len(lines):
                raise ValueError(f"line {number}: table is missing its separator")
            separators = table_cells(lines[i+1], number+1)
            if len(headers) != len(separators) or not all(re.fullmatch(r"-{3,}", cell) for cell in separators):
                raise ValueError(f"line {number+1}: expected one --- separator per table column")
            rows = ['<div class="table-wrap"><table><thead><tr>']
            rows.extend(f'<th scope="col">{inline(cell, number)}</th>' for cell in headers)
            rows.append("</tr></thead><tbody>")
            i += 2
            while i < len(lines) and lines[i].startswith("|"):
                cells = table_cells(lines[i], i+1)
                if len(cells) != len(headers):
                    raise ValueError(f"line {i+1}: expected {len(headers)} table columns, found {len(cells)}")
                rows.append("<tr>" + "".join(f"<td>{inline(cell, i+1)}</td>" for cell in cells) + "</tr>")
                i += 1
            rows.append("</tbody></table></div>")
            blocks.append("\n".join(rows))
            continue
        if item := LIST_ITEM.fullmatch(text):
            ordered, items = item[1] != "-", []
            while i < len(lines) and (item := LIST_ITEM.fullmatch(lines[i])):
                if (item[1] != "-") != ordered:
                    raise ValueError(f"line {i+1}: separate ordered and unordered lists with a blank line")
                if ordered and int(item[1][:-1]) != len(items)+1:
                    raise ValueError(f"line {i+1}: ordered lists must be numbered consecutively from 1")
                items.append(f"<li>{inline(item[2], i+1)}</li>")
                i += 1
            tag = "ol" if ordered else "ul"
            blocks.append(f"<{tag}>\n" + "\n".join(items) + f"\n</{tag}>")
            continue
        if (text.startswith(("#", ">", "~~~")) or re.match(r"(?:[*+] |\d+[.)](?:\s|$))", text)
                or re.fullmatch(r"(?:[-*_]\s*){3,}", text)):
            raise ValueError(f"line {number}: unsupported block markup: {text!r}")
        paragraph = [inline(text, number)]
        i += 1
        if i < len(lines) and lines[i].strip():
            raise ValueError(f"line {i+1}: use one source line per paragraph and blank lines between blocks")
        blocks.append("<p>" + "".join(paragraph) + "</p>")
    if title is None:
        raise ValueError("the guide must begin with a level-1 title")
    navigation = '<nav aria-label="目錄"><h2>目錄</h2><ul>\n' + "\n".join(contents) + "\n</ul></nav>"
    body = "\n".join(blocks).replace("<!-- CONTENTS -->", navigation)
    return ("<!doctype html>\n<!-- Generated by scripts/render_docs.py; edit user-guide.md instead. -->\n"
            '<html lang="zh-Hant">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{html.escape(title)}</title>\n<style>{STYLE}</style>\n</head>\n<body>\n<main>\n"
            + body + '\n<footer>簡譜工房 · 離線使用手冊 · <a href="#">回到頂端</a></footer>\n'
            "</main>\n</body>\n</html>\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if user-guide.html needs regeneration")
    args = parser.parse_args()
    try:
        result = render(SOURCE.read_text(encoding="utf-8"))
        if args.check:
            if not DESTINATION.exists() or DESTINATION.read_text(encoding="utf-8") != result:
                parser.exit(1, "user-guide.html is out of date; run python scripts/render_docs.py\n")
            print("user-guide.html matches user-guide.md")
        else:
            DESTINATION.write_text(result, encoding="utf-8", newline="\n")
            print(f"Rendered {DESTINATION.relative_to(ROOT)}")
    except (OSError, ValueError) as error:
        parser.exit(1, f"Cannot render {SOURCE.relative_to(ROOT)}: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
