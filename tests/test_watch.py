import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from watch import step  # noqa: E402


def run(oks, need=3):
    state, msgs = (0, False), []
    for ok in oks:
        state, m = step(state, ok, need)
        msgs.append(m)
    return msgs


def test_one_alert_after_enough_failures_and_one_recovery_message():
    msgs = run([True, False, False, False, False, False, True, True])
    assert [m for m in msgs if m] == ["DOWN", "RECOVERED"] and msgs[3] == "DOWN"


def test_short_blips_do_not_alert():
    assert not any(run([False, False, True, False, False, True]))


def test_a_recovery_message_is_only_sent_after_a_down_alert():
    assert not any(run([False, True, True]))
