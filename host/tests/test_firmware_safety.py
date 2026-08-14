"""펌웨어 소스가 안전 불변조건을 지키는지 검사한다.

🔴 이 시험들은 보드에 굽기 전에 도는 마지막 관문이다.

컴파일러도 링커도 "이 핀을 건드리면 24V 가 나온다" 는 것을 모른다. 그
사실은 회로에만 있고, 코드에는 `GPIO_PIN_8` 이라는 숫자로만 나타난다.
그래서 사람이 리뷰에서 놓치면 아무도 못 잡는다 — 굽고 나서 알게 된다.

여기서 잡는다.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

FW = Path(__file__).resolve().parents[2] / "firmware" / "stage1"

#: 🔴 전원 레일 제어 핀 (데이터시트 §4, CLAUDE.md §4).
#:
#:    PD10 = 5V   — 켠 뒤 절대 내리지 않는다. 쿨링 팬(J34)이 직결이고
#:                  상시 동작이 요구사항이다.
#:    PD9  = 14.9V
#:    PD8  = 24V  — 센서 구동. 붙어 있지도 않은 센서에 인가하지 않는다.
#:
#:    리셋 직후 3.3V 만 살아 있고 나머지는 꺼져 있다. 아무것도 안 하면
#:    그대로 꺼져 있다 — 1단계가 원하는 상태다.
RAIL_PINS = {
    "GPIO_PIN_8": "PD8 = 24V",
    "GPIO_PIN_9": "PD9 = 14.9V",
    "GPIO_PIN_10": "PD10 = 5V (쿨링 팬 직결)",
}


def _sources() -> list[Path]:
    return sorted(
        list((FW / "app").glob("*.c")) + list((FW / "app").glob("*.h"))
        + list((FW / "bsp").glob("*.c")) + list((FW / "bsp").glob("*.h"))
        + [FW / "main.c"]
    )


def _strip_comments(text: str) -> str:
    """주석을 지운다. 주석에서 레일을 **언급**하는 것은 오히려 권장이다."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def test_sources_exist():
    """소스가 옮겨가면 아래 검사가 조용히 통과하는 것을 막는다."""
    srcs = _sources()
    assert len(srcs) >= 8, f"펌웨어 소스를 못 찾았다: {FW}"
    assert (FW / "main.c").exists()


#: 🔴 GPIOD 를 만져도 되는 유일한 파일.
#:
#:    레일이 거기 있으므로 한 군데로 묶는다. 여러 파일로 흩어지면 아래
#:    검사들이 하나를 놓친다. 이전에는 `main.c` 였는데, 레일 제어를
#:    구현하면서 전용 파일로 옮겼다 — main.c 는 응용 흐름이고, 레일은
#:    하드웨어 불변조건이 걸린 곳이라 섞지 않는다.
RAIL_OWNER = "mk_rails.c"


def test_only_one_file_touches_gpiod():
    touching = [p.name for p in _sources()
                if "GPIOD" in _strip_comments(p.read_text(encoding="utf-8"))]
    assert touching == [RAIL_OWNER], (
        f"GPIOD 를 건드리는 파일: {touching} — {RAIL_OWNER} 하나여야 한다"
    )


@pytest.mark.parametrize("path", _sources(), ids=lambda p: p.name)
def test_rail_pins_only_appear_in_the_owner(path: Path):
    """레일 핀 상수가 소유 파일 밖에 나타나지 않는지.

    GPIOD 에 여러 핀을 OR 로 묶어 넣는 습관 때문에 실수하기 쉬운 자리다 —
    `GPIO_PIN_11 | GPIO_PIN_10` 한 글자가 5V 레일을 켠다.

    🔴 핀 번호만으로는 판정할 수 없다. `GPIO_PIN_10` 은 GPIOD 에서는 5V
       레일이지만 GPIOB 에서는 USART3 TX 이고, GPIOE 에서는 ADS1256 의
       RESET 이다. 포트가 무엇인지가 전부다. 그래서 **GPIOD 를 건드리는
       파일에서만** 본다.
    """
    if path.name == RAIL_OWNER:
        pytest.skip("레일을 소유한 파일 — 아래 전용 검사가 따로 본다")
    code = _strip_comments(path.read_text(encoding="utf-8"))
    if "GPIOD" not in code:
        pytest.skip("GPIOD 를 건드리지 않는다 — 레일과 무관하다")
    for pin, what in RAIL_PINS.items():
        assert not re.findall(rf"\b{pin}\b", code), (
            f"{path.name} 이 GPIOD 를 쓰면서 {pin} ({what}) 을 언급한다. "
            f"레일은 {RAIL_OWNER} 만 만진다."
        )


def test_five_volt_rail_is_never_driven_low():
    """🔴 PD10(5V)을 내리는 코드가 없는지.

    쿨링 팬(J34)이 5V 레일에 직결이고 상시 동작이 요구사항이다
    (CLAUDE.md §4). 저전력 모드·에러 처리·레일 재시작 루틴에서 실수로
    내리는 것이 문서가 경고하는 상황이고, 여기가 그것을 잡는 자리다.

    🔴 이 검사는 이전 규칙("레일을 아예 안 건드린다")보다 **촘촘하다.**
       이전에는 PD10 을 내리는 코드를 막을 방법이 아예 없었다 — 안 건드리는
       동안에는 검사할 것이 없었기 때문이다. 이제 레일을 제어하므로
       올리는 것과 내리는 것을 나눠서 본다.

    소유 파일이 PIN_5V 를 쓰는 자리는 두 곳뿐이어야 한다:
      - init 에서 전부 Low 로 두는 곳 (리셋 직후 상태 유지)
      - set 에서 `on` 일 때만 내보내는 곳
    """
    code = _strip_comments((FW / "bsp" / RAIL_OWNER).read_text(encoding="utf-8"))

    # `on ? SET : RESET` 삼항 밖에서 PIN_5V 와 RESET 이 같은 문장에 있으면
    # 조건 없이 내리는 코드다. init 의 일괄 Low 만 예외로 둔다.
    for stmt in code.split(";"):
        if "PIN_5V" not in stmt or "GPIO_PIN_RESET" not in stmt:
            continue
        # init 의 일괄 초기화: 네 핀을 모두 묶어 Low 로 두는 문장
        if "PIN_24V" in stmt and "PIN_14V9" in stmt and "PIN_LED" in stmt:
            continue
        # 삼항으로 on 일 때만 SET 하는 문장
        if "GPIO_PIN_SET" in stmt and "?" in stmt:
            continue
        raise AssertionError(
            f"{RAIL_OWNER} 에 5V 를 조건 없이 내리는 코드가 있다:\n"
            f"  {' '.join(stmt.split())[:160]}"
        )


def test_rail_controller_refuses_to_lower_five_volts():
    """순서 로직(app/mk_railctl.c)도 5V 내리기를 거부하는지.

    bsp 한 겹만으로는 부족하다 — 이 층이 상태를 들고 있고, 여기서 막지
    않으면 `on[]` 이 꺼진 것으로 바뀌어 `$STAT` 이 거짓을 보고한다.
    """
    code = _strip_comments((FW / "app" / "mk_railctl.c").read_text(encoding="utf-8"))
    assert "MK_RAIL_5V" in code and "return" in code, (
        "mk_railctl.c 에 5V 내리기를 막는 코드가 안 보인다"
    )
    # 실제 동작은 C 시험(test_railctl.c)이 확인한다. 여기서는 그 시험이
    # 존재하는지까지 본다 — 지워지면 이 불변조건이 조용히 사라진다.
    t = (FW / "tests" / "test_railctl.c").read_text(encoding="utf-8")
    assert "test_five_volts_can_never_be_turned_off" in t, (
        "5V 금지 시험이 사라졌다"
    )


def test_uart_uses_only_usart3_pins():
    """UART 는 PB10/PB11 (USART3) 만 쓴다.

    데이터시트 §7.5 는 이 경로로 통신할 수 없다고 하지만 틀렸다 —
    실기기에서 COM23 으로 나오는 것을 확인했고 넷리스트에도 경로가 있다.
    """
    code = _strip_comments((FW / "bsp" / "mk_uart.c").read_text(encoding="utf-8"))
    assert "GPIOB" in code
    assert "USART3" in code
    assert "GPIO_AF7_USART3" in code
    # 다른 포트를 열지 않는다.
    for other in ("USART1", "USART2", "UART4", "UART5"):
        assert other not in code, f"mk_uart.c 가 {other} 도 건드린다"


def test_app_layer_does_not_include_hal():
    """🔴 `app/` 은 HAL 을 include 하지 않는다.

    이 경계가 있어야 호스트에서 그대로 컴파일해 시험할 수 있고, Python
    구현과 바이트로 대조할 수 있다. 한 번 깨지면 되돌리기 어렵다 —
    HAL 타입이 인터페이스에 새어 나오기 시작한다.
    """
    for path in sorted((FW / "app").glob("*.[ch]")):
        # 🔴 주석은 빼고 본다. "이 파일은 stm32h7xx_hal.h 를 include 하지
        #    않는다" 는 주석이 검사에 걸리면, 경계를 설명하는 문장을 지워야
        #    시험이 통과하는 꼴이 된다.
        code = _strip_comments(path.read_text(encoding="utf-8"))
        for banned in ("stm32h7xx", "HAL_", "GPIO_", "USART"):
            assert banned not in code, (
                f"{path.name} 이 {banned} 를 쓴다 — app/ 은 HAL 비의존이어야 한다"
            )


def test_app_layer_does_not_use_libc_stdio():
    """`app/` 은 libc 의 printf 계열을 부르지 않는다.

    newlib-nano 에서 `%f` 를 쓰려면 `-u _printf_float` 가 필요하고 실측으로
    플래시 10,456 바이트를 더 쓴다. 더 중요한 것은 `%lld` 의 출력이 옳은지
    이 호스트에서 확인할 수 없다는 점이다
    (docs/measurements/2026-08-13_newlib_nano_printf.md).
    """
    for path in sorted((FW / "app").glob("*.c")):
        code = _strip_comments(path.read_text(encoding="utf-8"))
        assert "stdio.h" not in code, f"{path.name} 이 stdio.h 를 include 한다"
        for fn in ("printf", "sprintf", "snprintf", "sscanf"):
            assert not re.search(rf"\b{fn}\s*\(", code), (
                f"{path.name} 이 {fn}() 을 부른다"
            )


def test_makefile_does_not_pull_in_printf_float():
    """`-u _printf_float` 를 쓰지 않는다 — 10 KB 를 아낀다."""
    mk = (FW / "Makefile").read_text(encoding="utf-8")
    body = "\n".join(ln for ln in mk.splitlines() if not ln.strip().startswith("#"))
    assert "_printf_float" not in body, (
        "Makefile 이 -u _printf_float 를 쓴다. app/ 은 printf 를 부르지 않으므로 "
        "필요 없고, 실측으로 플래시 10,456 바이트가 든다."
    )


def test_makefile_builds_the_tested_sources():
    """보드에 굽는 것이 호스트에서 시험한 바로 그 파일들인지.

    🔴 보드용으로 따로 고친 판이 생기면 시험이 아무것도 보증하지 않는다.
    """
    mk = (FW / "Makefile").read_text(encoding="utf-8")
    for src in ("app/mk_framing.c", "app/mk_json.c", "app/mk_hostlink.c"):
        assert src in mk, f"Makefile 이 {src} 를 빌드하지 않는다"
