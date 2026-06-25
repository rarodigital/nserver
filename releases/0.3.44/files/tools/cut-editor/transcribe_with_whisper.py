#!/usr/bin/env python3
import argparse, json, os, subprocess, tempfile, urllib.request, urllib.error, uuid
from pathlib import Path


def run(cmd):
    p=subprocess.run(cmd,text=True,capture_output=True)
    if p.returncode!=0:
        raise SystemExit(f"Command failed: {' '.join(cmd)}\n{p.stdout}\n{p.stderr}")
    return p.stdout.strip()


def extract_audio(video, out):
    run(['ffmpeg','-y','-i',str(video),'-vn','-ac','1','-ar','16000','-c:a','aac','-b:a','96k',str(out)])


def multipart(fields, files):
    boundary='----OpenClaw'+uuid.uuid4().hex
    chunks=[]
    for name,val in fields.items():
        if isinstance(val,(list,tuple)):
            vals=val
        else:
            vals=[val]
        for v in vals:
            chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{v}\r\n'.encode())
    for name,path,ctype in files:
        data=Path(path).read_bytes()
        filename=Path(path).name
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\nContent-Type: {ctype}\r\n\r\n'.encode()+data+b'\r\n')
    chunks.append(f'--{boundary}--\r\n'.encode())
    return boundary,b''.join(chunks)


def call_api(audio, language='pt'):
    key=os.environ.get('OPENAI_API_KEY')
    if not key:
        raise SystemExit('OPENAI_API_KEY não está configurada no servidor.')
    base=os.environ.get('OPENAI_BASE_URL','https://api.openai.com/v1').rstrip('/')
    fields={'model':'whisper-1','response_format':'verbose_json'}
    if language: fields['language']=language
    # Whisper may return words when supported; fallback to segments otherwise.
    fields['timestamp_granularities[]']=['word','segment']
    boundary,body=multipart(fields,[('file',audio,'audio/mp4')])
    req=urllib.request.Request(base+'/audio/transcriptions',data=body,method='POST',headers={
        'Authorization':'Bearer '+key,
        'Content-Type':'multipart/form-data; boundary='+boundary,
    })
    try:
        with urllib.request.urlopen(req,timeout=600) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail=e.read().decode(errors='replace')
        if e.code == 429:
            raise SystemExit('Transcrição automática bloqueada por limite da API (429). Tente novamente depois ou ajuste a chave/quota do OpenAI Whisper.')
        # fallback without timestamp_granularities for compatible proxies
        fields.pop('timestamp_granularities[]',None)
        boundary,body=multipart(fields,[('file',audio,'audio/mp4')])
        req=urllib.request.Request(base+'/audio/transcriptions',data=body,method='POST',headers={'Authorization':'Bearer '+key,'Content-Type':'multipart/form-data; boundary='+boundary})
        with urllib.request.urlopen(req,timeout=600) as r:
            return json.loads(r.read().decode())


def group_words(words, max_words=5, max_chars=32):
    caps=[]; cur=[]
    for w in words:
        txt=str(w.get('word','')).strip()
        if not txt: continue
        test=' '.join([x['word'].strip() for x in cur]+[txt])
        if cur and (len(cur)>=max_words or len(test)>max_chars):
            caps.append({'start':float(cur[0].get('start',0)),'end':float(cur[-1].get('end',cur[-1].get('start',0)+.4)),'text':' '.join(x['word'].strip() for x in cur)})
            cur=[]
        cur.append({'word':txt,'start':w.get('start',0),'end':w.get('end',w.get('start',0)+.4)})
    if cur:
        caps.append({'start':float(cur[0].get('start',0)),'end':float(cur[-1].get('end',cur[-1].get('start',0)+.4)),'text':' '.join(x['word'].strip() for x in cur)})
    return caps


def from_segments(segments, max_words=7):
    caps=[]
    for seg in segments or []:
        text=str(seg.get('text','')).strip()
        if not text: continue
        words=text.split(); start=float(seg.get('start',0)); end=float(seg.get('end',start+1.5)); dur=max(.4,end-start)
        if len(words)<=max_words:
            caps.append({'start':start,'end':end,'text':text}); continue
        groups=[words[i:i+max_words] for i in range(0,len(words),max_words)]
        for idx,g in enumerate(groups):
            a=start+dur*idx/len(groups); b=start+dur*(idx+1)/len(groups)
            caps.append({'start':a,'end':b,'text':' '.join(g)})
    return caps


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('video')
    ap.add_argument('-o','--output',required=True)
    ap.add_argument('--language',default='pt')
    ap.add_argument('--max-words',type=int,default=5)
    ap.add_argument('--max-chars',type=int,default=34)
    args=ap.parse_args()
    video=Path(args.video).resolve(); out=Path(args.output).resolve(); out.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        audio=Path(td)/'audio.m4a'; extract_audio(video,audio)
        raw=call_api(audio,args.language)
    words=raw.get('words') or []
    captions=group_words(words,args.max_words,args.max_chars) if words else from_segments(raw.get('segments') or [],args.max_words)
    result={'ok':True,'text':raw.get('text',''),'language':raw.get('language',args.language),'duration':raw.get('duration'), 'captions':captions, 'raw_segments':raw.get('segments',[])}
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'ok':True,'output':str(out),'captions':len(captions)},ensure_ascii=False))

if __name__=='__main__': main()
