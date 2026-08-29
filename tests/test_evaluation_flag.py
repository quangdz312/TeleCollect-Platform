"""Đánh giá và huấn luyện bật/tắt độc lập nhau.

Rollout chỉ cần CPU, còn train cần GPU — nên một máy chủ có thể chấm điểm
checkpoint mà không train được nó.
"""

import pytest

from src.config import get_settings


@pytest.fixture
def settings():
    instance = get_settings()
    original = (instance.training_enabled, instance.evaluation_enabled)
    yield instance
    instance.training_enabled, instance.evaluation_enabled = original


def test_evaluation_follows_training_when_unset(settings):
    """Cấu hình đã có từ trước không đổi hành vi."""
    settings.evaluation_enabled = None

    settings.training_enabled = False
    assert settings.evaluation_is_enabled is False

    settings.training_enabled = True
    assert settings.evaluation_is_enabled is True


def test_evaluation_can_be_on_where_training_is_off(settings):
    """Chính là cấu hình của server staging."""
    settings.training_enabled = False
    settings.evaluation_enabled = True

    assert settings.evaluation_is_enabled is True


def test_evaluation_can_be_off_where_training_is_on(settings):
    settings.training_enabled = True
    settings.evaluation_enabled = False

    assert settings.evaluation_is_enabled is False
