-- JobHunter AI 2.0 — Supabase schema
-- User identity is expected to reference auth.users(id) in the production platform.

create table if not exists public.professional_profiles (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text,
  email text,
  headline text,
  bio text,
  experience_level text check (experience_level in ('Júnior','Pleno','Sênior','Especialista')),
  location text,
  linkedin_url text,
  github_url text,
  portfolio_url text,
  website_url text,
  behance_url text,
  dribbble_url text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.resume_files (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  file_url text not null,
  parsed_content jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.job_preferences (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  remote boolean not null default true,
  hybrid boolean not null default false,
  onsite boolean not null default false,
  clt boolean not null default false,
  pj boolean not null default true,
  freelance boolean not null default true,
  salary_min numeric,
  currency text,
  countries text[] not null default '{}',
  languages text[] not null default '{}',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.jobs (
  id uuid primary key default gen_random_uuid(),
  source text not null,
  external_id text,
  title text not null,
  company text,
  location text,
  salary text,
  description text,
  url text not null,
  posted_at timestamptz,
  created_at timestamptz not null default now(),
  unique(source, external_id),
  unique(title, company, url)
);

create table if not exists public.job_matches (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references public.jobs(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  score integer not null check (score >= 0 and score <= 100),
  analysis jsonb not null default '[]'::jsonb,
  gaps jsonb not null default '[]'::jsonb,
  recommendations jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(job_id, user_id)
);

create table if not exists public.applications (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  job_id uuid not null references public.jobs(id) on delete cascade,
  status text not null default 'saved' check (status in ('saved','applied','interview','proposal','hired','rejected')),
  cover_letter text,
  applied_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(user_id, job_id)
);

create table if not exists public.telegram_settings (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade unique,
  chat_id text,
  enabled boolean not null default false,
  daily_time time default '08:00',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.interview_simulations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  job_id uuid references public.jobs(id) on delete set null,
  simulation jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.career_plans (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  plan jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

alter table public.professional_profiles enable row level security;
alter table public.resume_files enable row level security;
alter table public.job_preferences enable row level security;
alter table public.job_matches enable row level security;
alter table public.applications enable row level security;
alter table public.telegram_settings enable row level security;
alter table public.interview_simulations enable row level security;
alter table public.career_plans enable row level security;

-- RLS examples. Service-role backend bypasses RLS for cron/matching.
drop policy if exists "own professional_profiles" on public.professional_profiles;
create policy "own professional_profiles" on public.professional_profiles for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
drop policy if exists "own resume_files" on public.resume_files;
create policy "own resume_files" on public.resume_files for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
drop policy if exists "own job_preferences" on public.job_preferences;
create policy "own job_preferences" on public.job_preferences for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
drop policy if exists "own job_matches" on public.job_matches;
create policy "own job_matches" on public.job_matches for select using (auth.uid() = user_id);
drop policy if exists "own applications" on public.applications;
create policy "own applications" on public.applications for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
drop policy if exists "own telegram_settings" on public.telegram_settings;
create policy "own telegram_settings" on public.telegram_settings for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
drop policy if exists "own interview_simulations" on public.interview_simulations;
create policy "own interview_simulations" on public.interview_simulations for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
drop policy if exists "own career_plans" on public.career_plans;
create policy "own career_plans" on public.career_plans for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- Jobs are shared/readable to authenticated users.
alter table public.jobs enable row level security;
drop policy if exists "authenticated jobs read" on public.jobs;
create policy "authenticated jobs read" on public.jobs for select using (auth.role() = 'authenticated');
