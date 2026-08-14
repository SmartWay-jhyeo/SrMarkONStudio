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
    sent = q.drain_pending()
    q.complete(sent[0].send_id, ok=True, payload={"mode": "RUN"})
    assert q.take_results()[0].payload == {"mode": "RUN"}
    assert q.take_results() == []           # 한 번만 걷힌다


def test_in_flight_drops_when_a_command_completes():
    q = CommandQueue()
    tag = q.submit("STAT")
    sent = q.drain_pending()
    assert q.in_flight == 1
    q.complete(sent[0].send_id, ok=True)
    assert q.in_flight == 0


def test_rejection_carries_the_reason_verbatim():
    """🔴 거부 사유를 '실패' 로 뭉개지 않는다.

    INTERLOCK 은 '쿨링 팬이 5V 레일 직결' 이라는 하드웨어 사실을 담고 있다.
    사용자가 왜 안 되는지 알아야 한다.
    """
    q = CommandQueue()
    tag = q.submit("CFG", "SET", "pwr.5v", "false")
    sent = q.drain_pending()
    q.complete(sent[0].send_id, ok=False, reason="INTERLOCK")
    r = q.take_results()[0]
    assert r.ok is False
    assert r.reason == "INTERLOCK"


def test_transport_error_is_distinct_from_a_rejection():
    """보드가 거부한 것과 보드에 못 닿은 것은 다른 사실이다."""
    q = CommandQueue()
    tag = q.submit("STAT")
    sent = q.drain_pending()
    q.complete(sent[0].send_id, ok=False, error="응답 없음")
    r = q.take_results()[0]
    assert r.reason is None
    assert r.error == "응답 없음"


def test_completing_an_unknown_tag_is_ignored():
    """워커가 늦게 끝난 명령을 보고해도 큐가 깨지지 않는다."""
    q = CommandQueue()
    q.complete(9999, ok=True)
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
    sent = q.drain_pending()
    q.complete(sent[0].send_id, ok=True)
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


def test_two_sends_of_the_same_tag_each_get_a_result():
    """🔴 회귀 시험 — 같은 항목을 응답 전에 두 번 보내면 결과도 둘이다.

    이전 판은 in-flight 를 **태그 집합**으로 셌다. 그래서 같은 태그의 두
    전송이 하나로 세어지고, 첫 응답 하나에 둘 다 끝난 것으로 처리됐다.
    **두 번째(최신) 결과가 사라진다.**

    사용자 시나리오로 옮기면: 250 을 넣고 응답을 기다리는 동안 500 으로
    고쳐 다시 보냈는데, 보드가 500 을 거부했다. 화면은 250 성공만 보고
    끝나고, 사용자는 500 이 적용된 줄 안다. 실제 보드 값은 250 이다.

    Codex 감사가 짚었고 재현으로 확인했다.
    """
    q = CommandQueue()
    q.submit("CFG", "SET", "tx.period_ms", "250", tag="set:tx.period_ms")
    first = q.drain_pending()
    q.submit("CFG", "SET", "tx.period_ms", "500", tag="set:tx.period_ms")
    second = q.drain_pending()

    assert q.in_flight == 2
    assert first[0].send_id != second[0].send_id

    q.complete(first[0].send_id, ok=True)
    assert q.in_flight == 1, "응답 하나에 둘 다 끝나면 안 된다"

    q.complete(second[0].send_id, ok=False, reason="RANGE")
    assert q.in_flight == 0

    results = q.take_results()
    assert len(results) == 2, "두 전송에 두 결과"
    assert [r.ok for r in results] == [True, False]
    assert results[1].reason == "RANGE"
    # 🔴 순서를 가릴 수 있어야 한다 — 큰 번호가 나중에 보낸 것이다.
    assert results[0].send_id < results[1].send_id


def test_send_id_is_assigned_at_drain_not_at_submit():
    """합쳐진 명령이 번호를 낭비하지 않는다.

    제출 때 번호를 붙이면 스핀박스를 2000번 돌렸을 때 2000개가 소모되고,
    실제로 나간 것과 번호가 어긋난다.
    """
    q = CommandQueue()
    for value in ("100", "200", "300"):
        q.submit("CFG", "SET", "tx.period_ms", value, tag="spin")
    sent = q.drain_pending()
    assert len(sent) == 1
    assert sent[0].args[-1] == "300"
    assert sent[0].send_id == 1


def test_completing_the_same_send_id_twice_is_ignored():
    """워커가 같은 결과를 두 번 보고해도 결과가 겹치지 않는다."""
    q = CommandQueue()
    q.submit("STAT")
    sent = q.drain_pending()
    q.complete(sent[0].send_id, ok=True)
    q.complete(sent[0].send_id, ok=True)
    assert len(q.take_results()) == 1


def test_merging_only_affects_commands_not_yet_sent():
    """이미 보낸 명령은 덮어쓰지 않는다 — 결과가 돌아와야 한다."""
    q = CommandQueue()
    first = q.submit("CFG", "SET", "tx.period_ms", "100", tag="spin")
    sent = q.drain_pending()                # 워커가 가져갔다
    q.submit("CFG", "SET", "tx.period_ms", "200", tag="spin")
    assert q.in_flight == 2                 # 보낸 것 + 대기 중인 것
    q.complete(sent[0].send_id, ok=True)
    assert q.take_results()[0].tag == "spin"


def test_drain_respects_a_limit_and_leaves_the_rest_queued():
    """🔴 상한을 넘긴 것은 큐에 남는다 — 꺼낸 뒤 되돌리지 않는다.

    되돌리는 사이에 같은 태그로 새 값이 들어오면 오래된 값이 새 값을
    덮어쓴다. 애초에 안 꺼내는 편이 안전하다.
    """
    q = CommandQueue()
    for i in range(7):
        q.submit("STAT", tag=f"t{i}")
    assert len(q.drain_pending(limit=3)) == 3
    assert len(q.pending_tags) == 4
    assert len(q.drain_pending(limit=3)) == 3
    assert len(q.drain_pending(limit=3)) == 1
    assert q.drain_pending(limit=3) == []


def test_drain_without_a_limit_takes_everything():
    q = CommandQueue()
    for i in range(5):
        q.submit("STAT", tag=f"t{i}")
    assert len(q.drain_pending()) == 5
