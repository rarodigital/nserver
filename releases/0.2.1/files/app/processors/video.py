from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .base import Processor, ProcessorResult

PROTECTED_HINTS = (
    "login", "sign in", "signin", "private", "premium", "members", "cookies",
    "authenticate", "authentication", "forbidden", "not available", "permission",
)


def _yt_dlp_cmd() -> str | None:
    return shutil.which("yt-dlp")


def _platform(url: str) -> str:
    host = (urlparse(url).netloc or "").lower().replace("www.", "")
    if "youtube" in host or "youtu.be" in host:
        return "YouTube"
    if "tiktok" in host:
        return "TikTok"
    if "instagram" in host:
        return "Instagram"
    if "facebook" in host or "fb.watch" in host:
        return "Facebook"
    if "twitter" in host or host == "x.com":
        return "X / Twitter"
    if "vimeo" in host:
        return "Vimeo"
    return host or "Plataforma pública"


def _safe_name(value: str) -> str:
    keep = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_. "
    cleaned = "".join(ch for ch in value if ch in keep).strip().replace("  ", " ")
    return cleaned[:90] or "video"


def _run(args: list[str], timeout: int = 1200) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _protected_message(stderr: str) -> str | None:
    low = stderr.lower()
    if any(h in low for h in PROTECTED_HINTS):
        return "Este conteúdo requer autenticação para ser acessado. Faça login na plataforma correspondente para continuar."
    return None


class VideoProcessor(Processor):
    id = "video-downloader"
    name = "Downloader e Processador de Vídeos"
    description = "Analisa links públicos, baixa vídeo/áudio e prepara transcrições/cortes."

    def analyze(self, url: str) -> ProcessorResult:
        if not url.startswith(("http://", "https://")):
            return ProcessorResult(False, "Cole uma URL válida começando com http:// ou https://")
        exe = _yt_dlp_cmd()
        if not exe:
            return ProcessorResult(False, "Dependência yt-dlp não encontrada. Feche o Nserver, execute novamente o iniciar-nserver.bat e aguarde a instalação automática.")
        proc = _run([exe, "--dump-single-json", "--no-playlist", url], timeout=180)
        if proc.returncode != 0:
            msg = _protected_message(proc.stderr) or "Não consegui analisar esse link. Verifique se ele é público e tente novamente."
            return ProcessorResult(False, msg, {"details": proc.stderr[-1000:]})
        try:
            info = json.loads(proc.stdout)
        except Exception:
            return ProcessorResult(False, "A análise retornou dados inválidos.")
        formats = []
        seen = set()
        for item in info.get("formats") or []:
            height = item.get("height")
            ext = item.get("ext")
            if height and ext:
                label = f"{height}p {ext}"
                if label not in seen:
                    formats.append({"height": height, "ext": ext, "label": label})
                    seen.add(label)
        formats = sorted(formats, key=lambda x: x["height"])
        data = {
            "title": info.get("title") or "Sem título",
            "duration": info.get("duration") or 0,
            "duration_text": self._duration(info.get("duration") or 0),
            "thumbnail": info.get("thumbnail") or "",
            "platform": _platform(url),
            "resolutions": formats[-12:],
            "webpage_url": info.get("webpage_url") or url,
        }
        return ProcessorResult(True, "Vídeo analisado com sucesso.", data)

    def download_video(self, url: str, quality: str = "720", ext: str = "mp4") -> ProcessorResult:
        exe = _yt_dlp_cmd()
        if not exe:
            return ProcessorResult(False, "yt-dlp não encontrado.")
        outdir = self.media_root / "Videos"
        outdir.mkdir(parents=True, exist_ok=True)
        outtmpl = str(outdir / "%(title).90s-%(id)s.%(ext)s")
        fmt = f"bv*[height<={quality}]+ba/b[height<={quality}]/b"
        args = [exe, "--no-playlist", "-f", fmt, "--merge-output-format", ext, "-o", outtmpl, url]
        proc = _run(args, timeout=3600)
        if proc.returncode != 0:
            return ProcessorResult(False, _protected_message(proc.stderr) or "Falha ao baixar o vídeo.", {"details": proc.stderr[-1000:]})
        return ProcessorResult(True, "Vídeo salvo no NServer/Midias/Videos.", {"folder": str(outdir), "log": proc.stdout[-1000:]})

    def extract_audio(self, url: str, audio_format: str = "mp3", quality: str = "192") -> ProcessorResult:
        exe = _yt_dlp_cmd()
        if not exe:
            return ProcessorResult(False, "yt-dlp não encontrado.")
        outdir = self.media_root / "Audios"
        outdir.mkdir(parents=True, exist_ok=True)
        outtmpl = str(outdir / "%(title).90s-%(id)s.%(ext)s")
        args = [exe, "--no-playlist", "-x", "--audio-format", audio_format, "--audio-quality", quality + "K", "-o", outtmpl, url]
        proc = _run(args, timeout=3600)
        if proc.returncode != 0:
            return ProcessorResult(False, _protected_message(proc.stderr) or "Falha ao extrair o áudio. Verifique se o FFmpeg está instalado.", {"details": proc.stderr[-1000:]})
        return ProcessorResult(True, "Áudio salvo no NServer/Midias/Audios.", {"folder": str(outdir), "log": proc.stdout[-1000:]})

    def transcribe(self, url: str) -> ProcessorResult:
        folder = self.media_root / "Transcricoes"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = folder / f"transcricao-pendente-{stamp}.md"
        path.write_text("# Transcrição pendente\n\nMódulo de transcrição será conectado ao Whisper/OpenAI ou motor local em próxima etapa.\n\nURL: " + url + "\n", encoding="utf-8")
        return ProcessorResult(True, "Transcrição registrada como pendente. O conector Whisper será adicionado na próxima evolução.", {"file": str(path)})

    def viral_clips(self, url: str, count: int = 3, max_seconds: int = 60) -> ProcessorResult:
        folder = self.media_root / "Cortes"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = folder / f"plano-cortes-{stamp}.md"
        path.write_text(f"# Plano de cortes virais\n\nURL: {url}\nQuantidade: {count}\nDuração máxima: {max_seconds}s\n\nMódulo de detecção automática, legendas e renderização vertical será conectado em etapa futura.\n", encoding="utf-8")
        return ProcessorResult(True, "Plano de cortes criado. Renderização automática entra na próxima fase.", {"file": str(path)})

    def run(self, payload: dict[str, Any]) -> ProcessorResult:
        action = payload.get("action")
        url = payload.get("url", "").strip()
        if action == "analyze":
            return self.analyze(url)
        if action == "download_video":
            return self.download_video(url, str(payload.get("quality") or "720"), str(payload.get("format") or "mp4"))
        if action == "extract_audio":
            return self.extract_audio(url, str(payload.get("format") or "mp3"), str(payload.get("quality") or "192"))
        if action == "transcribe":
            return self.transcribe(url)
        if action == "viral_clips":
            return self.viral_clips(url, int(payload.get("count") or 3), int(payload.get("max_seconds") or 60))
        return ProcessorResult(False, "Ação desconhecida.")

    @staticmethod
    def _duration(seconds: int) -> str:
        seconds = int(seconds or 0)
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
