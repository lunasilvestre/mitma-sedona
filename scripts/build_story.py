#!/usr/bin/env python3
"""build_story.py — render the scrollytelling page from content + engine.

Pure-python, no third-party deps (run with system ``python3``). Reads:

  * site/engine/story.template.html   — shell with __PLACEHOLDER__ tokens
  * site/content/chapters.json        — ordered chapters {id, mode, prose, src}
  * site/content/chapters/<id>.md     — per-chapter prose (simple markdown)
  * docs/story_data/manifest.json     — headline metrics (inlined verbatim)
  * marked source files               — snippets extracted at build time

Writes:

  * docs/story.html                   — the rendered page
  * docs/app/{story.css,scrolly.js,story-map.js,codeblock.js}
                                        — engine assets COPIED here, because
                                          .github/workflows/pages.yml publishes
                                          only docs/* (site/engine/ would not
                                          be served). The template's
                                          ../site/engine/ references are
                                          rewritten to app/.
  * docs/.nojekyll                    — serve app/ + underscore paths verbatim

Snippet extraction per ``src`` entry, in priority order:
  1. ``marker`` present in the file -> lines strictly between ``# <marker>``
     and the next ``# story:end`` (both exclusive).
  2. else ``lines`` like "L39-L100" -> those 1-based lines (inclusive),
     clamped to the file length.
  3. else (whole file) -> NO <pre>; emit a "view on GitHub" link only.
"""

from __future__ import annotations

import html
import json
import re
import shutil
from pathlib import Path

# --- constants ---------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
SITE = REPO_ROOT / "site"
ENGINE = SITE / "engine"
CONTENT = SITE / "content"
DOCS = REPO_ROOT / "docs"

TEMPLATE = ENGINE / "story.template.html"
CHAPTERS_JSON = CONTENT / "chapters.json"
MANIFEST_JSON = DOCS / "story_data" / "manifest.json"

OUT_HTML = DOCS / "story.html"
APP_DIR = DOCS / "app"

STORY_REF = "story-v1"
GH_REPO = "lunasilvestre/mitma-sedona"

ENGINE_ASSETS = ("story.css", "scrolly.js", "story-map.js", "codeblock.js")

# --- minimal markdown -> HTML ------------------------------------------------

_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<![\*])\*(?!\s)([^*]+?)(?<!\s)\*(?![\*])")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _inline(text: str) -> str:
    """Render inline markdown on an already HTML-escaped string."""
    # Links first (so their text can still carry bold/code).
    def _link_sub(m: "re.Match[str]") -> str:
        label, href = m.group(1), m.group(2)
        return f'<a href="{href}" target="_blank" rel="noopener noreferrer">{label}</a>'

    text = _LINK.sub(_link_sub, text)
    text = _INLINE_CODE.sub(lambda m: f"<code>{m.group(1)}</code>", text)
    text = _BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", text)
    text = _ITALIC.sub(lambda m: f"<em>{m.group(1)}</em>", text)
    return text


def md_to_html(md: str) -> str:
    """Convert a small subset of markdown to HTML.

    Supports: ATX headings (#..######), unordered lists (-/*), blank-line
    separated paragraphs, and inline bold/italic/code/links. Block content is
    HTML-escaped before inline markup is applied so source < > & stay safe.
    """
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    para: list[str] = []
    in_list = False

    def flush_para() -> None:
        if para:
            joined = " ".join(s.strip() for s in para)
            out.append(f"<p>{_inline(html.escape(joined))}</p>")
            para.clear()

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:
            flush_para()
            close_list()
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            flush_para()
            close_list()
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(html.escape(heading.group(2)))}</h{level}>")
            continue

        item = re.match(r"^[-*]\s+(.*)$", stripped)
        if item:
            flush_para()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(html.escape(item.group(1)))}</li>")
            continue

        if in_list:
            close_list()
        para.append(line)

    flush_para()
    close_list()
    return "\n".join(out)


# --- snippet extraction ------------------------------------------------------

_LINES_RE = re.compile(r"^L(\d+)-L(\d+)$")


def extract_marker(text: str, marker: str) -> str | None:
    """Lines strictly between '# <marker>' and the next '# story:end'.

    Returns None if the marker line is not found.
    """
    file_lines = text.split("\n")
    start = None
    target = f"# {marker}"
    for i, ln in enumerate(file_lines):
        if ln.strip() == target:
            start = i
            break
    if start is None:
        return None
    body: list[str] = []
    for ln in file_lines[start + 1:]:
        if ln.strip() == "# story:end":
            break
        body.append(ln)
    # Trim leading/trailing blank lines for a tidy embed.
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    return "\n".join(body)


def extract_lines(text: str, spec: str) -> str | None:
    """1-based inclusive line slice 'L<a>-L<b>', clamped to file length."""
    m = _LINES_RE.match(spec.strip())
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    file_lines = text.split("\n")
    # 1-based inclusive -> python slice; slicing clamps the upper bound.
    a = max(1, a)
    chunk = file_lines[a - 1:b]
    while chunk and not chunk[-1].strip():
        chunk.pop()
    return "\n".join(chunk)


def build_code_section(entry: dict, warnings: list[str], chapter_id: str) -> tuple[str, bool]:
    """Render one <section class="story-code"> for a src entry.

    Returns (html, embedded_snippet?). When neither marker nor lines yields a
    snippet, emits a link-only block (no <pre>) so the reader can still jump to
    GitHub.
    """
    label = entry.get("label", "")
    path = entry.get("path", "")
    ref = entry.get("ref", STORY_REF)
    lines = entry.get("lines", "")
    marker = entry.get("marker")

    src_path = REPO_ROOT / path
    text = None
    if src_path.is_file():
        text = src_path.read_text(encoding="utf-8")
    else:
        warnings.append(f"[{chapter_id}] file not found: {path}")

    snippet = None
    if text is not None:
        if marker:
            snippet = extract_marker(text, marker)
            if snippet is None:
                warnings.append(
                    f"[{chapter_id}] marker '{marker}' not found in {path}"
                    + (f"; falling back to {lines}" if lines else "")
                )
        if snippet is None and lines:
            snippet = extract_lines(text, lines)
            if snippet is None:
                warnings.append(f"[{chapter_id}] bad lines spec '{lines}' for {path}")
        # else: whole-file -> link only (by design, no warning)

    data_attrs = (
        f'data-path="{html.escape(path, quote=True)}" '
        f'data-ref="{html.escape(ref, quote=True)}" '
        f'data-lines="{html.escape(lines, quote=True)}" '
        f'data-label="{html.escape(label, quote=True)}"'
    )

    if snippet is None or snippet.strip() == "":
        # Link-only block: codeblock.js still renders the GitHub button from a
        # bare <pre data-*> with no <code> child? It needs a <pre>. To stay
        # "no <pre>" per spec for whole-file, render a plain anchor instead.
        gh_url = f"https://github.com/{GH_REPO}/blob/{ref}/{path}"
        if lines:
            gh_url += f"#{lines}"
        return (
            '<section class="story-code">'
            '<div class="code-wrap code-linkonly">'
            f'<a class="code-gh-link" href="{html.escape(gh_url, quote=True)}" '
            'target="_blank" rel="noopener noreferrer">'
            f"{html.escape(label or path)}"
            '<span class="code-gh-arrow"> ↗</span></a>'
            "</div></section>"
        ), False

    escaped = html.escape(snippet)
    return (
        '<section class="story-code">'
        '<div class="code-wrap">'
        f"<pre {data_attrs}>"
        f'<code class="language-python">{escaped}</code>'
        "</pre>"
        "</div></section>"
    ), True


def build_panel(chapter: dict, warnings: list[str], counters: dict) -> str:
    cid = chapter.get("id", "")
    mode = chapter.get("mode", "single")

    md_path = CONTENT / chapter["prose"]["md"]
    if md_path.is_file():
        prose_html = md_to_html(md_path.read_text(encoding="utf-8"))
    else:
        warnings.append(f"[{cid}] prose md not found: {chapter['prose']['md']}")
        prose_html = ""

    code_sections: list[str] = []
    for entry in chapter.get("src", []):
        section, embedded = build_code_section(entry, warnings, cid)
        code_sections.append(section)
        if embedded:
            counters["snippets"] += 1

    return (
        f'<section class="story-panel" data-chapter-id="{html.escape(cid, quote=True)}" '
        f'data-mode="{html.escape(mode, quote=True)}">\n'
        f'  <div class="panel-prose">\n{prose_html}\n  </div>\n'
        + "\n".join(code_sections)
        + "\n</section>"
    )


# --- engine asset publish ----------------------------------------------------

def copy_engine_assets() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    for name in ENGINE_ASSETS:
        src = ENGINE / name
        if not src.is_file():
            raise FileNotFoundError(f"engine asset missing: {src}")
        shutil.copy2(src, APP_DIR / name)


def rewrite_asset_refs(rendered: str) -> str:
    """Point the published page at docs/app/ instead of ../site/engine/."""
    return rendered.replace("../site/engine/", "app/")


# --- main --------------------------------------------------------------------

def strip_doc_comment(template: str) -> str:
    """Remove the template's leading placeholder-documentation comment.

    That comment block documents the __PLACEHOLDER__ tokens, so it contains a
    copy of every token. A blind string-replace would substitute into the
    comment as well (duplicating the panels, etc.). It is build-time
    scaffolding, not page content, so drop it entirely. Only the FIRST
    HTML comment that follows the doctype is removed; functional comments
    deeper in the shell are preserved.
    """
    m = re.match(r"^(<!DOCTYPE[^>]*>\s*)<!--.*?-->\s*", template, flags=re.IGNORECASE | re.DOTALL)
    if m:
        return template[: m.end(1)] + template[m.end():]
    return template


def main() -> None:
    template = strip_doc_comment(TEMPLATE.read_text(encoding="utf-8"))
    chapters_cfg = json.loads(CHAPTERS_JSON.read_text(encoding="utf-8"))
    manifest_raw = MANIFEST_JSON.read_text(encoding="utf-8").strip()

    title = chapters_cfg.get("title", "Story")
    warnings: list[str] = []
    counters = {"snippets": 0}

    panels = [build_panel(ch, warnings, counters) for ch in chapters_cfg["chapters"]]
    panels_html = "\n\n".join(panels)

    chapters_inline = json.dumps(chapters_cfg, ensure_ascii=False, indent=2)

    rendered = template
    rendered = rendered.replace("__TITLE__", html.escape(title))
    rendered = rendered.replace("__PANELS__", panels_html)
    rendered = rendered.replace("__CHAPTERS_JSON__", chapters_inline)
    rendered = rendered.replace("__MANIFEST__", manifest_raw)
    rendered = rendered.replace("__GH_REPO__", GH_REPO)

    # Publish engine assets into docs/app/ and rewrite the references.
    copy_engine_assets()
    rendered = rewrite_asset_refs(rendered)

    OUT_HTML.write_text(rendered, encoding="utf-8")
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")

    n_chapters = len(chapters_cfg["chapters"])
    print(f"wrote {OUT_HTML.relative_to(REPO_ROOT)}")
    print(f"engine assets -> {APP_DIR.relative_to(REPO_ROOT)}/: {', '.join(ENGINE_ASSETS)}")
    print(f"chapters rendered: {n_chapters}")
    print(f"code snippets embedded: {counters['snippets']}")
    if warnings:
        print(f"warnings ({len(warnings)}):")
        for w in warnings:
            print(f"  - {w}")
    else:
        print("warnings: none")


if __name__ == "__main__":
    main()
