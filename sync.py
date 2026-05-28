import os
import time
import requests
from datetime import datetime, timedelta, timezone

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

def get_recent_glyphic_calls():
    """Fetch calls from the last 25 hours."""
    since = datetime.now(timezone.utc) - timedelta(hours=25)
    calls = []
    cursor = None

    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor

        resp = requests.get(f"{GLYPHIC_BASE}/calls/", headers=GLYPHIC_HEADERS, params=params)
        resp.raise_for_status()
        data = resp.json()

        for call in data["data"]:
            raw_time = call.get("start_time")
            if raw_time:
                call_time = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                if call_time < since:
                    return calls
            if not is_internal_call(call):
                calls.append(call)

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
    participants = detail.get("participants", []) or []
    participant_by_id = {p["id"]: p for p in participants}

    participant_emails = [p["email"] for p in participants if p.get("email")]

    primary_email = next(
        (p["email"] for p in participants
         if p.get("email") and "halo.science" not in p["email"]),
        participant_emails[0] if participant_emails else None
    )

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

    start_time = detail.get("start_time")
    if not start_time:
        return None

    duration = detail.get("duration")
    if duration:
        try:
            start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            end_dt = start_dt + timedelta(seconds=duration)
            end_time = end_dt.isoformat()
        except Exception:
            end_time = start_time
    else:
        end_time = start_time

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

def main():
    print(f"Starting Glyphic → Pylon sync at {datetime.now(timezone.utc).isoformat()}")

    calls = get_recent_glyphic_calls()
    print(f"Found {len(calls)} external calls in the last 25 hours")

    success, failed = 0, 0

    for call in calls:
        call_id = call["id"]
        print(f"Processing call {call_id}...")

        try:
            detail = get_call_details(call_id)
            payload = build_pylon_payload(detail)

            if payload is None:
                print(f"  ⚠ Skipped: no start time")
                continue

            if not payload.get("call_recording_messages"):
                print(f"  ⚠ Skipped: no transcript available")
                continue

            time.sleep(1.0)

            resp = requests.post(
                f"{PYLON_BASE}/call-recordings",
                headers=PYLON_HEADERS,
                json=payload
            )

            if resp.status_code in (200, 201):
                print(f"  ✓ Synced: {payload['title']}")
                success += 1
            elif resp.status_code == 409:
                print(f"  ℹ Already exists (skipped): {payload['title']}")
            else:
                print(f"  ✗ Pylon error {resp.status_code}: {resp.text}")
                print(f"    Payload sent: {payload}")
                failed += 1

        except Exception as e:
            print(f"  ✗ Error processing call {call_id}: {e}")
            failed += 1

    print(f"\nSync complete. Success: {success}, Failed: {failed}")

if __name__ == "__main__":
    main()
