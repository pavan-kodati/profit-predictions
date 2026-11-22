"""Job runner: records every run and stage, prevents overlapping runs, and raises a failure alert (plan E9)."""

from __future__ import annotations

import logging
import time
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from stockresearch.container import Container
from stockresearch.db import models as m
from stockresearch.db.session import session_scope
from stockresearch.services.repo import now_ist

log = logging.getLogger(__name__)
LOCK_HOURS = 3


class JobContext:
    def __init__(self, c: Container, run_id: int, job: str) -> None:
        self.c, self.run_id, self.job = c, run_id, job
        self.notes: list[str] = []

    @contextmanager
    def stage(self, name: str) -> Iterator[list[str]]:
        """Use as `with ctx.stage("Download prices") as detail: detail.append("750 stocks")`."""
        detail: list[str] = []
        with session_scope(self.c.sessions) as s:
            row = m.RunStage(run_id=self.run_id, name=name, started_at=now_ist(), status="running")
            s.add(row)
            s.flush()
            stage_id = row.id
        log.info("stage started", extra={"job": self.job, "stage": name, "run_id": self.run_id})
        status = "ok"
        try:
            yield detail
        except Exception as e:
            status = "failed"
            detail.append(f"{type(e).__name__}: {e}")
            raise
        finally:
            with session_scope(self.c.sessions) as s:
                done = s.get(m.RunStage, stage_id)
                if done:
                    done.finished_at = now_ist()
                    done.status = status
                    done.detail = "; ".join(detail)[:4000]

    def soft_stage(self, name: str, fn: Callable[[list[str]], Any]) -> Any:
        """A stage whose failure is recorded but does not stop the job (graceful degradation)."""
        try:
            with self.stage(name) as detail:
                return fn(detail)
        except Exception as e:
            self.notes.append(f"{name} failed: {e}")
            log.warning("stage failed, continuing", extra={"job": self.job, "stage": name}, exc_info=True)
            return None


def run_job(c: Container, job: str, fn: Callable[[JobContext], str | None], alert_on_failure: bool = True) -> int:
    """Run `fn` as a recorded job. Returns 0 on success, 1 on failure, 2 if skipped because one is running."""
    with session_scope(c.sessions) as s:
        running = s.scalar(select(m.Run).where(
            m.Run.job == job, m.Run.status == "running", m.Run.started_at >= now_ist() - timedelta(hours=LOCK_HOURS)))
        if running:
            log.warning("job already running; skipping", extra={"job": job, "run_id": running.id})
            return 2
        run = m.Run(job=job, started_at=now_ist(), status="running", notes="")
        s.add(run)
        s.flush()
        run_id = run.id
    ctx = JobContext(c, run_id, job)
    started = time.monotonic()
    status, error = "ok", ""
    try:
        summary = fn(ctx)
        if summary:
            ctx.notes.insert(0, summary)
    except Exception as e:
        status = "failed"
        error = f"{type(e).__name__}: {e}"
        log.exception("job failed", extra={"job": job, "run_id": run_id})
        ctx.notes.append(error)
        ctx.notes.append(traceback.format_exc(limit=5))
    with session_scope(c.sessions) as s:
        finished = s.get(m.Run, run_id)
        if finished:
            finished.finished_at = now_ist()
            finished.status = status
            finished.notes = "\n".join(ctx.notes)[:8000]
    log.info("job finished", extra={"job": job, "run_id": run_id, "stage": f"{status} in {time.monotonic() - started:.0f}s"})
    if status == "failed" and alert_on_failure:
        from stockresearch.services import notify

        notify.failure_alert(c, job, error)
    return 0 if status == "ok" else 1
