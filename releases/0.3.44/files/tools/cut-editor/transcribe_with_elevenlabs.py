#!/usr/bin/env python3
import argparse, json, os, subprocess, tempfile, urllib.request, urllib.error, uuid
from pathlib import Path


def run(cmd):
    p=subprocess.run(cmd,text=True,capture_output=True)
    if p.returncode!=0:
        raise SystemExit(f"Command failed: {' '.join(cmd)}\n{p.stdout}\n{p.stderr}")
    return p.stdout.strip()


def extract_audio(video, out):
    run(['ffmpeg','-y','-i',str(video),'-vn','-ac','1','-ar','16000','-c:a','mp3','-b:a','96k',str(out)])


def multipart(fields, files):
    boundary='----OpenClaw'+uuid.uuid4().hex
    chunks=[]
    for name,val in fields.items():
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{val}\r\n'.encode())
    for name,path,ctype in files:
        data=Path(path).read_bytes(); filename=Path(path).name
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\nContent-Type: {ctype}\r\n\r\n'.encode()+data+b'\r\n')
    chunks.append(f'--{boundary}--\r\n'.encode())
    return boundary,b''.join(chunks)


def call_api(audio, language='pt'):
    key=os.environ.get('ELEVENLABS_API_KEY') or os.environ.get('ELEVEN_API_KEY') or os.environ.get('XI_API_KEY')
    if not key:
        raise SystemExit('ELEVENLABS_API_KEY não está configurada no servidor.')
    fields={'model_id':'scribe_v1'}
    if language: fields['language_code']=language
    boundary,body=multipart(fields,[('file',audio,'audio/mpeg')])
    req=urllib.request.Request('https://api.elevenlabs.io/v1/speech-to-text',data=body,method='POST',headers={
        'xi-api-key':key,
        'Content-Type':'multipart/form-data; boundary='+boundary,
    })
    try:
        with urllib.request.urlopen(req,timeout=600) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail=e.read().decode(errors='replace')
        if e.code==401:
            raise SystemExit('ElevenLabs recusou a chave de API (401). Verifique a chave configurada.')
        if e.code==429:
            raise SystemExit('ElevenLabs retornou limite/quota (429). Tente novamente depois ou ajuste o plano/quota.')
        raise SystemExit(f'Erro ElevenLabs HTTP {e.code}: {detail[:1000]}')


def group_words(raw_words, max_words=5, max_chars=34):
    words=[]
    for w in raw_words or []:
        # ElevenLabs usually returns {text,start,end,type}; tolerate {word,start,end}
        txt=str(w.get('text') or w.get('word') or '').strip()
        typ=str(w.get('type') or 'word')
        if not txt or typ not in ('word','spacing','punctuation'):
            continue
        if typ=='spacing':
            continue
        words.append({'word':txt,'start':float(w.get('start') or 0),'end':float(w.get('end') or (w.get('start') or 0)+.35)})
    caps=[]; cur=[]
    for w in words:
        test=' '.join([x['word'] for x in cur]+[w['word']])
        if cur and (len(cur)>=max_words or len(test)>max_chars):
            caps.append({'start':cur[0]['start'],'end':cur[-1]['end'],'text':' '.join(x['word'] for x in cur)})
            cur=[]
        cur.append(w)
    if cur: caps.append({'start':cur[0]['start'],'end':cur[-1]['end'],'text':' '.join(x['word'] for x in cur)})
    return caps


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('video'); ap.add_argument('-o','--output',required=True); ap.add_argument('--language',default='pt'); ap.add_argument('--max-words',type=int,default=5); ap.add_argument('--max-chars',type=int,default=34); args=ap.parse_args()
    video=Path(args.video).resolve(); out=Path(args.output).resolve(); out.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        audio=Path(td)/'audio.mp3'; extract_audio(video,audio); raw=call_api(audio,args.language)
    captions=group_words(raw.get('words') or [], args.max_words, args.max_chars)
    if not captions and raw.get('text'):
        # fallback: one unsynced block if provider doesn't return words
        captions=[{'start':0,'end':float(raw.get('duration') or 3),'text':raw.get('text')}]
    result={'ok':True,'provider':'elevenlabs','text':raw.get('text',''),'language':raw.get('language_code',args.language),'duration':raw.get('duration'), 'captions':captions, 'raw_words':raw.get('words',[])}
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'ok':True,'provider':'elevenlabs','output':str(out),'captions':len(captions)},ensure_ascii=False))

if __name__=='__main__': main()
