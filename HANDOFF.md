# HANDOFF — MarkON Studio

> 2026-08-17 시점. 다음 사람(또는 다음 세션의 나)이 읽고 바로 이어서 일할 수 있게.
> `CLAUDE.md`가 **규칙**이라면 이 문서는 **지금 상태**다. 규칙은 그쪽을 본다.

---

## 1. 한 줄 요약

STM32 v2.0 보드가 **USB로 GUI에 붙어 설정되고, 빼면 저장된 대로 혼자 돈다.**
7채널 4–20 mA 수집과 전원 레일 3종 제어가 실물에서 동작하고,
GUI는 계기판이 됐다 — 값·이력·공유 커서·제어 모드.

---

## 2. 지금 무엇이 되는가

실기기(COM23)에서 **확인된 것**만 적는다. 코드가 있어도 실물로 안 본 것은 §3에 있다.

| 기능 | 상태 | 어떻게 확인했나 |
|---|---|---|
| GUI·CLI로 설정 읽기·쓰기 | ✅ | `markon_cli --port COM23 list` |
| 저장 → 리셋 후 복원 | ✅ | `tools/verify_persist_rails.py` — 24V 켜고 저장, 리셋해도 살아 있음 |
| 전원 레일 3종 제어 | ✅ | 순차 기동·즉시 차단·5V 포함. `$STAT`과 GPIOD ODR이 일치 |
| ADS1256 7채널 수집 | ✅ | DRDY EXTI → SPI4 DMA → 채널별 큐. 타임스탬프 100 ms 간격 |
| `$STAT`이 실제 핀을 말함 | ✅ | `rails{v24,v14v9,v5}=true` ↔ `GPIOD ODR = 0x700` |
| 굽기 | ✅ | `tools/flash_verified.py` — 되읽어 바이트 비교, 틀리면 재시도 |

**시뮬레이터에서만 확인된 것** (실기기 미검증 — §3):
설정 항목 86개(디지털 출력·LED·I2C 포함), 제어 모드 TEST/ACTIVE, 계기판 GUI 전체.

### 되는 것을 직접 해 보려면

```bash
# 보드 없이 (시뮬레이터)
python -m host.gui.app --port sim
python -m tools.cli.markon_cli list

# 실물 보드
python -m host.gui.app --port COM23

# 굽기 — 반드시 이것으로. 이유는 §5
cd firmware/stage1 && python tools/flash_verified.py

# 시험 (보드 불필요)
python -m pytest -q                                   # 473 passed
powershell -File firmware/stage1/tests/run_tests.ps1  # C 10묶음 + 대조 6종
```

---

## 3. 안 되는 것 / 확인 안 된 것

**막힌 것은 없다.** 아래는 "아직 안 만들었다"거나 "확인할 방법이 없었다"다.

| | 상태 |
|---|---|
| **텔레메트리 송신** | 🔴 **다음 작업.** 큐를 비우는 쪽이 없어 `$STAT`의 `drops`가 계속 오른다 |
| **sol·led·i2c 실동작** | 설정 항목과 카탈로그만 있다. **핀을 움직이는 코드가 없다** — §7.2 |
| **WS2812 드라이버** | 미착수. 배선·타이밍 근거는 §6 |
| 제어 모드 실기기 | **[미검증]** 시뮬레이터와 C 단위시험에서만 확인 |
| 값 정확도 | **[미검증]** 4–20 mA 루프에 아무것도 안 물려 있다 |
| SPI 분주비 | **[미확인]** fCLKIN/4 = 1.92 MHz 한도 안인지 따로 봐야 한다 |
| 레일 실제 전압 | **[미검증]** 피드백 회로가 없어 보드도 모른다 |
| GNSS·PPS·I2C 실동작 | 미착수 (계획서 Phase 3·5) |
| 워커 한 바퀴 200 ms | 보드는 100 ms로 보내는데 화면의 `표본 간격`이 202 ms다. 원인 미확인 |

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

🔴 **방향은 한쪽이다.** `bsp/`가 `app/`을 include하는 것은 되고 반대는 안 된다.
Flash staging 버퍼가 그 예다 — 필요한 크기를 `app/mk_config.h`의
`MK_CFG_BLOB_MAX`가 선언하고 `bsp/mk_flash.c`가 그것을 쓴다.

### 호스트 — `host/`

```
core/     framing · records · config_schema · scaling · limits · errors
service/  board_service (연결·명령응답·유실집계)
gui/
  screen.py       🔴 화면이 무엇을 보여야 하는가. Qt를 import하지 않는다
  settings_form.py  표 접기·전송 모양·채널 범위. 역시 Qt 없음
  field_budget.py   필드 선택이 전선에 얼마를 쓰는가
  qt/view.py      뷰 계약 — render(state) 하나
  qt/*.py         어떻게 그리는가
  app.py          배치 + 배선
```

**UI를 바꿀 때 어디를 건드리나:**

| 하고 싶은 것 | 고칠 곳 |
|---|---|
| 배치만 바꾼다 | `app.py`의 레이아웃 + `_views` 목록. **`_on_step`은 안 건드린다** |
| 뷰를 새로 만든다 | `render(state)` 하나만 구현하면 데이터가 알아서 온다 |
| 보여줄 것 추가 | `ScreenState`에 필드 + `build_screen`에서 채움 |
| **설정 항목 추가** | **보드(시뮬레이터+펌웨어)에만 넣는다.** 화면은 카탈로그를 보고 저절로 그린다 |

마지막 줄이 실제로 증명됐다 — `sol`·`led`·`i2c` 41항목을 넣었더니
호스트 코드는 그룹 이름표와 표 접기 규칙만 고치고 탭 셋이 생겼다.

---

## 5. 🔴 밟으면 시간을 크게 버리는 함정

전부 **실제로 밟아 본 것**이다. 순서는 아픈 순.

### 5.1 굽기가 조용히 실패한다 — `compare-sections`를 믿지 마라

모든 섹션이 `matched`로 나왔는데 플래시를 되읽어 보니 **마지막 1,439바이트가
통째로 `0xff`** 였다. 하필 거기가 `.init_array`라 부팅 즉시 HardFault.
증상은 **"보드가 조용하다"** 뿐이다.

→ **항상 `tools/flash_verified.py`로 굽는다.** `flash.gdb`를 직접 쓰지 않는다.

### 5.2 DTCM에 DMA 버퍼를 두면 전송이 조용히 안 된다

`static uint8_t rx[3];`는 DTCM에 잡히는데 DMA1·DMA2는 거기 닿지 못한다
(RM0468 p.140, p.106). 컴파일·링크 다 통과하고 경고도 없다.
→ `bsp/mk_dma_mem.h`의 `MK_DMA_BUF`. 잊어도 `tools/check_dma_placement.py`가 막는다.

### 5.3 D2 SRAM 클럭이 리셋 직후 꺼져 있다

→ `__HAL_RCC_D2SRAM1_CLK_ENABLE()` / `D2SRAM2`.

### 5.4 STM32H7의 SPI는 DMA 완료가 아니라 자기 EOT로 알린다

DMA는 다 옮겨 놓고도 SPI4_IRQn을 안 걸면 완료 콜백이 안 온다.

### 5.5 DTR/RTS를 세운 채 포트를 열면 보드가 멈춘다

`.NET SerialPort`는 열 때 둘 다 세운다 — **쓰지 않는다.**
pyserial을 쓰되 `s.dtr = False; s.rts = False`를 **열기 전에** 설정한다.
멈췄으면 굽지 말고 GDB로 리셋한다 (`tools/reset.gdb`).

### 5.6 ADS1256의 아날로그 전원은 5V 레일이다

PD10이 Low면 **SPI 레지스터는 정상 응답하는데 변환이 안 되고 DRDY가
영영 안 떨어진다.** 채널마다 타임아웃만 쌓이면 배선이 아니라 레일부터 본다.

### 5.7 Qt — 위젯이 창 바탕을 칠한다

- 전역 `QWidget { background }`가 **모든** 위젯에 걸린다. `QLabel`·`QCheckBox`가
  흰 카드 위에서 각자 회색 사각형을 찍었다 — 설정 화면 오른쪽 절반이
  회색 띠밭이던 원인. `theme.py`에서 한 번에 막았다
- 순수 `QWidget`은 스타일시트 배경을 **스스로 그리지 않는다.** `QFrame` +
  `WA_StyledBackground`
- 인라인 `setStyleSheet`는 부모 규칙을 **통째로 덮는다**
- `QFrame.HLine`은 두꺼운 띠로 나온다. `qt/parts.hairline()`을 쓴다
- `QLabel`은 마크다운을 렌더링하지 않는다
- **공백 없는 긴 문자열은 `setWordWrap`으로 안 접힌다.** JSON 미리보기가
  카드 밖으로 삐져나갔다 — 쉼표 뒤에 폭 없는 공백(`\u200b`)을 넣는다
- `cv.height = …`처럼 **속성에 대입하면 원본이 덮인다.** 그 값을 매 프레임
  원본으로 다시 읽으면 캔버스가 배씩 자란다 (Artifact에서 겪음)

### 5.8 PowerShell로 소스를 치환하지 마라

`.Replace()`가 CRLF 때문에 **여섯 번** 조용히 실패했고, 나중에는
`Set-Content -Encoding utf8`이 한글 파일을 통째로 깨뜨렸다.
→ **`Edit` 도구를 쓴다.** 여러 파일을 한 번에 고쳐야 하면 파이썬 스크립트로.

---

## 6. 하드웨어 사실 (넷리스트·데이터시트로 확인한 것)

`docs/datasheet/STM32_v2.0_DATASHEET.md`는 2차 자료다. 하드웨어 판단은
`D:\STM32_PCB_Board\output\datasheet_src\STM32_v2.0_current.net`을 본다.

```
ADS1256 (U9)   PE11 CS · PE12 SCLK · PE13 DOUT · PE14 DIN · PE15 DRDY
               PE9 SYNC/PDWN · PE10 RESET
               전원: AVDD·VREFP = V5 레일 / DVDD = V3V3 상시

전원 레일      PD8 = 24V · PD9 = 14.9V · PD10 = 5V · PD11 = 상태 LED

디지털 출력    J18 = PA4 · J19 = PA5 · J20 = PA6   (§5.7)
               🔴 MCU GPIO 직결 — 버퍼도 직렬저항도 클램프도 없다.
                  핀당 20 mA 상한이라 밸브 직접 구동 불가, 외부 옵토 필수
               🔴 PA4·PA5는 3.3V 전용(절대최대 4.0V). PA6만 5V 허용
               방향은 **출력**으로 확정. 보드는 3채널, 하우징은 2채널

WS2812 (J21~J24)  PA7 → R84(390Ω) → WS_D1 → J21 pin1
               데이지체인 J21→J22→J23→J24, J24 Dout 미연결
               전원 pin2 = V5 → **PD10을 올려야 켜진다**
               J21부터 순서대로 안 채우면 뒤쪽이 죽는다

I2C            I2C3 = J10/J11 · I2C5 = J12/J13 · I2C1 = J14/J15
               🔴 짝 커넥터는 **같은 버스**다 — 같은 주소면 충돌
               풀업 4.7 kΩ ×2 온보드(R25~R30). JP1~JP3로 3.3V/5V 선택
```

**ADS1256 정착시간** — `ADS1256.pdf, p.20, Table 13`. 표 16행 전부가
`t18 = 1/DRATE + 0.18 ms`에 맞는다. 60 SPS → 16.84 ms, 7채널 한 바퀴 약 118 ms.
**DMA로 못 줄인다** — ΔΣ 필터의 물리다. 주기를 줄이려면 DRATE를 올린다.

---

## 7. 다음에 할 일

### 7.1 텔레메트리 송신 (다음 작업)

큐를 비우는 쪽이 없어 지금 `drops`가 계속 오른다. `mk_queue_pop`으로 꺼내
NDJSON `ain` 레코드로 만들어 UART로 내보내면 된다. 필드 마스크(`tx.fields`)와
전송 주기(`tx.period_ms`)가 이미 설정에 있고 시뮬레이터에 참조 구현이 있다.

호스트 쪽은 이미 받을 준비가 돼 있다 — 보내기만 하면 화면에 뜬다.

### 7.2 🔴 sol·led·i2c는 설정만 있고 **핀을 안 움직인다**

카탈로그·검증·저장·제어 모드까지 다 되는데, 값이 실제 GPIO로 나가지 않는다.
`mk_railctl`이 `pwr.*`에 대해 하는 일을 나머지에도 해야 한다.

- **sol** (쉽다): `bsp/mk_sol.c`로 PA4·PA5·PA6를 GPIO 출력으로. 순차 기동 불필요
- **led** (어렵다): WS2812는 TIM PWM + DMA 800 kHz다. Q2에 참고 구현이 있다
  (`LaneControlSystemQ2/.../ws2812b.{c,h}` — period 79 / bit0 CCR 22 /
  bit1 CCR 45 / reset 48슬롯). **그대로 못 쓴다:**
  - Q2는 TIM2(32비트)라 WORD 정렬 필수. PA7의 타이머 폭을 확인해 정렬을 맞춘다
  - Q2는 **RGB 순서**라고 명시. WS2812B는 보통 GRB — 실물로 확인
  - PA7의 타이머 AF 매핑을 STM32H723 데이터시트로 확인 (추정 금지)
  - DMA 버퍼는 `MK_DMA_BUF`로 D2에 (§5.2)
- **i2c**: Phase 5. 드라이버 플러그인 구조부터

### 7.3 대시보드 `출력` 구획

지금 sol·led는 설정 탭에서만 만진다. 전원 레일처럼 대시보드에 두되
**제어 모드에 따라 다르게** 굴어야 한다 — TEST면 누르고 있기(손 떼면 원위치),
ACTIVE면 유지되는 명령.

### 7.4 남겨 둔 작은 것들

- `check_dma_placement.py`가 이름 규칙 기반이라 불완전하다 (Codex 지적, 수용)
- `theme.py`가 토큰과 Qt 스타일시트를 같이 들고 있다
- `BoardService.records`가 무한히 자란다
- `LoopReading.broken=True`가 "데이터 없음"과 "물리적 단선"을 뭉갠다
- 워커 한 바퀴가 200 ms 걸린다 (§3)

---

## 8. 작업 방식 — 값을 한 것

### 되돌림 검사 (revert-check)

**고칠 때마다, 가드를 빼면 시험이 실제로 깨지는지 확인한다.** 이것으로 찾은 것:

- 라운드로빈 시험이 **헛돌고 있었다** — 굶주림을 막는 것은 정렬이 아니었다
- DMA 검사 도구가 **아무것도 못 잡고 있었다** (`--gc-sections`가 프로브를 걷어감)
- `$STAT` 버퍼가 7채널을 못 담았다
- `MATRIX_MIN_ROWS` 가드를 빼니 2행짜리 표가 생겼다 — 시험이 문다

### 대조 (crosscheck)

C 펌웨어와 Python 시뮬레이터가 같은 입력에 같게 답하는지 6가지로 대조한다.
**`crosscheck_cfgtable.py`가 가장 값지다** — 두 카탈로그가 어긋나면 GUI는
시뮬레이터에서 멀쩡하다가 보드에서만 틀어진다.

실제로 잡았다: `i2c*.addr`의 하한을 펌웨어에서 0으로 고치고 시뮬레이터를
안 고쳤는데, 대조가 `minimum: 펌웨어=0 시뮬레이터=8`로 짚어 줬다.

**손으로만 돌리면 썩는다.** 지금은 `run_tests.ps1`에 매달려 있다.

### 시험이 개수를 하드코딩하지 않게

`assert len(form.keys()) == 45`가 세 군데 있었다. 하드코딩을 금지하는
시험이 스스로 목록 길이를 하드코딩한 셈이라, 보드에 항목이 늘 때마다 깨졌다.
지금은 카탈로그가 말하는 것과 대조한다.

### 근거 (CLAUDE.md §5)

부품 사양·회로·핀맵은 **추정하지 않는다.** 이번에 확정한 것: ADS1256
정착시간, DTCM 접근 제약, ADS1256 전원 경로, WS2812 배선, SPI4 배선,
디지털 출력의 구동 한계와 전압 내성, I2C 버스 짝짓기.

---

## 9. 참고

- `CLAUDE.md` — 규칙. 설계 원칙 4가지, 하드웨어 제약, git 규칙
- `protocol/specification.md` — 호스트·펌웨어 공통 계약 (schema_ver 3)
  - §6.4 제어 모드 TEST/ACTIVE · §7.2.1 물리량 환산식 — **둘 다 펌웨어가 지켜야 한다**
- `docs/status/PROJECT_STATUS.md` — Codex 외부 감사 (내가 고치지 않는다)
- `firmware/stage1/tools/README.md` — 굽기·복구 절차
- `D:\STM32_PCB_Board` — KiCad 원본·넷리스트·부품 데이터시트 (읽기 전용)
- `LaneControlSystem` Q1/Q2 — 선행 펌웨어. **둘 다 H7A3용이라 바이너리·
  CubeMX 재사용 금지.** HAL 비의존 모듈의 로직만 본다
