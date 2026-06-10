from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from typing import Any
from urllib.parse import urlparse

from .base import Processor, ProcessorResult
from .media import MediaManager, safe_name

PROTECTED_HINTS = (
    "login", "sign in", "signin", "private", "premium", "members", "cookies",
    "authenticate", "authentication", "forbidden", "not available", "permission",
)


def _yt_dlp_cmd() -> str | None:
    return shutil.which("yt-dlp")


def _ffmpeg_location() -> str | None:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    # If Nserver was updated from the web panel, new Python dependencies may not
    # have been installed by the .bat yet. Try a one-time local install here.
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "imageio-ffmpeg>=0.5.1"], capture_output=True, text=True, timeout=300)
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


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



def _is_intermediate_name(path: Path) -> bool:
    return bool(__import__("re").search(r"\.f\d+\.(mp4|webm|m4a|opus)$", path.name.lower()))

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
        # Prefer video+audio and force merge so MP4 does not come muted.
        # Fallback only to progressive formats that already contain audio.
        fmt = f"bv*[height<={quality}]+ba[ext=m4a]/bv*[height<={quality}]+ba/b[height<={quality}][acodec!=none]/b[acodec!=none]"
        args = [exe, "--no-playlist", "-f", fmt, "--merge-output-format", ext, "--postprocessor-args", "Merger:-c:v copy -c:a aac", "--print", "after_move:filepath", "-o", outtmpl, url]
        ffmpeg = _ffmpeg_location()
        if not ffmpeg:
            return ProcessorResult(False, "FFmpeg não encontrado. Feche o Nserver, abra o iniciar-nserver.bat e aguarde instalar as dependências antes de baixar MP4 com áudio.")
        args[1:1] = ["--ffmpeg-location", ffmpeg]
        proc = _run(args, timeout=3600)
        if proc.returncode != 0:
            return ProcessorResult(False, _protected_message(proc.stderr) or "Falha ao baixar o vídeo.", {"details": proc.stderr[-1000:]})
        if ", acodec none" in proc.stdout.lower() or "acodec none" in proc.stderr.lower():
            return ProcessorResult(False, "O vídeo foi baixado sem áudio. Reinstale/atualize as dependências pelo iniciar-nserver.bat e tente novamente.", {"details": (proc.stdout + proc.stderr)[-1200:]})
        file_path = self._last_existing_file(proc.stdout, outdir, preferred_ext=ext)
        if not file_path or file_path.stat().st_size <= 0:
            return ProcessorResult(False, "O vídeo foi processado, mas o arquivo final mesclado não foi encontrado. O Nserver não vai salvar arquivo intermediário sem áudio.", {"folder": str(outdir), "log": proc.stdout[-1000:], "details": proc.stderr[-1000:]})
        if _is_intermediate_name(file_path):
            return ProcessorResult(False, "O download gerou apenas um arquivo intermediário sem áudio. Verifique se as dependências foram instaladas e tente novamente.", {"folder": str(outdir), "file": str(file_path), "log": proc.stdout[-1000:], "details": proc.stderr[-1000:]})
        self._cleanup_intermediates(outdir, file_path)
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
        ffmpeg = _ffmpeg_location()
        if not ffmpeg:
            return ProcessorResult(False, "FFmpeg não encontrado. O Nserver tentou instalar automaticamente o FFmpeg empacotado, mas não conseguiu. Feche e abra pelo iniciar-nserver.bat e tente novamente.")
        args[1:1] = ["--ffmpeg-location", ffmpeg]
        proc = _run(args, timeout=3600)
        if proc.returncode != 0:
            return ProcessorResult(False, _protected_message(proc.stderr) or "Falha ao extrair o áudio. Verifique se o FFmpeg está instalado.", {"details": proc.stderr[-1000:]})
        file_path = self._last_existing_file(proc.stdout, outdir, preferred_ext=audio_format)
        if not file_path or file_path.stat().st_size <= 0:
            return ProcessorResult(False, "O áudio foi processado, mas o arquivo final não foi encontrado corretamente. Tente novamente ou use Salvar no Nserver.", {"folder": str(outdir), "log": proc.stdout[-1000:]})
        self._cleanup_intermediates(outdir, file_path)
        data = {"folder": str(outdir), "file": str(file_path), "filename": file_path.name, "size_bytes": file_path.stat().st_size, "downloadable": True, "delete_after_download": destination == "device", "log": proc.stdout[-1000:]}
        if destination == "device":
            return ProcessorResult(True, "Áudio pronto para baixar neste dispositivo.", data)
        return ProcessorResult(True, "Áudio salvo no NServer/Midias/Audios.", data)

    def _last_existing_file(self, stdout: str, folder: Path, preferred_ext: str | None = None) -> Path | None:
        preferred = (preferred_ext or "").lower().lstrip(".")
        stdout_candidates: list[Path] = []
        for line in stdout.splitlines():
            value = line.strip()
            if not value:
                continue
            path = Path(value)
            if path.exists() and path.is_file() and path.stat().st_size > 0 and not path.name.endswith((".part", ".ytdl")):
                stdout_candidates.append(path)
        # Trust yt-dlp's after_move final path first, but never accept .fNNN intermediates as final.
        for path in reversed(stdout_candidates):
            if preferred and path.suffix.lower().lstrip(".") != preferred:
                continue
            if not _is_intermediate_name(path):
                return path
        files = [p for p in folder.rglob("*") if p.is_file() and p.stat().st_size > 0 and not p.name.endswith((".part", ".ytdl"))]
        unique = {p.resolve(): p for p in [*stdout_candidates, *files]}
        files = list(unique.values())
        if not files:
            return None
        if preferred:
            preferred_files = [p for p in files if p.suffix.lower().lstrip(".") == preferred and not _is_intermediate_name(p)]
            if preferred_files:
                return max(preferred_files, key=lambda p: (p.stat().st_mtime, p.stat().st_size))
        final_files = [p for p in files if not _is_intermediate_name(p)]
        if final_files:
            return max(final_files, key=lambda p: (p.stat().st_mtime, p.stat().st_size))
        return None

    def _cleanup_intermediates(self, folder: Path, final_file: Path):
        for p in folder.rglob("*"):
            if not p.is_file() or p.resolve() == final_file.resolve():
                continue
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass

    def _write_docx(self, path: Path, title: str, text: str):
        def esc_xml(v: str) -> str:
            return v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        body = "".join(f"<w:p><w:r><w:t>{esc_xml(line)}</w:t></w:r></w:p>" for line in [title, "", *text.splitlines()])
        document = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'
        content_types = '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
        rels = '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'
        with ZipFile(path, "w", ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", content_types)
            z.writestr("_rels/.rels", rels)
            z.writestr("word/document.xml", document)

    def _write_pdf(self, path: Path, title: str, text: str):
        lines = [title, "", *text.splitlines()]
        safe = []
        for line in lines[:1200]:
            line = line.encode("latin-1", "replace").decode("latin-1")
            safe.append(line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")[:95])
        stream = "BT /F1 11 Tf 50 790 Td 14 TL " + " T* ".join(f"({line}) Tj" for line in safe) + " ET"
        objects = [
            "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
            "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj",
            "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj",
            "4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj",
            f"5 0 obj << /Length {len(stream.encode('latin-1'))} >> stream\n{stream}\nendstream endobj",
        ]
        content = "%PDF-1.4\n"
        offsets = [0]
        for obj in objects:
            offsets.append(len(content.encode("latin-1")))
            content += obj + "\n"
        xref = len(content.encode("latin-1"))
        content += f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n"
        for off in offsets[1:]:
            content += f"{off:010d} 00000 n \n"
        content += f"trailer << /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF"
        path.write_bytes(content.encode("latin-1"))

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

    def _download_video_temp(self, url: str, tmp: Path, quality: str = "720") -> Path:
        exe = _yt_dlp_cmd()
        if not exe:
            raise RuntimeError("yt-dlp não encontrado.")
        outtmpl = str(tmp / "%(title).80s-%(id)s.%(ext)s")
        fmt = f"bv*[height<={quality}]+ba[ext=m4a]/bv*[height<={quality}]+ba/b[height<={quality}][acodec!=none]/b[acodec!=none]"
        args = [exe, "--no-playlist", "-f", fmt, "--merge-output-format", "mp4", "--print", "after_move:filepath", "-o", outtmpl, url]
        ffmpeg = _ffmpeg_location()
        if ffmpeg:
            args[1:1] = ["--ffmpeg-location", ffmpeg]
        proc = _run(args, timeout=3600)
        if proc.returncode != 0:
            raise RuntimeError(_protected_message(proc.stderr) or "Não consegui baixar o vídeo para processamento.")
        path = self._last_existing_file(proc.stdout, tmp, preferred_ext="mp4")
        if path and path.exists():
            return path
        files = [p for p in tmp.rglob("*") if p.is_file()]
        if files:
            return max(files, key=lambda p: p.stat().st_size)
        raise RuntimeError("Vídeo baixado não encontrado.")

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

    def transcribe_path(self, media_path: Path, formats: list[str] | None = None, title: str | None = None, origin: str = "Biblioteca") -> ProcessorResult:
        formats = [str(f).lower() for f in (formats or ["txt"])]
        formats = [f for f in formats if f in {"txt", "md", "docx", "pdf"}]
        if not formats:
            return ProcessorResult(False, "Selecione pelo menos um formato de transcrição: TXT, MD, DOCX ou PDF.")
        folder = self.media_root / "Transcricoes"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        try:
            title = title or media_path.stem or "transcricao"
            base = folder / f"{_safe_name(title)}-{stamp}"
            if self._transcription_provider() == "openai" and media_path.stat().st_size > 24 * 1024 * 1024:
                return ProcessorResult(False, "O arquivo ficou maior que o limite do Whisper API (~25 MB). Use modo Local ou divida o arquivo.")
            if self._transcription_provider() == "openai":
                data = self._openai_transcribe(media_path)
            else:
                data = self._local_transcribe(media_path)
            text = (data.get("text") or "").strip()
            segments = data.get("segments") or []
            lines = [f"Transcrição — {title}", "", f"Origem: {origin}", f"Gerado em: {time.strftime('%Y-%m-%d %H:%M:%S')}", "", "Texto completo", "", text, "", "Com timestamps", ""]
            if segments:
                for seg in segments:
                    lines.append(f"[{_format_ts(seg.get('start', 0))}] {seg.get('text', '').strip()}")
            else:
                lines.append("Timestamps não retornados pelo provedor.")
            full_text = "\n".join(lines) + "\n"
            files: dict[str, str] = {}
            if "txt" in formats:
                txt_path = base.with_suffix(".txt")
                txt_path.write_text(full_text, encoding="utf-8")
                files["txt"] = str(txt_path)
            if "md" in formats:
                md_path = base.with_suffix(".md")
                md_path.write_text(full_text, encoding="utf-8")
                files["md"] = str(md_path)
            if "docx" in formats:
                docx_path = base.with_suffix(".docx")
                self._write_docx(docx_path, f"Transcrição — {title}", full_text)
                files["docx"] = str(docx_path)
            if "pdf" in formats:
                pdf_path = base.with_suffix(".pdf")
                self._write_pdf(pdf_path, f"Transcrição — {title}", full_text)
                files["pdf"] = str(pdf_path)
            primary = next(iter(files.values()), "")
            return ProcessorResult(True, "Transcrição concluída no(s) formato(s) selecionado(s).", {**files, "file": primary, "folder": str(folder)})
        except Exception as exc:
            return ProcessorResult(False, str(exc))

    def transcribe(self, url: str, formats: list[str] | None = None) -> ProcessorResult:
        if not url.startswith(("http://", "https://")):
            return ProcessorResult(False, "Cole uma URL válida começando com http:// ou https://")
        try:
            info = self.analyze(url)
            title = ((info.data or {}).get("title") if info.ok else "transcricao") or "transcricao"
            with tempfile.TemporaryDirectory() as td:
                audio_path = self._download_audio_temp(url, Path(td))
                return self.transcribe_path(audio_path, formats, title=title, origin=url)
        except Exception as exc:
            return ProcessorResult(False, str(exc))

    def _video_duration(self, path: Path) -> float:
        # Prefer ffprobe, but Windows installs sometimes have ffmpeg without ffprobe.
        try:
            ffprobe = shutil.which("ffprobe")
            if ffprobe:
                out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], capture_output=True, text=True, check=True)
                value = float(out.stdout.strip() or 0)
                if value > 0:
                    return value
        except Exception:
            pass
        try:
            ffmpeg = _ffmpeg_location() or shutil.which("ffmpeg") or "ffmpeg"
            out = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True)
            text = (out.stderr or "") + (out.stdout or "")
            match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
            if match:
                h, m, s = match.groups()
                return int(h) * 3600 + int(m) * 60 + float(s)
        except Exception:
            pass
        return 0.0

    def viral_clips_from_file(self, source: Path, count: int = 3, max_seconds: int = 60, aspect: str = "9:16") -> ProcessorResult:
        folder = self.media_root / "Cortes"
        folder.mkdir(parents=True, exist_ok=True)
        duration = self._video_duration(source)
        if duration <= 0:
            return ProcessorResult(False, "Não consegui detectar a duração do vídeo para gerar cortes.")
        count = max(1, min(int(count or 3), 10))
        max_seconds = max(15, min(int(max_seconds or 60), 120))
        window = min(max_seconds, max(1, int(duration / max(count, 1))), int(duration))
        anchors = [0.18, 0.32, 0.50, 0.68, 0.82, 0.12, 0.42, 0.58, 0.74, 0.90][:count]
        clips = []
        stamp = time.strftime("%Y%m%d-%H%M%S")
        title = _safe_name(source.stem)
        if aspect == "16:9":
            vf = "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2"
        else:
            vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
        for idx, pct in enumerate(anchors, start=1):
            start = max(0, int(duration * pct) - window // 2)
            if start + window > duration:
                start = max(0, int(duration - window))
            out = folder / f"{title}-corte-{idx}-{stamp}.mp4"
            cmd = ["ffmpeg", "-y", "-ss", str(start), "-i", str(source), "-t", str(window), "-vf", vf, "-af", "afade=t=in:st=0:d=0.03,afade=t=out:st=" + str(max(0, window - 0.03)) + ":d=0.03", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            clips.append({"index": idx, "start": _format_ts(start), "end": _format_ts(start + window), "file": str(out), "title": f"Corte {idx} — {source.stem}"})
        return ProcessorResult(True, "Cortes reais gerados e salvos na Biblioteca.", {"folder": str(folder), "files": [c["file"] for c in clips], "clips": clips, "file": clips[0]["file"] if clips else ""})

    def viral_clips(self, url: str, count: int = 3, max_seconds: int = 60) -> ProcessorResult:
        folder = self.media_root / "Cortes"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = folder / f"plano-cortes-{stamp}.md"
        info = self.analyze(url)
        title = ((info.data or {}).get("title") if info.ok else "video") or "video"
        duration = int(((info.data or {}).get("duration") if info.ok else 0) or 0)
        count = max(1, min(int(count or 3), 10))
        max_seconds = max(15, min(int(max_seconds or 60), 120))
        clips = []
        if duration > 0:
            window = min(max_seconds, max(15, duration // max(count, 1)))
            anchors = [0.18, 0.32, 0.50, 0.68, 0.82, 0.12, 0.42, 0.58, 0.74, 0.90][:count]
            for idx, pct in enumerate(anchors, start=1):
                start = max(0, int(duration * pct) - window // 2)
                end = min(duration, start + window)
                score = max(72, 96 - (idx - 1) * 4)
                clips.append((idx, start, end, score))
        lines = [
            "# Plano de cortes virais",
            "",
            f"Título: {title}",
            f"URL: {url}",
            f"Quantidade solicitada: {count}",
            f"Duração máxima: {max_seconds}s",
            f"Duração do vídeo: {_format_ts(duration)}" if duration else "Duração do vídeo: não informada",
            "",
            "## Ranking inicial",
            "",
        ]
        if clips:
            for idx, start, end, score in clips:
                lines += [
                    f"### Corte {idx} — nota {score}/100",
                    f"- Trecho sugerido: {_format_ts(start)} até {_format_ts(end)}",
                    "- Motivo: janela candidata por posição de retenção/hype. Na próxima fase será combinada com transcrição, mudanças de assunto e sinais públicos quando disponíveis.",
                    "- Formato alvo: vertical 9:16 com legenda dinâmica.",
                    "",
                ]
        else:
            lines += ["Ainda não consegui estimar trechos porque a duração não veio na análise.", ""]
        lines += [
            "## Próxima fase técnica",
            "",
            "- Baixar/usar vídeo local da Biblioteca.",
            "- Transcrever e detectar frases fortes, viradas de assunto e momentos com gancho.",
            "- Quando disponível, considerar sinais públicos como barra de replay/hype do YouTube.",
            "- Renderizar cortes automaticamente com FFmpeg e salvar na Biblioteca.",
            "",
        ]
        path.write_text("\n".join(lines), encoding="utf-8")
        return ProcessorResult(True, "Plano/ranking de cortes criado. Renderização automática entra na próxima fase.", {"file": str(path), "clips": [{"index": i, "start": _format_ts(s), "end": _format_ts(e), "score": score} for i, s, e, score in clips]})

    def run(self, payload: dict[str, Any]) -> ProcessorResult:
        action = payload.get("action")
        url = payload.get("url", "").strip()
        source_type = payload.get("source_type") or ("library" if payload.get("library_path") else "url")
        if action in {"transcribe", "viral_clips"} and source_type == "library":
            try:
                media_path = MediaManager(self.root).resolve(payload.get("library_path", ""))
            except Exception as exc:
                return ProcessorResult(False, str(exc))
            if action == "transcribe":
                return self.transcribe_path(media_path, payload.get("formats") or ["txt"], title=media_path.stem, origin="Biblioteca")
            return self.viral_clips_from_file(media_path, int(payload.get("count") or 3), int(payload.get("max_seconds") or 60), str(payload.get("aspect") or "9:16"))
        if action == "analyze":
            return self.analyze(url)
        if action == "download_video":
            return self.download_video(url, str(payload.get("quality") or "720"), str(payload.get("format") or "mp4"), str(payload.get("destination") or "server"))
        if action == "extract_audio":
            return self.extract_audio(url, str(payload.get("format") or "mp3"), str(payload.get("quality") or "192"), str(payload.get("destination") or "server"))
        if action == "transcribe":
            return self.transcribe(url, payload.get("formats") or ["txt"])
        if action == "viral_clips":
            if payload.get("render"):
                try:
                    with tempfile.TemporaryDirectory() as td:
                        video_path = self._download_video_temp(url, Path(td), str(payload.get("quality") or "720"))
                        return self.viral_clips_from_file(video_path, int(payload.get("count") or 3), int(payload.get("max_seconds") or 60), str(payload.get("aspect") or "9:16"))
                except Exception as exc:
                    return ProcessorResult(False, str(exc))
            return self.viral_clips(url, int(payload.get("count") or 3), int(payload.get("max_seconds") or 60))
        return ProcessorResult(False, "Ação desconhecida.")

    @staticmethod
    def _duration(seconds: int) -> str:
        seconds = int(seconds or 0)
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
