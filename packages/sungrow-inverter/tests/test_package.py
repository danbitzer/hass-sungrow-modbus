"""Smoke test: the package imports and carries a version."""

import sungrow_inverter


def test_version_is_a_string() -> None:
    assert isinstance(sungrow_inverter.__version__, str)
    assert sungrow_inverter.__version__
