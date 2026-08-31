# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## 0. 이 저장소의 현재 상태

**계획 1(프로토콜·시뮬레이터·CLI) 완료, 계획 2(펌웨어) 1단계 진행 중, 계획 3(GUI) 완료.**
원격 저장소는 없다 — 로컬 전용.

**지금 상태와 다음 작업은 `HANDOFF.md`를 본다.** 이 파일은 규칙이고 그쪽이 현황이다.

```
MarkON_Studio/
├─ CLAUDE.md  HANDOFF.md  .gitignore  pyproject.toml
├─ protocol/specification.md   ← 펌웨어·호스트 공통 계약 (v3)
├─ firmware/stage1/            ← H723 펌웨어. app/(HAL 비의존) + bsp/
├─ host/
│  ├─ core/      framing · records · config_schema · scaling · errors
│  ├─ service/   board_service (연결·명령응답·유실집계)
│  ├─ gui/       screen · settings_form · field_budget · theme · qt/*
│  └─ tests/     879개 시험 + fake_board 스텁 — 보드도 디스플레이도 불필요
├─ tools/cli/     markon_cli
└─ docs/         ← 🔴 git 추적 제외. 로컬에만 있다
   ├─ STM32_V2_CONTROL_DAQ_DEVELOPMENT_PLAN.md   개발 계획
   ├─ superpowers/specs/   설계 스펙
   ├─ superpowers/plans/   구현 계획 (계획 1 = protocol-simulator-cli, 계획 3 = gui)
   ├─ status/              Codex 감사 결과 (§6)
   └─ datasheet/           대상 하드웨어 근거 문서
```

**시험은 보드 없이 돈다, 실행은 보드가 필요하다:**
```bash
python -m pytest -q                                  # 879 passed (보드 불필요)
python -m tools.cli.markon_cli --port COM23 list     # 실물 보드 — --port 필수
```

🔴 **시뮬레이터는 없다** (2026-08-20 사용자 결정으로 통째로 삭제). 보드가 상시
연결되면서 유지 비용이 값을 넘었다. `--port sim` 은 거절된다. 호스트 시험은
`host/tests/fake_board.py`(최소 스텁, ~560줄) + `catalog_snapshot.jsonl` 로 돈다 —
🔴 **스텁에 기능을 다시 키우지 마라.** 그게 시뮬레이터가 됐던 경위다.

### `host/gui/` 의 원칙 — Qt 를 import 하지 않는 층이 있다

`theme`·`command_queue`·`widgets/*` 는 **PyQt6 를 import 하지 않는다.** 색 계산,
기하 계산, 상태 판정, 동시성 계약만 들어 있다. 덕분에 시각 언어와 스레드
규약이 위젯 코드보다 먼저 굳고, 시험이 디스플레이 없이 돈다.

Qt 위젯은 이 함수들을 **호출만** 한다. 새 화면을 만들 때 이 경계를 지킨다.

**남은 것**: 텔레메트리 송신(펌웨어), sol·led·i2c 를 실제 핀으로 내보내는 코드,
GNSS/PPS·I2C 드라이버. 자세한 것은 `HANDOFF.md` §3·§7.

### 🔴 설정 항목은 **보드에만** 넣는다

화면은 `$CFG,LIST` 카탈로그만 보고 그려진다. 항목을 늘릴 때 고치는 곳은
**펌웨어(`firmware/stage1/app/mk_cfgtable.c`) 하나뿐**이고, 호스트 코드는
건드리지 않는다.

실제로 그랬다 — 디지털 출력·LED·I2C 41항목을 넣었더니 GUI 는 그룹 이름표만
추가하고 탭 셋을 저절로 그렸다.

(시뮬레이터가 있던 시절에는 두 곳을 함께 고쳐야 했다. 이제 카탈로그의 출처는
보드 하나다 — 확인도 실물 카탈로그로 한다: `markon_cli --port COM23 list`.)

🔴 남아 있는 이중 정의 둘 — I2C **종류를 늘릴 때 셋을 함께** 고칠 것:
① **종류→물리량 표**: `host/gui/screen.py` ↔ 펌웨어 `mk_i2c.c` (자동 대조 없음)
② **종류→클라우드 타입 표**(2026-08-31 신설): `host/core/typemap.py` 의
KIND_CLOUD ↔ 펌웨어 `mk_cloud.c` 의 I2C_KIND_TYPES. typemap↔screen 은
`test_typemap` 이 대조하지만 펌웨어와의 대조는 없다.

### 🔴 `docs/`는 무시 대상이지 불필요한 파일이 아니다

저장소는 **소스 코드만 추적**하기로 했고(사용자 결정), `docs/`는 `.gitignore`에 들어 있다. 하지만 이 프로젝트의 **모든 핀맵·전기사양·굽기 절차·설계 원칙의 근거**가 거기 있다.

- `git clean`, `git status`의 untracked 정리, "안 쓰는 파일 같으니 지우자" 류 판단으로 **절대 삭제하지 않는다.**
- 버전관리 밖이라 **되돌릴 수 없다.** 문서를 고칠 때는 상위 저장소(`D:\STM32_PCB_Board`, §1)에 원본이 있는지 먼저 확인한다.
- 문서를 고쳐야 할 상황이면 그 사실을 사용자에게 알린다. 커밋으로 남지 않으므로 변경 이력은 문서 안의 개정 이력 표가 전부다.

작업 시작 전 **두 문서를 모두 읽는다.** 데이터시트가 "무엇이 있는가", 계획서가 "무엇을 만드는가"다.

### 🔴 이 보드의 데이터시트는 2차 자료다 — KiCad 원본이 진실이다

**사용자 확정 (2026-08-13).** 부품 제조사 데이터시트(STM32H723, ADS1256, RM0468 등)는
믿어도 되지만, **`docs/datasheet/STM32_v2.0_DATASHEET.md`는 사용자가 자기 보드를 보고
직접 쓴 문서라 틀릴 수 있다.**

하드웨어 사실이 걸린 판단은 `D:\STM32_PCB_Board`의 **KiCad 원본과 넷리스트로 직접
확인**한다. 넷리스트 파싱 예시는 이 파일의 이력에 남아 있고, 경로는 §1.1에 있다.

**실제로 틀린 것이 확인된 사례**: 데이터시트 §7.5는 "BMP가 만드는 시리얼 포트로는
H723과 통신할 수 없다(F103 USART1 = PB6/PB7 미연결)"고 단정한다. 그런데 실기기에서
**COM23으로 H723의 NDJSON이 그대로 나온다.** 넷리스트를 보면 하드웨어 경로는
처음부터 있었고(`VCP_TX` = H723 PB10 ↔ F103 PA3, `VCP_RX` = H723 PB11 ↔ F103 PA2),
이 보드의 BMP 펌웨어가 이미 USART2로 매핑돼 있는 것으로 보인다.

→ **BMP 재빌드 없이 USB 한 가닥으로 H723과 통신된다.** 계획서에 선행 조건으로
적어둔 BMP 재빌드는 필요 없다.

## 1. 참조 저장소 (여기 없는 원본이 있는 곳)

| 궁금한 것 | 볼 곳 |
|---|---|
| **PCB·회로·부품** (핀맵, 넷, 전원, 데이터시트 PDF) | `D:\STM32_PCB_Board` → §1.1 |
| **센서 수집 펌웨어** (선행 구현, 검증된 모듈) | `C:\Users\xovud\Desktop\Mac에 있던 것\LaneControlSystem\EmbeddedCode` 의 Q1·Q2 → §1.2 |

둘 다 **읽기 전용 참조**다. 신규 코드는 이 저장소에서 개발하고, 저쪽 파일을 수정하지 않는다.

### 1.1 하드웨어 원본 — `D:\STM32_PCB_Board`

이 저장소는 하드웨어 저장소 `D:\STM32_PCB_Board`에서 문서만 떼어 온 것이다. 다음은 **여기 없고 상위 저장소에만 있다.**

| 자료 | 상위 저장소 경로 |
|---|---|
| KiCad 원본 (.kicad_pcb, 스키매틱) | `hardware/kicad/STM32_v2.0/STM32_v2.0/` |
| 넷리스트 (데이터시트의 근거) | `output/datasheet_src/STM32_v2.0_current.net` |
| 참고 펌웨어 (BMP, rails_on, sensor_read, f103_diag) | `hardware/firmware/` |
| 부품 데이터시트 PDF | `datasheets/` |
| 회로 검토·블록도·BOM·LCD 추가 상세 | `docs/` |

- 데이터시트 본문의 `hardware/...`, `output/...`, `docs/...` 경로는 **전부 상위 저장소 기준**이다. 여기서 찾지 말 것.
- `docs/datasheet/_internal/*.py`는 `D:\STM32_PCB_Board\...`를 **하드코딩**하고 있다. 상위 저장소가 없으면 실행되지 않는다.
- **ADS1256 드라이버 참고 구현**은 여기 있다: `hardware/firmware/h723_sensor_read/ads1256.c` (PGA=1, DRATE=60SPS + 120Ω 션트). LaneControlSystem 쪽에는 ADS1256이 없다(§1.2).

### 1.2 선행 펌웨어 — `LaneControlSystem` Q1 / Q2

경로: `C:\Users\xovud\Desktop\Mac에 있던 것\LaneControlSystem\EmbeddedCode\` (공백·한글 포함 — 셸에서 반드시 인용부호로 감쌀 것)

**🔴 둘 다 타깃이 STM32H7A3ZITxQ(NUCLEO-H7A3ZI-Q)다.** MarkON Studio는 **STM32H723ZGT6**이므로 `.ioc`·`.ld`·CubeMX 설정·빌드 산출물을 **그대로 쓸 수 없다.** 클럭·메모리 도메인·핀·주변장치가 다르다. 가져오는 것은 **HAL 비의존 애플리케이션 모듈의 로직**뿐이다.

| | Q1 | Q2 |
|---|---|---|
| 성격 | RTK 측위 중심 원형 | **MarkON Studio의 직계 조상** |
| 센서 | UM982 GNSS(RTK) / MPU9250 IMU / TSL2591 조도 | 4~20mA 아날로그 / BH1750·BNO055·MLX90614 / 압력·유량 / UM982 |
| 고유 모듈 | `data_pipeline` `error_mgr` `session_mgr` | `sol_valve_mgr` `led_status` `ws2812b` `jetson_tx` `host_link` `submcu_comm` `markonsync_mgr` `analog_4_20ma` |
| 호스트 단위 테스트 | **있음** — `STM32Code/Tests/` (time_sync, serializer, um982, mpu9250, tsl2591, session_mgr) | 없음 |
| NDJSON | `schema_ver` 1 | `schema_ver` 2 |
| 문서 | 저장소 README | `HANDOFF.md`, `Docs/` |

**계층 구조 (양쪽 공통, 그대로 계승할 만한 패턴)**

```
Core/Src/main.c   슈퍼루프 오케스트레이터
   ↓
App/Modules/      애플리케이션 로직 (HAL 비의존 — 호스트에서 단위 테스트 가능)
   ↓
App/Drivers/      센서 드라이버 (물리량 변환, HAL 비의존)
   ↓
App/BSP/bsp_io.h  HAL 추상화 (함수 포인터)
   ↓
Drivers/          CubeMX HAL
```

계획서 §6의 펌웨어 모듈 분리는 이 구조의 확장판이다. **`App/Modules`와 `App/Drivers`를 HAL 비의존으로 유지하는 것이 호스트 단위 테스트의 전제**이므로, 신규 코드도 이 경계를 지킨다.

**가져올 때 주의**

- **`time_sync`(Q2)** — `int64_t epoch_ms` + 4단계 폴백(gnss / gnss_nmea / host_clock / device_clock). 계획서 §7.2의 6단계 등급으로 확장할 기반이지만, **PPS를 `HAL_GetTick()`으로 잡아 분해능이 1ms다.** 계획서 §7.3이 요구하는 타이머 Input Capture + 오버플로 확장 카운터로 **반드시 교체**해야 한다. 그대로 이식하면 안 된다.
- **`adc_manager`(Q2)는 ADS1256이 아니다.** STM32 내장 ADC1 3채널 DMA(Flow/Pressure×2)다. MarkON Studio의 7채널 ADS1256에는 쓸 수 없다 — §1.1의 `ads1256.c`를 볼 것.
- **`sol_valve_mgr`·`markonsync_mgr`(Q2)** — EXTI 엣지 캡처 + 링큐 + 디바운스 패턴. 계획서의 GPIO 이벤트 타임스탬프에 그대로 쓸 만하다. 단 **옵토 극성이 서로 반대**다(Sol은 HIGH=ON, MarkOnSync는 PC817이라 LOW=ON). 🔴 v2.0 보드의 J18~J20은 **입력**이다(2026-08-18 사용자 확정, 2026-08-19 J20 실증 — 데이터시트 §5.7 의 "출력" 단정이 틀렸다). MarkOnSync 처럼 로우 액티브로 읽는다.
- **`sensor_queue.h`** — 채널별 독립 링큐. 계획서 §9의 원형이다.
- **`JetsonCode/`(양쪽)** — Python 호스트 측 선행 구현(`uart_receiver` `spool_manager` `file_writer` `batch_builder` `mqtt_publisher` `ntrip_forwarder`). 계획서 §12의 Board Service 설계 시 참고.

**🔴 미해결 이슈 — 대역폭·유실 (Q2 `HANDOFF.md`, 2026-06-17)**

Q2에서 **STM32→Jetson UART 직결에 ~2% 유실 + ~0.4~1% 손상**이 실측됐다(2Mbps, seq 카운터로 정량화). 송신측·버퍼·주기·baud·stopbit·RX스레드 등 **8개 가설이 기각**됐고 물리계층(배선/신호)이 남은 의심으로 미해결 상태다. 통신 주기는 10ms→20ms로 되돌려 둔 상태.

→ 계획서 §20의 "데이터량이 시리얼 대역폭을 초과할 수 있다" 위험은 **이미 실측으로 발현된 문제**다. Phase 1(통신)에서 **시퀀스 번호 기반 유실 검출을 처음부터 넣고**, 최대 데이터율을 계산해 확정할 것. 상세는 Q2의 `Docs/Jetson/jetson_dropout_session_report_2026-06-17.md`.

## 2. 명령어

### 문서 생성·검증 (`docs/datasheet/_internal/`)

상위 저장소 존재 필요. 입력 KiCad 파일은 **읽기 전용** — 스크립트가 절대 수정하지 않는다.

```bash
# 커넥터 핀표 추출 → pinout_v2.0.json / .md
python docs/datasheet/_internal/gen_pinout.py --board v2.0

# 커넥터 배치도 SVG 재생성 → docs/datasheet/img/
python docs/datasheet/_internal/gen_board_map.py --board v2.0

# 데이터시트 본문 ↔ 설계 원본 대조. 불일치 시 exit 1
python docs/datasheet/_internal/verify_datasheet.py

# .md → PDF (Chrome 헤드리스 + CDN, 인터넷 필요)
cd docs/datasheet && bash _internal/make_pdf.sh STM32_v2.0_DATASHEET.md
```

데이터시트의 핀맵·치수·커넥터 수를 손댔다면 **`verify_datasheet.py`를 통과시킨 뒤 커밋한다.**

### 보드에 펌웨어 굽기

전부 데이터시트 §7에 실증 기록이 있는 절차다. 요약만 옮긴다 — **실행 전 §7 원문을 읽을 것.**

```bash
# [보드당 1회] 빈 F103에 디버거(BMP) 굽기. J32 캡=3.3V쪽, J33에 3.3V UART 어댑터
"$CLI" -c port=COM4 br=115200 P=EVEN \
  -w bmp_f103_v2_bootloader.bin 0x08000000 \
  -w bmp_f103_v2_app.bin        0x08002000

# [평상시] USB-C만 꽂고 GDB로 H723 굽기 (scan.gdb는 §7.4 원문)
arm-none-eabi-gdb.exe --batch -x scan.gdb your_firmware.elf
```

참고 펌웨어 빌드는 순수 Makefile + arm-none-eabi-gcc다 (`make` → `build/<target>.elf/.bin/.hex`, `make clean`).

> 이 저장소에 펌웨어·호스트 코드가 생기면 **빌드·테스트·단일 테스트 실행 명령을 이 절에 추가한다.**

## 3. 만들려는 시스템 (계획서 요약)

STM32 v2.0 보드용 **통합 제어·DAQ 시스템**. 펌웨어(H723) + Windows/Jetson 공통 호스트 SW.

```
GNSS NMEA(UTC) + PPS(초 경계) → 공통 시간축
   → ADS1256 DRDY / I2C DMA완료 / EXTI 시점에 타임스탬프 확정
   → 채널별 독립 큐 → 패킷 프로토콜(UART)
   → Board Service (Windows / Jetson 공통) → GUI · 저장 · 오류기록
```

- **펌웨어**: STM32H723ZGT6용 CubeMX 프로젝트를 **새로 생성**한다. LaneControlSystem Q1·Q2는 **둘 다 H7A3용**이라 바이너리·CubeMX 설정 재사용 금지. HAL 비의존 모듈(`time_sync`·`sensor_queue`·`data_pipeline`·`serializer`·`error_mgr`·GNSS 드라이버 등)만 검토 후 이식한다 — 주의점은 §1.2.
- **호스트**: Python 3 + PyQt6 + pyserial + pyqtgraph + SQLite/Parquet + pytest. **Board Service와 GUI를 분리** — Jetson은 GUI 없이 부팅 후 자동 수집해야 한다.
- **디렉터리 계획**: `firmware/` `host/` `protocol/` `hardware_reference/` `tools/` `docs/` (계획서 §18).
- **단계**: Phase 0(회로 확정) → 1(통신) → 2(전원·출력) → 3(GNSS/PPS) → 4(ADS1256) → 5(I2C) → 6(Service) → 7(GUI) → 8(배포). 각 Phase 완료 기준이 계획서 §16에 있다.

### 설계 원칙 4가지 (계획서 §2 — 코드 리뷰 기준)

1. **핀 번호를 노출하지 않는다.** GUI·프로토콜은 `PD8`이 아니라 `24V 전원`을 쓴다. PCB 배선은 고정이므로 임의 핀 재배치 도구를 만들지 않는다.
2. **획득 시각은 STM32가 확정한다.** 호스트가 수신한 시각은 측정 시각이 아니다. UART·스케줄링·GUI·저장 지연이 타임스탬프를 오염시키면 설계 위반이다.
3. **센서 미연결은 정상 상태다.** 비활성 커넥터의 무응답을 오류로 올리지 않는다.
4. **명령 상태와 실제 상태를 구분한다.** 피드백 회로가 없는 레일은 `정상 ON`이 아니라 `ON 명령됨` / `확인 불가`로 표시한다. 전원 상태머신은 피드백 없이 `VERIFIED_ON`으로 가지 않는다.

추가로 **채널 장애 격리** — 센서 하나가 죽어도 다른 채널·PPS 동기·전원 제어·통신은 계속 동작해야 한다.

## 4. 하드웨어 제약 — 코드에 직접 영향

데이터시트에 🔴로 표시된 것 중 펌웨어가 반드시 지켜야 하는 것들.

- **PD8(24V)·PD9(14.9V)·PD10(5V)을 High로 올려야 해당 레일이 나온다.** 리셋 직후 3.3V만 살아 있다. "센서/Jetson/팬이 안 켜진다"의 대부분이 이 원인.
- **PD10은 켠 뒤 절대 내리지 않는다.** 쿨링 팬(J34)이 5V 레일 직결이고 상시 동작이 요구사항이다. 저전력 모드·에러 처리·레일 재시작 루틴에서 PD10을 내리지 않는지 확인할 것. 팬은 속도 제어도 회전수 읽기도 불가능하다(신호선 없음).
- **PA4·PA5는 TT 핀 = 3.3V 전용**(절대최대 4.0V). J18/J19에 5V 입력을 받으면 MCU가 손상된다. PA6(J20)만 5V 허용.
- **EXTI15는 ADS1256 DRDY(PE15)가 점유**한다. LCD 터치 IRQ가 PD12인 이유이며, 새 EXTI를 붙일 때 라인 번호 충돌을 먼저 확인한다.
- **J10/J11, J12/J13, J14/J15는 각각 같은 I2C 버스**다. 주소가 같은 센서를 짝 커넥터에 꽂으면 충돌한다.
- **WS2812 체인은 J21부터 순서대로** 채워야 뒤쪽이 산다.
- **GDB로 H723을 구울 때 `set remote memory-write-packet-size 256` + `fixed` 필수.** 없으면 512B 청크 경계에서 데이터가 손상되는데 `compare-sections`·`dump`로는 못 잡는다. 실제 동작으로 확인할 것.
- 🔴 **되읽기(`dump`)도 흔들린다** [실증 2026-08-17]. 같은 gdb 세션 안에서 같은 플래시를 세 번 읽었더니 **세 번이 다 달랐다**(16·12·4 바이트). 값은 `00` ↔ `ff` 로 통째로 튄다. `set remote memory-read-packet-size 256` + `fixed` 를 걸면 줄지만 없어지지 않고, SWD 클럭을 782 kHz 까지 낮춰도 그대로다.

  → **한 번 읽고 "몇 바이트가 다르다"고 판정하면 안 된다.** 멀쩡히 구워진 판을 두고 재시도를 되풀이하게 된다 — 실제로 20번 넘게 그랬다. `tools/flash_verified.py` 가 세 번 읽어 다수결을 내고, 흔들린 자리 수를 함께 알린다. 그 수가 크면 다시 굽지 말고 링크를 볼 것.
- 🔴 **gdb의 `restore` 로는 플래시를 굽지 못한다** [실증 2026-08-17]. 평범한 메모리 쓰기라 **조용히 아무 일도 일어나지 않는다** — 6번을 "구우고도" 예전 내용이 그대로였다. 플래시 경로를 타는 것은 `load` 뿐이고 오브젝트 파일을 요구한다. raw 백업은 `tools/restore_verified.py` 로 되돌린다(0x08000000 에 놓은 ELF 로 감싼다).
- **BMP 펌웨어는 `bmp_f103_v2/`만 쓴다.** MiniSTM32용 바이너리를 구우면 USB가 아예 열거되지 않는다.
- **BMP가 만드는 두 번째 COM 포트로는 H723과 통신할 수 없다**(PB6/PB7 미연결). H723 디버그 VCP는 USART3(PB10/PB11)이다.
- 🔴 **H723은 USB 연결을 감지할 수 없다** [넷리스트 확인 2026-08-14]. `VBUS` 네트에 U5(H723)도 U6(F103)도 없다 — J2(USB-C), C61/C62/C84, R83, U11(VIN), U7뿐이고 R83은 LED9로 간다(사람 눈에 보이는 표시등). 분압으로 GPIO에 오는 경로도 없다.

  그래서 CONFIG/RUN은 케이블이 아니라 **`$HB` 하트비트**로 판정한다(규격 §6.2). 그게 오히려 맞다 — USB만 꽂고 GUI를 안 켠 상태, 충전기에 꽂은 상태, GUI가 죽은 상태에서 USB 감지는 전부 CONFIG를 잘못 연다. 하트비트는 "케이블이 꽂혔나"가 아니라 **"저쪽에서 사람이 보고 있나"**를 알려주고, 24V를 켜고 끄는 설정을 여는 열쇠로는 후자가 맞다.
- 🔴 **이 보드는 USB로 전원을 받지 않는다** [실증 2026-08-14]. USB를 뽑았다 꽂아도 보드가 리셋되지 않는다 — GDB로 `uwTick`을 읽어 확인했다(재연결 전후로 가동 시간이 이어졌다).

  그래서 **F103(BMP)의 UART 브리지가 멈추면 USB 재연결로는 안 풀린다.** F103도 전원을 유지해 리셋되지 않기 때문이다. 실제로 겪었다 — H723은 완전히 정상이었는데(`mk_uart_write` 중단점 도달, `USART3 ISR`에 TC·TXE 세워짐 = 전송 완료, `BRR=69`, `AFRH=0x7700`) COM23에 한 바이트도 안 왔다. baud를 9600~921600으로 바꿔 CDC 재설정을 유도해도 안 됐고, **보드 전원을 완전히 끊었다 넣으니 즉시 살아났다.**

  → 시리얼이 조용한데 H723이 정상이면 USB가 아니라 **보드 전원**을 껐다 켠다.

- 🔴 **전원을 껐다 켤 때는 20초쯤 기다린다** [실증 2026-08-19]. 굽기가 `Error writing data to flash`로 막혔을 때, 짧게 껐다 켜는 것으로는 **안 풀렸다** — 그 상태에서 SWD 클럭을 2M/1M/500k로 낮춰도, `psize`를 x64~x8로 바꿔도, `connect_rst`를 꺼도, 예전에 성공했던 더 작은 바이너리를 구워도 전부 실패했다. `monitor erase_mass`조차 중간에 멈췄다. 플래시는 잠겨 있지 않았고(RDP Level 0, `FLASH_SR1=0`) 전압도 3.39V로 정상이었다. **전원을 빼고 20초 두었다 넣으니 첫 시도에 구워졌다.**

  → 굽기가 막히면 설정을 바꿔 가며 재시도하지 말고 **전원을 빼고 기다린다.** 짧은 재투입은 F103이 방전되지 않아 같은 상태가 이어진다.

- 🔴 **GDB로 들여다보면 보드가 리셋된다** [실증 2026-08-19]. `monitor connect_rst enable`은 **프로브에 남는다.** 굽기 스크립트가 한 번 켜 두면, 그 뒤에 변수 하나 읽으려고 `attach`만 해도 nRST가 걸려 보드가 재부팅한다.

  → 증상은 "화면이 갑자기 안 나온다"·"센서가 안 읽힌다"로 나타난다. 재부팅하면서 **RAM에만 있던 설정이 전부 기본값으로 돌아가기 때문**이다(`lcd.enabled` 기본은 꺼짐). 원인이 펌웨어처럼 보이지만 아니다. 실행 중인 보드를 진단할 때는 `monitor connect_rst disable`을 먼저 보내거나, 아예 GDB를 붙이지 않는다.

- 🔴 **펌웨어에 IWDG(독립 워치독, 5초)가 있다** [실증 2026-08-22 — PC를 무한루프로 납치하자 ~5초에 리셋·자가복구]. 메인 루프가 5초 서면 칩이 스스로 리셋하고 **RAM에만 있던 설정은 사라진다**(`$CFG,SAVE` 규칙 그대로). GDB로 코어를 세워도 DBGMCU 얼림 비트(APB4FZ1 bit18, 펌웨어가 세움) 덕에 리셋되지 않는다 — 단 이 비트는 디버거 세션이 끝나면 되돌아가며, halt 중에 비트를 손으로 꺼도 그 halt에서는 발화하지 않았다(래치로 추정, 같은 날 관측). 한 번 켜면 끌 수 없다(RM0468).
- 🔴 **24V 레일이 켜져 있으면 실행 중인 보드에 SWD가 안 붙는다** [실증 2026-08-22, A-B-A]. `swdp_scan`이 `SWD invalid ACK`/`parity error`로 실패한다 — 부스트 컨버터 스위칭 노이즈로 보인다. `pwr.24v`를 RAM에서 끄면(`$CFG,SET pwr.24v false`, SAVE 안 함) 즉시 붙고, 다시 켜면 다시 깨진다. **굽기는 영향 없다** — 리셋을 걸면 PD8~10이 풀려 레일이 다 꺼지기 때문이고, "리셋 중엔 타깃 전압 3.3V, 실행 중엔 3.0V"로 갈리는 것도 같은 이유다. 실행 중 진단이 필요하면 24V를 잠깐 끄고 붙은 뒤 되켠다(수집 중이면 4~20mA 채널이 그동안 0mA로 보인다 — 사용자에게 먼저 알릴 것).
- 🔴 **배터리(J17)가 물려 있으면 SWD 스캔이 깨진다** [실증 2026-08-22]. 충전기 U4(TPS5430)는 ENA 미연결 = 항상 ON이라 배터리가 물리면 메인 전원이 있는 한 계속 충전 전류를 스위칭한다 — 리셋 중이든 레일이 꺼졌든 못 끈다. 이 노이즈로 `swdp_scan`이 `invalid ACK`/`parity error`로 실패하고, **전원 20초 차단으로도 안 풀린다**(껐다 켜면 충전이 다시 시작되므로). 굽기·SWD 진단 전에 **J17을 뽑는다.**
- 🔴 **F103이 USB(CDC) 일을 하는 동안 SWD 쓰기가 구멍난다** [실증 2026-08-22]. F103은 USB 브리지와 SWD 마스터를 겸직한다. 굽는 중에 COM23 쪽에 일이 있으면(H723이 텔레메트리를 붓는 중이거나, PC 프로그램이 포트를 폴링 중이거나) `load`가 "Error writing data to flash"로 죽고, 플래시에 수백 개의 FF 구멍이 흩어진다 — mass erase 후 재시도해도 같다. 칩을 지워 조용하게 만들고 COM23을 여는 프로그램을 전부 끄자 첫 시도에 구워졌다. → **굽기 전에 GUI 등 COM23 리더를 닫는다.** 침묵 게이트(규격 §7.1.3) 이후로는 부팅 직후 홍수가 없어 이 조건이 거의 사라졌다.
- 🔴 **`$CFG,SET`은 RAM만 바꾼다.** `$CFG,SAVE`를 보내야 플래시에 남는다. 굽기는 설정 영역까지 지우므로, 구운 뒤에는 `tools/restore_board_config.py`로 되세우고 저장한다. 이것을 빠뜨리면 리셋·전원 재투입마다 기본값으로 돌아간다.
- 🔴 **굽기가 막히면 `monitor connect_rst enable`** [실증 2026-08-14]. 깨진 펌웨어가 붙기도 전에 실행되면 `attach` 자체가 실패한다(`Attaching to Remote target failed: FF`). 리셋을 건 채로 접속하면 코드가 돌지 않아 언제나 붙는다.

  그리고 **`load` 앞에 `monitor reset`을 넣지 않는다.** `attach`는 코어를 멈춘 채 붙는데 거기서 리셋하면 다시 달리기 시작하고, 깨진 판이면 그것이 곧 HardFault다 — 죽은 펌웨어가 다음 굽기를 계속 막는다.
- 🔴 **F103 브리지가 굳으면 J28 핀5↔핀6(GND) 점퍼로 F103만 리셋된다** [실증 2026-08-22 — COM 포트 소멸·복귀로 확인]. 전원 20초 차단보다 먼저 시도할 것. 단 이것으로 안 풀리면 원인이 F103이 아니다 — 위의 배터리 노이즈·CDC 겸직 항목을 먼저 의심한다(2026-08-22에 실제로 그 둘이었다).
- 🔴 **호스트 침묵 게이트(규격 §7.1.3, 2026-08-22)**: 보드는 호스트 `$HB`가 신선할 때(3초)만 COM23 텔레메트리를 낸다. **부팅 직후는 침묵**이고, GUI·CLI가 붙으면 1초 안에 흐르기 시작한다. "보드가 조용하다" = 고장이 아니라 HB를 안 보내고 있을 가능성부터 본다. 젯슨 링크(USART2)는 게이트 밖 — 항상 나간다.
- 🔴 **시리얼 포트를 DTR/RTS를 세운 채 열면 보드가 멈춘다** [실증 2026-08-14]. .NET `System.IO.Ports.SerialPort`는 열 때 둘 다 기본으로 세우므로 **읽기만 할 때도 쓰지 않는다.** pyserial을 쓰되 `s.dtr = False; s.rts = False`를 **열기 전에** 설정한다 — `serial.Serial("COM23", ...)` 한 줄 생성자는 열면서 세우므로 안 된다.

  멈췄을 때는 굽지 말고 GDB로 리셋한다. BMP는 살아 있다:
  ```
  target extended-remote \\.\COM24
  monitor swdp_scan
  attach 1
  monitor reset
  detach
  ```
  실제로 이렇게 되살렸다. `seq`가 처음부터 다시 올라가는 것으로 재부팅을 확인한다.

## 5. 근거 기반 작업 — 추정 금지 (BLOCKING)

상위 저장소에서 이어지는 규칙이며 여기서도 유효하다. 부품 사양·회로 동작·핀맵·호환성을 답할 때 **추정하지 않는다.**

- ❌ "보통은", "일반적으로", "아마", "~일 것 같다"
- ✅ 데이터시트의 정확한 인용 또는 출처를 명시한 검색 결과 위에서만 답한다.
- 확인 불가면 **"확인 필요"** 라고 명시하고 멈춘다. 짐작으로 진행하지 않는다.
- 인용 형식: `<파일명>.pdf, p.<페이지>, Section <X.Y>: "<인용>"` → 따라서 <결론>
- PDF는 PDF MCP 도구로 읽는다. 텍스트 추출 일부나 화면 캡쳐 추측으로 결론 내리지 않는다.
- 부품 데이터시트가 없으면 직접 다운로드한다(JLCPCB part page → aliyun OSS PDF, `&amp;`→`&`). 실패했을 때만 "확보 필요"로 멈춘다.

**검증 상태 배지를 지어내지 않는다.** 데이터시트는 **[실증]**(실물 확인, 날짜 병기) / **[설계확정]**(회로·계산 확정, 실물 미검증) / **[미확인]**을 구분한다. 현재 **실증된 것은 커넥터 핀맵과 펌웨어 굽기 절차뿐**이고 전원 시퀀싱 동작·ADS1256 측정 체인·I2C·GNSS·WS2812·LCD·배터리 절체는 전부 **[설계확정·실기기 미검증]**이다. 미검증 항목을 "동작한다"고 서술하지 말 것.

## 6. Codex 점검 결과 (`docs/status/`)

**Codex가 이 프로젝트의 작업 상태를 외부에서 점검하고 결과를 기록한다.** 세션 시작 시 그리고 작업 방향을 정할 때 **읽고 반영한다.**

| 경로 | 내용 |
|---|---|
| `docs/status/PROJECT_STATUS.md` | **최신 상태.** 여기부터 읽는다 |
| `docs/status/history/YYYY-MM-DD_HHMM.md` | 점검 이력 (시점별 스냅샷) |
| `docs/status/logs/` | 터미널·시험 원본 로그 |

- **이 파일들은 Codex가 쓴다. 내가 고치지 않는다.** 점검 결과가 사실과 다르다고 판단되면 파일을 수정하는 대신 **사용자에게 근거와 함께 알린다.**
- 지적받은 항목은 **§5의 근거 기준으로 검증한 뒤** 반영한다. "지적당했으니 일단 고친다"도, "내가 맞으니 무시한다"도 아니다. 로그가 있으면 `logs/`의 원본을 직접 확인한다.
- 내가 한 주장(동작 확인, 시험 통과)이 `logs/`의 실제 출력과 어긋나면 **로그가 맞다.**
- `docs/status/`도 `docs/` 아래라 **git 추적 대상이 아니다**(§0). 점검 이력은 파일 자체가 유일한 기록이므로 삭제·덮어쓰기에 주의한다.

## 7. 알려진 문서 불일치

- 계획서 §6.1은 이식 대상으로 **Q1만** 언급하지만, 실제로는 **Q2가 직계 조상**이고(4~20mA·솔레노이드·WS2812·Jetson 링크) Q1에만 있는 모듈(`data_pipeline` `error_mgr`)과 호스트 단위 테스트가 따로 있다. **양쪽을 모두 봐야 한다**(§1.2).
- 계획서 §4 표: `Jetson 데이터 UART = PA2/PA3`, `H723 플래시 UART = PA9/PA10`. 데이터시트 기준으로는 **PA9/PA10은 J29 핀4·5의 Jetson 보조 UART1**이고, **H723 디버그/플래시용 VCP는 USART3(PB10/PB11)**, UART 부트로더 직접 굽기도 PB10/PB11을 쓴다(데이터시트 §7.6). 계획서가 MiniSTM32 기준을 일부 물고 온 것으로 보인다. **데이터시트를 따르고**, 계획서 §4를 고칠 때 함께 정정한다.
- `docs/PCB_PIN_CONNECTIONS.md`(07-02), `docs/NETLIST.md`(07-09)는 상위 저장소에 있으나 **낡았다** — J34와 LCD 변경이 빠져 있다. 커넥터 정보는 항상 데이터시트를 기준으로 본다.

## 8. Git 규칙

기본 브랜치 `main`, **원격 없음(로컬 전용)**. 추적 범위는 **소스 코드만** — `docs/`는 제외한다(§0).

### 형식 — Conventional Commits + 한국어 제목

```
<type>(<scope>): <한국어 요약, 명사형 종결>

<왜 이렇게 했는지. 무엇을 바꿨는지가 아니라 이유와 배경.>

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
```

**type**: `feat` `fix` `refactor` `docs` `test` `build` `chore`
**scope**: `fw`(펌웨어) `host`(Board Service) `gui` `proto`(통신 프로토콜) `tools` `docs` `hw`(하드웨어 참조자료)

예시(상위 저장소 실제 이력):

```
feat(fw): H723 전원 레일 순차 기동 펌웨어 추가

3.3V만 상시 ON이므로 나머지 5V/14.9V/24V는 MCU가 GPIO로 켜야 한다.
PD10/PD9/PD8 순으로 500ms 간격 enable, PD11 LED_STATUS로 진행 표시.
```

### 규칙

- **커밋·푸시는 사용자가 요청할 때만 한다.** 작업이 끝났다고 자동으로 커밋하지 않는다.
- 본문은 **"왜"를 쓴다.** diff를 보면 알 수 있는 변경 목록 나열은 가치가 없다. 위 예시처럼 하드웨어 제약이나 설계 판단의 근거를 남긴다.
- 커밋 하나 = 논리적 변경 하나. 펌웨어 모듈 추가와 문서 수정을 한 커밋에 섞지 않는다.
- **빌드 산출물(`build/`, `.elf`, `.bin`, `.hex`)과 수집 데이터(`*.sqlite`, `*.parquet`, `*.ndjson`, `logs/`)는 커밋하지 않는다.** 참고용 펌웨어 바이너리처럼 배포 목적이 명확해 `git add -f`가 필요한 경우에만 예외로 하고, 그 이유를 커밋 본문에 남긴다.
- **`docs/` 안의 파일을 커밋하려고 `-f`를 쓰지 않는다.** 제외는 의도된 결정이다. 문서를 저장소에 넣어야 할 이유가 생기면 `.gitignore`부터 사용자와 상의해 바꾼다.
- `--no-verify`, `--amend`(이미 공유된 커밋), force push는 사용자가 명시적으로 요청하지 않는 한 쓰지 않는다.

### 문서를 고칠 때 (커밋되지 않으므로 더 조심할 것)

- 데이터시트·핀표를 고쳤으면 `verify_datasheet.py`를 통과시킨다.
- 생성물(`img/*.svg`, `pinout_v2.0.*`, `*.pdf`, `_print_*.html`)은 **손으로 고치지 말고 스크립트로 재생성**한다.
- 해당 문서의 **개정 이력 표와 Version·Last Updated를 함께 갱신**한다. git 이력이 없으니 이 표가 유일한 변경 기록이다.
