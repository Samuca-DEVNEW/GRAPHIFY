"""Parser de vaults Obsidian: notas, wikilinks, tags e frontmatter."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

ProgressCallback = Optional[Callable[[float, str], None]]

# [[Note]] | [[Note|alias]] | [[Note#heading]] | [[Note#heading|alias]]
WIKILINK_RE = re.compile(
    r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]+)?\]\]"
)
# #tag ou #tag/subtag (evita headings markdown # Title no início da linha)
TAG_RE = re.compile(r"(?<!\S)#([A-Za-z0-9_/\-]+)")
# Markdown links [text](path.md) ou [text](path)
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
# Frontmatter YAML simples entre ---
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class Note:
    """Representa uma nota Obsidian parseada."""

    id: str
    title: str
    path: str
    content: str = ""
    links: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    frontmatter: dict[str, Any] = field(default_factory=dict)
    word_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "path": self.path,
            "links": self.links,
            "tags": self.tags,
            "frontmatter": self.frontmatter,
            "word_count": self.word_count,
        }


@dataclass
class VaultData:
    """Resultado do parse de um vault."""

    notes: dict[str, Note]
    root: str
    total_files: int = 0
    skipped_files: int = 0

    def to_summary(self) -> dict[str, Any]:
        all_tags: set[str] = set()
        total_links = 0
        for note in self.notes.values():
            all_tags.update(note.tags)
            total_links += len(note.links)
        return {
            "root": self.root,
            "total_notes": len(self.notes),
            "total_files_scanned": self.total_files,
            "skipped_files": self.skipped_files,
            "total_wikilinks": total_links,
            "unique_tags": sorted(all_tags),
            "tag_count": len(all_tags),
        }


def _normalize_note_id(name: str) -> str:
    """Normaliza ID de nota (nome sem extensão, path separators unificados)."""
    name = name.strip().replace("\\", "/")
    if name.lower().endswith(".md"):
        name = name[:-3]
    return name.strip()


def _note_id_from_path(vault_root: Path, file_path: Path) -> str:
    rel = file_path.relative_to(vault_root).as_posix()
    return _normalize_note_id(rel)


def _title_from_path(file_path: Path) -> str:
    return file_path.stem


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Extrai frontmatter YAML básico (sem depender de PyYAML pesado)."""
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    raw = match.group(1)
    body = text[match.end() :]
    data: dict[str, Any] = {}

    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip("\"'")
        if not key:
            continue
        # Lista simples: [a, b] ou - item (linhas seguintes ignoradas no parser leve)
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            data[key] = [
                v.strip().strip("\"'") for v in inner.split(",") if v.strip()
            ] if inner else []
        elif value.lower() in {"true", "false"}:
            data[key] = value.lower() == "true"
        elif value.isdigit():
            data[key] = int(value)
        else:
            data[key] = value

    return data, body


def _extract_wikilinks(text: str) -> list[str]:
    found = []
    for match in WIKILINK_RE.finditer(text):
        target = _normalize_note_id(match.group(1))
        # Ignora embeds de imagens ![[image.png]] — o regex não captura o !,
        # então filtramos extensões de mídia
        if target.lower().endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".pdf", ".mp4", ".webm")
        ):
            continue
        found.append(target)
    # Dedup preservando ordem
    seen: set[str] = set()
    unique: list[str] = []
    for link in found:
        key = link.lower()
        if key not in seen:
            seen.add(key)
            unique.append(link)
    return unique


def _extract_tags(text: str, frontmatter: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    fm_tags = frontmatter.get("tags") or frontmatter.get("tag")
    if isinstance(fm_tags, str):
        tags.extend(t.strip() for t in fm_tags.replace(",", " ").split() if t.strip())
    elif isinstance(fm_tags, list):
        tags.extend(str(t).strip().lstrip("#") for t in fm_tags if str(t).strip())

    for match in TAG_RE.finditer(text):
        tag = match.group(1).strip()
        # Evita capturar fragmentos de URLs ou anchors longos inválidos
        if tag and not tag.startswith("#"):
            tags.append(tag)

    seen: set[str] = set()
    unique: list[str] = []
    for t in tags:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            unique.append(t)
    return unique


def _extract_md_links(text: str) -> list[str]:
    """Extrai links markdown internos para outros .md."""
    links: list[str] = []
    for match in MD_LINK_RE.finditer(text):
        href = match.group(2).strip()
        if href.startswith(("http://", "https://", "mailto:", "#")):
            continue
        href = href.split("#")[0].split("?")[0]
        if href.endswith(".md") or ("." not in Path(href).name):
            links.append(_normalize_note_id(href))
    return links


def parse_note(vault_root: Path, file_path: Path) -> Note:
    """Parseia um único arquivo Markdown."""
    try:
        text = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = file_path.read_text(encoding="utf-8", errors="replace")

    frontmatter, body = _parse_frontmatter(text)
    note_id = _note_id_from_path(vault_root, file_path)
    title = str(frontmatter.get("title") or _title_from_path(file_path))

    wikilinks = _extract_wikilinks(body)
    md_links = _extract_md_links(body)
    # Merge links
    all_links: list[str] = []
    seen: set[str] = set()
    for link in wikilinks + md_links:
        key = link.lower()
        if key not in seen:
            seen.add(key)
            all_links.append(link)

    tags = _extract_tags(body, frontmatter)
    words = len(re.findall(r"\S+", body))

    return Note(
        id=note_id,
        title=title,
        path=file_path.relative_to(vault_root).as_posix(),
        content=body[:5000],  # trecho para busca; evita inflar memória
        links=all_links,
        tags=tags,
        frontmatter=frontmatter,
        word_count=words,
    )


def parse_vault(
    vault_root: str | Path,
    progress: ProgressCallback = None,
    ignore_dirs: Optional[set[str]] = None,
) -> VaultData:
    """
    Percorre o vault e parseia todas as notas Markdown.

    Args:
        vault_root: Diretório raiz do vault.
        progress: Callback opcional (0-1, mensagem).
        ignore_dirs: Pastas a ignorar (ex: .git, .obsidian, node_modules).
    """
    root = Path(vault_root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Vault inválido: {root}")

    ignore = ignore_dirs or {
        ".git",
        ".obsidian",
        ".trash",
        "node_modules",
        ".smart-env",
        ".space",
    }

    md_files: list[Path] = []
    for path in root.rglob("*.md"):
        # Ignora pastas ocultas / especiais (ex: .obsidian, .git)
        skip = False
        for part in path.relative_to(root).parts[:-1]:
            if part in ignore or part.startswith("."):
                skip = True
                break
        if not skip:
            md_files.append(path)

    if not md_files:
        raise FileNotFoundError(
            f"Nenhuma nota Markdown encontrada em '{root}'. "
            "Verifique se o vault contém arquivos .md."
        )

    notes: dict[str, Note] = {}
    # Índice auxiliar por título (basename) para resolver wikilinks curtos
    by_title: dict[str, str] = {}
    skipped = 0
    total = len(md_files)

    if progress:
        progress(0.4, f"Parseando {total} notas…")

    for i, file_path in enumerate(md_files):
        try:
            note = parse_note(root, file_path)
            notes[note.id] = note
            by_title[note.title.lower()] = note.id
            by_title[Path(note.path).stem.lower()] = note.id
        except Exception:
            skipped += 1

        if progress and (i % 25 == 0 or i == total - 1):
            frac = 0.4 + 0.25 * ((i + 1) / total)
            progress(frac, f"Parseando notas… {i + 1}/{total}")

    vault = VaultData(
        notes=notes,
        root=str(root),
        total_files=total,
        skipped_files=skipped,
    )
    # Anexa mapa de títulos para resolução de links (atributo dinâmico)
    vault._title_index = by_title  # type: ignore[attr-defined]
    return vault


def resolve_link_target(target: str, notes: dict[str, Note], title_index: dict[str, str]) -> Optional[str]:
    """
    Resolve um wikilink para um note_id existente.
    Tenta: id exato, path relativo, basename/título.
    """
    if not target:
        return None

    candidates = [
        target,
        _normalize_note_id(target),
        target.replace("\\", "/"),
    ]
    # Basename
    base = Path(target.replace("\\", "/")).name
    candidates.append(base)
    candidates.append(_normalize_note_id(base))

    lower_map = {k.lower(): k for k in notes}

    for c in candidates:
        if c in notes:
            return c
        low = c.lower()
        if low in lower_map:
            return lower_map[low]
        if low in title_index:
            return title_index[low]

    return None
