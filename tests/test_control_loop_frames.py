"""Preview frame routing: three cameras in, three cameras out.

`_publish_encoded_frame` runs on the encoder thread and `take_frames` on the
websocket sender, so the pair is the whole contract between them. Building a
real `ControlLoop` needs a simulator, so these tests drive the two methods
against a bare instance carrying only the state they touch.
"""

import threading

from src.core.control_loop import ControlLoop


def _frame_state() -> ControlLoop:
    loop = object.__new__(ControlLoop)
    loop._state_lock = threading.Lock()
    loop._stats_lock = threading.Lock()
    loop._frame = None
    loop._frame_secondary = None
    loop._frame_tertiary = None
    loop._frame_seq = 0
    loop._frame_secondary_seq = 0
    loop._frame_tertiary_seq = 0
    loop._dropped_frames = 0
    return loop


def test_each_camera_lands_in_its_own_slot():
    loop = _frame_state()
    loop._publish_encoded_frame("primary", 7, b"front")
    loop._publish_encoded_frame("secondary", 8, b"top")
    loop._publish_encoded_frame("tertiary", 9, b"wrist")

    assert loop.take_frames() == (7, b"front", 8, b"top", 9, b"wrist")


def test_taking_frames_clears_all_three_slots():
    loop = _frame_state()
    for camera, jpeg in (("primary", b"a"), ("secondary", b"b"), ("tertiary", b"c")):
        loop._publish_encoded_frame(camera, 1, jpeg)
    loop.take_frames()

    assert loop.take_frames() == (1, None, 1, None, 1, None)


def test_overwriting_an_unsent_frame_counts_as_dropped():
    loop = _frame_state()
    loop._publish_encoded_frame("tertiary", 1, b"old")
    loop._publish_encoded_frame("tertiary", 2, b"new")

    assert loop._dropped_frames == 1
    assert loop.take_frames()[5] == b"new"


def test_cameras_keep_independent_sequence_numbers():
    """The wrist pane runs slower than the main pane, so its seq lags."""

    loop = _frame_state()
    loop._publish_encoded_frame("primary", 30, b"front")
    loop._publish_encoded_frame("tertiary", 10, b"wrist")

    primary_seq, _, _, _, tertiary_seq, _ = loop.take_frames()
    assert (primary_seq, tertiary_seq) == (30, 10)
