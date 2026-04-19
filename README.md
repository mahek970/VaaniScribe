# VaaniScribe

Bilingual meeting assistant for Hindi + English (Hinglish) meetings.

## Stack
- Deepgram Nova-3 for real-time speech-to-text
- Gemini for structured notes and memory answers
- Snowflake for persistent meeting memory
- Streamlit for UI

## Project files
- `transcribe.py`: local real-time Deepgram microphone transcription
- `app.py`: Streamlit app for notes generation + memory query
- `summarise.py`: Gemini note generation and RAG answer logic
- `snowflake_utils.py`: Snowflake save/query utilities
- `seed_data.py`: seed fake meetings for demo

## Live bridge (terminal -> app)
`transcribe.py` now writes live transcript state to `live_transcript.json` (configurable with `TRANSCRIPT_BRIDGE_PATH`).

In the Streamlit app:
- Enable `Use Live Bridge`
- Click `Start Meeting`
- Keep `Auto Sync` on to pull new final transcript lines continuously
- Manual `Sync Live Feed` is available as fallback

This lets local transcription appear directly in the app without copy/paste.

## Audio diagnostics for users
Before starting live transcription, users can run:

```bash
python transcribe.py --list-devices
```

One-command mic preflight with PASS/FAIL and device suggestion:

```bash
python transcribe.py --doctor
```

Auto-apply suggested device directly to `.env`:

```bash
python transcribe.py --doctor --doctor-apply
```

Skip interactive prompt behavior:

```bash
python transcribe.py --doctor --doctor-no-prompt
```

Optional custom primary test duration:

```bash
python transcribe.py --doctor --doctor-seconds 6
```

Then set the best input in `.env`:

```dotenv
DEEPGRAM_INPUT_DEVICE=<device index or exact name>
```

During transcription, the script warns about:
- No mic frames captured
- Silent mic input
- Audio flowing but no transcript (key/model/language issue)

## Quick start
1. Create and activate a virtual environment.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and set all keys.
4. Run local transcription (optional):
   ```bash
   python transcribe.py
   ```
    To list usable microphone devices:
    ```bash
    python transcribe.py --list-devices
    ```
5. Run app:
   ```bash
    powershell -ExecutionPolicy Bypass -File .\run_app.ps1
   ```

    Always open the local URL only:
    ```
    http://127.0.0.1:8501
    ```

    If port 8501 is busy, the launcher auto-selects a nearby free port and prints the exact URL.

## Snowflake setup
Run the schema setup once in Snowsight:

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

## Seed demo memory
After Snowflake credentials are set:

```bash
python seed_data.py
```

## Deploy (DigitalOcean App Platform)
- Ensure `Procfile` exists in repo root.
- Connect GitHub repo to App Platform.
- Add env vars from `.env.example`.
- Every push to main triggers auto-deploy.

## Note on live mic in deployment
Cloud-deployed Streamlit apps generally cannot capture your local microphone directly in server-side Python. Use local mode for live mic demo and deployed mode for transcript upload/paste and memory search.
