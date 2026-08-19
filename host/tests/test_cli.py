from tools.cli.markon_cli import (
    build_parser,
    cmd_get,
    cmd_list,
    cmd_nearest,
    cmd_query,
    cmd_set,
)
from host.service.board_service import BoardService, LoopbackTransport
from host.storage.store import RecordStore
from tools.simulator.config_store import default_store
from tools.simulator.device_sim import DeviceSim

T0 = 1_772_000_000_000


def _svc():
    clock = lambda: 0  # noqa: E731
    return BoardService(LoopbackTransport(DeviceSim(default_store())), clock=clock)


def test_parser_requires_subcommand():
    parser = build_parser()
    args = parser.parse_args(["--port", "sim", "get", "tx.period_ms"])
    assert args.command == "get"
    assert args.key == "tx.period_ms"


def test_parser_defaults_to_simulator_port():
    args = build_parser().parse_args(["list"])
    assert args.port == "sim"


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
