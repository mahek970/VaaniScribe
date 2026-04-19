# VaaniScribe

VaaniScribe is a bilingual (Hindi + English / Hinglish) AI meeting assistant that turns conversations into structured notes and searchable team memory.

## Problem
Fast-moving teams lose decisions and action items in mixed-language meetings. Manual note-taking is inconsistent, and important context gets buried across chat threads and calls.

## Solution
VaaniScribe captures Hinglish meeting content, generates structured notes, and stores every meeting in a queryable memory layer so teams can ask follow-up questions later with source-backed answers.

## Why this matters
- Reduces post-meeting manual work
- Preserves organizational memory across sprints
- Makes bilingual communication searchable and reusable

## Theme alignment
- AI Productivity: automates repetitive meeting capture and note-taking work
- Enterprise Collaboration: creates a shared, query-able memory across teams
- Applied Generative AI: converts unstructured speech into structured, actionable outputs
- Responsible UX: includes source-backed retrieval so users can verify answers

## What is novel here
- Built specifically for Hindi + English code-switching meetings
- Combines realtime transcription, structured summarization, and persistent memory retrieval in one workflow
- Returns memory answers with source meetings to improve trust and verification

## Live Demo
- App: https://vaaniscribe-app-8fdte.ondigitalocean.app/
- Demo video: https://youtu.be/1p-40dC_Bsk
- Repository: https://github.com/mahek970/VaaniScribe

## What it does
- Captures meeting transcript from local microphone (Deepgram realtime)
- Generates structured notes (summary, decisions, action items, key points) using Gemini
- Saves transcript + notes to Snowflake
- Lets you ask questions about past meetings with source-backed responses
- Supports HTTP bridge mode so local transcriber can push live transcript to hosted Streamlit app

## Tech stack
- Streamlit: app UI
- Deepgram Nova-3: speech-to-text
- Gemini: summarization + meeting memory Q&A
- Snowflake: persistent meeting storage and retrieval

## Architecture (high level)
1. `transcribe.py` streams microphone audio to Deepgram and writes live transcript updates
2. `app.py` reads the live bridge and manages meeting flow
3. `summarise.py` converts transcript to structured notes and answers memory queries
4. `snowflake_utils.py` stores and retrieves meeting data from Snowflake
5. `bridge_api.py` (optional) accepts transcript updates over HTTP and serves bridge state to hosted app instances

## Project structure
- `app.py`: Streamlit interface and session flow
- `transcribe.py`: local realtime transcription and mic diagnostics
- `bridge_api.py`: lightweight HTTP bridge service (`/push`, `/state`, `/health`)
- `summarise.py`: Gemini note generation and memory Q&A
- `snowflake_utils.py`: Snowflake schema, insert, and query utilities
- `seed_data.py`: optional demo data seeding
- `run_app.ps1`: Windows launcher with localhost bind and smart port fallback

## Local setup
1. Create and activate a virtual environment
2. Install dependencies

```bash
pip install -r requirements.txt
```

3. Configure environment

```bash
copy .env.example .env
```

Fill all required keys in `.env`:
- Deepgram key
- Gemini key
- Snowflake account/user/password/warehouse/database/schema/role

Optional HTTP bridge keys (for local mic -> hosted app sync):
- `TRANSCRIPT_HTTP_PUSH_URL` (example: `https://bridge.example.com/push`)
- `TRANSCRIPT_HTTP_PULL_URL` (example: `https://bridge.example.com/state`)
- `TRANSCRIPT_HTTP_TOKEN` (shared secret used by both push and pull)

4. (Optional) Run realtime transcription

```bash
python transcribe.py
```

If you are using hosted Streamlit and local microphone, run bridge service and HTTP sync:

```bash
python bridge_api.py
```

Set env values as follows:
- On the machine running `transcribe.py`:
    - `TRANSCRIPT_HTTP_PUSH_URL=https://<bridge-host>/push`
    - `TRANSCRIPT_HTTP_TOKEN=<shared-secret>`
- On hosted app (`app.py`) environment:
    - `TRANSCRIPT_HTTP_PULL_URL=https://<bridge-host>/state`
    - `TRANSCRIPT_HTTP_TOKEN=<shared-secret>`

Useful microphone checks:

```bash
python transcribe.py --list-devices
python transcribe.py --doctor
python transcribe.py --doctor --doctor-apply
```

5. Start the Streamlit app

```powershell
powershell -ExecutionPolicy Bypass -File .\run_app.ps1
```

Open the printed localhost URL (typically `http://127.0.0.1:8501`).

## Snowflake schema setup
Run once in Snowsight:

```sql
CREATE DATABASE vaaniscribe;
USE DATABASE vaaniscribe;
CREATE SCHEMA meetings;

CREATE TABLE meetings.transcripts (
    meeting_id VARCHAR PRIMARY KEY,
    meeting_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    title VARCHAR,
    raw_transcript TEXT,
    language_mix VARCHAR DEFAULT 'hi-en'
);

CREATE TABLE meetings.summaries (
    meeting_id VARCHAR,
    summary TEXT,
    decisions VARIANT,
    action_items VARIANT,
    key_points VARIANT
);

CREATE TABLE meetings.chunks (
    chunk_id VARCHAR PRIMARY KEY,
    meeting_id VARCHAR,
    chunk_text TEXT,
    chunk_index INTEGER
);
```

Optional seed data:

```bash
python seed_data.py
```

## Deploy (DigitalOcean App Platform)
- Keep `Procfile` in repo root
- Connect GitHub repository to App Platform
- Set all environment variables from `.env.example`
- Deploy from `main` branch

Procfile command:

```procfile
web: streamlit run app.py --server.address 0.0.0.0 --server.port $PORT --server.headless true
```

## Cloud microphone note
Live microphone capture works in local mode. On cloud deployment, use pasted/uploaded transcript and memory query flow.

For near-live cloud sync from your local terminal, use HTTP bridge mode (`bridge_api.py` + `TRANSCRIPT_HTTP_*` env vars). This avoids relying on local files in hosted environments.

## Hackathon judging quick view
- Category fit: AI productivity, collaboration, and knowledge management
- End-to-end completeness: capture -> summarize -> persist -> retrieve
- Production readiness: deployed app, persistent backend, environment-based configuration
- Demo clarity: visible before/after value in under 2 minutes

## Business impact snapshot
- Saves team time by reducing manual meeting documentation overhead
- Improves follow-through by surfacing decisions and action items clearly
- Prevents knowledge loss by storing meeting context in a searchable memory layer

## Security
- Never commit `.env`
- Rotate any key that was ever exposed in logs/screenshots/chat

<!-- redeploy trigger -->
