from __future__ import annotations


def test_scheduler_has_three_jobs() -> None:
    from scheduler import setup_scheduler

    scheduler = setup_scheduler()
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert "check_reminders" in job_ids
    assert "send_digest" in job_ids
    assert "lifecycle_check" in job_ids


def test_check_reminders_runs_at_09_00() -> None:
    from scheduler import setup_scheduler

    scheduler = setup_scheduler()
    job = next(j for j in scheduler.get_jobs() if j.id == "check_reminders")
    # CronTrigger stores fields; verify hour and minute
    trigger = job.trigger
    fields = {f.name: str(f) for f in trigger.fields}
    assert fields["hour"] == "9"
    assert fields["minute"] == "0"


def test_send_digest_runs_at_09_05() -> None:
    from scheduler import setup_scheduler

    scheduler = setup_scheduler()
    job = next(j for j in scheduler.get_jobs() if j.id == "send_digest")
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["hour"] == "9"
    assert fields["minute"] == "5"


def test_lifecycle_check_runs_on_monday() -> None:
    from scheduler import setup_scheduler

    scheduler = setup_scheduler()
    job = next(j for j in scheduler.get_jobs() if j.id == "lifecycle_check")
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["day_of_week"] == "mon"
    assert fields["hour"] == "10"
