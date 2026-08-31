"""디지털 입력 (J18~J20) 레코드 → 화면 상태. Qt 없이 돈다 (규격 §7.6).

🔴 J18~J20 은 출력이 아니라 입력이다 (사용자 확정 2026-08-18). 옵토커플러가
붙어 있고 보드가 신호를 읽는다 — `sol.j18` 같은 설정 항목이 아니라
`din` 텔레메트리로만 상태를 안다. 그래서 `build_dins` 는 `build_sensors`
와 달리 "설정에서 시드"가 아니라 **항상 세 자리를 고정으로 깐다** — 종류가
바뀌는 I2C 와 달리 J18~J20 은 존재 자체가 보드 리비전으로 고정이다.

연결 끊김 처리는 아날로그·I2C(값 지우고 이력은 남김)가 아니라 **전원
레일**(`RailState`)과 같은 결로 간다 — 값이 아니라 **상태**(켜짐/꺼짐
둘뿐)이고, 흐름 이력보다 "마지막으로 알던 것 (n초 전)" 쪽이 더 뜻이
통한다. `host/gui/last_known.StateHistory` 를 그대로 쓴다.
"""

from host.gui.last_known import StateHistory
from host.gui.screen import DIN_PORTS, build_dins


def rec(cid=18, state=1, t=1000):
    return {"schema_ver": 3, "seq": 1, "t": t, "type": "din",
            "connector_id": cid, "state": state}


def _dins(records, *, reachable=True, previous=(), history=None, now_s=0.0):
    return build_dins(records, reachable=reachable, previous=previous,
                      history=history or StateHistory(), now_s=now_s)


# ---- 시드 (I2C 의 seed_sensors() 와 같은 자리, 그러나 늘 고정 셋) -----------


def test_three_ports_always_show_up_even_with_no_records_at_all():
    out = _dins([])
    assert {d.connector for d in out} == {"J18", "J19", "J20"}
    assert len(out) == 3


def test_ports_come_in_connector_order():
    out = _dins([])
    assert [d.connector for d in out] == ["J18", "J19", "J20"]
    assert DIN_PORTS == (18, 19, 20)


def test_no_edge_yet_means_state_is_unknown():
    """🔴 아직 한 번도 안 바뀌었으면 그 사실을 말해야 한다 — 꺼짐이 아니다."""
    (d,) = [d for d in _dins([]) if d.connector == "J18"]
    assert d.state is None
    assert d.changed_at is None


# ---- 값 --------------------------------------------------------------------


def test_a_din_record_sets_the_state_and_the_changed_at_time():
    (d,) = [d for d in _dins([rec(18, 1, t=5000)]) if d.connector == "J18"]
    assert d.state is True
    assert d.changed_at == 5000


def test_state_zero_means_off():
    (d,) = [d for d in _dins([rec(19, 0, t=1000)]) if d.connector == "J19"]
    assert d.state is False


def test_a_quiet_port_keeps_its_last_state():
    """🔴 din 은 주기 송신이 아니다 — 상태가 바뀔 때만 온다. 안 온 포트를
    "모름"으로 되돌리면, 마지막으로 안 것이 있는데도 화면이 잊어버린다."""
    first = _dins([rec(18, 1, t=1000)])
    second = _dins([], previous=first)
    (d,) = [d for d in second if d.connector == "J18"]
    assert d.state is True
    assert d.changed_at == 1000


def test_a_later_edge_updates_the_changed_at_time():
    first = _dins([rec(18, 1, t=1000)])
    second = _dins([rec(18, 0, t=4000)], previous=first)
    (d,) = [d for d in second if d.connector == "J18"]
    assert d.state is False
    assert d.changed_at == 4000


def test_ports_stay_independent():
    out = _dins([rec(18, 1, t=1000)])
    j19 = [d for d in out if d.connector == "J19"][0]
    j20 = [d for d in out if d.connector == "J20"][0]
    assert j19.state is None
    assert j20.state is None


# ---- 버릴 것 ----------------------------------------------------------------


def test_records_with_an_unknown_connector_are_dropped():
    out = _dins([rec(99, 1, t=1000)])
    assert all(d.state is None for d in out)


def test_other_record_types_are_ignored():
    ain = {"type": "ain", "connector_id": 3, "ma": 12.0}
    out = _dins([ain])
    assert all(d.state is None for d in out)


def test_non_dict_records_are_ignored():
    out = _dins(["not a record"])
    assert all(d.state is None for d in out)


# ---- 연결 끊김 — 레일과 같은 결 (StateHistory) ------------------------------


def test_link_loss_clears_the_state():
    """🔴 지금은 지운다 — 마지막 값을 계속 띄우면 지금 값으로 읽힌다."""
    history = StateHistory()
    first = _dins([rec(18, 1, t=1000)], history=history, now_s=0.0)
    second = _dins([], reachable=False, previous=first,
                   history=history, now_s=1.0)
    (d,) = [d for d in second if d.connector == "J18"]
    assert d.state is None


def test_link_loss_keeps_the_last_known_text():
    """🔴 마지막으로 알던 것은 남긴다 — 전원 레일(RailState)과 같은 결이다."""
    history = StateHistory()
    first = _dins([rec(18, 1, t=1000)], history=history, now_s=0.0)
    second = _dins([], reachable=False, previous=first,
                   history=history, now_s=12.0)
    (d,) = [d for d in second if d.connector == "J18"]
    assert d.last_known != ""
    assert "12초" in d.last_known


def test_a_port_never_observed_has_no_last_known_text_on_link_loss():
    history = StateHistory()
    first = _dins([], history=history, now_s=0.0)
    second = _dins([], reachable=False, previous=first,
                   history=history, now_s=5.0)
    (d,) = [d for d in second if d.connector == "J19"]
    assert d.last_known == ""


def test_reconnecting_clears_the_last_known_text_once_state_is_known_again():
    history = StateHistory()
    first = _dins([rec(18, 1, t=1000)], history=history, now_s=0.0)
    lost = _dins([], reachable=False, previous=first, history=history, now_s=5.0)
    back = _dins([rec(18, 0, t=6000)], reachable=True, previous=lost,
                 history=history, now_s=6.0)
    (d,) = [d for d in back if d.connector == "J18"]
    assert d.state is False
    assert d.last_known == ""
