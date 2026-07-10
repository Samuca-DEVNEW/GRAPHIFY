"""Carregamento de vaults Obsidian a partir de GitHub ou arquivo ZIP."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

ProgressCallback = Optional[Callable[[float, str], None]]


def _progress(cb: ProgressCallback, value: float, message: str) -> None:
    if cb is not None:
        cb(value, message)


def _validate_github_url(url: str) -> str:
    """Normaliza e valida URL de repositório GitHub."""
    url = (url or "").strip()
    if not url:
        raise ValueError("URL do repositório GitHub não pode ser vazia.")

    # Aceita https://github.com/user/repo, git@github.com:user/repo.git, user/repo
    if re.fullmatch(r"[\w.-]+/[\w.-]+", url):
        url = f"https://github.com/{url}"

    if url.startswith("git@github.com:"):
        path = url.replace("git@github.com:", "").removesuffix(".git")
        url = f"https://github.com/{path}"

    parsed = urlparse(url)
    if parsed.netloc not in {"github.com", "www.github.com"}:
        raise ValueError(
            "URL inválida. Use um repositório GitHub "
            "(ex: https://github.com/usuario/vault-obsidian)."
        )

    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        raise ValueError("URL incompleta. Esperado: https://github.com/usuario/repo")

    owner, repo = parts[0], parts[1].removesuffix(".git")
    return f"https://github.com/{owner}/{repo}.git"


def load_vault_from_github(
    github_url: str,
    work_dir: Optional[str] = None,
    progress: ProgressCallback = None,
    depth: int = 1,
) -> Path:
    """
    Clona um repositório GitHub contendo um vault Obsidian.

    Returns:
        Path do diretório do vault clonado.
    """
    try:
        from git import Repo  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "GitPython não está instalado. Adicione 'GitPython' ao requirements.txt."
        ) from exc

    clone_url = _validate_github_url(github_url)
    base = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="graphify_vault_"))
    base.mkdir(parents=True, exist_ok=True)
    target = base / "repo"

    if target.exists():
        shutil.rmtree(target, ignore_errors=True)

    _progress(progress, 0.1, f"Clonando repositório: {clone_url}")
    try:
        Repo.clone_from(clone_url, str(target), depth=depth)
    except Exception as exc:
        raise RuntimeError(
            f"Falha ao clonar repositório. Verifique se a URL é pública e válida.\n"
            f"Detalhe: {exc}"
        ) from exc

    _progress(progress, 0.35, "Repositório clonado com sucesso.")
    return _resolve_vault_root(target)


def load_vault_from_zip(
    zip_path: str | Path,
    work_dir: Optional[str] = None,
    progress: ProgressCallback = None,
) -> Path:
    """
    Extrai um vault Obsidian a partir de um arquivo ZIP.

    Returns:
        Path do diretório raiz do vault.
    """
    zip_path = Path(zip_path)
    if not zip_path.exists():
        raise FileNotFoundError(f"Arquivo ZIP não encontrado: {zip_path}")
    if not zipfile.is_zipfile(zip_path):
        raise ValueError("O arquivo enviado não é um ZIP válido.")

    base = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="graphify_vault_"))
    base.mkdir(parents=True, exist_ok=True)
    target = base / "unzipped"

    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)

    _progress(progress, 0.1, "Extraindo vault ZIP…")
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            # Proteção básica contra zip-slip
            for member in zf.infolist():
                member_path = Path(member.filename)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise ValueError(f"Entrada ZIP insegura detectada: {member.filename}")
            zf.extractall(target)
    except zipfile.BadZipFile as exc:
        raise ValueError("ZIP corrompido ou inválido.") from exc

    _progress(progress, 0.35, "Vault extraído com sucesso.")
    return _resolve_vault_root(target)


def _resolve_vault_root(path: Path) -> Path:
    """
    Tenta localizar a raiz do vault (pasta com .md ou .obsidian).
    Se o ZIP/repo tiver uma única pasta no topo, desce nela.
    """
    path = path.resolve()

    # Se houver .obsidian, é a raiz
    if (path / ".obsidian").is_dir():
        return path

    md_here = list(path.rglob("*.md"))
    if not md_here:
        raise FileNotFoundError(
            "Nenhum arquivo Markdown (.md) encontrado no vault. "
            "Confirme que o repositório/ZIP contém notas Obsidian."
        )

    # Se só existe um subdiretório de primeiro nível com os .md, use-o
    children = [c for c in path.iterdir() if c.is_dir() and not c.name.startswith(".")]
    files = [c for c in path.iterdir() if c.is_file() and c.suffix.lower() == ".md"]
    if len(children) == 1 and not files:
        candidate = children[0]
        if (candidate / ".obsidian").is_dir() or any(candidate.rglob("*.md")):
            return candidate

    return path


def cleanup_workspace(path: str | Path | None) -> None:
    """Remove diretório temporário de trabalho com segurança."""
    if not path:
        return
    p = Path(path)
    try:
        if p.exists() and p.is_dir():
            # Só remove pastas temporárias do Graphify
            if "graphify_vault_" in str(p) or p.name in {"repo", "unzipped"}:
                root = p
                # Sobe até o prefixo graphify_vault_ se possível
                for parent in [p, *p.parents]:
                    if parent.name.startswith("graphify_vault_"):
                        root = parent
                        break
                shutil.rmtree(root, ignore_errors=True)
    except OSError:
        pass


def default_work_dir() -> Path:
    """Cria um diretório de trabalho temporário isolado."""
    return Path(tempfile.mkdtemp(prefix="graphify_vault_"))
