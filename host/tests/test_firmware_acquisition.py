"""수집이 슈퍼루프에 매여 있지 않은지 펌웨어 소스에서 확인한다.

🔴 **왜 이 파일이 생겼나** (2026-08-19, 채널당 10 ms 작업)

   이 시스템의 존재 이유는 카메라 동기다. 카메라가 최대 33 ms 마다 찍고,
   요구는 센서값이 채널당 10 ms ~ 100 ms 간격으로 오는 것이다. 사용자가
   못박은 절대 제약은 둘이다:

       "뭘 하던지 센서 수집에는 방해가 안된다면 뭐든지 해도 돼"
       "센서 값을 늦게 보내도 돼. 무조건 수집을 정상적으로 타임스탬프
        찍어서 가지고 있어야해"

   즉 송신 지연은 죄가 아니고 **표본을 못 뜨는 것**이 죄다.

   그런데 예전 판은 `mk_ads_tick()` 을 **슈퍼루프에서만** 불렀다. 슈퍼루프
   한 바퀴는 짧지 않다:

       - `mk_i2c_tick()` 이 HAL 블로킹 I2C 를 쓴다. 버스가 눌리면 한 바퀴에
         최악 60 ms (firmware/stage1/bsp/mk_i2c_io.c 머리말).
       - `mk_telem_tick()` 이 `HAL_UART_Transmit`(블로킹)으로 줄을 낸다.
         921600 baud 에서 163 B 한 줄이 1.77 ms 이고, 한 바퀴에 최대
         MK_TELEM_MAX_LINES 줄이 나간다.

   7채널 × 10 ms 면 채널 하나에 쓸 수 있는 시간이 1.43 ms 다. 슈퍼루프가
   시작 신호를 쥐고 있는 한 그 예산은 지킬 수 없고, 못 지킨 표본은
   `finish()` 의 따라잡기 포기 때문에 **큐의 drops 에도 안 잡힌 채**
   사라진다. 화면에는 아무 이상이 없다.

   그래서 수집의 진행을 인터럽트로 옮겼다. 이 파일은 그것이 **되돌려지지
   않았는지** 소스에서 본다 — 되돌려도 컴파일되고 부팅되고 값도 나오기
   때문에, 시험이 없으면 아무도 모른다.
"""
from __future__ import annotations

import re
from pathlib import Path

FW = Path(__file__).resolve().parents[2] / "firmware" / "stage1"
MAIN_C = FW / "main.c"
ADS_IO_C = FW / "bsp" / "mk_ads_io.c"
IT_C = FW / "bsp" / "stm32h7xx_it.c"
ADS_C = FW / "app" / "mk_ads1256.c"


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def _code(path: Path) -> str:
    return _strip_comments(path.read_text(encoding="utf-8"))


def test_sources_exist():
    """소스가 옮겨가면 아래 검사가 조용히 통과하는 것을 막는다."""
    for path in (MAIN_C, ADS_IO_C, IT_C, ADS_C):
        assert path.exists(), f"없다: {path}"


# ------------------------------------------------------- 진행의 출처
def test_the_superloop_does_not_drive_the_acquisition():
    """🔴 `main()` 이 `mk_ads_tick()` 을 부르면 안 된다.

    부르는 순간 수집 주기가 슈퍼루프 주기에 묶인다 — 그것이 정확히
    이번에 걷어낸 결함이다. 진행은 전부 인터럽트에서 나온다:
    다음 채널로 넘어가는 것은 SPI 완료 인터럽트 안의 `finish()` 가,
    쉬고 있다가 다시 시작하는 것은 `mk_ads_io.c` 의 1 kHz 타이머가 한다.
    """
    assert "mk_ads_tick" not in _code(MAIN_C), (
        "main.c 가 mk_ads_tick 을 부른다 — 수집이 다시 슈퍼루프에 묶였다. "
        "I2C 한 번(최악 60 ms)이면 채널당 10 ms 는 불가능해진다"
    )


def test_the_acquisition_has_its_own_interrupt_timebase():
    """수집을 다시 시작하는 1 kHz 인터럽트가 실제로 배선돼 있는가."""
    it = _code(IT_C)
    assert "TIM7_IRQHandler" in it, "TIM7 벡터가 없다"
    assert "mk_ads_io_tick_isr" in it, (
        "TIM7_IRQHandler 가 수집 tick 을 부르지 않는다"
    )
    io = _code(ADS_IO_C)
    assert "mk_ads_io_tick_isr" in io, "bsp 에 tick ISR 구현이 없다"
    assert "mk_ads_tick" in io, "tick ISR 이 상태머신을 밀지 않는다"


def test_the_tick_timer_runs_at_one_kilohertz():
    """🔴 1 kHz 여야 한다.

    표본 예정(`next_due_ms`)의 단위가 밀리초이므로, 그보다 성기게 깨우면
    예정이 그 간격만큼 반올림된다 — 10 ms 주기에서 그것은 곧 유실이다.
    프리스케일·ARR 을 숫자로 박지 않고 `mk_clock.h` 에서 파생시켰는지도
    함께 본다(클럭을 바꾸면 조용히 어긋나는 종류의 상수다).
    """
    io = _code(ADS_IO_C)
    assert "MK_ADS_TICK_HZ" in io, "tick 주파수 상수가 없다"
    m = re.search(r"#define\s+MK_ADS_TICK_HZ\s+(\d+)", io)
    assert m is not None and int(m.group(1)) == 1000, (
        "수집 tick 이 1 kHz 가 아니다"
    )
    m = re.search(r"#define\s+MK_ADS_TICK_PSC\s+(\S+)", io)
    assert m is not None and m.group(1).startswith("MK_"), (
        "TIM7 의 프리스케일이 숫자로 박혀 있다 — mk_clock.h 에서 파생시키지 "
        "않으면 클럭을 바꿨을 때 수집 주기가 조용히 어긋난다"
    )
    clock_h = (FW / "bsp" / "mk_clock.h").read_text(encoding="utf-8")
    assert f"#define {m.group(1)}" in clock_h, (
        f"{m.group(1)} 이 mk_clock.h 에 없다"
    )


def test_the_tick_shares_the_priority_of_the_other_acquisition_interrupts():
    """🔴 우선순위가 같아야 한다 — 그것이 유일한 상호배제 장치다.

    `mk_ads_tick`·`mk_ads_on_spi_done`·`mk_ads_on_drdy` 는 같은 `MkAds`
    구조체를 건드린다. Cortex-M 에서 **같은 우선순위의 인터럽트는 서로를
    선점하지 못하므로**(대기만 한다), 넷을 같은 값으로 두면 임계구역 없이
    안전하다. 하나만 값이 다르면 그 하나가 나머지를 상태 전이 도중에
    끊고 들어와 전송이 겹친다 — 재현하기 거의 불가능한 결함이 된다.
    """
    io = _code(ADS_IO_C)
    prios = dict(
        re.findall(
            r"HAL_NVIC_SetPriority\(\s*([A-Za-z0-9_]+)\s*,\s*([A-Za-z0-9_]+)",
            io,
        )
    )
    wanted = ("EXTI15_10_IRQn", "SPI4_IRQn", "DMA1_Stream0_IRQn",
              "DMA1_Stream1_IRQn", "TIM7_IRQn")
    missing = [n for n in wanted if n not in prios]
    assert not missing, f"우선순위를 안 정한 벡터가 있다: {missing}"
    assert len({prios[n] for n in wanted}) == 1, (
        f"수집 인터럽트의 우선순위가 서로 다르다: "
        f"{ {n: prios[n] for n in wanted} } — 서로 선점하면 상태머신이 깨진다"
    )


def test_the_state_machine_chains_to_the_next_channel_itself():
    """한 바퀴가 끝나는 자리에서 다음 채널을 곧바로 시작하는가.

    1 kHz 타이머만으로는 부족하다. 채널 하나에 1.43 ms 인데 시작이 최대
    1 ms 씩 늦춰지면 한 바퀴가 예산을 넘는다. 이어 돌리기가 그 지연을
    없앤다 — 타이머는 "다 돌고 쉬다가 다시 시작" 만 맡는다.
    """
    ads = _code(ADS_C)
    m = re.search(r"static void finish\(MkAds \*a, int64_t now_ms\)\s*\{"
                  r"(.*?)\n\}", ads, flags=re.S)
    assert m is not None, "finish() 를 못 찾았다"
    assert "start_if_due" in m.group(1), (
        "finish() 가 다음 채널로 이어가지 않는다 — 표본 시작이 슈퍼루프를 "
        "기다리게 된다"
    )


# ------------------------------------------------------- 큐 깊이
#: 슈퍼루프가 채널 하나의 큐를 비우지 않고 지나갈 수 있는 최악 시간 (ms).
#:
#: 🔴 어림이 아니라 소스에 적힌 값의 합이다:
#:
#:    60 ms   I2C — HAL 블로킹. `I2C_TIMEOUT_BUSY`(25 ms, HAL 고정) +
#:            우리 타임아웃 5 ms = 30 ms 가 xfer 한 번이고, BH1750 의 start
#:            가 한 스텝에서 xfer 를 두 번 부른다 (bsp/mk_i2c_io.c 머리말).
#:   134 ms   텔레메트리 — `HAL_UART_Transmit` 은 블로킹이다. 한 바퀴에
#:            ain·i2c·din·gnss_raw 가 각각 최대 MK_TELEM_MAX_LINES(16) 줄,
#:            한 줄 최대 MK_LINE_MAX(192)+1 B → 64 × 193 = 12,352 B.
#:            921600 baud 는 8N1 이라 92,160 B/s → 134 ms.
WORST_STALL_MS = 60 + 134

#: `ain*.period_ms` 의 하한 (app/mk_cfgtable.c). 이보다 빨리 수집할 수 없다.
MIN_PERIOD_MS = 10


def test_the_sample_queue_survives_the_worst_superloop_stall():
    """🔴 큐 깊이는 어림이 아니라 최악 정지 시간에서 나온다.

    32 칸은 100 ms 주기를 전제로 고른 값이었다(3.2 초분). 10 ms 로 내리면
    같은 32 칸이 **0.32 초분**이 되고, 위의 최악 정지(194 ms) 한 번에
    20 칸이 찬다 — 그 뒤 배출이 한 바퀴만 더 밀리면 넘친다.

    넘치면 `mk_queue_push` 가 가장 오래된 표본을 버린다. 그것이 사용자가
    가장 싫어하는 실패다.

    여유를 2배로 두는 이유: 정지가 풀린 직후에도 배출은 한 번에
    MK_TELEM_MAX_LINES 줄까지만 나가므로, 밀린 것을 다 내보내는 동안에도
    새 표본이 계속 들어온다.
    """
    src = _code(MAIN_C)
    m = re.search(r"#define\s+SAMPLES_PER_CHANNEL\s+(\d+)", src)
    assert m is not None, "SAMPLES_PER_CHANNEL 을 못 찾았다"
    depth = int(m.group(1))
    need = 2 * (WORST_STALL_MS // MIN_PERIOD_MS)
    assert depth >= need, (
        f"큐 깊이 {depth} 칸은 최악 정지 {WORST_STALL_MS} ms 를 "
        f"{MIN_PERIOD_MS} ms 주기로 견디지 못한다 (최소 {need} 칸)"
    )
