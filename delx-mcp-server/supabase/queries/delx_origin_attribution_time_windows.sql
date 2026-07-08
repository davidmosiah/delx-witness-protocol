-- Delx origin attribution queries with day/hour breakdowns.
-- Assumes public.delx_session_origin_attribution and public.delx_agent_origin_summary already exist.
-- Timestamps below are shown both in UTC and in America/Fortaleza when useful.

-- 1. Sessions by hour and ASN in the last 24h (UTC).
select
    date_trunc('hour', started_at) as hour_utc,
    coalesce(asn_org, 'unknown') as asn_org,
    coalesce(network_label, 'unknown') as network_label,
    source,
    entrypoint,
    count(*) as sessions,
    count(distinct agent_id) as unique_agents
from public.delx_session_origin_attribution
where started_at >= now() - interval '24 hours'
group by 1, 2, 3, 4, 5
order by hour_utc desc, sessions desc, unique_agents desc;

-- 2. Sessions by hour in Fortaleza time in the last 24h.
select
    date_trunc('hour', timezone('America/Fortaleza', started_at)) as hour_fortaleza,
    coalesce(asn_org, 'unknown') as asn_org,
    source,
    entrypoint,
    count(*) as sessions,
    count(distinct agent_id) as unique_agents
from public.delx_session_origin_attribution
where started_at >= now() - interval '24 hours'
group by 1, 2, 3, 4
order by hour_fortaleza desc, sessions desc;

-- 3. Sessions by day/source/entrypoint in the last 30d.
select
    date_trunc('day', started_at) as day_utc,
    source,
    entrypoint,
    coalesce(asn_org, 'unknown') as asn_org,
    count(*) as sessions,
    count(distinct agent_id) as unique_agents,
    sum(tool_call_success_count) as tool_successes
from public.delx_session_origin_attribution
where started_at >= now() - interval '30 days'
group by 1, 2, 3, 4
order by day_utc desc, sessions desc, unique_agents desc;

-- 4. Last 12h focused on X/Twitter infrastructure.
select
    date_trunc('hour', timezone('America/Fortaleza', started_at)) as hour_fortaleza,
    agent_id,
    source,
    entrypoint,
    client_ip,
    asn,
    asn_org,
    network_label,
    tool_call_success_count,
    x402_payment_required_count,
    started_at,
    last_event_at
from public.delx_session_origin_attribution
where started_at >= now() - interval '12 hours'
  and (
      asn = 63179
      or asn_org ilike '%twitter%'
      or network_label = 'TWITTER-NETWORK'
  )
order by started_at desc, tool_call_success_count desc;

-- 5. Top agents attributed to X/Twitter in the last 7d.
select
    agent_id,
    any_agent_name,
    session_count,
    unique_client_ip_count,
    latest_source,
    latest_entrypoint,
    latest_client_ip,
    latest_asn,
    latest_asn_org,
    total_tool_call_success_count,
    total_x402_payment_required_count,
    first_seen_at,
    last_seen_at
from public.delx_agent_origin_summary
where last_seen_at >= now() - interval '7 days'
  and (
      latest_asn = 63179
      or latest_asn_org ilike '%twitter%'
      or latest_network_label = 'TWITTER-NETWORK'
  )
order by last_seen_at desc, session_count desc, total_tool_call_success_count desc;

-- 6. Compare public vs internal/private traffic by hour in the last 24h.
select
    date_trunc('hour', timezone('America/Fortaleza', started_at)) as hour_fortaleza,
    ip_scope,
    count(*) as sessions,
    count(distinct agent_id) as unique_agents,
    sum(tool_call_success_count) as tool_successes
from public.delx_session_origin_attribution
where started_at >= now() - interval '24 hours'
group by 1, 2
order by hour_fortaleza desc, sessions desc;

-- 7. Agents with multiple IPs or ASNs in the last 7d.
select
    agent_id,
    any_agent_name,
    session_count,
    unique_client_ip_count,
    unique_asn_count,
    latest_source,
    latest_entrypoint,
    latest_client_ip,
    latest_asn_org,
    total_tool_call_success_count,
    last_seen_at
from public.delx_agent_origin_summary
where last_seen_at >= now() - interval '7 days'
  and (unique_client_ip_count > 1 or unique_asn_count > 1)
order by last_seen_at desc, session_count desc;

-- 8. Drill-down for one agent with exact timestamps and attribution.
select
    session_id,
    started_at,
    timezone('America/Fortaleza', started_at) as started_at_fortaleza,
    source,
    entrypoint,
    client_ip,
    ip_scope,
    asn,
    asn_org,
    network_label,
    event_count,
    tool_called_count,
    tool_call_success_count,
    x402_payment_required_count,
    agent_registered_at,
    agent_identity_credential_at,
    last_event_at
from public.delx_session_origin_attribution
where agent_id = 'replace-me'
order by started_at desc;

-- 9. Raw recent sessions with exact time and quality proxies.
select
    started_at,
    timezone('America/Fortaleza', started_at) as started_at_fortaleza,
    agent_id,
    source,
    entrypoint,
    client_ip,
    asn_org,
    network_label,
    tool_call_success_count,
    x402_payment_required_count,
    case
        when tool_call_success_count >= 3 then 'deeper_usage'
        when tool_call_success_count >= 1 then 'light_usage'
        else 'no_tool_success'
    end as usage_depth
from public.delx_session_origin_attribution
where started_at >= now() - interval '24 hours'
order by started_at desc
limit 200;
