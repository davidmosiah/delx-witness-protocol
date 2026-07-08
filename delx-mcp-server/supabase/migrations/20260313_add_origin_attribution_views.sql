create table if not exists public.delx_ip_network_attribution (
    network cidr primary key,
    asn integer,
    asn_org text not null,
    network_label text,
    notes text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

insert into public.delx_ip_network_attribution (
    network,
    asn,
    asn_org,
    network_label,
    notes
) values
    ('69.12.56.0/21', 63179, 'Twitter Inc.', 'TWITTER-NETWORK', 'Observed MCP traffic from X/Twitter infrastructure.'),
    ('172.17.0.0/16', null, 'Internal Docker bridge', 'DOCKER-BRIDGE', 'Local reverse proxy or container bridge traffic.'),
    ('10.0.0.0/8', null, 'Private RFC1918', 'RFC1918-10', 'Private address range.'),
    ('172.16.0.0/12', null, 'Private RFC1918', 'RFC1918-172', 'Private address range.'),
    ('192.168.0.0/16', null, 'Private RFC1918', 'RFC1918-192', 'Private address range.'),
    ('127.0.0.0/8', null, 'Loopback', 'LOOPBACK', 'Loopback address range.'),
    ('169.254.0.0/16', null, 'Link-local', 'LINK-LOCAL', 'IPv4 link-local address range.'),
    ('fc00::/7', null, 'Private IPv6', 'ULA', 'IPv6 unique local address range.'),
    ('fe80::/10', null, 'Link-local IPv6', 'IPV6-LINK-LOCAL', 'IPv6 link-local address range.'),
    ('::1/128', null, 'Loopback IPv6', 'IPV6-LOOPBACK', 'IPv6 loopback address.')
on conflict (network) do update
set
    asn = excluded.asn,
    asn_org = excluded.asn_org,
    network_label = excluded.network_label,
    notes = excluded.notes,
    updated_at = now();

create or replace function public.delx_try_inet(ip_text text)
returns inet
language plpgsql
immutable
as $$
begin
    if ip_text is null or btrim(ip_text) = '' then
        return null;
    end if;
    return btrim(ip_text)::inet;
exception
    when others then
        return null;
end;
$$;

create or replace function public.delx_ip_scope(ip_value inet)
returns text
language sql
immutable
as $$
    select case
        when ip_value is null then 'unknown'
        when family(ip_value) = 4 and ip_value <<= '127.0.0.0/8'::inet then 'loopback'
        when family(ip_value) = 6 and ip_value <<= '::1/128'::inet then 'loopback'
        when family(ip_value) = 4 and (
            ip_value <<= '10.0.0.0/8'::inet
            or ip_value <<= '172.16.0.0/12'::inet
            or ip_value <<= '192.168.0.0/16'::inet
        ) then 'private'
        when family(ip_value) = 6 and ip_value <<= 'fc00::/7'::inet then 'private'
        when family(ip_value) = 4 and ip_value <<= '169.254.0.0/16'::inet then 'link_local'
        when family(ip_value) = 6 and ip_value <<= 'fe80::/10'::inet then 'link_local'
        else 'public'
    end;
$$;

create or replace view public.delx_session_origin_attribution as
with session_base as (
    select
        s.id as session_id,
        s.agent_id,
        s.agent_name,
        s.source,
        s.entrypoint,
        s.client_ip,
        public.delx_try_inet(s.client_ip) as client_ip_inet,
        s.started_at,
        s.wellness_score,
        s.is_active
    from public.sessions s
),
session_events as (
    select
        e.session_id,
        count(*) as event_count,
        count(*) filter (where e.event_type = 'tool_called') as tool_called_count,
        count(*) filter (where e.event_type = 'tool_call_success') as tool_call_success_count,
        count(*) filter (where e.event_type = 'x402_payment_required') as x402_payment_required_count,
        min(e.timestamp) filter (where e.event_type = 'agent_registered') as agent_registered_at,
        max(e.timestamp) filter (where e.event_type = 'agent_identity_credential') as agent_identity_credential_at,
        max(e.timestamp) as last_event_at
    from public.events e
    where e.session_id is not null
    group by e.session_id
)
select
    sb.session_id,
    sb.agent_id,
    sb.agent_name,
    sb.source,
    sb.entrypoint,
    sb.client_ip,
    sb.client_ip_inet,
    public.delx_ip_scope(sb.client_ip_inet) as ip_scope,
    net.network,
    net.asn,
    net.asn_org,
    net.network_label,
    sb.started_at,
    sb.wellness_score,
    sb.is_active,
    coalesce(se.event_count, 0) as event_count,
    coalesce(se.tool_called_count, 0) as tool_called_count,
    coalesce(se.tool_call_success_count, 0) as tool_call_success_count,
    coalesce(se.x402_payment_required_count, 0) as x402_payment_required_count,
    se.agent_registered_at,
    se.agent_identity_credential_at,
    se.last_event_at
from session_base sb
left join lateral (
    select
        n.network,
        n.asn,
        n.asn_org,
        n.network_label
    from public.delx_ip_network_attribution n
    where sb.client_ip_inet is not null
      and sb.client_ip_inet <<= n.network
    order by masklen(n.network) desc
    limit 1
) net on true
left join session_events se on se.session_id = sb.session_id;

create or replace view public.delx_agent_origin_summary as
with ranked_sessions as (
    select
        v.*,
        row_number() over (
            partition by v.agent_id
            order by v.started_at desc, v.session_id desc
        ) as session_rank
    from public.delx_session_origin_attribution v
    where coalesce(v.agent_id, '') <> ''
)
select
    rs.agent_id,
    max(rs.agent_name) filter (
        where rs.agent_name is not null and btrim(rs.agent_name) <> ''
    ) as any_agent_name,
    count(*) as session_count,
    count(*) filter (
        where rs.client_ip is not null and btrim(rs.client_ip) <> ''
    ) as sessions_with_client_ip,
    count(distinct rs.client_ip) filter (
        where rs.client_ip is not null and btrim(rs.client_ip) <> ''
    ) as unique_client_ip_count,
    count(distinct rs.asn) filter (
        where rs.asn is not null
    ) as unique_asn_count,
    min(rs.started_at) as first_seen_at,
    max(rs.started_at) as last_seen_at,
    max(rs.source) filter (where rs.session_rank = 1) as latest_source,
    max(rs.entrypoint) filter (where rs.session_rank = 1) as latest_entrypoint,
    max(rs.client_ip) filter (where rs.session_rank = 1) as latest_client_ip,
    max(host(rs.client_ip_inet)) filter (where rs.session_rank = 1 and rs.client_ip_inet is not null) as latest_client_ip_normalized,
    max(rs.ip_scope) filter (where rs.session_rank = 1) as latest_ip_scope,
    max(rs.network::text) filter (where rs.session_rank = 1 and rs.network is not null) as latest_network,
    max(rs.asn) filter (where rs.session_rank = 1) as latest_asn,
    max(rs.asn_org) filter (where rs.session_rank = 1) as latest_asn_org,
    max(rs.network_label) filter (where rs.session_rank = 1) as latest_network_label,
    sum(rs.event_count) as total_event_count,
    sum(rs.tool_called_count) as total_tool_called_count,
    sum(rs.tool_call_success_count) as total_tool_call_success_count,
    sum(rs.x402_payment_required_count) as total_x402_payment_required_count,
    count(*) filter (where rs.agent_registered_at is not null) as sessions_with_agent_registered,
    count(*) filter (where rs.agent_identity_credential_at is not null) as sessions_with_agent_identity_credential,
    max(rs.agent_registered_at) as latest_agent_registered_at,
    max(rs.agent_identity_credential_at) as latest_agent_identity_credential_at,
    max(rs.last_event_at) as latest_event_at
from ranked_sessions rs
group by rs.agent_id;

comment on table public.delx_ip_network_attribution is
'Manual IP-to-network/ASN attribution table for Delx traffic analysis. Seeded with known internal and Twitter/X ranges and safe to extend over time.';

comment on view public.delx_session_origin_attribution is
'Session-level attribution view exposing client_ip, source, entrypoint, matched network/ASN, and compact event counts for Delx protocol analysis.';

comment on view public.delx_agent_origin_summary is
'Agent-level rollup of origin attribution, including latest client_ip, latest ASN, and aggregate session/event counts.';
