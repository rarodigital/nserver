#!/usr/bin/env python3
"""Silence Cut v1: FFmpeg-based first-pass silence remover for talking-head ads."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

PROFILES = {
    "vitor-v1": {
        "threshold": "-32dB",
        "min_silence": 0.35,
        "keep_after_speech": 0.14,
        "keep_before_speech": 0.10,
        "min_clip": 0.55,
        "description": "Leitura em blocos: corta pausas/viradas/sussurros baixos sem destruir frases diretas.",
    },
    "vitor-dry-v1": {
        "threshold": "-32dB",
        "min_silence": 0.25,
        "keep_after_speech": 0.06,
        "keep_before_speech": 0.04,
        "min_clip": 0.25,
        "merge_tiny_keeps": False,
        "description": "Corte seco para Vitor: frases entrando uma atrás da outra com mínimo respiro.",
    },
    "vitor-dry-v2": {
        "threshold": "-30dB",
        "min_silence": 0.22,
        "keep_after_speech": 0.04,
        "keep_before_speech": 0.03,
        "min_clip": 0.25,
        "merge_tiny_keeps": False,
        "description": "Corte ainda mais seco para Vitor: remove olhadas/pausas curtas e evita religar silêncio por clipes pequenos.",
    },
    "conservador": {
        "threshold": "-35dB",
        "min_silence": 0.55,
        "keep_after_speech": 0.22,
        "keep_before_speech": 0.12,
        "min_clip": 0.70,
        "description": "Preserva contexto; corta apenas pausas óbvias.",
    },
    "equilibrado": {
        "threshold": "-32dB",
        "min_silence": 0.40,
        "keep_after_speech": 0.18,
        "keep_before_speech": 0.10,
        "min_clip": 0.55,
        "description": "Padrão geral para anúncios curtos.",
    },
    "agressivo": {
        "threshold": "-28dB",
        "min_silence": 0.25,
        "keep_after_speech": 0.12,
        "keep_before_speech": 0.06,
        "min_clip": 0.40,
        "description": "Ritmo rápido; exige revisão cuidadosa.",
    },
}

SILENCE_START_RE = re.compile(r"silence_start: (?P<t>[0-9.]+)")
SILENCE_END_RE = re.compile(r"silence_end: (?P<t>[0-9.]+) \| silence_duration: (?P<d>[0-9.]+)")

@dataclass
class Segment:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def ffprobe_duration(input_path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(input_path)
    ]
    proc = run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    return float(proc.stdout.strip())


def detect_silences(input_path: Path, threshold: str, min_silence: float) -> list[Segment]:
    cmd = [
        "ffmpeg", "-hide_banner", "-i", str(input_path),
        "-af", f"silencedetect=n={threshold}:d={min_silence}",
        "-f", "null", "-"
    ]
    proc = run(cmd)
    text = proc.stderr
    silences: list[Segment] = []
    current_start: float | None = None
    for line in text.splitlines():
        m_start = SILENCE_START_RE.search(line)
        if m_start:
            current_start = float(m_start.group("t"))
            continue
        m_end = SILENCE_END_RE.search(line)
        if m_end and current_start is not None:
            end = float(m_end.group("t"))
            silences.append(Segment(current_start, end))
            current_start = None
    return silences


def build_cut_intervals(silences: list[Segment], duration: float, keep_after: float, keep_before: float) -> list[Segment]:
    cuts: list[Segment] = []
    for s in silences:
        cut_start = min(duration, s.start + keep_after)
        cut_end = max(0.0, s.end - keep_before)
        if cut_end > cut_start:
            cuts.append(Segment(cut_start, cut_end))
    return cuts


def build_keep_segments(cuts: list[Segment], duration: float, min_clip: float, merge_tiny_keeps: bool = True) -> list[Segment]:
    keeps: list[Segment] = []
    cursor = 0.0
    for c in cuts:
        if c.start > cursor:
            keeps.append(Segment(cursor, c.start))
        cursor = max(cursor, c.end)
    if cursor < duration:
        keeps.append(Segment(cursor, duration))

    if not merge_tiny_keeps:
        return [seg for seg in keeps if seg.duration >= min_clip]

    # Merge tiny kept clips into neighbors to avoid nervous/picotado cuts.
    # Use only for conservative profiles. For dry cuts, merging can accidentally
    # bridge silence back into the edit.
    merged: list[Segment] = []
    for seg in keeps:
        if seg.duration <= 0.02:
            continue
        if merged and seg.duration < min_clip:
            merged[-1].end = seg.end
        else:
            merged.append(seg)
    if len(merged) > 1 and merged[0].duration < min_clip:
        merged[1].start = merged[0].start
        merged.pop(0)
    return merged


def render_preview(input_path: Path, output_path: Path, keeps: list[Segment]) -> None:
    if not keeps:
        raise RuntimeError("No keep segments generated; refusing to render empty video.")

    parts = []
    labels = []
    for i, seg in enumerate(keeps):
        v = f"v{i}"
        a = f"a{i}"
        parts.append(
            f"[0:v]trim=start={seg.start:.3f}:end={seg.end:.3f},setpts=PTS-STARTPTS[{v}];"
            f"[0:a]atrim=start={seg.start:.3f}:end={seg.end:.3f},asetpts=PTS-STARTPTS[{a}]"
        )
        labels.append(f"[{v}][{a}]")
    filter_complex = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(keeps)}:v=1:a=1[outv][outa]"

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-i", str(input_path),
        "-filter_complex", filter_complex,
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "160k",
        str(output_path)
    ]
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        quoted = " ".join(shlex.quote(x) for x in cmd)
        raise RuntimeError(f"ffmpeg render failed. Command: {quoted}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Silence Cut v1")
    parser.add_argument("input", type=Path)
    parser.add_argument("--profile", choices=PROFILES.keys(), default="vitor-v1")
    parser.add_argument("--output", type=Path, default=Path("preview_silence_cut_v1.mp4"))
    parser.add_argument("--report", type=Path, default=Path("silence_cut_report.json"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--threshold")
    parser.add_argument("--min-silence", type=float)
    parser.add_argument("--keep-after-speech", type=float)
    parser.add_argument("--keep-before-speech", type=float)
    parser.add_argument("--min-clip", type=float)
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Input not found: {args.input}", file=sys.stderr)
        return 2

    cfg = dict(PROFILES[args.profile])
    if args.threshold: cfg["threshold"] = args.threshold
    if args.min_silence is not None: cfg["min_silence"] = args.min_silence
    if args.keep_after_speech is not None: cfg["keep_after_speech"] = args.keep_after_speech
    if args.keep_before_speech is not None: cfg["keep_before_speech"] = args.keep_before_speech
    if args.min_clip is not None: cfg["min_clip"] = args.min_clip

    duration = ffprobe_duration(args.input)
    silences = detect_silences(args.input, cfg["threshold"], cfg["min_silence"])
    cuts = build_cut_intervals(silences, duration, cfg["keep_after_speech"], cfg["keep_before_speech"])
    keeps = build_keep_segments(cuts, duration, cfg["min_clip"], cfg.get("merge_tiny_keeps", True))

    cut_time = sum(s.duration for s in cuts)
    keep_time = sum(s.duration for s in keeps)
    report = {
        "input": str(args.input),
        "profile": args.profile,
        "config": cfg,
        "duration_original": round(duration, 3),
        "duration_removed_estimate": round(cut_time, 3),
        "duration_preview_estimate": round(keep_time, 3),
        "silences_detected": [asdict(s) for s in silences],
        "cuts": [asdict(s) for s in cuts],
        "keeps": [asdict(s) for s in keeps],
        "dry_run": args.dry_run,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.dry_run:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        render_preview(args.input, args.output, keeps)
        report["output"] = str(args.output)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "profile": args.profile,
        "threshold": cfg["threshold"],
        "min_silence": cfg["min_silence"],
        "keep_after_speech": cfg["keep_after_speech"],
        "keep_before_speech": cfg["keep_before_speech"],
        "min_clip": cfg["min_clip"],
        "original_s": round(duration, 2),
        "removed_estimate_s": round(cut_time, 2),
        "preview_estimate_s": round(keep_time, 2),
        "silences": len(silences),
        "report": str(args.report),
        "output": None if args.dry_run else str(args.output),
    }, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
