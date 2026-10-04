"""Tests for prez.timer with a deterministic fake clock."""

import pytest

from prez.timer import TalkTimer, TimerState


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def test_initial_state(clock: FakeClock):
    timer = TalkTimer(clock=clock)
    assert timer.state is TimerState.IDLE
    assert timer.elapsed == 0.0
    assert timer.talk_seconds is None
    assert timer.remaining is None
    assert timer.fraction is None
    assert timer.overtime is False


def test_idle_does_not_count(clock: FakeClock):
    timer = TalkTimer(clock=clock)
    clock.advance(30)
    assert timer.elapsed == 0.0


def test_start_counts_elapsed(clock: FakeClock):
    timer = TalkTimer(clock=clock)
    timer.start()
    assert timer.state is TimerState.RUNNING
    clock.advance(12.5)
    assert timer.elapsed == pytest.approx(12.5)


def test_pause_freezes_and_resume_accumulates(clock: FakeClock):
    timer = TalkTimer(clock=clock)
    timer.start()
    clock.advance(10)
    timer.pause()
    assert timer.state is TimerState.PAUSED
    clock.advance(100)
    assert timer.elapsed == pytest.approx(10)
    timer.start()
    clock.advance(5)
    assert timer.state is TimerState.RUNNING
    assert timer.elapsed == pytest.approx(15)


def test_toggle(clock: FakeClock):
    timer = TalkTimer(clock=clock)
    timer.toggle()
    assert timer.state is TimerState.RUNNING
    clock.advance(3)
    timer.toggle()
    assert timer.state is TimerState.PAUSED
    clock.advance(3)
    timer.toggle()
    assert timer.state is TimerState.RUNNING
    clock.advance(3)
    assert timer.elapsed == pytest.approx(6)


def test_start_while_running_is_a_noop(clock: FakeClock):
    timer = TalkTimer(clock=clock)
    timer.start()
    clock.advance(7)
    timer.start()
    clock.advance(1)
    assert timer.elapsed == pytest.approx(8)


def test_pause_while_idle_is_a_noop(clock: FakeClock):
    timer = TalkTimer(clock=clock)
    timer.pause()
    assert timer.state is TimerState.IDLE
    assert timer.elapsed == 0.0


def test_reset(clock: FakeClock):
    timer = TalkTimer(600, clock=clock)
    timer.start()
    clock.advance(42)
    timer.reset()
    assert timer.state is TimerState.IDLE
    assert timer.elapsed == 0.0
    assert timer.talk_seconds == 600  # the talk time survives a reset
    clock.advance(10)
    assert timer.elapsed == 0.0
    timer.start()
    clock.advance(2)
    assert timer.elapsed == pytest.approx(2)


def test_remaining_fraction_and_overtime(clock: FakeClock):
    timer = TalkTimer(1200, clock=clock)
    assert timer.remaining == 1200
    assert timer.fraction == 0.0
    timer.start()
    clock.advance(600)
    assert timer.remaining == pytest.approx(600)
    assert timer.fraction == pytest.approx(0.5)
    assert timer.overtime is False
    clock.advance(600)
    assert timer.remaining == pytest.approx(0)
    assert timer.overtime is False
    clock.advance(100)
    assert timer.remaining == pytest.approx(-100)
    assert timer.fraction == pytest.approx(1300 / 1200)
    assert timer.overtime is True


def test_set_talk_time(clock: FakeClock):
    timer = TalkTimer(clock=clock)
    timer.start()
    clock.advance(30)
    timer.set_talk_time(90)
    assert timer.talk_seconds == 90
    assert timer.remaining == pytest.approx(60)
    assert timer.fraction == pytest.approx(30 / 90)
    timer.set_talk_time(None)
    assert timer.remaining is None
    assert timer.fraction is None
    timer.set_talk_time(0)
    assert timer.talk_seconds is None  # 0 means "no talk time"
    with pytest.raises(ValueError):
        timer.set_talk_time(-5)
    assert TalkTimer(0, clock=clock).talk_seconds is None


def test_default_clock_is_monotonic():
    timer = TalkTimer()
    timer.start()
    assert 0.0 <= timer.elapsed < 1.0
    timer.pause()
    assert timer.state is TimerState.PAUSED


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "00:00"),
        (0.4, "00:00"),
        (59, "00:59"),
        (61.9, "01:01"),
        (600, "10:00"),
        (3599, "59:59"),
        (3600, "1:00:00"),
        (3900, "1:05:00"),
        (36061, "10:01:01"),
        (-1, "-00:01"),
        (-90, "-01:30"),
        (-0.5, "-00:00"),
        (-3661, "-1:01:01"),
    ],
)
def test_format(seconds, expected):
    assert TalkTimer.format(seconds) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("20", 1200),
        ("20:30", 1230),
        ("1:05:00", 3900),
        ("0", 0),
        (" 5 ", 300),
        ("00:05", 5),
        ("20:", 1200),
        (":30", 30),
        ("2:00:00", 7200),
        ("20:90", 1290),  # lenient like pympress: seconds are not range-checked
    ],
)
def test_parse(text, expected):
    assert TalkTimer.parse(text) == expected


@pytest.mark.parametrize("text", ["", "   ", ":", "abc", "1:2:3:4", "-5", "20:30s", "1.5", "20m"])
def test_parse_rejects_garbage(text):
    with pytest.raises(ValueError):
        TalkTimer.parse(text)


def test_format_parse_roundtrip():
    for seconds in (0, 5, 65, 1230, 3900, 36061):
        assert TalkTimer.parse(TalkTimer.format(seconds)) == seconds
