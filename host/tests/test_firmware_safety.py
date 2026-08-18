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


def test_five_volt_rail_warns_about_what_it_stops():
    """🔴 5V 를 끌 수 있게 한 대신, 무엇이 함께 멈추는지 알려 주는지.

    사용자 확정(2026-08-14)으로 5V 는 이제 설정으로 끌 수 있다. 그런데
    이 레일에는 세 가지가 걸려 있다:

        쿨링 팬 (J34)       — 직결. 회전수를 읽을 수도 없다.
        ADS1256 AVDD·VREFP  — 아날로그 수집이 전부 선다. 디지털만 3.3V 라
                              SPI 는 여전히 응답하므로, "레지스터는 읽히는데
                              변환이 안 되는" 헷갈리는 상태가 된다.
        WS2812 (J21~J24)    — 전원이 V5 다.

    막지 않기로 한 이상 남은 안전장치는 **사용자가 알고 끄는 것** 하나다.
    화면은 `note` 를 그대로 띄우므로(규격 §7.3), note 가 사라지면 그
    안전장치도 사라진다. 여기가 그것을 지키는 자리다.

    🔴 문구를 통째로 못박지는 않는다 — 표현은 다듬을 수 있어야 한다.
       대신 세 가지가 다 언급되는지 본다.
    """
    # 🔴 `pwr.5v` 는 문서 주석에도 나온다. **항목 정의** 근처를 봐야 한다 —
    #    처음에 첫 등장을 찾았더니 파일 머리의 설명글에 걸려서, 사유가
    #    멀쩡한데도 없다고 했다.
    for path, anchor in ((FW / "app" / "mk_cfgtable.c", '.key = "pwr.5v"'),
                         (FW.parents[1] / "tools" / "simulator"
                          / "config_store.py", '"pwr.5v", "pwr", "bool"')):
        text = path.read_text(encoding="utf-8")
        at = text.find(anchor)
        assert at >= 0, f"{path.name} 에서 pwr.5v 항목 정의를 못 찾았다"
        near = text[at:at + 600]
        for word in ("팬", "수집", "WS2812"):
            assert word in near, (
                f"{path.name} 의 pwr.5v 사유에 '{word}' 가 없다. 5V 를 끄면 "
                f"무엇이 멈추는지 사용자가 알아야 한다 — 그것이 인터록을 "
                f"푼 대신 남은 유일한 안전장치다."
            )


def test_the_rail_sequence_test_still_exists():
    """레일 순서·동시차단 시험이 지워지지 않았는지.

    실제 동작은 C 시험이 확인한다. 여기서는 그 시험들이 **존재하는지**를
    본다 — 지워지면 불변조건이 조용히 사라지고, 그것은 코드를 고치는 것보다
    알아채기 어렵다.
    """
    t = (FW / "tests" / "test_railctl.c").read_text(encoding="utf-8")
    for name in ("test_sequence_order_and_spacing",
                 "test_turning_off_five_volts_drops_the_others_too",
                 "test_five_volts_comes_back_and_resequences"):
        assert name in t, f"{name} 이 사라졌다"


#: 🔴 디지털 출력 J18~J20 의 핀 (데이터시트 §5.7, 넷리스트 확인).
#:
#:    PA4 = J18 · PA5 = J19 · PA6 = J20
#:
#:    레일과 같은 이유로 한 파일에 묶는다. 여기는 위험의 종류가 다르다 —
#:    MCU GPIO 가 커넥터에 직결이라 버퍼도 클램프도 없고, PA4·PA5 는
#:    3.3V 전용(절대최대 4.0V)이다. 어느 파일이 이 핀을 만지는지 흩어지면
#:    "누가 언제 High 로 냈나" 를 코드로 답할 수 없게 된다.
SOL_OWNER = "mk_sol.c"
SOL_PINS = {
    "GPIO_PIN_4": "PA4 = J18 (3.3V 전용)",
    "GPIO_PIN_5": "PA5 = J19 (3.3V 전용)",
    "GPIO_PIN_6": "PA6 = J20",
}


def test_only_one_file_drives_the_digital_outputs():
    """sol 핀을 GPIOA 에서 만지는 파일은 하나여야 한다.

    🔴 GPIOA 자체는 여러 파일이 쓴다 — PA7 이 WS2812 다(mk_ws2812_io.c).
       그래서 포트가 아니라 **핀 번호까지** 봐야 가려진다.
    """
    touching = []
    for path in _sources():
        code = _strip_comments(path.read_text(encoding="utf-8"))
        if "GPIOA" not in code:
            continue
        if any(re.search(rf"\b{pin}\b", code) for pin in SOL_PINS):
            touching.append(path.name)
    assert touching == [SOL_OWNER], (
        f"GPIOA 에서 J18~J20 핀을 건드리는 파일: {touching} — "
        f"{SOL_OWNER} 하나여야 한다"
    )


def test_the_digital_output_test_still_exists():
    """설정이 핀까지 닿는지 보는 C 시험이 지워지지 않았는지.

    🔴 이 프로젝트가 같은 빠짐을 네 번 밟았다 — ain*.enabled·adc.pga·
       led.*·sol.* 이 차례로 "카탈로그에는 있는데 하드웨어에 안 닿는"
       상태였다. 전부 GUI 에는 멀쩡히 뜨므로 화면으로는 못 잡는다.
       그 시험이 사라지면 같은 자리로 조용히 되돌아간다.
    """
    t = (FW / "tests" / "test_sol.c").read_text(encoding="utf-8")
    for name in ("test_each_key_drives_its_own_connector",
                 "test_missing_items_are_treated_as_off",
                 "test_the_catalog_still_has_the_three_keys"):
        assert name in t, f"{name} 이 사라졌다"


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


#: 🔴 I2C 핀 (데이터시트 §5.4, 넷리스트 확인 2026-08-18).
#:
#:    I2C3 PA8/PC9 · I2C5 PC11/PC10 · I2C1 PB8/PB9
#:
#:    PA8 이 GPIOA 라는 것이 이 검사의 이유다. 같은 포트에 sol(PA4·PA5·PA6)
#:    과 WS2812(PA7)가 있어, 한 파일이 GPIOA 를 통째로 초기화하면 서로를
#:    덮는다.
I2C_OWNER = "mk_i2c_io.c"
SOL_AND_LED_PINS = ("GPIO_PIN_4", "GPIO_PIN_5", "GPIO_PIN_6", "GPIO_PIN_7")


def test_i2c_owner_does_not_touch_the_sol_or_led_pins():
    """I2C 파일이 같은 포트의 다른 핀을 건드리지 않는지.

    🔴 GPIOA 를 여는 파일이 셋이 됐다(sol · WS2812 · I2C). 핀별 초기화라
       서로 덮지 않지만, OR 로 묶어 넣는 실수 한 번이면 밸브가 열리거나
       LED 체인이 죽는다.
    """
    path = FW / "bsp" / I2C_OWNER
    assert path.exists(), f"{I2C_OWNER} 이 없다"
    code = _strip_comments(path.read_text(encoding="utf-8"))
    for pin in SOL_AND_LED_PINS:
        assert not re.findall(rf"\b{pin}\b", code), (
            f"{I2C_OWNER} 이 {pin} 을 언급한다 — sol·WS2812 의 핀이다"
        )


def test_makefile_builds_the_tested_sources():
    """보드에 굽는 것이 호스트에서 시험한 바로 그 파일들인지.

    🔴 보드용으로 따로 고친 판이 생기면 시험이 아무것도 보증하지 않는다.
    """
    mk = (FW / "Makefile").read_text(encoding="utf-8")
    for src in ("app/mk_framing.c", "app/mk_json.c", "app/mk_hostlink.c"):
        assert src in mk, f"Makefile 이 {src} 를 빌드하지 않는다"
