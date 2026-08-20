import pytest

from tools.cli.markon_cli import (
    build_parser,
    cmd_get,
    cmd_list,
    cmd_nearest,
    cmd_query,
    cmd_set,
)
from host.storage.store import RecordStore
from host.tests.fake_board import fake_service

T0 = 1_772_000_000_000


def _svc():
    return fake_service(clock=lambda: 0)


def test_parser_requires_subcommand():
    parser = build_parser()
    args = parser.parse_args(["--port", "COM23", "get", "tx.period_ms"])
    assert args.command == "get"
    assert args.key == "tx.period_ms"


def test_parser_requires_a_port():
    """🔴 기본 포트가 없다.

    예전 기본값은 `sim`(내장 시뮬레이터)이었다. 그것을 없앤 뒤 기본값을
    아무 COM 포트로 채우면 사람이 의도하지 않은 장치에 명령을 보낸다 —
    이 도구는 24V 를 켜고 끈다. 지정하지 않으면 멈추는 편이 맞다.
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["list"])


def test_sim_port_says_it_is_gone_instead_of_failing_strangely():
    """🔴 `sim` 은 손가락·스크립트·부팅 서비스 유닛에 남아 있을 이름이다.

    그대로 시리얼 포트로 넘기면 "COM 포트를 못 연다" 는 엉뚱한 오류가 되고,
    수집기는 그 오류로 재접속을 무한히 되풀이한다. 없어졌다고 말한다.
    """
    from tools.cli.markon_cli import make_service

    with pytest.raises(SystemExit) as exc:
        make_service("sim", 921600)
    assert "없어졌다" in str(exc.value)


def test_cmd_list_prints_grouped_items(capsys):
    assert cmd_list(_svc()) == 0
    out = capsys.readouterr().out
    assert "tx.period_ms" in out
    assert "pwr.5v" in out


def test_cmd_get_prints_value(capsys):
    assert cmd_get(_svc(), "tx.period_ms") == 0
    assert "100" in capsys.readouterr().out


def test_cmd_get_unknown_key_returns_nonzero(capsys):
    assert cmd_get(_svc(), "nope") != 0
    assert "UNKNOWN_KEY" in capsys.readouterr().out


def test_cmd_set_sends_heartbeat_first(capsys):
    """설정 변경은 CONFIG 모드가 필요하므로 CLI 가 먼저 \\$HB 를 보내야 한다."""
    svc = _svc()
    assert cmd_set(svc, "tx.period_ms", "250") == 0
    assert svc.transport.sim.store.get("tx.period_ms") == 250


def test_cmd_set_rejection_prints_the_reason(capsys):
    """거부 사유가 사용자에게 그대로 보여야 한다.

    🔴 예전에는 pwr.5v 를 인터록 예시로 썼는데, 5V 를 끌 수 있게 하면서
       제품 표에 인터록 항목이 하나도 남지 않았다. 사유가 보이는지가
       요점이므로 범위 밖 값으로 확인한다.
    """
    assert cmd_set(_svc(), "tx.period_ms", "999999") != 0
    out = capsys.readouterr().out
    assert "RANGE" in out


# ------------------------------------------------------------------- query·nearest
def _ain(seq, t, connector_id=3, value=3.98):
    return {
        "schema_ver": 3, "seq": seq, "t": t, "type": "ain",
        "connector_id": connector_id, "value": value, "status": 0,
    }


def _store_with(tmp_path, records):
    store = RecordStore(tmp_path, commit_batch=1)
    for rec in records:
        store.write(rec, "{}")
    store.close()
    return tmp_path


def test_query_parser_requires_range(tmp_path):
    args = build_parser().parse_args([
        "--port", "COM23",
        "query", "--data-dir", str(tmp_path), "--start", "0", "--end", "100",
    ])
    assert args.command == "query"
    assert args.start == 0
    assert args.end == 100


def test_cmd_query_prints_matching_records(tmp_path, capsys):
    base = _store_with(tmp_path, [_ain(0, T0), _ain(1, T0 + 100)])
    assert cmd_query(base, T0, T0 + 100, type=None, connector_id=None, quantity=None) == 0
    out = capsys.readouterr().out
    assert '"seq": 0' in out or "'seq': 0" in out or "seq" in out


def test_cmd_query_reports_when_nothing_found(tmp_path, capsys):
    tmp_path.mkdir(exist_ok=True)
    assert cmd_query(tmp_path, 0, 1, type=None, connector_id=None, quantity=None) == 0
    out = capsys.readouterr().out
    assert "0" in out  # 0건


def test_cmd_nearest_prints_the_closest_record(tmp_path, capsys):
    base = _store_with(tmp_path, [_ain(0, T0), _ain(1, T0 + 1000)])
    rc = cmd_nearest(
        base, T0 + 100, type=None, connector_id=None, quantity=None,
        max_age_ms=2000,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "seq" in out


def test_cmd_nearest_reports_stale_when_too_old(tmp_path, capsys):
    base = _store_with(tmp_path, [_ain(0, T0)])
    rc = cmd_nearest(
        base, T0 + 5000, type=None, connector_id=None, quantity=None,
        max_age_ms=1000,
    )
    out = capsys.readouterr().out
    assert "stale" in out.lower() or "묵" in out
    assert rc != 0


# ---- 링크 속도 (규격 §4.2) ---------------------------------------------------


def test_baud_is_its_own_subcommand_not_a_set():
    """🔴 `set link.baud` 로는 안 된다.

    값을 넣는 것으로 끝나지 않고 포트를 새 속도로 다시 열어 확인까지
    보내야 하며, 실패하면 옛 속도로 돌아와야 한다. 전용 하위 명령으로
    갈라 두면 `set` 이 실수로 링크를 끊는 일이 없다.
    """
    args = build_parser().parse_args(["--port", "COM23", "baud", "1500000"])
    assert args.command == "baud" and args.value == 1500000


def test_the_parser_refuses_a_speed_the_board_cannot_make():
    """규격 §4.2.6 — 목록에 없는 값은 보내기 전에 막는다."""
    import pytest

    with pytest.raises(SystemExit):
        build_parser().parse_args(["baud", "250000"])


def test_cmd_baud_changes_the_speed_and_says_it_is_not_saved(capsys):
    """🔴 확정과 저장은 다른 일이다 (규격 §4.2.2 규칙 3)."""
    from tools.cli.markon_cli import cmd_baud

    svc = _svc()
    assert cmd_baud(svc, 1500000) == 0
    out = capsys.readouterr().out
    assert "1500000" in out
    assert "Flash" in out and "저장" in out
    assert svc.transport.baud == 1500000


def test_cmd_baud_reports_the_failure_and_whether_the_board_came_back(capsys):
    from tools.cli.markon_cli import cmd_baud

    svc = _svc()
    # RUN 모드에서 보내면 보드가 ERR,MODE 로 거부한다 — 아무것도 안 바뀐다.
    svc.heartbeat = lambda: None          # 하트비트를 죽여 RUN 에 머물게 한다
    assert cmd_baud(svc, 1500000) == 1
    err = capsys.readouterr().err
    assert "MODE" in err
    assert "살아 있다" in err
    assert svc.transport.baud == 921600
