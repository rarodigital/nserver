#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import socket
import shutil
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from processors.video import VideoProcessor
from updater import Updater

APP_NAME = "Nserver"
APP_VERSION = "0.2.0"
HOST = os.environ.get("NSERVER_HOST", "0.0.0.0")
PORT = int(os.environ.get("NSERVER_PORT", "8791"))
ROOT = Path(__file__).resolve().parents[1]
SYSTEM = ROOT / "system"
USERDATA = ROOT / "userdata"
MIDIAS = ROOT / "midias"
DATA = USERDATA  # compatibilidade interna: dados do usuário agora vivem em /userdata
for folder in (SYSTEM, USERDATA, MIDIAS):
    folder.mkdir(exist_ok=True)
old_media = ROOT / "data" / "Midias"
if old_media.exists() and not any(MIDIAS.iterdir()):
    shutil.copytree(old_media, MIDIAS, dirs_exist_ok=True)
old_history = ROOT / "data" / "history.json"
if old_history.exists() and not (USERDATA / "history.json").exists():
    shutil.copy2(old_history, USERDATA / "history.json")

# Usuário inicial solicitado. Senha armazenada como hash, não texto puro.
USERNAME = "Adaltovieira"
SALT = "nserver-local-v1"
PASSWORD_SHA256 = ""
SESSIONS: dict[str, dict] = {}
TOOLS = [
    {
        "id": "video-downloader",
        "name": "Downloader e Processador de Vídeos",
        "description": "Baixe vídeos, extraia áudio, gere transcrições e prepare cortes virais a partir de links públicos.",
        "status": "mvp ativo",
        "href": "/tool/video-downloader",
    }
]
PROCESSORS = {"video-downloader": VideoProcessor(ROOT)}
UPDATER = Updater(ROOT, APP_VERSION)
HISTORY_FILE = USERDATA / "history.json"


def load_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_history(entry: dict):
    history = load_history()
    history.insert(0, entry)
    HISTORY_FILE.write_text(json.dumps(history[:100], ensure_ascii=False, indent=2), encoding="utf-8")


def password_hash(password: str) -> str:
    return hashlib.sha256((SALT + ":" + password).encode("utf-8")).hexdigest()


# hash de 52ar4ever
PASSWORD_SHA256 = password_hash("52ar4ever")


def local_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except Exception:
        return "IP-DO-NOTEBOOK"


def html_page(title: str, body: str, authenticated: bool = False) -> bytes:
    nav = ""
    if authenticated:
        nav = """
        <nav class="topbar">
          <div class="brand">Nserver</div>
          <div class="navlinks">
            <a href="/welcome">Boas-vindas</a>
            <a href="/dashboard">Dashboard</a>
            <a href="/updates">Atualizações</a>
            <a href="/logout">Sair</a>
          </div>
        </nav>
        """
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title} — Nserver</title>
  <style>
    :root {{ --bg:#090b10; --panel:#131722; --panel2:#181d2a; --line:#2a3140; --text:#f8fafc; --muted:#94a3b8; --accent:#5b7cfa; --accent2:#22c55e; --danger:#fb7185; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; min-height:100vh; font-family:Inter,Segoe UI,system-ui,-apple-system,sans-serif; color:var(--text); background: radial-gradient(circle at top left,#1d2b5f 0,#090b10 35%,#05060a 100%); }}
    a {{ color:#bfdbfe; text-decoration:none; }}
    .topbar {{ display:flex; align-items:center; justify-content:space-between; padding:16px 22px; border-bottom:1px solid var(--line); background:#080a0fcc; backdrop-filter:blur(14px); position:sticky; top:0; }}
    .brand {{ font-weight:800; letter-spacing:.4px; }}
    .navlinks {{ display:flex; gap:14px; font-size:14px; }}
    .wrap {{ width:min(1040px,92vw); margin:0 auto; padding:36px 0; }}
    .center {{ min-height:100vh; display:grid; place-items:center; padding:24px; }}
    .card {{ background:linear-gradient(180deg,var(--panel),#0d111a); border:1px solid var(--line); border-radius:22px; padding:24px; box-shadow:0 24px 80px #0008; }}
    .login {{ width:min(420px,94vw); }}
    h1 {{ margin:0 0 10px; font-size:clamp(28px,5vw,48px); }}
    h2 {{ margin:0 0 10px; }}
    p {{ color:var(--muted); line-height:1.55; }}
    label {{ display:block; margin:16px 0 7px; color:#cbd5e1; font-weight:650; }}
    input {{ width:100%; padding:13px 14px; border-radius:13px; border:1px solid #334155; background:#080a10; color:var(--text); font-size:16px; outline:none; }}
    input:focus {{ border-color:var(--accent); box-shadow:0 0 0 3px #5b7cfa33; }}
    button,.button {{ display:inline-flex; align-items:center; justify-content:center; gap:8px; margin-top:18px; padding:13px 17px; border-radius:13px; border:0; background:linear-gradient(135deg,var(--accent),#7c3aed); color:white; font-weight:800; cursor:pointer; font-size:15px; }}
    .button.secondary {{ background:#1f2937; border:1px solid #374151; }}
    .error {{ color:#fecdd3; background:#88133755; border:1px solid #fb718555; padding:10px 12px; border-radius:12px; }}
    .hero {{ display:grid; gap:18px; }}
    .meta {{ display:flex; gap:12px; flex-wrap:wrap; margin-top:18px; }}
    .pill {{ padding:9px 12px; border-radius:999px; background:#111827; border:1px solid #293241; color:#cbd5e1; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:16px; margin-top:22px; }}
    .tool {{ min-height:170px; display:flex; flex-direction:column; justify-content:space-between; transition:.16s transform,.16s border-color; }}
    .tool:hover {{ transform:translateY(-2px); border-color:#5264ff; }}
    .status {{ color:#bbf7d0; font-size:13px; text-transform:uppercase; letter-spacing:.08em; }}
    .muted {{ color:var(--muted); }}
    .footer-note {{ margin-top:24px; font-size:14px; color:#64748b; }}
    .row {{ display:flex; gap:12px; flex-wrap:wrap; align-items:end; }}
    .row > * {{ flex:1 1 170px; }}
    select {{ width:100%; padding:13px 14px; border-radius:13px; border:1px solid #334155; background:#080a10; color:var(--text); font-size:16px; }}
    .result {{ margin-top:18px; padding:14px; border-radius:14px; background:#0b1220; border:1px solid #243044; white-space:pre-wrap; }}
    .video-info {{ display:grid; grid-template-columns:minmax(180px,300px) 1fr; gap:18px; margin-top:18px; align-items:start; }}
    .video-info img {{ width:100%; border-radius:16px; border:1px solid #334155; background:#05060a; }}
    .actions {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:14px; margin-top:18px; }}
    .mini {{ font-size:13px; color:#94a3b8; }}
    @media (max-width:720px) {{ .video-info {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
{nav}
{body}
<script>
function tick() {{
  const el = document.querySelector('[data-clock]');
  if (el) el.textContent = new Date().toLocaleString('pt-BR');
}}
setInterval(tick, 1000); tick();
</script>
</body>
</html>""".encode("utf-8")


def parse_cookie(header: str | None) -> dict[str, str]:
    if not header:
        return {}
    jar = cookies.SimpleCookie()
    try:
        jar.load(header)
    except Exception:
        return {}
    return {k: v.value for k, v in jar.items()}


class Handler(BaseHTTPRequestHandler):
    def user_session(self) -> dict | None:
        sid = parse_cookie(self.headers.get("Cookie")).get("nserver_session")
        if not sid:
            return None
        sess = SESSIONS.get(sid)
        if not sess:
            return None
        if time.time() - sess["created"] > 60 * 60 * 12:
            SESSIONS.pop(sid, None)
            return None
        return sess

    def send_html(self, body: bytes, code: int = 200, extra_headers: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, path: str, headers: dict | None = None):
        self.send_response(302)
        self.send_header("Location", path)
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()

    def require_login(self) -> dict | None:
        sess = self.user_session()
        if not sess:
            self.redirect("/")
            return None
        return sess

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            if self.user_session():
                self.redirect("/welcome")
                return
            error = ""
            if parse_qs(urlparse(self.path).query).get("error"):
                error = '<div class="error">Usuário ou senha inválidos.</div>'
            body = html_page("Login", f"""
            <main class="center">
              <section class="card login">
                <h1>Nserver</h1>
                <p>Painel pessoal local rodando no notebook.</p>
                {error}
                <form method="post" action="/login">
                  <label>Usuário</label>
                  <input name="username" autocomplete="username" autofocus />
                  <label>Senha</label>
                  <input name="password" type="password" autocomplete="current-password" />
                  <button type="submit">Acessar</button>
                </form>
              </section>
            </main>
            """)
            self.send_html(body)
            return
        if path == "/welcome":
            sess = self.require_login()
            if not sess: return
            body = html_page("Boas-vindas", f"""
            <main class="wrap hero">
              <section class="card">
                <h1>Bem-vindo, {USERNAME}.</h1>
                <p>O <strong>Nserver</strong> está online no notebook e pronto para receber suas ferramentas pessoais.</p>
                <div class="meta">
                  <span class="pill">Sistema: Nserver</span>
                  <span class="pill">Versão: {APP_VERSION}</span>
                  <span class="pill">Agora: <span data-clock></span></span>
                  <span class="pill">Modo: rede local</span>
                </div>
                <a class="button" href="/dashboard">Abrir dashboard</a>
              </section>
            </main>
            """, authenticated=True)
            self.send_html(body)
            return
        if path == "/dashboard":
            sess = self.require_login()
            if not sess: return
            cards = "".join(f"""
              <a class="card tool" href="{tool['href']}">
                <div>
                  <div class="status">{tool['status']}</div>
                  <h2>{tool['name']}</h2>
                  <p>{tool['description']}</p>
                </div>
                <span class="muted">Abrir →</span>
              </a>
            """ for tool in TOOLS)
            body = html_page("Dashboard", f"""
            <main class="wrap">
              <h1>Dashboard</h1>
              <p>Centro de controle do Nserver. As próximas ferramentas aparecerão aqui como módulos independentes.</p>
              <section class="grid">{cards}</section>
              <p class="footer-note">MVP ativo: login, dashboard, ferramenta de vídeo e atualização automática modular.</p>
            </main>
            """, authenticated=True)
            self.send_html(body)
            return
        if path == "/tool/video-downloader":
            sess = self.require_login()
            if not sess: return
            recent = load_history()[:8]
            rows = "".join(f"<p class='mini'>• {item.get('date','')} — {item.get('operation','')} — {item.get('status','')}<br><span class='muted'>{item.get('url','')}</span></p>" for item in recent) or "<p class='muted'>Nenhum processamento ainda.</p>"
            body = html_page("Downloader e Processador de Vídeos", f"""
            <main class="wrap">
              <section class="card">
                <h1>Ferramenta 01 — Downloader e Processador de Vídeos</h1>
                <p>Cole um link público, analise o vídeo e escolha a ação. Arquitetura preparada por processadores para novos módulos.</p>
                <label>URL do vídeo</label>
                <div class="row">
                  <input id="url" placeholder="https://youtube.com/..." />
                  <button onclick="analyzeVideo()">Analisar Vídeo</button>
                </div>
                <div id="result" class="result muted">Aguardando link.</div>
                <div id="info"></div>
              </section>

              <section class="card" style="margin-top:18px">
                <h2>Ações disponíveis</h2>
                <div class="actions">
                  <div>
                    <h3>Download de vídeo</h3>
                    <label>Resolução</label><select id="videoQuality"><option>360</option><option>480</option><option selected>720</option><option>1080</option><option>1440</option><option>2160</option></select>
                    <label>Formato</label><select id="videoFormat"><option selected>mp4</option><option>mov</option></select>
                    <button onclick="runAction('download_video')">Baixar vídeo</button>
                  </div>
                  <div>
                    <h3>Extrair áudio</h3>
                    <label>Formato</label><select id="audioFormat"><option selected>mp3</option><option>wav</option></select>
                    <label>Qualidade</label><select id="audioQuality"><option>64</option><option>128</option><option selected>192</option><option>256</option><option>320</option></select>
                    <button onclick="runAction('extract_audio')">Extrair áudio</button>
                  </div>
                  <div>
                    <h3>Transcrição</h3>
                    <p class="mini">Base pronta para Whisper/local. Nesta versão cria registro pendente.</p>
                    <button onclick="runAction('transcribe')">Gerar transcrição</button>
                  </div>
                  <div>
                    <h3>Cortes virais</h3>
                    <label>Quantidade</label><select id="clipCount"><option>1</option><option selected>3</option><option>5</option><option>10</option></select>
                    <label>Duração máx.</label><select id="clipSeconds"><option>30</option><option selected>60</option><option>90</option></select>
                    <button onclick="runAction('viral_clips')">Criar plano de cortes</button>
                  </div>
                </div>
              </section>

              <section class="card" style="margin-top:18px">
                <h2>Histórico recente</h2>
                {rows}
              </section>
            </main>
<script>
async function api(payload) {{
  const res = await fetch('/api/video', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(payload)}});
  return await res.json();
}}
function currentUrl() {{ return document.getElementById('url').value.trim(); }}
function esc(value) {{ return String(value ?? '').replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch])); }}
async function analyzeVideo() {{
  const box = document.getElementById('result'); box.textContent='Analisando...';
  const data = await api({{action:'analyze', url: currentUrl()}});
  box.textContent = data.message;
  if (data.ok && data.data) {{
    const d=data.data;
    document.getElementById('info').innerHTML = `<div class="video-info">${{d.thumbnail ? `<img src="${{esc(d.thumbnail)}}">` : ''}}<div><h2>${{esc(d.title)}}</h2><p><b>Plataforma:</b> ${{esc(d.platform)}}<br><b>Duração:</b> ${{esc(d.duration_text)}}<br><b>Resoluções:</b> ${{esc((d.resolutions||[]).map(x=>x.label).join(', ') || 'não informado')}}</p></div></div>`;
  }}
}}
async function runAction(action) {{
  const box = document.getElementById('result'); box.textContent='Processando... isso pode levar alguns minutos.';
  const payload={{action, url:currentUrl()}};
  if(action==='download_video') {{ payload.quality=document.getElementById('videoQuality').value; payload.format=document.getElementById('videoFormat').value; }}
  if(action==='extract_audio') {{ payload.quality=document.getElementById('audioQuality').value; payload.format=document.getElementById('audioFormat').value; }}
  if(action==='viral_clips') {{ payload.count=document.getElementById('clipCount').value; payload.max_seconds=document.getElementById('clipSeconds').value; }}
  const data=await api(payload);
  box.textContent = data.message + (data.data ? '\n' + JSON.stringify(data.data, null, 2) : '');
}}
</script>
            """, authenticated=True)
            self.send_html(body)
            return
        if path.startswith("/tool/"):
            sess = self.require_login()
            if not sess: return
            self.redirect("/tool/video-downloader")
            return
        if path == "/updates":
            sess = self.require_login()
            if not sess: return
            cfg = UPDATER.config
            last_file = USERDATA / "last-update.json"
            last = ""
            if last_file.exists():
                try:
                    info = json.loads(last_file.read_text(encoding="utf-8"))
                    last = f"<p class='mini'>Última atualização: {info.get('date')} — {info.get('from')} → {info.get('to')}<br>Backup: {info.get('backup')}</p>"
                except Exception:
                    last = ""
            manifest_url = cfg.get("update_manifest_url", "")
            body = html_page("Atualizações", f"""
            <main class="wrap">
              <section class="card">
                <h1>Atualizações do Nserver</h1>
                <p>Atualização automática com backup antes de aplicar mudanças. As pastas <strong>userdata</strong> e <strong>midias</strong> são preservadas.</p>
                <div class="meta">
                  <span class="pill">Versão atual: {APP_VERSION}</span>
                  <span class="pill">Canal: {cfg.get('update_channel', 'stable')}</span>
                  <span class="pill">Instalado em: {cfg.get('installed_at', '-')}</span>
                </div>
                <label>URL do manifesto de atualização</label>
                <input id="manifestUrl" value="{manifest_url}" placeholder="https://.../nserver-manifest.json" />
                <label>Canal</label>
                <select id="channel"><option value="stable">stable</option><option value="beta">beta</option></select>
                <div class="row">
                  <button onclick="saveUpdateConfig()">Salvar configuração</button>
                  <button onclick="checkUpdate()">Verificar atualizações</button>
                  <button onclick="applyUpdate()">Atualizar agora</button>
                </div>
                <div id="updateResult" class="result muted">Aguardando verificação.</div>
                {last}
              </section>

              <section class="card" style="margin-top:18px">
                <h2>Estrutura protegida</h2>
                <p><strong>/system</strong> — arquivos internos e manifestos locais<br>
                <strong>/userdata</strong> — configurações, histórico e banco de dados<br>
                <strong>/midias</strong> — vídeos, áudios, transcrições e cortes<br>
                <strong>/backups</strong> — cópia automática antes de cada update</p>
              </section>
            </main>
<script>
document.getElementById('channel').value = '{cfg.get('update_channel', 'stable')}';
async function updateApi(payload) {{
  const res = await fetch('/api/updates', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(payload)}});
  return await res.json();
}}
async function saveUpdateConfig() {{
  const data = await updateApi({{action:'config', manifest_url:document.getElementById('manifestUrl').value, channel:document.getElementById('channel').value}});
  document.getElementById('updateResult').textContent = data.message;
}}
async function checkUpdate() {{
  const box=document.getElementById('updateResult'); box.textContent='Verificando...';
  const data = await updateApi({{action:'check'}});
  box.textContent = data.message + '\n' + JSON.stringify(data.data || {{}}, null, 2);
}}
async function applyUpdate() {{
  if(!confirm('Criar backup e aplicar atualização agora?')) return;
  const box=document.getElementById('updateResult'); box.textContent='Atualizando...';
  const data = await updateApi({{action:'apply'}});
  box.textContent = data.message + '\n' + JSON.stringify(data.data || {{}}, null, 2);
}}
</script>
            """, authenticated=True)
            self.send_html(body)
            return
        if path == "/logout":
            sid = parse_cookie(self.headers.get("Cookie")).get("nserver_session")
            if sid:
                SESSIONS.pop(sid, None)
            self.redirect("/", {"Set-Cookie": "nserver_session=; Max-Age=0; Path=/; SameSite=Lax"})
            return
        self.send_response(404); self.end_headers()

    def send_json(self, payload: dict, code: int = 200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/updates":
            sess = self.require_login()
            if not sess: return
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                self.send_json({"ok": False, "message": "JSON inválido."}, 400); return
            action = payload.get("action")
            try:
                if action == "config":
                    cfg = UPDATER.save_config({
                        "update_manifest_url": payload.get("manifest_url", "").strip(),
                        "update_channel": payload.get("channel", "stable"),
                    })
                    self.send_json({"ok": True, "message": "Configuração de atualização salva.", "data": cfg}); return
                if action == "check":
                    check = UPDATER.check()
                    data = {
                        "current_version": check.current_version,
                        "latest_version": check.latest_version,
                        "update_available": check.update_available,
                        "changelog": (check.manifest or {}).get("changelog", []),
                    }
                    self.send_json({"ok": check.ok, "message": check.message, "data": data}); return
                if action == "apply":
                    check = UPDATER.check()
                    if not check.ok:
                        self.send_json({"ok": False, "message": check.message}); return
                    if not check.update_available:
                        self.send_json({"ok": True, "message": "Nenhuma atualização disponível.", "data": {"current_version": APP_VERSION}}); return
                    result = UPDATER.apply(check.manifest)
                    UPDATER.schedule_restart()
                    self.send_json({"ok": True, "message": result["message"], "data": result}); return
                self.send_json({"ok": False, "message": "Ação de update desconhecida."}, 400); return
            except Exception as exc:
                self.send_json({"ok": False, "message": f"Falha no update: {exc}"}, 500); return
        if path == "/api/video":
            sess = self.require_login()
            if not sess: return
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                self.send_json({"ok": False, "message": "JSON inválido."}, 400); return
            result = PROCESSORS["video-downloader"].run(payload)
            save_history({
                "date": time.strftime("%Y-%m-%d %H:%M:%S"),
                "url": payload.get("url", ""),
                "operation": payload.get("action", ""),
                "status": "ok" if result.ok else "erro",
                "location": (result.data or {}).get("folder") or (result.data or {}).get("file") or "",
            })
            self.send_json({"ok": result.ok, "message": result.message, "data": result.data or {}})
            return
        if path != "/login":
            self.send_response(404); self.end_headers(); return
        length = int(self.headers.get("Content-Length", "0"))
        data = parse_qs(self.rfile.read(length).decode("utf-8"))
        username = data.get("username", [""])[0]
        password = data.get("password", [""])[0]
        ok_user = hmac.compare_digest(username, USERNAME)
        ok_pass = hmac.compare_digest(password_hash(password), PASSWORD_SHA256)
        if ok_user and ok_pass:
            sid = secrets.token_urlsafe(32)
            SESSIONS[sid] = {"username": USERNAME, "created": time.time()}
            self.redirect("/welcome", {"Set-Cookie": f"nserver_session={sid}; HttpOnly; Path=/; SameSite=Lax; Max-Age=43200"})
            return
        self.redirect("/?error=1")

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))


if __name__ == "__main__":
    print("========================================")
    print("  Nserver - Painel Pessoal Desktop")
    print("========================================")
    print(f"No notebook: http://127.0.0.1:{PORT}")
    print(f"No celular na mesma rede Wi-Fi: http://{local_ip()}:{PORT}")
    print("")
    print("Login: usuário configurado")
    print("Para parar: feche esta janela ou pressione Ctrl+C")
    print("")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
