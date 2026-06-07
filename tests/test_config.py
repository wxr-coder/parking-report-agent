import pytest
from pydantic import ValidationError

from app.config import Settings


def test_worker_count_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(worker_count=0)


def test_log_level_is_validated_and_normalized() -> None:
    settings = Settings(log_level="warning")
    assert settings.log_level == "WARNING"

    with pytest.raises(ValidationError):
        Settings(log_level="verbose")
