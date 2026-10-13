"""Shared fixtures. The model is faked everywhere except tests/test_model.py, so these run without a GPU or llama-cpp-python."""
import sys
import types

import pytest

try:
    import llama_cpp  # noqa: F401
except ImportError:  # CI and machines without llama-cpp-python
    _stub = types.ModuleType("llama_cpp")
    _stub.__stub__ = True
    _stub.Llama = object
    _stub.llama_supports_gpu_offload = lambda: False
    _stub.StoppingCriteriaList = list
    sys.modules["llama_cpp"] = _stub

from decisionlm import server, settings  # noqa: E402


@pytest.fixture(autouse=True)
def default_settings():
    """Every test starts from the default config file, ignoring the shell's environment overrides."""
    settings.use(settings.load(environ={}))
    yield
    settings.use(None)


@pytest.fixture
def client():
    return server.create_app().test_client()
