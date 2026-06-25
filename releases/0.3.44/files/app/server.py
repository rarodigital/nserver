#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import hmac
import html
import json
import mimetypes
import os
import asyncio
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
from urllib.parse import parse_qs, quote, urlencode, urlparse
import urllib.request

from processors.video import VideoProcessor
from processors.editor import VideoEditorProcessor
from processors.course import CourseProcessor
from processors.media import MediaManager, VIDEO_EXTS, AUDIO_EXTS
from updater import Updater

APP_NAME = "Nserver"
APP_VERSION = "0.3.44"
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
    {
        "id": "jobhunter",
        "name": "JobHunter AI",
        "description": "Busque vagas reais, calcule match com o perfil profissional e gere cartas de apresentação por usuário.",
        "status": "novo",
        "href": "/tool/jobhunter",
    },
]
PROCESSORS = {"video-downloader": VideoProcessor(ROOT), "video-editor": VideoEditorProcessor(ROOT), "course-ingest": CourseProcessor(ROOT)}
MEDIA = MediaManager(ROOT)
UPDATER = Updater(ROOT, APP_VERSION)
HISTORY_FILE = USERDATA / "history.json"
FAVORITES_FILE = USERDATA / "favorites.json"
LIBRARY_OWNERS_FILE = USERDATA / "library-owners.json"
DOWNLOADS: dict[str, dict] = {}
FILE_TOKENS: dict[str, dict] = {}
USERS_FILE = USERDATA / "users.json"
TELEGRAM_PAIRINGS_FILE = USERDATA / "telegram-pairings.json"
TELEGRAM_REAL_DIR = USERDATA / "telegram-real"
TELEGRAM_REAL_LOGINS: dict[str, dict] = {}

PERMISSIONS = {
    "dashboard": "Dashboard",
    "library": "Biblioteca",
    "updates": "Atualizações",
    "agent_chat": "Chat do Agente",
    "users_manage": "Gerenciar usuários",
    "settings": "Configurações",
    "ai_modify_server": "IA pode alterar servidor/arquivos",
    "tool.video-downloader": "Ferramenta 01 — Downloader",
    "tool.transcription": "Ferramenta 02 — Transcrição",
    "tool.viral-clips": "Ferramenta 03 — Cortes Virais",
    "tool.video-editor": "Ferramenta 04 — Editor de Vídeo",
    "tool.course-ingest": "Ferramenta 05 — Curso → TheronCore",
    "tool.jobhunter": "JobHunter AI",
}

ROLE_PERMISSIONS = {
    "admin": list(PERMISSIONS.keys()),
    "colaborador": [p for p in PERMISSIONS if p != "users_manage"],
    "usuario": ["dashboard", "agent_chat", "tool.video-downloader", "tool.jobhunter"],
}



JOBHUNTER_FILE = USERDATA / "jobhunter.json"
JOBHUNTER_RESUMES = USERDATA / "jobhunter-resumes"
JOBHUNTER_RESUMES.mkdir(parents=True, exist_ok=True)

JOBHUNTER_DEFAULT = {
    "professional_profiles": [],
    "resume_files": [],
    "job_preferences": [],
    "jobs": [],
    "job_matches": [],
    "applications": [],
    "telegram_settings": [],
    "interview_simulations": [],
    "career_plans": [],
}


def load_jobhunter() -> dict:
    if not JOBHUNTER_FILE.exists():
        return json.loads(json.dumps(JOBHUNTER_DEFAULT))
    try:
        data = json.loads(JOBHUNTER_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    for key, default in JOBHUNTER_DEFAULT.items():
        data.setdefault(key, list(default))
    return data


def save_jobhunter(data: dict):
    for key, default in JOBHUNTER_DEFAULT.items():
        data.setdefault(key, list(default))
    JOBHUNTER_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def jh_now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def current_user_id(sess: dict) -> str:
    return str(sess.get("username") or "user")


def jh_latest(items: list[dict], user_id: str) -> dict | None:
    mine = [x for x in items if str(x.get("user_id")) == user_id]
    if not mine:
        return None
    return sorted(mine, key=lambda x: str(x.get("created_at") or ""), reverse=True)[0]


def jh_upsert_user_record(data: dict, table: str, user_id: str, values: dict) -> dict:
    items = data.setdefault(table, [])
    existing = jh_latest(items, user_id)
    if not existing:
        existing = {"id": secrets.token_urlsafe(12), "user_id": user_id, "created_at": jh_now()}
        items.append(existing)
    existing.update(values)
    existing["updated_at"] = jh_now()
    return existing


def normalize_job_key(job: dict) -> str:
    raw = f"{job.get('title','')}|{job.get('company','')}|{job.get('url','')}".lower()
    return re.sub(r"\s+", " ", raw).strip()


def jh_job_exists(jobs: list[dict], job: dict) -> bool:
    key = normalize_job_key(job)
    return any(normalize_job_key(j) == key or (job.get("external_id") and j.get("source") == job.get("source") and j.get("external_id") == job.get("external_id")) for j in jobs)


def fetch_json_url(url: str, timeout: int = 20) -> dict | list:
    req = urllib.request.Request(url, headers={"User-Agent": "Nserver JobHunter/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8", errors="replace"))


def jh_ingest_remotive(limit: int = 30) -> list[dict]:
    data = fetch_json_url(f"https://remotive.com/api/remote-jobs?limit={int(limit)}")
    out = []
    for item in (data.get("jobs") if isinstance(data, dict) else []) or []:
        out.append({"id": secrets.token_urlsafe(12), "source": "Remotive", "external_id": str(item.get("id") or ""), "title": item.get("title") or "", "company": item.get("company_name") or "", "location": item.get("candidate_required_location") or "Remote", "salary": item.get("salary") or "", "description": re.sub(r"<[^>]+>", " ", item.get("description") or "").strip(), "url": item.get("url") or "", "posted_at": item.get("publication_date") or "", "created_at": jh_now()})
    return out


def jh_ingest_remoteok(limit: int = 30) -> list[dict]:
    data = fetch_json_url("https://remoteok.com/api")
    out = []
    for item in (data if isinstance(data, list) else [])[1:int(limit)+1]:
        out.append({"id": secrets.token_urlsafe(12), "source": "RemoteOK", "external_id": str(item.get("id") or ""), "title": item.get("position") or item.get("title") or "", "company": item.get("company") or "", "location": item.get("location") or "Remote", "salary": item.get("salary") or "", "description": re.sub(r"<[^>]+>", " ", item.get("description") or "").strip(), "url": item.get("url") or item.get("apply_url") or "", "posted_at": item.get("date") or "", "created_at": jh_now()})
    return out


def text_tokens(value: str) -> set[str]:
    stop = {"para","com","uma","the","and","or","de","da","do","em","dos","das","a","o","e","ai","ia"}
    return {t for t in re.findall(r"[a-zA-ZÀ-ÿ0-9+#.]{3,}", (value or "").lower()) if t not in stop}


def jh_score(profile: dict | None, prefs: dict | None, resume: dict | None, job: dict) -> dict:
    profile = profile or {}; prefs = prefs or {}; parsed = resume.get("parsed_content") if resume else {}
    hay = " ".join(str(job.get(k) or "") for k in ["title", "company", "location", "salary", "description"]).lower()
    skills = parsed.get("skills") if isinstance(parsed, dict) else []
    if isinstance(skills, str): skills = [skills]
    terms = text_tokens(" ".join([profile.get("headline", ""), profile.get("bio", ""), " ".join(skills or [])]))
    hits = sorted([t for t in terms if t in hay])[:16]
    score = min(98, 35 + len(hits) * 6)
    if prefs.get("remote") and any(x in hay for x in ["remote", "remoto", "anywhere"]): score += 8
    if prefs.get("freelance") and any(x in hay for x in ["freelance", "contract", "pj"]): score += 5
    score = max(0, min(100, score))
    gaps = [w for w in ["python", "langchain", "sql", "english", "inglês", "api"] if w in hay and w not in hits][:5]
    motives = hits[:6] or ["vaga remota capturada para análise", "complete o perfil para refinar o match"]
    recs = ["Personalizar carta citando resultados mensuráveis.", "Destacar projetos práticos conectados às keywords da vaga."]
    return {"score": score, "analysis": motives, "gaps": gaps, "recommendations": recs, "provider": "local-fallback"}


def jh_ai_match(profile: dict | None, prefs: dict | None, resume: dict | None, job: dict) -> dict | None:
    prompt = {
        "task": "Compare o perfil profissional, preferências e currículo com a vaga. Responda somente JSON válido.",
        "schema": {"score": "0-100", "analysis": ["motivos objetivos do match"], "gaps": ["lacunas"], "recommendations": ["ações práticas"]},
        "profile": profile or {},
        "preferences": prefs or {},
        "resume": (resume or {}).get("parsed_content") or {},
        "job": job,
    }
    data = nserver_ai_json("JobHunter AI — match de vaga", json.dumps(prompt, ensure_ascii=False), max_tokens=1200)
    if not data:
        return None
    try:
        score = int(data.get("score", 0))
    except Exception:
        score = 0
    return {
        "score": max(0, min(100, score)),
        "analysis": data.get("analysis") or data.get("motivos") or [],
        "gaps": data.get("gaps") or [],
        "recommendations": data.get("recommendations") or data.get("recomendacoes") or [],
        "provider": data.get("provider") or "platform-ai",
    }


def jh_match_job(data: dict, user_id: str, job: dict) -> dict:
    profile = jh_latest(data.get("professional_profiles", []), user_id)
    prefs = jh_latest(data.get("job_preferences", []), user_id)
    resume = jh_latest(data.get("resume_files", []), user_id)
    result = jh_ai_match(profile, prefs, resume, job) or jh_score(profile, prefs, resume, job)
    existing = next((m for m in data.setdefault("job_matches", []) if m.get("job_id") == job.get("id") and m.get("user_id") == user_id), None)
    if not existing:
        existing = {"id": secrets.token_urlsafe(12), "job_id": job.get("id"), "user_id": user_id, "created_at": jh_now()}
        data["job_matches"].append(existing)
    existing.update(result)
    return existing


def jh_generate_cover(profile: dict | None, resume: dict | None, job: dict) -> str:
    name = (profile or {}).get("name") or "Olá"
    headline = (profile or {}).get("headline") or "profissional de tecnologia e automação"
    parsed = (resume or {}).get("parsed_content") or {}
    skills = parsed.get("skills") if isinstance(parsed, dict) else []
    if isinstance(skills, str): skills = [skills]
    skill_text = ", ".join((skills or [])[:6]) or "automação, integração de sistemas e IA"
    ai_cover = nserver_ai_text(
        "JobHunter AI — gere uma carta de apresentação profissional, personalizada e direta. Não invente dados; use somente o perfil/currículo/vaga recebidos.",
        json.dumps({"profile": profile or {}, "resume": parsed, "job": job, "rules": ["3 parágrafos", "tom profissional", "idioma automático pela vaga", "sem frases genéricas", "mencione skills reais"]}, ensure_ascii=False),
        max_tokens=1200,
        temperature=0.25,
    )
    if ai_cover.strip():
        return ai_cover.strip()
    english = any(w in (job.get("description", "") + " " + job.get("title", "")).lower() for w in ["english", "remote", "latam", "worldwide"])
    if english:
        return f"Hi {job.get('company') or 'team'},\n\nI am applying for the {job.get('title')} role because my background as {headline} connects directly with this opportunity. I have hands-on experience with {skill_text}, building practical workflows that reduce manual work and improve operational execution.\n\nMy advantage is combining business understanding with implementation: I can map the process, design the automation, connect APIs/tools, and communicate results clearly.\n\nI would be glad to discuss how I can help {job.get('company') or 'your team'} deliver faster, more reliable outcomes.\n\nBest regards,\n{name}"
    return f"Olá, time da {job.get('company') or 'empresa'}.\n\nTenho interesse na vaga de {job.get('title')} porque minha experiência como {headline} se conecta diretamente com o que a oportunidade pede. Tenho vivência prática com {skill_text}, criando fluxos e soluções que reduzem trabalho manual e melhoram a execução.\n\nMeu diferencial é unir visão de negócio com implementação: entendo o processo, desenho a automação, integro APIs/ferramentas e organizo a entrega de ponta a ponta.\n\nFico à disposição para conversar e mostrar como posso contribuir rapidamente para os resultados da {job.get('company') or 'empresa'}.\n\nAtenciosamente,\n{name}"


def jh_simulate_interview(profile: dict | None, resume: dict | None, job: dict | None = None) -> dict:
    profile = profile or {}
    parsed = (resume or {}).get("parsed_content") or {}
    skills = parsed.get("skills") if isinstance(parsed, dict) else []
    if isinstance(skills, str): skills = [skills]
    ai_sim = nserver_ai_json(
        "JobHunter AI — crie simulação de entrevista personalizada. Retorne JSON com target, questions e guidance.",
        json.dumps({"profile": profile, "resume": parsed, "job": job or {}, "schema": {"target": "string", "questions": ["perguntas"], "guidance": ["orientações"]}}, ensure_ascii=False),
        max_tokens=1400,
    )
    if ai_sim and isinstance(ai_sim.get("questions"), list):
        return {"target": ai_sim.get("target") or ((job or {}).get("title") or profile.get("headline") or "vaga alvo"), "questions": ai_sim.get("questions") or [], "guidance": ai_sim.get("guidance") or []}
    target = job.get("title") if job else profile.get("headline") or "vaga alvo"
    company = job.get("company") if job else "empresa alvo"
    base_questions = [
        f"Conte sua trajetória e por que ela combina com {target}.",
        "Descreva um projeto real em que você automatizou um processo de ponta a ponta.",
        "Como você prioriza requisitos quando o cliente quer velocidade, qualidade e baixo custo ao mesmo tempo?",
        "Explique uma integração via API/Webhook que você já construiu ou construiria.",
        "Como você mede sucesso de uma automação ou agente de IA em produção?",
        "Fale de um erro técnico/comercial que você cometeu e como corrigiu.",
        f"Que resultado você entregaria nos primeiros 30 dias na {company}?",
    ]
    if skills:
        base_questions.append(f"Pergunta técnica: como você usaria {', '.join(skills[:3])} para resolver um problema real dessa vaga?")
    guidance = [
        "Responder com método STAR: Situação, Tarefa, Ação, Resultado.",
        "Trazer números: horas economizadas, conversão, receita, volume ou tempo de entrega.",
        "Conectar cada resposta a uma necessidade explícita da vaga.",
        "Preparar uma pergunta final sobre metas, stack e próximos 90 dias.",
    ]
    return {"target": target, "questions": base_questions, "guidance": guidance}


def jh_plan_career(profile: dict | None, prefs: dict | None, resume: dict | None, jobs: list[dict] | None = None) -> dict:
    profile = profile or {}; prefs = prefs or {}; parsed = (resume or {}).get("parsed_content") or {}
    skills = parsed.get("skills") if isinstance(parsed, dict) else []
    if isinstance(skills, str): skills = [skills]
    ai_plan = nserver_ai_json(
        "JobHunter AI — crie planejamento de carreira prático. Retorne JSON com positioning, next_30_days, next_60_days, next_90_days, skill_gaps e recommended_focus.",
        json.dumps({"profile": profile, "preferences": prefs, "resume": parsed, "jobs_sample": (jobs or [])[:10]}, ensure_ascii=False),
        max_tokens=1800,
    )
    if ai_plan and isinstance(ai_plan.get("next_30_days"), list):
        return {
            "positioning": ai_plan.get("positioning") or profile.get("headline") or "Definir posicionamento.",
            "next_30_days": ai_plan.get("next_30_days") or [],
            "next_60_days": ai_plan.get("next_60_days") or [],
            "next_90_days": ai_plan.get("next_90_days") or [],
            "skill_gaps": ai_plan.get("skill_gaps") or [],
            "recommended_focus": ai_plan.get("recommended_focus") or [],
        }
    wanted = []
    for job in jobs or []:
        wanted.extend(list(text_tokens((job.get("title", "") + " " + job.get("description", "")))))
    market = [x for x in ["python", "sql", "langchain", "supabase", "api", "n8n", "openai", "react", "crm", "english"] if x in wanted]
    gaps = [x for x in market if x not in set(skills or [])][:6]
    return {
        "positioning": profile.get("headline") or "Definir headline focada no cargo alvo.",
        "next_30_days": [
            "Finalizar currículo com métricas e versão em inglês se buscar vagas internacionais.",
            "Publicar 1 case curto de automação/IA com problema, solução e resultado.",
            "Aplicar em vagas com score alto e registrar status no JobHunter.",
        ],
        "next_60_days": [
            "Criar portfólio com 2-3 projetos demonstráveis.",
            "Ajustar LinkedIn para keywords recorrentes das vagas importadas.",
            "Fazer simulações de entrevista para as 5 vagas com maior score.",
        ],
        "next_90_days": [
            "Medir conversão: aplicações → respostas → entrevistas → propostas.",
            "Dobrar aposta nos canais/fontes com maior taxa de resposta.",
            "Adicionar certificação ou projeto prático cobrindo os gaps mais frequentes.",
        ],
        "skill_gaps": gaps,
        "recommended_focus": ["Automação aplicada", "Integrações API", "Agentes IA", "Provas de resultado", "Comunicação comercial"],
    }


def jh_parse_resume_text(text: str) -> dict:
    ai_parsed = nserver_ai_json(
        "JobHunter AI — extraia dados estruturados de currículo. Retorne JSON com experiences, skills, languages, certifications, education e summary. Não invente dados.",
        (text or "")[:12000],
        max_tokens=1800,
    )
    if ai_parsed and any(ai_parsed.get(k) for k in ["experiences", "skills", "languages", "certifications", "education", "summary"]):
        ai_parsed.setdefault("raw_excerpt", text[:4000])
        return ai_parsed
    words = text_tokens(text)
    known = ["python","javascript","react","node","n8n","make","zapier","openai","claude","gemini","api","rest","webhook","json","sql","supabase","airtable","notion","linkedin","sdr","crm","sales","ghl","langchain"]
    skills = sorted([k for k in known if k in words])
    languages = [lang for lang in ["português", "inglês", "espanhol", "english", "spanish"] if lang in text.lower()]
    return {"experiences": [], "skills": skills, "languages": languages, "certifications": [], "education": [], "raw_excerpt": text[:4000], "provider": "local-fallback"}

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


def load_library_owners() -> dict:
    if LIBRARY_OWNERS_FILE.exists():
        try:
            data = json.loads(LIBRARY_OWNERS_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def save_library_owners(data: dict):
    LIBRARY_OWNERS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def set_library_owner(rel: str, username: str, shared_with: list[str] | None = None, shared_all: bool = False):
    data = load_library_owners()
    item = data.setdefault(rel, {})
    item["owner"] = username
    item["shared_with"] = shared_with or item.get("shared_with", []) or []
    item["shared_all"] = bool(shared_all or item.get("shared_all", False))
    item["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_library_owners(data)


def library_meta(rel: str) -> dict:
    return load_library_owners().get(rel, {})


def can_see_library_rel(sess: dict, rel: str) -> bool:
    if has_permission(sess, "users_manage"):
        return True
    username = sess.get("username", "")
    meta = library_meta(rel)
    if not meta:
        return False
    return meta.get("owner") == username or meta.get("shared_all") or username in set(meta.get("shared_with") or [])


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


def library_files(sess: dict | None = None) -> list[dict]:
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
            if sess is not None and not can_see_library_rel(sess, rel):
                continue
            size = resolved.stat().st_size
            kind = media_kind(resolved)
            meta = library_meta(rel)
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
                "owner": meta.get("owner") or "legado/admin",
                "shared_with": meta.get("shared_with") or [],
                "shared_all": bool(meta.get("shared_all")),
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


AGENT_BRAIN = ROOT / "agent-brain"
AGENT_WEB = USERDATA / "agent-web"
AGENT_WEB_SESSIONS = AGENT_WEB / "sessions"
AGENT_WEB.mkdir(parents=True, exist_ok=True)
AGENT_WEB_SESSIONS.mkdir(parents=True, exist_ok=True)


def read_agent_env() -> dict:
    data = {}
    for env_path in [ROOT / "agente" / ".env", ROOT / "agent" / ".env", ROOT / ".env"]:
        if not env_path.exists():
            continue
        for raw in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip().strip('"').strip("'")
    return data


def openrouter_config() -> dict:
    cfg = load_config()
    env = read_agent_env()
    key = os.environ.get("OPENROUTER_API_KEY") or cfg.get("openrouter_api_key") or env.get("OPENROUTER_API_KEY") or ""
    models = os.environ.get("OPENROUTER_MODELS") or cfg.get("openrouter_models") or env.get("OPENROUTER_MODELS") or env.get("OPENROUTER_MODEL") or "qwen/qwen3-coder:free,nex-agi/nex-n2-pro:free,nvidia/nemotron-3-super-120b-a12b:free"
    return {"key": key.strip(), "models": [m.strip() for m in str(models).split(",") if m.strip()]}


def telegram_config() -> dict:
    env = read_agent_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or env.get("TELEGRAM_BOT_TOKEN") or ""
    bot_username = os.environ.get("NSERVER_TELEGRAM_BOT_USERNAME") or env.get("NSERVER_TELEGRAM_BOT_USERNAME") or env.get("TELEGRAM_BOT_USERNAME") or ""
    if token and not bot_username:
        try:
            req = urllib.request.Request(f"https://api.telegram.org/bot{token}/getMe", method="POST")
            with urllib.request.urlopen(req, timeout=10) as res:
                data = json.loads(res.read().decode("utf-8"))
            bot_username = ((data.get("result") or {}).get("username") or "").strip()
        except Exception:
            bot_username = ""
    return {"token": token.strip(), "bot_username": bot_username.strip().lstrip("@")}


def load_telegram_pairings() -> dict:
    if TELEGRAM_PAIRINGS_FILE.exists():
        try:
            data = json.loads(TELEGRAM_PAIRINGS_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def save_telegram_pairings(data: dict):
    TELEGRAM_PAIRINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def create_telegram_pairing(username: str) -> dict:
    cfg = telegram_config()
    if not cfg.get("token"):
        return {"ok": False, "message": "TELEGRAM_BOT_TOKEN não configurado no .env do agente."}
    if not cfg.get("bot_username"):
        return {"ok": False, "message": "Não consegui descobrir o username do bot. Configure NSERVER_TELEGRAM_BOT_USERNAME no .env do agente."}
    code = secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:10]
    pairings = load_telegram_pairings()
    pairings[code] = {"username": username, "status": "pending", "created": time.time(), "created_text": time.strftime("%Y-%m-%d %H:%M:%S")}
    # Clean old pending pairings after 30 minutes.
    now = time.time()
    for key, item in list(pairings.items()):
        if now - float(item.get("created") or 0) > 1800 and item.get("status") == "pending":
            pairings.pop(key, None)
    save_telegram_pairings(pairings)
    start = f"nserver_{code}"
    url = f"https://t.me/{cfg['bot_username']}?start={start}"
    qr = "https://api.qrserver.com/v1/create-qr-code/?size=220x220&data=" + quote(url, safe="")
    return {"ok": True, "code": code, "url": url, "qr_url": qr, "bot_username": cfg["bot_username"]}


def telegram_pairing_status(username: str, code: str) -> dict:
    item = load_telegram_pairings().get(code) or {}
    if not item or item.get("username") != username:
        return {"ok": False, "status": "missing", "message": "Pareamento não encontrado ou expirado."}
    user = find_user(username) or {}
    connected = bool(user.get("telegram_chat_id")) or item.get("status") == "connected"
    return {"ok": True, "status": "connected" if connected else "pending", "telegram_chat_id": user.get("telegram_chat_id", ""), "message": "Telegram conectado." if connected else "Aguardando abrir o bot no Telegram."}


def telegram_real_config() -> dict:
    env = read_agent_env()
    api_id = os.environ.get("TELEGRAM_API_ID") or env.get("TELEGRAM_API_ID") or env.get("TG_API_ID") or ""
    api_hash = os.environ.get("TELEGRAM_API_HASH") or env.get("TELEGRAM_API_HASH") or env.get("TG_API_HASH") or ""
    bot_username = telegram_config().get("bot_username") or os.environ.get("NSERVER_TELEGRAM_BOT_USERNAME") or env.get("NSERVER_TELEGRAM_BOT_USERNAME") or "nserverlbot"
    return {"api_id": str(api_id).strip(), "api_hash": str(api_hash).strip(), "bot_username": str(bot_username).strip().lstrip("@")}


def ensure_telethon():
    try:
        import telethon  # noqa: F401
        return True, ""
    except Exception:
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "telethon>=1.36.0"], capture_output=True, text=True, timeout=300)
            import telethon  # noqa: F401
            return True, ""
        except Exception as exc:
            return False, f"Não consegui instalar/carregar Telethon: {exc}"


def telegram_real_session_path(username: str) -> Path:
    safe_user = re.sub(r"[^A-Za-z0-9._-]+", "-", username or "user")[:80]
    folder = TELEGRAM_REAL_DIR / safe_user
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "telegram"


def telegram_real_qr_url(raw_url: str) -> str:
    return "https://api.qrserver.com/v1/create-qr-code/?size=240x240&data=" + quote(raw_url, safe="")


def start_telegram_real_login(username: str) -> dict:
    cfg = telegram_real_config()
    if not cfg["api_id"] or not cfg["api_hash"]:
        return {"ok": False, "message": "Configure TELEGRAM_API_ID e TELEGRAM_API_HASH no .env do agente. Esses dados vêm de https://my.telegram.org/apps."}
    ok, err = ensure_telethon()
    if not ok:
        return {"ok": False, "message": err}
    if username in TELEGRAM_REAL_LOGINS and TELEGRAM_REAL_LOGINS[username].get("status") == "pending":
        item = TELEGRAM_REAL_LOGINS[username]
        return {"ok": True, "status": "pending", "url": item.get("url"), "qr_url": item.get("qr_url"), "message": "Login Telegram já iniciado."}

    TELEGRAM_REAL_LOGINS[username] = {"status": "starting", "created": time.time()}

    def worker():
        async def run():
            from telethon import TelegramClient
            session = str(telegram_real_session_path(username))
            client = TelegramClient(session, int(cfg["api_id"]), cfg["api_hash"])
            await client.connect()
            if await client.is_user_authorized():
                me = await client.get_me()
                await client.disconnect()
                TELEGRAM_REAL_LOGINS[username] = {"status": "connected", "message": f"Telegram já conectado como {getattr(me, 'username', '') or getattr(me, 'first_name', '')}."}
                return
            qr = await client.qr_login()
            TELEGRAM_REAL_LOGINS[username] = {"status": "pending", "url": qr.url, "qr_url": telegram_real_qr_url(qr.url), "created": time.time()}
            try:
                await qr.wait(timeout=180)
                me = await client.get_me()
                TELEGRAM_REAL_LOGINS[username] = {"status": "connected", "message": f"Telegram conectado como {getattr(me, 'username', '') or getattr(me, 'first_name', '')}."}
            except Exception as exc:
                TELEGRAM_REAL_LOGINS[username] = {"status": "error", "message": f"Login Telegram não concluído: {exc}"}
            finally:
                await client.disconnect()
        try:
            asyncio.run(run())
        except Exception as exc:
            TELEGRAM_REAL_LOGINS[username] = {"status": "error", "message": f"Falha no login real Telegram: {exc}"}

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "status": "starting", "message": "Gerando QR real do Telegram. Aguarde alguns segundos."}


def telegram_real_status(username: str) -> dict:
    cfg = telegram_real_config()
    ok, err = ensure_telethon()
    if not ok:
        return {"ok": False, "status": "error", "message": err}
    session = telegram_real_session_path(username)
    item = TELEGRAM_REAL_LOGINS.get(username) or {}
    if item.get("status") in {"pending", "starting", "error", "connected"}:
        return {"ok": item.get("status") != "error", **item}
    async def run():
        from telethon import TelegramClient
        client = TelegramClient(str(session), int(cfg["api_id"]), cfg["api_hash"])
        await client.connect()
        authorized = await client.is_user_authorized()
        me_name = ""
        if authorized:
            me = await client.get_me()
            me_name = getattr(me, "username", "") or getattr(me, "first_name", "") or "Telegram"
        await client.disconnect()
        return authorized, me_name
    try:
        authorized, me_name = asyncio.run(run())
        return {"ok": True, "status": "connected" if authorized else "disconnected", "message": f"Conectado como {me_name}." if authorized else "Telegram Real ainda não conectado."}
    except Exception as exc:
        return {"ok": False, "status": "error", "message": str(exc)}


def telegram_real_send_to_bot(username: str, text: str) -> tuple[bool, str]:
    cfg = telegram_real_config()
    if not cfg["api_id"] or not cfg["api_hash"]:
        return False, "Configure TELEGRAM_API_ID e TELEGRAM_API_HASH no .env."
    ok, err = ensure_telethon()
    if not ok:
        return False, err
    async def run():
        from telethon import TelegramClient
        client = TelegramClient(str(telegram_real_session_path(username)), int(cfg["api_id"]), cfg["api_hash"])
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return False, "Telegram Real não conectado para este usuário. Clique em Conectar Telegram Real."
        await client.send_message(cfg["bot_username"], text)
        await client.disconnect()
        return True, "Mensagem enviada pelo Telegram real do usuário."
    try:
        return asyncio.run(run())
    except Exception as exc:
        return False, f"Falha ao enviar pelo Telegram Real: {exc}"


def telegram_chat_id_for_user(username: str) -> str:
    user = find_user(username) or {}
    return str(user.get("telegram_chat_id") or "").strip()


def telegram_send_from_web(text: str, username: str) -> tuple[bool, str]:
    cfg = telegram_config()
    chat_id = telegram_chat_id_for_user(username)
    if not cfg["token"]:
        return False, "Telegram não configurado. Configure TELEGRAM_BOT_TOKEN no .env do agente."
    if not chat_id:
        return False, "Este usuário ainda não tem Telegram conectado. Peça ao administrador para preencher o Telegram chat ID no painel Usuários."
    msg = f"[Nserver/{username}] {text}"
    payload = urlencode({"chat_id": chat_id, "text": msg}).encode("utf-8")
    req = urllib.request.Request(f"https://api.telegram.org/bot{cfg['token']}/sendMessage", data=payload, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=45) as res:
            data = json.loads(res.read().decode("utf-8"))
        return bool(data.get("ok")), "Mensagem enviada para o Telegram." if data.get("ok") else str(data)
    except Exception as exc:
        return False, f"Falha ao enviar para Telegram: {exc}"


def agent_file_excerpt(path: Path, max_chars: int = 6000) -> str:
    if not path.exists() or not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-max_chars:]


def agent_brain_context(max_chars: int = 26000) -> str:
    chunks = []
    for rel in ["IDENTITY.md", "RULES.md", "HELP.md", "MEMORY.md", "LEARNING.md", "TASKS.md"]:
        path = AGENT_BRAIN / rel
        if path.exists():
            chunks.append(f"## {rel}\n{agent_file_excerpt(path, 5000)}")
    for folder in [AGENT_BRAIN / "knowledge", AGENT_BRAIN / "skills"]:
        if folder.exists():
            for md in sorted(folder.rglob("*.md"))[:30]:
                try:
                    chunks.append(f"## {md.relative_to(AGENT_BRAIN)}\n{agent_file_excerpt(md, 1800)}")
                except Exception:
                    pass
    out = "\n\n".join(chunks)
    return out[-max_chars:]


def agent_session_path(username: str, session_id: str) -> Path:
    safe_user = re.sub(r"[^A-Za-z0-9._-]+", "-", username or "user")[:80]
    folder = AGENT_WEB_SESSIONS / safe_user
    folder.mkdir(parents=True, exist_ok=True)
    safe_session = re.sub(r"[^A-Za-z0-9._-]+", "-", session_id or "default")[:100]
    return folder / f"{safe_session}.json"


def load_agent_session(username: str, session_id: str) -> dict:
    path = agent_session_path(username, session_id)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"id": session_id, "username": username, "created": time.strftime("%Y-%m-%d %H:%M:%S"), "messages": []}


def save_agent_session(session: dict):
    path = agent_session_path(session.get("username", "user"), session.get("id", "default"))
    session["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")


def list_agent_sessions(username: str) -> list[dict]:
    folder = agent_session_path(username, "dummy").parent
    items = []
    for path in sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            items.append({"id": data.get("id") or path.stem, "updated": data.get("updated") or data.get("created", ""), "count": len(data.get("messages") or [])})
        except Exception:
            pass
    return items


def telegram_journal_messages(limit: int = 60) -> list[dict]:
    journal = AGENT_BRAIN / "journal"
    if not journal.exists():
        return []
    messages = []
    for path in sorted(journal.glob("*.md"))[-7:]:
        text = path.read_text(encoding="utf-8", errors="replace")
        parts = re.split(r"\n## ", text)
        for part in parts:
            if "—" not in part:
                continue
            head, _, body = part.partition("\n\n")
            role = "agent" if "agente" in head.lower() else "user"
            messages.append({"role": role, "content": body.strip(), "time": head.strip()})
    return messages[-limit:]


def telegram_user_session(username: str) -> dict:
    session = load_agent_session(username, "telegram")
    session["id"] = "telegram"
    session["username"] = username
    session.setdefault("messages", [])
    return session


def web_extract_url(text: str) -> str:
    match = re.search(r"https?://\S+", text or "")
    return match.group(0).rstrip(".,;)\n\r\t") if match else ""


def parse_web_tool_request(text: str) -> dict | None:
    low = (text or "").lower()
    if '"ferramenta"' not in low and "“ferramenta”" not in low and "'ferramenta'" not in low:
        return None
    url = web_extract_url(text)
    if any(w in low for w in ["transcri", "legenda", "texto"]):
        formats = [fmt for fmt in ["txt", "md", "docx", "pdf"] if re.search(rf"\b{fmt}\b", low)] or ["txt", "md"]
        return {"tool": "transcription", "permission": "tool.transcription", "url": url, "formats": formats}
    if any(w in low for w in ["download", "baix", "video", "vídeo", "mp4", "audio", "áudio", "mp3"]):
        quality_match = re.search(r"(\d{3,4})\s*p", low)
        quality = quality_match.group(1) if quality_match else "720"
        if any(w in low for w in ["audio", "áudio", "mp3", "wav", "m4a"]):
            fmt = "wav" if "wav" in low else "m4a" if "m4a" in low else "mp3"
            return {"tool": "audio-download", "permission": "tool.video-downloader", "url": url, "format": fmt, "quality": "192"}
        fmt = "webm" if "webm" in low else "mov" if "mov" in low else "mp4"
        return {"tool": "video-download", "permission": "tool.video-downloader", "url": url, "quality": quality, "format": fmt}
    return {"tool": "unknown", "permission": "", "url": url}


def append_telegram_web_message(username: str, role: str, content: str):
    session = telegram_user_session(username)
    messages = session.setdefault("messages", [])
    messages.append({"role": role, "content": content, "time": time.strftime("%Y-%m-%d %H:%M:%S")})
    session["messages"] = messages[-200:]
    save_agent_session(session)


def run_web_tool_background(username: str, text: str):
    req = parse_web_tool_request(text)
    if not req:
        return
    try:
        fake_sess = {"username": username}
        if not has_permission(fake_sess, req.get("permission", "")):
            msg = "Você não tem permissão, consulte o administrador do Nserver."
            append_telegram_web_message(username, "agent", msg)
            telegram_send_from_web(msg, username)
            return
        if req.get("tool") == "unknown":
            msg = "Entendi que você quer usar uma ferramenta, mas não identifiquei qual. Diga: \"ferramenta\" video download ou \"ferramenta\" transcrição."
            append_telegram_web_message(username, "agent", msg)
            telegram_send_from_web(msg, username)
            return
        if not req.get("url"):
            msg = "Me envie o link junto com o comando da \"ferramenta\" para eu executar."
            append_telegram_web_message(username, "agent", msg)
            telegram_send_from_web(msg, username)
            return
        processor = PROCESSORS["video-downloader"]
        if req["tool"] == "video-download":
            result = processor.run({"action": "download_video", "url": req["url"], "quality": req.get("quality") or "720", "format": req.get("format") or "mp4", "destination": "server"})
            data = result.data or {}
            if result.ok and data.get("file"):
                rel = Path(data["file"]).resolve().relative_to(MIDIAS.resolve()).as_posix()
                set_library_owner(rel, username)
                msg = "Vídeo salvo na biblioteca."
            else:
                msg = result.message or "Não consegui salvar o vídeo."
        elif req["tool"] == "audio-download":
            result = processor.run({"action": "extract_audio", "url": req["url"], "format": req.get("format") or "mp3", "quality": req.get("quality") or "192", "destination": "server"})
            data = result.data or {}
            if result.ok and data.get("file"):
                rel = Path(data["file"]).resolve().relative_to(MIDIAS.resolve()).as_posix()
                set_library_owner(rel, username)
                msg = "Áudio salvo na biblioteca."
            else:
                msg = result.message or "Não consegui salvar o áudio."
        elif req["tool"] == "transcription":
            result = processor.run({"action": "transcribe", "url": req["url"], "formats": req.get("formats") or ["txt", "md"]})
            data = result.data or {}
            if result.ok:
                for value in data.values():
                    if isinstance(value, str):
                        p = Path(value)
                        if p.exists() and p.is_file() and MIDIAS.resolve() in p.resolve().parents:
                            set_library_owner(p.resolve().relative_to(MIDIAS.resolve()).as_posix(), username)
                msg = "Transcrição salva na biblioteca."
            else:
                msg = result.message or "Não consegui transcrever."
        else:
            msg = "Ferramenta ainda não disponível pelo painel Telegram."
        append_telegram_web_message(username, "agent", msg)
        telegram_send_from_web(msg, username)
    except Exception as exc:
        msg = f"A ferramenta falhou: {exc}"
        append_telegram_web_message(username, "agent", msg)
        try:
            telegram_send_from_web(msg, username)
        except Exception:
            pass



def nserver_ai_provider_config() -> dict:
    cfg = load_config()
    env = read_agent_env()
    provider = (os.environ.get("NSERVER_AI_PROVIDER") or cfg.get("ai_provider") or env.get("NSERVER_AI_PROVIDER") or "auto").strip().lower()
    gemini_key = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or cfg.get("gemini_api_key") or env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY") or "").strip()
    gemini_model = (os.environ.get("GEMINI_MODEL") or cfg.get("gemini_model") or env.get("GEMINI_MODEL") or "gemini-1.5-flash").strip()
    jobhunter_model = (os.environ.get("NSERVER_JOBHUNTER_MODEL") or cfg.get("jobhunter_model") or env.get("NSERVER_JOBHUNTER_MODEL") or "").strip()
    return {"provider": provider, "gemini_key": gemini_key, "gemini_model": gemini_model, "jobhunter_model": jobhunter_model}


def extract_json_object(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def nserver_ai_text(system: str, user: str, max_tokens: int = 1600, temperature: float = 0.2) -> str:
    ai = nserver_ai_provider_config()
    errors = []
    provider_order = []
    if ai["provider"] == "gemini":
        provider_order = ["gemini"]
    elif ai["provider"] == "openrouter":
        provider_order = ["openrouter"]
    else:
        provider_order = ["gemini", "openrouter"]

    if "gemini" in provider_order and ai.get("gemini_key"):
        model = ai.get("jobhunter_model") or ai.get("gemini_model") or "gemini-1.5-flash"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{quote(model, safe='-_.')}:generateContent?key={quote(ai['gemini_key'], safe='')}"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": f"{system}\n\n{user}"}]}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        }
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as res:
                data = json.loads(res.read().decode("utf-8"))
            parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
            text = "".join(str(p.get("text") or "") for p in parts).strip()
            if text:
                return text
            errors.append("gemini: resposta vazia")
        except Exception as exc:
            errors.append(f"gemini: {exc}")

    if "openrouter" in provider_order:
        cfg = openrouter_config()
        if cfg.get("key"):
            models = [ai.get("jobhunter_model")] if ai.get("jobhunter_model") else cfg.get("models", [])
            api_messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
            for model in [m for m in models if m]:
                payload = {"model": model, "messages": api_messages, "max_tokens": max_tokens, "temperature": temperature}
                req = urllib.request.Request(
                    "https://openrouter.ai/api/v1/chat/completions",
                    data=json.dumps(payload).encode("utf-8"),
                    method="POST",
                    headers={"Authorization": f"Bearer {cfg['key']}", "Content-Type": "application/json", "HTTP-Referer": "https://nserver.local", "X-Title": "Nserver JobHunter"},
                )
                try:
                    with urllib.request.urlopen(req, timeout=120) as res:
                        data = json.loads(res.read().decode("utf-8"))
                    text = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                    if text:
                        return text
                    errors.append(f"{model}: resposta vazia")
                except Exception as exc:
                    errors.append(f"{model}: {exc}")
        else:
            errors.append("openrouter: chave não configurada")
    return ""


def nserver_ai_json(system: str, user: str, max_tokens: int = 1600) -> dict | None:
    text = nserver_ai_text(system + "\nResponda somente JSON válido, sem markdown.", user, max_tokens=max_tokens, temperature=0.1)
    data = extract_json_object(text)
    if data is not None:
        data.setdefault("provider", nserver_ai_provider_config().get("provider") or "platform-ai")
    return data


def openrouter_agent_reply(messages: list[dict], mode: str) -> str:
    cfg = openrouter_config()
    if not cfg["key"]:
        return "OpenRouter não está configurado para o chat web. Configure OPENROUTER_API_KEY no .env do agente."
    system = (
        "Você é o agente do Nserver respondendo dentro do painel web. "
        "Responda em português, de forma prática. Respeite privacidade por sessão/usuário. "
        "Não afirme que alterou arquivos. Para alterações reais, oriente usar o agente Telegram/modo projeto."
    )
    user_context = f"Modo da sessão: {mode}\n\nCérebro disponível:\n{agent_brain_context()}"
    api_messages = [{"role": "system", "content": system}, {"role": "user", "content": user_context}]
    api_messages.extend(messages[-20:])
    errors = []
    for model in cfg["models"]:
        payload = {"model": model, "messages": api_messages, "max_tokens": 2200, "temperature": 0.2}
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Authorization": f"Bearer {cfg['key']}", "Content-Type": "application/json", "HTTP-Referer": "https://nserver.local", "X-Title": "Nserver Web Agent"},
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as res:
                data = json.loads(res.read().decode("utf-8"))
            return data.get("choices", [{}])[0].get("message", {}).get("content", "").strip() or "Sem resposta do modelo."
        except Exception as exc:
            errors.append(f"{model}: {exc}")
    return "Todos os modelos falharam no chat web:\n" + "\n".join(errors[-3:])


def password_hash(password: str) -> str:
    return hashlib.sha256((SALT + ":" + password).encode("utf-8")).hexdigest()


# hash de 52ar4ever
PASSWORD_SHA256 = password_hash("52ar4ever")


def load_users() -> dict:
    if USERS_FILE.exists():
        try:
            data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("users"), list):
                return data
        except Exception:
            pass
    data = {
        "users": [
            {
                "username": USERNAME,
                "password_sha256": PASSWORD_SHA256,
                "role": "admin",
                "permissions": ROLE_PERMISSIONS["admin"],
                "active": True,
                "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        ]
    }
    USERS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def save_users(data: dict):
    USERS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def find_user(username: str) -> dict | None:
    for user in load_users().get("users", []):
        if hmac.compare_digest(str(user.get("username", "")), username):
            return user
    return None


def public_user(user: dict) -> dict:
    return {
        "username": user.get("username", ""),
        "role": user.get("role", "usuario"),
        "permissions": user.get("permissions", []),
        "active": bool(user.get("active", True)),
        "telegram_chat_id": user.get("telegram_chat_id", ""),
        "created": user.get("created", ""),
    }


def has_permission(sess: dict | None, perm: str) -> bool:
    if not sess:
        return False
    user = find_user(sess.get("username", "")) or {}
    if not user.get("active", True):
        return False
    return perm in set(user.get("permissions") or [])


def tool_permission(tool_id: str) -> str:
    return f"tool.{tool_id}"


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
            <a href="/agent">Agente</a>
            <a href="/library">Biblioteca</a>
            <a href="/updates">Atualizações</a>
            <a href="/users">Usuários</a>
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
    .chat-layout {{ display:grid; grid-template-columns:260px 1fr; gap:16px; }}
    .chat-sidebar {{ display:grid; gap:10px; align-content:start; }}
    .chat-box {{ height:58vh; overflow:auto; padding:14px; border:1px solid #263244; border-radius:16px; background:#070b12; }}
    .msg {{ margin:0 0 12px; padding:11px 13px; border-radius:14px; white-space:pre-wrap; }}
    .msg.user {{ background:#1d2b5f; margin-left:12%; }}
    .msg.agent {{ background:#111827; margin-right:12%; }}
    textarea {{ width:100%; min-height:92px; padding:13px 14px; border-radius:13px; border:1px solid #334155; background:#080a10; color:var(--text); font-size:15px; outline:none; resize:vertical; }}
    .mode-btn {{ width:100%; justify-content:flex-start; margin-top:0; background:#1f2937; border:1px solid #374151; }}
    @media (max-width:720px) {{ .video-info,.history-item,.media-item {{ grid-template-columns:1fr; }} .thumb {{ width:100%; height:160px; }} }}
    @media (max-width:860px) {{ .chat-layout {{ grid-template-columns:1fr; }} }}
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

    def require_permission(self, perm: str) -> dict | None:
        sess = self.require_login()
        if not sess:
            return None
        if not has_permission(sess, perm):
            self.send_html(html_page("Acesso negado", """
            <main class="wrap"><section class="card">
              <h1>Acesso negado</h1>
              <p>Seu usuário não tem permissão para acessar esta área.</p>
              <a class="button" href="/dashboard">Voltar</a>
            </section></main>
            """, authenticated=True), 403)
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
                <h1>Bem-vindo, {html.escape(str(sess.get('username', USERNAME)))}.</h1>
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
            sess = self.require_permission("dashboard")
            if not sess: return
            visible_tools = [tool for tool in TOOLS if has_permission(sess, tool_permission(tool["id"]))]
            cards = "".join(f"""
              <a class="card tool" href="{tool['href']}">
                <div>
                  <div class="status">{tool['status']}</div>
                  <h2>{tool['name']}</h2>
                  <p>{tool['description']}</p>
                </div>
                <span class="muted">Abrir →</span>
              </a>
            """ for tool in visible_tools) or "<p class='muted'>Nenhuma ferramenta liberada para este usuário.</p>"
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
        if path == "/tool/jobhunter":
            sess = self.require_permission("tool.jobhunter")
            if not sess: return
            body = html_page("JobHunter AI", """
            <main class="wrap">
              <section class="card">
                <h1>⚡ JobHunter AI</h1>
                <p>Módulo multiusuário para perfil profissional, captura de vagas reais, match inteligente e cartas de apresentação. Toda ação passa pelo backend do Nserver.</p>
                <div class="stats" id="jhStats"></div>
                <div id="aiStatus" class="result muted">Verificando IA da plataforma...</div>
              </section>

              <section class="card" style="margin-top:18px">
                <h2>1. Perfil profissional</h2>
                <div class="row">
                  <div><label>Nome</label><input id="jhName" placeholder="Seu nome" /></div>
                  <div><label>Email</label><input id="jhEmail" placeholder="email@exemplo.com" /></div>
                </div>
                <div class="row">
                  <div><label>Headline / cargo alvo</label><input id="jhHeadline" placeholder="AI Automation Specialist | n8n | SDR" /></div>
                  <div><label>Nível</label><select id="jhLevel"><option>Júnior</option><option>Pleno</option><option>Sênior</option><option selected>Especialista</option></select></div>
                </div>
                <label>Bio / resumo profissional</label><textarea id="jhBio" placeholder="Resumo do perfil, resultados e principais experiências..."></textarea>
                <div class="row">
                  <div><label>LinkedIn</label><input id="jhLinkedin" placeholder="https://linkedin.com/in/..." /></div>
                  <div><label>GitHub</label><input id="jhGithub" placeholder="https://github.com/..." /></div>
                  <div><label>Portfólio</label><input id="jhPortfolio" placeholder="https://..." /></div>
                </div>
                <button onclick="saveProfile()">Salvar perfil</button>
                <span class="mini" id="profileStatus"></span>
              </section>

              <section class="card" style="margin-top:18px">
                <h2>2. Currículo e preferências</h2>
                <div class="row">
                  <div><label>Currículo PDF/DOCX/TXT</label><input id="resumeFile" type="file" accept=".pdf,.doc,.docx,.txt,.md" /></div>
                  <div><button onclick="uploadResume()">Enviar e extrair</button></div>
                </div>
                <div class="row">
                  <label class="mini"><input id="prefRemote" type="checkbox" checked /> Remoto</label>
                  <label class="mini"><input id="prefHybrid" type="checkbox" /> Híbrido</label>
                  <label class="mini"><input id="prefOnsite" type="checkbox" /> Presencial</label>
                  <label class="mini"><input id="prefClt" type="checkbox" /> CLT</label>
                  <label class="mini"><input id="prefPj" type="checkbox" checked /> PJ</label>
                  <label class="mini"><input id="prefFreelance" type="checkbox" checked /> Freelancer</label>
                </div>
                <div class="row">
                  <div><label>Salário mínimo</label><input id="prefSalary" type="number" placeholder="10000" /></div>
                  <div><label>Moeda</label><input id="prefCurrency" placeholder="BRL, USD..." /></div>
                  <div><label>Países desejados</label><input id="prefCountries" placeholder="Brasil, EUA, Portugal" /></div>
                  <div><label>Idiomas aceitos</label><input id="prefLanguages" placeholder="Português, Inglês" /></div>
                </div>
                <button onclick="savePrefs()">Salvar preferências</button>
                <span class="mini" id="resumeStatus"></span>
              </section>

              <section class="card" style="margin-top:18px">
                <h2>3. Vagas reais + match</h2>
                <div class="row">
                  <button onclick="ingestJobs()">Buscar Remotive + RemoteOK</button>
                  <button class="button secondary" onclick="matchJobs()">Recalcular matches</button>
                  <input id="jobSearch" oninput="renderJobs()" placeholder="Filtrar por título, empresa, skill..." />
                </div>
                <div id="jobsList" class="media-list"></div>
              </section>

              <section class="card" style="margin-top:18px">
                <h2>4. Agente JobHunter</h2>
                <p class="mini">Funções: analisar currículo, melhorar LinkedIn, sugerir skills, criar carta, simular entrevistas e montar planejamento de carreira.</p>
                <div class="row">
                  <button onclick="careerPlan()">Planejamento de carreira</button>
                  <button class="button secondary" onclick="simulateInterview()">Simular entrevista geral</button>
                </div>
                <div id="detailBox" class="result muted">Abra uma vaga para ver análise, gaps e carta; ou use o agente para entrevista/plano.</div>
              </section>
            </main>
<script>
let JH={profile:null,preferences:null,resume:null,jobs:[],matches:[],applications:[]};
function esc(v){return String(v??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));}
async function jhApi(payload){const res=await fetch('/api/jobhunter',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});return await res.json();}
async function loadJobHunter(){const data=await jhApi({action:'load'}); if(!data.ok){alert(data.message);return;} JH=data.data; fillProfile(); renderStats(); renderJobs(); loadAiStatus();}
async function loadAiStatus(){const d=await jhApi({action:'ai_status'}); if(!d.ok)return; const x=d.data; aiStatus.textContent=`IA backend: ${x.provider} | Gemini: ${x.gemini_configured?'configurado':'não'} | OpenRouter: ${x.openrouter_configured?'configurado':'não'} | Modelo JobHunter: ${x.jobhunter_model||'padrão da plataforma'} | Fallback local: ativo`;}
function fillProfile(){const p=JH.profile||{}, pref=JH.preferences||{}; jhName.value=p.name||''; jhEmail.value=p.email||''; jhHeadline.value=p.headline||''; jhLevel.value=p.experience_level||'Especialista'; jhBio.value=p.bio||''; jhLinkedin.value=p.linkedin_url||''; jhGithub.value=p.github_url||''; jhPortfolio.value=p.portfolio_url||''; prefRemote.checked=!!pref.remote; prefHybrid.checked=!!pref.hybrid; prefOnsite.checked=!!pref.onsite; prefClt.checked=!!pref.clt; prefPj.checked=!!pref.pj; prefFreelance.checked=!!pref.freelance; prefSalary.value=pref.salary_min||''; prefCurrency.value=pref.currency||''; prefCountries.value=(pref.countries||[]).join(', '); prefLanguages.value=(pref.languages||[]).join(', ');}
function renderStats(){const today=new Date().toISOString().slice(0,10); const apps=JH.applications||[]; const jobs=JH.jobs||[]; const interviews=apps.filter(a=>a.status==='interview').length; const hired=apps.filter(a=>a.status==='hired').length; jhStats.innerHTML=[['Total vagas',jobs.length],['Novas hoje',jobs.filter(j=>(j.created_at||'').slice(0,10)===today).length],['Aplicadas',apps.filter(a=>a.status==='applied').length],['Entrevistas',interviews],['Contratações',hired]].map(x=>`<div class="stat"><div class="mini">${x[0]}</div><h2>${x[1]}</h2></div>`).join('');}
async function saveProfile(){const payload={action:'save_profile',name:jhName.value,email:jhEmail.value,headline:jhHeadline.value,experience_level:jhLevel.value,bio:jhBio.value,linkedin_url:jhLinkedin.value,github_url:jhGithub.value,portfolio_url:jhPortfolio.value}; const d=await jhApi(payload); profileStatus.textContent=d.message; await loadJobHunter();}
async function savePrefs(){const arr=v=>String(v||'').split(',').map(x=>x.trim()).filter(Boolean); const payload={action:'save_preferences',remote:prefRemote.checked,hybrid:prefHybrid.checked,onsite:prefOnsite.checked,clt:prefClt.checked,pj:prefPj.checked,freelance:prefFreelance.checked,salary_min:prefSalary.value,currency:prefCurrency.value,countries:arr(prefCountries.value),languages:arr(prefLanguages.value)}; const d=await jhApi(payload); resumeStatus.textContent=d.message; await loadJobHunter();}
async function uploadResume(){const input=document.getElementById('resumeFile'); if(!input.files.length){alert('Escolha um arquivo.');return;} const fd=new FormData(); fd.append('file',input.files[0]); fd.append('action','upload_resume'); const res=await fetch('/api/jobhunter',{method:'POST',body:fd}); const d=await res.json(); resumeStatus.textContent=d.message; await loadJobHunter();}
async function ingestJobs(){jobsList.innerHTML='<p class="muted">Buscando vagas...</p>'; const d=await jhApi({action:'ingest_jobs'}); alert(d.message); await loadJobHunter();}
async function matchJobs(){jobsList.innerHTML='<p class="muted">Calculando matches no backend...</p>'; const d=await jhApi({action:'match_jobs'}); alert(d.message); await loadJobHunter();}
function matchFor(job){return (JH.matches||[]).find(m=>m.job_id===job.id)||{};}
function appFor(job){return (JH.applications||[]).find(a=>a.job_id===job.id)||{};}
function renderJobs(){const q=(jobSearch?.value||'').toLowerCase(); const rows=(JH.jobs||[]).filter(j=>`${j.title} ${j.company} ${j.description}`.toLowerCase().includes(q)).map(j=>({j,m:matchFor(j)})).sort((a,b)=>(b.m.score||0)-(a.m.score||0)); jobsList.innerHTML=rows.map(({j,m})=>`<div class="media-item"><div><div class="pill">${esc(j.source)}</div><h2>${m.score||0}%</h2></div><div><h3>${esc(j.title)}</h3><p class="mini"><b>${esc(j.company)}</b> · ${esc(j.location)}<br>${esc(j.salary||'Salário não informado')} · ${esc(j.posted_at||'')}</p><p class="mini">${esc((j.description||'').slice(0,240))}...</p><div class="item-actions"><button onclick="openJob('${esc(j.id)}')">Análise + carta</button><a class="button secondary" href="${esc(j.url)}" target="_blank">Abrir vaga</a><button onclick="setStatus('${esc(j.id)}','applied')">Marcar aplicada</button></div></div></div>`).join('')||'<p class="muted">Nenhuma vaga. Clique em buscar.</p>';}
async function openJob(id){const d=await jhApi({action:'job_detail',job_id:id}); if(!d.ok){alert(d.message);return;} const x=d.data; detailBox.className='result'; detailBox.innerHTML=`<h2>${esc(x.job.title)}</h2><p><b>Score:</b> ${x.match.score}%</p><div class="item-actions"><button onclick="simulateInterview('${esc(x.job.id)}')">Simular entrevista desta vaga</button></div><h3>Análise</h3><ul>${(x.match.analysis||[]).map(v=>`<li>${esc(v)}</li>`).join('')}</ul><h3>Gaps</h3><ul>${(x.match.gaps||[]).map(v=>`<li>${esc(v)}</li>`).join('')||'<li>Nenhum gap claro.</li>'}</ul><h3>Recomendações</h3><ul>${(x.match.recommendations||[]).map(v=>`<li>${esc(v)}</li>`).join('')}</ul><h3>Carta</h3><pre style="white-space:pre-wrap">${esc(x.cover_letter)}</pre>`; detailBox.scrollIntoView({behavior:'smooth'});}
async function simulateInterview(jobId=''){const d=await jhApi({action:'simulate_interview',job_id:jobId}); if(!d.ok){alert(d.message);return;} const x=d.data.simulation; detailBox.className='result'; detailBox.innerHTML=`<h2>Simulação de entrevista — ${esc(x.target)}</h2><h3>Perguntas prováveis</h3><ol>${(x.questions||[]).map(v=>`<li>${esc(v)}</li>`).join('')}</ol><h3>Como responder melhor</h3><ul>${(x.guidance||[]).map(v=>`<li>${esc(v)}</li>`).join('')}</ul>`; detailBox.scrollIntoView({behavior:'smooth'});}
async function careerPlan(){const d=await jhApi({action:'career_plan'}); if(!d.ok){alert(d.message);return;} const x=d.data.plan; const list=a=>(a||[]).map(v=>`<li>${esc(v)}</li>`).join(''); detailBox.className='result'; detailBox.innerHTML=`<h2>Planejamento de carreira</h2><p><b>Posicionamento:</b> ${esc(x.positioning)}</p><h3>Próximos 30 dias</h3><ul>${list(x.next_30_days)}</ul><h3>Próximos 60 dias</h3><ul>${list(x.next_60_days)}</ul><h3>Próximos 90 dias</h3><ul>${list(x.next_90_days)}</ul><h3>Gaps de skills</h3><ul>${list(x.skill_gaps)}</ul><h3>Foco recomendado</h3><ul>${list(x.recommended_focus)}</ul>`; detailBox.scrollIntoView({behavior:'smooth'});}
async function setStatus(id,status){const d=await jhApi({action:'application_status',job_id:id,status}); alert(d.message); await loadJobHunter();}
loadJobHunter();
</script>
            """, authenticated=True)
            self.send_html(body)
            return
        if path == "/tool/video-downloader":
            sess = self.require_permission("tool.video-downloader")
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
            is_transcription = path == "/tool/transcription"
            sess = self.require_permission("tool.transcription" if is_transcription else "tool.viral-clips")
            if not sess: return
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
            sess = self.require_permission("tool.course-ingest")
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
            sess = self.require_permission("tool.video-editor")
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
        if path == "/users":
            sess = self.require_permission("users_manage")
            if not sess: return
            perms_html = "".join(f"<label class='mini'><input type='checkbox' class='perm' value='{html.escape(k)}'> {html.escape(v)}</label>" for k, v in PERMISSIONS.items())
            roles_js = json.dumps(ROLE_PERMISSIONS, ensure_ascii=False)
            body = html_page("Usuários", f"""
            <main class="wrap">
              <section class="card">
                <h1>Gerenciar usuários</h1>
                <p>Crie usuários, defina função e escolha permissões por ferramenta/função.</p>
                <div id="usersList" class="history-list"></div>
              </section>
              <section class="card" style="margin-top:18px">
                <h2>Novo/editar usuário</h2>
                <div class="row">
                  <div><label>Usuário</label><input id="uName" placeholder="nome" /></div>
                  <div><label>Senha nova</label><input id="uPass" type="password" placeholder="deixe vazio para manter" /></div>
                  <div><label>Função</label><select id="uRole" onchange="applyRolePerms()"><option value="usuario">usuario</option><option value="colaborador">colaborador</option><option value="admin">admin</option></select></div>
                  <div><label>Telegram chat ID</label><input id="uTelegram" placeholder="ex: 8913245353" /></div>
                </div>
                <label><input type="checkbox" id="uActive" checked /> Usuário ativo</label>
                <h3>Permissões</h3>
                <div class="grid">{perms_html}</div>
                <button onclick="saveUser()">Salvar usuário</button>
                <button class="secondary" onclick="clearUserForm()">Novo usuário / limpar formulário</button>
                <div id="userMsg" class="result muted">Aguardando.</div>
              </section>
            </main>
            <script>
            const ROLE_PERMS={roles_js};
            let USERS=[];
            async function usersApi(payload){{const res=await fetch('/api/users',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload)}});return await res.json();}}
            function esc(s){{return String(s||'').replace(/[&<>"']/g,m=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[m]));}}
            function getPerms(){{return [...document.querySelectorAll('.perm:checked')].map(x=>x.value);}}
            function setPerms(perms){{document.querySelectorAll('.perm').forEach(x=>x.checked=(perms||[]).includes(x.value));}}
            function applyRolePerms(){{setPerms(ROLE_PERMS[document.getElementById('uRole').value]||[]);}}
            function clearUserForm(){{document.getElementById('uName').value=''; document.getElementById('uName').disabled=false; document.getElementById('uPass').value=''; document.getElementById('uRole').value='usuario'; document.getElementById('uTelegram').value=''; document.getElementById('uActive').checked=true; applyRolePerms();}}
            function editUserIndex(i){{const u=USERS[i]; if(!u)return; document.getElementById('uName').value=u.username; document.getElementById('uName').disabled=false; document.getElementById('uPass').value=''; document.getElementById('uRole').value=u.role||'usuario'; document.getElementById('uTelegram').value=u.telegram_chat_id||''; document.getElementById('uActive').checked=!!u.active; setPerms(u.permissions||[]); document.getElementById('userMsg').textContent='Editando permissões de '+u.username+'. Deixe a senha vazia para manter.'; window.scrollTo({{top:document.body.scrollHeight,behavior:'smooth'}});}}
            async function loadUsers(){{const data=await usersApi({{action:'list'}}); USERS=(data.data&&data.data.users)||[]; document.getElementById('usersList').innerHTML=USERS.map((u,i)=>`<div class="history-item"><div class="thumb"></div><div><h3>${{esc(u.username)}}</h3><p class="mini"><b>Função:</b> ${{esc(u.role)}}<br><b>Ativo:</b> ${{u.active?'sim':'não'}}<br><b>Telegram:</b> ${{u.telegram_chat_id?esc(u.telegram_chat_id):'não conectado'}}<br><b>Permissões:</b> ${{(u.permissions||[]).map(esc).join(', ')}}</p><button onclick="editUserIndex(${{i}})">Editar permissões</button><button class="danger-btn" onclick="deleteUser(${{i}})">Remover</button></div></div>`).join('');}}
            async function saveUser(){{const payload={{action:'save',username:document.getElementById('uName').value.trim(),password:document.getElementById('uPass').value,role:document.getElementById('uRole').value,active:document.getElementById('uActive').checked,telegram_chat_id:document.getElementById('uTelegram').value.trim(),permissions:getPerms()}}; const data=await usersApi(payload); document.getElementById('userMsg').textContent=data.message; if(data.ok)clearUserForm(); await loadUsers();}}
            async function deleteUser(i){{const u=USERS[i]; if(!u)return; if(!confirm('Remover usuário '+u.username+'?'))return; const data=await usersApi({{action:'delete',username:u.username}}); document.getElementById('userMsg').textContent=data.message; await loadUsers();}}
            applyRolePerms(); loadUsers();
            </script>
            """, authenticated=True)
            self.send_html(body); return
        if path == "/agent":
            sess = self.require_permission("agent_chat")
            if not sess: return
            body = html_page("Agente", f"""
            <main class="wrap">
              <section class="card">
                <h1>Agente Nserver</h1>
                <p>Chat do agente dentro do painel. Escolha o tipo de sessão antes de conversar.</p>
                <div class="chat-layout">
                  <aside class="chat-sidebar">
                    <button class="mode-btn" onclick="setMode('nserver')">Sessão Nserver</button>
                    <button class="mode-btn" onclick="setMode('telegram')">Sessão Telegram</button>
                    <button class="mode-btn" onclick="newSession()">Nova sessão Nserver</button>
                    <button class="mode-btn" onclick="loadSessions()">Atualizar sessões</button>
                    <div class="result mini" id="sessionInfo">Carregando...</div>
                    <div class="mini" id="sessionList"></div>
                  </aside>
                  <section>
                    <div id="chatBox" class="chat-box"></div>
                    <textarea id="chatInput" placeholder="Escreva sua mensagem..."></textarea>
                    <div class="row">
                      <button onclick="sendAgentMessage()">Enviar</button>
                      <button class="button secondary" onclick="loadCurrent()">Recarregar</button>
                      <button class="button secondary" onclick="connectTelegramReal()">Conectar Telegram Real</button>
                      <button class="button secondary" onclick="connectTelegram()">Conectar Bot (simples)</button>
                    </div>
                    <div id="telegramPair" class="result mini" style="display:none"></div>
                    <p class="mini">Privacidade: sessões Nserver ficam separadas por usuário. No modo Telegram, o painel usa apenas o Telegram chat ID configurado para o usuário logado.</p>
                  </section>
                </div>
              </section>
            </main>
            <script>
            let mode='nserver';
            let sessionId='default';
            function esc(s){{return String(s||'').replace(/[&<>"']/g,m=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[m]));}}
            function render(items){{
              const box=document.getElementById('chatBox');
              box.innerHTML=(items||[]).map(m=>`<div class="msg ${{m.role==='user'?'user':'agent'}}"><b>${{m.role==='user'?'Você':'Agente'}}</b><br>${{esc(m.content)}}</div>`).join('')||'<p class="muted">Sem mensagens nesta sessão.</p>';
              box.scrollTop=box.scrollHeight;
              document.getElementById('sessionInfo').textContent=`Modo: ${{mode}} | sessão: ${{sessionId}}`;
            }}
            async function agentApi(payload){{const res=await fetch('/api/agent',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload)}});return await res.json();}}
            async function loadCurrent(){{const data=await agentApi({{action:'load',mode,session_id:sessionId}}); render(data.data?.messages||[]);}}
            async function loadSessions(){{const data=await agentApi({{action:'sessions'}}); const box=document.getElementById('sessionList'); box.innerHTML=(data.data?.sessions||[]).map(s=>`<div><a href="#" onclick="sessionId='${{esc(s.id)}}';mode='nserver';loadCurrent();return false;">${{esc(s.id)}}</a><br><span class="mini">${{esc(s.updated)}} • ${{s.count}} msg</span></div>`).join('<hr>')||'<p class="mini">Nenhuma sessão salva.</p>';}}
            function setMode(m){{mode=m; if(m==='telegram')sessionId='telegram'; if(m==='nserver'&&sessionId==='telegram')sessionId='default'; document.getElementById('chatInput').disabled=false; document.getElementById('chatInput').placeholder=m==='telegram'?'Responder no Telegram via painel...':'Escreva sua mensagem...'; loadCurrent();}}
            function newSession(){{mode='nserver'; sessionId='web-'+Date.now(); render([]); loadSessions();}}
            async function sendAgentMessage(){{const input=document.getElementById('chatInput'); const text=input.value.trim(); if(!text)return; input.value=''; render([...(document.querySelectorAll('.msg')).length?[]:[], {{role:'user',content:text}}, {{role:'agent',content:'Pensando...'}}]); const data=await agentApi({{action:'send',mode,session_id:sessionId,message:text}}); render(data.data?.messages||[{{role:'agent',content:data.message||'Erro'}}]); loadSessions();}}
            let pairCode='';
            async function connectTelegram(){{const box=document.getElementById('telegramPair'); box.style.display='block'; box.innerHTML='Gerando link seguro...'; const data=await agentApi({{action:'telegram_pair_start',mode:'telegram'}}); if(!data.ok){{box.textContent=data.message||'Não consegui iniciar conexão.';return;}} pairCode=data.data.code; box.innerHTML=`<b>Conectar Bot simples</b><br>Abra este link ou escaneie o QR:<br><a href="${{esc(data.data.url)}}" target="_blank">${{esc(data.data.url)}}</a><br><br><img src="${{esc(data.data.qr_url)}}" style="width:220px;height:220px;background:white;padding:8px;border-radius:12px"><br><br>Este modo conecta chat_id do bot. Para mensagem sair como usuário, use Telegram Real.`;}}
            async function connectTelegramReal(){{const box=document.getElementById('telegramPair'); box.style.display='block'; box.innerHTML='Iniciando login real do Telegram...'; const data=await agentApi({{action:'telegram_real_start',mode:'telegram'}}); box.innerHTML=data.message||'Aguardando QR...'; setTimeout(checkReal,2000);}}
            async function checkReal(){{const box=document.getElementById('telegramPair'); const data=await agentApi({{action:'telegram_real_status',mode:'telegram'}}); if(data.data?.url){{box.innerHTML=`<b>Telegram Real</b><br>Escaneie este QR no Telegram do celular:<br><br><img src="${{esc(data.data.qr_url)}}" style="width:240px;height:240px;background:white;padding:8px;border-radius:12px"><br><br><a href="${{esc(data.data.url)}}" target="_blank">Abrir link</a><br><br>Status: ${{esc(data.data.status)}}`;}} else {{box.innerHTML=esc(data.message||data.data?.message||'Status carregado.');}} if(data.data?.status==='pending'||data.data?.status==='starting')setTimeout(checkReal,3000); if(data.data?.status==='connected')loadCurrent();}}
            async function checkPair(){{if(!pairCode)return; const data=await agentApi({{action:'telegram_pair_status',mode:'telegram',code:pairCode}}); if(data.ok&&data.data?.status==='connected'){{document.getElementById('telegramPair').innerHTML='Bot simples conectado ✅'; pairCode=''; loadCurrent();}}}}
            setInterval(()=>{{ if(mode==='telegram'){{ loadCurrent(); checkPair(); }} }}, 5000);
            loadCurrent(); loadSessions();
            </script>
            """, authenticated=True)
            self.send_html(body); return
        if path == "/library":
            sess = self.require_permission("library")
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
async function shareMedia(relative){const all=confirm('Compartilhar este arquivo com TODOS os usuários?\\n\\nOK = todos\\nCancelar = escolher usuários manualmente'); let users=[]; let shared_all=all; if(!all){const txt=prompt('Digite usuários separados por vírgula. Deixe vazio para remover compartilhamento.',''); users=(txt||'').split(',').map(x=>x.trim()).filter(Boolean);} const data=await api({action:'share',relative,shared_all,users}); alert(data.message||'Resposta recebida.'); await loadLibrary();}
function renderLibrary(){const q=(document.getElementById('q').value||'').toLowerCase(); const list=ITEMS.filter(x=>`${x.name} ${x.kind} ${x.relative} ${x.owner||''}`.toLowerCase().includes(q)); document.getElementById('items').innerHTML=list.map(x=>`<div class="media-item"><div>${x.is_video?`<video class="thumb" src="${esc(x.play_url)}"></video>`:'<div class="thumb"></div>'}</div><div><h3>${esc(x.name)}</h3><p class="mini"><b>Tipo:</b> ${esc(x.kind)}<br><b>Tamanho:</b> ${esc(x.size)}<br><b>Dono:</b> ${esc(x.owner||'-')} ${x.shared_all?'• compartilhado com todos':''}${(x.shared_with||[]).length?' • compartilhado: '+esc((x.shared_with||[]).join(', ')):''}<br><b>Local:</b> ${esc(x.relative)}</p><div class="item-actions">${x.play_url?`<a class="button secondary" href="${esc(x.play_url)}" target="_blank">Abrir</a>`:''}<a class="button" href="${esc(x.download_url)}">Baixar</a><button class="secondary" onclick="shareMedia(decodeURIComponent('${encodeURIComponent(x.relative)}'))">Compartilhar</button><button class="danger-btn" onclick="deleteMedia(decodeURIComponent('${encodeURIComponent(x.relative)}'),decodeURIComponent('${encodeURIComponent(x.name)}'))">Remover</button></div></div></div>`).join('')||'<p class="muted">Nenhum arquivo encontrado para este usuário.</p>';}
loadLibrary();
</script>
            """, authenticated=True)
            self.send_html(body)
            return
        if path == "/updates":
            sess = self.require_permission("updates")
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
            sess = self.require_permission("tool.course-ingest")
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
        if path == "/api/jobhunter":
            sess = self.require_permission("tool.jobhunter")
            if not sess: return
            user_id = current_user_id(sess)
            length = int(self.headers.get("Content-Length", "0"))
            ctype = self.headers.get("Content-Type", "")
            try:
                data = load_jobhunter()
                if ctype.startswith("multipart/form-data"):
                    filename, raw = self.parse_multipart_upload(length)
                    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename or "resume.bin")[:120]
                    dest = JOBHUNTER_RESUMES / f"{user_id}-{int(time.time())}-{safe}"
                    dest.write_bytes(raw)
                    text = ""
                    if dest.suffix.lower() in {".txt", ".md", ".csv"}:
                        text = raw.decode("utf-8", errors="replace")
                    else:
                        text = f"Arquivo {filename} recebido. Parser profundo de PDF/DOCX será ligado na próxima etapa com biblioteca dedicada/IA."
                    parsed = jh_parse_resume_text(text)
                    item = {"id": secrets.token_urlsafe(12), "user_id": user_id, "file_url": str(dest.relative_to(ROOT)), "filename": filename, "parsed_content": parsed, "created_at": jh_now()}
                    data.setdefault("resume_files", []).append(item)
                    save_jobhunter(data)
                    self.send_json({"ok": True, "message": "Currículo salvo e parser inicial executado.", "data": {"resume": item}}); return
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                except Exception:
                    self.send_json({"ok": False, "message": "JSON inválido."}, 400); return
                action = payload.get("action", "load")
                if action == "load":
                    profile = jh_latest(data.get("professional_profiles", []), user_id)
                    prefs = jh_latest(data.get("job_preferences", []), user_id)
                    resume = jh_latest(data.get("resume_files", []), user_id)
                    matches = [m for m in data.get("job_matches", []) if m.get("user_id") == user_id]
                    apps = [a for a in data.get("applications", []) if a.get("user_id") == user_id]
                    self.send_json({"ok": True, "message": "JobHunter carregado.", "data": {"profile": profile, "preferences": prefs, "resume": resume, "jobs": data.get("jobs", []), "matches": matches, "applications": apps}}); return
                if action == "ai_status":
                    ai = nserver_ai_provider_config()
                    or_cfg = openrouter_config()
                    self.send_json({"ok": True, "message": "Status de IA carregado.", "data": {"provider": ai.get("provider") or "auto", "gemini_configured": bool(ai.get("gemini_key")), "openrouter_configured": bool(or_cfg.get("key")), "gemini_model": ai.get("gemini_model"), "jobhunter_model": ai.get("jobhunter_model")}}); return
                if action == "save_profile":
                    profile = jh_upsert_user_record(data, "professional_profiles", user_id, {
                        "name": str(payload.get("name") or "").strip(), "email": str(payload.get("email") or "").strip(),
                        "headline": str(payload.get("headline") or "").strip(), "bio": str(payload.get("bio") or "").strip(),
                        "experience_level": str(payload.get("experience_level") or "").strip(), "location": str(payload.get("location") or "").strip(),
                        "linkedin_url": str(payload.get("linkedin_url") or "").strip(), "github_url": str(payload.get("github_url") or "").strip(),
                        "portfolio_url": str(payload.get("portfolio_url") or "").strip(), "website_url": str(payload.get("website_url") or "").strip(),
                        "behance_url": str(payload.get("behance_url") or "").strip(), "dribbble_url": str(payload.get("dribbble_url") or "").strip(),
                    })
                    save_jobhunter(data)
                    self.send_json({"ok": True, "message": "Perfil profissional salvo.", "data": {"profile": profile}}); return
                if action == "save_preferences":
                    prefs = jh_upsert_user_record(data, "job_preferences", user_id, {
                        "remote": bool(payload.get("remote")), "hybrid": bool(payload.get("hybrid")), "onsite": bool(payload.get("onsite")),
                        "clt": bool(payload.get("clt")), "pj": bool(payload.get("pj")), "freelance": bool(payload.get("freelance")),
                        "salary_min": payload.get("salary_min") or "", "currency": str(payload.get("currency") or "").strip(),
                        "countries": payload.get("countries") or [], "languages": payload.get("languages") or [],
                    })
                    save_jobhunter(data)
                    self.send_json({"ok": True, "message": "Preferências salvas.", "data": {"preferences": prefs}}); return
                if action == "ingest_jobs":
                    collected = []
                    errors = []
                    for name, fn in [("Remotive", jh_ingest_remotive), ("RemoteOK", jh_ingest_remoteok)]:
                        try:
                            collected.extend(fn(30))
                        except Exception as exc:
                            errors.append(f"{name}: {exc}")
                    added = 0
                    jobs = data.setdefault("jobs", [])
                    for job in collected:
                        if job.get("title") and not jh_job_exists(jobs, job):
                            jobs.append(job); added += 1
                    save_jobhunter(data)
                    msg = f"{added} vaga(s) nova(s) salvas. Total: {len(jobs)}."
                    if errors: msg += " Avisos: " + " | ".join(errors[:2])
                    self.send_json({"ok": True, "message": msg, "data": {"added": added, "total": len(jobs), "errors": errors}}); return
                if action == "match_jobs":
                    count = 0
                    for job in data.get("jobs", []):
                        jh_match_job(data, user_id, job); count += 1
                    save_jobhunter(data)
                    self.send_json({"ok": True, "message": f"{count} match(es) calculados no backend.", "data": {"count": count}}); return
                if action == "job_detail":
                    job_id = str(payload.get("job_id") or "")
                    job = next((j for j in data.get("jobs", []) if j.get("id") == job_id), None)
                    if not job:
                        self.send_json({"ok": False, "message": "Vaga não encontrada."}, 404); return
                    match = jh_match_job(data, user_id, job)
                    profile = jh_latest(data.get("professional_profiles", []), user_id)
                    resume = jh_latest(data.get("resume_files", []), user_id)
                    cover = jh_generate_cover(profile, resume, job)
                    existing = next((a for a in data.setdefault("applications", []) if a.get("user_id") == user_id and a.get("job_id") == job_id), None)
                    if not existing:
                        existing = {"id": secrets.token_urlsafe(12), "user_id": user_id, "job_id": job_id, "status": "saved", "created_at": jh_now()}
                        data["applications"].append(existing)
                    existing["cover_letter"] = cover
                    save_jobhunter(data)
                    self.send_json({"ok": True, "message": "Detalhe gerado no backend.", "data": {"job": job, "match": match, "cover_letter": cover, "application": existing}}); return
                if action == "application_status":
                    status = str(payload.get("status") or "saved")
                    if status not in {"saved", "applied", "interview", "proposal", "hired", "rejected"}:
                        self.send_json({"ok": False, "message": "Status inválido."}, 400); return
                    job_id = str(payload.get("job_id") or "")
                    existing = next((a for a in data.setdefault("applications", []) if a.get("user_id") == user_id and a.get("job_id") == job_id), None)
                    if not existing:
                        existing = {"id": secrets.token_urlsafe(12), "user_id": user_id, "job_id": job_id, "created_at": jh_now()}
                        data["applications"].append(existing)
                    existing["status"] = status
                    if status == "applied": existing["applied_at"] = jh_now()
                    save_jobhunter(data)
                    self.send_json({"ok": True, "message": f"Candidatura marcada como {status}.", "data": {"application": existing}}); return
                if action == "simulate_interview":
                    job_id = str(payload.get("job_id") or "")
                    job = next((j for j in data.get("jobs", []) if j.get("id") == job_id), None) if job_id else None
                    profile = jh_latest(data.get("professional_profiles", []), user_id)
                    resume = jh_latest(data.get("resume_files", []), user_id)
                    simulation = jh_simulate_interview(profile, resume, job)
                    item = {"id": secrets.token_urlsafe(12), "user_id": user_id, "job_id": job_id or None, "simulation": simulation, "created_at": jh_now()}
                    data.setdefault("interview_simulations", []).append(item)
                    save_jobhunter(data)
                    self.send_json({"ok": True, "message": "Simulação de entrevista gerada.", "data": {"simulation": simulation, "record": item}}); return
                if action == "career_plan":
                    profile = jh_latest(data.get("professional_profiles", []), user_id)
                    prefs = jh_latest(data.get("job_preferences", []), user_id)
                    resume = jh_latest(data.get("resume_files", []), user_id)
                    user_matches = [m for m in data.get("job_matches", []) if m.get("user_id") == user_id]
                    top_ids = {m.get("job_id") for m in sorted(user_matches, key=lambda x: int(x.get("score") or 0), reverse=True)[:20]}
                    jobs = [j for j in data.get("jobs", []) if j.get("id") in top_ids] or data.get("jobs", [])[:20]
                    plan = jh_plan_career(profile, prefs, resume, jobs)
                    item = {"id": secrets.token_urlsafe(12), "user_id": user_id, "plan": plan, "created_at": jh_now()}
                    data.setdefault("career_plans", []).append(item)
                    save_jobhunter(data)
                    self.send_json({"ok": True, "message": "Planejamento de carreira gerado.", "data": {"plan": plan, "record": item}}); return
                if action == "telegram_settings":
                    item = jh_upsert_user_record(data, "telegram_settings", user_id, {"chat_id": str(payload.get("chat_id") or "").strip(), "enabled": bool(payload.get("enabled")), "daily_time": str(payload.get("daily_time") or "08:00")})
                    save_jobhunter(data)
                    self.send_json({"ok": True, "message": "Configuração Telegram salva.", "data": {"telegram": item}}); return
                self.send_json({"ok": False, "message": "Ação JobHunter desconhecida."}, 400); return
            except Exception as exc:
                self.send_json({"ok": False, "message": f"Falha no JobHunter: {exc}"}, 500); return
        if path == "/api/users":
            sess = self.require_permission("users_manage")
            if not sess: return
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except Exception:
                self.send_json({"ok": False, "message": "JSON inválido."}, 400); return
            action = payload.get("action", "list")
            data = load_users()
            users = data.setdefault("users", [])
            if action == "list":
                self.send_json({"ok": True, "message": "Usuários carregados.", "data": {"users": [public_user(u) for u in users], "permissions": PERMISSIONS, "roles": ROLE_PERMISSIONS}}); return
            if action == "save":
                username = str(payload.get("username") or "").strip()
                if not username:
                    self.send_json({"ok": False, "message": "Usuário obrigatório."}, 400); return
                role = payload.get("role") if payload.get("role") in ROLE_PERMISSIONS else "usuario"
                perms = [p for p in (payload.get("permissions") or []) if p in PERMISSIONS]
                active = bool(payload.get("active", True))
                existing = None
                for u in users:
                    if u.get("username") == username:
                        existing = u; break
                if existing is None:
                    password = str(payload.get("password") or "")
                    if not password:
                        self.send_json({"ok": False, "message": "Senha obrigatória para novo usuário."}, 400); return
                    existing = {"username": username, "created": time.strftime("%Y-%m-%d %H:%M:%S")}
                    users.append(existing)
                if payload.get("password"):
                    existing["password_sha256"] = password_hash(str(payload.get("password")))
                existing.update({"role": role, "permissions": perms, "active": active, "telegram_chat_id": str(payload.get("telegram_chat_id") or "").strip()})
                save_users(data)
                self.send_json({"ok": True, "message": f"Usuário {username} salvo.", "data": {"user": public_user(existing)}}); return
            if action == "delete":
                username = str(payload.get("username") or "").strip()
                if username == sess.get("username"):
                    self.send_json({"ok": False, "message": "Você não pode remover seu próprio usuário logado."}, 400); return
                kept = [u for u in users if u.get("username") != username]
                data["users"] = kept
                save_users(data)
                self.send_json({"ok": True, "message": f"Usuário {username} removido.", "data": {"removed": username}}); return
            self.send_json({"ok": False, "message": "Ação de usuários desconhecida."}, 400); return
        if path == "/api/agent":
            sess = self.require_permission("agent_chat")
            if not sess: return
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except Exception:
                self.send_json({"ok": False, "message": "JSON inválido."}, 400); return
            username = sess.get("username", "user")
            action = payload.get("action", "load")
            mode = payload.get("mode", "nserver")
            session_id = payload.get("session_id") or "default"
            if action == "telegram_pair_start":
                data = create_telegram_pairing(username)
                self.send_json({"ok": bool(data.get("ok")), "message": data.get("message") or "Pareamento criado.", "data": data}, 200 if data.get("ok") else 400); return
            if action == "telegram_pair_status":
                data = telegram_pairing_status(username, str(payload.get("code") or ""))
                self.send_json({"ok": bool(data.get("ok")), "message": data.get("message") or "Status carregado.", "data": data}, 200 if data.get("ok") else 404); return
            if action == "telegram_real_start":
                data = start_telegram_real_login(username)
                self.send_json({"ok": bool(data.get("ok")), "message": data.get("message") or "Login iniciado.", "data": data}, 200 if data.get("ok") else 400); return
            if action == "telegram_real_status":
                data = telegram_real_status(username)
                self.send_json({"ok": bool(data.get("ok")), "message": data.get("message") or "Status carregado.", "data": data}, 200 if data.get("ok") else 400); return
            if action == "sessions":
                self.send_json({"ok": True, "message": "Sessões carregadas.", "data": {"sessions": list_agent_sessions(username)}}); return
            if mode == "telegram":
                if not has_permission(sess, "agent_chat"):
                    self.send_json({"ok": False, "message": "Sem permissão para usar o chat."}, 403); return
                tg_session = telegram_user_session(username)
                messages = tg_session.setdefault("messages", [])
                chat_id = telegram_chat_id_for_user(username)
                if not chat_id:
                    self.send_json({"ok": False, "message": "Este usuário ainda não tem Telegram conectado. O administrador precisa preencher o Telegram chat ID no painel Usuários.", "data": {"messages": messages, "mode": mode, "session_id": "telegram"}}, 403); return
                if action == "send":
                    text = str(payload.get("message") or "").strip()
                    if not text:
                        self.send_json({"ok": False, "message": "Mensagem vazia."}, 400); return
                    now = time.strftime("%Y-%m-%d %H:%M:%S")
                    messages.append({"role": "user", "content": f"[pedido pelo painel às {now}] {text}", "time": now})
                    ok, message = telegram_real_send_to_bot(username, text)
                    parsed_tool = parse_web_tool_request(text)
                    if ok:
                        messages.append({"role": "agent", "content": "Mensagem enviada pelo Telegram real do usuário. O bot vai responder/processar normalmente.", "time": now})
                    elif parsed_tool:
                        progress = "Telegram Real ainda não conectado; executei a ferramenta localmente pelo Nserver ✅\nEstou processando agora. Dependendo do tamanho do vídeo, pode demorar alguns minutos."
                        messages.append({"role": "agent", "content": progress, "time": now})
                        threading.Thread(target=run_web_tool_background, args=(username, text), daemon=True).start()
                        try:
                            telegram_send_from_web(progress, username)
                        except Exception:
                            pass
                        ok, message = True, progress
                    else:
                        messages.append({"role": "agent", "content": message, "time": now})
                    tg_session["messages"] = messages[-200:]
                    save_agent_session(tg_session)
                    self.send_json({"ok": ok, "message": message, "data": {"messages": tg_session["messages"], "mode": mode, "session_id": "telegram"}}, 200 if ok else 400); return
                self.send_json({"ok": True, "message": "Sessão Telegram carregada para este usuário.", "data": {"messages": messages, "mode": mode, "session_id": "telegram"}}); return
            session = load_agent_session(username, session_id)
            if action == "load":
                self.send_json({"ok": True, "message": "Sessão carregada.", "data": {"messages": session.get("messages", []), "mode": mode, "session_id": session_id}}); return
            if action == "send":
                text = str(payload.get("message") or "").strip()
                if not text:
                    self.send_json({"ok": False, "message": "Mensagem vazia."}, 400); return
                messages = session.setdefault("messages", [])
                messages.append({"role": "user", "content": text, "time": time.strftime("%Y-%m-%d %H:%M:%S")})
                reply = openrouter_agent_reply(messages, mode)
                messages.append({"role": "agent", "content": reply, "time": time.strftime("%Y-%m-%d %H:%M:%S")})
                save_agent_session(session)
                self.send_json({"ok": True, "message": "Resposta gerada.", "data": {"messages": messages, "mode": mode, "session_id": session_id}}); return
            self.send_json({"ok": False, "message": "Ação de agente desconhecida."}, 400); return
        if path == "/api/media":
            sess = self.require_login()
            if not sess: return
            if not (has_permission(sess, "library") or any(has_permission(sess, tool_permission(t["id"])) for t in TOOLS)):
                self.send_json({"ok": False, "message": "Seu usuário não tem permissão para acessar mídias."}, 403); return
            length = int(self.headers.get("Content-Length", "0"))
            ctype = self.headers.get("Content-Type", "")
            try:
                if ctype.startswith("multipart/form-data"):
                    filename, data = self.parse_multipart_upload(length)
                    item = MEDIA.save_upload(filename, data)
                    if item.get("relative"):
                        set_library_owner(item["relative"], sess.get("username", ""))
                    self.send_json({"ok": True, "message": "Upload salvo na Biblioteca.", "data": item}); return
                payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                action = payload.get("action", "list")
                if action == "list":
                    kind = payload.get("kind")
                    kinds = set(kind.split(",")) if kind else None
                    media_items = [item for item in MEDIA.list(kinds) if can_see_library_rel(sess, item.get("relative", ""))]
                    self.send_json({"ok": True, "message": "Mídias carregadas.", "data": {"items": media_items}}); return
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
            sess = self.require_permission("library")
            if not sess: return
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except Exception:
                self.send_json({"ok": False, "message": "JSON inválido."}, 400); return
            action = payload.get("action", "list")
            if action == "list":
                items = library_files(sess)
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
                if not can_see_library_rel(sess, rel):
                    self.send_json({"ok": False, "message": "Arquivo não disponível para seu usuário."}, 403); return
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
                if not can_see_library_rel(sess, rel):
                    self.send_json({"ok": False, "message": "Arquivo não disponível para seu usuário."}, 403); return
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
                    owners = load_library_owners()
                    if rel in owners:
                        owners.pop(rel, None)
                        save_library_owners(owners)
                    # Remove links temporários que apontavam para o arquivo apagado.
                    for token, item in list(FILE_TOKENS.items()):
                        if item.get("path") == str(target):
                            FILE_TOKENS.pop(token, None)
                    self.send_json({"ok": True, "message": f"Arquivo removido do PC: {name}", "data": {"deleted": rel}}); return
                except Exception as exc:
                    self.send_json({"ok": False, "message": f"Não consegui excluir o arquivo: {exc}"}, 500); return
            if action == "open_folder":
                rel = payload.get("relative", "")
                if rel and not can_see_library_rel(sess, rel):
                    self.send_json({"ok": False, "message": "Arquivo não disponível para seu usuário."}, 403); return
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
            if action == "share":
                rel = payload.get("relative", "")
                meta = library_meta(rel)
                if not rel or not (has_permission(sess, "users_manage") or meta.get("owner") == sess.get("username")):
                    self.send_json({"ok": False, "message": "Você não pode compartilhar este arquivo."}, 403); return
                users = [str(x).strip() for x in (payload.get("users") or []) if str(x).strip()]
                set_library_owner(rel, meta.get("owner") or sess.get("username", ""), users, bool(payload.get("shared_all")))
                self.send_json({"ok": True, "message": "Compartilhamento atualizado.", "data": {"relative": rel, "meta": library_meta(rel)}}); return
            self.send_json({"ok": False, "message": "Ação de biblioteca desconhecida."}, 400); return
        if path == "/api/settings":
            sess = self.require_permission("settings")
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
            sess = self.require_permission("updates")
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
            sess = self.require_permission("tool.course-ingest")
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
                        set_library_owner(mp.relative_to(MIDIAS.resolve()).as_posix(), sess.get("username", ""))
                        data["download_url"] = f"/file/{token_for_file(mp, inline=False)}/{safe_download_name(mp.name)[0]}"
                elif result.ok and data.get("zip"):
                    zp = Path(data["zip"]).resolve()
                    if zp.exists() and MIDIAS.resolve() in zp.parents:
                        set_library_owner(zp.relative_to(MIDIAS.resolve()).as_posix(), sess.get("username", ""))
                        data["download_url"] = f"/file/{token_for_file(zp, inline=False)}/{safe_download_name(zp.name)[0]}"
                if result.ok and data.get("folder"):
                    data["library_url"] = "/library"
            except Exception:
                pass
            self.send_json({"ok": result.ok, "message": result.message, "data": data})
            return
        if path == "/api/editor":
            sess = self.require_permission("tool.video-editor")
            if not sess: return
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except Exception:
                self.send_json({"ok": False, "message": "JSON inválido."}, 400); return
            if payload.get("source") and not can_see_library_rel(sess, str(payload.get("source"))):
                self.send_json({"ok": False, "message": "Arquivo da Biblioteca não disponível para seu usuário."}, 403); return
            result = PROCESSORS["video-editor"].run(payload)
            data = result.data or {}
            if result.ok:
                if payload.get("action") == "list_sources" and isinstance(data.get("items"), list):
                    data["items"] = [item for item in data.get("items", []) if can_see_library_rel(sess, item.get("relative", ""))]
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
                            set_library_owner(data["relative"], sess.get("username", ""))
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
            action_perm = {
                "analyze": "tool.video-downloader",
                "download_video": "tool.video-downloader",
                "extract_audio": "tool.video-downloader",
                "transcribe": "tool.transcription",
                "viral_clips": "tool.viral-clips",
                "video_editor": "tool.video-editor",
            }.get(action, "tool.video-downloader")
            if not has_permission(sess, action_perm):
                self.send_json({"ok": False, "message": "Seu usuário não tem permissão para esta ferramenta."}, 403); return
            if payload.get("source_type") == "library" and payload.get("library_path") and not can_see_library_rel(sess, str(payload.get("library_path"))):
                self.send_json({"ok": False, "message": "Arquivo da Biblioteca não disponível para seu usuário."}, 403); return
            if payload.get("source") and not can_see_library_rel(sess, str(payload.get("source"))):
                self.send_json({"ok": False, "message": "Arquivo da Biblioteca não disponível para seu usuário."}, 403); return
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
                        set_library_owner(rel, sess.get("username", ""))
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
        user = find_user(username)
        ok_pass = bool(user and user.get("active", True) and hmac.compare_digest(password_hash(password), str(user.get("password_sha256", ""))))
        if user and ok_pass:
            sid = secrets.token_urlsafe(32)
            SESSIONS[sid] = {"username": user.get("username"), "role": user.get("role", "usuario"), "created": time.time()}
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
