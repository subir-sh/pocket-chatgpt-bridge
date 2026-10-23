import threading
import pytest
from fastapi.testclient import TestClient
from remote_bridge.app import create_app
from remote_bridge.engine import DemoBridge
from remote_bridge.power import SleepGuard, ES_CONTINUOUS, ES_SYSTEM_REQUIRED


def test_sleep_guard_keeps_only_system_awake_and_releases_on_same_thread():
    calls = []
    def fake_setter(flags):
        calls.append((flags, threading.get_ident()))
        return ES_CONTINUOUS
    guard = SleepGuard(setter=fake_setter)
    guard.start()
    assert guard.active
    assert calls == [(ES_CONTINUOUS | ES_SYSTEM_REQUIRED, calls[0][1])]
    guard.stop()
    assert not guard.active
    assert [x[0] for x in calls] == [ES_CONTINUOUS | ES_SYSTEM_REQUIRED, ES_CONTINUOUS]
    assert calls[0][1] == calls[1][1], 'Power request must be cleared on the same thread'


def test_sleep_guard_reports_setup_failure():
    guard = SleepGuard(setter=lambda flags: (_ for _ in ()).throw(OSError('fake denial')))
    with pytest.raises(RuntimeError, match='Could not prevent Windows idle sleep'):
        guard.start()
    assert not guard.active


def test_fastapi_lifespan_starts_and_stops_guard():
    instances = []
    def guard_factory():
        def setter(flags):
            events.append(flags)
            return ES_CONTINUOUS
        guard=SleepGuard(setter=setter)
        instances.append(guard)
        return guard
    events = []
    app = create_app(DemoBridge(), token='a'*48, prevent_sleep=True, sleep_guard_factory=guard_factory)
    with TestClient(app) as client:
        response = client.get('/api/status', headers={'X-Remote-Token':'a'*48})
        assert response.status_code == 200
        assert response.json()['sleep_prevention_active'] is True
        assert instances[0].active
        assert events == [ES_CONTINUOUS | ES_SYSTEM_REQUIRED]
    assert not instances[0].active
    assert events == [ES_CONTINUOUS | ES_SYSTEM_REQUIRED, ES_CONTINUOUS]
