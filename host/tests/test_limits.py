"""전선 길이 상한이 세 곳에서 같은 값인지 검사한다.

같은 상한이 세 군데에 산다.

  1. `host/core/limits.py`        — 호스트가 보내기 전에 확인하는 값
  2. `firmware/stage1/app/mk_framing.h` — 보드가 실제로 가진 버퍼 크기
  3. 설정 카탈로그의 항목별 `maximum` — 사용자가 넣을 수 있는 값의 상한

🔴 이 셋이 어긋나면 증상이 **침묵**이다. 보드는 담을 수 없는 줄을 잘라 담지
   않고 조용히 버리므로(§3.1), 호스트는 오류도 거부도 아닌 무응답만 본다.
   원인이 프로토콜 어디에도 드러나지 않는다. 그래서 검사로 묶어 둔다.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from host.core import limits
from host.core.errors import MalformedLineError
from host.core.framing import build_command

HEADER = (
    Path(__file__).resolve().parents[2]
    / "firmware" / "stage1" / "app" / "mk_framing.h"
)

# limits.py 의 이름 -> mk_framing.h 의 매크로 이름
PAIRS = {
    "MAX_PAYLOAD_BYTES": "MK_LINE_MAX",
    "MAX_VERB_BYTES": "MK_VERB_MAX",
    "MAX_ARG_BYTES": "MK_ARG_MAX",
    "MAX_ARGS": "MK_ARGS_MAX",
}


def _header_defines() -> dict[str, int]:
    text = HEADER.read_text(encoding="utf-8")
    found = dict(re.findall(r"^#define\s+(MK_\w+)\s+(\d+)\s*$", text, re.M))
    return {k: int(v) for k, v in found.items()}


def test_header_exists():
    """헤더가 사라지거나 옮겨가면 아래 대조가 조용히 통과하는 것을 막는다."""
    assert HEADER.exists(), f"펌웨어 헤더를 찾을 수 없다: {HEADER}"


@pytest.mark.parametrize("py_name,c_name", PAIRS.items())
def test_python_limit_matches_firmware_header(py_name: str, c_name: str):
    defines = _header_defines()
    assert c_name in defines, f"{HEADER.name} 에 {c_name} 이(가) 없다"
    assert getattr(limits, py_name) == defines[c_name], (
        f"{py_name}={getattr(limits, py_name)} 인데 "
        f"{c_name}={defines[c_name]} 이다. 두 구현이 갈렸다."
    )


def test_build_command_rejects_long_verb():
    long_verb = "V" * (limits.MAX_VERB_BYTES + 1)
    with pytest.raises(MalformedLineError):
        build_command(long_verb)


def test_build_command_rejects_long_arg():
    long_arg = "x" * (limits.MAX_ARG_BYTES + 1)
    with pytest.raises(MalformedLineError):
        build_command("CFG", "SET", "dev.id", long_arg)


def test_build_command_rejects_too_many_args():
    with pytest.raises(MalformedLineError):
        build_command("CFG", *["a"] * (limits.MAX_ARGS + 1))


def test_build_command_accepts_exact_limits():
    """경계 바로 안쪽은 통과해야 한다. 넘치는 쪽만 막는 것을 확인한다."""
    verb = "V" * limits.MAX_VERB_BYTES
    arg = "x" * limits.MAX_ARG_BYTES
    line = build_command(verb, arg)
    assert line.startswith(f"${verb},{arg}*")


def test_real_commands_fit():
    """실제로 쓰는 가장 긴 명령이 상한 안에 드는지."""
    build_command("CFG", "SET", "pwr.seq_delay_ms", "1000")
    build_command("CFG", "SET", "ain0.unit", "degC")
    build_command("CFG", "GET", "tx.period_ms")


def test_catalog_items_fit_the_wire():
    """카탈로그의 어떤 항목도 보드가 못 받는 값을 허용하지 않는다.

    키 이름은 인자 하나로 실려 가고(`$CFG,SET,<key>,<value>`), 문자열 항목의
    `maximum` 은 값이 인자 하나에 들어갈 수 있는지를 정한다. 둘 중 하나라도
    상한을 넘으면 GUI 가 만들어낸 명령을 보드가 조용히 버린다.
    """
    from tools.simulator.config_store import default_store

    store = default_store()
    over_key = [
        i.key for i in store.items.values()
        if len(i.key.encode("utf-8")) > limits.MAX_ARG_BYTES
    ]
    assert not over_key, f"키가 {limits.MAX_ARG_BYTES} 바이트를 넘는다: {over_key}"

    over_value = [
        (i.key, i.maximum)
        for i in store.items.values()
        if i.vtype == "str"
        and i.maximum is not None
        and int(i.maximum) > limits.MAX_ARG_BYTES
    ]
    assert not over_value, (
        f"문자열 항목의 maximum 이 {limits.MAX_ARG_BYTES} 를 넘는다: {over_value}"
    )
