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

#: 🔴 GPIOD 를 열어도 되는 파일. **레일 핀을 소유하는 파일은 여전히
#:    mk_rails.c 하나**이고(아래 test_rail_pins_only_appear_in_the_owner),
#:    이 목록은 "같은 포트에 다른 것도 있다" 는 사실을 적을 뿐이다.
#:
#:    2026-08-19 에 mk_lcd_io.c 가 들어왔다 — LCD(J25)의 RESX·D/CX·터치 CS
#:    가 PD13·PD15·PD14 다. 포트가 겹치는 것은 회로가 그런 것이고 옮길 수
#:    없다. 대신 **핀 번호까지 보는 검사**가 그때부터 실제로 일을 한다:
#:    예전에는 GPIOD 를 여는 파일이 소유자 하나뿐이라 아래 검사가 늘
#:    skip 됐다(sol 이 GPIOA 에서 겪은 것과 같은 구도).
GPIOD_OWNERS = {"mk_rails.c", "mk_lcd_io.c"}


def test_only_the_listed_files_touch_gpiod():
    touching = {p.name for p in _sources()
                if "GPIOD" in _strip_comments(p.read_text(encoding="utf-8"))}
    assert touching == GPIOD_OWNERS, (
        f"GPIOD 를 건드리는 파일: {sorted(touching)} — "
        f"{sorted(GPIOD_OWNERS)} 여야 한다"
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


#: 🔴 디지털 입력 J18~J20 의 핀 (데이터시트 §5.7, 넷리스트 확인).
#:
#:    PA4 = J18 · PA5 = J19 · PA6 = J20
#:
#:    사용자 확정(2026-08-18) — 출력이 아니라 입력이다. 커넥터 반대편에
#:    옵토커플러가 붙고 보드는 신호를 읽는다. 방향이 바뀌어도 "어느
#:    파일이 이 핀을 만지는가" 를 하나로 묶어야 하는 이유는 그대로다 —
#:    MCU GPIO 가 커넥터에 직결이라 버퍼도 클램프도 없고, PA4·PA5 는
#:    3.3V 전용(절대최대 4.0V)이다. 흩어지면 "누가 언제 이 핀을 초기화
#:    했나" 를 코드로 답할 수 없게 된다.
SOL_OWNER = "mk_sol.c"
SOL_PINS = {
    "GPIO_PIN_4": "PA4 = J18 (3.3V 전용)",
    "GPIO_PIN_5": "PA5 = J19 (3.3V 전용)",
    "GPIO_PIN_6": "PA6 = J20",
}


def test_only_one_file_drives_the_digital_inputs():
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


def test_sol_owner_configures_the_pins_as_interrupt_inputs():
    """mk_sol.c 가 실제로 입력(EXTI)으로 여는지 — 출력으로 되돌아가지 않았는지.

    🔴 [검토 지적 I5] 위 test_only_one_file_drives_the_digital_inputs 는
       "PA4·5·6 을 **누가** 언급하는가" 만 본다. mk_sol.c 안에서
       `GPIO_MODE_IT_RISING_FALLING` 이 `GPIO_MODE_OUTPUT_PP` 로 바뀌거나
       `HAL_GPIO_WritePin(GPIOA, PIN_J18, ...)` 이 한 줄 생겨도, 그 파일은
       여전히 이 핀들을 만지는 **유일한** 파일이므로 그 시험은 빨개지지
       않는다 — "누가" 만 보고 "**어떻게**" 는 안 보기 때문이다.

       오늘(2026-08-18) 바로잡은 실수가 정확히 이 방향이었다 — 방향이
       출력에서 입력으로 뒤집혔었다. 이 핀들은 커넥터에 직결이라 버퍼도
       직렬저항도 클램프도 없다(mk_sol.h 상단 주석) — 출력으로 잡으면
       반대편 포토트랜지스터와 맞서 소리 없이 핀을 손상시킬 수 있다.
    """
    code = _strip_comments((FW / "bsp" / SOL_OWNER).read_text(encoding="utf-8"))
    assert "GPIO_MODE_IT_" in code, (
        f"{SOL_OWNER} 가 GPIO_MODE_IT_* 를 쓰지 않는다 — 더는 입력(EXTI)이 "
        f"아니게 됐을 수 있다"
    )
    for banned in ("GPIO_MODE_OUTPUT", "GPIO_MODE_AF", "HAL_GPIO_WritePin"):
        assert banned not in code, (
            f"{SOL_OWNER} 가 {banned} 를 쓴다 — J18~J20 은 입력이다(사용자 "
            f"확정 2026-08-18). 출력으로 잡으면 반대편 포토트랜지스터와 맞선다."
        )


def test_sol_owner_does_not_touch_the_led_pin():
    """역방향 하나 더 — mk_sol.c 가 WS2812(PA7=GPIO_PIN_7)를 언급하지 않는지.

    🔴 [검토 지적 I5] test_i2c_owner_does_not_touch_the_sol_or_led_pins 는
       mk_i2c_io.c 가 sol·LED 핀을 안 건드리는지만 본다 — GPIOA 를 여는
       파일이 이제 셋(sol·WS2812·I2C)인데 이 방향의 검사는 I2C 파일에만
       있었다.
    """
    code = _strip_comments((FW / "bsp" / SOL_OWNER).read_text(encoding="utf-8"))
    assert not re.findall(r"\bGPIO_PIN_7\b", code), (
        f"{SOL_OWNER} 가 GPIO_PIN_7(PA7 = WS2812)을 언급한다 — LED 체인의 핀이다"
    )


def test_the_digital_input_test_still_exists():
    """엣지가 디바운스를 거쳐 확정 상태·레코드로 옮겨지는지 보는 C 시험이
    지워지지 않았는지.

    🔴 이 프로젝트가 같은 빠짐을 네 번 밟았다 — ain*.enabled·adc.pga·
       led.*·sol.* 이 차례로 "카탈로그에는 있는데 하드웨어에 안 닿는"
       상태였다. 전부 GUI 에는 멀쩡히 뜨므로 화면으로는 못 잡는다.
       그 시험이 사라지면 같은 자리로 조용히 되돌아간다.

       sol.* 은 출력에서 입력으로 뒤집히면서(사용자 확정 2026-08-18)
       옛 시험 이름(test_each_key_drives_its_own_connector 등, "설정이
       핀을 명령하는가")이 더는 뜻이 안 맞는다 — 이제는 "핀 엣지가
       디바운스·극성 반전을 거쳐 확정 상태·레코드가 되는가" 다.
    """
    t = (FW / "tests" / "test_sol.c").read_text(encoding="utf-8")
    for name in ("test_edge_confirms_after_debounce_elapses",
                 "test_glitches_within_debounce_window_are_ignored",
                 "test_polarity_is_flipped_exactly_once",
                 "test_repeated_same_level_emits_only_once",
                 "test_queue_overflow_is_counted",
                 "test_channels_are_independent",
                 "test_the_catalog_has_the_debounce_item"):
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
       서로 덮지 않지만, OR 로 묶어 넣는 실수 한 번이면 디지털 입력이
       못 읽히거나 LED 체인이 죽는다.
    """
    path = FW / "bsp" / I2C_OWNER
    assert path.exists(), f"{I2C_OWNER} 이 없다"
    code = _strip_comments(path.read_text(encoding="utf-8"))
    for pin in SOL_AND_LED_PINS:
        assert not re.findall(rf"\b{pin}\b", code), (
            f"{I2C_OWNER} 이 {pin} 을 언급한다 — sol·WS2812 의 핀이다"
        )


#: 🔴 [검토 지적 I6] 역방향 검사. 위 test_i2c_owner_does_not_touch_the_sol_
#:    or_led_pins 는 mk_i2c_io.c 가 **남의** 핀을 안 건드리는지만 본다 —
#:    반대로 **다른 파일이 I2C 핀을 건드리는지**는 아무도 안 봤다. 레일이
#:    GPIOD 에서 받는 검사(test_only_one_file_touches_gpiod ·
#:    test_rail_pins_only_appear_in_the_owner)는 이 양방향을 갖췄는데
#:    I2C 만 정방향뿐이었다 — sol 도 이때는 정방향
#:    (test_only_one_file_drives_the_digital_inputs)뿐이었고, 역방향은
#:    검토 지적 I5 로 test_sol_owner_does_not_touch_the_led_pin 이 채웠다.
#:
#:    I2C 는 세 포트(A·B·C)에 걸쳐 있어 sol·레일처럼 "포트 하나 + 핀
#:    번호 집합"으로 못 묶는다 — 포트마다 다른 핀 집합을 쓴다.
I2C_PORT_PINS = {
    "GPIOA": {"GPIO_PIN_8": "PA8 = I2C3 SCL"},
    "GPIOB": {"GPIO_PIN_8": "PB8 = I2C1 SCL", "GPIO_PIN_9": "PB9 = I2C1 SDA"},
    "GPIOC": {"GPIO_PIN_9": "PC9 = I2C3 SDA", "GPIO_PIN_10": "PC10 = I2C5 SDA",
              "GPIO_PIN_11": "PC11 = I2C5 SCL"},
}


def test_only_one_file_drives_the_i2c_pins():
    """I2C 핀(PA8·PC9·PC10·PC11·PB8·PB9)을 만지는 파일은 mk_i2c_io.c 하나.

    🔴 레일·sol 과 같은 모양의 역방향 검사다. GPIOA 를 여는 파일이 이제
       셋이라(sol · WS2812 · I2C) 그 자리에서 핀 번호가 겹치는 실수를
       "누가 이 핀을 언급하는가" 목록으로 잡는다.
    """
    touching = []
    for path in _sources():
        if path.name == I2C_OWNER:
            continue
        code = _strip_comments(path.read_text(encoding="utf-8"))
        for port, pins in I2C_PORT_PINS.items():
            if port not in code:
                continue
            for pin in pins:
                if re.findall(rf"\b{pin}\b", code):
                    touching.append(f"{path.name}:{port}.{pin}")
    assert touching == [], (
        f"I2C 핀을 {I2C_OWNER} 말고 다른 파일이 건드린다: {touching}"
    )


#: 🔴 LCD(J25) 핀 — KiCad 넷리스트 확인 2026-08-19
#:    (docs/superpowers/specs/2026-08-19-lcd-hardware-facts.md).
#:
#:    포트 둘에 걸쳐 있고, **같은 핀 번호가 두 포트에 다 있다** —
#:    PB14 = MISO, PD14 = 터치 CS. 그래서 I2C 와 같은 모양으로 포트별
#:    핀 집합을 쓴다. 번호만 보면 가려지지 않는다.
LCD_OWNER = "mk_lcd_io.c"
LCD_PORT_PINS = {
    "GPIOB": {"GPIO_PIN_6": "PB6 = 백라이트 (USART1_TX 이기도 하다)",
              "GPIO_PIN_12": "PB12 = LCD CS",
              "GPIO_PIN_13": "PB13 = SPI2 SCK",
              "GPIO_PIN_14": "PB14 = SPI2 MISO (터치와 공유)",
              "GPIO_PIN_15": "PB15 = SPI2 MOSI"},
    "GPIOD": {"GPIO_PIN_12": "PD12 = 터치 IRQ",
              "GPIO_PIN_13": "PD13 = LCD RESX",
              "GPIO_PIN_14": "PD14 = 터치 CS",
              "GPIO_PIN_15": "PD15 = LCD D/CX"},
}


def test_only_one_file_drives_the_lcd_pins():
    """J25 의 핀을 만지는 파일은 mk_lcd_io.c 하나.

    🔴 두 포트 다 이미 남이 쓰고 있다 — GPIOB 는 UART(PB10·PB11)와
       I2C1(PB8·PB9), GPIOD 는 전원 레일(PD8~PD10)과 상태 LED(PD11).
       한 파일이 포트를 통째로 초기화하면 서로를 덮는다. 레일을 덮으면
       24V 가 예고 없이 움직인다.
    """
    touching = []
    for path in _sources():
        if path.name == LCD_OWNER:
            continue
        code = _strip_comments(path.read_text(encoding="utf-8"))
        for port, pins in LCD_PORT_PINS.items():
            if port not in code:
                continue
            for pin in pins:
                if re.findall(rf"\b{pin}\b", code):
                    touching.append(f"{path.name}:{port}.{pin}")
    assert touching == [], (
        f"J25 핀을 {LCD_OWNER} 말고 다른 파일이 건드린다: {touching}"
    )


def test_the_lcd_owner_does_not_touch_the_rails_or_the_status_led():
    """역방향 — mk_lcd_io.c 가 같은 포트(GPIOD)의 레일·LED 를 안 건드리는지.

    🔴 test_rail_pins_only_appear_in_the_owner 가 PD8·PD9·PD10 을 이미
       보지만 상태 LED(PD11)는 RAIL_PINS 에 없다. LCD 가 GPIOD 를 여는
       첫 남의 파일이므로 여기서 함께 못박는다 — 상태 LED 는 시리얼이
       조용할 때 "보드가 죽었나 통신만 안 되나" 를 가르는 유일한 신호다
       (main.c 의 blink).
    """
    code = _strip_comments((FW / "bsp" / LCD_OWNER).read_text(encoding="utf-8"))
    for pin, what in (("GPIO_PIN_8", "PD8 = 24V"), ("GPIO_PIN_9", "PD9 = 14.9V"),
                      ("GPIO_PIN_10", "PD10 = 5V"), ("GPIO_PIN_11", "PD11 = 상태 LED")):
        assert not re.findall(rf"\b{pin}\b", code), (
            f"{LCD_OWNER} 이 {pin} ({what}) 을 언급한다 — GPIOD 를 함께 쓰는 "
            f"파일이라 이 핀들은 {RAIL_OWNER} 만 만진다"
        )


def test_the_lcd_owner_pins_the_touch_chip_select_high():
    """🔴 터치 CS(PD14)를 비선택(High)으로 못박고 다시는 안 내리는지.

    LCD 와 터치(XPT2046)가 **같은 SPI 버스**다 — SCK·MOSI·MISO 가 한 넷이고
    CS 만 갈린다(넷리스트 확인 2026-08-19). 터치 CS 가 떠 있거나 어쩌다
    Low 로 가면, 그 칩이 LCD 로 보내는 클럭에 반응해 MISO 를 물고 늘어진다.
    R89 10k 풀업이 리셋 직후를 덮어 주지만 풀업은 "아직 아무도 안 몬 상태"
    일 뿐이라 근거로 삼지 않는다.

    1단계는 터치를 안 쓰므로 이 핀에 대한 옳은 코드는 **딱 한 줄**,
    초기화에서 High 로 쓰는 것뿐이다.
    """
    code = _strip_comments((FW / "bsp" / LCD_OWNER).read_text(encoding="utf-8"))
    lines = [ln for ln in code.splitlines() if "PIN_TOUCH_CS" in ln]
    assert lines, f"{LCD_OWNER} 에 PIN_TOUCH_CS 가 없다 — 이름이 바뀌었나"

    wrote_high = [ln for ln in lines
                  if "HAL_GPIO_WritePin" in ln and "GPIO_PIN_SET" in ln]
    assert wrote_high, (
        f"{LCD_OWNER} 이 터치 CS 를 High(비선택)로 세우지 않는다"
    )
    for ln in lines:
        assert "GPIO_PIN_RESET" not in ln and "?" not in ln, (
            f"{LCD_OWNER} 이 터치 CS 를 내릴 수 있다: {ln.strip()} — "
            f"1단계에서 이 핀은 High 로 고정이다"
        )


def test_nothing_opens_usart1():
    """🔴 PB6 은 이 보드에서 백라이트다 — USART1_TX 로 열면 안 된다.

    STM32H723 의 AF 표(DS13313 p.75 Table 8)에는 PB6 에 USART1_TX 가 있다.
    회로가 그 핀을 J25.8(백라이트)로 뺐으므로, 누가 USART1 을 기본 핀맵으로
    열면 백라이트가 시리얼 파형으로 깜빡인다. 증상이 "화면이 이상하게
    깜빡인다" 뿐이라 UART 를 의심하기까지 오래 걸린다.
    """
    offenders = [p.name for p in _sources()
                 if "USART1" in _strip_comments(p.read_text(encoding="utf-8"))]
    assert offenders == [], (
        f"USART1 을 언급하는 파일: {offenders} — PB6 이 백라이트다"
    )


def test_the_lcd_test_still_exists():
    """초기화 순서·대기시간·비차단을 보는 C 시험이 지워지지 않았는지.

    🔴 대기시간이 빠져도 **켜지는 판이 있다.** 그래서 화면으로는 못 잡고,
       나중에 다른 판에서 "가끔 안 켜진다" 로 돌아온다. 시험이 사라지면
       그 자리로 조용히 되돌아간다.
    """
    t = (FW / "tests" / "test_lcd.c").read_text(encoding="utf-8")
    for name in ("test_pixel_bytes_keep_the_top_six_bits",
                 "test_disabled_lcd_never_touches_anything",
                 "test_reset_pulse_comes_first_and_is_long_enough",
                 "test_no_command_before_the_reset_cancel_time",
                 "test_init_command_order_is_exactly_what_the_datasheet_needs",
                 "test_colmod_selects_eighteen_bits_per_pixel",
                 "test_tick_never_waits_for_the_transfer",
                 "test_sleep_out_is_followed_by_the_datasheet_wait",
                 "test_chip_select_stays_low_through_the_pixel_stream",
                 "test_the_catalog_has_the_lcd_enabled_item"):
        assert name in t, f"{name} 이 사라졌다"


def test_makefile_builds_the_tested_sources():
    """보드에 굽는 것이 호스트에서 시험한 바로 그 파일들인지.

    🔴 보드용으로 따로 고친 판이 생기면 시험이 아무것도 보증하지 않는다.
    """
    mk = (FW / "Makefile").read_text(encoding="utf-8")
    for src in ("app/mk_framing.c", "app/mk_json.c", "app/mk_hostlink.c"):
        assert src in mk, f"Makefile 이 {src} 를 빌드하지 않는다"
