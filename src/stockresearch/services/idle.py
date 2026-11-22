"""When the server may switch itself off (plan v4, section 4).

The server is started on weekdays by an AWS schedule and stops itself when there is nothing left to do. The decision
lives here, in plain code with tests; the shell script on the server only asks `stockresearch idle-check` and powers
off when the answer is yes. If anything is unclear (for example the uptime cannot be read) the answer is "stay on":
staying on costs a little money, switching off too early could lose work.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from stockresearch.config import Settings
from stockresearch.db import models as m
from stockresearch.services import repo

# Monday to Friday, the server stays on for the day's scheduled jobs: publish 08:00, open check 09:20, market close 19:00.
WORK_START = time(7, 15)
WORK_END = time(20, 30)
MIN_UPTIME_MIN = 20  # never switch off right after a start, so a manual start gets time to be used
VISIT_GRACE_MIN = 30  # stay on this long after the last signed-in page view
QUEUE_MAX_AGE_HOURS = 6  # research requested longer ago than this no longer keeps the server on
JOB_MAX_AGE_HOURS = 3  # a job row still marked "running" after this long is a leftover, not real work
VISIT_FILE = ".last_visit"
VISIT_WRITE_EVERY = timedelta(seconds=60)


@dataclass(frozen=True)
class Decision:
    ok: bool  # True = it is fine to switch off now
    reason: str


def in_work_window(now: datetime) -> bool:
    return now.weekday() < 5 and WORK_START <= now.time() < WORK_END


def decide(now: datetime, uptime_min: float | None, running_jobs: int, queue_items: int,
           last_visit: datetime | None) -> Decision:
    """The rules, in the order they are checked. Pure: no database, no clock."""
    if uptime_min is None:
        return Decision(False, "the uptime is unknown")
    if uptime_min < MIN_UPTIME_MIN:
        return Decision(False, f"it started only {uptime_min:.0f} minutes ago")
    if in_work_window(now):
        return Decision(False, f"it is inside the weekday work window ({WORK_START:%H:%M} to {WORK_END:%H:%M})")
    if running_jobs:
        return Decision(False, f"{running_jobs} job(s) are still running")
    if queue_items:
        return Decision(False, f"{queue_items} stock(s) are still waiting to be researched")
    if last_visit is not None and now - last_visit < timedelta(minutes=VISIT_GRACE_MIN):
        return Decision(False, f"you used the website {int((now - last_visit).total_seconds() // 60)} minutes ago")
    return Decision(True, "no jobs, nothing queued, outside the work window and nobody is using the website")


# ---------------------------------------------------------------- visits (a signed-in page view)

def touch_visit(data_dir: Path, now: datetime | None = None) -> None:
    """Remember that the signed-in user just opened a page. Written at most once a minute."""
    path = data_dir / VISIT_FILE
    now = now or repo.now_ist()
    try:
        last = read_last_visit(data_dir)
        if last is None or now - last >= VISIT_WRITE_EVERY:
            data_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(now.isoformat(), encoding="utf-8")
    except OSError:
        pass  # never let a bookkeeping file break a page


def read_last_visit(data_dir: Path) -> datetime | None:
    try:
        return datetime.fromisoformat((data_dir / VISIT_FILE).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- gathering the facts

def uptime_minutes(path: str = "/proc/uptime") -> float | None:
    """Minutes since the machine booted. Inside a container this is the host's uptime."""
    try:
        return float(Path(path).read_text().split()[0]) / 60
    except (OSError, ValueError, IndexError):
        return None


def check(settings: Settings, sessions: sessionmaker[Session], now: datetime | None = None,
          uptime_min: float | None = None) -> Decision:
    now = now or repo.now_ist()
    uptime = uptime_min if uptime_min is not None else uptime_minutes()
    with sessions() as s:
        jobs = s.scalar(select(func.count()).select_from(m.Run).where(
            m.Run.status == "running", m.Run.started_at >= now - timedelta(hours=JOB_MAX_AGE_HOURS))) or 0
        queued = s.scalar(select(func.count()).select_from(m.ResearchQueueItem).where(
            m.ResearchQueueItem.status.in_(("pending", "running")),
            m.ResearchQueueItem.requested_at >= now - timedelta(hours=QUEUE_MAX_AGE_HOURS))) or 0
    return decide(now, uptime, jobs, queued, read_last_visit(settings.data_dir))
