"""Token máy: ký cho đúng một training job, không mượn được quyền user."""

from datetime import timedelta

import pytest
from fastapi import HTTPException

from src.services.security import (
    create_access_token,
    create_machine_token,
    current_machine_job,
    decode_machine_token,
)

ONE_HOUR = timedelta(hours=1)


def test_round_trip_returns_job_id():
    token = create_machine_token("job-a", ONE_HOUR)
    assert decode_machine_token(token) == "job-a"


def test_current_machine_job_accepts_matching_job():
    token = create_machine_token("job-a", ONE_HOUR)
    assert current_machine_job("job-a", token) == "job-a"


def test_token_of_one_job_cannot_touch_another():
    token = create_machine_token("job-a", ONE_HOUR)
    with pytest.raises(HTTPException) as exc:
        current_machine_job("job-b", token)
    assert exc.value.status_code == 403


def test_missing_token_is_unauthorized():
    with pytest.raises(HTTPException) as exc:
        current_machine_job("job-a", None)
    assert exc.value.status_code == 401


def test_expired_token_is_unauthorized():
    token = create_machine_token("job-a", timedelta(seconds=-1))
    with pytest.raises(HTTPException) as exc:
        decode_machine_token(token)
    assert exc.value.status_code == 401


def test_user_access_token_is_not_a_machine_token():
    """Access token của user có `type=access` và không có `job_id`."""
    token = create_access_token("user-1", "admin")
    with pytest.raises(HTTPException) as exc:
        decode_machine_token(token)
    assert exc.value.status_code == 401
