from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zipfile import ZipFile, ZIP_DEFLATED


PRESERVED_DIRS = {"userdata", "midias", "backups", "logs"}


def parse_version(value: str) -> tuple[int, ...]:
    parts = []
    for chunk in str(value).split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts or [0])


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


@dataclass
class UpdateCheck:
    ok: bool
    message: str
    current_version: str
    latest_version: str | None = None
    update_available: bool = False
    manifest: dict[str, Any] | None = None


class Updater:
    def __init__(self, root: Path, version: str):
        self.root = root
        self.version = version
        self.userdata = root / "userdata"
        self.midias = root / "midias"
        self.backups = root / "backups"
        self.system = root / "system"
        self.config_file = self.userdata / "config.json"
        for folder in (self.userdata, self.midias, self.backups, self.system):
            folder.mkdir(parents=True, exist_ok=True)
        self.config = self.load_config()

    def load_config(self) -> dict[str, Any]:
        default = {
            "update_channel": "stable",
            "update_manifest_url": "https://raw.githubusercontent.com/rarodigital/nserver/main/manifest.json",
            "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if self.config_file.exists():
            try:
                data = json.loads(self.config_file.read_text(encoding="utf-8"))
                default.update(data)
            except Exception:
                pass
        self.config_file.write_text(json.dumps(default, ensure_ascii=False, indent=2), encoding="utf-8")
        return default

    def save_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        self.config.update(patch)
        self.config_file.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.config

    def fetch_manifest(self) -> dict[str, Any]:
        url = self.config.get("update_manifest_url") or ""
        if not url:
            raise RuntimeError("Canal de atualização ainda não configurado. Defina uma URL de manifesto quando o servidor de updates estiver pronto.")
        if url.startswith("file://"):
            return json.loads(Path(url[7:]).read_text(encoding="utf-8"))
        if url.startswith(("http://", "https://")):
            with urllib.request.urlopen(url, timeout=20) as res:
                return json.loads(res.read().decode("utf-8"))
        return json.loads(Path(url).read_text(encoding="utf-8"))

    def check(self) -> UpdateCheck:
        try:
            manifest = self.fetch_manifest()
        except Exception as exc:
            return UpdateCheck(False, str(exc), self.version)
        channel = self.config.get("update_channel", "stable")
        latest = manifest.get("channels", {}).get(channel) or manifest.get("latest_version") or manifest.get("version")
        if not latest:
            return UpdateCheck(False, "Manifesto de atualização inválido: versão não encontrada.", self.version, manifest=manifest)
        available = parse_version(str(latest)) > parse_version(self.version)
        msg = "Nova versão disponível." if available else "Nserver já está atualizado."
        return UpdateCheck(True, msg, self.version, str(latest), available, manifest)

    def create_backup(self) -> Path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = self.backups / f"nserver-{self.version}-{stamp}.zip"
        with ZipFile(path, "w", ZIP_DEFLATED) as z:
            for item in self.root.rglob("*"):
                if item.is_dir():
                    continue
                rel = item.relative_to(self.root)
                if rel.parts and rel.parts[0] in {"backups", "midias"}:
                    continue
                z.write(item, rel.as_posix())
        return path

    def _download(self, url: str, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        if url.startswith("file://"):
            shutil.copy2(Path(url[7:]), dest)
            return
        if url.startswith(("http://", "https://")):
            with urllib.request.urlopen(url, timeout=120) as res, dest.open("wb") as f:
                shutil.copyfileobj(res, f)
            return
        shutil.copy2(Path(url), dest)

    def apply(self, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
        manifest = manifest or self.fetch_manifest()
        backup = self.create_backup()
        staging = self.root / ".update-staging"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        applied: list[str] = []
        try:
            for file_item in manifest.get("files", []):
                rel = Path(file_item["path"])
                if rel.is_absolute() or ".." in rel.parts or (rel.parts and rel.parts[0] in PRESERVED_DIRS):
                    raise RuntimeError(f"Caminho de update não permitido: {rel}")
                temp = staging / rel
                self._download(file_item["url"], temp)
                expected = file_item.get("sha256")
                if expected and sha256_file(temp) != expected:
                    raise RuntimeError(f"Checksum inválido para {rel}")
            for temp in staging.rglob("*"):
                if temp.is_dir():
                    continue
                rel = temp.relative_to(staging)
                dest = self.root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(temp), str(dest))
                applied.append(rel.as_posix())
            (self.userdata / "last-update.json").write_text(json.dumps({
                "date": time.strftime("%Y-%m-%d %H:%M:%S"),
                "from": self.version,
                "to": manifest.get("version") or manifest.get("latest_version"),
                "backup": str(backup),
                "files": applied,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            return {"ok": True, "message": "Atualização aplicada. Reiniciando o Nserver...", "backup": str(backup), "files": applied}
        except Exception:
            self.restore_backup(backup)
            raise
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    def restore_backup(self, backup: Path):
        with ZipFile(backup) as z:
            for info in z.infolist():
                rel = Path(info.filename)
                if rel.parts and rel.parts[0] in {"userdata", "midias"}:
                    continue
                z.extract(info, self.root)

    def schedule_restart(self, delay: float = 1.5):
        def restart():
            time.sleep(delay)
            os.execv(sys.executable, [sys.executable] + sys.argv)
        threading.Thread(target=restart, daemon=True).start()
