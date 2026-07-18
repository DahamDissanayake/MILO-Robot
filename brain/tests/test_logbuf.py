import logging

from milo_brain.logbuf import RingBufferLogHandler


def test_ring_buffer_caps_and_tails():
    h = RingBufferLogHandler(capacity=3)
    logger = logging.getLogger("rbtest-brain")
    logger.addHandler(h)
    logger.setLevel(logging.INFO)
    try:
        for i in range(5):
            logger.info("line %d", i)
    finally:
        logger.removeHandler(h)
    assert len(h.lines(10)) == 3
    assert h.lines(1)[0].endswith("line 4")
    assert h.lines(2)[0].endswith("line 3")


def test_lines_defaults_to_the_full_buffer_when_under_the_requested_count():
    h = RingBufferLogHandler(capacity=10)
    logger = logging.getLogger("rbtest-brain-2")
    logger.addHandler(h)
    logger.setLevel(logging.INFO)
    try:
        logger.info("only line")
    finally:
        logger.removeHandler(h)
    assert len(h.lines(200)) == 1
    assert h.lines(200)[0].endswith("only line")


def test_a_formatting_failure_is_swallowed_not_raised():
    h = RingBufferLogHandler()
    # A record whose args don't match its message's format spec raises
    # inside format() -- emit() must not propagate that into the logging
    # call site (one bad log call must never crash the app).
    record = logging.LogRecord(
        name="rbtest-brain-3", level=logging.INFO, pathname=__file__, lineno=1,
        msg="one slot: %s", args=("too", "many"), exc_info=None,
    )
    h.emit(record)  # must not raise
    assert h.lines() == []
