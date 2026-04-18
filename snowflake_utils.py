from __future__ import annotations

import json
import os
import uuid
from contextlib import closing
from datetime import datetime
from typing import Any

import snowflake.connector


def get_snowflake_connection() -> snowflake.connector.SnowflakeConnection:
    user = os.getenv("SNOWFLAKE_USER")
    password = os.getenv("SNOWFLAKE_PASSWORD")
    account = os.getenv("SNOWFLAKE_ACCOUNT")

    if not user or not password or not account:
        raise RuntimeError("Snowflake credentials are missing. Set SNOWFLAKE_USER, SNOWFLAKE_PASSWORD, and SNOWFLAKE_ACCOUNT.")

    kwargs: dict[str, Any] = {
        "user": user,
        "password": password,
        "account": account,
        "database": os.getenv("SNOWFLAKE_DATABASE", "VAANISCRIBE"),
        "schema": os.getenv("SNOWFLAKE_SCHEMA", "MEETINGS"),
    }

    warehouse = os.getenv("SNOWFLAKE_WAREHOUSE")
    role = os.getenv("SNOWFLAKE_ROLE")
    if warehouse:
        kwargs["warehouse"] = warehouse
    if role:
        kwargs["role"] = role

    return snowflake.connector.connect(**kwargs)


def init_schema() -> None:
    ddl = [
        "CREATE DATABASE IF NOT EXISTS VAANISCRIBE",
        "CREATE SCHEMA IF NOT EXISTS VAANISCRIBE.MEETINGS",
        """
        CREATE TABLE IF NOT EXISTS VAANISCRIBE.MEETINGS.TRANSCRIPTS (
            MEETING_ID VARCHAR PRIMARY KEY,
            MEETING_DATE TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            TITLE VARCHAR,
            RAW_TRANSCRIPT TEXT,
            LANGUAGE_MIX VARCHAR DEFAULT 'hi-en'
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS VAANISCRIBE.MEETINGS.SUMMARIES (
            MEETING_ID VARCHAR,
            SUMMARY TEXT,
            DECISIONS VARIANT,
            ACTION_ITEMS VARIANT,
            KEY_POINTS VARIANT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS VAANISCRIBE.MEETINGS.CHUNKS (
            CHUNK_ID VARCHAR PRIMARY KEY,
            MEETING_ID VARCHAR,
            CHUNK_TEXT TEXT,
            CHUNK_INDEX INTEGER
        )
        """,
    ]

    with closing(get_snowflake_connection()) as conn:
        with closing(conn.cursor()) as cur:
            for statement in ddl:
                cur.execute(statement)
        conn.commit()


def save_meeting(transcript: str, summary: dict[str, Any], title: str | None = None) -> str:
    if not transcript.strip():
        raise ValueError("Transcript is empty.")

    meeting_id = str(uuid.uuid4())
    meeting_title = title or f"Meeting {datetime.now().strftime('%d %b %Y')}"

    with closing(get_snowflake_connection()) as conn:
        with closing(conn.cursor()) as cur:
            cur.execute(
                """
                INSERT INTO TRANSCRIPTS (MEETING_ID, TITLE, RAW_TRANSCRIPT)
                VALUES (%s, %s, %s)
                """,
                (meeting_id, meeting_title, transcript),
            )

            cur.execute(
                """
                INSERT INTO SUMMARIES (MEETING_ID, SUMMARY, DECISIONS, ACTION_ITEMS, KEY_POINTS)
                VALUES (%s, %s, PARSE_JSON(%s), PARSE_JSON(%s), PARSE_JSON(%s))
                """,
                (
                    meeting_id,
                    summary.get("summary", ""),
                    json.dumps(summary.get("decisions", []), ensure_ascii=False),
                    json.dumps(summary.get("action_items", []), ensure_ascii=False),
                    json.dumps(summary.get("key_points", []), ensure_ascii=False),
                ),
            )

            words = transcript.split()
            chunk_size = 200
            chunks = [" ".join(words[i : i + chunk_size]) for i in range(0, len(words), chunk_size)]
            for idx, chunk_text in enumerate(chunks):
                cur.execute(
                    """
                    INSERT INTO CHUNKS (CHUNK_ID, MEETING_ID, CHUNK_TEXT, CHUNK_INDEX)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (str(uuid.uuid4()), meeting_id, chunk_text, idx),
                )

        conn.commit()

    return meeting_id


def query_past_meetings(user_question: str, limit: int = 5) -> list[dict[str, str]]:
    tokens = [token.strip(".,!?;:\"'()[]{}") for token in user_question.lower().split()]
    stop_words = {
        "kya",
        "hai",
        "tha",
        "thi",
        "mein",
        "main",
        "ka",
        "ki",
        "ke",
        "the",
        "is",
        "was",
        "what",
        "about",
        "tell",
        "me",
        "please",
        "from",
        "past",
        "meeting",
    }
    keywords = [w for w in tokens if w and w not in stop_words and len(w) > 2][:5]

    if not keywords:
        return []

    where_clause = " OR ".join(["c.CHUNK_TEXT ILIKE %s" for _ in keywords])
    params: list[Any] = [f"%{kw}%" for kw in keywords]
    params.append(limit)

    sql = f"""
        SELECT c.CHUNK_TEXT, t.MEETING_DATE, t.TITLE, t.MEETING_ID
        FROM CHUNKS c
        JOIN TRANSCRIPTS t ON c.MEETING_ID = t.MEETING_ID
        WHERE {where_clause}
        ORDER BY t.MEETING_DATE DESC, c.CHUNK_INDEX ASC
        LIMIT %s
    """

    rows: list[tuple[Any, Any, Any, Any]] = []
    with closing(get_snowflake_connection()) as conn:
        with closing(conn.cursor()) as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()

    return [
        {
            "chunk": str(row[0]),
            "date": str(row[1]),
            "title": str(row[2] or "Untitled"),
            "meeting_id": str(row[3]),
        }
        for row in rows
    ]


def get_recent_meetings(limit: int = 10) -> list[dict[str, str]]:
    sql = """
        SELECT MEETING_ID, MEETING_DATE, TITLE
        FROM TRANSCRIPTS
        ORDER BY MEETING_DATE DESC
        LIMIT %s
    """
    rows: list[tuple[Any, Any, Any]] = []

    with closing(get_snowflake_connection()) as conn:
        with closing(conn.cursor()) as cur:
            cur.execute(sql, (limit,))
            rows = cur.fetchall()

    return [
        {
            "meeting_id": str(row[0]),
            "date": str(row[1]),
            "title": str(row[2] or "Untitled"),
        }
        for row in rows
    ]
