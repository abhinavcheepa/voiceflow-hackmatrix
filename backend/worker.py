"""Background worker for scheduled and automated jobs.

Runs inside the FastAPI process (started in the lifespan). Persists jobs in
the DB so they survive restarts. Uses atomic claiming (UPDATE ... WHERE
status='pending' RETURNING) to be safe even if two workers somehow start.

Job types:
    follow_up     — send a follow-up message after a delay
    retry_reply   — retry a failed auto-reply
    reminder      — scheduled reminder to a customer
    campaign_call — dial a campaign target

Usage:
    import worker
    worker.schedule("follow_up", {"phone": "+91...", "text": "..."}, delay_seconds=3600)
    worker.schedule("reminder", {...}, run_at="2026-10-22T10:00:00+05:30")
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

import db
import events

log = logging.getLogger("voiceflow.worker")

# How often the worker checks for pending jobs.
POLL_SECONDS = int(os.getenv("WORKER_POLL_SECONDS", "15"))

# Global state for dashboard status reporting.
_state = {
    "running": False,
    "last_activity": None,
    "jobs_processed": 0,
    "last_error": None,
}

# Controls — stored in DB settings, cached here and refreshed each tick.
AUTOMATION_KEY = "automation_enabled"


def state() -> dict:
    """Current worker state for the /health endpoint."""
    return {
        "running": _state["running"],
        "lastActivity": _state["last_activity"],
        "jobsProcessed": _state["jobs_processed"],
        "lastError": _state["last_error"],
    }


def automation_enabled() -> bool:
    """Whether the automation worker processes jobs. Controlled from the dashboard."""
    return db.get_setting(AUTOMATION_KEY, "on") == "on"


def set_automation_enabled(enabled: bool) -> None:
    db.set_setting(AUTOMATION_KEY, "on" if enabled else "off")
    events.broadcast("status_change", {"automation": enabled})


def schedule(job_type: str, payload: dict, delay_seconds: int = 0,
             run_at: str | None = None) -> int:
    """Enqueue a job. Returns the job ID."""
    if run_at is None:
        from datetime import timedelta
        run_at = (datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)).isoformat()
    return db.add_job(job_type, payload, run_at)


async def _process_job(job: dict) -> None:
    """Execute one claimed job. Catches everything — a bad job must never kill
    the worker loop."""
    job_type = job["job_type"]
    payload = job.get("payload", {})

    try:
        if job_type == "retry_reply":
            import whatsapp
            await whatsapp.reply_to(
                payload["phone"], payload.get("name"), payload["body"],
                payload.get("is_voice", False),
                payload.get("language", "Hindi"),
                payload.get("message_id"),
            )

        elif job_type == "follow_up":
            import whatsapp
            await whatsapp.send_text(payload["phone"], payload["text"])
            db.add_message(payload["phone"], "us", "text", payload["text"])

        elif job_type == "reminder":
            import push
            push.notify("Reminder", payload.get("text", "Scheduled reminder"),
                        {"url": payload.get("url", "/app")}, tag="reminder")

        else:
            log.warning("unknown job type: %s", job_type)

        db.complete_job(job["id"], "done")
        _state["jobs_processed"] += 1

    except Exception as e:  # noqa: BLE001
        error_text = f"{type(e).__name__}: {e}"[:200]
        log.warning("job %d (%s) failed: %s", job["id"], job_type, error_text)
        db.complete_job(job["id"], "failed", error_text)
        _state["last_error"] = error_text


async def run_forever() -> None:
    """Main worker loop. Started as an asyncio task in the FastAPI lifespan.

    ARCHITECTURE REQUIREMENT:
    Uvicorn must run with a SINGLE process worker (`workers=1`). The SSE broadcaster
    and background worker are in-process. Running multiple uvicorn workers would
    split SSE connections across processes and cause worker competition.
    """
    _state["running"] = True
    log.info("worker started (poll every %ds, single-worker architecture)", POLL_SECONDS)

    try:
        while True:
            try:
                # Crash recovery: revert orphaned jobs stuck in 'running' for >5 min
                recovered = db.recover_stuck_jobs(max_attempts=3, timeout_minutes=5)
                if recovered > 0:
                    log.warning("recovered %d stuck job(s) back to pending", recovered)

                if automation_enabled():
                    # Atomically claim one pending job whose run_at has passed.
                    job = db.claim_next_job()
                    if job:
                        _state["last_activity"] = datetime.now(timezone.utc).isoformat()
                        await _process_job(job)
                        continue  # check for more immediately
            except Exception as e:  # noqa: BLE001
                log.warning("worker tick failed: %s", e)
                _state["last_error"] = str(e)[:200]

            await asyncio.sleep(POLL_SECONDS)

    finally:
        _state["running"] = False
        log.info("worker stopped")
