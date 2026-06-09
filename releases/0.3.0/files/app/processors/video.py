from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
import uuid
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


def _format_ts(seconds: float | int) -> str:
    total = int(float(seconds or 0))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


class VideoProcessor(Processor):
    id = "video-downloader"
    name = "Downloader e Processador de Vídeos"
    description = "Analisa links públicos, baixa vídeo/áudio, transcreve e prepara cortes."

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

    def download_video(self, url: str, quality: str = "720", ext: str = "mp4", destination: str = "server") -> ProcessorResult:
        exe = _yt_dlp_cmd()
        if not exe:
            return ProcessorResult(False, "yt-dlp não encontrado.")
        outdir = self.media_root / "Videos"
        if destination == "device":
            outdir = self.media_root / "_Temporarios" / ("video-" + uuid.uuid4().hex)
        outdir.mkdir(parents=True, exist_ok=True)
        outtmpl = str(outdir / "%(title).90s-%(id)s.%(ext)s")
        fmt = f"bv*[height<={quality}]+ba/b[height<={quality}]/b"
        args = [exe, "--no-playlist", "-f", fmt, "--merge-output-format", ext, "--print", "after_move:filepath", "-o", outtmpl, url]
        proc = _run(args, timeout=3600)
        if proc.returncode != 0:
            return ProcessorResult(False, _protected_message(proc.stderr) or "Falha ao baixar o vídeo.", {"details": proc.stderr[-1000:]})
        file_path = self._last_existing_file(proc.stdout, outdir, preferred_ext=ext)
        if not file_path or file_path.stat().st_size <= 0:
            return ProcessorResult(False, "O vídeo foi processado, mas o arquivo final não foi encontrado corretamente. Tente novamente ou use Salvar no Nserver.", {"folder": str(outdir), "log": proc.stdout[-1000:]})
        data = {"folder": str(outdir), "file": str(file_path), "filename": file_path.name, "size_bytes": file_path.stat().st_size, "downloadable": True, "delete_after_download": destination == "device", "log": proc.stdout[-1000:]}
        if destination == "device":
            return ProcessorResult(True, "Vídeo pronto para baixar neste dispositivo.", data)
        return ProcessorResult(True, "Vídeo salvo no NServer/Midias/Videos.", data)

    def extract_audio(self, url: str, audio_format: str = "mp3", quality: str = "192", destination: str = "server") -> ProcessorResult:
        exe = _yt_dlp_cmd()
        if not exe:
            return ProcessorResult(False, "yt-dlp não encontrado.")
        outdir = self.media_root / "Audios"
        if destination == "device":
            outdir = self.media_root / "_Temporarios" / ("audio-" + uuid.uuid4().hex)
        outdir.mkdir(parents=True, exist_ok=True)
        outtmpl = str(outdir / "%(title).90s-%(id)s.%(ext)s")
        args = [exe, "--no-playlist", "-x", "--audio-format", audio_format, "--audio-quality", quality + "K", "--print", "after_move:filepath", "-o", outtmpl, url]
        proc = _run(args, timeout=3600)
        if proc.returncode != 0:
            return ProcessorResult(False, _protected_message(proc.stderr) or "Falha ao extrair o áudio. Verifique se o FFmpeg está instalado.", {"details": proc.stderr[-1000:]})
        file_path = self._last_existing_file(proc.stdout, outdir, preferred_ext=audio_format)
        if not file_path or file_path.stat().st_size <= 0:
            return ProcessorResult(False, "O áudio foi processado, mas o arquivo final não foi encontrado corretamente. Tente novamente ou use Salvar no Nserver.", {"folder": str(outdir), "log": proc.stdout[-1000:]})
        data = {"folder": str(outdir), "file": str(file_path), "filename": file_path.name, "size_bytes": file_path.stat().st_size, "downloadable": True, "delete_after_download": destination == "device", "log": proc.stdout[-1000:]}
        if destination == "device":
            return ProcessorResult(True, "Áudio pronto para baixar neste dispositivo.", data)
        return ProcessorResult(True, "Áudio salvo no NServer/Midias/Audios.", data)

    def _last_existing_file(self, stdout: str, folder: Path, preferred_ext: str | None = None) -> Path | None:
        preferred = (preferred_ext or "").lower().lstrip(".")
        candidates = []
        for line in stdout.splitlines():
            value = line.strip()
            if not value:
                continue
            path = Path(value)
            if path.exists() and path.is_file() and path.stat().st_size > 0 and not path.name.endswith((".part", ".ytdl")):
                candidates.append(path)
        candidates.extend(p for p in folder.rglob("*") if p.is_file() and p.stat().st_size > 0 and not p.name.endswith((".part", ".ytdl")))
        # Dedup while preserving actual Path objects.
        unique = {p.resolve(): p for p in candidates}
        files = list(unique.values())
        if not files:
            return None
        if preferred:
            preferred_files = [p for p in files if p.suffix.lower().lstrip(".") == preferred]
            if preferred_files:
                return max(preferred_files, key=lambda p: (p.stat().st_mtime, p.stat().st_size))
        # For video downloads, avoid picking tiny/intermediate audio-only webm if a larger merged file exists.
        return max(files, key=lambda p: (p.stat().st_mtime, p.stat().st_size))

    def _settings(self) -> dict[str, Any]:
        path = self.root / "userdata" / "config.json"
        data: dict[str, Any] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        return data

    def _openai_key(self) -> str:
        return (os.environ.get("OPENAI_API_KEY") or self._settings().get("openai_api_key") or "").strip()

    def _openai_base(self) -> str:
        return (os.environ.get("OPENAI_BASE_URL") or self._settings().get("openai_base_url") or "https://api.openai.com/v1").rstrip("/")

    def _transcription_provider(self) -> str:
        return (self._settings().get("transcription_provider") or "local").strip().lower()

    def _local_model(self) -> str:
        return (self._settings().get("local_whisper_model") or "base").strip()

    def _download_audio_temp(self, url: str, tmp: Path) -> Path:
        exe = _yt_dlp_cmd()
        if not exe:
            raise RuntimeError("yt-dlp não encontrado.")
        outtmpl = str(tmp / "%(id)s.%(ext)s")
        proc = _run([exe, "--no-playlist", "-f", "ba/bestaudio/b", "--print", "after_move:filepath", "-o", outtmpl, url], timeout=3600)
        if proc.returncode != 0:
            raise RuntimeError(_protected_message(proc.stderr) or "Não consegui baixar o áudio para transcrição.")
        candidates = [Path(line.strip()) for line in proc.stdout.splitlines() if line.strip()]
        for path in reversed(candidates):
            if path.exists() and path.is_file():
                return path
        files = [p for p in tmp.iterdir() if p.is_file()]
        if files:
            return max(files, key=lambda p: p.stat().st_size)
        raise RuntimeError("Áudio baixado não encontrado.")

    def _local_transcribe(self, audio_path: Path) -> dict[str, Any]:
        try:
            from faster_whisper import WhisperModel
        except Exception as exc:
            raise RuntimeError("Transcrição local ainda não está instalada. Feche o Nserver e abra o iniciar-nserver.bat novamente para instalar as dependências, ou rode: python -m pip install faster-whisper") from exc
        model_name = self._local_model()
        try:
            model = WhisperModel(model_name, device="cpu", compute_type="int8")
            segments_iter, info = model.transcribe(str(audio_path), vad_filter=True, beam_size=5)
            segments = []
            texts = []
            for seg in segments_iter:
                text = (seg.text or "").strip()
                texts.append(text)
                segments.append({"start": float(seg.start), "end": float(seg.end), "text": text})
            return {
                "text": " ".join(t for t in texts if t).strip(),
                "language": getattr(info, "language", None),
                "duration": getattr(info, "duration", None),
                "segments": segments,
                "provider": "local",
                "model": model_name,
            }
        except Exception as exc:
            raise RuntimeError(f"Falha na transcrição local: {exc}") from exc

    def _openai_transcribe(self, audio_path: Path) -> dict[str, Any]:
        key = self._openai_key()
        if not key:
            raise RuntimeError("Configure uma chave OpenAI na tela da ferramenta antes de transcrever.")
        boundary = "----NserverBoundary" + uuid.uuid4().hex
        fields = [
            ("model", "whisper-1"),
            ("response_format", "verbose_json"),
            ("timestamp_granularities[]", "segment"),
        ]
        body = bytearray()
        for name, value in fields:
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="file"; filename="{audio_path.name}"\r\n'.encode())
        body.extend(b"Content-Type: application/octet-stream\r\n\r\n")
        body.extend(audio_path.read_bytes())
        body.extend(f"\r\n--{boundary}--\r\n".encode())
        req = urllib.request.Request(
            self._openai_base() + "/audio/transcriptions",
            data=bytes(body),
            headers={"Authorization": f"Bearer {key}", "Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=1800) as res:
                return json.loads(res.read().decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Falha na transcrição via Whisper/OpenAI: {exc}") from exc

    def transcribe(self, url: str) -> ProcessorResult:
        if not url.startswith(("http://", "https://")):
            return ProcessorResult(False, "Cole uma URL válida começando com http:// ou https://")
        folder = self.media_root / "Transcricoes"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        try:
            info = self.analyze(url)
            title = ((info.data or {}).get("title") if info.ok else "transcricao") or "transcricao"
            base = folder / f"{_safe_name(title)}-{stamp}"
            with tempfile.TemporaryDirectory() as td:
                audio_path = self._download_audio_temp(url, Path(td))
                if self._transcription_provider() == "openai" and audio_path.stat().st_size > 24 * 1024 * 1024:
                    return ProcessorResult(False, "O áudio ficou maior que o limite do Whisper API (~25 MB). Vou precisar adicionar divisão automática em partes na próxima etapa.")
                if self._transcription_provider() == "openai":
                    data = self._openai_transcribe(audio_path)
                else:
                    data = self._local_transcribe(audio_path)
            text = (data.get("text") or "").strip()
            segments = data.get("segments") or []
            txt_path = base.with_suffix(".txt")
            md_path = base.with_suffix(".md")
            json_path = base.with_suffix(".json")
            txt_path.write_text(text + "\n", encoding="utf-8")
            lines = [f"# Transcrição — {title}", "", f"URL: {url}", f"Gerado em: {time.strftime('%Y-%m-%d %H:%M:%S')}", "", "## Texto completo", "", text, "", "## Com timestamps", ""]
            if segments:
                for seg in segments:
                    lines.append(f"[{_format_ts(seg.get('start', 0))}] {seg.get('text', '').strip()}")
            else:
                lines.append("Timestamps não retornados pelo provedor.")
            md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            return ProcessorResult(True, "Transcrição concluída e salva em NServer/Midias/Transcricoes.", {"txt": str(txt_path), "md": str(md_path), "json": str(json_path)})
        except Exception as exc:
            return ProcessorResult(False, str(exc))

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
            return self.download_video(url, str(payload.get("quality") or "720"), str(payload.get("format") or "mp4"), str(payload.get("destination") or "server"))
        if action == "extract_audio":
            return self.extract_audio(url, str(payload.get("format") or "mp3"), str(payload.get("quality") or "192"), str(payload.get("destination") or "server"))
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
