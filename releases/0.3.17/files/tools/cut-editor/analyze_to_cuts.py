#!/usr/bin/env python3
"""Run the existing silence-cut analyzer and convert its report to cuts.json.

This is the Fase 1 bridge: automatic analysis -> editable cuts.json.
It intentionally reuses skills/silence-cut/scripts/silence_cut_v3.py instead of
inventing a second cut algorithm.
"""
from __future__ import annotations
import argparse, json, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SILENCE_V3 = ROOT / 'skills/silence-cut/scripts/silence_cut_v3.py'

PARAM_MAP = {
    'pre_word_margin': float,
    'post_word_margin': float,
    'phrase_gap': float,
    'energy_gate_below_ref_db': float,
    'energy_merge_gap': float,
    'energy_pad_before': float,
    'energy_pad_after': float,
    'energy_min_active': float,
    'join_gap': float,
}


def ffprobe_duration(video: Path) -> float:
    p = subprocess.run([
        'ffprobe','-v','error','-show_entries','format=duration',
        '-of','default=noprint_wrappers=1:nokey=1',str(video)
    ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip())
    return float(p.stdout.strip())


def parse_set(values: list[str]) -> dict[str, object]:
    out: dict[str, object] = {}
    for raw in values:
        if '=' not in raw:
            raise SystemExit(f'Invalid --set {raw!r}; use key=value')
        key, val = raw.split('=', 1)
        key = key.strip()
        if key not in PARAM_MAP:
            raise SystemExit(f'Unsupported parameter: {key}. Allowed: {", ".join(PARAM_MAP)}')
        out[key] = PARAM_MAP[key](val)
    return out


def report_to_cuts(report: dict, output: Path, source_video: Path, basis: str) -> dict:
    duration = float(report.get('duration_original') or ffprobe_duration(source_video))
    keeps = sorted(report.get('keeps', []), key=lambda x: (float(x['start']), float(x['end'])))
    items = []
    last = 0.0
    idx = 0
    for k in keeps:
        s = round(float(k['start']), 3)
        e = round(float(k['end']), 3)
        if s > last + 0.001:
            items.append({'id': f'cut_{idx:03d}', 'start': round(last, 3), 'end': s, 'action': 'cut', 'reason': 'auto: removed gap/silence/look-away candidate'})
            idx += 1
        items.append({'id': f'keep_{idx:03d}', 'start': s, 'end': e, 'action': 'keep', 'reason': str(k.get('reason', 'auto keep'))})
        idx += 1
        last = max(last, e)
    if last < duration - 0.001:
        items.append({'id': f'cut_{idx:03d}', 'start': round(last, 3), 'end': round(duration, 3), 'action': 'cut', 'reason': 'auto: tail removed'})
    data = {
        'schema': 'openclaw.cuts.v1',
        'source_video': str(source_video),
        'source_duration': round(duration, 3),
        'basis': basis,
        'analyzer_report': str(report.get('_report_path', '')),
        'params': report.get('config', {}),
        'items': items,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('video', type=Path)
    ap.add_argument('--transcript', type=Path, required=True, help='Word timestamp JSON used by the existing silence_cut_v3 analyzer')
    ap.add_argument('-o', '--output', type=Path, required=True)
    ap.add_argument('--report', type=Path)
    ap.add_argument('--profile', default='vitor-dry-v3')
    ap.add_argument('--set', dest='sets', action='append', default=[], help='Override analyzer config, ex: --set energy_pad_after=0.015')
    args = ap.parse_args()
    if not args.video.exists():
        raise SystemExit(f'Video not found: {args.video}')
    if not args.transcript.exists():
        raise SystemExit(f'Transcript not found: {args.transcript}')
    overrides = parse_set(args.sets)
    report = args.report or args.output.with_suffix('.report.json')

    cmd = [sys.executable, str(SILENCE_V3), str(args.video), '--transcript', str(args.transcript), '--profile', args.profile, '--report', str(report), '--dry-run']
    for k, v in overrides.items():
        cmd += ['--set', f'{k}={v}']
    subprocess.run(cmd, check=True)
    data = json.loads(report.read_text(encoding='utf-8'))
    data['_report_path'] = str(report)
    cuts = report_to_cuts(data, args.output, args.video, f'{args.profile} automatic analysis')
    print(json.dumps({'cuts': str(args.output), 'items': len(cuts['items']), 'keep_s': round(sum(x['end']-x['start'] for x in cuts['items'] if x['action']=='keep'), 3)}, ensure_ascii=False))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
