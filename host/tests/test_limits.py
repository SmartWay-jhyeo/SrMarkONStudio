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
    # $GNSS 전용 원문 꼬리 상한(규격 §4.1) — MK_ARG_MAX 와 별개다.
    "MAX_GNSS_TEXT_BYTES": "MK_GNSS_TEXT_MAX",
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


def test_baud_matches_the_firmware():
    """🔴 호스트와 보드가 같은 UART 속도를 써야 한다.

    다르면 전선에 쓰레기만 흐르고, 증상은 "아무것도 안 나온다" 라 원인을
    찾기 어렵다 — 케이블·포트·전원을 다 의심하게 된다.

    921600 인 이유와 실기기 확인 결과는 `host/core/limits.py` 와
    `docs/measurements/2026-08-14_baud_921600.md` 에 있다.
    """
    main_c = HEADER.parent.parent / "main.c"
    assert main_c.exists(), f"펌웨어 main.c 를 찾을 수 없다: {main_c}"
    text = main_c.read_text(encoding="utf-8")
    m = re.search(r"^#define\s+UART_BAUD\s+(\d+)u?\s*$", text, re.M)
    assert m, "main.c 에서 UART_BAUD 를 못 찾았다"
    assert int(m.group(1)) == limits.DEFAULT_BAUD, (
        f"펌웨어는 {m.group(1)}, 호스트는 {limits.DEFAULT_BAUD} — 갈렸다"
    )


def test_host_entry_points_use_the_shared_baud():
    """CLI·GUI·서비스가 각자 숫자를 들고 있지 않은지.

    한 곳만 고치고 다른 곳을 잊으면 도구마다 다른 속도로 연다.
    """
    import inspect

    from host.gui import app as gui_app
    from host.service import board_service
    from tools.cli import markon_cli

    for mod in (gui_app, markon_cli, board_service):
        src = inspect.getsource(mod)
        assert "115200" not in src.replace("115200 baud 에서", "").replace(
            "115200 에서", ""
        ), f"{mod.__name__} 에 115200 이 박혀 있다"


def test_serial_ports_are_opened_with_dtr_rts_down():
    """🔴 이 보드는 DTR/RTS 를 세운 채 열면 멈춘다 (CLAUDE.md §4).

    `serial.Serial(port, baud, ...)` 한 줄 생성자는 생성과 동시에 포트를
    열면서 두 선을 세운다. 실제로 두 번 당했다 — 한 번은 보드가 죽어 GDB 로
    리셋했고, 한 번은 F103 의 UART 브리지가 멈춰 보드 전원을 끊었다 넣어야
    했다. 두 번째는 GUI 가 이 경로로 포트를 연 직후였다.

    포트를 만들고 → 두 선을 내리고 → 그 다음에 연다. 순서가 전부다.
    """
    import inspect
    import re

    from host.service import board_service

    src = inspect.getsource(board_service.SerialTransport)
    body = re.sub(r"#[^\n]*", "", src)          # 주석은 뺀다

    assert not re.search(r"serial\.Serial\s*\(\s*[^)\s]", body), (
        "serial.Serial(...) 에 인자를 주면 열면서 DTR/RTS 를 세운다. "
        "빈 생성자로 만들고 dtr/rts 를 내린 뒤 open() 해야 한다."
    )
    assert "dtr = False" in body and "rts = False" in body, (
        "DTR/RTS 를 내리는 코드가 없다"
    )
    # open() 보다 먼저 내려야 한다.
    assert body.index("dtr = False") < body.index(".open()"), (
        "open() 뒤에만 내리면 이미 늦다 — 여는 순간 세워진다"
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


# 🔴 [정리, 2026-08-20] `test_catalog_items_fit_the_wire` 는 여기서 걷어냈다.
#
#    시뮬레이터의 설정표를 훑어 "키가 MAX_ARG_BYTES 를 넘지 않는가" 를 봤다.
#    시뮬레이터가 사라진 지금 그 자리를 얼린 스냅샷으로 대신하면, 펌웨어가
#    긴 키를 넣어도 계속 통과하는 **거짓 안전망**이 된다.
#
#    그리고 그 보증은 이미 두 겹으로 있다:
#      1. 펌웨어가 키·문자열 값을 타입으로 묶어 둔다 —
#         `mk_config.h`: `MK_CFG_KEY_MAX = MK_CFG_STR_MAX = MK_ARG_MAX`.
#         길이가 넘는 항목은 애초에 담기지 않는다.
#      2. 위 `_PAIRS` 가 `MAX_ARG_BYTES == MK_ARG_MAX` 를 대조한다.
#    호스트가 따로 셀 것이 남지 않는다.


# ---- 링크 속도 (규격 §4.2) ---------------------------------------------------
#
# 🔴 여기가 어긋나면 증상은 또 침묵이다. 호스트가 목록에 없는 속도를 보내면
#    보드가 거부하고(그건 낫다), 반대로 펌웨어에만 있는 속도를 호스트가 못
#    고르면 그 속도는 존재하지 않는 것이 된다. 그리고 시한이 갈리면 호스트가
#    아직 확인 중인데 보드는 이미 되돌아가 있다 — 그 어긋남은 실기기에서만,
#    그것도 링크가 끊긴 채로 드러난다.

LINKBAUD_H = (
    Path(__file__).resolve().parents[2]
    / "firmware" / "stage1" / "app" / "mk_linkbaud.h"
)


def _linkbaud_text() -> str:
    assert LINKBAUD_H.exists(), f"펌웨어 헤더를 찾을 수 없다: {LINKBAUD_H}"
    return LINKBAUD_H.read_text(encoding="utf-8")


def test_link_baud_choices_match_the_firmware():
    """🔴 고를 수 있는 속도가 두 곳에서 같아야 한다.

    펌웨어 쪽은 X 매크로 목록 하나뿐이고(`MK_LINKBAUD_CHOICE_LIST`), 설정
    카탈로그와 컴파일 시 오차 검사가 **그 목록을 펼쳐 쓴다.** 호스트는
    그것을 다시 적을 수밖에 없으므로 여기서 대조한다.
    """
    text = _linkbaud_text()
    m = re.search(r"#define\s+MK_LINKBAUD_CHOICE_LIST\(X\)((?:.*\\s*\n)*.*)",
                  text)
    assert m, "MK_LINKBAUD_CHOICE_LIST 를 못 찾았다 — 형식이 바뀌었나?"
    fw = tuple(int(v) for v in re.findall(r"X\((\d+)u?\)", m.group(1)))
    assert fw, "목록에서 값을 하나도 못 뽑았다"
    assert fw == limits.LINK_BAUD_CHOICES, (
        f"펌웨어 {fw}, 호스트 {limits.LINK_BAUD_CHOICES} — 갈렸다"
    )


def test_link_baud_confirm_deadline_matches_the_firmware():
    """🔴 시한이 갈리면 호스트가 아직 확인 중인데 보드는 이미 되돌아간다."""
    m = re.search(r"^#define\s+MK_LINKBAUD_CONFIRM_MS\s+(\d+)\s*$",
                  _linkbaud_text(), re.M)
    assert m, "MK_LINKBAUD_CONFIRM_MS 를 못 찾았다"
    assert int(m.group(1)) == limits.LINK_BAUD_CONFIRM_MS


def test_the_boot_default_is_the_catalog_default():
    """🔴 저장이 없는 보드가 카탈로그와 다른 속도로 말하면 안 된다.

    펌웨어에도 같은 `_Static_assert` 가 있지만 그것은 ARM 빌드를 돌려야만
    돈다 — 이 저장소의 기본 확인 절차는 보드도 크로스 컴파일러도 없이 도는
    것이 원칙이다(CLAUDE.md §0).
    """
    m = re.search(r"^#define\s+MK_LINKBAUD_DEFAULT\s+(\d+)u?\s*$",
                  _linkbaud_text(), re.M)
    assert m, "MK_LINKBAUD_DEFAULT 를 못 찾았다"
    assert int(m.group(1)) == limits.DEFAULT_BAUD

    main_c = HEADER.parent.parent / "main.c"
    text = main_c.read_text(encoding="utf-8")
    assert re.search(r"_Static_assert\(UART_BAUD == MK_LINKBAUD_DEFAULT", text), (
        "main.c 가 UART_BAUD 와 카탈로그 기본값이 같은지 컴파일 때 확인하지 "
        "않는다 — 둘이 갈리면 저장 없는 보드가 카탈로그와 다른 속도로 말한다"
    )


def test_the_default_is_still_the_only_speed_anyone_has_verified():
    """🔴 기본값을 올리지 않는다 (규격 §4.2.5).

    921600 만 실기기에서 확인됐다($ID 200회 왕복, 누락·손상 0). 선행
    프로젝트(Q2)에서 2 Mbps 직결에 ~2 % 유실이 실측된 채 미해결이고
    (CLAUDE.md §1.2), 이 보드는 거기에 F103(BMP) 브리지가 하나 더 낀다.

    이 시험은 "성능이 좋아 보이니 기본값을 올리자" 를 막는 자리다. 올리려면
    실기기 근거를 먼저 만들고 이 시험을 함께 고쳐야 한다.
    """
    assert limits.DEFAULT_BAUD == 921600
    assert max(limits.LINK_BAUD_CHOICES) > limits.DEFAULT_BAUD, (
        "더 빠른 선택지가 있어야 사용자가 시험해 볼 수 있다"
    )
