from milo_brain.conversation import ConversationLog, Exchange


def test_add_and_recent_are_ordered_oldest_to_newest():
    log = ConversationLog(maxlen=10)
    log.add("hi", "hello")
    log.add("how are you", "good")
    recent = log.recent(5)
    assert [(e.heard, e.reply) for e in recent] == [("hi", "hello"), ("how are you", "good")]
    assert all(isinstance(e, Exchange) for e in recent)


def test_recent_caps_at_n_and_buffer_is_bounded():
    log = ConversationLog(maxlen=3)
    for i in range(5):
        log.add(f"q{i}", f"a{i}")
    assert [e.heard for e in log.recent(10)] == ["q2", "q3", "q4"]  # maxlen drops oldest
    assert [e.heard for e in log.recent(2)] == ["q3", "q4"]         # recent(n) caps
