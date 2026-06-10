from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .base import Processor, ProcessorResult


VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg"}


class VideoEditorProcessor(Processor):
    id = "video-editor"
    name = "Editor de Vídeo"
    description = "Editor simples inspirado no fluxo do Kael/video-use: planeje cortes, renderize preview/final e salve na Biblioteca."

    def __init__(self, root: Path):
        super().__init__(root)
        self.output_dir = self.media_root / "Editados"
        self.projects_dir = root / "userdata" / "editor-projects"
        self.temp_dir = self.media_root / "_Temporarios" / "editor"
        for folder in (self.output_dir, self.projects_dir, self.temp_dir):
            folder.mkdir(parents=True, exist_ok=True)

    def run(self, payload: dict[str, Any]) -> ProcessorResult:
        action = payload.get("action", "")
        try:
            if action == "list_sources":
                return ProcessorResult(True, "Vídeos carregados.", {"items": self.list_sources()})
            if action == "probe":
                source = self.resolve_media(payload.get("source", ""))
                return ProcessorResult(True, "Vídeo analisado.", self.probe(source))
            if action == "auto_plan":
                source = self.resolve_media(payload.get("source", ""))
                data = self.probe(source)
                segments = self.auto_plan(float(data.get("duration") or 0), int(payload.get("segment_seconds") or 30))
                return ProcessorResult(True, "Timeline inicial criada. Ajuste os trechos antes de renderizar.", {"segments": segments, "source": source.relative_to(self.media_root).as_posix(), **data})
            if action == "render":
                source = self.resolve_media(payload.get("source", ""))
                data = self.render(source, payload)
                return ProcessorResult(True, "Renderização concluída e salva na Biblioteca.", data)
            return ProcessorResult(False, "Ação de editor desconhecida.")
        except subprocess.CalledProcessError as exc:
            details = (exc.stderr or b"").decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or exc)
            return ProcessorResult(False, f"FFmpeg falhou: {details[-900:]}", {"details": details[-2000:]})
        except Exception as exc:
            return ProcessorResult(False, f"Não consegui executar o editor: {exc}")

    def resolve_media(self, rel: str) -> Path:
        if not rel:
            raise ValueError("Escolha um vídeo da Biblioteca.")
        target = (self.media_root / rel).resolve()
        if self.media_root.resolve() not in target.parents or not target.is_file():
            raise ValueError("Arquivo de mídia inválido.")
        if target.suffix.lower() not in VIDEO_EXTS:
            raise ValueError("O editor aceita vídeos MP4, MOV, MKV ou WEBM.")
        return target

    def list_sources(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in self.media_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in VIDEO_EXTS:
                continue
            try:
                rel = path.resolve().relative_to(self.media_root.resolve()).as_posix()
            except Exception:
                continue
            if rel.startswith("_Temporarios/"):
                continue
            stat = path.stat()
            items.append({
                "relative": rel,
                "name": path.name,
                "size_bytes": stat.st_size,
                "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
            })
        return sorted(items, key=lambda x: x["modified"], reverse=True)

    def probe(self, source: Path) -> dict[str, Any]:
        cmd = [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(source),
        ]
        out = subprocess.run(cmd, check=True, capture_output=True, text=True)
        data = json.loads(out.stdout or "{}")
        fmt = data.get("format", {})
        video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
        audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})
        duration = float(fmt.get("duration") or video.get("duration") or 0)
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
        return {
            "source": source.relative_to(self.media_root).as_posix(),
            "filename": source.name,
            "duration": duration,
            "duration_text": self.human_duration(duration),
            "width": width,
            "height": height,
            "fps": video.get("r_frame_rate") or "",
            "video_codec": video.get("codec_name") or "",
            "audio_codec": audio.get("codec_name") or "sem áudio",
        }

    def auto_plan(self, duration: float, segment_seconds: int = 30) -> list[dict[str, Any]]:
        if duration <= 0:
            return []
        segment_seconds = max(5, min(300, segment_seconds))
        if duration <= segment_seconds:
            return [{"start": 0, "end": round(duration, 2), "label": "Vídeo inteiro"}]
        segments = []
        start = 0.0
        idx = 1
        while start < duration:
            end = min(duration, start + segment_seconds)
            segments.append({"start": round(start, 2), "end": round(end, 2), "label": f"Parte {idx}"})
            start = end
            idx += 1
        return segments

    def render(self, source: Path, payload: dict[str, Any]) -> dict[str, Any]:
        segments = self.clean_segments(payload.get("segments") or [], float(self.probe(source).get("duration") or 0))
        if not segments:
            raise ValueError("Adicione pelo menos um trecho válido na timeline.")
        mode = payload.get("mode", "preview")
        title = self.slug(payload.get("title") or source.stem)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        suffix = "preview" if mode == "preview" else "final"
        out = self.output_dir / f"{title}-{suffix}-{stamp}.mp4"
        work = self.temp_dir / f"job-{stamp}-{title}"
        work.mkdir(parents=True, exist_ok=True)
        aspect = payload.get("aspect", "original")
        grade = payload.get("grade", "none")
        try:
            clips = []
            for i, seg in enumerate(segments, start=1):
                clip = work / f"seg-{i:03d}.mp4"
                self.extract_segment(source, float(seg["start"]), float(seg["end"]), clip, aspect, grade, preview=(mode == "preview"))
                clips.append(clip)
            concat_file = work / "concat.txt"
            concat_file.write_text("".join(f"file '{clip.as_posix()}'\n" for clip in clips), encoding="utf-8")
            cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", "-movflags", "+faststart", str(out)]
            subprocess.run(cmd, check=True, capture_output=True)
            project = {
                "source": source.relative_to(self.media_root).as_posix(),
                "output": out.relative_to(self.media_root).as_posix(),
                "segments": segments,
                "aspect": aspect,
                "grade": grade,
                "mode": mode,
                "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            project_path = self.projects_dir / f"{out.stem}.json"
            project_path.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
            return {
                "file": str(out),
                "relative": out.relative_to(self.media_root).as_posix(),
                "filename": out.name,
                "project": str(project_path),
                "segments": segments,
                "duration": sum(float(s["end"]) - float(s["start"]) for s in segments),
            }
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def clean_segments(self, raw: list[Any], duration: float) -> list[dict[str, Any]]:
        out = []
        for item in raw:
            try:
                start = max(0.0, float(item.get("start", 0)))
                end = min(duration, float(item.get("end", 0))) if duration > 0 else float(item.get("end", 0))
                if end - start < 0.2:
                    continue
                label = str(item.get("label") or f"Trecho {len(out)+1}")[:80]
                out.append({"start": round(start, 3), "end": round(end, 3), "label": label})
            except Exception:
                continue
        out.sort(key=lambda x: x["start"])
        return out

    def extract_segment(self, source: Path, start: float, end: float, out: Path, aspect: str, grade: str, preview: bool) -> None:
        duration = max(0.2, end - start)
        vf = self.video_filter(aspect, grade, preview)
        fade_out = max(0.0, duration - 0.03)
        af = f"afade=t=in:st=0:d=0.03,afade=t=out:st={fade_out:.3f}:d=0.03"
        cmd = [
            "ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}",
            "-vf", vf, "-af", af,
            "-c:v", "libx264", "-preset", "ultrafast" if preview else "fast", "-crf", "28" if preview else "20",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", str(out),
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def video_filter(self, aspect: str, grade: str, preview: bool) -> str:
        max_w = 1280 if preview else 1920
        max_h = 720 if preview else 1080
        filters: list[str] = []
        if aspect == "9:16":
            filters.append("scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920")
        elif aspect == "1:1":
            filters.append("scale=1080:1080:force_original_aspect_ratio=increase,crop=1080:1080")
        elif aspect == "16:9":
            filters.append("scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2")
        else:
            filters.append(f"scale='min({max_w},iw)':-2")
        if grade == "punch":
            filters.append("eq=contrast=1.08:saturation=1.05:brightness=0.01")
        elif grade == "warm":
            filters.append("eq=contrast=1.05:saturation=0.98:gamma_r=1.03:gamma_b=0.97")
        return ",".join(filters)

    @staticmethod
    def human_duration(seconds: float) -> str:
        seconds = max(0, int(round(seconds)))
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    @staticmethod
    def slug(value: str) -> str:
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-_.")
        return (slug or "video-editado")[:80]
