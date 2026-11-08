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


def test_monitor_requests_close_on_natural_completion(tmp_path, monkeypatch):
    """Exercise the real monitor loop: platforms complete -> close_env sent
    with the correct sim dir. Regression net for the sim_dir wiring."""
    from app.services import simulation_runner as runner_module

    sim_id = "sim-auto-close"
    sim_dir = tmp_path / sim_id
    (sim_dir / "twitter").mkdir(parents=True)
    (sim_dir / "reddit").mkdir(parents=True)
    (sim_dir / "twitter" / "actions.jsonl").write_text("", encoding="utf-8")
    (sim_dir / "reddit" / "actions.jsonl").write_text("", encoding="utf-8")

    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))

    state = SimpleNamespace(
        simulation_id=sim_id,
        twitter_completed=True,
        reddit_completed=True,
    )

    class _FakeProcess:
        _calls = {"poll": 0}

        def poll(self):
            self._calls["poll"] += 1
            return None if self._calls["poll"] == 1 else 0

        returncode = 0

    SimulationRunner._processes[sim_id] = _FakeProcess()

    monkeypatch.setattr(SimulationRunner, "get_run_state", lambda _sid: state)
    monkeypatch.setattr(SimulationRunner, "_save_run_state", lambda _s: None)
    monkeypatch.setattr(SimulationRunner, "_read_action_log", lambda *a, **k: 0)

    close_calls = []
    monkeypatch.setattr(
        SimulationRunner,
        "_request_env_close",
        lambda sdir, sid: close_calls.append((sdir, sid)),
    )
    monkeypatch.setattr(runner_module.time, "sleep", lambda _s: None)

    try:
        SimulationRunner._monitor_simulation(sim_id)
    except Exception:
        pass  # deep finalization beyond the close trigger is out of scope

    assert close_calls == [(str(sim_dir), sim_id)], close_calls
    SimulationRunner._processes.pop(sim_id, None)
