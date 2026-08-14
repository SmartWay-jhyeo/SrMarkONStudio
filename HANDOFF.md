# HANDOFF — MarkON Studio

> 2026-08-14 시점. 다음 사람(또는 다음 세션의 나)이 읽고 바로 이어서 일할 수 있게.
> `CLAUDE.md`가 **규칙**이라면 이 문서는 **지금 상태**다. 규칙은 그쪽을 본다.

---

## 1. 한 줄 요약

STM32 v2.0 보드가 **USB로 GUI에 붙어 설정되고, 빼면 저장된 대로 혼자 돈다.**
7채널 4–20 mA 수집과 전원 레일 3종 제어가 실물에서 동작한다.

---

## 2. 지금 무엇이 되는가

실기기(COM23)에서 **확인된 것**만 적는다. 코드가 있어도 실물로 안 본 것은 §3에 있다.

| 기능 | 상태 | 어떻게 확인했나 |
|---|---|---|
| GUI·CLI로 설정 읽기·쓰기 | ✅ | `markon_cli --port COM23 list` → 45항목 |
| 저장 → 리셋 후 복원 | ✅ | `tools/verify_persist_rails.py` — 24V 켜고 저장, 리셋해도 살아 있음 |
| 전원 레일 3종 제어 | ✅ | 순차 기동·즉시 차단·5V 포함. `$STAT`과 GPIOD ODR이 일치 |
| ADS1256 7채널 수집 | ✅ | DRDY EXTI → SPI4 DMA → 채널별 큐. 타임스탬프 정확히 100 ms 간격 |
| `$STAT`이 실제 핀을 말함 | ✅ | `rails{v24,v14v9,v5}=true` ↔ `GPIOD ODR = 0x700` |
| 굽기 | ✅ | `tools/flash_verified.py` — 되읽어 바이트 비교, 틀리면 재시도 |

### 되는 것을 직접 해 보려면

```bash
# 보드 없이 (시뮬레이터)
python -m host.gui.app --port sim
python -m tools.cli.markon_cli list

# 실물 보드
python -m host.gui.app --port COM23
python -m tools.cli.markon_cli --port COM23 list

# 굽기 — 반드시 이것으로. 이유는 §5
cd firmware/stage1 && python tools/flash_verified.py

# 시험 (보드 불필요)
python -m pytest -q                                  # 399 passed
powershell -File firmware/stage1/tests/run_tests.ps1  # C 10묶음 + 대조 6종
```

---

## 3. 안 되는 것 / 확인 안 된 것

**막힌 것은 없다.** 아래는 "아직 안 만들었다"거나 "확인할 방법이 없었다"다.

| | 상태 |
|---|---|
| **텔레메트리 송신** | 🔴 **다음 작업.** 큐를 비우는 쪽이 없어 `$STAT`의 `drops`가 계속 오른다. 설계대로 오래된 것부터 버리는 중이고, 값이 호스트로 안 나간다 |
| WS2812 (J21~J24) | 미착수. 배선은 확인됨 — §6 |
| 값 정확도 | **[미검증]** 4–20 mA 루프에 아무것도 안 물려 있어 입력이 떠 있다. 기준 입력을 넣어야 안다 |
| SPI 분주비 | **[미확인]** 도는 것은 봤지만 SPI4 커널 클럭을 재서 fCLKIN/4 = 1.92 MHz 한도 안인지 따로 봐야 한다 |
| 레일 실제 전압 | **[미검증]** 피드백 회로가 없어 보드도 모른다. 멀티미터를 대야 한다 |
| GNSS·PPS·I2C | 미착수 (계획서 Phase 3·5) |

---

## 4. 코드가 어떻게 나뉘어 있나

### 펌웨어 — `firmware/stage1/`

```
app/     HAL 비의존. 보드 없이 호스트에서 컴파일해 시험한다 (10개 모듈)
  mk_framing   mk_json    mk_hostlink  mk_config   mk_cfgwire
  mk_cfgtable  mk_crc     mk_queue     mk_ads1256  mk_railctl
bsp/     HAL이 나오는 유일한 층
  mk_uart  mk_flash  mk_time  mk_ads_io  mk_rails  stm32h7xx_it
main.c   슈퍼루프
```

**이 경계가 이 프로젝트의 전제다.** `app/`이 HAL을 import하지 않기 때문에
호스트에서 그대로 컴파일되고, Python 시뮬레이터와 바이트로 대조할 수 있다.
`test_firmware_safety.py`가 그 경계를 강제한다.

### 호스트 — `host/`

```
core/     framing · records · config_schema · limits · errors
service/  board_service (연결·명령응답·유실집계)
gui/
  screen.py    🔴 화면이 무엇을 보여야 하는가. Qt를 import하지 않는다
  qt/view.py   뷰 계약 — render(state) 하나
  qt/*.py      어떻게 그리는가
  app.py       배치 + 배선
```

**UI를 바꿀 때 어디를 건드리나:**

| 하고 싶은 것 | 고칠 곳 |
|---|---|
| 배치만 바꾼다 | `app.py`의 레이아웃 + `_views` 목록. **`_on_step`은 안 건드린다** |
| 뷰를 새로 만든다 | `render(state)` 하나만 구현하면 데이터가 알아서 온다 |
| 보여줄 것 추가 | `ScreenState`에 필드 + `build_screen`에서 채움 |

효과가 실제로 나왔다 — 레일을 화면 아래에서 왼쪽으로 옮기면서 위젯 이름이
전부 바뀌었는데, 계약 시험(`test_screen.py`)은 하나도 안 바뀌었다.

---

## 5. 🔴 밟으면 시간을 크게 버리는 함정

전부 **이번에 실제로 밟아 본 것**이다. 순서는 아픈 순.

### 5.1 굽기가 조용히 실패한다 — `compare-sections`를 믿지 마라

모든 섹션이 `matched`로 나왔는데 플래시를 되읽어 보니 **마지막 1,439바이트가
통째로 `0xff`** 였다. 하필 거기가 `.init_array`라 `__libc_init_array`가
`0xfffffffe`를 함수 포인터로 불러 부팅 즉시 HardFault(`CFSR=0x01000001`).

증상은 **"보드가 조용하다"** 뿐이다. 굽기는 성공했다고 말한다. UART 배선과
BMP 브리지를 한참 의심했다.

→ **항상 `tools/flash_verified.py`로 굽는다.** 되읽어 바이트 비교하고 틀리면
재시도한다. `flash.gdb`를 직접 쓰지 않는다.

### 5.2 DTCM에 DMA 버퍼를 두면 전송이 조용히 안 된다

`static uint8_t rx[3];`는 DTCM(0x2000_0000)에 잡히는데 DMA1·DMA2는 거기
닿지 못한다(RM0468 p.140, p.106). 컴파일·링크 다 통과하고 경고도 없다.

→ `bsp/mk_dma_mem.h`의 `MK_DMA_BUF`를 붙인다. 잊어도
`tools/check_dma_placement.py`가 링크 후 실제 주소를 보고 빌드를 세운다.

### 5.3 D2 SRAM 클럭이 리셋 직후 꺼져 있다

DTCM을 피해 D2(0x3000_0000)로 옮겨 놓고도 `AHB2ENR`의 SRAM1EN·SRAM2EN이
0이면 결국 같은 자리다. 실기기에서 `AHB2ENR = 0x00000000`이었다.

→ `__HAL_RCC_D2SRAM1_CLK_ENABLE()` / `D2SRAM2`.

### 5.4 STM32H7의 SPI는 DMA 완료가 아니라 자기 EOT로 알린다

DMA1_Stream0/1은 걸었는데 SPI4_IRQn을 안 걸어서, DMA는 다 옮겨 놓고도
완료 콜백이 안 왔다. 실기기 상태: `SPI4 SR=0x101a`(EOT 섬), DMA `EN=0 NDTR=0`,
그런데 `s_spi.State = 5(BUSY_TX_RX)`. 상태머신이 SETUP에 갇혀 타임아웃만 쌓았다.

### 5.5 DTR/RTS를 세운 채 포트를 열면 보드가 멈춘다

`.NET SerialPort`는 열 때 둘 다 세운다 — **쓰지 않는다.**
pyserial을 쓰되 `s.dtr = False; s.rts = False`를 **열기 전에** 설정한다.
`serial.Serial("COM23", ...)` 한 줄 생성자는 열면서 세우므로 안 된다.

멈췄으면 굽지 말고 GDB로 리셋한다 (`tools/reset.gdb`).

### 5.6 ADS1256의 아날로그 전원은 5V 레일이다

넷리스트: `V5 → FB1 → AVDD5 → U9 pin1(AVDD)`, 그리고 같은 AVDD5가
`U10(VIN) → VREF2V5 → U9 pin4(VREFP)`. 디지털(DVDD pin16)만 3.3V 상시.

→ PD10이 Low면 **SPI 레지스터는 정상 응답하는데 변환이 안 되고 DRDY가
영영 안 떨어진다.** 채널마다 타임아웃만 쌓이면 배선이 아니라 레일부터 본다.

### 5.7 Qt — 어두운 면 위의 위젯

- 순수 `QWidget`은 스타일시트 배경을 **스스로 그리지 않는다.** `QFrame` +
  `WA_StyledBackground`.
- 인라인 `setStyleSheet`는 부모 규칙을 **통째로 덮는다.** 배경을 안 적으면
  전역 `QWidget { background: GROUND }`가 살아나 흰 상자가 찍힌다.
- `QFrame.HLine`은 어두운 면에서 두꺼운 밝은 띠로 나온다. 1px 위젯을 직접.
- `QLabel`은 마크다운을 렌더링하지 않는다. `**...**`가 별표째 찍힌다.

### 5.8 PowerShell로 소스를 치환하지 마라

이번 세션에서 `.Replace()`가 CRLF 때문에 **여섯 번** 조용히 실패했다.
치환이 안 됐는데 성공한 것처럼 넘어가서, 나중에 빌드가 깨지고서야 알았다.
→ `Edit` 도구를 쓴다.

---

## 6. 하드웨어 사실 (넷리스트로 확인한 것)

`docs/datasheet/STM32_v2.0_DATASHEET.md`는 2차 자료다. 하드웨어 판단은
`D:\STM32_PCB_Board\output\datasheet_src\STM32_v2.0_current.net`을 본다.

```
ADS1256 (U9)
  PE11 CS · PE12 SCLK · PE13 DOUT · PE14 DIN · PE15 DRDY   (넷 이름이 SPI4_*)
  PE9 SYNC/PDWN · PE10 RESET
  전원: AVDD·VREFP = V5 레일 / DVDD = V3V3 상시

전원 레일
  PD8 = 24V · PD9 = 14.9V · PD10 = 5V · PD11 = 상태 LED

WS2812 (J21~J24)
  U5 pin43 (PA7) → R84 → WS_D1 → J21 pin1
  데이지체인: J21 pin4 → J22 pin1 → J23 → J24, J24 pin4 미연결
  전원: pin2 = V5, pin3 = GND        ← 5V 레일 필요

I2C
  I2C1 = J14/J15 · I2C3 = J10/J11 · I2C5 = J12/J13
```

**ADS1256 정착시간** — `ADS1256.pdf, p.20, Table 13`. 표 16행 전부가
`t18 = 1/DRATE + 0.18 ms`에 맞는다. 60 SPS → 16.84 ms.
그래서 7채널 한 바퀴가 약 118 ms다. **DMA로 못 줄인다** — ΔΣ 필터의 물리다.
DMA가 하는 일은 그동안 CPU를 풀어 주는 것이고, 주기를 줄이려면 DRATE를 올린다.

---

## 7. 다음에 할 일

### 7.1 텔레메트리 송신 (다음 작업)

큐를 비우는 쪽이 없어 지금 `drops`가 계속 오른다. `mk_queue_pop`으로 꺼내
NDJSON `ain` 레코드로 만들어 UART로 내보내면 된다. 필드 마스크(`tx.fields`)와
전송 주기(`tx.period_ms`)가 이미 설정에 있고 시뮬레이터에 참조 구현이 있다
(`tools/simulator/telemetry.py`).

호스트 쪽은 이미 받을 준비가 돼 있다 — `build_channels()`가 `ain` 레코드를
읽어 게이지로 옮긴다. 보내기만 하면 화면에 뜬다.

### 7.2 WS2812 (§6에 배선)

Q2에 참고 구현이 있다: `LaneControlSystemQ2/STM32Code/App/Drivers/ws2812b.{c,h}`
— TIM PWM + DMA 800 kHz, period 79 / bit0 CCR 22 / bit1 CCR 45 / reset 48슬롯.

**그대로 못 쓴다:**
- Q2는 TIM2(32비트)라 WORD 정렬 필수라고 헤더에 경고. PA7의 타이머 폭을
  확인하고 정렬을 맞춰야 한다(16비트면 HALFWORD).
- Q2는 **RGB 순서**라고 명시. WS2812B는 보통 GRB — 실물로 확인.
- PA7의 타이머 AF 매핑을 STM32H723 데이터시트로 확인할 것 (추정 금지).
- DMA 버퍼는 `MK_DMA_BUF`로 D2에.

### 7.3 남겨 둔 작은 것들

- `check_dma_placement.py`가 이름 규칙 기반이라 불완전하다(Codex 지적, 수용).
  제대로 된 해결은 전송을 거는 지점에서 실제 주소를 보는 것.
- `theme.py`가 토큰과 Qt 스타일시트를 같이 들고 있다. 나누는 편이 맞다.
- 채널 카드 높이가 줄마다 다르다. `$STAT`의 큐 깊이·유실을 화면 오른쪽
  아래 빈 자리에 넣으면 좋겠다.
- `BoardService.records`가 무한히 자란다.
- `LoopReading.broken=True`가 "데이터 없음"과 "물리적 단선"을 뭉갠다 (Codex [낮음]).

---

## 8. 작업 방식 — 이번에 값을 한 것

### 되돌림 검사 (revert-check)

**고칠 때마다, 가드를 빼면 시험이 실제로 깨지는지 확인한다.** 이번에 이것으로
찾은 것:

- 라운드로빈 시험이 **헛돌고 있었다.** 정렬 논리를 지워도 안 깨져서
  확인해 보니 굶주림을 막는 것은 정렬이 아니라 따라잡기 포기였다 — 내 주석이
  과장이었다.
- DMA 검사 도구가 **아무것도 못 잡고 있었다.** `--gc-sections`가 참조 없는
  프로브를 걷어가서 시험이 무의미했고, 고치자 진짜 결함이 나왔다(`static`
  변수는 맵에 심볼 줄로 안 나오고 구역 이름으로만 나오며, 그 줄이 접힌다).
- `$STAT` 버퍼가 7채널을 못 담았다. 사용자가 채널을 다 켜면 진단 창구가
  닫히는 상태였다.

### 대조 (crosscheck)

C 펌웨어와 Python 시뮬레이터가 같은 입력에 같게 답하는지 6가지로 대조한다.
GUI가 시뮬레이터를 상대로 개발되고 실물에서 돌아야 하기 때문이다.

**손으로만 돌리면 썩는다.** `crosscheck_hostlink.py`가 "$CFG는 1단계
미구현"이라고 적힌 채 구현 뒤에도 한참을 통과했다. 지금은
`run_tests.ps1`에 매달려 있다.

### 근거 (CLAUDE.md §5)

부품 사양·회로·핀맵은 **추정하지 않는다.** 이번에 데이터시트/넷리스트로
확정한 것: ADS1256 정착시간, DTCM 접근 제약, ADS1256 전원 경로, WS2812 배선,
SPI4 배선. 전부 §5·§6에 인용과 함께 있다.

---

## 9. 참고

- `CLAUDE.md` — 규칙. 설계 원칙 4가지, 하드웨어 제약, git 규칙
- `protocol/specification.md` — 호스트·펌웨어 공통 계약 (schema_ver 3)
- `docs/status/PROJECT_STATUS.md` — Codex 외부 감사 (내가 고치지 않는다)
- `firmware/stage1/tools/README.md` — 굽기·복구 절차
- `D:\STM32_PCB_Board` — KiCad 원본·넷리스트·부품 데이터시트 (읽기 전용)
- `LaneControlSystem` Q1/Q2 — 선행 펌웨어. **둘 다 H7A3용이라 바이너리·
  CubeMX 재사용 금지.** HAL 비의존 모듈의 로직만 본다
