# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## 0. 이 저장소의 현재 상태

**아직 소스 코드가 없다.** git 저장소(`main`)는 초기화돼 있고, 현재 추적되는 것은 `CLAUDE.md`와 `.gitignore`뿐이다. 원격 저장소는 없다 — 로컬 전용.

```
MarkON_Studio/
├─ CLAUDE.md          ← 추적됨
├─ .gitignore         ← 추적됨
└─ docs/              ← 🔴 git 추적 제외. 로컬에만 있다
   ├─ STM32_V2_CONTROL_DAQ_DEVELOPMENT_PLAN.md   ← 만들 것 (개발 계획, 초안)
   └─ datasheet/
      ├─ STM32_v2.0_DATASHEET.md (v1.1)          ← 대상 하드웨어 (근거 문서)
      ├─ STM32_v2.0_DATASHEET.pdf / _print_*.html
      ├─ img/        커넥터 배치도·상하면 실크 SVG (생성물)
      └─ _internal/  생성·검증 스크립트 + pinout_v2.0.json/md
```

### 🔴 `docs/`는 무시 대상이지 불필요한 파일이 아니다

저장소는 **소스 코드만 추적**하기로 했고(사용자 결정), `docs/`는 `.gitignore`에 들어 있다. 하지만 이 프로젝트의 **모든 핀맵·전기사양·굽기 절차·설계 원칙의 근거**가 거기 있다.

- `git clean`, `git status`의 untracked 정리, "안 쓰는 파일 같으니 지우자" 류 판단으로 **절대 삭제하지 않는다.**
- 버전관리 밖이라 **되돌릴 수 없다.** 문서를 고칠 때는 상위 저장소(`D:\STM32_PCB_Board`, §1)에 원본이 있는지 먼저 확인한다.
- 문서를 고쳐야 할 상황이면 그 사실을 사용자에게 알린다. 커밋으로 남지 않으므로 변경 이력은 문서 안의 개정 이력 표가 전부다.

작업 시작 전 **두 문서를 모두 읽는다.** 데이터시트가 "무엇이 있는가"(사실), 계획서가 "무엇을 만드는가"(목표)다. 충돌하면 **데이터시트가 우선**이다(§6 참조).

## 1. 상위 저장소 `D:\STM32_PCB_Board`

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
- 신규 코드(펌웨어·호스트 SW)는 **이 저장소에서** 개발한다. 상위 저장소는 하드웨어·문서 원본으로만 참조한다.

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

- **펌웨어**: STM32H723ZGT6용 CubeMX 프로젝트를 **새로 생성**한다. 기존 LaneControlSystemQ1은 **H7A3용**이라 바이너리·CubeMX 설정 재사용 금지. `time_sync`·`sensor_queue`·`data_pipeline`·`serializer`·`error_mgr`·GNSS 드라이버만 검토 후 이식.
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
- **BMP 펌웨어는 `bmp_f103_v2/`만 쓴다.** MiniSTM32용 바이너리를 구우면 USB가 아예 열거되지 않는다.
- **BMP가 만드는 두 번째 COM 포트로는 H723과 통신할 수 없다**(PB6/PB7 미연결). H723 디버그 VCP는 USART3(PB10/PB11)이다.

## 5. 근거 기반 작업 — 추정 금지 (BLOCKING)

상위 저장소에서 이어지는 규칙이며 여기서도 유효하다. 부품 사양·회로 동작·핀맵·호환성을 답할 때 **추정하지 않는다.**

- ❌ "보통은", "일반적으로", "아마", "~일 것 같다"
- ✅ 데이터시트의 정확한 인용 또는 출처를 명시한 검색 결과 위에서만 답한다.
- 확인 불가면 **"확인 필요"** 라고 명시하고 멈춘다. 짐작으로 진행하지 않는다.
- 인용 형식: `<파일명>.pdf, p.<페이지>, Section <X.Y>: "<인용>"` → 따라서 <결론>
- PDF는 PDF MCP 도구로 읽는다. 텍스트 추출 일부나 화면 캡쳐 추측으로 결론 내리지 않는다.
- 부품 데이터시트가 없으면 직접 다운로드한다(JLCPCB part page → aliyun OSS PDF, `&amp;`→`&`). 실패했을 때만 "확보 필요"로 멈춘다.

**검증 상태 배지를 지어내지 않는다.** 데이터시트는 **[실증]**(실물 확인, 날짜 병기) / **[설계확정]**(회로·계산 확정, 실물 미검증) / **[미확인]**을 구분한다. 현재 **실증된 것은 커넥터 핀맵과 펌웨어 굽기 절차뿐**이고 전원 시퀀싱 동작·ADS1256 측정 체인·I2C·GNSS·WS2812·LCD·배터리 절체는 전부 **[설계확정·실기기 미검증]**이다. 미검증 항목을 "동작한다"고 서술하지 말 것.

## 6. 알려진 문서 불일치

- 계획서 §4 표: `Jetson 데이터 UART = PA2/PA3`, `H723 플래시 UART = PA9/PA10`. 데이터시트 기준으로는 **PA9/PA10은 J29 핀4·5의 Jetson 보조 UART1**이고, **H723 디버그/플래시용 VCP는 USART3(PB10/PB11)**, UART 부트로더 직접 굽기도 PB10/PB11을 쓴다(§7.6). 계획서가 MiniSTM32 기준을 일부 물고 온 것으로 보인다. **데이터시트를 따르고**, 계획서 §4를 고칠 때 함께 정정한다.
- `docs/PCB_PIN_CONNECTIONS.md`(07-02), `docs/NETLIST.md`(07-09)는 상위 저장소에 있으나 **낡았다** — J34와 LCD 변경이 빠져 있다. 커넥터 정보는 항상 데이터시트를 기준으로 본다.

## 7. Git 규칙

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
