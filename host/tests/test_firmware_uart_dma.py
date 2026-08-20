"""호스트 링크 송신이 슈퍼루프를 붙잡지 않는지 펌웨어 소스에서 확인한다.

🔴 **왜 이 파일이 생겼나** (2026-08-20, 실기기 30초 측정)

   7채널 × 10 ms, `adc.drate=2000`, `tx.period_ms=10`, 링크 1.5 Mbps:

       J3~J9  고유 2123~2124개씩   (3000 이어야 한다)
       간격   중앙 10 ms / 최대 30 ms
       큐     일곱 채널 모두 depth 64 peak 64 drops 853~860
       총     14,863줄 / 30초 = 초당 495줄   (700 이어야 한다)

   495 ÷ 16(`MK_TELEM_MAX_LINES`) = 초당 31 틱 → **슈퍼루프 한 바퀴가
   32 ms**. 획득 간격 최대 30 ms 와 정확히 맞물린다. 그런데 링크는
   495줄 × 약 135 B = 67 KB/s 로 1.5 Mbps(187 KB/s)의 36 % 밖에 안 썼다.

   막고 있던 것은 링크가 아니라 `bsp/mk_uart.c` 의

       HAL_UART_Transmit(&s_uart, ..., 100u);   /* 블로킹 */

   이었다. 16줄 × 135 B = 2,160 B 를 1.5 Mbps 로 내보내는 데만 14 ms
   (921600 이면 23 ms), 거기에 I2C·LCD·GNSS 가 붙어 한 바퀴 32 ms 가 됐다.
   수집은 이미 인터럽트로 떼어냈는데(TIM7·DRDY·SPI4 DMA) 송신만 아직
   슈퍼루프를 붙잡고 있었던 것이다.

   이 파일은 그것이 **되돌려지지 않았는지** 소스에서 본다. 되돌려도
   컴파일되고 부팅되고 값도 나오기 때문에, 시험이 없으면 아무도 모른다.
   `test_firmware_acquisition.py` 와 같은 성격의 시험이다.
"""
from __future__ import annotations

import re
from pathlib import Path

FW = Path(__file__).resolve().parents[2] / "firmware" / "stage1"
UART_C = FW / "bsp" / "mk_uart.c"
UART_H = FW / "bsp" / "mk_uart.h"
IT_C = FW / "bsp" / "stm32h7xx_it.c"
ADS_IO_C = FW / "bsp" / "mk_ads_io.c"
LCD_IO_C = FW / "bsp" / "mk_lcd_io.c"
WS_IO_C = FW / "bsp" / "mk_ws2812_io.c"
MAIN_C = FW / "main.c"


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def _code(path: Path) -> str:
    return _strip_comments(path.read_text(encoding="utf-8"))


def test_sources_exist():
    """소스가 옮겨가면 아래 검사가 조용히 통과하는 것을 막는다."""
    for path in (UART_C, UART_H, IT_C, ADS_IO_C, LCD_IO_C, WS_IO_C, MAIN_C):
        assert path.exists(), f"없다: {path}"


# ------------------------------------------------------- 블로킹 되돌림
def test_the_link_write_is_not_blocking():
    """🔴 `HAL_UART_Transmit` 이 다시 나타나면 안 된다.

    그 한 줄이 이번에 걷어낸 결함 자체다. 바이트가 다 나갈 때까지
    슈퍼루프가 서고, 그동안 인터럽트로 계속 들어오는 표본이 큐를 넘친다.
    """
    code = _code(UART_C)
    assert "HAL_UART_Transmit(" not in code, (
        "mk_uart.c 가 HAL_UART_Transmit(블로킹)을 다시 부른다 — 송신이 "
        "슈퍼루프를 붙잡는다. 실기기에서 한 바퀴가 32 ms 였다(2026-08-20)"
    )


def test_the_write_path_goes_through_the_ring():
    """넣는 쪽은 링에 넣고 끝나야 한다."""
    code = _code(UART_C)
    assert "mk_txring_push" in code, "mk_uart_write 가 링에 넣지 않는다"
    m = re.search(r"void\s+mk_uart_write\s*\([^)]*\)\s*\{(.*?)\n\}", code,
                  flags=re.S)
    assert m is not None, "mk_uart_write 를 못 찾았다"
    body = m.group(1)
    assert "while" not in body and "for (" not in body, (
        "mk_uart_write 안에 대기 루프가 있다 — 즉시 돌아와야 한다"
    )


def test_the_telemetry_path_leaves_room_for_command_replies():
    """🔴 텔레메트리가 링을 다 먹으면 사용자가 되돌릴 수단을 잃는다.

    링크를 포화시킨 것은 설정(채널 수·주기·baud)인데, 그 설정을 되돌리는
    `$SACK` 이 나갈 자리가 없으면 사람이 아무것도 못 하는 상태가 된다.
    mk_linkbaud 의 자동 되돌림과 같은 정신이다.
    """
    code = _code(UART_C)
    assert "mk_uart_write_bulk" in code, (
        "텔레메트리 전용 경로(mk_uart_write_bulk)가 없다 — 명령 응답 몫을 "
        "남길 방법이 없다"
    )
    assert "MK_TX_RESERVE" in code, "예약 몫 상수가 없다"
    assert "mk_uart_write_bulk" in _code(MAIN_C), (
        "main.c 의 텔레메트리 emit 이 bulk 경로를 안 쓴다 — 예약이 무의미해진다"
    )


# ------------------------------------------------------- DMA 배치
def test_the_tx_buffer_lives_where_dma_can_reach_it():
    """🔴 DTCM 에 두면 전송이 **조용히** 안 된다 (RM0468 p.140/p.106).

    빌드 뒤 `tools/check_dma_placement.py` 가 실제 주소로 다시 본다.
    여기서는 선언에 표시가 붙어 있는지를 본다 — 붙이는 것을 잊는 것이
    정확히 이 결함의 발현 방식이기 때문이다.
    """
    code = _code(UART_C)
    m = re.search(r"MK_DMA_BUF\s+\w+\s*\[", code)
    assert m is not None, (
        "송신 링 저장소에 MK_DMA_BUF 가 없다 — DTCM 에 잡히면 DMA1·DMA2 가 "
        "닿지 못해 전송이 조용히 실패한다"
    )


def test_the_tx_stream_does_not_collide_with_acquisition_or_display():
    """🔴 스트림이 겹치면 수집이 깨진다 — 이 프로젝트에서 가장 나쁜 실패다.

    이미 쓰는 것: DMA1 Stream0·1 = SPI4(ADS1256), Stream2 = TIM3(WS2812),
    Stream3 = SPI2(LCD).
    """
    used: dict[str, str] = {}
    for path in (ADS_IO_C, LCD_IO_C, WS_IO_C):
        for name in re.findall(r"(DMA\d_Stream\d)\s*;", _code(path)):
            used[name] = path.name
    assert used, "다른 파일에서 DMA 스트림을 하나도 못 읽었다 — 검사가 헛돈다"

    mine = re.findall(r"(DMA\d_Stream\d)\s*;", _code(UART_C))
    assert mine, "mk_uart.c 가 DMA 스트림을 안 고른다"
    for name in mine:
        assert name not in used, (
            f"송신 DMA 가 {name} 를 쓰는데 {used[name]} 가 이미 쓰고 있다"
        )


def test_the_tx_stream_has_its_interrupt_vector_wired():
    """완료 인터럽트가 없으면 첫 조각만 나가고 링크가 통째로 선다."""
    mine = re.findall(r"(DMA\d_Stream\d)\s*;", _code(UART_C))
    it = _code(IT_C)
    for name in mine:
        assert f"{name}_IRQHandler" in it, f"{name} 의 벡터가 없다"
    assert "mk_uart_dma_isr" in it, (
        "벡터가 송신 DMA ISR 을 부르지 않는다 — 다음 조각이 이어지지 않는다"
    )


def test_the_tx_interrupt_is_not_more_urgent_than_the_acquisition():
    """🔴 수집(DRDY·SPI4·DMA1 Stream0/1·TIM7)이 우선이다.

    Cortex-M 은 **숫자가 작을수록 급하다.** 송신 완료가 수집을 선점하면
    획득 시각이 밀리는데, 그것은 이 시스템이 존재하는 이유를 깎는 일이다
    (설계 원칙 2). 송신은 몇 µs 늦어도 아무 일도 안 일어난다.
    """
    ads = _code(ADS_IO_C)
    m = re.search(r"#define\s+MK_ADS_IRQ_PRIO\s+(\d+)", ads)
    assert m is not None, "MK_ADS_IRQ_PRIO 를 못 찾았다"
    acq = int(m.group(1))

    uart = _code(UART_C)
    prios = dict(
        re.findall(
            r"HAL_NVIC_SetPriority\(\s*(DMA\d_Stream\d_IRQn)\s*,\s*([A-Za-z0-9_]+)",
            uart,
        )
    )
    assert prios, "송신 DMA 의 NVIC 우선순위를 안 정했다"
    defines = dict(re.findall(r"#define\s+(MK_\w+)\s+(\d+)u?", uart))
    for vec, value in prios.items():
        num = int(defines.get(value, value)) if str(value).isdigit() or value in defines \
            else None
        assert num is not None, f"{vec} 의 우선순위 {value} 를 숫자로 못 읽었다"
        assert num > acq, (
            f"{vec} 우선순위 {num} 이 수집({acq})보다 급하다 — 수집을 선점한다"
        )


# ------------------------------------------------------- baud 전환
def test_changing_the_baud_waits_for_the_ring_to_drain():
    """🔴 보내던 바이트를 자르지 않는다 (규격 §4.2.2 규칙 1).

    `mk_linkbaud` 는 "응답을 옛 속도로 다 보낸 뒤 전환" 에 기대고 있다.
    링이 생기면서 "다 보냈다" 의 뜻이 달라졌다 — 예전엔
    `HAL_UART_Transmit` 이 돌아온 것만으로 충분했지만, 이제는 링이 비고
    DMA 가 끝나고 TC 까지 서야 한다. 셋 중 하나라도 안 보면 응답 앞부분만
    옛 속도로 나가고 호스트는 성공했는지조차 모른 채 링크를 잃는다.
    """
    code = _code(UART_C)
    m = re.search(r"void\s+mk_uart_set_baud\s*\([^)]*\)\s*\{(.*?)\n\}", code,
                  flags=re.S)
    assert m is not None, "mk_uart_set_baud 를 못 찾았다"
    body = m.group(1)
    assert "mk_txring_used" in body or "mk_txring_free" in body, (
        "속도를 바꾸기 전에 링이 빌 때까지 기다리지 않는다 — 아직 안 보낸 "
        "바이트가 새 속도로 나가 통째로 깨진다"
    )
    assert "USART_ISR_TC" in body, (
        "TC(전송 완료)를 확인하지 않는다 — 마지막 바이트가 전선에 다 실리기 "
        "전에 속도가 바뀐다"
    )


def test_the_baud_wait_cannot_hang_forever():
    """🔴 영영 기다리지 않는다.

    TC 가 안 서는 고장에서 여기 갇히면 보드가 통째로 멈춘다 — 되돌림
    시한도 못 돈다. 그러면 안전장치가 그 자리에서 벽돌을 만든다.
    """
    code = _code(UART_C)
    m = re.search(r"void\s+mk_uart_set_baud\s*\([^)]*\)\s*\{(.*?)\n\}", code,
                  flags=re.S)
    assert m is not None
    body = m.group(1)
    assert re.search(r"guard|deadline|timeout", body), (
        "대기에 시한이 없다 — 고장 하나로 보드가 통째로 선다"
    )


def test_the_dma_request_is_re_armed_after_the_uart_is_reconfigured():
    """🔴 `HAL_UART_Init()` 이 CR3 를 다시 쓴다 — DMAT 가 지워진다.

    RXNE 를 매번 다시 켜야 하는 것과 정확히 같은 함정이다(그 주석이 이미
    `uart_configure()` 에 있다). 여기를 빠뜨리면 baud 를 한 번 바꾼 뒤
    보드가 **영원히 말을 못 한다** — DMA 는 걸리는데 USART 가 요청을 안 한다.
    """
    code = _code(UART_C)
    m = re.search(r"static void uart_configure\([^)]*\)\s*\{(.*?)\n\}", code,
                  flags=re.S)
    assert m is not None, "uart_configure 를 못 찾았다"
    assert "USART_CR3_DMAT" in m.group(1), (
        "uart_configure 가 DMAT 를 다시 안 켠다 — HAL_UART_Init 이 지운다"
    )
