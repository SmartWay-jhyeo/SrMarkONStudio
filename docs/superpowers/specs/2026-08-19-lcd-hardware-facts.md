# LCD(J25) 하드웨어 사실 — 원본 확인분

> 근거: KiCad 넷리스트 `D:\STM32_PCB_Board\output\datasheet_src\STM32_v2.0_current.net`
> (데이터시트가 아니라 설계 원본. CLAUDE.md §0 의 규칙대로 넷리스트를 진실로 삼는다)
> 확인일 2026-08-19.

## J25 핀 배치 (넷리스트에서 그대로)

| J25 | 넷 | MCU | 역할(모듈 통례) |
|---|---|---|---|
| 1 | V3V3 | — | 전원 |
| 2 | GND | — | 접지 |
| 3 | PB12 | U5.73 | LCD_CS (R88 10k 풀업) |
| 4 | PD13 | U5.82 | RESET (R90 10k 풀업) |
| 5 | PD15 | U5.86 | DC / RS |
| 6 | PB15_MOSI | U5.76 경유 R92 | MOSI (LCD) |
| 7 | PB13_SCK | U5.74 경유 R91 | SCK (LCD) |
| 8 | PB6 | U5.136 | 백라이트 |
| 9 | PB14 | U5.75 | MISO (LCD) |
| 10 | PB13_SCK | 〃 | SCK (터치) — 6·7 과 같은 넷 |
| 11 | PD14 | U5.85 | 터치 CS (R89 10k 풀업) |
| 12 | PB15_MOSI | 〃 | MOSI (터치) |
| 13 | PB14 | 〃 | MISO (터치) |
| 14 | PD12 | U5.81 | 터치 IRQ |

🔴 **LCD 와 터치가 SPI 버스를 공유한다.** SCK/MOSI/MISO 가 같은 넷이고 CS 만 갈린다
(PB12 = LCD, PD14 = 터치). 두 칩의 SPI 모드·클럭 상한이 다르므로 CS 를 바꿀 때마다
클럭 분주비를 함께 바꿔야 한다.

🔴 **직렬 저항이 있다**: R91 = 22Ω (SCK), R92 = 22Ω (MOSI). 링잉 억제용이다.
MISO(PB14)에는 없다.

🔴 **CS·RESET 에 10k 풀업이 걸려 있다**(R88·R89·R90 → V3V3). 리셋 직후 MCU 핀이
입력으로 떠 있어도 두 칩 모두 비선택 상태이고 RESET 이 해제 상태로 유지된다.

## 대체 기능 (AF) — 확인분

근거: `STM32H723ZGT6.pdf` (DS13313) p.75, Table 8 "STM32H723 pin alternate functions".
열 순서는 AF0…AF15.

| 핀 | AF5 | 결론 |
|---|---|---|
| PB13 | `SPI2_SCK/I2S2_CK` | SPI2_SCK = **AF5** |
| PB14 | `SPI2_MISO/I2S2_SDI` | SPI2_MISO = **AF5** |
| PB15 | `SPI2_MOSI/I2S2_SDO` | SPI2_MOSI = **AF5** |
| PB12 | `SPI2_NSS/I2S2_WS` | 하드웨어 NSS 가 있지만 **쓰지 않는다** — 칩이 둘이라 CS 두 개를 GPIO 로 직접 몬다 |

PD12·PD13·PD14·PD15·PB6 은 모두 평범한 GPIO 로 쓴다(AF 불필요).

## 자원 충돌 — 확인분

- **SPI2 는 비어 있다.** ADS1256 은 SPI4 다(`bsp/mk_ads_io.c`).
- **DMA1 Stream0·1 = SPI4(ADS1256), Stream2 = WS2812.** Stream3 이상과 DMA2 전체가 비어 있다.
- 🔴 **EXTI12 는 터치 IRQ(PD12) 가 쓴다.** EXTI15 는 ADS1256 DRDY(PE15), EXTI4·EXTI9_5 는
  디지털 입력(PA4~PA6)이 이미 쓴다. 겹치지 않는다.
- 🔴 **PB6 은 백라이트다.** USART1_TX 도 PB6 에 있지만(Table 8) 이 보드에서는 LCD 백라이트로
  배선돼 있으므로 USART1 을 PB6 으로 열면 안 된다.

## 화소 형식 — 데이터시트가 스스로 모순된다 🔴

`ILI9488.pdf` p.121 §4.7.2 "DBI Type-C Option 3 (4-Line Serial Interface)" 는 쓸 수 있는
형식을 셋으로 **명시한다**:

| 형식 | 3Ah DBI[2:0] |
|---|---|
| 8 colors, RGB 1-1-1 | `001` |
| **65K-Colors, RGB 5-6-5** | **`101`** |
| 262K-Colors, RGB 6-6-6 | `110` |

그런데 바로 아래 하위 절은 **둘뿐이다** — §4.7.2.1 = 3비트/화소, §4.7.2.2 = 18비트/화소.
**16비트/화소의 SPI 데이터 배치 그림이 없다.** 다른 두 형식에는 있고, 병렬 인터페이스
(§4.7.3, p.123)에는 16비트 그림이 있다. 즉 SPI 에서만 빠져 있다.

→ 원문으로 확정되는 것은 여기까지다. "된다고 적혀 있지만 배치 그림이 없다."
  세간에 "ILI9488 은 SPI 로 565 를 못 쓴다"고 알려진 것이 이 공백에서 나온 것으로 보인다.

🔴 **설계는 18bpp(화소당 3바이트)를 전제로 잡는다.** 근거가 있는 쪽이 그것이다. 565 는
   실물에서 시도해 보고 색이 맞으면 그때 쓴다 — 데이터시트만 보고 565 를 전제하면,
   안 될 경우 전송량이 1.5배로 늘어나 화면 갱신 설계를 다시 짜야 한다.

   18bpp 전면 갱신 = 320 × 480 × 3 = **460,800 바이트**. SPI 20 MHz(≈2.5 MB/s)에서
   **약 184 ms**. 부분 갱신과 DMA 없이는 못 쓴다.

## SPI 클럭 상한 — 확정 ✅

`ILI9488.pdf` p.332 §17.4.3 "DBI Type C Option 3 (4-Line SPI System) Timing
Characteristics" 의 SCL 행:

| 기호 | 항목 | min | 뜻 |
|---|---|---|---|
| `twc` | Serial clock cycle (Write) | **50 ns** | **쓰기 20 MHz** |
| `twrh` / `twrl` | SCL H/L pulse width (Write) | 10 ns | 듀티 여유 충분 |
| `trc` | Serial clock cycle (Read) | **150 ns** | **읽기 6.6 MHz** |
| `trdh` / `trdl` | SCL H/L pulse width (Read) | 60 ns | |

같은 표의 `tds`(Data setup, Write) = 10 ns, `tas`(D/CX setup) = 10 ns.
조건은 `Ta = -30~70 °C, IOVCC = 1.65~3.3V` (표 아래 Notes 1).

→ **쓰기 상한 20 MHz, 읽기 상한 6.6 MHz.** 지금 구현은 쓰기만 하므로
  16 MHz 로 잡았다(64 MHz / 4). 나중에 레지스터를 되읽으려면 그때만
  분주비를 낮춘다 — 두 칩이 상한이 다른 것과 같은 이야기다.

## SPI 모드 — 확정 ✅ (모드 0)

`ILI9488.pdf` p.44 §4.2.1, Figure 6 아래 본문:

> "The host drives the CSX pin to low and sets the D/CX bit on the SDA pin.
> The bit is read by the ILI9488 on the **first rising edge of the SCL**
> signal."

같은 쪽 §4.2.2(읽기)도 "samples the SDA (input data) at the **rising edges**
of the SCL, and shifts to SDO (output data) at the **falling edges**".

→ 상승 엣지 샘플링 + SCL 은 Low 에서 쉰다 = **CPOL=0, CPHA=0 (모드 0)**.
  HAL 로는 `SPI_POLARITY_LOW` + `SPI_PHASE_1EDGE`.

## 🔴 SPI2 커널 클럭을 직접 골라야 한다 — 확정 ✅

이 저장소 특유의 함정이라 여기 남긴다.

`main.c` 의 `SystemClock_Config()` 는 **PLL 을 아예 켜지 않는다**
(`osc.PLL.PLLState = RCC_PLL_NONE`, HSI 64 MHz 직결). 그런데 SPI1/2/3 의
커널 클럭 기본 소스는 `pll1_q_ck` 다(`RCC_D2CCIP1R.SPI123SEL` 리셋값 000).
그대로 두면 **SCK 가 한 번도 안 움직인다.**

SPI4(ADS1256)가 멀쩡했던 것은 SPI4/5 의 기본 소스가 APB2 라서다 —
같은 보드에서 SPI 하나는 되고 하나는 안 되는 이유가 이것이다.

→ `HAL_RCCEx_PeriphCLKConfig()` 로 `Spi123ClockSelection =
  RCC_SPI123CLKSOURCE_CLKP`(per_ck), `CkperClockSelection =
  RCC_CLKPSOURCE_HSI` 를 세운다. per_ck = hsi_ker_ck = 64 MHz.
  구현: `bsp/mk_lcd_io.c` 의 `spi_init()`.

## 초기화 명령 순서 — 확정 ✅

전부 `ILI9488.pdf` 원문 근거가 있는 것만 넣었다. 구현은
`app/mk_lcd.c` 의 `INIT_CMDS[]` · `POST_CMDS[]`.

| # | 하는 일 | 근거 | 대기 |
|---|---|---|---|
| 0 | RESX Low | p.308 Table 39 `tRW` MIN 10 us · Table 40 "Longer than 9us: Reset" | ≥10 us (구현 1 ms) |
| 1 | RESX High | p.309 Table 39 주석 7 | **120 ms** |
| 2 | `3Ah` COLMOD = `0x66` | p.200 §5.2.34. 파라미터 `X DPI[2:0] X DBI[2:0]`, 110 = 18 bits/pixel | — |
| 3 | `36h` MADCTL = `0x00` | p.192 §5.2.30. 리셋 기본값도 00h(p.194) — 명시해 둔다 | — |
| 4 | `11h` SLPOUT | p.166 §5.2.13 | **5 ms** |
| 5 | `2Ah` CASET = 0 … 0x013F | p.175 §5.2.22 (MADCTL D5=0 이면 013Fh 상한) | — |
| 6 | `2Bh` PASET = 0 … 0x01DF | p.177 §5.2.23 | — |
| 7 | `2Ch` RAMWR + 화소 460,800 B | p.179 §5.2.24 | — |
| 8 | `29h` DISPON | p.174 §5.2.21 | — |
| 9 | 백라이트 On | — | — |

**리셋 취소 대기 120 ms 의 원문** (p.309, Table 39 주석 7):

> "It is necessary to wait 5msec after releasing RESX before sending
> commands. The Sleep Out command also cannot be sent in 120msec."

두 시계를 따로 셀 값이 없으므로 큰 쪽 하나로 묶어 120 ms 를 기다린 뒤
첫 명령을 낸다.

**SLPOUT 뒤 5 ms 의 원문** (p.166 §5.2.13 Restriction):

> "It is necessary to wait 5msec before sending the next command; this is
> to allow time for supply voltages and clock circuits to stabilize."

🔴 세간의 초기화 배열이 여기에 120 ms 를 두는 것은 같은 절의 **다른**
문장(Sleep In 을 보내기까지 120 ms)을 옮겨 온 것으로 보인다. 원문이
SLPOUT 뒤에 요구하는 것은 5 ms 다.

### 일부러 **넣지 않은** 명령

| 명령 | 왜 안 넣었나 |
|---|---|
| `F7h` Adjust Control 3 (`A9 51 2C 82`) | p.276 §5.3.39. 파라미터의 뜻이 `DSI_18_option` 하나뿐이고 설명이 "DSI write DCS command, use stream/loose packet RGB 666" 이다 — **MIPI DSI 전용**. SPI 로는 아무 뜻이 없다. 인터넷 초기화 배열이 거의 다 들고 있다 |
| `B0h` Interface Mode Control | p.219 §5.3.1. 기본값 `00h` = `SDA_EN=0` = "DIN and SDO pins are used for 3/4 wire serial interface" 이고, 이 보드가 정확히 그 결선이다(MOSI=PB15·MISO=PB14 가 따로 나온다). 나머지 비트는 RGB 인터페이스용 |
| `01h` SWRESET | RESX 선(PD13)이 실제로 있다. 하드웨어 리셋이 같은 일을 하면서 이전 상태에 관계없이 확정적이다 (p.150 §5.2.2 는 "Sleep Out 중에 걸면 120 ms" 라는 조건이 붙는다) |
| 감마·전원 (`E0h/E1h/C0h/C1h/C5h` …) | p.308 Table 39 주석 1: 리셋 취소 구간에 "loading ID bytes, VCOM setting and other settings from **the EEPROM** to registers" 가 일어난다. 그 값은 모듈 제조사가 이 패널에 맞춰 넣은 것이고, 덮어쓸 근거가 없다 |

## 아직 확인 못 한 것 — 남아 있는 것

- 🔴 **백라이트(PB6)의 극성.** High = 켜짐으로 **가정**하고 짰다. J25.8 은
  MCU 핀이 커넥터로 바로 나가고, 그 너머 모듈 안에서 무엇이 받는지는 모듈
  제조사 자료가 있어야 안다. 화면이 검게만 나오면 여기부터 뒤집어 본다
  (`bsp/mk_lcd_io.c` 의 `io_backlight()`).
- 🔴 **컬러필터가 RGB 인가 BGR 인가.** 데이터시트로 못 정한다 — WS2812 의
  색 순서와 같은 종류의 사실이다. 1단계가 주황(255,128,0)으로 채우는 이유가
  이것이다: **파랑 계열로 보이면 BGR** 이고, 그때는 MADCTL(36h)의 D3(BGR)을
  세운다(p.192 §5.2.30). 회색·흰색으로 채우면 이것을 못 가린다.
- LCD 모듈의 실제 IM[2:0] 결선(4선 직렬인지) — 모듈 제조사 자료 필요.
  p.121 §4.7.2 는 4선/8비트 직렬이 `IM[2:0] = 111` 일 때라고 적는다. J25 에
  IM 핀이 없으므로 모듈 안에서 고정돼 있을 것이나 **근거는 아직 없다.**
- XPT2046 의 클럭 상한과 SPI 모드 (터치를 붙일 때).
