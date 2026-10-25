from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main_rag  # noqa: E402


def test_runtime_options_override_context_and_output_without_mutating_config():
    config = {"options": {"temperature": 0.1, "num_ctx": 8192}}

    effective = main_rag.merge_runtime_options(
        config, {"num_ctx": 4096, "num_predict": 512})

    assert effective["options"] == {
        "temperature": 0.1, "num_ctx": 4096, "num_predict": 512}
    assert config["options"] == {"temperature": 0.1, "num_ctx": 8192}


def test_runtime_timeout_override_is_validated_and_preserved():
    effective = main_rag.merge_runtime_options({}, {"request_timeout_s": 360})

    assert effective["ollama_request_timeout_s"] == 360


@pytest.mark.parametrize("invalid", [True, 0, -1, "360", 1.5])
def test_runtime_timeout_override_rejects_invalid_values(invalid):
    with pytest.raises(ValueError, match="request_timeout_s"):
        main_rag.merge_runtime_options({}, {"request_timeout_s": invalid})


@pytest.mark.parametrize("invalid", [True, 0, -1, "4096"])
def test_runtime_options_reject_nonpositive_or_noninteger_limits(invalid):
    with pytest.raises(ValueError, match="num_ctx"):
        main_rag.merge_runtime_options({}, {"num_ctx": invalid})
