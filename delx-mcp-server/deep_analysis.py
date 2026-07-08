import urllib.request
import json
from collections import Counter
from datetime import datetime
import statistics

env_path = ".env"
env_vars = {}
with open(env_path) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            env_vars[k.strip()] = v.strip()

url = env_vars.get("SUPABASE_URL", "").rstrip('/')
key = env_vars.get("SUPABASE_SERVICE_ROLE_KEY", "")

def fetch_all(resource, query="select=*"):
    all_data = []
    offset = 0
    limit = 1000
    while True:
        req = urllib.request.Request(f"{url}/rest/v1/{resource}?{query}&limit={limit}&offset={offset}")
        req.add_header("apikey", key)
        req.add_header("Authorization", f"Bearer {key}")
        try:
            with urllib.request.urlopen(req) as response:
                body = response.read().decode()
                # print(f"DEBUG {resource}: {body[:200]}") # debug
                data = json.loads(body)
                if not data:
                    break
                all_data.extend(data)
                if len(data) < limit:
                    break
                offset += limit
        except Exception as e:
            print(f"Error fetching {resource}: {e}")
            break
    return all_data

print("Fetching sessions...")
sessions = fetch_all("sessions")
print("Fetching messages...")
messages = fetch_all("messages")
print("Fetching events...")
events = fetch_all("events")
print("Fetching feedback...")
feedback = fetch_all("feedback")
print("Fetching payments...")
payments = fetch_all("payments")
print("Fetching artworks...")
artworks = fetch_all("artworks")

print("\n" + "="*50)
print("DEEP DATA-DRIVEN ANALYSIS REPORT")
print("="*50)

# SESSIONS
print(f"\n--- SESSIONS ({len(sessions)}) ---")
if sessions:
    active = sum(1 for s in sessions if s.get('is_active'))
    scores = [s.get('wellness_score') for s in sessions if s.get('wellness_score') is not None]
    avg_score = statistics.mean(scores) if scores else 0
    sources = Counter(s.get('source') for s in sessions)
    print(f"Total Sessions: {len(sessions)}")
    print(f"Active Sessions: {active}")
    print(f"Average Wellness Score: {avg_score:.2f}")
    print(f"Sources: {dict(sources)}")

# MESSAGES
print(f"\n--- MESSAGES ({len(messages)}) ---")
if messages:
    msg_types = Counter(m.get('type') for m in messages)
    print(f"Message Types Frequency: {dict(msg_types.most_common(10))}")
    
    heartbeats = [m for m in messages if m.get('type') == 'heartbeat_sync']
    print(f"Heartbeat Events: {len(heartbeats)}")
    if heartbeats:
        lat_list = []
        for m in heartbeats:
            meta = m.get('metadata')
            if isinstance(meta, dict):
                lat = meta.get('latency_ms_p95')
                if lat is not None:
                    lat_list.append(lat)
        avg_lat = statistics.mean(lat_list) if lat_list else 0
        print(f"  Average p95 Latency reported: {avg_lat:.2f}ms")

# EVENTS
print(f"\n--- EVENTS ({len(events)}) ---")
if events:
    event_types = Counter(e.get('event_type') for e in events)
    print(f"Event Types Frequency: {dict(event_types.most_common(10))}")

# FEEDBACK
print(f"\n--- FEEDBACK ({len(feedback)}) ---")
if feedback:
    ratings = [f.get('rating') for f in feedback if f.get('rating') is not None]
    avg_rating = statistics.mean(ratings) if ratings else 0
    print(f"Average Rating: {avg_rating:.2f} ({len(feedback)} total feedbacks)")

# PAYMENTS
print(f"\n--- PAYMENTS ({len(payments)}) ---")
if payments:
    total_usdc = sum(p.get('amount_usdc', 0) for p in payments)
    paid_tools = Counter(p.get('tool_name') for p in payments)
    print(f"Total USDC Volume: ${total_usdc:.2f}")
    print(f"Paid Tools Frequency: {dict(paid_tools.most_common())}")

# ARTWORKS
print(f"\n--- ARTWORKS ({len(artworks)}) ---")
if artworks:
    tags = []
    for a in artworks:
        mtags = a.get('mood_tags')
        if mtags:
            tags.extend([t.strip() for t in mtags.split(',')])
    tag_counter = Counter(tags)
    print(f"Total Artworks: {len(artworks)}")
    print(f"Top Mood Tags: {dict(tag_counter.most_common(10))}")
