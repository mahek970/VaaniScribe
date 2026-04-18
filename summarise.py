from __future__ import annotations

import json
import os
import re
from typing import Any

from dotenv import load_dotenv
import google.generativeai as genai


load_dotenv()

SUMMARY_PROMPT = """
You are a meeting notes assistant for Indian professionals who speak in Hindi and English mixed together.

You will receive a raw meeting transcript that may contain Hindi words, English words, and mid-sentence language switches. This is normal and not an error.

From this transcript, extract and return a JSON object with exactly these keys:
{
  "summary": "2-3 sentence overview of what the meeting was about",
  "decisions": ["decision 1", "decision 2"],
  "action_items": [
    {"task": "what needs to be done", "owner": "person name or unassigned", "deadline": "mentioned deadline or not specified"}
  ],
  "key_points": ["point 1", "point 2"]
}

Rules:
- Write output in English
- If a name appears in Hindi, keep it as-is
- Do not invent facts
- Action items must be concrete tasks
- Return only valid JSON with no markdown fences

Transcript:
{transcript}
"""

RAG_PROMPT = """
You are a meeting memory assistant for an Indian professional.
You have been given excerpts from past meeting transcripts which may be Hindi, English, or mixed Hinglish.
Answer the user's question using only the provided excerpts.
If the answer is not clearly present, say so honestly.
Answer in the same language as the user question.
Always mention which meeting the information came from and when it happened.

Past meeting excerpts:
{context}

User question: {question}

Answer:
"""


def _get_model() -> Any:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set.")
    genai.configure(api_key=api_key)
    model_name = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
    return genai.GenerativeModel(model_name)


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        raise ValueError("Gemini response did not contain JSON.")

    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("Gemini JSON output is not an object.")
    return parsed


def _normalize_summary(payload: dict[str, Any]) -> dict[str, Any]:
    summary = str(payload.get("summary", "")).strip()

    decisions = payload.get("decisions", [])
    if not isinstance(decisions, list):
        decisions = []
    decisions = [str(d).strip() for d in decisions if str(d).strip()]

    raw_items = payload.get("action_items", [])
    action_items: list[dict[str, str]] = []
    if isinstance(raw_items, list):
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            task = str(item.get("task", "")).strip()
            owner = str(item.get("owner", "unassigned")).strip() or "unassigned"
            deadline = str(item.get("deadline", "not specified")).strip() or "not specified"
            if task:
                action_items.append({"task": task, "owner": owner, "deadline": deadline})

    key_points = payload.get("key_points", [])
    if not isinstance(key_points, list):
        key_points = []
    key_points = [str(k).strip() for k in key_points if str(k).strip()]

    return {
        "summary": summary,
        "decisions": decisions,
        "action_items": action_items,
        "key_points": key_points,
    }


def generate_meeting_notes(transcript: str) -> dict[str, Any]:
    transcript = transcript.strip()
    if not transcript:
        raise ValueError("Transcript is empty.")

    model = _get_model()
    prompt = SUMMARY_PROMPT.format(transcript=transcript)
    response = model.generate_content(prompt)

    if not response or not getattr(response, "text", ""):
        raise RuntimeError("Gemini returned an empty response.")

    parsed = _extract_json(response.text)
    return _normalize_summary(parsed)


def answer_from_memory(user_question: str, retrieved_chunks: list[dict[str, str]]) -> str:
    if not user_question.strip():
        raise ValueError("Question is empty.")

    if not retrieved_chunks:
        return "Mujhe is baare mein koi relevant past meeting nahi mili."

    context = "\n\n".join(
        [
            f"[Meeting: {item.get('title', 'Untitled')} on {item.get('date', 'unknown date')}]\n"
            f"{item.get('chunk', '')}"
            for item in retrieved_chunks
        ]
    )

    model = _get_model()
    prompt = RAG_PROMPT.format(context=context, question=user_question.strip())
    response = model.generate_content(prompt)

    if not response or not getattr(response, "text", ""):
        return "Could not generate an answer from memory right now."

    return response.text.strip()
