#!/usr/bin/env python3
"""Silence Cut v3: word-timestamp + audio-gate cut builder for dry talking-head ads."""

from __future__ import annotations

import argparse
import json
import math
import re
import shlex
import struct
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

WORD_RE = re.compile(r"[\wÀ-ÿ]+", re.UNICODE)

PROFILES = {
    "vitor-dry-v3": {
        "pre_word_margin": 0.05,
        "post_word_margin": 0.16,
        "phrase_gap": 0.85,
        "short_word_max_duration": 0.32,
        "energy_window": 0.02,
        "energy_hop": 0.01,
        "energy_gate_below_ref_db": 12.5,
        "energy_min_active": 0.05,
        "energy_merge_gap": 0.35,
        "energy_pad_before": -0.050,
        "energy_pad_after": 0.100,
        "energy_min_segment": 0.16,
        "energy_trim_enabled": True,
        "max_word_duration": 1.00,
        "min_phrase_duration": 0.18,
        "drop_single_word_max_duration": 0.22,
        "min_tail_words": 2,
        "speech_ref_percentile": 0.70,
        "relative_noise_db": 11.0,
        "micro_island_max_duration": 0.80,
        "micro_island_relative_db": 9.0,
        "join_gap": 0.06,
        "quiet_single_fragment_words": ["a", "e", "o", "as", "os", "um", "uma", "tempo"],
        "tail_duplicate_gap": 0.65,
        "trim_trailing_fragment_words": ["a", "e", "o", "as", "os", "um", "uma"],
        "description": "Corte seco Vitor v3: transcrição por palavra protege falas; volume relativo e micro-ilhas removem olhadas/sussurros.",
    }
}

CONFIG_CASTS = {
    "pre_word_margin": float,
    "post_word_margin": float,
    "phrase_gap": float,
    "short_word_max_duration": float,
    "energy_window": float,
    "energy_hop": float,
    "energy_gate_below_ref_db": float,
    "energy_min_active": float,
    "energy_merge_gap": float,
    "energy_pad_before": float,
    "energy_pad_after": float,
    "energy_min_segment": float,
    "max_word_duration": float,
    "min_phrase_duration": float,
    "drop_single_word_max_duration": float,
    "min_tail_words": int,
    "speech_ref_percentile": float,
    "relative_noise_db": float,
    "micro_island_max_duration": float,
    "micro_island_relative_db": float,
    "join_gap": float,
}


def apply_overrides(cfg: dict, overrides: list[str]) -> None:
    for raw in overrides:
        if "=" not in raw:
            raise ValueError(f"Invalid --set {raw!r}; use key=value")
        key, val = raw.split("=", 1)
        key = key.strip()
        if key not in CONFIG_CASTS:
            raise ValueError(f"Unsupported --set key: {key}")
        cfg[key] = CONFIG_CASTS[key](val)

@dataclass
class Segment:
    start: float
    end: float
    reason: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def ffprobe_duration(input_path: Path) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(input_path)]
    proc = run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    return float(proc.stdout.strip())


def read_audio_f32(input_path: Path, start: float, end: float, sample_rate: int = 16000) -> tuple[list[float], int]:
    dur = max(0.001, end - start)
    cmd = [
        "ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
        "-i", str(input_path), "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "f32le", "-",
    ]
    data = subprocess.check_output(cmd)
    n = len(data) // 4
    if n <= 0:
        return [], sample_rate
    return list(struct.unpack(f"<{n}f", data)), sample_rate


def db_from_rms(rms: float) -> float:
    return -120.0 if rms <= 0 else 20 * math.log10(rms)


def rms_dbfs(input_path: Path, start: float, end: float) -> float:
    vals, _sr = read_audio_f32(input_path, start, end)
    if not vals:
        return -120.0
    rms = math.sqrt(sum(v * v for v in vals) / len(vals))
    return db_from_rms(rms)


def clean_word(w: str) -> str:
    m = WORD_RE.findall(w.lower())
    return "".join(m)


def load_words(transcript_path: Path, max_word_duration: float) -> list[dict]:
    data = json.loads(transcript_path.read_text(encoding="utf-8"))
    words = data.get("words", [])
    cleaned = []
    for item in words:
        text = str(item.get("word", "")).strip()
        word = clean_word(text)
        if not word:
            continue
        start = float(item.get("start", 0.0))
        end = float(item.get("end", start))
        if end <= start:
            end = start + 0.18
        # Whisper sometimes stretches a short word across a silent look-away.
        # Cap each word duration; phrase continuity is handled by gaps.
        cap = max_word_duration
        if len(word) <= 2:
            # Short connectors like "se", "e", "o" are often stretched over
            # a look-away. Keep them short so they don't preserve movement.
            cap = min(cap, PROFILES["vitor-dry-v3"].get("short_word_max_duration", 0.32))
        if end - start > cap:
            end = start + cap
        cleaned.append({"word": word, "raw": text, "start": start, "end": end})
    cleaned.sort(key=lambda x: (x["start"], x["end"]))
    return cleaned


def build_word_phrases(words: list[dict], duration: float, cfg: dict) -> list[Segment]:
    phrases: list[Segment] = []
    cur_words: list[dict] = []

    # Whisper may append a tiny hallucinated word after the real CTA.
    # Remove this only at the very end when it is both tiny and separated.
    if len(words) >= 2:
        last = words[-1]
        prev = words[-2]
        if (
            last["end"] - last["start"] <= cfg["drop_single_word_max_duration"]
            and last["start"] - prev["end"] >= cfg.get("tail_duplicate_gap", 0.65)
        ):
            words = words[:-1]

    def flush() -> None:
        nonlocal cur_words
        if not cur_words:
            return
        phrase_words = list(cur_words)
        trailing_fragments = set(cfg.get("trim_trailing_fragment_words", []))
        # If a phrase ends in a connector/filler and the next real phrase starts
        # much later, do not keep the connector's trailing silent movement.
        while len(phrase_words) > 1 and phrase_words[-1]["word"] in trailing_fragments:
            phrase_words.pop()
        start = max(0.0, phrase_words[0]["start"] - cfg["pre_word_margin"])
        end = min(duration, phrase_words[-1]["end"] + cfg["post_word_margin"])
        phrase_dur = end - start
        if phrase_dur >= cfg["min_phrase_duration"]:
            raw = " ".join(w["raw"] for w in phrase_words)
            phrases.append(Segment(start, end, f"words:{len(phrase_words)}:{raw}"))
        cur_words = []

    prev = None
    for w in words:
        if prev is not None and w["start"] - prev["end"] > cfg["phrase_gap"]:
            flush()
        cur_words.append(w)
        prev = w
    flush()

    # Remove trailing hallucinated/tiny single-word tails like a repeated "toca" after CTA.
    while phrases:
        reason = phrases[-1].reason
        m = re.search(r"words:(\d+):", reason)
        count = int(m.group(1)) if m else 99
        if count < cfg["min_tail_words"] and phrases[-1].duration <= cfg["drop_single_word_max_duration"] + cfg["pre_word_margin"] + cfg["post_word_margin"]:
            phrases.pop()
        else:
            break
    return phrases


def measure_reference_db(input_path: Path, phrases: list[Segment], percentile: float) -> float:
    vals = []
    for p in phrases:
        # Prefer phrases long enough to represent real projected speech.
        if p.duration >= 1.2:
            vals.append(rms_dbfs(input_path, p.start, p.end))
    if not vals:
        return -24.0
    vals.sort(reverse=True)  # less negative first
    idx = min(len(vals) - 1, max(0, int(round((len(vals) - 1) * percentile))))
    return vals[idx]


def filter_micro_islands(input_path: Path, phrases: list[Segment], ref_db: float, cfg: dict) -> tuple[list[Segment], list[dict]]:
    kept = []
    dropped = []
    fragment_words = set(cfg.get("quiet_single_fragment_words", []))
    for p in phrases:
        m = re.search(r"words:(\d+):(.*?)(?:\||$)", p.reason)
        word_count = int(m.group(1)) if m else 99
        phrase_text = (m.group(2).strip().lower() if m else "")
        db = rms_dbfs(input_path, p.start, p.end)
        is_micro = p.duration <= cfg["micro_island_max_duration"]
        is_quiet = db <= ref_db - cfg["micro_island_relative_db"]
        is_single = word_count <= 1
        is_quiet_fragment = is_single and phrase_text in fragment_words and is_quiet
        # Do not drop every quiet single word: it can be the final word of a real
        # sentence (ex: "fecha"). Drop only obvious whispered fragments/fillers,
        # while trailing tiny hallucinations are handled by build_word_phrases().
        if is_quiet_fragment or (is_micro and is_quiet and is_single and phrase_text in fragment_words):
            dropped.append({"start": p.start, "end": p.end, "duration": p.duration, "rms_dbfs": db, "reason": p.reason})
        else:
            p.reason += f"|rms_dbfs:{db:.1f}"
            kept.append(p)
    return kept, dropped


def energy_refine_segments(input_path: Path, segments: list[Segment], ref_db: float, duration: float, cfg: dict) -> tuple[list[Segment], list[dict]]:
    if not cfg.get("energy_trim_enabled", False):
        return segments, []
    refined: list[Segment] = []
    debug: list[dict] = []
    gate_db = ref_db - cfg["energy_gate_below_ref_db"]
    win = cfg["energy_window"]
    hop = cfg["energy_hop"]
    min_active = cfg["energy_min_active"]
    merge_gap = cfg["energy_merge_gap"]
    min_seg = cfg["energy_min_segment"]
    for seg in segments:
        vals, sr = read_audio_f32(input_path, seg.start, seg.end)
        if not vals:
            continue
        win_n = max(1, int(win * sr))
        hop_n = max(1, int(hop * sr))
        active_ranges: list[Segment] = []
        cur_start: float | None = None
        last_active_end: float | None = None
        for offset in range(0, max(1, len(vals) - win_n + 1), hop_n):
            chunk = vals[offset:offset + win_n]
            if not chunk:
                continue
            db = db_from_rms(math.sqrt(sum(v * v for v in chunk) / len(chunk)))
            t0 = seg.start + offset / sr
            t1 = min(seg.end, t0 + win)
            if db >= gate_db:
                if cur_start is None:
                    cur_start = t0
                last_active_end = t1
            elif cur_start is not None and last_active_end is not None:
                if last_active_end - cur_start >= min_active:
                    active_ranges.append(Segment(cur_start, last_active_end, seg.reason + f"|energy_db>={gate_db:.1f}"))
                cur_start = None
                last_active_end = None
        if cur_start is not None and last_active_end is not None and last_active_end - cur_start >= min_active:
            active_ranges.append(Segment(cur_start, last_active_end, seg.reason + f"|energy_db>={gate_db:.1f}"))

        if not active_ranges:
            # Fallback: keep transcript segment if energy gate misses a very low useful word.
            refined.append(seg)
            debug.append({"start": seg.start, "end": seg.end, "mode": "fallback_no_energy", "reason": seg.reason})
            continue

        merged = merge_segments(active_ranges, merge_gap)
        for a in merged:
            start = max(0.0, a.start + cfg["energy_pad_before"])
            end = min(duration, a.end + cfg["energy_pad_after"])
            if end - start >= min_seg:
                refined.append(Segment(start, end, a.reason))
        debug.append({"start": seg.start, "end": seg.end, "gate_db": gate_db, "active": [asdict(x) for x in merged], "reason": seg.reason})
    return merge_segments(refined, cfg["join_gap"]), debug


def merge_segments(segments: list[Segment], join_gap: float) -> list[Segment]:
    if not segments:
        return []
    segments = sorted(segments, key=lambda s: (s.start, s.end))
    out = [segments[0]]
    for s in segments[1:]:
        last = out[-1]
        if s.start <= last.end + join_gap:
            last.end = max(last.end, s.end)
            last.reason += " + " + s.reason
        else:
            out.append(s)
    return out


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
        str(output_path),
    ]
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        quoted = " ".join(shlex.quote(x) for x in cmd)
        raise RuntimeError(f"ffmpeg render failed. Command: {quoted}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Silence Cut v3")
    parser.add_argument("input", type=Path)
    parser.add_argument("--transcript", type=Path, required=True, help="OpenAI verbose_json with word timestamps")
    parser.add_argument("--profile", choices=PROFILES.keys(), default="vitor-dry-v3")
    parser.add_argument("--output", type=Path, default=Path("preview_silence_cut_v3.mp4"))
    parser.add_argument("--report", type=Path, default=Path("silence_cut_v3_report.json"))
    parser.add_argument("--set", dest="sets", action="append", default=[], help="Override config key=value, e.g. --set energy_pad_after=0.015")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Input not found: {args.input}", file=sys.stderr)
        return 2
    if not args.transcript.exists():
        print(f"Transcript not found: {args.transcript}", file=sys.stderr)
        return 2

    cfg = dict(PROFILES[args.profile])
    try:
        apply_overrides(cfg, args.sets)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    duration = ffprobe_duration(args.input)
    words = load_words(args.transcript, cfg["max_word_duration"])
    phrases = build_word_phrases(words, duration, cfg)
    ref_db = measure_reference_db(args.input, phrases, cfg["speech_ref_percentile"])
    filtered, dropped = filter_micro_islands(args.input, phrases, ref_db, cfg)
    transcript_keeps = merge_segments(filtered, cfg["join_gap"])
    keeps, energy_debug = energy_refine_segments(args.input, transcript_keeps, ref_db, duration, cfg)
    keep_time = sum(s.duration for s in keeps)

    report = {
        "input": str(args.input),
        "transcript": str(args.transcript),
        "profile": args.profile,
        "config": cfg,
        "duration_original": round(duration, 3),
        "speech_reference_dbfs": round(ref_db, 2),
        "words": words,
        "phrases_before_filter": [asdict(s) for s in phrases],
        "dropped_micro_islands": dropped,
        "transcript_keeps_before_energy": [asdict(s) for s in transcript_keeps],
        "energy_refine_debug": energy_debug,
        "keeps": [asdict(s) for s in keeps],
        "duration_preview_estimate": round(keep_time, 3),
        "duration_removed_estimate": round(duration - keep_time, 3),
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
        "original_s": round(duration, 2),
        "preview_estimate_s": round(keep_time, 2),
        "removed_estimate_s": round(duration - keep_time, 2),
        "speech_reference_dbfs": round(ref_db, 2),
        "words": len(words),
        "phrases": len(phrases),
        "keeps": len(keeps),
        "dropped_micro_islands": len(dropped),
        "report": str(args.report),
        "output": None if args.dry_run else str(args.output),
    }, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
