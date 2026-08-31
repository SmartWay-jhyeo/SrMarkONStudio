"""보드 파라미터 PC 사본 (HANDOFF_0831 결정 3)."""

from host.core.config_schema import ConfigItem, ConfigSchema
from host.core.config_snapshot import (
    items_from_schema,
    load_snapshot,
    save_snapshot,
    wire_value,
)


def _schema() -> ConfigSchema:
    def item(key, current, *, vtype="str", readonly=False):
        return ConfigItem(key=key, group="x", vtype=vtype, default="",
                          current=current, readonly=readonly)

    return ConfigSchema(items={
        "ain0.zero": item("ain0.zero", 3.841, vtype="f32"),
        "i2c12.enabled": item("i2c12.enabled", True, vtype="bool"),
        "gnss.echo": item("gnss.echo", False, vtype="bool"),
        "ain0.name": item("ain0.name", "유량1"),
        "pwr.24v": item("pwr.24v", True, vtype="bool", readonly=True),
        "link.baud": item("link.baud", 921600, vtype="u32"),
        "dev.fw": item("dev.fw", "0.1.0", readonly=True),
    })


def test_wire_value_speaks_firmware_bool():
    """str(True)="True" 는 보드 parse_bool 이 거절한다 — true/false 로."""
    assert wire_value(True) == "true"
    assert wire_value(False) == "false"
    assert wire_value(3.841) == "3.841"
    assert wire_value("유량1") == "유량1"


def test_items_exclude_readonly_and_link_baud():
    items = items_from_schema(_schema())
    assert items["ain0.zero"] == "3.841"
    assert items["i2c12.enabled"] == "true"
    assert items["gnss.echo"] == "false"
    assert items["ain0.name"] == "유량1"
    assert "pwr.24v" not in items, "읽기 전용은 SET 이 거절한다"
    assert "dev.fw" not in items
    assert "link.baud" not in items, "§4.2 확인 절차 밖에서 보내면 링크가 끊긴다"


def test_snapshot_round_trip(tmp_path):
    path = tmp_path / "board_config.json"
    items = items_from_schema(_schema())
    save_snapshot(items, path, port="COM23")
    assert load_snapshot(path) == items


def test_missing_file_means_empty(tmp_path):
    assert load_snapshot(tmp_path / "none.json") == {}


def test_restore_prefers_snapshot_and_falls_back_to_plan(tmp_path):
    """굽기 후 복원이 사본을 우선한다 — PLAN 은 손 관리라 낡는다
    (2026-08-31 온습도 J13→J12 를 PLAN 이 몰랐다)."""
    from tools.restore_board_config import PLAN, resolve_plan

    path = tmp_path / "board_config.json"
    save_snapshot({"i2c12.kind": "2", "i2c12.addr": "92"}, path)
    plan, source = resolve_plan(path)
    assert ("i2c12.kind", "2", "") in plan
    assert "사본" in source

    plan, source = resolve_plan(tmp_path / "none.json")
    assert plan == list(PLAN)
    assert "PLAN" in source
