from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import scheduler


def test_setup_registers_one_job() -> None:
    s = scheduler.setup_scheduler()
    job_ids = [j.id for j in s.get_jobs()]
    assert job_ids == ["expiry_check"]


@pytest.mark.asyncio
async def test_expiry_check_job_calls_digest() -> None:
    with patch("scheduler.notifications.send_expiry_digest", AsyncMock()) as m:
        await scheduler.expiry_check_job()
    m.assert_awaited_once()
