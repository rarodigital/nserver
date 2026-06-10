#!/usr/bin/env python3
"""Simple upload-friendly silence analyzer -> cuts.json.

Use when the user uploads a fresh video from phone and no word-level transcript exists yet.
This is less precise than vitor-dry-v3, but enables the full HTML flow:
upload video -> params -> cuts.json -> manual bars -> final render.
"""
from __future__ import annotations
import argparse, json, re, subprocess
from pathlib import Path

SILENCE_RE = re.compile(r"silence_(start|end):\s*([0-9.]+)(?:\s*\|\s*silence_duration:\s*([0-9.]+))?")


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def duration(video: Path) -> float:
    p = run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(video)])
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip())
    return float(p.stdout.strip())


def detect_silences(video: Path, noise: str, min_silence: float) -> list[tuple[float,float]]:
    p = run(['ffmpeg','-hide_banner','-i',str(video),'-af',f'silencedetect=noise={noise}:d={min_silence}','-f','null','-'])
    text = p.stderr
    starts: list[float] = []
    silences: list[tuple[float,float]] = []
    for line in text.splitlines():
        m = SILENCE_RE.search(line)
        if not m:
            continue
        kind = m.group(1); val = float(m.group(2))
        if kind == 'start':
            starts.append(val)
        elif kind == 'end' and starts:
            silences.append((starts.pop(0), val))
    return silences


def build_cuts(video: Path, out: Path, noise: str, min_silence: float, pad_before: float, pad_after: float, min_keep: float, ignore_cut_under: float) -> dict:
    dur = duration(video)
    silences = detect_silences(video, noise, min_silence)
    cut_ranges = []
    for s, e in silences:
        cs = max(0.0, s + pad_after)      # keep a little after previous speech before cutting
        ce = min(dur, e - pad_before)     # resume a little before next speech
        if ce - cs >= ignore_cut_under:
            cut_ranges.append((cs, ce))
    items = []
    last = 0.0
    idx = 0
    for cs, ce in cut_ranges:
        if cs > last:
            if cs - last >= min_keep:
                items.append({'id': f'keep_{idx:03d}', 'start': round(last,3), 'end': round(cs,3), 'action': 'keep', 'reason': 'auto: sound above threshold'})
                idx += 1
            else:
                # tiny keeps between cuts become part of the cut
                cs = last
        items.append({'id': f'cut_{idx:03d}', 'start': round(cs,3), 'end': round(ce,3), 'action': 'cut', 'reason': f'auto silence {noise} d>={min_silence}'})
        idx += 1
        last = max(last, ce)
    if last < dur:
        items.append({'id': f'keep_{idx:03d}', 'start': round(last,3), 'end': round(dur,3), 'action': 'keep', 'reason': 'auto: sound above threshold'})
    data = {
        'schema': 'openclaw.cuts.v1',
        'source_video': str(video),
        'source_duration': round(dur,3),
        'basis': 'simple-silence-upload-v1',
        'params': {'noise': noise, 'min_silence': min_silence, 'pad_before': pad_before, 'pad_after': pad_after, 'min_keep': min_keep, 'ignore_cut_under': ignore_cut_under},
        'items': items,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('video', type=Path)
    ap.add_argument('-o','--output', type=Path, required=True)
    ap.add_argument('--noise', default='-32dB')
    ap.add_argument('--min-silence', type=float, default=0.25)
    ap.add_argument('--pad-before', type=float, default=0.04)
    ap.add_argument('--pad-after', type=float, default=0.04)
    ap.add_argument('--min-keep', type=float, default=0.18)
    ap.add_argument('--ignore-cut-under', type=float, default=0.12)
    args = ap.parse_args()
    if not args.video.exists():
        raise SystemExit(f'Video not found: {args.video}')
    data = build_cuts(args.video, args.output, args.noise, args.min_silence, args.pad_before, args.pad_after, args.min_keep, args.ignore_cut_under)
    keep_s = sum(x['end']-x['start'] for x in data['items'] if x['action']=='keep')
    print(json.dumps({'cuts': str(args.output), 'items': len(data['items']), 'keep_s': round(keep_s,3)}, ensure_ascii=False))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
