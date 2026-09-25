"""Find documents in a folder, extract their text, and split them into chunks."""
from __future__ import annotations

import html
import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

TEXT_EXTS = {".md", ".markdown", ".txt", ".rst", ".org", ".text", ".adoc"}
HTML_EXTS = {".html", ".htm"}
DOCX_EXTS = {".docx"}
PDF_EXTS = {".pdf"}
SUPPORTED_EXTS = TEXT_EXTS | HTML_EXTS | DOCX_EXTS | PDF_EXTS

SKIP_DIRS = {".hunch", ".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode"}
MAX_FILE_BYTES = 25 * 1024 * 1024

CHUNK_TARGET = 900
CHUNK_MAX = 1600


@dataclass
class Chunk:
    doc: str          # relative path, forward slashes
    section: str      # nearest heading, may be ""
    text: str


@dataclass
class Document:
    path: str                 # relative path, forward slashes
    title: str
    folder: str | None        # top-level folder, or None for files at the root
    mtime: float
    size: int
    chunks: list[Chunk] = field(default_factory=list)


def iter_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            p = Path(dirpath) / name
            if p.suffix.lower() in SUPPORTED_EXTS:
                yield p


def snapshot(root: Path) -> dict[str, tuple[float, int]]:
    """Cheap change detection: {relative path: (mtime, size)}."""
    out = {}
    for p in iter_files(root):
        try:
            st = p.stat()
        except OSError:
            continue
        out[p.relative_to(root).as_posix()] = (round(st.st_mtime, 3), st.st_size)
    return out


# ---------------------------------------------------------------- extraction

_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_BLOCK = re.compile(r"</?(p|div|br|li|tr|h[1-6]|section|article|pre|blockquote)[^>]*>", re.I)
_HTML_HEAD = re.compile(r"<h([1-6])[^>]*>(.*?)</h\1>", re.S | re.I)


def html_to_text(raw: str) -> str:
    raw = _SCRIPT.sub(" ", raw)
    raw = _HTML_HEAD.sub(lambda m: "\n\n" + "#" * int(m.group(1)) + " " + _TAG.sub("", m.group(2)).strip() + "\n\n", raw)
    raw = _BLOCK.sub("\n\n", raw)
    raw = _TAG.sub(" ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"[ \t]+", " ", raw)
    return re.sub(r"\n\s*\n+", "\n\n", raw).strip()


def docx_to_text(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    xml = re.sub(r"</w:p>", "\n\n", xml)
    xml = re.sub(r"<w:tab/>", "\t", xml)
    return html.unescape(_TAG.sub("", xml)).strip()


def pdf_to_text(path: Path) -> str:
    try:
        from pypdf import PdfReader  # optional dependency
    except ImportError:
        return ""
    try:
        reader = PdfReader(str(path))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        return ""


def read_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in DOCX_EXTS:
        try:
            return docx_to_text(path)
        except Exception:
            return ""
    if ext in PDF_EXTS:
        return pdf_to_text(path)
    raw = path.read_bytes()[:MAX_FILE_BYTES].decode("utf-8", "ignore")
    if ext in HTML_EXTS:
        return html_to_text(raw)
    return raw


# ------------------------------------------------------------------ chunking

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def chunk_text(text: str, doc: str) -> list[Chunk]:
    """Split on headings and blank lines, then pack paragraphs to ~CHUNK_TARGET chars."""
    chunks: list[Chunk] = []
    section = ""
    buf: list[str] = []
    size = 0

    def flush():
        nonlocal buf, size
        body = "\n\n".join(buf).strip()
        if body:
            chunks.append(Chunk(doc=doc, section=section, text=body))
        buf, size = [], 0

    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        first = para.splitlines()[0]
        m = _MD_HEADING.match(first)
        if m:
            flush()
            section = m.group(2).strip()
            rest = "\n".join(para.splitlines()[1:]).strip()
            if not rest:
                continue
            para = rest
        # hard-split giant paragraphs
        while len(para) > CHUNK_MAX:
            cut = para.rfind(" ", 0, CHUNK_MAX)
            cut = cut if cut > CHUNK_TARGET // 2 else CHUNK_MAX
            if buf:
                flush()
            buf, size = [para[:cut]], cut
            flush()
            para = para[cut:].strip()
        if size + len(para) > CHUNK_TARGET and buf:
            flush()
        buf.append(para)
        size += len(para)
    flush()
    return chunks


def guess_title(text: str, path: Path) -> str:
    for line in text.splitlines()[:40]:
        m = _MD_HEADING.match(line.strip())
        if m:
            return m.group(2).strip()
    return path.stem.replace("_", " ").replace("-", " ").strip() or path.name


def load_documents(root: Path) -> list[Document]:
    docs: list[Document] = []
    for p in iter_files(root):
        try:
            st = p.stat()
            if st.st_size > MAX_FILE_BYTES:
                continue
            text = read_text(p)
        except OSError:
            continue
        if not text.strip():
            continue
        rel = p.relative_to(root)
        rel_s = rel.as_posix()
        folder = rel.parts[0] if len(rel.parts) > 1 else None
        doc = Document(
            path=rel_s,
            title=guess_title(text, p),
            folder=folder,
            mtime=round(st.st_mtime, 3),
            size=st.st_size,
        )
        doc.chunks = chunk_text(text, rel_s)
        if doc.chunks:
            docs.append(doc)
    return docs
