alter table public.sessions add column if not exists client_ip text;
alter table public.events add column if not exists client_ip text;

create index if not exists idx_sessions_client_ip on public.sessions (client_ip);
create index if not exists idx_events_client_ip_timestamp on public.events (client_ip, timestamp desc);
