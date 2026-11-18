"""Goal ownership/targeted cancellation regressions; no ROS imports or servers."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

DEMO = Path(__file__).resolve().parents[1]
OWN = "12345678123456781234567812345678"
OTHER = "87654321876543218765432187654321"


def load(name):
    path = DEMO / (name + ".py")
    assert path.exists(), "GUI goal ownership must be recorded before transport"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Request:
    def __init__(self):
        self.goal_info = SimpleNamespace(goal_id=SimpleNamespace(uuid=[0] * 16),
                                         stamp=SimpleNamespace(sec=0, nanosec=0))


def test_cancel_request_contains_one_nonzero_owned_goal_uuid():
    module = load("navigation_cancel")
    request = module.make_cancel_request(OWN, Request)
    assert bytes(request.goal_info.goal_id.uuid).hex() == OWN
    assert any(request.goal_info.goal_id.uuid)
    assert request.goal_info.stamp.sec == request.goal_info.stamp.nanosec == 0


@pytest.mark.parametrize("value", ["", "0" * 32, "invalid", "abcd"])
def test_unknown_or_zero_uuid_cannot_create_cancel_request(value):
    module = load("navigation_cancel")
    with pytest.raises(ValueError):
        module.make_cancel_request(value, Request)


def test_cancel_waits_for_only_gui_goal_terminal_and_does_not_touch_other_client():
    module = load("navigation_cancel")
    statuses = {OWN: 2, OTHER: 2}
    requests = []
    def request(goal):
        requests.append(goal)
        return True
    def poll():
        statuses[OWN] = 5
    assert module.cancel_owned_goals([OWN], request, statuses.get, poll, timeout=1)
    assert requests == [OWN]
    assert statuses[OTHER] == 2


def test_accepted_cancel_without_target_terminal_is_not_confirmation():
    module = load("navigation_cancel")
    assert not module.cancel_owned_goals([OWN], lambda goal: True,
                                         lambda goal: None, lambda: None, timeout=0)


def test_missing_owned_goals_never_requests_cancel_all():
    module = load("navigation_cancel")
    requests = []
    assert not module.cancel_owned_goals([], requests.append,
                                         lambda goal: None, lambda: None, timeout=0)
    assert requests == []


def test_runner_records_only_its_own_motion_goal_before_transport(tmp_path):
    module = load("navigation_runner")
    ledger = module.GoalLedger(tmp_path / "goals.json", "run-id")
    sent = []
    class Client:
        def __init__(self, node, action_type, action_name):
            self.action_name = action_name
        def send_goal_async(self, goal, feedback_callback=None, goal_uuid=None):
            if self.action_name == "/navigate_to_pose":
                saved = json.loads((tmp_path / "goals.json").read_text())
                assert bytes(goal_uuid.uuid).hex() in saved["goal_ids"]
                assert saved["execution_id"] == "run-id"
            sent.append(self.action_name)
            return "future"
    wrapped = module.recording_action_client(Client, ledger,
                  lambda: SimpleNamespace(uuid=list(bytes.fromhex(OWN))))
    motion = wrapped(None, None, "/navigate_to_pose")
    query = wrapped(None, None, "/compute_path_to_pose")
    assert motion.send_goal_async(object()) == "future"
    assert query.send_goal_async(object()) == "future"
    assert json.loads((tmp_path / "goals.json").read_text())["goal_ids"] == [OWN]
    assert sent == ["/navigate_to_pose", "/compute_path_to_pose"]


def test_runner_never_sends_unrecorded_motion_goal_if_ledger_write_fails(tmp_path):
    module = load("navigation_runner")
    ledger = module.GoalLedger(tmp_path / "goals.json", "run-id")
    sent = []
    class Client:
        def __init__(self, *args):
            pass
        def send_goal_async(self, *args, **kwargs):
            sent.append(1)
    def fail(_):
        raise OSError("Ledger unavailable")
    ledger.record = fail
    wrapped = module.recording_action_client(Client, ledger,
                  lambda: SimpleNamespace(uuid=list(bytes.fromhex(OWN))))
    with pytest.raises(OSError):
        wrapped(None, None, "/navigate_to_pose").send_goal_async(object())
    assert sent == []


def test_goal_accepted_after_initial_cancel_is_cancelled_by_uuid_again():
    module = load("navigation_cancel")
    statuses = {OTHER: 2}
    requests = []
    def request(goal):
        requests.append(goal)
        if len(requests) == 2:
            statuses[OWN] = 5
    def poll():
        statuses.setdefault(OWN, 2)  # The previously in-flight GUI send is now visible.
    assert module.cancel_owned_goals([OWN], request, statuses.get, poll, timeout=1)
    assert requests == [OWN, OWN]
    assert statuses[OTHER] == 2
