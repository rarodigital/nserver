#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import hmac
import html
import json
import mimetypes
import os
import subprocess
import sys
import re
import secrets
import socket
import shutil
import threading
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from processors.video import VideoProcessor
from processors.editor import VideoEditorProcessor
from processors.course import CourseProcessor
from processors.media import MediaManager, VIDEO_EXTS, AUDIO_EXTS
from updater import Updater

APP_NAME = "Nserver"
APP_VERSION = "0.3.32"
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
        "name": "Ferramenta 01 — Downloader",
        "description": "Baixe vídeo MP4/MOV ou extraia áudio MP3/WAV de links públicos.",
        "status": "ativo",
        "href": "/tool/video-downloader",
    },
    {
        "id": "transcription",
        "name": "Ferramenta 02 — Transcrição",
        "description": "Transcreva vídeos em TXT, MD, DOCX ou PDF usando modo local gratuito ou OpenAI opcional.",
        "status": "ativo",
        "href": "/tool/transcription",
    },
    {
        "id": "viral-clips",
        "name": "Ferramenta 03 — Cortes Virais",
        "description": "Planeje cortes automáticos estilo OpusClip com quantidade, duração e ranking de potencial viral.",
        "status": "em evolução",
        "href": "/tool/viral-clips",
    },
    {
        "id": "video-editor",
        "name": "Ferramenta 04 — Editor de Vídeo",
        "description": "Monte uma timeline, corte trechos, ajuste formato/grade e renderize preview ou final na Biblioteca.",
        "status": "novo",
        "href": "/tool/video-editor",
    },
    {
        "id": "course-ingest",
        "name": "Ferramenta 05 — Curso → TheronCore",
        "description": "Mapeie um curso com login, escolha tudo/módulo/aula e gere vídeos, áudios ou transcrições organizadas.",
        "status": "novo",
        "href": "/tool/course-ingest",
    },
]
PROCESSORS = {"video-downloader": VideoProcessor(ROOT), "video-editor": VideoEditorProcessor(ROOT), "course-ingest": CourseProcessor(ROOT)}
MEDIA = MediaManager(ROOT)
UPDATER = Updater(ROOT, APP_VERSION)
HISTORY_FILE = USERDATA / "history.json"
FAVORITES_FILE = USERDATA / "favorites.json"
DOWNLOADS: dict[str, dict] = {}
FILE_TOKENS: dict[str, dict] = {}


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


def safe_download_name(name: str) -> tuple[str, str]:
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "download"
    # Keep extension if regex mangling removed too much.
    if "." not in ascii_name and "." in name:
        ascii_name += Path(name).suffix
    utf8_name = quote(name, safe="")
    return ascii_name[:120], utf8_name




def operation_label(action: str, payload: dict | None = None) -> str:
    payload = payload or {}
    labels = {
        "analyze": "Análise de vídeo",
        "download_video": f"Download {str(payload.get('format') or 'mp4').upper()} {payload.get('quality') or '720'}p",
        "extract_audio": f"Download áudio {str(payload.get('format') or 'mp3').upper()} {payload.get('quality') or '192'} kbps",
        "transcribe": "Transcrição",
        "viral_clips": "Corte viral",
        "video_editor": "Editor de vídeo",
    }
    return labels.get(action, action or "Operação")


def upsert_history(entry: dict):
    history = load_history()
    history.insert(0, entry)
    HISTORY_FILE.write_text(json.dumps(history[:300], ensure_ascii=False, indent=2), encoding="utf-8")


def delete_history(ids: list[str] | None = None, all_items: bool = False) -> int:
    history = load_history()
    if all_items:
        HISTORY_FILE.write_text("[]", encoding="utf-8")
        return len(history)
    remove = set(ids or [])
    kept = [item for item in history if item.get("id") not in remove]
    HISTORY_FILE.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(history) - len(kept)


def load_favorites() -> set[str]:
    if not FAVORITES_FILE.exists():
        return set()
    try:
        return set(json.loads(FAVORITES_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()


def save_favorites(favorites: set[str]):
    FAVORITES_FILE.write_text(json.dumps(sorted(favorites), ensure_ascii=False, indent=2), encoding="utf-8")


def is_intermediate_media(path: Path) -> bool:
    name = path.name.lower()
    if name.endswith((".part", ".ytdl")):
        return True
    match = re.search(r"\.f\d+\.(mp4|webm|m4a|opus)$", name)
    if not match:
        return False
    # Hide yt-dlp intermediates only when a final sibling exists.
    # If the intermediate is the only file left, show it so the user can play/delete it.
    base = re.sub(r"\.f\d+(?=\.(mp4|webm|m4a|opus)$)", "", name)
    for sibling in path.parent.iterdir():
        if sibling.is_file() and sibling.name.lower() == base:
            return True
    return False


def media_kind(path: Path) -> str:
    rel = path.relative_to(MIDIAS).parts[0] if MIDIAS in path.resolve().parents else "Outros"
    low = path.suffix.lower()
    if rel == "Videos" or low in VIDEO_EXTS:
        return "Vídeos"
    if rel == "Audios" or low in AUDIO_EXTS:
        return "Áudios"
    if rel == "Transcricoes" or low in {".txt", ".md", ".pdf", ".docx", ".json"}:
        return "Transcrições"
    if rel == "Cortes":
        return "Cortes"
    return rel


def human_size(size: int) -> str:
    value = float(size)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024


def token_for_file(path: Path, inline: bool = False) -> str:
    token = secrets.token_urlsafe(18)
    FILE_TOKENS[token] = {"path": str(path), "inline": inline, "created": time.time()}
    return token


def library_files() -> list[dict]:
    items = []
    favorites = load_favorites()
    for path in MIDIAS.rglob("*"):
        if not path.is_file() or is_intermediate_media(path):
            continue
        try:
            resolved = path.resolve()
            rel = resolved.relative_to(MIDIAS.resolve()).as_posix()
            if rel.split("/", 1)[0] == "_Temporarios":
                # Arquivos de "Baixar neste dispositivo" são links temporários,
                # não itens permanentes da Biblioteca.
                continue
            size = resolved.stat().st_size
            kind = media_kind(resolved)
            dl = token_for_file(resolved, inline=False)
            play = token_for_file(resolved, inline=True)
            items.append({
                "id": rel,
                "name": resolved.name,
                "relative": rel,
                "kind": kind,
                "created": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(resolved.stat().st_ctime)),
                "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(resolved.stat().st_mtime)),
                "size_bytes": size,
                "size": human_size(size),
                "download_url": f"/file/{dl}/{safe_download_name(resolved.name)[0]}",
                "play_url": f"/file/{play}/{safe_download_name(resolved.name)[0]}",
                "folder": str(resolved.parent),
                "is_video": resolved.suffix.lower() in VIDEO_EXTS,
                "is_audio": resolved.suffix.lower() in AUDIO_EXTS,
                "is_text": resolved.suffix.lower() in {".txt", ".md", ".json"},
                "favorite": rel in favorites,
            })
        except Exception:
            continue
    return sorted(items, key=lambda x: x["modified"], reverse=True)


def library_stats(items: list[dict]) -> dict:
    stats = {"Vídeos": 0, "Áudios": 0, "Transcrições": 0, "Cortes": 0, "Outros": 0, "bytes": 0}
    for item in items:
        stats[item.get("kind", "Outros")] = stats.get(item.get("kind", "Outros"), 0) + 1
        stats["bytes"] += int(item.get("size_bytes") or 0)
    usage = shutil.disk_usage(ROOT)
    stats["size"] = human_size(stats["bytes"])
    stats["disk_free"] = human_size(usage.free)
    stats["disk_total"] = human_size(usage.total)
    return stats

def load_config() -> dict:
    return UPDATER.load_config()


def save_app_config(patch: dict) -> dict:
    return UPDATER.save_config(patch)


def public_config() -> dict:
    cfg = load_config()
    key = (cfg.get("openai_api_key") or "").strip()
    return {
        "openai_configured": bool(key),
        "openai_key_masked": (key[:7] + "..." + key[-4:]) if len(key) > 12 else ("configurada" if key else ""),
        "openai_base_url": cfg.get("openai_base_url", "https://api.openai.com/v1"),
        "transcription_provider": cfg.get("transcription_provider", "local"),
        "local_whisper_model": cfg.get("local_whisper_model", "base"),
    }


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
            <a href="/library">Biblioteca</a>
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
    details.settings-panel {{ margin-top:12px; padding:12px; border:1px solid #263244; border-radius:14px; background:#0b1220; }}
    details.settings-panel summary {{ cursor:pointer; color:#bfdbfe; font-weight:800; }}
    button:disabled {{ opacity:.62; cursor:wait; }}
    .history-list,.media-list {{ display:grid; gap:14px; margin-top:16px; }}
    .history-item,.media-item {{ display:grid; grid-template-columns:92px 1fr; gap:14px; align-items:start; padding:14px; border:1px solid #263244; border-radius:16px; background:#0b1220; }}
    .thumb {{ width:92px; height:62px; object-fit:cover; border-radius:10px; background:#111827; border:1px solid #293241; }}
    .item-actions {{ display:flex; gap:8px; flex-wrap:wrap; }}
    .item-actions button,.item-actions .button {{ margin-top:8px; padding:9px 11px; font-size:13px; }}
    .danger-btn {{ background:#991b1b !important; }}
    .stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin:18px 0; }}
    .stat {{ padding:14px; border-radius:16px; background:#0b1220; border:1px solid #263244; }}
    .player {{ width:100%; max-height:70vh; margin-top:10px; border-radius:14px; background:#000; }}
    @media (max-width:720px) {{ .video-info,.history-item,.media-item {{ grid-template-columns:1fr; }} .thumb {{ width:100%; height:160px; }} }}
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
            body = html_page("Downloader e Processador de Vídeos", f"""
            <main class="wrap">
              <section class="card">
                <h1>Ferramenta 01 — Downloader</h1>
                <p>Baixe vídeo ou áudio de links públicos. Transcrição e cortes agora ficam em ferramentas próprias no Dashboard.</p>
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
                    <label>Destino</label><select id="videoDestination"><option value="server" selected>Salvar no Nserver</option><option value="device">Baixar neste dispositivo</option></select>
                    <button onclick="runAction('download_video')">Baixar vídeo</button>
                  </div>
                  <div>
                    <h3>Extrair áudio</h3>
                    <label>Formato</label><select id="audioFormat"><option selected>mp3</option><option>wav</option></select>
                    <label>Qualidade</label><select id="audioQuality"><option>64</option><option>128</option><option selected>192</option><option>256</option><option>320</option></select>
                    <label>Destino</label><select id="audioDestination"><option value="server" selected>Salvar no Nserver</option><option value="device">Baixar neste dispositivo</option></select>
                    <button onclick="runAction('extract_audio')">Extrair áudio</button>
                  </div>


                </div>
              </section>

              <section class="card" style="margin-top:18px">
                <h2>Histórico inteligente</h2>
                <div class="row">
                  <input id="historySearch" placeholder="Buscar por título, plataforma ou link..." oninput="renderHistory()" />
                  <button onclick="deleteSelectedHistory()" class="danger-btn">Excluir selecionados</button>
                  <button onclick="deleteAllHistory()" class="danger-btn">Excluir todo histórico</button>
                </div>
                <div id="historyMessage" class="mini muted"></div>
                <div id="historyList" class="history-list"></div>
              </section>
            </main>
<script>
async function api(payload) {{
  const res = await fetch('/api/video', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(payload)}});
  return await res.json();
}}
async function settingsApi(payload) {{
  const res = await fetch('/api/settings', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(payload)}});
  return await res.json();
}}
async function historyApi(payload) {{
  const res = await fetch('/api/history', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(payload)}});
  return await res.json();
}}
let HISTORY=[];
async function loadHistory() {{
  const data = await historyApi({{action:'list'}});
  HISTORY = (data.data && data.data.items) || [];
  renderHistory();
}}
function renderHistory() {{
  const q = (document.getElementById('historySearch')?.value || '').toLowerCase();
  const items = HISTORY.filter(x => `${{x.title||''}} ${{x.platform||''}} ${{x.url||''}} ${{x.operation||''}}`.toLowerCase().includes(q));
  const box = document.getElementById('historyList');
  if (!box) return;
  box.innerHTML = items.map(x => `<div class="history-item"><div>${{x.thumbnail ? `<img class="thumb" src="${{esc(x.thumbnail)}}">` : '<div class="thumb"></div>'}}<label class="mini"><input type="checkbox" class="history-check" value="${{esc(x.id)}}"> Selecionar</label></div><div><h3>${{esc(x.title||'Sem título')}}</h3><p class="mini"><b>Origem:</b> ${{esc(x.platform||'-')}}<br><b>Data:</b> ${{esc(x.date||'-')}}<br><b>Operação:</b> ${{esc(x.operation_label||x.operation||'-')}}<br><b>Status:</b> ${{esc(x.status||'-')}}<br><b>Local:</b> ${{esc(x.location||'-')}}</p><div class="item-actions"><button onclick="copyLink('${{esc(x.url||'')}}')">Copiar Link</button>${{x.library_url ? `<a class="button secondary" href="${{esc(x.library_url)}}">Abrir na Biblioteca</a>` : ''}}</div></div></div>`).join('') || '<p class="muted">Nenhum histórico encontrado.</p>';
}}
async function copyLink(url) {{
  try {{ await navigator.clipboard.writeText(url); document.getElementById('historyMessage').textContent='Link copiado com sucesso.'; }}
  catch(e) {{ document.getElementById('historyMessage').textContent='Não consegui copiar automaticamente. Link: '+url; }}
}}
async function deleteSelectedHistory() {{
  const ids=[...document.querySelectorAll('.history-check:checked')].map(x=>x.value);
  if(!ids.length) {{ alert('Selecione pelo menos um item.'); return; }}
  if(!confirm('Tem certeza que deseja remover os itens selecionados?')) return;
  const data=await historyApi({{action:'delete', ids}}); document.getElementById('historyMessage').textContent=data.message; await loadHistory();
}}
async function deleteAllHistory() {{
  if(!confirm('Tem certeza que deseja remover todo o histórico? Os arquivos salvos não serão apagados.')) return;
  const data=await historyApi({{action:'delete', all:true}}); document.getElementById('historyMessage').textContent=data.message; await loadHistory();
}}
async function loadSettings() {{
  if (!document.getElementById('transcriptionProvider')) return;
  const data = await settingsApi({{action:'get'}});
  const cfg = data.data || {{}};
  document.getElementById('openaiBase').value = cfg.openai_base_url || 'https://api.openai.com/v1';
  document.getElementById('transcriptionProvider').value = cfg.transcription_provider || 'local';
  document.getElementById('localModel').value = cfg.local_whisper_model || 'base';
  const mode = (cfg.transcription_provider || 'local') === 'openai' ? 'OpenAI' : 'Local gratuito';
  const keyText = cfg.openai_configured ? ' • OpenAI: ' + cfg.openai_key_masked : '';
  document.getElementById('settingsStatus').textContent = 'Modo atual: ' + mode + ' • Modelo local: ' + (cfg.local_whisper_model || 'base') + keyText;
}}
async function saveSettings() {{
  if (!document.getElementById('transcriptionProvider')) return;
  const key = document.getElementById('openaiKey').value.trim();
  const base = document.getElementById('openaiBase').value.trim();
  const provider = document.getElementById('transcriptionProvider').value;
  const localModel = document.getElementById('localModel').value;
  const data = await settingsApi({{action:'save', openai_api_key:key, openai_base_url:base, transcription_provider:provider, local_whisper_model:localModel}});
  document.getElementById('settingsStatus').textContent = data.message;
  document.getElementById('openaiKey').value = '';
  await loadSettings();
}}
loadSettings();
loadHistory();
function currentUrl() {{ return document.getElementById('url').value.trim(); }}
function esc(value) {{ return String(value ?? '').replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch])); }}
async function analyzeVideo() {{
  const box = document.getElementById('result');
  const btn = event?.target;
  try {{
    if (!currentUrl()) {{ box.textContent='Cole um link primeiro.'; return; }}
    if (btn) btn.disabled = true;
    box.textContent='Analisando...';
    document.getElementById('info').innerHTML = '';
    const data = await api({{action:'analyze', url: currentUrl()}});
    box.textContent = data.message || 'Resposta recebida.';
    if (data.ok && data.data) {{
      const d=data.data;
      document.getElementById('info').innerHTML = `<div class="video-info">${{d.thumbnail ? `<img src="${{esc(d.thumbnail)}}">` : ''}}<div><h2>${{esc(d.title)}}</h2><p><b>Plataforma:</b> ${{esc(d.platform)}}<br><b>Duração:</b> ${{esc(d.duration_text)}}<br><b>Resoluções:</b> ${{esc((d.resolutions||[]).map(x=>x.label).join(', ') || 'não informado')}}</p></div></div>`;
    }} else if (data.data && data.data.details) {{
      box.textContent += '\\nDetalhes: ' + data.data.details;
    }}
  }} catch (err) {{
    box.textContent = 'Erro ao analisar. Verifique se o Nserver está online e tente novamente. Detalhes: ' + err;
  }} finally {{
    if (btn) btn.disabled = false;
  }}
}}
async function runAction(action) {{
  const box = document.getElementById('result');
  const btn = event?.target;
  try {{
    if (!currentUrl()) {{ box.textContent='Cole um link primeiro.'; return; }}
    if (btn) btn.disabled = true;
    box.textContent='Processando... isso pode levar alguns minutos.';
    const payload={{action, url:currentUrl()}};
    if(action==='download_video') {{ payload.quality=document.getElementById('videoQuality').value; payload.format=document.getElementById('videoFormat').value; payload.destination=document.getElementById('videoDestination').value; }}
    if(action==='extract_audio') {{ payload.quality=document.getElementById('audioQuality').value; payload.format=document.getElementById('audioFormat').value; payload.destination=document.getElementById('audioDestination').value; }}
    if(action==='transcribe') {{ payload.formats=[...document.querySelectorAll('.transcript-format:checked')].map(x=>x.value); }}
    if(action==='viral_clips') {{ payload.count=document.getElementById('clipCount').value; payload.max_seconds=document.getElementById('clipSeconds').value; }}
    const data=await api(payload);
    box.textContent = (data.message || 'Resposta recebida.') + (data.data ? '\\n' + JSON.stringify(data.data, null, 2) : '');
    if (data.data && data.data.download_url) {{
      const link = document.createElement('a');
      link.className = 'button secondary';
      link.href = data.data.download_url;
      link.textContent = 'Baixar arquivo neste dispositivo';
      link.download = data.data.download_filename || data.data.filename || '';
      box.appendChild(document.createElement('br'));
      box.appendChild(link);
    }}
    await loadHistory();
  }} catch (err) {{
    box.textContent = 'Erro ao processar. Detalhes: ' + err;
  }} finally {{
    if (btn) btn.disabled = false;
  }}
}}
</script>
            """, authenticated=True)
            self.send_html(body)
            return
        if path in {"/tool/transcription", "/tool/viral-clips"}:
            sess = self.require_login()
            if not sess: return
            is_transcription = path == "/tool/transcription"
            title = "Ferramenta 02 — Transcrição" if is_transcription else "Ferramenta 03 — Cortes Virais"
            description = "Gere transcrições em TXT, MD, DOCX ou PDF usando URL, Biblioteca ou upload." if is_transcription else "Gere cortes virais reais a partir de URL, Biblioteca ou upload, salvando os arquivos na Biblioteca."
            if is_transcription:
                actions_html = """
                  <div>
                    <h3>Transcrição</h3>
                    <p class="mini">Padrão: modo local gratuito. Escolha os formatos que quer gerar.</p>
                    <label>Formatos</label>
                    <label class="mini"><input type="checkbox" class="transcript-format" value="txt" checked> TXT</label>
                    <label class="mini"><input type="checkbox" class="transcript-format" value="md" checked> MD</label>
                    <label class="mini"><input type="checkbox" class="transcript-format" value="docx"> DOCX</label>
                    <label class="mini"><input type="checkbox" class="transcript-format" value="pdf"> PDF</label>
                    <button onclick="runAction('transcribe')">Gerar transcrição</button>
                    <details class="settings-panel" open>
                      <summary>Configurar transcrição</summary>
                      <p class="mini">Local é gratuito e roda no notebook. OpenAI é opcional.</p>
                      <label>Modo</label>
                      <select id="transcriptionProvider"><option value="local">Local gratuito</option><option value="openai">OpenAI / Whisper API</option></select>
                      <label>Modelo local</label>
                      <select id="localModel"><option>tiny</option><option selected>base</option><option>small</option><option>medium</option></select>
                      <label>OpenAI API Key (opcional)</label>
                      <input id="openaiKey" type="password" placeholder="sk-..." />
                      <label>Base URL</label>
                      <input id="openaiBase" placeholder="https://api.openai.com/v1" />
                      <button onclick="saveSettings()">Salvar configuração</button>
                      <div id="settingsStatus" class="mini muted">Carregando configuração...</div>
                    </details>
                  </div>
                """
            else:
                actions_html = """
                  <div>
                    <h3>Cortes virais</h3>
                    <p class="mini">Informe quantidade e duração. O Nserver renderiza cortes reais em arquivos finais prontos para revisar/publicar.</p>
                    <label>Quantidade</label><select id="clipCount"><option>1</option><option selected>3</option><option>5</option><option>10</option></select>
                    <label>Duração máx.</label><select id="clipSeconds"><option>30</option><option selected>60</option><option>90</option></select>
                    <label>Formato</label><select id="clipAspect"><option value="9:16" selected>Vertical 9:16</option><option value="16:9">Horizontal 16:9</option></select>
                    <button onclick="runAction('viral_clips')">Criar cortes virais</button>
                  </div>
                """
            body = html_page(title, f"""
            <main class="wrap">
              <section class="card">
                <h1>{title}</h1>
                <p>{description}</p>
                <h2>Etapa 1 — Selecionar mídia</h2>
                <label>Fonte</label>
                <div class="row">
                  <select id="sourceType" onchange="toggleSourceInputs()"><option value="url">Inserir URL</option><option value="library">Selecionar da Biblioteca</option><option value="upload">Upload do computador</option></select>
                  <input id="url" placeholder="https://youtube.com/..." />
                  <select id="libraryPath" style="display:none"></select>
                  <input id="uploadFile" type="file" style="display:none" />
                  <button onclick="prepareSource()">Preparar</button>
                </div>
                <p class="mini">Fluxo padrão do Nserver: URL, Biblioteca ou Upload. A mídia preparada pode ser usada por todas as ferramentas.</p>
                <div id="result" class="result muted">Aguardando link.</div>
                <div id="info"></div>
              </section>
              <section class="card" style="margin-top:18px">
                <h2>Etapa 2 — Processamento</h2>
                <div class="actions">{actions_html}</div>
              </section>
              <details class="card" style="margin-top:18px">
                <summary><strong>Histórico</strong> — abrir/fechar</summary>
                <div class="row">
                  <input id="historySearch" placeholder="Buscar por título, plataforma ou link..." oninput="renderHistory()" />
                  <button onclick="deleteSelectedHistory()" class="danger-btn">Excluir selecionados</button>
                  <button onclick="deleteAllHistory()" class="danger-btn">Excluir todo histórico</button>
                </div>
                <div id="historyMessage" class="mini muted"></div>
                <div id="historyList" class="history-list"></div>
              </details>
            </main>
<script>
async function api(payload) {{ const res = await fetch('/api/video', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(payload)}}); return await res.json(); }}
async function settingsApi(payload) {{ const res = await fetch('/api/settings', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(payload)}}); return await res.json(); }}
async function historyApi(payload) {{ const res = await fetch('/api/history', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(payload)}}); return await res.json(); }}
function esc(value) {{ return String(value ?? '').replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch])); }}
function currentUrl() {{ return document.getElementById('url').value.trim(); }}
function sourceType() {{ return document.getElementById('sourceType')?.value || 'url'; }}
function selectedLibrary() {{ return document.getElementById('libraryPath')?.value || ''; }}
async function mediaApi(payload) {{ const res = await fetch('/api/media', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(payload)}}); return await res.json(); }}
async function loadMediaOptions() {{ const data=await mediaApi({{action:'list', kind:'video,audio'}}); const items=(data.data&&data.data.items)||[]; const sel=document.getElementById('libraryPath'); if(sel) sel.innerHTML=items.map(x=>`<option value="${{esc(x.relative)}}">${{esc(x.name)}} — ${{esc(x.kind)}}</option>`).join(''); }}
function toggleSourceInputs() {{ const t=sourceType(); document.getElementById('url').style.display=t==='url'?'block':'none'; document.getElementById('libraryPath').style.display=t==='library'?'block':'none'; document.getElementById('uploadFile').style.display=t==='upload'?'block':'none'; }}
async function uploadSelectedFile() {{ const input=document.getElementById('uploadFile'); if(!input || !input.files.length) throw new Error('Escolha um arquivo para upload.'); const fd=new FormData(); fd.append('file', input.files[0]); const res=await fetch('/api/media', {{method:'POST', body:fd}}); const data=await res.json(); if(!data.ok) throw new Error(data.message||'Falha no upload.'); await loadMediaOptions(); return data.data.relative; }}
async function prepareSource() {{ const box=document.getElementById('result'); try {{ if(sourceType()==='url') return await analyzeVideo(); if(sourceType()==='upload') {{ const rel=await uploadSelectedFile(); document.getElementById('libraryPath').value=rel; document.getElementById('sourceType').value='library'; toggleSourceInputs(); box.textContent='Upload salvo e selecionado na Biblioteca: '+rel; return; }} box.textContent=selectedLibrary()?'Mídia selecionada: '+selectedLibrary():'Escolha um arquivo da Biblioteca.'; }} catch(err) {{ box.textContent='Erro ao preparar mídia: '+err; }} }}
function buildSourcePayload(payload) {{ const t=sourceType(); payload.source_type=t; if(t==='library') payload.library_path=selectedLibrary(); if(t==='url') payload.url=currentUrl(); return payload; }}
let HISTORY=[];
async function loadHistory() {{ const data = await historyApi({{action:'list'}}); HISTORY=(data.data&&data.data.items)||[]; renderHistory(); }}
function renderHistory() {{ const q=(document.getElementById('historySearch')?.value||'').toLowerCase(); const items=HISTORY.filter(x=>`${{x.title||''}} ${{x.platform||''}} ${{x.url||''}} ${{x.operation||''}}`.toLowerCase().includes(q)); const box=document.getElementById('historyList'); if(!box)return; box.innerHTML=items.map(x=>`<div class="history-item"><div>${{x.thumbnail?`<img class="thumb" src="${{esc(x.thumbnail)}}">`:'<div class="thumb"></div>'}}<label class="mini"><input type="checkbox" class="history-check" value="${{esc(x.id)}}"> Selecionar</label></div><div><h3>${{esc(x.title||'Sem título')}}</h3><p class="mini"><b>Origem:</b> ${{esc(x.platform||'-')}}<br><b>Data:</b> ${{esc(x.date||'-')}}<br><b>Operação:</b> ${{esc(x.operation_label||x.operation||'-')}}<br><b>Status:</b> ${{esc(x.status||'-')}}<br><b>Local:</b> ${{esc(x.location||'-')}}</p><div class="item-actions"><button onclick="copyLink('${{esc(x.url||'')}}')">Copiar Link</button>${{x.library_url?`<a class="button secondary" href="${{esc(x.library_url)}}">Abrir na Biblioteca</a>`:''}}</div></div></div>`).join('') || '<p class="muted">Nenhum histórico encontrado.</p>'; }}
async function copyLink(url) {{ try {{ await navigator.clipboard.writeText(url); document.getElementById('historyMessage').textContent='Link copiado com sucesso.'; }} catch(e) {{ document.getElementById('historyMessage').textContent='Link: '+url; }} }}
async function deleteSelectedHistory() {{ const ids=[...document.querySelectorAll('.history-check:checked')].map(x=>x.value); if(!ids.length){{alert('Selecione pelo menos um item.');return;}} if(!confirm('Tem certeza que deseja remover os itens selecionados?'))return; const data=await historyApi({{action:'delete',ids}}); document.getElementById('historyMessage').textContent=data.message; await loadHistory(); }}
async function deleteAllHistory() {{ if(!confirm('Tem certeza que deseja remover todo o histórico? Os arquivos salvos não serão apagados.'))return; const data=await historyApi({{action:'delete',all:true}}); document.getElementById('historyMessage').textContent=data.message; await loadHistory(); }}
async function loadSettings() {{ if(!document.getElementById('transcriptionProvider'))return; const data=await settingsApi({{action:'get'}}); const cfg=data.data||{{}}; document.getElementById('openaiBase').value=cfg.openai_base_url||'https://api.openai.com/v1'; document.getElementById('transcriptionProvider').value=cfg.transcription_provider||'local'; document.getElementById('localModel').value=cfg.local_whisper_model||'base'; const mode=(cfg.transcription_provider||'local')==='openai'?'OpenAI':'Local gratuito'; const keyText=cfg.openai_configured?' • OpenAI: '+cfg.openai_key_masked:''; document.getElementById('settingsStatus').textContent='Modo atual: '+mode+' • Modelo local: '+(cfg.local_whisper_model||'base')+keyText; }}
async function saveSettings() {{ if(!document.getElementById('transcriptionProvider'))return; const key=document.getElementById('openaiKey').value.trim(); const base=document.getElementById('openaiBase').value.trim(); const provider=document.getElementById('transcriptionProvider').value; const localModel=document.getElementById('localModel').value; const data=await settingsApi({{action:'save',openai_api_key:key,openai_base_url:base,transcription_provider:provider,local_whisper_model:localModel}}); document.getElementById('settingsStatus').textContent=data.message; document.getElementById('openaiKey').value=''; await loadSettings(); }}
async function analyzeVideo() {{ const box=document.getElementById('result'); const btn=event?.target; try {{ if(sourceType()!=='url'){{box.textContent='Análise online é usada apenas para URL. Para Biblioteca/Upload, clique em Processar.';return;}} if(!currentUrl()){{box.textContent='Cole um link primeiro.';return;}} if(btn)btn.disabled=true; box.textContent='Analisando...'; document.getElementById('info').innerHTML=''; const data=await api({{action:'analyze',url:currentUrl()}}); box.textContent=data.message||'Resposta recebida.'; if(data.ok&&data.data){{const d=data.data; document.getElementById('info').innerHTML=`<div class="video-info">${{d.thumbnail?`<img src="${{esc(d.thumbnail)}}">`:''}}<div><h2>${{esc(d.title)}}</h2><p><b>Plataforma:</b> ${{esc(d.platform)}}<br><b>Duração:</b> ${{esc(d.duration_text)}}<br><b>Resoluções:</b> ${{esc((d.resolutions||[]).map(x=>x.label).join(', ')||'não informado')}}</p></div></div>`;}} else if(data.data&&data.data.details){{box.textContent+='\\nDetalhes: '+data.data.details;}} }} catch(err) {{ box.textContent='Erro ao analisar: '+err; }} finally {{ if(btn)btn.disabled=false; }} }}
async function runAction(action) {{ const box=document.getElementById('result'); const btn=event?.target; try {{ if(sourceType()==='upload') await uploadSelectedFile().then(rel=>{{document.getElementById('sourceType').value='library'; toggleSourceInputs(); document.getElementById('libraryPath').value=rel;}}); if(sourceType()==='url'&&!currentUrl()){{box.textContent='Cole um link primeiro.';return;}} if(sourceType()==='library'&&!selectedLibrary()){{box.textContent='Escolha uma mídia da Biblioteca.';return;}} if(btn)btn.disabled=true; box.textContent='Processando... isso pode levar alguns minutos.'; const payload=buildSourcePayload({{action}}); if(action==='transcribe'){{payload.formats=[...document.querySelectorAll('.transcript-format:checked')].map(x=>x.value);}} if(action==='viral_clips'){{payload.count=document.getElementById('clipCount').value; payload.max_seconds=document.getElementById('clipSeconds').value; payload.aspect=document.getElementById('clipAspect').value; payload.render=true;}} const data=await api(payload); box.textContent=(data.message||'Resposta recebida.')+(data.data?'\\n'+JSON.stringify(data.data,null,2):''); await loadHistory(); await loadMediaOptions(); }} catch(err) {{ box.textContent='Erro ao processar. Detalhes: '+err; }} finally {{ if(btn)btn.disabled=false; }} }}
loadSettings(); loadHistory(); loadMediaOptions(); toggleSourceInputs();
</script>
            """, authenticated=True)
            self.send_html(body)
            return
        if path == "/tool/course-ingest":
            sess = self.require_login()
            if not sess: return
            body = html_page("Curso → TheronCore", """
            <main class="wrap">
              <section class="card">
                <h1>Ferramenta 05 — Curso → TheronCore</h1>
                <p>Mapeia um curso autenticado, organiza módulos/aulas e gera material para o subagente TheronCore.</p>
                <p class="mini">Esta ferramenta é isolada: não altera Downloader, Biblioteca nem Editor de Vídeo.</p>
              </section>

              <section class="card" style="margin-top:18px">
                <h2>Etapa 1 — Acesso e mapeamento</h2>
                <form method="post" action="/tool/course-ingest-map">
                  <label>Link do curso</label>
                  <input id="courseUrl" name="url" value="https://treinamentos.coreeducacao.com/area/vitrine/home" />
                  <div class="row">
                    <div><label>Login</label><input id="courseEmail" name="email" placeholder="email do curso" /></div>
                    <div><label>Senha</label><input id="coursePassword" name="password" type="password" placeholder="senha" /></div>
                  </div>
                  <button type="button" onclick="mapCourse()">Mapear curso</button>
                  <button type="submit" class="secondary">Mapear modo compatibilidade</button>
                </form>
                <p class="mini">Use “Mapear curso” primeiro. Se ficar parado em Aguardando, use “Mapear modo compatibilidade”. A senha é usada somente nesta execução; o Nserver não grava a senha em arquivo.</p>
                <div id="mapResult" class="result muted">Aguardando mapeamento. Se clicar e nada mudar, use o botão “Mapear modo compatibilidade”.</div>
              </section>

              <section class="card" style="margin-top:18px">
                <h2>Etapa 2 — Escolher o que processar</h2>
                <div class="row">
                  <div><label>Escopo</label><select id="scope" onchange="toggleScope()"><option value="all">Curso todo</option><option value="module">Um módulo</option><option value="lesson">Uma aula</option></select></div>
                  <div><label>Módulo</label><select id="moduleId"></select></div>
                  <div><label>Aula</label><select id="lessonId"></select></div>
                </div>
                <div class="row">
                  <div><label>Saída</label><select id="output"><option value="llm_package">Pacote Transcrição Otimizada LLM</option><option value="transcript">Somente transcrição</option><option value="audio">Somente áudio</option><option value="video">Somente vídeo</option></select></div>
                </div>
                <button onclick="processCourse()">Processar seleção</button>
                <div id="processResult" class="result muted">Mapeie o curso antes de processar.</div>
              </section>

              <section class="card" style="margin-top:18px">
                <h2>Mapa do curso</h2>
                <div id="courseMap" class="history-list"></div>
              </section>
            </main>
<script>
let COURSE=null;
function esc(v){return String(v??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));}
async function api(payload){const res=await fetch('/api/course',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); const text=await res.text(); let data={}; try{data=JSON.parse(text);}catch(e){throw new Error('Resposta inválida do servidor: '+text.slice(0,300));} if(!res.ok) throw new Error(data.message||('HTTP '+res.status)); return data;}
function creds(){return {email:document.getElementById('courseEmail').value.trim(),password:document.getElementById('coursePassword').value,url:document.getElementById('courseUrl').value.trim()};}
async function loadLatestCourse(){try{const data=await api({action:'status',course_id:'latest'}); if(data.ok&&data.data&&data.data.modules){COURSE=data.data; document.getElementById('mapResult').textContent='Último mapa carregado: '+COURSE.title+' — '+COURSE.total_modules+' módulo(s), '+COURSE.total_lessons+' aula(s).'; renderCourse();}}catch(e){}}
function renderCourse(){
  if(!COURSE){return;}
  const modules=COURSE.modules||[];
  document.getElementById('moduleId').innerHTML=modules.map(m=>`<option value="${esc(m.id)}">${String(m.order||'').padStart(2,'0')} — ${esc(m.title)} (${(m.lessons||[]).length})</option>`).join('');
  const allLessons=[]; modules.forEach(m=>(m.lessons||[]).forEach(l=>allLessons.push({module:m,title:l.title,id:l.id})));
  document.getElementById('lessonId').innerHTML=allLessons.map(x=>`<option value="${esc(x.id)}">${esc(x.module.title)} / ${esc(x.title)}</option>`).join('');
  document.getElementById('courseMap').innerHTML=modules.map(m=>`<div class="media-item"><div><b>${String(m.order||'').padStart(2,'0')}</b></div><div><h3>${esc(m.title)}</h3><p class="mini">${esc(m.url||'')}</p><ol>${(m.lessons||[]).map(l=>`<li>${esc(l.title)} <span class="mini">${esc(l.id)}</span></li>`).join('')}</ol></div></div>`).join('') || '<p class="muted">Nenhum módulo encontrado.</p>';
  toggleScope();
}
function toggleScope(){const s=document.getElementById('scope').value; document.getElementById('moduleId').disabled=s!=='module'; document.getElementById('lessonId').disabled=s!=='lesson';}
async function mapCourse(){
  const box=document.getElementById('mapResult');
  try{
    box.textContent='Iniciando mapeamento em segundo plano...';
    const start=await api({action:'map_start',...creds()});
    if(!start.ok) throw new Error(start.message);
    const jobId=start.data.job_id;
    for(let i=0;i<90;i++){
      await new Promise(r=>setTimeout(r,2000));
      const st=await api({action:'map_job',job_id:jobId});
      const job=st.data||{};
      box.textContent=(st.message||'Mapeando...')+'\nStatus: '+(job.status||'-')+'\nTempo: '+((i+1)*2)+'s';
      if(job.status==='done'){
        COURSE=job.data;
        box.textContent=st.message+'\nID: '+COURSE.id+'\nMódulos: '+COURSE.total_modules+'\nAulas: '+COURSE.total_lessons;
        renderCourse();
        return;
      }
      if(job.status==='error') throw new Error(st.message||'Falha no mapeamento.');
    }
    box.textContent='O mapeamento ainda está rodando. Pode recarregar a página; se terminar, o último mapa será carregado automaticamente.';
  }catch(err){box.textContent='Erro ao mapear: '+err;}
}
async function processCourse(){
  const box=document.getElementById('processResult');
  if(!COURSE){box.textContent='Mapeie o curso primeiro.';return;}
  try{box.textContent='Processando... pode demorar bastante dependendo da quantidade de aulas.'; const payload={action:'process',course_id:COURSE.id,scope:scope.value,module_id:moduleId.value,lesson_id:lessonId.value,output:output.value,...creds()}; const data=await api(payload); if(!data.ok) throw new Error(data.message); const link=(data.data&&data.data.download_url)?'\n\nBaixar arquivo: '+location.origin+data.data.download_url:''; box.textContent=data.message+link+'\n\n'+JSON.stringify(data.data,null,2);}
  catch(err){box.textContent='Erro ao processar: '+err;}
}
toggleScope();
loadLatestCourse();
</script>
            """, authenticated=True)
            self.send_html(body)
            return
        if path == "/tool/video-editor":
            sess = self.require_login()
            if not sess: return
            body = html_page("CutFlow Studio", """
            <main class="wrap">
              <section class="card">
                <h1>Ferramenta 04 — CutFlow Studio</h1>
                <p>Editor de vídeo no estilo Nserver com o fluxo do Kael: cortar silêncios, ajustar timeline, exportar final e aplicar legendas.</p>
                <div class="meta">
                  <button id="tabCut" type="button" onclick="showTab('cut')">1. Cortar vídeo</button>
                  <button id="tabCaption" type="button" class="button secondary" onclick="showTab('caption')">2. Colocar legenda</button>
                </div>
              </section>

              <section id="cutPanel" class="card" style="margin-top:18px">
                <h2>Etapa 1 — Selecionar vídeo</h2>
                <label>Origem do vídeo</label>
                <select id="sourceType" onchange="toggleSource()">
                  <option value="library">Selecionar da Biblioteca do Nserver</option>
                  <option value="upload">Enviar arquivo do dispositivo</option>
                  <option value="url">Inserir URL / Drive / YouTube</option>
                </select>
                <div id="sourceLibraryBox" style="margin-top:12px">
                  <label>Vídeo da Biblioteca</label>
                  <select id="source"></select>
                </div>
                <div id="sourceUploadBox" style="margin-top:12px;display:none">
                  <label>Arquivo do dispositivo</label>
                  <input id="uploadFile" type="file" accept="video/*" onchange="document.getElementById('uploadName').textContent=this.files.length?'Selecionado: '+this.files[0].name:'Nenhum arquivo selecionado'" />
                  <p id="uploadName" class="mini">Nenhum arquivo selecionado.</p>
                </div>
                <div id="sourceUrlBox" style="margin-top:12px;display:none">
                  <label>Link do vídeo</label>
                  <input id="sourceUrl" placeholder="Cole o link do vídeo" />
                </div>
                <div class="row" style="margin-top:12px">
                  <button type="button" onclick="prepareSource()">Carregar vídeo no editor</button>
                </div>
                <p class="mini">Escolha uma origem acima. Só aparece o campo da opção selecionada.</p>
                <div id="playerStatus" class="result muted" style="display:none">Preparando preview...</div>
                <video id="player" class="player" controls controlsList="nodownload" disablePictureInPicture oncontextmenu="return false"></video>
                <div id="selectedCutPanel" class="result muted">Selecione um trecho na timeline para ajustar enquanto assiste ao vídeo.</div>
                <div id="result" class="result muted">Carregando vídeos da Biblioteca...</div>
              </section>

              <section id="cutControls" class="card" style="margin-top:18px">
                <h2>Etapa 2 — Analisar / Cortar</h2>
                <p class="mini">Parâmetros principais do corte de silêncio: threshold, duração mínima e padding. Os presets seguem a especificação enviada: estilo CapCut/VAD com FFmpeg silencedetect.</p>
                <div class="row">
                  <button onclick="applyPreset('kael')">Kael original</button>
                  <button class="button secondary" onclick="applyPreset('dry')">Reels seco</button>
                  <button class="button secondary" onclick="applyPreset('balanced')">CapCut / equilibrado</button>
                  <button class="button secondary" onclick="applyPreset('safe')">Podcast seguro</button>
                  <button class="button secondary" onclick="applyPreset('noise')">Ambiente barulhento</button>
                </div>
                <div class="row">
                  <div><label>Threshold</label><input id="noise" value="-32dB"></div>
                  <div><label>Duração mínima</label><input id="minSilence" value="0.25"></div>
                  <div><label>Padding antes</label><input id="padBefore" value="0.04"></div>
                  <div><label>Padding depois</label><input id="padAfter" value="0.04"></div>
                  <div><label>Ignorar corte menor</label><input id="ignoreCut" value="0.12"></div>
                  <div><label>Keep mínimo</label><input id="minKeep" value="0.18"></div>
                  <div><label>Unir cortes até</label><input id="joinGap" value="0.00"></div>
                </div>
                <div class="row">
                  <button onclick="analyzeCuts()">Analisar / Cortar</button>
                  <button class="button secondary" onclick="previewKeeps()">Preview rápido</button>
                  <button class="button secondary" onclick="pausePreview()">Pausar</button>
                  <button class="button secondary" onclick="renderEditedPreview()">Renderizar preview editado</button>
                  <button onclick="renderFinal()">Exportar final</button>
                </div>
              </section>

              <section id="timelineCard" class="card" style="margin-top:18px">
                <h2>Timeline manual</h2>
                <p class="mini">Verde = manter. Vermelho = cortar. Clique em um bloco e ajuste início/fim nas barras, estilo Kael. O cuts.json continua sendo a fonte da verdade.</p>
                <div id="timeline" style="display:flex;height:42px;border:1px solid #263244;border-radius:12px;overflow:hidden;background:#111827"></div>
                <div id="cutItems" class="history-list"></div>
                <div class="row">
                  <button class="button secondary" onclick="exportCuts()">Exportar cuts.json</button>
                  <label class="button secondary">Abrir cuts.json<input id="cutsFile" type="file" accept="application/json" style="display:none" onchange="importCuts(event)"></label>
                </div>
                <label>cuts.json — fonte da verdade (preenche após Analisar / Cortar)</label>
                <textarea id="cutsJson" style="width:100%;min-height:240px;border-radius:14px;background:#080a10;color:#e5e7eb;border:1px solid #334155;padding:12px;font-family:monospace">{ "schema": "openclaw.cuts.v1", "source_duration": 0, "items": [] }</textarea>
              </section>

              <section id="captionPanel" class="card" style="margin-top:18px">
                <h2>Etapa 3 — Legendas</h2>
                <p class="mini">Mantém “usar vídeo atual”, mas também permite selecionar outro vídeo pelo fluxo padrão acima.</p>
                <div class="row">
                  <button type="button" onclick="useCurrentVideoForCaption()">Usar vídeo atual</button>
                  <select id="captionSourceType" onchange="toggleCaptionSource()"><option value="library">Biblioteca</option><option value="upload">Upload</option><option value="url">URL / Drive / YouTube</option></select>
                  <select id="captionSource"></select>
                  <input id="captionUpload" type="file" accept="video/*" style="display:none" />
                  <input id="captionUrl" placeholder="Cole o link do vídeo para legendar" style="display:none" />
                  <button type="button" onclick="prepareCaptionSource()">Preparar vídeo da legenda</button>
                </div>
                <video id="captionPlayer" class="player" controls controlsList="nodownload" disablePictureInPicture oncontextmenu="return false"></video>
                <label>Texto manual da legenda</label>
                <textarea id="captionText" style="width:100%;min-height:120px;border-radius:14px;background:#080a10;color:#e5e7eb;border:1px solid #334155;padding:12px">SUA LEGENDA AQUI</textarea>
                <h3>Parâmetros</h3>
                <div class="row">
                  <div><label>Preset</label><select id="captionPreset"><option value="viral">Viral</option><option value="clean">Clean</option><option value="yellow">Yellow</option><option value="box">Box</option></select></div>
                  <div><label>Palavras</label><input id="maxWords" value="5"></div>
                  <div><label>Caracteres</label><input id="maxChars" value="34"></div>
                  <div><label>Linhas</label><input id="maxLines" value="2"></div>
                  <div><label>Fonte px</label><input id="fontSize" value="54"></div>
                  <div><label>Margem V</label><input id="marginV" value="110"></div>
                </div>
                <div class="row">
                  <div><label>Cor</label><input id="captionColor" value="#ffffff"></div>
                  <div><label>Borda</label><input id="outlineColor" value="#000000"></div>
                  <div><label>Espessura</label><input id="outline" value="4"></div>
                  <div><label>Posição</label><select id="alignment"><option value="2">Baixo</option><option value="5">Centro</option><option value="8">Topo</option></select></div>
                </div>
                <button onclick="renderCaption()">Exportar vídeo com legenda</button>
                <div id="captionResult" class="result muted">Aguardando configuração.</div>
              </section>
            </main>
<script>
let SOURCES=[]; let CURRENT=null; let CUTS={schema:'openclaw.cuts.v1',source_duration:0,items:[]}; let SELECTED_ID=null; let PREVIEW_TIMER=null; let LAST_FINAL=null;
function esc(v){return String(v??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));}
async function jsonApi(path,payload,timeoutMs=20000){
  const ctrl=new AbortController();
  const timer=setTimeout(()=>ctrl.abort(),timeoutMs);
  try{
    const res=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),credentials:'same-origin',signal:ctrl.signal});
    const text=await res.text();
    let data=null;
    try{data=text?JSON.parse(text):{};}catch(e){
      if(res.redirected || text.includes('<form') || text.includes('Nserver')) throw new Error('sessão expirada ou resposta inválida. Saia, entre de novo e tente novamente.');
      throw new Error('resposta inválida do servidor: '+text.slice(0,180));
    }
    if(!res.ok || data.ok===false) throw new Error((data&&data.message)||('HTTP '+res.status));
    return data;
  }catch(err){
    if(err&&err.name==='AbortError') throw new Error('o servidor demorou demais para responder. Reinicie o Nserver e tente de novo.');
    throw err;
  }finally{clearTimeout(timer);}
}
async function editorApi(payload){return await jsonApi('/api/editor',payload);}
async function videoApi(payload){return await jsonApi('/api/video',payload,60000);}
function showTab(tab){document.getElementById('cutPanel').style.display=tab==='cut'?'block':'none';document.getElementById('cutControls').style.display=tab==='cut'?'block':'none';document.getElementById('timelineCard').style.display=tab==='cut'?'block':'none';document.getElementById('captionPanel').style.display=tab==='caption'?'block':'none';}
function toggleSource(){const t=document.getElementById('sourceType').value; document.getElementById('sourceLibraryBox').style.display=t==='library'?'block':'none'; document.getElementById('sourceUploadBox').style.display=t==='upload'?'block':'none'; document.getElementById('sourceUrlBox').style.display=t==='url'?'block':'none';}
function toggleCaptionSource(){const t=document.getElementById('captionSourceType').value; document.getElementById('captionSource').style.display=t==='library'?'inline-block':'none'; document.getElementById('captionUpload').style.display=t==='upload'?'inline-block':'none'; document.getElementById('captionUrl').style.display=t==='url'?'inline-block':'none';}
function setPlayerLoading(msg){const st=document.getElementById('playerStatus'); const v=document.getElementById('player'); if(st){st.style.display='block'; st.textContent=msg||'Preparando preview...';} if(v){v.pause(); v.removeAttribute('src'); v.load(); v.style.opacity='0.35';}}
function setPlayerReady(url,msg){const st=document.getElementById('playerStatus'); const v=document.getElementById('player'); if(!v)return; if(!url){if(st){st.style.display='block'; st.textContent='Não recebi URL de preview. Tente carregar novamente ou escolha outro vídeo.';} return;} const ready=()=>{if(st){st.style.display='none';} v.style.opacity='1';}; v.onloadedmetadata=ready; v.oncanplay=ready; v.onplaying=ready; v.onerror=()=>{if(st){st.style.display='block'; st.innerHTML='Não consegui tocar este preview no navegador. <a href="'+url+'" target="_blank">Abrir vídeo em nova aba</a> ou tente renderizar um preview editado.';} v.style.opacity='0.75';}; if(st){st.style.display='block'; st.textContent=msg||'Carregando vídeo no player...';} v.style.opacity='0.55'; v.src=url; v.load(); setTimeout(()=>{if(st&&st.style.display!=='none'&&v.readyState>=1)ready();},1500);}
async function loadSources(){
  const box=document.getElementById('result');
  try{
    if(box) box.textContent='Carregando vídeos da Biblioteca...';
    const data=await editorApi({action:'list_sources'});
    SOURCES=(data.data&&data.data.items)||[];
    const opts=SOURCES.map(x=>`<option value="${esc(x.relative)}">${esc(x.name)} — ${esc(x.modified)}</option>`).join('');
    document.getElementById('source').innerHTML=opts;
    document.getElementById('captionSource').innerHTML=opts;
    if(box) box.textContent=SOURCES.length?'Escolha um vídeo, envie upload ou cole uma URL.':'Nenhum vídeo encontrado. Use upload ou URL.';
  }catch(err){
    SOURCES=[];
    document.getElementById('source').innerHTML='';
    document.getElementById('captionSource').innerHTML='';
    if(box) box.textContent='Não consegui carregar a Biblioteca: '+err+'\\nVocê ainda pode usar “Enviar arquivo” ou “Inserir URL”. Se continuar, reinicie o Nserver.';
  }
}
async function uploadFile(){const input=document.getElementById('uploadFile'); if(!input.files.length) throw new Error('Escolha um vídeo.'); const fd=new FormData(); fd.append('file',input.files[0]); const res=await fetch('/api/media',{method:'POST',body:fd}); const data=await res.json(); if(!data.ok) throw new Error(data.message); await loadSources(); return data.data.relative;}
async function prepareSource(){const box=document.getElementById('result'); try{let rel=''; let p=null; const upload=document.getElementById('uploadFile'); const url=(document.getElementById('sourceUrl').value||'').trim(); let t=document.getElementById('sourceType').value; setPlayerLoading('Preparando vídeo... se for TikTok/HEVC, o Nserver vai criar um preview compatível.'); if(t==='upload'){box.textContent='Enviando upload...'; rel=await uploadFile(); document.getElementById('source').value=rel;} else if(t==='url'){if(!url) throw new Error('Cole o link do vídeo.'); box.textContent='Carregando link para edição temporária... não será salvo na Biblioteca.'; p=await editorApi({action:'prepare_url',url,quality:'720'}); if(!p.ok) throw new Error(p.message); rel=p.data.source;} else rel=document.getElementById('source').value; if(!rel) throw new Error('Escolha um vídeo da Biblioteca, selecione um arquivo ou cole um link.'); box.textContent='Analisando vídeo e preparando preview...'; if(!p){p=await editorApi({action:'probe',source:rel});} if(!p.ok) throw new Error(p.message); CURRENT=p.data; setPlayerReady(p.data.play_url||'', p.data.preview_note || 'Carregando vídeo no player...'); document.getElementById('captionSource').value=rel; document.getElementById('captionPlayer').src=p.data.play_url||''; box.textContent='Vídeo selecionado: '+JSON.stringify(p.data,null,2);}catch(err){const st=document.getElementById('playerStatus'); if(st){st.style.display='none';} box.textContent='Erro: '+err;}}
async function uploadCaptionFile(){const input=document.getElementById('captionUpload'); if(!input.files.length) throw new Error('Escolha um vídeo para legenda.'); const fd=new FormData(); fd.append('file',input.files[0]); const res=await fetch('/api/media',{method:'POST',body:fd}); const data=await res.json(); if(!data.ok) throw new Error(data.message); await loadSources(); return data.data.relative;}
async function prepareCaptionSource(){const box=document.getElementById('captionResult'); try{let rel=''; const upload=document.getElementById('captionUpload'); const url=(document.getElementById('captionUrl').value||'').trim(); let t=document.getElementById('captionSourceType').value; if(t==='upload'){box.textContent='Enviando upload...'; rel=await uploadCaptionFile(); document.getElementById('captionSource').value=rel;} else if(t==='url'){box.textContent='Baixando URL para a Biblioteca...'; const data=await videoApi({action:'download_video',url,destination:'server',quality:'720',format:'mp4'}); if(!data.ok) throw new Error(data.message); await loadSources(); rel=(data.data&&data.data.file)?data.data.file.split('/midias/').pop():''; if(rel) document.getElementById('captionSource').value=rel;} else rel=document.getElementById('captionSource').value; if(!rel) throw new Error('Escolha um vídeo, envie upload, cole URL ou use vídeo atual.'); const p=await editorApi({action:'probe',source:rel}); if(!p.ok) throw new Error(p.message); document.getElementById('captionPlayer').src=p.data.play_url||''; box.textContent='Vídeo da legenda pronto: '+p.data.filename;}catch(err){box.textContent='Erro ao preparar legenda: '+err;}}
function applyPreset(p){const vals={kael:['-32dB','0.25','0.04','0.04','0.12','0.18','0.00'],dry:['-35dB','0.35','0.08','0.08','0.12','0.18','0.05'],balanced:['-40dB','0.75','0.30','0.30','0.20','0.25','0.15'],safe:['-45dB','1.00','0.40','0.40','0.25','0.35','0.20'],noise:['-25dB','1.00','0.45','0.45','0.30','0.40','0.20']}[p]; ['noise','minSilence','padBefore','padAfter','ignoreCut','minKeep','joinGap'].forEach((id,i)=>document.getElementById(id).value=vals[i]);}
function sourceRel(){return (CURRENT&&CURRENT.source)||document.getElementById('source').value;}
async function analyzeCuts(){const box=document.getElementById('result'); try{box.textContent='Analisando silêncios com FFmpeg/VAD...'; const data=await editorApi({action:'analyze_cuts',source:sourceRel(),noise:noise.value,min_silence:minSilence.value,pad_before:padBefore.value,pad_after:padAfter.value,ignore_cut_under:ignoreCut.value,min_keep:minKeep.value,join_gap:joinGap.value}); if(!data.ok) throw new Error(data.message); CUTS=data.data.cuts; renderCuts(); const s=(data.data&&data.data.summary)||CUTS.summary||{}; box.textContent=(data.message||'Silêncios analisados.')+(s.cut_count!==undefined?'\\nResumo: '+s.cut_count+' corte(s), '+s.removed_seconds+'s removidos ('+s.removed_percent+'%). Trechos verdes serão mantidos.':'');}catch(err){box.textContent='Erro ao analisar: '+err;}}
function getCuts(){try{return JSON.parse(document.getElementById('cutsJson').value);}catch(e){alert('cuts.json inválido'); throw e;}}
function syncCuts(){document.getElementById('cutsJson').value=JSON.stringify(CUTS,null,2);}
function selectedItem(){return (CUTS.items||[]).find(x=>x.id===SELECTED_ID)||null;}
function selectCut(id){CUTS=getCuts(); SELECTED_ID=id; renderCuts(); const it=selectedItem(); if(it) seekToCut(id,false);}
function setAction(id,action){CUTS=getCuts(); const it=CUTS.items.find(x=>x.id===id); if(it){it.action=action; it.reason=action==='keep'?'manual: liberar trecho':'manual: cortar trecho'; SELECTED_ID=id; renderCuts();}}
function updateCutTime(id,field,value){CUTS=getCuts(); const it=CUTS.items.find(x=>x.id===id); if(!it)return; const dur=Number(CUTS.source_duration||0); let v=Math.max(0,Math.min(dur,Number(value)||0)); if(field==='start') it.start=Math.min(v,Number(it.end||0)-0.05); else it.end=Math.max(v,Number(it.start||0)+0.05); it.start=Number(it.start.toFixed(3)); it.end=Number(it.end.toFixed(3)); if(!(it.reason||'').includes('manual ajuste')) it.reason=(it.reason||'')+' | manual ajuste'; SELECTED_ID=id; renderCuts(false); seekToCut(id,false);}
function nudgeCut(id,field,delta){const it=(CUTS.items||[]).find(x=>x.id===id); if(!it)return; updateCutTime(id,field,Number(it[field]||0)+delta);}
function stopSegmentWatcher(){if(PREVIEW_TIMER){clearInterval(PREVIEW_TIMER); PREVIEW_TIMER=null;}}
function seekToCut(id,play=false){const it=(CUTS.items||[]).find(x=>x.id===id); const v=document.getElementById('player'); if(!it||!v)return; stopSegmentWatcher(); v.currentTime=Math.max(0,Number(it.start||0)); if(play){const end=Number(it.end||0); const box=document.getElementById('result'); if(box) box.textContent='Tocando trecho selecionado: '+it.start+'s → '+it.end+'s'; const playPromise=v.play(); if(playPromise&&playPromise.catch) playPromise.catch(()=>{}); PREVIEW_TIMER=setInterval(()=>{if(v.currentTime>=end){v.pause(); stopSegmentWatcher();}},80);}}
function playSelectedCut(){if(SELECTED_ID) seekToCut(SELECTED_ID,true);}
function renderSelectedPanel(){const box=document.getElementById('selectedCutPanel'); const it=selectedItem(); const dur=Number(CUTS.source_duration||1); if(!box)return; if(!it){box.innerHTML='Selecione um trecho na timeline para ajustar enquanto assiste ao vídeo.';return;} const color=it.action==='keep'?'#22c55e':'#ef4444'; const label=it.action==='keep'?'MANTER':'CORTAR'; box.innerHTML=`<h3 style="margin-top:0">Ajuste do trecho — <span style="color:${color}">${label}</span> <span class="mini">${esc(it.id)}</span></h3><p class="mini">Este painel fica perto do vídeo para você assistir e ajustar. Vermelho será removido; verde será mantido.</p><div class="row"><button onclick="playSelectedCut()">Ouvir/ver trecho</button><button onclick="setAction('${esc(it.id)}','keep')">Manter</button><button class="danger-btn" onclick="setAction('${esc(it.id)}','cut')">Cortar</button></div><label>Início: <b>${Number(it.start).toFixed(3)}s</b></label><input type="range" min="0" max="${dur}" step="0.01" value="${it.start}" oninput="updateCutTime('${esc(it.id)}','start',this.value)"><div class="row"><button class="button secondary" onclick="nudgeCut('${esc(it.id)}','start',-1)">-1s</button><button class="button secondary" onclick="nudgeCut('${esc(it.id)}','start',-0.1)">-0.1s</button><input value="${it.start}" onchange="updateCutTime('${esc(it.id)}','start',this.value)"><button class="button secondary" onclick="nudgeCut('${esc(it.id)}','start',0.1)">+0.1s</button><button class="button secondary" onclick="nudgeCut('${esc(it.id)}','start',1)">+1s</button></div><label>Fim: <b>${Number(it.end).toFixed(3)}s</b></label><input type="range" min="0" max="${dur}" step="0.01" value="${it.end}" oninput="updateCutTime('${esc(it.id)}','end',this.value)"><div class="row"><button class="button secondary" onclick="nudgeCut('${esc(it.id)}','end',-1)">-1s</button><button class="button secondary" onclick="nudgeCut('${esc(it.id)}','end',-0.1)">-0.1s</button><input value="${it.end}" onchange="updateCutTime('${esc(it.id)}','end',this.value)"><button class="button secondary" onclick="nudgeCut('${esc(it.id)}','end',0.1)">+0.1s</button><button class="button secondary" onclick="nudgeCut('${esc(it.id)}','end',1)">+1s</button></div>`;}
function renderCuts(seek=false){syncCuts(); const dur=Number(CUTS.source_duration||1); const tl=document.getElementById('timeline'); tl.innerHTML=(CUTS.items||[]).map(x=>`<div onclick="selectCut('${esc(x.id)}')" title="${esc(x.id)} ${esc(x.action)}" style="cursor:pointer;width:${Math.max(.5,(x.end-x.start)/dur*100)}%;background:${x.action==='keep'?'#22c55e':'#ef4444'};border-right:1px solid #111;outline:${x.id===SELECTED_ID?'3px solid #facc15':'none'};opacity:${x.id===SELECTED_ID?'1':'.82'}"></div>`).join(''); document.getElementById('cutItems').innerHTML=(CUTS.items||[]).map(x=>`<div class="media-item" onclick="selectCut('${esc(x.id)}')" style="cursor:pointer;border-color:${x.id===SELECTED_ID?'#facc15':''}"><div><b>${esc(x.action)}</b><br><span class="mini">${x.start}s → ${x.end}s<br>Duração: ${(Number(x.end)-Number(x.start)).toFixed(2)}s</span></div><div><b>${esc(x.id)}</b><p class="mini">${esc(x.reason||'')}</p><div class="item-actions"><button onclick="event.stopPropagation();setAction('${esc(x.id)}','keep')">Manter</button><button class="danger-btn" onclick="event.stopPropagation();setAction('${esc(x.id)}','cut')">Cortar</button></div></div></div>`).join(''); renderSelectedPanel(); if(seek&&SELECTED_ID) seekToCut(SELECTED_ID,false);}
function previewKeeps(){CUTS=getCuts(); const keeps=(CUTS.items||[]).filter(x=>x.action==='keep' && Number(x.end)>Number(x.start)).sort((a,b)=>Number(a.start)-Number(b.start)); const v=document.getElementById('player'); let i=0; if(!v)return; if(!keeps.length){document.getElementById('result').textContent='Não há trechos verdes para preview. Marque pelo menos um trecho como Manter.';return;} stopSegmentWatcher(); document.getElementById('result').textContent='Preview rápido: tocando somente os trechos verdes. Trechos vermelhos serão pulados.'; function playSeg(){if(i>=keeps.length){v.pause(); stopSegmentWatcher(); document.getElementById('result').textContent='Preview rápido finalizado.'; return;} v.currentTime=Number(keeps[i].start||0); const playPromise=v.play(); if(playPromise&&playPromise.catch) playPromise.catch(()=>{});} PREVIEW_TIMER=setInterval(()=>{if(i<keeps.length && v.currentTime>=Number(keeps[i].end||0)-0.03){i++;playSeg();}},80); playSeg();}
function pausePreview(){clearInterval(PREVIEW_TIMER); document.getElementById('player').pause();}
async function renderEditedPreview(){const box=document.getElementById('result'); try{CUTS=getCuts(); box.textContent='Renderizando preview editado real...'; const data=await editorApi({action:'preview_cuts',source:sourceRel(),cuts:CUTS,title:(CURRENT&&CURRENT.filename)||'cutflow'}); if(!data.ok) throw new Error(data.message); box.textContent=data.message+'\\n'+JSON.stringify(data.data,null,2); if(data.data.play_url) setPlayerReady(data.data.play_url,'Preview editado renderizado. Este player agora mostra o resultado dos cortes.');}catch(err){box.textContent='Erro no preview renderizado: '+err;}}
async function renderFinal(){const box=document.getElementById('result'); try{CUTS=getCuts(); box.textContent='Renderizando final...'; const data=await editorApi({action:'render_cuts',source:sourceRel(),cuts:CUTS,title:(CURRENT&&CURRENT.filename)||'cutflow'}); if(!data.ok) throw new Error(data.message); LAST_FINAL=data.data.relative; box.textContent=data.message+'\\n'+JSON.stringify(data.data,null,2); if(data.data.play_url) setPlayerReady(data.data.play_url,'Vídeo final renderizado. Este player agora mostra o arquivo exportado.'); await loadSources();}catch(err){box.textContent='Erro ao exportar: '+err;}}
function exportCuts(){const blob=new Blob([document.getElementById('cutsJson').value],{type:'application/json'}); const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='cuts.json'; a.click();}
async function importCuts(ev){const txt=await ev.target.files[0].text(); CUTS=JSON.parse(txt); renderCuts();}
function useCurrentVideoForCaption(){if(LAST_FINAL) document.getElementById('captionSource').value=LAST_FINAL; document.getElementById('captionPlayer').src=document.getElementById('player').src;}
async function renderCaption(){const box=document.getElementById('captionResult'); try{box.textContent='Aplicando legenda...'; const data=await editorApi({action:'caption_render',source:document.getElementById('captionSource').value,text:captionText.value,preset:captionPreset.value,max_words:maxWords.value,max_chars:maxChars.value,max_lines:maxLines.value,font_size:fontSize.value,margin_v:marginV.value,color:captionColor.value,outline_color:outlineColor.value,outline:outline.value,alignment:alignment.value}); if(!data.ok) throw new Error(data.message); box.textContent=data.message+'\\n'+JSON.stringify(data.data,null,2); if(data.data.play_url) document.getElementById('captionPlayer').src=data.data.play_url; await loadSources();}catch(err){box.textContent='Erro na legenda: '+err;}}
loadSources(); toggleSource(); toggleCaptionSource(); renderCuts();
</script>
            """, authenticated=True)
            self.send_html(body)
            return
        if path.startswith("/file/"):
            sess = self.require_login()
            if not sess: return
            parts = path.split("/")
            token = parts[2] if len(parts) >= 3 else ""
            item = FILE_TOKENS.get(token)
            if not item:
                self.send_response(404); self.end_headers(); return
            file_path = Path(item.get("path", ""))
            try:
                resolved = file_path.resolve()
                if not resolved.exists() or MIDIAS.resolve() not in resolved.parents:
                    self.send_response(404); self.end_headers(); return
                size = resolved.stat().st_size
                ascii_name, utf8_name = safe_download_name(resolved.name)
                ctype = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
                disposition = "inline" if item.get("inline") else "attachment"
                range_header = self.headers.get("Range") if item.get("inline") else None
                if range_header:
                    match = re.match(r"bytes=(\d*)-(\d*)", range_header)
                    if match:
                        start_s, end_s = match.groups()
                        start = int(start_s) if start_s else 0
                        end = int(end_s) if end_s else size - 1
                        start = max(0, min(start, size - 1))
                        end = max(start, min(end, size - 1))
                        length = end - start + 1
                        self.send_response(206)
                        self.send_header("Content-Type", ctype)
                        self.send_header("Accept-Ranges", "bytes")
                        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                        self.send_header("Content-Length", str(length))
                        self.send_header("Content-Disposition", f'{disposition}; filename="{ascii_name}"; filename*=UTF-8\'\'{utf8_name}')
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        with resolved.open("rb") as f:
                            f.seek(start)
                            remaining = length
                            while remaining > 0:
                                chunk = f.read(min(1024 * 1024, remaining))
                                if not chunk:
                                    break
                                self.wfile.write(chunk)
                                remaining -= len(chunk)
                        return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(size))
                self.send_header("Content-Disposition", f'{disposition}; filename="{ascii_name}"; filename*=UTF-8\'\'{utf8_name}')
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                with resolved.open("rb") as f:
                    shutil.copyfileobj(f, self.wfile, length=1024 * 1024)
                return
            except Exception:
                self.send_response(500); self.end_headers(); return
        if path.startswith("/download/"):
            sess = self.require_login()
            if not sess: return
            parts = path.split("/")
            token = parts[2] if len(parts) >= 3 else ""
            item = DOWNLOADS.get(token)
            if not item:
                self.send_response(404); self.end_headers(); return
            file_path = Path(item.get("path", ""))
            try:
                resolved = file_path.resolve()
                if not resolved.exists() or MIDIAS.resolve() not in resolved.parents:
                    self.send_response(404); self.end_headers(); return
                size = resolved.stat().st_size
                ascii_name, utf8_name = safe_download_name(resolved.name)
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(resolved.name)[0] or "application/octet-stream")
                self.send_header("Content-Length", str(size))
                self.send_header("Content-Disposition", f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{utf8_name}')
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                with resolved.open("rb") as f:
                    shutil.copyfileobj(f, self.wfile, length=1024 * 1024)
                return
            except Exception:
                self.send_response(500); self.end_headers(); return
        if path == "/library":
            sess = self.require_login()
            if not sess: return
            body = html_page("Biblioteca", """
            <main class="wrap">
              <section class="card">
                <h1>Biblioteca</h1>
                <p>Arquivos salvos no Nserver. Use a busca para encontrar vídeos, áudios, transcrições, cortes e editados.</p>
                <div class="row"><input id="q" placeholder="Buscar na Biblioteca..." oninput="renderLibrary()"><button onclick="loadLibrary()">Atualizar</button></div>
                <div id="stats" class="stats"></div>
                <div id="items" class="media-list"></div>
              </section>
            </main>
<script>
let ITEMS=[];
function esc(v){return String(v??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));}
async function api(payload){const res=await fetch('/api/library',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});return await res.json();}
async function loadLibrary(){const data=await api({action:'list'}); ITEMS=(data.data&&data.data.items)||[]; const st=(data.data&&data.data.stats)||{}; document.getElementById('stats').innerHTML=Object.entries(st).map(([k,v])=>`<div class="stat"><b>${esc(k)}</b><br>${esc(v)}</div>`).join(''); renderLibrary();}
async function deleteMedia(relative,name){if(!confirm('Excluir definitivamente do PC?\\n\\n'+name+'\\n\\nIsso apaga o arquivo da pasta midias do Nserver.'))return; const data=await api({action:'delete_file',relative}); alert(data.message||'Resposta recebida.'); await loadLibrary();}
function renderLibrary(){const q=(document.getElementById('q').value||'').toLowerCase(); const list=ITEMS.filter(x=>`${x.name} ${x.kind} ${x.relative}`.toLowerCase().includes(q)); document.getElementById('items').innerHTML=list.map(x=>`<div class="media-item"><div>${x.is_video?`<video class="thumb" src="${esc(x.play_url)}"></video>`:'<div class="thumb"></div>'}</div><div><h3>${esc(x.name)}</h3><p class="mini"><b>Tipo:</b> ${esc(x.kind)}<br><b>Tamanho:</b> ${esc(x.size)}<br><b>Local:</b> ${esc(x.relative)}</p><div class="item-actions">${x.play_url?`<a class="button secondary" href="${esc(x.play_url)}" target="_blank">Abrir</a>`:''}<a class="button" href="${esc(x.download_url)}">Baixar</a><button class="danger-btn" onclick="deleteMedia(decodeURIComponent('${encodeURIComponent(x.relative)}'),decodeURIComponent('${encodeURIComponent(x.name)}'))">Remover</button></div></div></div>`).join('')||'<p class="muted">Nenhum arquivo encontrado.</p>';}
loadLibrary();
</script>
            """, authenticated=True)
            self.send_html(body)
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
                  <button class="secondary" onclick="restartServer()">Reiniciar Nserver</button>
                </div>
                <div id="updateResult" class="result muted">Aguardando verificação.</div>
                {last}
              </section>
            </main>
<script>
document.getElementById('channel').value = '{cfg.get('update_channel', 'stable')}';
async function updateApi(payload) {{ const res = await fetch('/api/updates', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(payload)}}); return await res.json(); }}
async function saveUpdateConfig() {{ const data = await updateApi({{action:'config', manifest_url:document.getElementById('manifestUrl').value, channel:document.getElementById('channel').value}}); document.getElementById('updateResult').textContent = data.message; }}
async function checkUpdate() {{ const box=document.getElementById('updateResult'); box.textContent='Verificando...'; const data = await updateApi({{action:'check'}}); box.textContent = data.message + '\\n' + JSON.stringify(data.data || {{}}, null, 2); }}
async function applyUpdate() {{ if(!confirm('Criar backup e aplicar atualização agora?')) return; const box=document.getElementById('updateResult'); box.textContent='Atualizando...'; const data = await updateApi({{action:'apply'}}); box.textContent = data.message + '\\n' + JSON.stringify(data.data || {{}}, null, 2); }}
async function restartServer() {{ if(!confirm('Reiniciar o Nserver agora? A página pode ficar fora do ar por alguns segundos.')) return; const box=document.getElementById('updateResult'); box.textContent='Reiniciando o Nserver... aguarde alguns segundos e recarregue a página.'; const data = await updateApi({{action:'restart'}}); box.textContent = data.message + '\\n' + JSON.stringify(data.data || {{}}, null, 2); setTimeout(() => location.reload(), 5000); }}
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

    def parse_multipart_upload(self, length: int) -> tuple[str, bytes]:
        content_type = self.headers.get("Content-Type", "")
        match = re.search(r"boundary=(.+)", content_type)
        if not match:
            raise ValueError("Upload inválido: boundary ausente.")
        boundary = match.group(1).strip().strip('"').encode("utf-8")
        body = self.rfile.read(length)
        marker = b"--" + boundary
        for part in body.split(marker):
            if b"Content-Disposition" not in part or b"filename=" not in part:
                continue
            header, _, data = part.partition(b"\r\n\r\n")
            if not data:
                continue
            data = data.rstrip(b"\r\n-")
            header_text = header.decode("utf-8", errors="replace")
            fname = re.search(r'filename="([^"]*)"', header_text)
            filename = fname.group(1) if fname else "upload.bin"
            return filename, data
        raise ValueError("Nenhum arquivo encontrado no upload.")

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/tool/course-ingest-map":
            sess = self.require_login()
            if not sess: return
            length = int(self.headers.get("Content-Length", "0"))
            try:
                data = parse_qs(self.rfile.read(length).decode("utf-8"))
                url = data.get("url", [""])[0]
                email_value = data.get("email", [""])[0]
                password_value = data.get("password", [""])[0]
                result = PROCESSORS["course-ingest"].map_course(url, email_value, password_value)
                payload = result.data or {}
                if result.ok:
                    modules = payload.get("modules") or []
                    rows = "".join(f"<li><strong>{html.escape(str(m.get('title','')))}</strong> — {len(m.get('lessons') or [])} aula(s)</li>" for m in modules)
                    msg = f"<h1>Curso mapeado</h1><p>{html.escape(result.message)}</p><p><strong>ID:</strong> {html.escape(str(payload.get('id','latest')))}</p><p><strong>Curso:</strong> {html.escape(str(payload.get('title','')))}</p><ul>{rows}</ul><p><a class='button' href='/tool/course-ingest'>Voltar para a Ferramenta 05</a></p>"
                else:
                    msg = f"<h1>Falha ao mapear</h1><p>{html.escape(result.message)}</p><p><a class='button' href='/tool/course-ingest'>Voltar e tentar novamente</a></p>"
                self.send_html(html_page("Resultado do mapeamento", f"<main class='wrap'><section class='card'>{msg}</section></main>", authenticated=True)); return
            except Exception as exc:
                self.send_html(html_page("Erro no mapeamento", f"<main class='wrap'><section class='card'><h1>Erro no mapeamento</h1><p>{html.escape(str(exc))}</p><p><a class='button' href='/tool/course-ingest'>Voltar</a></p></section></main>", authenticated=True), 500); return
        if path == "/api/media":
            sess = self.require_login()
            if not sess: return
            length = int(self.headers.get("Content-Length", "0"))
            ctype = self.headers.get("Content-Type", "")
            try:
                if ctype.startswith("multipart/form-data"):
                    filename, data = self.parse_multipart_upload(length)
                    item = MEDIA.save_upload(filename, data)
                    self.send_json({"ok": True, "message": "Upload salvo na Biblioteca.", "data": item}); return
                payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                action = payload.get("action", "list")
                if action == "list":
                    kind = payload.get("kind")
                    kinds = set(kind.split(",")) if kind else None
                    self.send_json({"ok": True, "message": "Mídias carregadas.", "data": {"items": MEDIA.list(kinds)}}); return
                self.send_json({"ok": False, "message": "Ação de mídia desconhecida."}, 400); return
            except Exception as exc:
                self.send_json({"ok": False, "message": f"Falha no Gerenciador de Mídia: {exc}"}, 500); return
        if path == "/api/history":
            sess = self.require_login()
            if not sess: return
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except Exception:
                self.send_json({"ok": False, "message": "JSON inválido."}, 400); return
            action = payload.get("action", "list")
            if action == "list":
                self.send_json({"ok": True, "message": "Histórico carregado.", "data": {"items": load_history()}}); return
            if action == "delete":
                removed = delete_history(payload.get("ids") or [], bool(payload.get("all")))
                self.send_json({"ok": True, "message": f"{removed} item(ns) removido(s) do histórico.", "data": {"removed": removed}}); return
            self.send_json({"ok": False, "message": "Ação de histórico desconhecida."}, 400); return
        if path == "/api/library":
            sess = self.require_login()
            if not sess: return
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except Exception:
                self.send_json({"ok": False, "message": "JSON inválido."}, 400); return
            action = payload.get("action", "list")
            if action == "list":
                items = library_files()
                self.send_json({"ok": True, "message": "Biblioteca carregada.", "data": {"items": items, "stats": library_stats(items)}}); return
            if action == "favorite":
                rel = payload.get("relative", "")
                fav = load_favorites()
                if rel in fav:
                    fav.remove(rel)
                    message = "Removido dos favoritos."
                else:
                    fav.add(rel)
                    message = "Adicionado aos favoritos."
                save_favorites(fav)
                self.send_json({"ok": True, "message": message, "data": {"favorite": rel in fav}}); return
            if action == "read_text":
                rel = payload.get("relative", "")
                try:
                    target = (MIDIAS / rel).resolve()
                    if MIDIAS.resolve() not in target.parents or target.suffix.lower() not in {".txt", ".md", ".json"}:
                        self.send_json({"ok": False, "message": "Arquivo de texto inválido."}, 400); return
                    text = target.read_text(encoding="utf-8", errors="replace")
                    self.send_json({"ok": True, "message": "Transcrição carregada.", "data": {"text": text, "name": target.name}}); return
                except Exception as exc:
                    self.send_json({"ok": False, "message": f"Não consegui abrir a transcrição: {exc}"}, 500); return
            if action == "delete_file":
                rel = payload.get("relative", "")
                try:
                    target = (MIDIAS / rel).resolve()
                    if MIDIAS.resolve() not in target.parents or not target.is_file():
                        self.send_json({"ok": False, "message": "Arquivo inválido."}, 400); return
                    name = target.name
                    target.unlink()
                    fav = load_favorites()
                    if rel in fav:
                        fav.remove(rel)
                        save_favorites(fav)
                    # Remove links temporários que apontavam para o arquivo apagado.
                    for token, item in list(FILE_TOKENS.items()):
                        if item.get("path") == str(target):
                            FILE_TOKENS.pop(token, None)
                    self.send_json({"ok": True, "message": f"Arquivo removido do PC: {name}", "data": {"deleted": rel}}); return
                except Exception as exc:
                    self.send_json({"ok": False, "message": f"Não consegui excluir o arquivo: {exc}"}, 500); return
            if action == "open_folder":
                rel = payload.get("relative", "")
                try:
                    target = (MIDIAS / rel).resolve()
                    if MIDIAS.resolve() not in target.parents and target != MIDIAS.resolve():
                        self.send_json({"ok": False, "message": "Caminho inválido."}, 400); return
                    folder = target.parent if target.is_file() else target
                    if os.name == "nt":
                        os.startfile(str(folder))  # type: ignore[attr-defined]
                    elif sys.platform == "darwin":
                        subprocess.Popen(["open", str(folder)])
                    else:
                        subprocess.Popen(["xdg-open", str(folder)])
                    self.send_json({"ok": True, "message": "Pasta aberta no servidor.", "data": {"folder": str(folder)}}); return
                except Exception as exc:
                    self.send_json({"ok": False, "message": f"Não consegui abrir a pasta no servidor: {exc}"}, 500); return
            self.send_json({"ok": False, "message": "Ação de biblioteca desconhecida."}, 400); return
        if path == "/api/settings":
            sess = self.require_login()
            if not sess: return
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                self.send_json({"ok": False, "message": "JSON inválido."}, 400); return
            action = payload.get("action")
            if action == "get":
                self.send_json({"ok": True, "message": "Configuração carregada.", "data": public_config()}); return
            if action == "save":
                patch = {
                    "openai_base_url": payload.get("openai_base_url", "https://api.openai.com/v1").strip() or "https://api.openai.com/v1",
                    "transcription_provider": payload.get("transcription_provider", "local"),
                    "local_whisper_model": payload.get("local_whisper_model", "base"),
                }
                key = payload.get("openai_api_key", "").strip()
                if key:
                    patch["openai_api_key"] = key
                save_app_config(patch)
                self.send_json({"ok": True, "message": "Configuração salva com segurança neste PC.", "data": public_config()}); return
            self.send_json({"ok": False, "message": "Ação de configuração desconhecida."}, 400); return
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
                if action == "restart":
                    UPDATER.schedule_restart(delay=0.8)
                    self.send_json({"ok": True, "message": "Nserver reiniciando. Aguarde alguns segundos e recarregue a página.", "data": {"current_version": APP_VERSION}}); return
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
        if path == "/api/course":
            sess = self.require_login()
            if not sess: return
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except Exception:
                self.send_json({"ok": False, "message": "JSON inválido."}, 400); return
            result = PROCESSORS["course-ingest"].run(payload)
            data = result.data or {}
            try:
                if result.ok and data.get("md"):
                    mp = Path(data["md"]).resolve()
                    if mp.exists() and MIDIAS.resolve() in mp.parents:
                        data["download_url"] = f"/file/{token_for_file(mp, inline=False)}/{safe_download_name(mp.name)[0]}"
                elif result.ok and data.get("zip"):
                    zp = Path(data["zip"]).resolve()
                    if zp.exists() and MIDIAS.resolve() in zp.parents:
                        data["download_url"] = f"/file/{token_for_file(zp, inline=False)}/{safe_download_name(zp.name)[0]}"
                if result.ok and data.get("folder"):
                    data["library_url"] = "/library"
            except Exception:
                pass
            self.send_json({"ok": result.ok, "message": result.message, "data": data})
            return
        if path == "/api/editor":
            sess = self.require_login()
            if not sess: return
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except Exception:
                self.send_json({"ok": False, "message": "JSON inválido."}, 400); return
            result = PROCESSORS["video-editor"].run(payload)
            data = result.data or {}
            if result.ok:
                rel = data.get("preview_source") or data.get("source") or payload.get("source") or ""
                try:
                    if rel:
                        src = (MIDIAS / rel).resolve()
                        if src.exists() and MIDIAS.resolve() in src.parents:
                            data["play_url"] = f"/file/{token_for_file(src, inline=True)}/{safe_download_name(src.name)[0]}"
                except Exception:
                    pass
                try:
                    if data.get("relative"):
                        out = (MIDIAS / data["relative"]).resolve()
                        if out.exists() and MIDIAS.resolve() in out.parents:
                            data["play_url"] = f"/file/{token_for_file(out, inline=True)}/{safe_download_name(out.name)[0]}"
                            data["download_url"] = f"/file/{token_for_file(out, inline=False)}/{safe_download_name(out.name)[0]}"
                except Exception:
                    pass
            if result.ok and payload.get("action") in {"render", "render_cuts", "caption_render"} and data.get("file"):
                upsert_history({
                    "id": secrets.token_urlsafe(12),
                    "date": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "url": data.get("source") or payload.get("source", ""),
                    "title": data.get("filename") or payload.get("title") or "Vídeo editado",
                    "thumbnail": "",
                    "platform": "Nserver",
                    "operation": "video_editor",
                    "operation_label": operation_label("video_editor", payload),
                    "status": "Concluído",
                    "location": data.get("file") or "",
                    "library_url": "/library?file=" + quote(data.get("relative", ""), safe="") if data.get("relative") else "",
                })
            self.send_json({"ok": result.ok, "message": result.message, "data": data})
            return
        if path == "/api/video":
            sess = self.require_login()
            if not sess: return
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                self.send_json({"ok": False, "message": "JSON inválido."}, 400); return
            action = payload.get("action", "")
            meta = {}
            if payload.get("url") and action != "analyze":
                try:
                    analyzed = PROCESSORS["video-downloader"].analyze(payload.get("url", ""))
                    if analyzed.ok:
                        meta = analyzed.data or {}
                except Exception:
                    meta = {}
            result = PROCESSORS["video-downloader"].run(payload)
            data = result.data or {}
            if action == "analyze" and result.ok:
                meta = data
            file_path = data.get("file")
            if result.ok and data.get("downloadable") and file_path:
                token = secrets.token_urlsafe(18)
                DOWNLOADS[token] = {"path": file_path, "delete_after_download": bool(data.get("delete_after_download")), "created": time.time()}
                display_name = data.get("filename") or Path(file_path).name
                ascii_name, _ = safe_download_name(display_name)
                data["download_url"] = f"/download/{token}/{ascii_name}"
                data["download_filename"] = ascii_name
                data["download_note"] = "Link temporário. Se o destino for dispositivo, o arquivo é removido do servidor após alguns minutos." if data.get("delete_after_download") else "Link para baixar uma cópia; o arquivo continua salvo no Nserver."
            library_url = ""
            if data.get("file"):
                try:
                    rel = Path(data["file"]).resolve().relative_to(MIDIAS.resolve()).as_posix()
                    if not rel.startswith("_Temporarios/"):
                        library_url = "/library?file=" + quote(rel, safe="")
                except Exception:
                    library_url = ""
            if action != "analyze":
                upsert_history({
                    "id": secrets.token_urlsafe(12),
                    "date": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "url": payload.get("url", ""),
                    "title": meta.get("title") or data.get("title") or "Sem título",
                    "thumbnail": meta.get("thumbnail") or data.get("thumbnail") or "",
                    "platform": meta.get("platform") or data.get("platform") or "-",
                    "operation": action,
                    "operation_label": operation_label(action, payload),
                    "status": "Concluído" if result.ok else "Erro",
                    "location": data.get("file") or data.get("folder") or "",
                    "library_url": library_url,
                })
            self.send_json({"ok": result.ok, "message": result.message, "data": data})
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
