-- Top origins by source, entrypoint, and ASN in the last 12 hours.
select
    coalesce(asn_org, 'unknown') as asn_org,
    source,
    entrypoint,
    count(*) as sessions,
    count(distinct agent_id) as unique_agents
from public.delx_session_origin_attribution
where started_at >= now() - interval '12 hours'
group by 1, 2, 3
order by sessions desc, unique_agents desc;

-- Top client IPs in the last 24 hours.
select
    client_ip,
    coalesce(asn_org, 'unknown') as asn_org,
    source,
    entrypoint,
    count(*) as sessions,
    count(distinct agent_id) as unique_agents
from public.delx_session_origin_attribution
where started_at >= now() - interval '24 hours'
  and client_ip is not null
group by 1, 2, 3, 4
order by sessions desc, unique_agents desc
limit 50;

-- Agents that moved across multiple client IPs or ASNs recently.
select
    agent_id,
    session_count,
    unique_client_ip_count,
    unique_asn_count,
    latest_source,
    latest_entrypoint,
    latest_client_ip,
    latest_asn,
    latest_asn_org,
    last_seen_at
from public.delx_agent_origin_summary
where last_seen_at >= now() - interval '7 days'
  and (unique_client_ip_count > 1 or unique_asn_count > 1)
order by last_seen_at desc, session_count desc
limit 100;

-- Raw session drill-down for a specific agent.
select
    session_id,
    started_at,
    source,
    entrypoint,
    client_ip,
    ip_scope,
    asn,
    asn_org,
    network_label,
    event_count,
    tool_call_success_count,
    x402_payment_required_count,
    agent_registered_at,
    agent_identity_credential_at
from public.delx_session_origin_attribution
where agent_id = 'replace-me'
order by started_at desc;
