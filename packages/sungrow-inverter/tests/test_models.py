"""The model gate."""

import pytest

from sungrow_inverter import SHT_MODELS, UnsupportedModelError, model_for


def test_every_sht_code_maps_to_its_own_model() -> None:
    for code, model in SHT_MODELS.items():
        assert model.code == code
        assert model_for(code) is model
        assert model.mppt == len(model.strings)


def test_sh15t_has_three_mppt() -> None:
    model = model_for(0x0E25)
    assert model.name == "SH15T"
    assert model.mppt == 3
    assert model.strings == (2, 2, 1)


def test_known_non_sht_model_is_named_in_the_error() -> None:
    with pytest.raises(UnsupportedModelError, match=r"SH10RT \(0x0E03\)") as info:
        model_for(0x0E03)
    assert info.value.code == 0x0E03
    assert info.value.known_name == "SH10RT"


def test_unknown_code_is_rejected_by_code() -> None:
    with pytest.raises(UnsupportedModelError, match="0x1234") as info:
        model_for(0x1234)
    assert info.value.known_name is None


def test_unassigned_0x0e27_is_not_an_sht() -> None:
    with pytest.raises(UnsupportedModelError):
        model_for(0x0E27)
