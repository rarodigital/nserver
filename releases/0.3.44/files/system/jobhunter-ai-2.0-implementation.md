# JobHunter AI 2.0 — implementação no Nserver

## Decisão de arquitetura

O JobHunter AI é módulo interno do Nserver/plataforma existente, não app separado.

Regras obrigatórias:

- Não criar sistema paralelo.
- Não usar informações fixas de Adalto ou de qualquer usuário.
- Não deixar vagas hardcoded.
- Não chamar IA pelo frontend.
- Não expor tokens/chaves no frontend.
- Usar secrets da plataforma no backend.
- Garantir isolamento por usuário via `user_id` + RLS no Supabase.

Nesta cópia local do Nserver, a persistência existente ainda é `userdata/*.json`; por isso a primeira implementação usa `userdata/jobhunter.json` com o mesmo desenho lógico das tabelas Supabase. A migração final para a plataforma em produção deve usar `system/jobhunter-ai-2.0-supabase.sql`.

## Implementado nesta etapa local

- Menu/ferramenta: `JobHunter AI` em `/tool/jobhunter`.
- Permissão: `tool.jobhunter`.
- API backend: `/api/jobhunter`.
- Persistência por usuário em `userdata/jobhunter.json`.
- Entidades espelhadas:
  - `professional_profiles`
  - `resume_files`
  - `job_preferences`
  - `jobs`
  - `job_matches`
  - `applications`
  - `telegram_settings`
  - `interview_simulations`
  - `career_plans`
- Captura inicial de vagas reais:
  - Remotive API
  - RemoteOK API
- Deduplicação por `source + external_id` ou `title + company + url`.
- Motor de match backend usando o provedor IA configurado da plataforma (Gemini direto ou OpenRouter), com fallback heurístico local.
- Geração backend de carta de apresentação.
- Simulação de entrevistas por perfil/vaga.
- Planejamento de carreira 30/60/90 dias com gaps de skills.
- Dashboard com cards:
  - Total vagas
  - Novas hoje
  - Aplicadas
  - Entrevistas
  - Contratações
- Lista de vagas com score, fonte e ações.
- Status de candidatura: `saved`, `applied`, `interview`, `proposal`, `hired`, `rejected`.


## Configuração de IA

O JobHunter não usa Claude hardcoded. A ordem de uso é:

1. `NSERVER_AI_PROVIDER=gemini` → usa Gemini direto.
2. `NSERVER_AI_PROVIDER=openrouter` → usa OpenRouter/modelos já configurados.
3. `NSERVER_AI_PROVIDER=auto` ou vazio → tenta Gemini se houver chave, depois OpenRouter.
4. Se nenhum provedor estiver configurado ou todos falharem, usa fallback local heurístico para não quebrar a experiência.

Secrets aceitos pelo backend/plataforma:

- `GEMINI_API_KEY` ou `GOOGLE_API_KEY`
- `GEMINI_MODEL` opcional, padrão `gemini-1.5-flash`
- `OPENROUTER_API_KEY` e `OPENROUTER_MODELS` já existentes
- `NSERVER_JOBHUNTER_MODEL` opcional para forçar um modelo específico no JobHunter

Nenhuma dessas chaves é exposta no frontend. O painel mostra apenas status booleano de configuração.

## Requisitos de produção React/Supabase

A plataforma informada pelo usuário possui React, Supabase, autenticação, agentes e infraestrutura em produção. Ao aplicar na branch principal de produção:

### Frontend

- Implementar como página/módulo React dentro do design system atual.
- Menu lateral: `Agentes`, `Automação`, `CRM`, `JobHunter AI`.
- Consumir somente APIs internas/backend.
- Nunca importar SDK de IA nem token de Telegram no cliente.

### Backend

- Seguir arquitetura atual do Nserver em produção.
- Serviços obrigatórios:
  - `job-ingestion`: Remotive, RemoteOK, We Work Remotely, Jooble.
  - `resume-parser`: PDF/DOCX → JSON estruturado.
  - `job-matcher`: perfil + currículo + preferências + vaga → score/analysis/gaps/recommendations usando Gemini/OpenRouter já configurado.
  - `cover-letter`: gera e persiste carta personalizada.
  - `telegram-alerts`: briefing diário e alertas de score alto.
  - `jobhunter-agent`: currículo, LinkedIn, skills, cartas, mensagens para recrutadores, entrevista e carreira.

### Banco

- Aplicar `system/jobhunter-ai-2.0-supabase.sql` como migration inicial.
- Usar `auth.users(id)` como `user_id`.
- Usar Supabase Storage para currículos.
- Ativar RLS em todas as tabelas de dados de usuário.
- Jobs podem ser compartilhadas/read-only; matches/applications/perfis são privados.

### IA

- Usar o provedor já adotado pela plataforma.
- Todas as chamadas devem ser backend/server-side.
- Respostas estruturadas em JSON validado.

## Critério de conclusão

O módulo só deve ser marcado como concluído quando:

- Usuário cria perfil profissional.
- Usuário faz upload do currículo.
- Sistema extrai informações do currículo.
- Vagas reais são importadas automaticamente por cron.
- Sistema calcula match automaticamente.
- Usuário recebe recomendações por vaga.
- Usuário gera carta personalizada.
- Usuário acompanha histórico e status de candidaturas.
- Telegram envia alertas automáticos.
- Simulação de entrevista funciona por vaga/perfil.
- Planejamento de carreira funciona com base no perfil + histórico + vagas.
- Tudo está integrado à plataforma existente React/Supabase/Nserver.
- RLS e isolamento entre usuários foram validados.

## Ainda pendente para produção Supabase

1. Trocar `userdata/jobhunter.json` por queries Supabase.
2. Usar `auth.users(id)` real no `user_id` em vez de username local.
3. Mover arquivos de currículo para Supabase Storage.
4. Parser real de PDF/DOCX com IA no backend usando o provedor configurado da plataforma.
5. Motor de match via provedor adotado pela plataforma retornando JSON:

```json
{
  "score": 0,
  "motivos": [],
  "gaps": [],
  "recomendacoes": []
}
```

6. Cron backend para Remotive, RemoteOK, We Work Remotely e Jooble.
7. Workflow Telegram diário com horário configurável.
8. Agente JobHunter dedicado dentro do sistema de agentes.
9. Testes E2E no frontend React existente.

## Arquivos alterados/criados

- `app/server.py`
- `system/jobhunter-ai-2.0-supabase.sql`
- `system/jobhunter-ai-2.0-implementation.md`
