from tools.cli.markon_cli import build_parser, cmd_get, cmd_list, cmd_set
from host.service.board_service import BoardService, LoopbackTransport
from tools.simulator.config_store import default_store
from tools.simulator.device_sim import DeviceSim


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
    assert "[읽기전용]" in out


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


def test_cmd_set_interlock_prints_reason(capsys):
    assert cmd_set(_svc(), "pwr.5v", "false") != 0
    out = capsys.readouterr().out
    assert "INTERLOCK" in out
