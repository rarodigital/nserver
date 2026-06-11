from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".3gp", ".3g2", ".mpeg", ".mpg", ".ts", ".mts", ".m2ts"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg"}
TEXT_EXTS = {".txt", ".md", ".json", ".docx", ".pdf"}


def safe_name(name: str) -> str:
    name = Path(name or "arquivo").name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-_.")
    return (stem or "arquivo")[:120]


class MediaManager:
    """Central media layer used by all Nserver tools.

    Owns uploads, library listing and safe path resolution so tools do not keep
    creating their own incompatible upload/library/history behavior.
    """

    def __init__(self, root: Path):
        self.root = root
        self.media_root = root / "midias"
        self.media_root.mkdir(parents=True, exist_ok=True)
        for folder in ["Videos", "Audios", "Transcricoes", "Cortes", "Editados", "Uploads", "_Temporarios"]:
            (self.media_root / folder).mkdir(parents=True, exist_ok=True)

    def kind_for(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in VIDEO_EXTS:
            return "video"
        if suffix in AUDIO_EXTS:
            return "audio"
        if suffix in TEXT_EXTS:
            return "text"
        return "other"

    def folder_for(self, filename: str) -> Path:
        suffix = Path(filename).suffix.lower()
        if suffix in VIDEO_EXTS:
            return self.media_root / "Videos"
        if suffix in AUDIO_EXTS:
            return self.media_root / "Audios"
        return self.media_root / "Uploads"

    def save_upload(self, filename: str, data: bytes) -> dict[str, Any]:
        if not data:
            raise ValueError("Arquivo vazio.")
        filename = safe_name(filename)
        folder = self.folder_for(filename)
        folder.mkdir(parents=True, exist_ok=True)
        stem = Path(filename).stem
        suffix = Path(filename).suffix
        target = folder / filename
        if target.exists():
            target = folder / f"{stem}-{time.strftime('%Y%m%d-%H%M%S')}{suffix}"
        target.write_bytes(data)
        return self.describe(target)

    def resolve(self, relative: str) -> Path:
        if not relative:
            raise ValueError("Selecione um arquivo da Biblioteca.")
        target = (self.media_root / relative).resolve()
        if self.media_root.resolve() not in target.parents or not target.is_file():
            raise ValueError("Arquivo da Biblioteca inválido.")
        return target

    def describe(self, path: Path) -> dict[str, Any]:
        resolved = path.resolve()
        rel = resolved.relative_to(self.media_root.resolve()).as_posix()
        stat = resolved.stat()
        return {
            "relative": rel,
            "name": resolved.name,
            "kind": self.kind_for(resolved),
            "size_bytes": stat.st_size,
            "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
        }

    def list(self, kinds: set[str] | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in self.media_root.rglob("*"):
            if not path.is_file():
                continue
            try:
                rel = path.resolve().relative_to(self.media_root.resolve()).as_posix()
            except Exception:
                continue
            if rel.startswith("_Temporarios/"):
                continue
            item = self.describe(path)
            if kinds and item["kind"] not in kinds:
                continue
            items.append(item)
        return sorted(items, key=lambda x: x["modified"], reverse=True)
