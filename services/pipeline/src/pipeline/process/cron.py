"""Cron-trigger scheduling (spec §6, the ingest-scheduler pattern).

Pure due-calculation over a five-field schedule, so "did this source come due"
is testable without a clock or a database.

We evaluate cron ourselves rather than registering one periodic job per source
because sources are USER DATA: they are created, edited and deleted at
runtime, while the queue backend's periodic registry is fixed at worker
startup. One tick that asks "which sources are due?" keeps the schedule in the
database where it belongs.
"""

from __future__ import annotations

import datetime as dt

from pipeline.process.config import ProcessConfigError, parse_process_trigger


def _field_matches(field: str, value: int) -> bool:
    """One cron field against one time component.

    Supports the forms the write gate admits: ``*``, a number, ``a-b``, a
    comma list, and any of those with ``/step``.
    """
    for part in field.split(","):
        step = 1
        spec = part
        if "/" in part:
            spec, _, step_text = part.partition("/")
            if not step_text.isdigit() or int(step_text) < 1:
                continue
            step = int(step_text)
        if spec == "*":
            if value % step == 0:
                return True
            continue
        if "-" in spec:
            low_text, _, high_text = spec.partition("-")
            if not (low_text.isdigit() and high_text.isdigit()):
                continue
            low, high = int(low_text), int(high_text)
            if low <= value <= high and (value - low) % step == 0:
                return True
            continue
        if spec.isdigit() and int(spec) == value:
            return True
    return False


def matches_minute(schedule: str, moment: dt.datetime) -> bool:
    """True when a five-field schedule fires in ``moment``'s minute."""
    fields = schedule.split()
    if len(fields) != 5:
        return False
    minute, hour, day, month, weekday = fields
    # Cron's weekday accepts both 0 and 7 for Sunday; Python's weekday() is
    # Monday=0, so translate rather than comparing raw.
    dow = (moment.weekday() + 1) % 7
    return (
        _field_matches(minute, moment.minute)
        and _field_matches(hour, moment.hour)
        and _field_matches(day, moment.day)
        and _field_matches(month, moment.month)
        and (
            _field_matches(weekday, dow)
            or (dow == 0 and _field_matches(weekday, 7))
        )
    )


def is_due(
    trigger: dict, now: dt.datetime, last_run_at: dt.datetime | None
) -> bool:
    """Whether a cron source should enqueue a run on this tick.

    The last-run guard is what makes a minute-granular tick idempotent: two
    ticks inside the same minute (a retry, an overlapping worker) must not
    produce two runs for one scheduled slot.
    """
    try:
        parsed = parse_process_trigger(trigger)
    except ProcessConfigError:
        return False
    if parsed.kind != "cron" or not parsed.schedule:
        return False
    if not matches_minute(parsed.schedule, now):
        return False
    if last_run_at is not None:
        elapsed = now - last_run_at
        if elapsed < dt.timedelta(minutes=1):
            return False
    return True
