#!/usr/bin/env python3
"""Render a cuts.json file by concatenating all action=keep ranges with FFmpeg.

Fase 1 rule: cuts.json is source of truth; only render after user approval.
Default output keeps native orientation/resolution/fps as much as possible. Optional
scale/fps can be provided explicitly when needed.
"""
from __future__ import annotations
import argparse, json, os, shlex, shutil, subprocess, tempfile
from pathlib import Path


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def ffmpeg_cmd() -> str:
    return os.environ.get('NSERVER_FFMPEG') or shutil.which('ffmpeg') or 'ffmpeg'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('cuts', type=Path)
    ap.add_argument('--video', type=Path, help='Original video. Overrides source_video in cuts.json')
    ap.add_argument('-o', '--output', type=Path, required=True)
    ap.add_argument('--preset', default='veryfast')
    ap.add_argument('--crf', default='20')
    ap.add_argument('--scale', help='Optional scale filter, ex: 576:1024. Default: native')
    ap.add_argument('--fps', help='Optional output fps. Default: native')
    ap.add_argument('--copy', action='store_true', help='Try stream-copy segment extraction. Fastest, but less reliable around arbitrary cut points.')
    args = ap.parse_args()

    data = json.loads(args.cuts.read_text(encoding='utf-8'))
    video = args.video or Path(data.get('source_video', ''))
    if not video.exists():
        raise SystemExit(f'Original video not found: {video}. Put source_video in cuts.json or pass --video /path/to/original.mp4')
    keeps = [x for x in data.get('items', []) if x.get('action') == 'keep' and float(x.get('end', 0)) > float(x.get('start', 0))]
    if not keeps:
        raise SystemExit('No keep segments in cuts.json')

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='openclaw-cut-render-') as td:
        td = Path(td)
        segs = []
        for i, k in enumerate(keeps):
            start = float(k['start']); end = float(k['end']); dur = end - start
            seg = td / f'seg_{i:04d}.mp4'
            segs.append(seg)
            if args.copy:
                cmd = [ffmpeg_cmd(),'-y','-hide_banner','-loglevel','error','-ss',f'{start:.3f}','-t',f'{dur:.3f}','-i',str(video),'-map','0:v:0','-map','0:a:0?','-c','copy','-avoid_negative_ts','make_zero',str(seg)]
            else:
                cmd = [ffmpeg_cmd(),'-y','-hide_banner','-loglevel','error','-ss',f'{start:.3f}','-t',f'{dur:.3f}','-i',str(video),'-map','0:v:0','-map','0:a:0?']
                if args.scale:
                    cmd += ['-vf', f'scale={args.scale},setsar=1']
                if args.fps:
                    cmd += ['-r', str(args.fps)]
                cmd += ['-c:v','libx264','-preset',args.preset,'-crf',str(args.crf),'-c:a','aac','-b:a','160k','-avoid_negative_ts','make_zero',str(seg)]
            run(cmd)
        concat = td / 'concat.txt'
        concat.write_text(''.join(f"file {shlex.quote(str(s))}\n" for s in segs), encoding='utf-8')
        run([ffmpeg_cmd(),'-y','-hide_banner','-loglevel','error','-f','concat','-safe','0','-i',str(concat),'-c','copy',str(args.output)])
    print(args.output)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
