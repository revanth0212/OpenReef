"""Tests for natural-completion env close (OpenReef)."""

from types import SimpleNamespace

import pytest

from app.services import simulation_runner
from app.services.simulation_runner import SimulationRunner


class _FakeIPCClient:
    instances = []

    def __init__(self, simulation_dir):
        self.simulation_dir = simulation_dir
        self.calls = []
        _FakeIPCClient.instances.append(self)

    def send_close_env(self, timeout=30):
        self.calls.append(timeout)
        return SimpleNamespace(success=True)


@pytest.fixture(autouse=True)
def clean_instances(monkeypatch):
    _FakeIPCClient.instances = []
    monkeypatch.setattr(
        "app.services.simulation_ipc.SimulationIPCClient", _FakeIPCClient
    )
    yield


def test_request_env_close_uses_ipc_and_swallows_failures(monkeypatch):
    SimulationRunner._request_env_close("/tmp/sim-dir", "sim-1")
    assert len(_FakeIPCClient.instances) == 1
    assert _FakeIPCClient.instances[0].simulation_dir == "/tmp/sim-dir"
    assert _FakeIPCClient.instances[0].calls == [30]

    class _BoomIPC:
        def __init__(self, simulation_dir):
            pass

        def send_close_env(self, timeout=30):
            raise ConnectionError("ipc down")

    monkeypatch.setattr("app.services.simulation_ipc.SimulationIPCClient", _BoomIPC)
    SimulationRunner._request_env_close("/tmp/sim-dir", "sim-1")  # must not raise


def test_check_all_platforms_completed_needs_an_enabled_platform(tmp_path, monkeypatch):
    state = SimpleNamespace(
        simulation_id="sim-x",
        twitter_completed=False,
        reddit_completed=False,
    )
    sim_dir = tmp_path / "sim-x"
    sim_dir.mkdir()
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))

    # no platform logs yet -> nothing enabled -> not complete
    assert SimulationRunner._check_all_platforms_completed(state) is False

    (sim_dir / "twitter").mkdir()
    (sim_dir / "twitter" / "actions.jsonl").write_text("", encoding="utf-8")
    assert SimulationRunner._check_all_platforms_completed(state) is False

    state.twitter_completed = True
    assert SimulationRunner._check_all_platforms_completed(state) is True
