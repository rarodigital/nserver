#!/usr/bin/env python3
"""Silence Cut v4: dynamic word margins + optional visual gaze-away layer.

This script is intentionally conservative about dependencies:
- It reuses OpenAI/Whisper word timestamp JSON when provided.
- If MediaPipe/OpenCV are installed, it adds a visual gaze-away pass.
- WhisperX forced alignment is documented in the skill and can be plugged in by
  passing a WhisperX-style JSON with word_segments/words.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import sys
from dataclasses import asdict
from pathlib import Path

V3_PATH = Path(__file__).with_name("silence_cut_v3.py")
spec = importlib.util.spec_from_file_location("silence_cut_v3_base", V3_PATH)
base = importlib.util.module_from_spec(spec)
sys.modules["silence_cut_v3_base"] = base
assert spec and spec.loader
spec.loader.exec_module(base)

CONNECTORS = {"e", "o", "a", "se", "que", "do", "da", "de", "no", "na", "em", "um", "uma", "os", "as"}

PROFILES = {
    "vitor-dry-v4": {
        "pre_word_margin": 0.05,
        "post_word_margin_default": 0.10,
        "post_word_margin_last": 0.22,
        "post_word_margin_connector": 0.04,
        "phrase_gap": 0.85,
        "short_word_max_duration": 0.32,
        "max_word_duration": 1.00,
        "min_phrase_duration": 0.18,
        "drop_single_word_max_duration": 0.22,
        "min_tail_words": 2,
        "speech_ref_percentile": 0.70,
        "micro_island_max_duration": 0.80,
        "micro_island_relative_db": 9.0,
        "quiet_single_fragment_words": ["a", "e", "o", "as", "os", "um", "uma", "tempo"],
        "tail_duplicate_gap": 0.65,
        "trim_trailing_fragment_words": ["a", "e", "o", "as", "os", "um", "uma"],
        "energy_trim_enabled": True,
        "energy_window": 0.02,
        "energy_hop": 0.01,
        "energy_gate_below_ref_db": 12.5,
        "energy_min_active": 0.05,
        "energy_merge_gap": 0.35,
        "energy_pad_before": -0.050,
        "energy_pad_after": 0.100,
        "energy_min_segment": 0.16,
        "join_gap": 0.06,
        "strong_word_min_score": 0.60,
        "gaze_enabled": True,
        "gaze_away_threshold": 0.18,
        "gaze_fps_sample": 0.5,
        "gaze_group_gap": 0.22,
        "gaze_pad_before": 0.04,
        "gaze_pad_after": 0.04,
        "gaze_cut_only_without_strong_word": True,
        "description": "Corte seco Vitor v4: margem dinâmica de palavras + camada visual opcional para olhadas.",
    }
}


def is_short_connector(word_text: str) -> bool:
    return base.clean_word(word_text) in CONNECTORS


def get_post_margin(word_text: str, is_last_in_phrase: bool, cfg: dict) -> float:
    if is_short_connector(word_text):
        return cfg["post_word_margin_connector"]
    if is_last_in_phrase:
        return cfg["post_word_margin_last"]
    return cfg["post_word_margin_default"]


def load_words_any(transcript_path: Path, cfg: dict) -> list[dict]:
    data = json.loads(transcript_path.read_text(encoding="utf-8"))
    raw_words = data.get("word_segments") or data.get("words") or []
    cleaned = []
    for item in raw_words:
        text = str(item.get("word") or item.get("text") or "").strip()
        word = base.clean_word(text)
        if not word:
            continue
        start = float(item.get("start", 0.0))
        end = float(item.get("end", start))
        score = float(item.get("score", item.get("confidence", 1.0)) or 1.0)
        if end <= start:
            end = start + 0.18
        cap = cfg["max_word_duration"]
        if len(word) <= 2:
            cap = min(cap, cfg.get("short_word_max_duration", 0.32))
        if end - start > cap:
            end = start + cap
        cleaned.append({"word": word, "raw": text, "start": start, "end": end, "score": score})
    cleaned.sort(key=lambda x: (x["start"], x["end"]))
    return cleaned


def build_word_phrases_v4(words: list[dict], duration: float, cfg: dict) -> tuple[list[base.Segment], list[list[dict]]]:
    phrases: list[base.Segment] = []
    phrase_words_out: list[list[dict]] = []
    cur_words: list[dict] = []

    # Remove tiny hallucinated trailing word after CTA.
    if len(words) >= 2:
        last, prev = words[-1], words[-2]
        if last["end"] - last["start"] <= cfg["drop_single_word_max_duration"] and last["start"] - prev["end"] >= cfg.get("tail_duplicate_gap", 0.65):
            words = words[:-1]

    def flush() -> None:
        nonlocal cur_words
        if not cur_words:
            return
        phrase_words = list(cur_words)
        trailing_fragments = set(cfg.get("trim_trailing_fragment_words", []))
        while len(phrase_words) > 1 and phrase_words[-1]["word"] in trailing_fragments:
            phrase_words.pop()
        first = phrase_words[0]
        last = phrase_words[-1]
        start = max(0.0, first["start"] - cfg["pre_word_margin"])
        end = min(duration, last["end"] + get_post_margin(last["word"], True, cfg))
        if end - start >= cfg["min_phrase_duration"]:
            raw = " ".join(w["raw"] for w in phrase_words)
            phrases.append(base.Segment(start, end, f"words:{len(phrase_words)}:{raw}"))
            phrase_words_out.append(phrase_words)
        cur_words = []

    prev = None
    for w in words:
        if prev is not None and w["start"] - prev["end"] > cfg["phrase_gap"]:
            flush()
        cur_words.append(w)
        prev = w
    flush()

    while phrases:
        reason = phrases[-1].reason
        m = re.search(r"words:(\d+):", reason)
        count = int(m.group(1)) if m else 99
        if count < cfg["min_tail_words"] and phrases[-1].duration <= cfg["drop_single_word_max_duration"] + cfg["pre_word_margin"] + cfg["post_word_margin_last"]:
            phrases.pop(); phrase_words_out.pop()
        else:
            break
    return phrases, phrase_words_out


def apply_dynamic_word_guards(segments: list[base.Segment], words: list[dict], duration: float, cfg: dict) -> list[base.Segment]:
    guarded = []
    for seg in segments:
        overlapping = [w for w in words if w["start"] < seg.end + 0.35 and w["end"] > seg.start - 0.10]
        if overlapping:
            first, last = overlapping[0], overlapping[-1]
            # Protect initial consonants and final low-energy tails. The last-word
            # margin is intentionally larger to fix words like "mesmo/tempo".
            start = min(seg.start, max(0.0, first["start"] - cfg["pre_word_margin"]))
            end = max(seg.end, min(duration, last["end"] + get_post_margin(last["word"], True, cfg)))
            guarded.append(base.Segment(start, end, seg.reason + "|dynamic_word_guard"))
        else:
            guarded.append(seg)
    return base.merge_segments(guarded, cfg["join_gap"])


def group_times(times: list[float], gap: float, pad_before: float, pad_after: float, duration: float) -> list[base.Segment]:
    if not times:
        return []
    times = sorted(times)
    groups = []
    start = prev = times[0]
    for t in times[1:]:
        if t - prev <= gap:
            prev = t
        else:
            groups.append(base.Segment(max(0.0, start - pad_before), min(duration, prev + pad_after), "gaze_away"))
            start = prev = t
    groups.append(base.Segment(max(0.0, start - pad_before), min(duration, prev + pad_after), "gaze_away"))
    return groups


def detect_gaze_away(video_path: Path, duration: float, cfg: dict) -> tuple[list[base.Segment], str]:
    if not cfg.get("gaze_enabled", True):
        return [], "disabled"
    try:
        import cv2  # type: ignore
    except Exception as exc:
        return [], f"unavailable_cv2:{type(exc).__name__}:{exc}"

    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    sample_every = max(1, int(fps / cfg.get("gaze_fps_sample", 5)))
    threshold = cfg.get("gaze_away_threshold", 0.18)
    times: list[float] = []
    frame_idx = 0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    # Preferred path: classic MediaPipe FaceMesh. Some newer py3.13 wheels only
    # expose mediapipe.tasks and do not include mp.solutions; in that case we
    # fall back to OpenCV Haar/profiles so the script still runs.
    try:
        import mediapipe as mp  # type: ignore
        mp_face = mp.solutions.face_mesh  # type: ignore[attr-defined]
    except Exception:
        mp_face = None

    def maybe_rotate(frame):
        h, w = frame.shape[:2]
        # The source phone video stores rotation metadata; OpenCV may return it
        # sideways. Rotate to portrait for face detectors when needed.
        if w > h:
            return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        return frame

    if mp_face is not None:
        with mp_face.FaceMesh(static_image_mode=False, max_num_faces=1, refine_landmarks=False) as face_mesh:
            while True:
                if total_frames and frame_idx >= total_frames:
                    break
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                ret, frame = cap.read()
                if not ret:
                    break
                frame = maybe_rotate(frame)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                result = face_mesh.process(rgb)
                if result.multi_face_landmarks:
                    lm = result.multi_face_landmarks[0].landmark
                    nose_x = lm[1].x
                    left_x = lm[234].x
                    right_x = lm[454].x
                    face_width = abs(right_x - left_x)
                    if face_width > 0:
                        offset = (nose_x - (left_x + right_x) / 2) / face_width
                        if abs(offset) > threshold:
                            times.append(frame_idx / fps)
                frame_idx += sample_every
        cap.release()
        return group_times(times, cfg["gaze_group_gap"], cfg["gaze_pad_before"], cfg["gaze_pad_after"], duration), "mediapipe_solutions_ok"

    # Fallback: OpenCV frontal/profile classifiers. This is weaker than FaceMesh
    # but catches many look-away moments as loss of frontal face / profile face.
    frontal = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    profile = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_profileface.xml")
    while True:
        if total_frames and frame_idx >= total_frames:
            break
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret:
            break
        frame = maybe_rotate(frame)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        scale = 180 / max(1, gray.shape[1])
        if scale < 1:
            gray_small = cv2.resize(gray, (180, int(gray.shape[0] * scale)))
        else:
            gray_small = gray
        faces = frontal.detectMultiScale(gray_small, 1.1, 4)
        profiles = profile.detectMultiScale(gray_small, 1.1, 4)
        if len(profiles) > 0 and len(faces) == 0:
            times.append(frame_idx / fps)
        elif len(faces) > 0:
            x, y, w, h = max(faces, key=lambda r: r[2] * r[3])
            center_offset = ((x + w / 2) - gray_small.shape[1] / 2) / max(1, gray_small.shape[1])
            # Large face-center drift is a weak proxy for turning/reading.
            if abs(center_offset) > 0.20:
                times.append(frame_idx / fps)
        frame_idx += sample_every
    cap.release()
    return group_times(times, cfg["gaze_group_gap"], cfg["gaze_pad_before"], cfg["gaze_pad_after"], duration), "opencv_haar_fallback"


def words_in_interval(words: list[dict], start: float, end: float) -> list[dict]:
    return [w for w in words if w["start"] < end and w["end"] > start]


def has_strong_word(words: list[dict], start: float, end: float, cfg: dict) -> bool:
    for w in words_in_interval(words, start, end):
        if not is_short_connector(w["word"]) and float(w.get("score", 1.0)) >= cfg["strong_word_min_score"]:
            return True
    return False


def subtract_gaze_without_strong_words(segments: list[base.Segment], gaze: list[base.Segment], words: list[dict], cfg: dict) -> tuple[list[base.Segment], list[dict]]:
    if not gaze:
        return segments, []
    out = []
    cuts = []
    for seg in segments:
        parts = [seg]
        for g in gaze:
            new_parts = []
            for p in parts:
                if g.end <= p.start or g.start >= p.end:
                    new_parts.append(p); continue
                cut_start = max(p.start, g.start)
                cut_end = min(p.end, g.end)
                # Visual wins only where there is no strong word. This avoids
                # cutting real spoken content while removing look-away filler.
                if cfg.get("gaze_cut_only_without_strong_word", True) and has_strong_word(words, cut_start, cut_end, cfg):
                    new_parts.append(p); continue
                if cut_start > p.start:
                    new_parts.append(base.Segment(p.start, cut_start, p.reason + "|before_gaze_cut"))
                if cut_end < p.end:
                    new_parts.append(base.Segment(cut_end, p.end, p.reason + "|after_gaze_cut"))
                cuts.append({"start": cut_start, "end": cut_end, "reason": "gaze_away_no_strong_word"})
            parts = [x for x in new_parts if x.duration >= cfg["energy_min_segment"]]
        out.extend(parts)
    return base.merge_segments(out, cfg["join_gap"]), cuts


def main() -> int:
    parser = argparse.ArgumentParser(description="Silence Cut v4")
    parser.add_argument("input", type=Path)
    parser.add_argument("--transcript", type=Path, help="Whisper/OpenAI verbose_json or WhisperX aligned JSON with word timestamps")
    parser.add_argument("--profile", choices=PROFILES.keys(), default="vitor-dry-v4")
    parser.add_argument("--output", type=Path, default=Path("preview_vitor_dry_v4.mp4"))
    parser.add_argument("--report", type=Path, default=Path("report_vitor_dry_v4.json"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--disable-visual", action="store_true")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Input not found: {args.input}", file=sys.stderr); return 2
    if not args.transcript or not args.transcript.exists():
        print("Transcript is required for this implementation. Pass --transcript transcript_words.json or a WhisperX aligned JSON.", file=sys.stderr)
        return 2

    cfg = dict(PROFILES[args.profile])
    if args.disable_visual:
        cfg["gaze_enabled"] = False
    duration = base.ffprobe_duration(args.input)
    words = load_words_any(args.transcript, cfg)
    phrases, phrase_words = build_word_phrases_v4(words, duration, cfg)
    ref_db = base.measure_reference_db(args.input, phrases, cfg["speech_ref_percentile"])
    filtered, dropped = base.filter_micro_islands(args.input, phrases, ref_db, cfg)
    transcript_keeps = base.merge_segments(filtered, cfg["join_gap"])
    energy_keeps, energy_debug = base.energy_refine_segments(args.input, transcript_keeps, ref_db, duration, cfg)
    guarded_keeps = apply_dynamic_word_guards(energy_keeps, words, duration, cfg)
    gaze_segments, gaze_status = detect_gaze_away(args.input, duration, cfg)
    keeps, gaze_cuts = subtract_gaze_without_strong_words(guarded_keeps, gaze_segments, words, cfg)
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
        "phrase_words": phrase_words,
        "dropped_micro_islands": dropped,
        "transcript_keeps_before_energy": [asdict(s) for s in transcript_keeps],
        "energy_keeps_before_word_guard": [asdict(s) for s in energy_keeps],
        "energy_refine_debug": energy_debug,
        "guarded_keeps_before_visual": [asdict(s) for s in guarded_keeps],
        "gaze_status": gaze_status,
        "gaze_away_segments": [asdict(s) for s in gaze_segments],
        "gaze_cuts": gaze_cuts,
        "keeps": [asdict(s) for s in keeps],
        "duration_preview_estimate": round(keep_time, 3),
        "duration_removed_estimate": round(duration - keep_time, 3),
        "dry_run": args.dry_run,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.dry_run:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        base.render_preview(args.input, args.output, keeps)
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
        "gaze_status": gaze_status,
        "gaze_segments": len(gaze_segments),
        "gaze_cuts": len(gaze_cuts),
        "report": str(args.report),
        "output": None if args.dry_run else str(args.output),
    }, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
