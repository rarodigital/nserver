#!/usr/bin/env python3
import argparse, json, subprocess, tempfile, os, re, textwrap
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def run(cmd):
    p = subprocess.run(cmd, text=True, encoding='utf-8', errors='replace', capture_output=True)
    if p.returncode != 0:
        raise SystemExit(f"Command failed: {' '.join(cmd)}\n{p.stdout}\n{p.stderr}")
    return p.stdout.strip()


def probe(path):
    dur=float(run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',str(path)]) or 0)
    raw=run(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=width,height:stream_side_data=rotation','-of','json',str(path)])
    info=json.loads(raw); st=info.get('streams',[{}])[0]; w=int(st.get('width') or 0); h=int(st.get('height') or 0)
    rot=0
    for sd in st.get('side_data_list') or []:
        try: rot=int(float(sd.get('rotation') or 0))
        except Exception: pass
    if abs(rot)%180==90: w,h=h,w
    return dur,w,h


def split_text(text, max_words=7):
    words=re.findall(r'\S+', str(text or ''))
    lines=[]
    for i in range(0,len(words),max_words):
        lines.append(' '.join(words[i:i+max_words]))
    return lines or ['']


def normalize_captions(cfg, dur):
    caps=cfg.get('captions') or []
    if not caps:
        lines=split_text(cfg.get('text',''), int(cfg.get('max_words') or 7))
        step=max(0.9, max(0.9,dur)/max(1,len(lines)))
        caps=[]; t=0
        for line in lines:
            caps.append({'start':t,'end':min(dur,t+step),'text':line}); t+=step
    out=[]
    for c in caps:
        start=float(c.get('start',0)); end=float(c.get('end', start+1.4)); txt=str(c.get('text','')).strip()
        if txt and end>start: out.append({'start':max(0,start),'end':min(dur,end),'text':txt})
    return out


def hex_color(c, default=(255,255,255)):
    c=str(c or '').strip().lstrip('#')
    if len(c)==3: c=''.join(ch*2 for ch in c)
    if not re.fullmatch(r'[0-9a-fA-F]{6}', c): return default
    return tuple(int(c[i:i+2],16) for i in (0,2,4))


def font(size, name='DejaVuSans-Bold'):
    font_map={
        'DejaVuSans-Bold':'/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        'DejaVuSans':'/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        'DejaVuSerif-Bold':'/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf',
        'LiberationSans-Bold':'/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf',
    }
    preferred=font_map.get(str(name), font_map['DejaVuSans-Bold'])
    for fp in [preferred,'/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf','/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf']:
        if Path(fp).exists(): return ImageFont.truetype(fp, size)
    return ImageFont.load_default(size=size)


def wrap_lines(draw, text, fnt, max_width, max_lines=2):
    words=text.split(); lines=[]; cur=''
    for w in words:
        test=(cur+' '+w).strip()
        box=draw.textbbox((0,0),test,font=fnt,stroke_width=0)
        if box[2]-box[0] <= max_width or not cur: cur=test
        else: lines.append(cur); cur=w
    if cur: lines.append(cur)
    return lines[:max_lines]


def make_png(path, text, w, h, style):
    img=Image.new('RGBA',(w,h),(0,0,0,0)); d=ImageDraw.Draw(img)
    size=int(style.get('font_size') or max(36, h*0.055)); fnt=font(size, style.get('font','DejaVuSans-Bold'))
    color=hex_color(style.get('color','#ffffff')); outline=hex_color(style.get('outline_color','#000000'), (0,0,0))
    stroke=int(float(style.get('outline') if style.get('outline') is not None else 4))
    margin_v=int(style.get('margin_v') or max(80,h*0.08)); align=int(style.get('alignment') or 2)
    max_chars=int(style.get('max_chars') or 34); max_lines=int(style.get('max_lines') or 2)
    src=text.upper() if style.get('preset','viral')!='clean' else text
    lines=wrap_lines(d, src, fnt, int(w*0.84), max_lines=max_lines)
    line_h=int(size*1.18); total_h=line_h*len(lines)
    if align==8: y=margin_v
    elif align==5: y=(h-total_h)//2
    else: y=h-margin_v-total_h
    if style.get('preset')=='box' and lines:
        widths=[d.textbbox((0,0),ln,font=fnt,stroke_width=stroke)[2] for ln in lines]
        box_w=min(w-40,max(widths)+50); box_h=total_h+34; box_x=(w-box_w)//2; box_y=y-17
        d.rounded_rectangle((box_x,box_y,box_x+box_w,box_y+box_h),radius=24,fill=(0,0,0,150))
    for line in lines:
        box=d.textbbox((0,0),line,font=fnt,stroke_width=stroke)
        tw=box[2]-box[0]
        x=(w-tw)//2
        d.text((x,y),line,font=fnt,fill=color+(255,),stroke_width=stroke,stroke_fill=outline+(255,))
        y+=line_h
    img.save(path)


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('video'); ap.add_argument('config'); ap.add_argument('-o','--output',required=True); args=ap.parse_args()
    video=Path(args.video).resolve(); cfg=json.loads(Path(args.config).read_text()); out=Path(args.output).resolve(); out.parent.mkdir(parents=True,exist_ok=True)
    dur,w,h=probe(video); caps=normalize_captions(cfg,dur); style=cfg.get('style') or {}; style['max_chars']=cfg.get('max_chars', style.get('max_chars',34)); style['max_lines']=cfg.get('max_lines', style.get('max_lines',2))
    with tempfile.TemporaryDirectory() as td:
        pngs=[]
        for i,c in enumerate(caps):
            pp=Path(td)/f'cap_{i:04d}.png'; make_png(pp,c['text'],w,h,style); pngs.append(pp)
        cmd=['ffmpeg','-y','-i',str(video)]
        for pp in pngs: cmd += ['-loop','1','-i',str(pp)]
        if pngs:
            chains=[]; prev='0:v'
            for i,c in enumerate(caps):
                outlabel=f'v{i+1}'
                chains.append(f"[{prev}][{i+1}:v]overlay=0:0:enable='between(t,{c['start']:.3f},{c['end']:.3f})'[{outlabel}]")
                prev=outlabel
            filter_complex=';'.join(chains)
            cmd += ['-filter_complex',filter_complex,'-map',f'[{prev}]','-map','0:a?']
        cmd += ['-shortest','-c:v','libx264','-preset','veryfast','-crf','18','-c:a','aac','-b:a','192k','-movflags','+faststart',str(out)]
        run(cmd)
    print(json.dumps({'ok':True,'output':str(out),'duration':dur,'captions':len(caps)},ensure_ascii=False))

if __name__=='__main__': main()
