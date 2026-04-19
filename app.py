from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv
import streamlit as st

from snowflake_utils import query_past_meetings, save_meeting
from summarise import answer_from_memory, correct_transcript_text, generate_meeting_notes


load_dotenv()

st.set_page_config(page_title="VaaniScribe", layout="wide", initial_sidebar_state="collapsed")

BRIDGE_PATH = Path(os.getenv("TRANSCRIPT_BRIDGE_PATH", "live_transcript.json"))
UI_REFRESH_SECONDS = max(1, int(os.getenv("TRANSCRIPT_UI_REFRESH_SECONDS", "2")))
BRIDGE_PULL_URL = os.getenv("TRANSCRIPT_HTTP_PULL_URL", "").strip()
BRIDGE_HTTP_TOKEN = os.getenv("TRANSCRIPT_HTTP_TOKEN", "").strip()
BRIDGE_HTTP_TIMEOUT = max(1.0, float(os.getenv("TRANSCRIPT_HTTP_TIMEOUT_SECONDS", "3")))
DEPLOYED_APP_URL = os.getenv("DIGITALOCEAN_APP_URL", "").strip()


def _init_state() -> None:
    defaults: dict[str, Any] = {
        "meeting_active": False,
        "transcript_text": "",
        "meeting_notes": None,
        "notes_raw_text": "",
        "saved_meeting_id": "",
        "notes_text": "",
        "notes_view_mode": "full",
        "rag_answer": "",
        "rag_sources": [],
        "bridge_enabled": True,
        "bridge_auto_sync": True,
        "bridge_last_seen_lines": 0,
        "bridge_last_seen_updated_at": 0.0,
        "local_transcriber_pid": 0,
        "session_type": "Meeting",
        "vaani_correct": True,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _load_bridge_state() -> dict[str, Any]:
    if BRIDGE_PULL_URL:
        try:
            req = Request(BRIDGE_PULL_URL, method="GET")
            if BRIDGE_HTTP_TOKEN:
                req.add_header("Authorization", f"Bearer {BRIDGE_HTTP_TOKEN}")
                req.add_header("X-Bridge-Token", BRIDGE_HTTP_TOKEN)
            with urlopen(req, timeout=BRIDGE_HTTP_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except (URLError, TimeoutError, ValueError, OSError):
            # Fallback to local file bridge if remote bridge is temporarily unavailable.
            pass

    if not BRIDGE_PATH.exists():
        return {
            "connected": False,
            "device": "",
            "interim": "",
            "final_lines": [],
            "updated_at": 0,
        }

    try:
        raw = BRIDGE_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {
                "connected": False,
                "device": "",
                "interim": "",
                "final_lines": [],
                "updated_at": 0,
            }
        return data
    except Exception:
        return {
            "connected": False,
            "device": "",
            "interim": "",
            "final_lines": [],
            "updated_at": 0,
        }


def _bridge_text(state: dict[str, Any]) -> str:
    lines = state.get("final_lines", [])
    if not isinstance(lines, list):
        return ""
    return "\n".join([str(line).strip() for line in lines if str(line).strip()]).strip()


def _notes_to_markdown(notes: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("Summary")
    lines.append(notes.get("summary", ""))
    lines.append("")
    lines.append("Key Decisions")
    for d in notes.get("decisions", []):
        lines.append(f"- {d}")
    lines.append("")
    lines.append("Action Items")
    for item in notes.get("action_items", []):
        lines.append(f"- {item.get('task', '')} | Owner: {item.get('owner', 'unassigned')} | Deadline: {item.get('deadline', 'not specified')}")
    lines.append("")
    lines.append("Key Points")
    for k in notes.get("key_points", []):
        lines.append(f"- {k}")
    return "\n".join(lines).strip()


def _simplify_notes_to_markdown(notes: dict[str, Any]) -> str:
    lines: list[str] = []
    summary = str(notes.get("summary", "")).strip()
    if summary:
        short_summary = summary.split(".")[0].strip()
        if short_summary and not short_summary.endswith("."):
            short_summary += "."
        lines.append("Simple Summary")
        lines.append(short_summary or summary)
        lines.append("")

    decisions = [str(item).strip() for item in notes.get("decisions", []) if str(item).strip()]
    if decisions:
        lines.append("Top Decisions")
        for item in decisions[:3]:
            lines.append(f"- {item}")
        lines.append("")

    action_items = notes.get("action_items", [])
    if isinstance(action_items, list) and action_items:
        lines.append("Action Items")
        for item in action_items[:3]:
            if isinstance(item, dict):
                task = str(item.get("task", "")).strip()
                owner = str(item.get("owner", "unassigned")).strip() or "unassigned"
                deadline = str(item.get("deadline", "not specified")).strip() or "not specified"
                if task:
                    lines.append(f"- {task} ({owner}, {deadline})")
        lines.append("")

    key_points = [str(item).strip() for item in notes.get("key_points", []) if str(item).strip()]
    if key_points:
        lines.append("Key Points")
        for item in key_points[:4]:
            lines.append(f"- {item}")

    return "\n".join(lines).strip()


def _render_notes(notes: dict[str, Any]) -> None:
    st.subheader("Meeting Notes")
    st.markdown("**Summary**")
    st.write(notes.get("summary", ""))

    st.markdown("**Key Decisions**")
    decisions = notes.get("decisions", [])
    if decisions:
        for d in decisions:
            st.write(f"- {d}")
    else:
        st.write("- None captured")

    st.markdown("**Action Items**")
    action_items = notes.get("action_items", [])
    if action_items:
        for item in action_items:
            st.write(
                f"- {item.get('task', '')} | Owner: {item.get('owner', 'unassigned')} | "
                f"Deadline: {item.get('deadline', 'not specified')}"
            )
    else:
        st.write("- None captured")

    st.markdown("**Key Points**")
    key_points = notes.get("key_points", [])
    if key_points:
        for p in key_points:
            st.write(f"- {p}")
    else:
        st.write("- None captured")


def _can_save_to_snowflake() -> bool:
    return bool(
        os.getenv("SNOWFLAKE_USER")
        and os.getenv("SNOWFLAKE_PASSWORD")
        and os.getenv("SNOWFLAKE_ACCOUNT")
    )


def _is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _start_local_transcriber() -> tuple[bool, str]:
    transcribe_path = Path(__file__).with_name("transcribe.py")
    if not transcribe_path.exists():
        return False, f"transcribe.py not found at {transcribe_path}"

    python_exec = Path(sys.executable)
    if os.name == "nt":
        pythonw = python_exec.with_name("pythonw.exe")
        if pythonw.exists():
            python_exec = pythonw

    command = [str(python_exec), str(transcribe_path)]
    creationflags = 0
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )

    try:
        proc = subprocess.Popen(
            command,
            cwd=str(transcribe_path.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except Exception as exc:
        return False, f"Failed to launch transcribe.py: {exc}"

    st.session_state.local_transcriber_pid = int(proc.pid)
    return True, f"Local mic process started (PID {proc.pid})."


def _stop_local_transcriber() -> tuple[bool, str]:
    pid = int(st.session_state.get("local_transcriber_pid") or 0)
    if pid <= 0:
        return False, "No tracked local mic process to stop."

    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
            )
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception as exc:
        return False, f"Failed to stop local mic process {pid}: {exc}"

    st.session_state.local_transcriber_pid = 0
    return True, f"Stopped local mic process {pid}."


_init_state()

st.markdown(
    """
    <style>
        .stApp {
            background: radial-gradient(circle at top, rgba(0, 191, 165, 0.14), transparent 32%),
                        linear-gradient(180deg, #0f172a 0%, #111827 100%);
        }
        .app-hero {
            padding: 1.1rem 1.25rem;
            border-radius: 1rem;
            border: 1px solid rgba(255, 255, 255, 0.12);
            background: rgba(255, 255, 255, 0.04);
            box-shadow: 0 18px 45px rgba(0, 0, 0, 0.22);
            margin-bottom: 1rem;
        }
        .app-hero h1 {
            margin: 0;
            font-size: 2rem;
            line-height: 1.1;
            color: #f8fafc;
        }
        .app-hero p {
            margin: 0.4rem 0 0;
            color: rgba(248, 250, 252, 0.82);
            font-size: 1rem;
        }
    </style>
    <div class="app-hero">
        <h1>VaaniScribe</h1>
        <p>Hindi + English (Hinglish) AI meeting assistant</p>
    </div>
    """,
    unsafe_allow_html=True,
)

st.success("App loaded. If the screen ever looks empty, refresh the tab or reopen the active Streamlit URL.")

left, right = st.columns([55, 45], gap="large")

with left:
    st.subheader("Live Transcript")
    st.info(
        "Run transcribe.py locally for live mic transcription. Use local bridge sync below, or paste manually as fallback. "
        "Cloud browsers generally cannot stream your system mic directly to server-side Python."
    )

    bridge_state = _load_bridge_state()
    final_lines = bridge_state.get("final_lines", []) if isinstance(bridge_state.get("final_lines", []), list) else []
    line_count = len(final_lines)
    bridge_updated_at = float(bridge_state.get("updated_at", 0) or 0)
    bridge_connected = bool(bridge_state.get("connected", False))
    bridge_device = str(bridge_state.get("device", "") or "unknown")
    interim = str(bridge_state.get("interim", "") or "")
    bridge_source = "http" if BRIDGE_PULL_URL else "file"

    st.caption(
        f"Bridge: {'connected' if bridge_connected else 'offline'} | Source: {bridge_source} | Device: {bridge_device} | Final lines: {line_count}"
    )
    if st.session_state.bridge_enabled:
        if BRIDGE_PULL_URL:
            if not bridge_connected:
                st.warning("HTTP bridge is configured but currently offline/unreachable. Check TRANSCRIPT_HTTP_PULL_URL, token, and bridge service health.")
        elif DEPLOYED_APP_URL:
            st.warning(
                "Hosted app detected but HTTP bridge is not configured. Set TRANSCRIPT_HTTP_PULL_URL + TRANSCRIPT_HTTP_TOKEN on the hosted app and TRANSCRIPT_HTTP_PUSH_URL + TRANSCRIPT_HTTP_TOKEN where transcribe.py runs."
            )
    if interim:
        st.caption(f"Interim: {interim}")

    tracked_pid = int(st.session_state.get("local_transcriber_pid") or 0)
    mic_running = _is_process_running(tracked_pid)
    if not mic_running and tracked_pid > 0:
        st.session_state.local_transcriber_pid = 0
        tracked_pid = 0

    st.caption(f"Local mic process: {'running' if mic_running else 'stopped'}" + (f" (PID {tracked_pid})" if tracked_pid else ""))

    st.caption("VaaniScribe was built for equity — it works for students with dyslexia, physical disabilities, and non-native speakers.")
    st.caption("Every meeting is stored permanently — your organization builds institutional memory over time.")

    bridge_controls = st.columns([1, 1, 1])
    with bridge_controls[0]:
        st.session_state.bridge_enabled = st.checkbox(
            "Use Live Bridge",
            value=st.session_state.bridge_enabled,
            help="Reads transcript from live_transcript.json written by transcribe.py.",
        )
    with bridge_controls[1]:
        st.session_state.bridge_auto_sync = st.checkbox(
            "Auto Sync",
            value=st.session_state.bridge_auto_sync,
            disabled=not st.session_state.bridge_enabled,
        )
    with bridge_controls[2]:
        sync_clicked = st.button(
            "Sync Live Feed",
            use_container_width=True,
            disabled=not st.session_state.bridge_enabled,
        )

    if st.session_state.bridge_enabled and sync_clicked:
        st.session_state.transcript_text = _bridge_text(bridge_state)
        st.session_state.bridge_last_seen_lines = line_count
        st.session_state.bridge_last_seen_updated_at = bridge_updated_at

    mic_controls = st.columns([1, 1, 1])
    with mic_controls[0]:
        start_mic_clicked = st.button("Start Mic", use_container_width=True, disabled=mic_running)
    with mic_controls[1]:
        stop_mic_clicked = st.button("Stop Mic", use_container_width=True, disabled=not mic_running)
    with mic_controls[2]:
        st.caption("Starts/stops local transcribe.py for live bridge audio.")

    if start_mic_clicked:
        ok, msg = _start_local_transcriber()
        if ok:
            st.success(msg)
        else:
            st.error(msg)

    if stop_mic_clicked:
        ok, msg = _stop_local_transcriber()
        if ok:
            st.info(msg)
        else:
            st.warning(msg)

    controls = st.columns([1, 1, 2])
    with controls[0]:
        start_clicked = st.button("Start Meeting", use_container_width=True)
        if start_clicked:
            st.session_state.meeting_active = True
            st.session_state.meeting_notes = None
            st.session_state.saved_meeting_id = ""
            st.session_state.rag_answer = ""
            st.session_state.rag_sources = []
            st.session_state.transcript_text = ""
            st.session_state.bridge_last_seen_lines = line_count
            st.session_state.bridge_last_seen_updated_at = bridge_updated_at
            if st.session_state.bridge_enabled and not bridge_connected:
                ok, msg = _start_local_transcriber()
                if ok:
                    st.info("Bridge was offline. Auto-started local mic capture.")
                    st.success(msg)
                else:
                    st.warning(
                        "Start Meeting begins a notes session only. Auto-start failed; click Start Mic or run python transcribe.py in a terminal."
                    )
                    st.error(msg)

    with controls[1]:
        end_clicked = st.button("End Meeting", use_container_width=True)

    st.session_state.session_type = st.selectbox(
        "Session Type",
        options=["Meeting", "Lecture", "Lab Report"],
        index=["Meeting", "Lecture", "Lab Report"].index(st.session_state.session_type)
        if st.session_state.session_type in ["Meeting", "Lecture", "Lab Report"]
        else 0,
        help="Choose how the transcript should be framed before summarization.",
    )

    st.session_state.vaani_correct = st.checkbox(
        "VaaniCorrect",
        value=st.session_state.vaani_correct,
        help="VaaniCorrect automatically fixes Indian accent mishearings before summarizing.",
    )

    st.session_state.transcript_text = st.text_area(
        "Transcript",
        value=st.session_state.transcript_text,
        height=320,
        placeholder="Paste or type transcript here. Example: Toh aaj hum deadline discuss karenge...",
        disabled=not st.session_state.meeting_active,
    )

    meeting_title = st.text_input("Meeting Title (optional)", value="")

    if end_clicked:
        transcript = st.session_state.transcript_text.strip()
        if not transcript:
            st.error("Transcript is empty. Add some transcript text before ending the meeting.")
        else:
            notes_error: Exception | None = None
            transcript_for_summary = transcript
            if st.session_state.vaani_correct:
                transcript_for_summary = correct_transcript_text(transcript, st.session_state.session_type)

            with st.spinner("Generating notes with Gemini..."):
                try:
                    notes = generate_meeting_notes(
                        f"Session type: {st.session_state.session_type}\n\n{transcript_for_summary}"
                    )
                except Exception as exc:
                    notes_error = exc
                    # Keep pipeline moving even if Gemini has a temporary issue.
                    notes = {
                        "summary": "Auto-generated notes unavailable. Raw transcript was saved.",
                        "decisions": [],
                        "action_items": [],
                        "key_points": [],
                    }

            if notes_error is not None:
                st.warning("Gemini notes generation failed. Saving transcript with fallback notes.")
                st.error(
                    "Gemini is unavailable right now (model/quota issue). "
                    "Transcript was still saved to Snowflake with fallback notes."
                )
                with st.expander("Gemini error details"):
                    st.code(str(notes_error))

            if notes:
                st.session_state.meeting_notes = notes
                st.session_state.notes_raw_text = _notes_to_markdown(notes)
                st.session_state.notes_text = st.session_state.notes_raw_text
                st.session_state.notes_view_mode = "full"
                st.session_state.meeting_active = False

                if _can_save_to_snowflake():
                    with st.spinner("Saving meeting to Snowflake..."):
                        try:
                            meeting_id = save_meeting(
                                transcript=transcript,
                                summary=notes,
                                title=meeting_title.strip() or None,
                            )
                            st.session_state.saved_meeting_id = meeting_id
                            st.success("Meeting saved to Snowflake.")
                        except Exception as exc:
                            st.warning("Notes generated, but Snowflake save failed.")
                            st.exception(exc)
                else:
                    st.warning("Snowflake credentials not set. Notes were generated but not saved.")

    if (
        st.session_state.bridge_enabled
        and st.session_state.bridge_auto_sync
        and not end_clicked
    ):
        if (
            line_count != st.session_state.bridge_last_seen_lines
            or bridge_updated_at != float(st.session_state.bridge_last_seen_updated_at or 0)
        ):
            st.session_state.transcript_text = _bridge_text(bridge_state)
            st.session_state.bridge_last_seen_lines = line_count
            st.session_state.bridge_last_seen_updated_at = bridge_updated_at
        time.sleep(UI_REFRESH_SECONDS)
        st.rerun()

with right:
    notes = st.session_state.meeting_notes
    if notes:
        if st.session_state.notes_view_mode == "simple":
            st.subheader("Simplified Notes")
            st.write(_simplify_notes_to_markdown(notes))
        else:
            _render_notes(notes)

        if st.session_state.saved_meeting_id:
            st.caption(f"Meeting ID: {st.session_state.saved_meeting_id}")

        c1, c2, c3 = st.columns(3)
        with c1:
            st.download_button(
                "Copy Notes (txt)",
                st.session_state.notes_text,
                file_name="meeting_notes.txt",
                mime="text/plain",
                use_container_width=True,
            )
        with c2:
            if st.button("Simplify", use_container_width=True):
                st.session_state.notes_text = _simplify_notes_to_markdown(notes)
                st.session_state.notes_view_mode = "simple"
                st.rerun()
        with c3:
            if st.button("New Meeting", use_container_width=True):
                st.session_state.meeting_active = False
                st.session_state.transcript_text = ""
                st.session_state.meeting_notes = None
                st.session_state.saved_meeting_id = ""
                st.session_state.notes_text = ""
                st.session_state.notes_raw_text = ""
                st.session_state.notes_view_mode = "full"
                st.session_state.rag_answer = ""
                st.session_state.rag_sources = []
                st.rerun()
    else:
        st.subheader("Meeting Notes")
        st.write("No notes yet. Start a meeting, add transcript, then click End Meeting.")

st.divider()
st.subheader("Ask about past meetings")
question = st.text_input("Type your question in Hindi or English")

if st.button("Search Memory"):
    if not question.strip():
        st.error("Please type a question.")
    elif not _can_save_to_snowflake():
        st.error("Snowflake credentials are missing. Set SNOWFLAKE_USER, SNOWFLAKE_PASSWORD, SNOWFLAKE_ACCOUNT.")
    else:
        with st.spinner("Searching memory..."):
            try:
                chunks = query_past_meetings(question)
                answer = answer_from_memory(question, chunks)
                st.session_state.rag_answer = answer
                st.session_state.rag_sources = chunks
            except Exception as exc:
                st.exception(exc)

if st.session_state.rag_answer:
    st.markdown("**Answer**")
    st.write(st.session_state.rag_answer)

if st.session_state.rag_sources:
    st.markdown("**Sources**")
    for item in st.session_state.rag_sources:
        st.write(f"- {item['title']} ({item['date']})")
        st.caption(item["chunk"][:350] + ("..." if len(item["chunk"]) > 350 else ""))

with st.expander("Debug JSON output"):
    if st.session_state.meeting_notes:
        st.code(json.dumps(st.session_state.meeting_notes, indent=2, ensure_ascii=False), language="json")

st.caption("Powered by Deepgram Nova-3, Google Gemini, Snowflake, and DigitalOcean")
