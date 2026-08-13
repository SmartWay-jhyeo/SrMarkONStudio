import pytest

from host.gui.command_queue import CommandQueue


def test_submit_returns_a_tag_and_does_not_block():
    q = CommandQueue()
    tag = q.submit("CFG", "SET", "tx.period_ms", "250")
    assert isinstance(tag, str) and tag
    assert q.in_flight == 1


def test_drain_pending_hands_commands_to_the_worker():
    q = CommandQueue()
    q.submit("CFG", "LIST")
    q.submit("STAT")
    cmds = q.drain_pending()
    assert [c.verb for c in cmds] == ["CFG", "STAT"]
    assert q.drain_pending() == []          # 두 번 꺼내지지 않는다


def test_results_are_not_visible_until_taken():
    q = CommandQueue()
    tag = q.submit("STAT")
    q.drain_pending()
    q.complete(tag, ok=True, payload={"mode": "RUN"})
    assert q.take_results()[0].payload == {"mode": "RUN"}
    assert q.take_results() == []           # 한 번만 걷힌다


def test_in_flight_drops_when_a_command_completes():
    q = CommandQueue()
    tag = q.submit("STAT")
    q.drain_pending()
    assert q.in_flight == 1
    q.complete(tag, ok=True)
    assert q.in_flight == 0


def test_rejection_carries_the_reason_verbatim():
    """🔴 거부 사유를 '실패' 로 뭉개지 않는다.

    INTERLOCK 은 '쿨링 팬이 5V 레일 직결' 이라는 하드웨어 사실을 담고 있다.
    사용자가 왜 안 되는지 알아야 한다.
    """
    q = CommandQueue()
    tag = q.submit("CFG", "SET", "pwr.5v", "false")
    q.drain_pending()
    q.complete(tag, ok=False, reason="INTERLOCK")
    r = q.take_results()[0]
    assert r.ok is False
    assert r.reason == "INTERLOCK"


def test_transport_error_is_distinct_from_a_rejection():
    """보드가 거부한 것과 보드에 못 닿은 것은 다른 사실이다."""
    q = CommandQueue()
    tag = q.submit("STAT")
    q.drain_pending()
    q.complete(tag, ok=False, error="응답 없음")
    r = q.take_results()[0]
    assert r.reason is None
    assert r.error == "응답 없음"


def test_completing_an_unknown_tag_is_ignored():
    """워커가 늦게 끝난 명령을 보고해도 큐가 깨지지 않는다."""
    q = CommandQueue()
    q.complete("nope", ok=True)
    assert q.take_results() == []
    assert q.in_flight == 0


def test_tags_are_unique_across_identical_commands():
    """같은 명령을 연달아 눌러도 결과가 섞이지 않는다."""
    q = CommandQueue()
    tags = {q.submit("STAT") for _ in range(50)}
    assert len(tags) == 50


def test_caller_supplied_tag_is_preserved():
    """화면이 위젯 식별자를 태그로 쓸 수 있어야 결과를 되돌려 붙인다."""
    q = CommandQueue()
    tag = q.submit("CFG", "SET", "tx.period_ms", "250", tag="spin:tx.period_ms")
    assert tag == "spin:tx.period_ms"
    q.drain_pending()
    q.complete(tag, ok=True)
    assert q.take_results()[0].tag == "spin:tx.period_ms"


def test_resubmitting_the_same_tag_replaces_the_older_one():
    """🔴 스핀박스를 빠르게 돌리면 같은 위젯에서 명령이 쏟아진다.

    전부 보내면 링크가 막히고, 마지막 값만 의미가 있다. 같은 태그의
    이전 명령은 버린다.
    """
    q = CommandQueue()
    q.submit("CFG", "SET", "tx.period_ms", "100", tag="spin")
    q.submit("CFG", "SET", "tx.period_ms", "200", tag="spin")
    q.submit("CFG", "SET", "tx.period_ms", "300", tag="spin")
    cmds = q.drain_pending()
    assert len(cmds) == 1
    assert cmds[0].args[-1] == "300"
    assert q.in_flight == 1


def test_untagged_commands_are_never_merged():
    """🔴 합치면 안 되는 명령이 있다.

    SAVE 를 두 번 누른 것은 두 번 저장하겠다는 뜻이고, 펄스 출력을 두 번
    누른 것은 두 번 내보내겠다는 뜻이다. 태그를 생략하면 매번 고유 태그가
    붙어 하나도 합쳐지지 않는다.
    """
    q = CommandQueue()
    for _ in range(3):
        q.submit("CFG", "SAVE")
    assert len(q.drain_pending()) == 3


def test_merging_only_affects_commands_not_yet_sent():
    """이미 보낸 명령은 덮어쓰지 않는다 — 결과가 돌아와야 한다."""
    q = CommandQueue()
    first = q.submit("CFG", "SET", "tx.period_ms", "100", tag="spin")
    q.drain_pending()                       # 워커가 가져갔다
    q.submit("CFG", "SET", "tx.period_ms", "200", tag="spin")
    assert q.in_flight == 2                 # 보낸 것 + 대기 중인 것
    q.complete(first, ok=True)
    assert q.take_results()[0].tag == "spin"
