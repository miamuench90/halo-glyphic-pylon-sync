import os
import time
import requests
from datetime import datetime, timezone

GLYPHIC_API_KEY = os.environ["GLYPHIC_API_KEY"]
PYLON_API_KEY = os.environ["PYLON_API_KEY"]

GLYPHIC_BASE = "https://api.glyphic.ai/v1"
PYLON_BASE = "https://api.usepylon.com"

GLYPHIC_HEADERS = {"X-API-Key": GLYPHIC_API_KEY}
PYLON_HEADERS = {
    "Authorization": f"Bearer {PYLON_API_KEY}",
    "Content-Type": "application/json"
}

INTERNAL_TAGS = {
    "hiring interviews",
    "pipeline meeting",
    "sales standup",
    "team meetings",
    "1:1 meetings",
    "vendor meeting"
}

# Pylon allows 60 POST requests/minute
RATE_LIMIT_DELAY = 1.0

def is_internal_call(call):
    """A call is internal if ALL participants are @halo.science emails."""
    participants = call.get("participants", []) or []
    if not participants:
        return True
    return all("halo.science" in (p.get("email") or "") for p in participants)

def timestamp_to_ms(ts):
    """Convert 'MM:SS' or 'HH:MM:SS' timestamp string to milliseconds."""
    try:
        parts = ts.strip().split(":")
        if len(parts) == 2:
            mins, secs = int(parts[0]), int(parts[1])
            return (mins * 60 + secs) * 1000
        elif len(parts) == 3:
            hrs, mins, secs = int(parts[0]), int(parts[1]), int(parts[2])
            return (hrs * 3600 + mins * 60 + secs) * 1000
    except Exception:
        pass
    return 0

def get_all_glyphic_calls():
    calls = []
    cursor = None
    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(f"{GLYPHIC_BASE}/calls/", headers=GLYPHIC_HEADERS, params=params)
        resp.raise_for_status()
        data = resp.json()
        calls.extend(data["data"])
        print(f"  Fetched {len(calls)} calls so far...")
        next_cursor = data["pagination"].get("next_cursor")
        if not next_cursor:
            break
        cursor = next_cursor
    return calls

def get_call_details(call_id):
    resp = requests.get(f"{GLYPHIC_BASE}/calls/{call_id}", headers=GLYPHIC_HEADERS)
    resp.raise_for_status()
    return resp.json()

def build_pylon_payload(detail):
    # Build participant lookup by party_id
    participants = detail.get("participants", []) or []
    participant_by_id = {p["id"]: p for p in participants}

    # All participant emails as comma-separated string (Pylon requires this format)
    participant_emails = ", ".join(p["email"] for p in participants if p.get("email"))

    # Primary user = first non-halo.science email (i.e. the customer)
    primary_email = next(
        (p["email"] for p in participants
         if p.get("email") and "halo.science" not in p["email"]),
        participant_emails.split(", ")[0] if participant_emails else None
    )

    # Build transcript messages from transcript_turns
    transcript_turns = detail.get("transcript_turns", []) or []
    messages = []
    for turn in transcript_turns:
        party_id = turn.get("party_id")
        participant = participant_by_id.get(party_id, {})
        messages.append({
            "message_at_ms": timestamp_to_ms(turn.get("timestamp", "0:00")),
            "speaker_email": participant.get("email", ""),
            "speaker_name": participant.get("name", "Unknown"),
            "message_content": turn.get("turn_text", "")
        })

    # Get start time
    start_time = detail.get("start_time")
    if not start_time:
        return None

    # Calculate end time using duration (in seconds)
    duration = detail.get("duration")
    if duration and start_time:
        try:
            start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            from datetime import timedelta
            end_dt = start_dt + timedelta(seconds=duration)
            end_time = end_dt.isoformat()
        except Exception:
            end_time = start_time
    else:
        end_time = start_time

    # Get recording URL from nested media object
    recording_url = None
    media = detail.get("media")
    if media and isinstance(media, dict):
        recording_url = media.get("media_url")

    payload = {
        "title": detail.get("title") or "Untitled Call",
        "recording_id": detail.get("id"),
        "app_type": "custom_call_recorder",
        "start_time": start_time,
        "end_time": end_time,
        "participant_emails": participant_emails,
        "primary_user_email": primary_email,
        "call_recording_messages": messages
    }

    if recording_url:
        payload["recording_url"] = recording_url

    return payload

def send_to_pylon_with_retry(payload, max_retries=3):
    for attempt in range(max_retries):
        resp = requests.post(
            f"{PYLON_BASE}/call-recordings",
            headers=PYLON_HEADERS,
            json=payload
        )
        if resp.status_code == 429:
            wait = 60 * (attempt + 1)
            print(f"  ⚠ Rate limited. Waiting {wait}s before retry {attempt + 1}/{max_retries}...")
            time.sleep(wait)
            continue
        return resp
    return resp

def main():
    print(f"Starting full Glyphic → Pylon backfill at {datetime.now(timezone.utc).isoformat()}")
    print("Fetching all calls from Glyphic...")

    all_calls = get_all_glyphic_calls()
    print(f"\nTotal calls found: {len(all_calls)}")

    external_calls = [c for c in all_calls if not is_internal_call(c)]
    skipped_internal = len(all_calls) - len(external_calls)
    print(f"External calls to sync: {len(external_calls)} (skipping {skipped_internal} internal/vendor calls)")
    print(f"Estimated time: ~{round(len(external_calls) * RATE_LIMIT_DELAY / 60, 1)} minutes\n")

    success, failed, duplicate, skipped_no_time = 0, 0, 0, 0

    for i, call in enumerate(external_calls, 1):
        call_id = call["id"]
        print(f"[{i}/{len(external_calls)}] Processing call {call_id}...")

        try:
            detail = get_call_details(call_id)
            payload = build_pylon_payload(detail)

            if payload is None:
                print(f"  ⚠ Skipped: no start time available")
                skipped_no_time += 1
                continue

            time.sleep(RATE_LIMIT_DELAY)

            resp = send_to_pylon_with_retry(payload)

            if resp.status_code in (200, 201):
                print(f"  ✓ Synced: {payload['title']}")
                success += 1
            elif resp.status_code == 409:
                print(f"  ℹ Already exists (skipped): {payload['title']}")
                duplicate += 1
            else:
                print(f"  ✗ Pylon error {resp.status_code}: {resp.text}")
                print(f"    Payload sent: {payload}")
                failed += 1

        except Exception as e:
            print(f"  ✗ Error processing call {call_id}: {e}")
            failed += 1

    print(f"\n--- Backfill Complete ---")
    print(f"✓ Synced:             {success}")
    print(f"ℹ Already in Pylon:   {duplicate}")
    print(f"✗ Internal/skipped:   {skipped_internal}")
    print(f"⚠ Skipped (no time):  {skipped_no_time}")
    print(f"✗ Failed:             {failed}")

if __name__ == "__main__":
    main()
