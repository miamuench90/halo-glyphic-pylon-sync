# halo-glyphic-pylon-sync

A Python script that automatically syncs external customer calls from Glyphic to Pylon's call recorder on a daily schedule.

## What it does

Fetches calls from the Glyphic API from the past 25 hours and posts them to Pylon, filtering out internal and vendor meetings so only customer-facing calls are synced. Duplicate calls are handled gracefully — Pylon rejects them with a 409 and the script skips them automatically.

## Filtering logic

Calls tagged with any Internal / Vendor Meetings tag (hiring interviews, pipeline meetings, sales standups, team meetings, 1:1s, vendor meetings) are excluded. Everything else syncs.

## Schedule

Runs daily at 9:00 AM UTC via GitHub Actions.

## Setup

Add the following secrets to your GitHub repository under **Settings > Secrets and variables > Actions**:

- `GLYPHIC_API_KEY`
- `PYLON_API_KEY`

## Built with

Python, GitHub Actions, Glyphic API, Pylon API
