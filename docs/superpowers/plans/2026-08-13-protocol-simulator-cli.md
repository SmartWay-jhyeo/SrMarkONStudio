# 프로토콜 기반 + 장치 시뮬레이터 + CLI 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** STM32 v2.0 보드와 주고받을 프로토콜을 확정하고, 보드 없이도 전 구간을 시험할 수 있는 Python 프로토콜 라이브러리·장치 시뮬레이터·CLI를 만든다.

**Architecture:** 순수 로직과 I/O를 분리한다. 프레이밍·스키마·시뮬레이터는 시리얼이나 시계에 의존하지 않는 순수 함수·클래스로 만들어 pytest로 전부 검증하고, 시리얼 입출력은 얇은 어댑터로 감싼다. 시뮬레이터는 펌웨어와 같은 상태 기계(CONFIG/RUN, 설정 검증, 인터록)를 구현해 이후 펌웨어의 기준 구현이자 GUI의 개발용 상대역이 된다.

**Tech Stack:** Python 3.11+, pytest, pyserial. GUI 의존성(PyQt6, pyqtgraph)은 이 계획에 포함하지 않는다.

**Spec:** `docs/superpowers/specs/2026-08-13-config-control-gui-design.md`

## Global Constraints

- `schema_ver` = **3**. 모든 NDJSON 줄에 포함한다.
- `seq`, `t`, `type` 세 필드는 **끌 수 없다.** 필드 마스크가 무엇이든 항상 출력한다.
- 체크섬은 **NMEA 방식** — `$`와 `*` 사이 문자 전부의 XOR, 대문자 2자리 16진수. 줄 끝은 `\r\n`.
- 모드 판정: 호스트 `$HB`를 **3000 ms** 안에 받으면 CONFIG, 그 이상 끊기면 RUN.
- `$CFG,SET` / `$CFG,SAVE` / `$CFG,RESET`은 CONFIG 모드에서만 수락. RUN에서는 `ERR,MODE`.
- 거부 사유 문자열은 정확히 이 8개: `RANGE`, `UNKNOWN_KEY`, `READONLY`, `INTERLOCK`, `MODE`, `CHECKSUM`, `BUSY`, `CAPACITY`.
- **하트비트 시각은 체크섬 검증을 통과한 뒤에만 갱신한다.** `$HB`가 CONFIG 모드를 여는 열쇠이므로, 깨진 프레임이 우연히 `$HB*`로 시작한다고 모드가 유지되면 안 된다. (Q2 `host_link.c:183-187`은 반대로 구현돼 있다 — 그대로 이식하지 말 것)
- `$HB`는 **양방향**이다. 호스트→보드 = "GUI가 살아 있다"(CONFIG 유지), 보드→호스트 = "보드가 살아 있다"(연결 표시).
- `pwr.5v`는 읽기 전용 `true`. 변경 시도는 `INTERLOCK`. (쿨링 팬이 5V 레일 직결, 데이터시트 §5.14)
- 부동소수 직렬화 기본 자릿수 **4**, 범위 2~6 (`tx.float_digits`).
- 모든 설정·제어 요청은 **반드시 응답을 가진다.** 무응답 경로를 만들지 않는다.
- Python 3.11+, 표준 라이브러리 우선. 외부 의존성은 `pyserial`과 `pytest`만.

---

## 파일 구조

| 경로 | 책임 |
|---|---|
| `pyproject.toml` | 패키지 설정, pytest 설정 |
| `protocol/specification.md` | 프로토콜 규격서 — 펌웨어·호스트 공통 계약 |
| `host/core/framing.py` | XOR 체크섬, `$` 명령 조립·파싱 |
| `host/core/records.py` | NDJSON 레코드 파싱, seq 누락 추적 |
| `host/core/config_schema.py` | `$CFG,LIST` 응답 → 스키마 객체, 값 검증 |
| `host/core/errors.py` | 프로토콜 예외 및 거부 사유 상수 |
| `tools/simulator/config_store.py` | 시뮬레이터 설정 저장소 (검증·인터록·영속) |
| `tools/simulator/device_sim.py` | 가상 보드 — 명령 디스패치, 모드, 텔레메트리 |
| `tools/simulator/serial_server.py` | 시뮬레이터를 시리얼 포트에 붙이는 어댑터 |
| `host/service/board_service.py` | 시리얼 연결·재연결, 수신 스레드, 명령/응답 매칭 |
| `tools/cli/markon_cli.py` | 명령줄 도구 |
| `host/tests/` | pytest |

순수 로직(`framing`, `records`, `config_schema`, `config_store`, `device_sim`)은 **시리얼과 시계에 의존하지 않는다.** I/O는 `serial_server`와 `board_service`에만 있다.

---

## Task 1: 프로젝트 뼈대

**Files:**
- Create: `pyproject.toml`
- Create: `host/__init__.py`, `host/core/__init__.py`, `host/tests/__init__.py`
- Create: `tools/__init__.py`, `tools/simulator/__init__.py`, `tools/cli/__init__.py`
- Test: `host/tests/test_smoke.py`

**Interfaces:**
- Consumes: 없음
- Produces: `pytest` 실행 환경. 이후 모든 태스크가 `pytest host/tests -v`로 검증한다.

- [ ] **Step 1: `pyproject.toml` 작성**

```toml
[project]
name = "markon-studio"
version = "0.1.0"
description = "STM32 v2.0 통합 제어·DAQ 시스템"
requires-python = ">=3.11"
dependencies = ["pyserial>=3.5"]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

# setuptools 자동탐색은 최상위 패키지를 하나만 골라 tools 를 누락한다.
# Task 6~11 이 전부 tools.simulator.* 를 import 하므로 명시한다.
[tool.setuptools.packages.find]
include = ["host*", "tools*"]

[tool.pytest.ini_options]
testpaths = ["host/tests"]
pythonpath = ["."]
addopts = "-v"
```

- [ ] **Step 2: 패키지 디렉터리와 빈 `__init__.py` 생성**

```bash
mkdir -p host/core host/service host/tests tools/simulator tools/cli protocol
touch host/__init__.py host/core/__init__.py host/service/__init__.py host/tests/__init__.py
touch tools/__init__.py tools/simulator/__init__.py tools/cli/__init__.py
```

- [ ] **Step 3: 스모크 테스트 작성**

`host/tests/test_smoke.py`:
```python
import sys


def test_python_version():
    assert sys.version_info >= (3, 11)
```

- [ ] **Step 4: 테스트 실행**

Run: `python -m pytest host/tests/test_smoke.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: `.gitignore` 확인**

`.gitignore`에 `__pycache__/`, `.pytest_cache/`, `.venv/`가 이미 있는지 확인한다. 있으면 그대로 둔다.

Run: `git check-ignore -v __pycache__ .pytest_cache`
Expected: 두 경로 모두 `.gitignore` 규칙에 걸린다고 출력

- [ ] **Step 6: 커밋**

🔴 **`git add tools` 를 쓰지 말 것.** 저장소에는 이 계획과 무관한
`tools/project_watchdog.ps1`(감사 도구)과 `AGENTS.md` 가 추적되지 않은 채로
있다. 디렉터리째 add 하면 함께 커밋된다. **아래 경로만 정확히 add 한다.**

```bash
git add pyproject.toml host/ tools/__init__.py tools/simulator/ tools/cli/
git status --short          # AGENTS.md 와 project_watchdog.ps1 이 ?? 로 남아야 한다
git commit -m "chore: Python 프로젝트 뼈대 생성

pytest 실행 환경과 host/tools 패키지 구조를 만든다.
외부 의존성은 pyserial 하나로 제한한다."
```

---

## Task 2: 프로토콜 규격서

**Files:**
- Create: `protocol/specification.md`

**Interfaces:**
- Consumes: 없음
- Produces: 펌웨어와 호스트가 함께 참조하는 계약 문서. 이후 태스크의 구현이 이 문서와 어긋나면 문서가 기준이다.

이 태스크는 코드가 없다. 계약을 먼저 못 박아 두면 이후 태스크가 서로 다른 가정을 하지 않는다.

- [ ] **Step 1: 규격서 작성**

`protocol/specification.md`:
````markdown
# MarkON Studio 시리얼 프로토콜 규격 v3

**대상:** STM32 v2.0 (STM32H723ZGT6) ↔ 호스트 (Windows / Jetson)
**설계 근거:** `docs/superpowers/specs/2026-08-13-config-control-gui-design.md`

## 1. 물리 계층

| 채널 | 경로 | 용도 |
|---|---|---|
| 설정·제어 | H723 USART3 (PB10/PB11) → F103 USART2 (PA2/PA3) → USB-C (J2) | GUI |
| 데이터 | H723 USART2 (PA2/PA3) / USART1 (PA9/PA10) → J29 | Jetson |

두 채널은 물리적으로 분리돼 있어 동시에 살아 있어도 서로 방해하지 않는다.

⚠️ 상용 Black Magic Probe 펌웨어의 VCP는 F103 USART1(PB6/PB7)에 묶여 있고 그
핀은 **미연결**이다. USBUSART를 USART2(PA2/PA3)로 바꿔 재빌드해야 USB 한
가닥으로 설정 채널이 열린다.

## 2. 줄 형식

두 종류의 줄이 같은 스트림에 섞여 흐른다. 첫 글자로 구분한다.

| 첫 글자 | 종류 |
|---|---|
| `$` | 명령 / 응답 / 하트비트 |
| `{` | NDJSON 텔레메트리 또는 설정 응답 |

모든 줄은 `\r\n`으로 끝난다. 수신측은 `\n`만 와도 받아들인다.

## 3. `$` 줄 — 체크섬

```
$<PAYLOAD>*<CS>\r\n
```

`<CS>`는 `<PAYLOAD>`를 **UTF-8로 인코딩한 바이트**들의 XOR을 **대문자 2자리
16진수**로 쓴 것이다 (NMEA 0183과 같은 방식). `$`와 `*`는 계산에 포함하지 않는다.

🔴 **설정 문자열 값은 인쇄 가능 ASCII에서 구분자를 뺀 것만 허용한다.**

```
허용 = 0x20 ~ 0x7E  −  { '$', ',', '*' }
```

**ASCII인 것만으로는 부족하다.** 제어문자가 전부 ASCII이기 때문이다 — `'\n'`도
`'\x00'`도 ASCII다. 다음이 이 규칙으로 막힌다.

| 문자 | 허용하면 |
|---|---|
| `\r` `\n` | 값 안에 완결된 명령을 심을 수 있다. `dev.id`를 `x\r\n$HB*0A`로 두면 조립된 줄이 `$CFG,SET,dev.id,x\r\n$HB*0A*72\r\n`가 되고, **주입된 `$HB*0A`는 체크섬까지 유효해** 보드가 진짜 하트비트로 받아 CONFIG 모드를 연다 |
| `\x00` | C 펌웨어에서 문자열이 중간에 끝난다. 호스트가 검증한 값과 보드가 저장한 값이 달라진다 |
| `,` | 명령 인자를 추가로 쪼갠다 |
| `*` | 체크섬 구분자로 오인된다 |
| `$` | 명령 시작으로 오인된다 |

단위는 `degC`·`kPa`·`LPM`·`%`처럼 쓰고 `℃`는 쓰지 않는다. 위반값은
`ERR,RANGE`로 거부한다. **`$CFG,SET` 경로와 Flash 복원 경로 양쪽에서 같은
검사를 적용한다** — 한쪽만 막으면 다른 쪽으로 들어온다.

이 제약 덕에 문자열 길이 제한(`str≤7` 등)이 **문자 수 = 바이트 수**가 되어
펌웨어의 고정폭 버퍼와 정확히 일치한다.

체크섬을 문자가 아니라 바이트로 정의하는 것은 펌웨어와 정의를 맞추기 위해서다.
값이 ASCII면 두 계산은 결과가 같다.

수신측은 **소문자 16진수도 받아들인다.** 송신은 항상 대문자로 만든다.
`<PAYLOAD>`가 비어 있는 줄(`$*00`)은 체크섬이 맞아도 거부한다 — verb가 없다.

> ⚠️ 예외는 `cfg_item`의 `label`·`note`뿐이다. 이들은 GUI에 그대로 표시되는
> 한국어 텍스트이며 NDJSON으로만 전달된다. `$` 줄에 실리지 않으므로 체크섬
> 계산과 무관하고, 사용자가 입력하는 값도 아니다.

검증 벡터:

| 줄 | PAYLOAD | XOR |
|---|---|---|
| `$HB*0A` | `HB` | `0x48 ^ 0x42 = 0x0A` |
| `$ID*0D` | `ID` | `0x49 ^ 0x44 = 0x0D` |

체크섬이 맞지 않는 줄은 폐기하고 `$SACK,<verb>,ERR,CHECKSUM`을 보낸다.
verb를 읽을 수 없을 만큼 깨졌으면 조용히 버린다 (링크는 유지).

## 4. 호스트 → 보드 명령

| 명령 | 인자 | CONFIG 전용 |
|---|---|---|
| `$HB` | — | 아니오 |
| `$ID` | — | 아니오 |
| `$CFG,LIST` | — | 아니오 |
| `$CFG,GET` | `<key>` | 아니오 |
| `$CFG,SET` | `<key>,<value>` | **예** |
| `$CFG,SAVE` | — | **예** |
| `$CFG,RESET` | — | **예** |
| `$STAT` | — | 아니오 |

## 5. 보드 → 호스트 응답

```
$SACK,<verb>,OK*<CS>
$SACK,<verb>,ERR,<reason>*<CS>
```

`<verb>`는 요청한 명령의 첫 토큰(`CFG`, `ID`, `STAT`)이다.

| reason | 의미 |
|---|---|
| `RANGE` | 값이 허용 범위 밖 |
| `UNKNOWN_KEY` | 존재하지 않는 설정 키 |
| `READONLY` | 읽기 전용 항목 |
| `INTERLOCK` | 안전 정책상 거부 |
| `MODE` | RUN 모드에서 설정 변경 시도 |
| `CHECKSUM` | 체크섬 불일치 |
| `BUSY` | Flash 쓰기 중 |
| `CAPACITY` | 값은 범위 안이지만 **조합이 물리적으로 달성 불가** (§5.1) |

이 8개가 전부다. 수신측은 모르는 사유를 만나면 일반 오류로 처리한다.

### 5.1 `CAPACITY` — 조합 검증

개별 값이 전부 범위 안이어도 조합이 불가능할 수 있다. 7채널을 각각 10 ms
주기로 두고 `adc.drate`를 30 SPS로 잡으면 요구 700 SPS 대 가용 30 SPS라
큐가 영구히 넘친다.

```
요구 총 샘플률 = Σ (1000 / ainN.period_ms)   (enabled 채널만)
채널당 소요    = 1/DRATE + 채널 전환 정착시간
가용 총 샘플률 = 1 / 채널당 소요
요구 > 가용 × 안전여유  →  ERR,CAPACITY
```

거부 메시지에 **요구값과 가용값을 함께 담아** 무엇을 줄여야 하는지 알린다.

🔴 **부하를 늘리지 않는 변경은 이 검사를 건너뛴다.** 이미 초과 상태에 빠진
설정에서 채널을 끄거나 주기를 늘리는 것까지 막으면 빠져나올 방법이 없어진다.

### 5.2 명령별 응답 본문

`$SACK`는 성공·실패만 알린다. 데이터를 돌려주는 명령은 **`$SACK` 앞에 NDJSON
줄을 먼저 보낸다.** 수신측은 `$SACK`를 받을 때까지 줄을 모은다.

| 명령 | 본문 | `$SACK` |
|---|---|---|
| `$HB` | 없음 | **없음** — 하트비트에는 응답하지 않는다 |
| `$ID` | `id` 레코드 1줄 | `$SACK,ID,OK` |
| `$STAT` | `stat` 레코드 1줄 (§7.4) | `$SACK,STAT,OK` |
| `$CFG,LIST` | `cfg_item`·`cfg_field` 여러 줄 + `cfg_end` 1줄 (§7.3) | `$SACK,CFG,OK` |
| `$CFG,GET` | `cfg_value` 레코드 1줄 | `$SACK,CFG,OK` |
| `$CFG,SET` | 없음 | `$SACK,CFG,OK` 또는 `ERR` |
| `$CFG,SAVE` | 없음 | `$SACK,CFG,OK` 또는 `ERR` |
| `$CFG,RESET` | 없음 | `$SACK,CFG,OK` 또는 `ERR` |

실패 시에는 본문 없이 `$SACK,<verb>,ERR,<reason>`만 보낸다.

**`id` 레코드**

```json
{"schema_ver":3,"seq":0,"t":1772200855875,"type":"id",
 "device_id":"1","fw":"0.1.0","board_rev":"2.0"}
```

| 필드 | 타입 | 의미 |
|---|---|---|
| `device_id` | str≤15 | `dev.id` 설정값 |
| `fw` | str | 펌웨어 버전 |
| `board_rev` | str | 보드 리비전 (`"2.0"`) |

**`cfg_value` 레코드**

```json
{"schema_ver":3,"seq":0,"t":1772200855875,"type":"cfg_value",
 "key":"tx.period_ms","cur":100}
```

`cur`의 JSON 타입은 해당 항목의 `vtype`을 따른다 — `bool`은 참/거짓,
`str`은 문자열, 나머지는 수다.

⚠️ 명령 응답 레코드(`id`·`stat`·`cfg_value`·`cfg_*`)의 `seq`는 **항상 0**이다.
텔레메트리 `seq`와 같은 계열이 아니므로 유실 검출에 쓰지 않는다.

검증 순서는 위 표의 순서가 아니라 다음과 같다. 값이 틀린 것과 안전상
거부된 것은 사용자에게 다른 메시지여야 하므로 인터록을 범위 검사 뒤에 둔다.

```
체크섬 → 모드 → 키 존재 → 타입·범위 → 인터록 → 읽기 전용 → 수락
```

🔴 **인터록이 읽기 전용보다 우선한다.** `pwr.5v`처럼 둘 다 해당하는 항목은
`READONLY`가 아니라 `INTERLOCK`을 반환한다. 사용자가 "왜 안 되는지"를 알아야
하기 때문이다 — `note`에 담긴 "쿨링 팬이 5V 레일 직결"이라는 하드웨어 사실이
곧 사유가 된다. `READONLY`만 돌려주면 그 이유가 사라진다.

**현재값과 같은 값을 쓰는 것은 거부하지 않는다.** `pwr.5v`를 `true`로 두는
요청은 아무것도 바꾸지 않으므로 `OK`다. 호스트가 전체 설정을 한꺼번에
되돌려 쓸 때 불필요한 거부가 나지 않게 한다.

## 6. 모드와 하트비트

### 6.1 `$HB`는 양방향이며 방향마다 뜻이 다르다

같은 줄이지만 방향에 따라 의미가 다르다. 뭉뚱그리면 구현이 갈린다.

| 방향 | 뜻 | 받는 쪽이 하는 일 |
|---|---|---|
| 호스트 → 보드 | "GUI가 살아 있다" | **CONFIG 모드 유지.** 3000 ms 끊기면 RUN |
| 보드 → 호스트 | "보드가 살아 있다" | 연결 상태 표시. 끊기면 재연결 시도 |

양쪽 다 **1 Hz**로 보내고 줄 형식은 동일한 `$HB*0A`다. **어느 방향이든
`$SACK`를 보내지 않는다.**

### 6.2 모드 판정

`last_hb_ms`를 마지막으로 **검증을 통과한** `$HB`를 받은 시각이라 하고,
`elapsed = now_ms - last_hb_ms`라 하면:

| 모드 | 조건 |
|---|---|
| `CONFIG` | `last_hb_ms`가 존재하고 `elapsed <= 3000` |
| `RUN` | `last_hb_ms`가 없거나(부팅 직후) `elapsed > 3000` |

🔴 **경계는 `>`이지 `>=`가 아니다.** 정확히 3000 ms 경과한 순간은 아직 CONFIG다.
경계를 명시하지 않으면 펌웨어와 호스트가 1 ms 차이로 서로 다른 모드를 표시하고,
그 차이가 `ERR,MODE`로 나타나 재현 안 되는 버그가 된다.

H723은 USB 연결을 감지할 수 없다(VBUS가 MCU에 배선되지 않음). 따라서 모드는
케이블이 아니라 하트비트로 판정한다. 설정을 바꾸려는 호스트는 `$HB`를 1 Hz로
보내야 한다.

### 6.3 🔴 체크섬을 통과한 뒤에만 하트비트 시각을 갱신한다

위 표의 "받고 있음"은 **완전한 프레임 검증을 통과한 `$HB`**를 뜻한다. 구체적으로:

1. 체크섬이 일치하고,
2. 파싱된 verb가 정확히 `HB`일 것

둘 다 만족한 뒤에만 하트비트 수신 시각을 갱신한다.

**왜 이걸 따로 못 박는가**: 기존 Q2 펌웨어(`host_link.c:183-187`)는 줄이
`$HB*` 로 시작하는지만 보고 체크섬 검증 **전에** 시각을 갱신한다. Q2에서는
`$HB`가 단순 링크 생존 신호라 무해했지만, 이 프로토콜에서 `$HB`는 **설정 변경을
여는 열쇠**다. 그 구현을 그대로 옮기면 잡음으로 깨진 프레임이 우연히 `$HB*`로
시작하기만 해도 설정 변경이 계속 허용된다. **회귀 시험을 필수로 둔다.**

수집과 전송은 **두 모드에서 모두 계속된다.** 모드가 바꾸는 것은 설정 변경
수락 여부뿐이다.

## 7. NDJSON

### 7.1 공통 필드

`seq`, `t`, `type`, `schema_ver`는 항상 포함된다. 필드 마스크로 끌 수 없다.

| 필드 | 타입 | 의미 |
|---|---|---|
| `schema_ver` | int | 항상 3 |
| `seq` | uint32 | **텔레메트리 전용** 시퀀스 (§7.1.1) |
| `t` | int64 | 밀리초 타임스탬프. 기준점은 `time_source`가 정한다 (§7.1.2) |
| `type` | str | 레코드 종류 |

#### 7.1.1 🔴 `seq`는 텔레메트리 전용이다

`seq`는 **텔레메트리 레코드에서만** 1씩 증가한다. 유실 검출이 그 존재 이유이고,
유실을 세야 하는 것은 연속 스트림뿐이다.

**명령 응답 레코드(`id`·`stat`·`cfg_value`·`cfg_item`·`cfg_field`·`cfg_end`)의
`seq`는 항상 0이며 이 시퀀스에 참여하지 않는다.** 요청이 있을 때만 나가므로
"빠졌다"는 개념이 성립하지 않고, 응답 자체가 `$SACK`로 보증된다.

수신측은 **`seq`를 텔레메트리 레코드에 대해서만 추적**한다. 명령 응답의 0을
시퀀스에 넣으면 매번 거대한 역방향 점프로 보여 통계가 망가진다.

#### 7.1.2 🔴 `t`의 기준점은 `time_source`가 정한다

`t`는 항상 밀리초 단위 정수지만 **기준점이 시간원에 따라 다르다.** 이 둘을
뭉뚱그리면 호스트가 부팅 후 경과시간을 UTC로 오해한다.

| `time_source` | `t`의 기준점 | 벽시계로 써도 되나 |
|---|---|---|
| `gnss` | UTC epoch (1970-01-01) | **예** |
| `gnss_nmea` | UTC epoch | 예 (PPS 없어 정밀도 낮음) |
| `host_clock` | UTC epoch (호스트가 주입) | 예 |
| `device_clock` | **부팅 시점** | **아니오** |

🔴 **`time_source`가 `device_clock`이면 `t`는 UTC가 아니라 부팅 후 경과 밀리초다.**
현재 단계는 GNSS가 구현 범위 밖이므로 **항상 `device_clock`**이며, 따라서 `t`는
단조 증가하는 uptime이다.

호스트는 다음을 지켜야 한다.

- `device_clock`인 `t`를 날짜·시각으로 표시하지 않는다
- 재부팅하면 `t`가 0으로 되돌아간다. **서로 다른 세션의 `t`를 비교하지 않는다**
- 저장 파일에는 `t`와 함께 `time_source`를 반드시 기록한다. 나중에 어느 것이
  벽시계였는지 구분할 수 없게 된다

GNSS 시간 엔진이 들어오면 `time_source`가 `gnss`로 바뀌고 `t`가 UTC epoch가
된다. **필드의 이름도 타입도 바뀌지 않는다** — 그래서 지금 이 구분을 못 박아 둔다.

### 7.2 텔레메트리 (`type` = `ain`)

```json
{"schema_ver":3,"seq":1234,"t":1772200855875,"type":"ain","connector_id":3,
 "raw":8388608,"ma":12.0041,"value":3.4210,"unit":"bar","status":0,
 "device_id":"1","time_source":"device_clock","time_quality":0,
 "capture_counter":123456789}
```

마스크는 설정 항목 **`tx.fields`** (u32 비트필드)로 바꾼다.

| 필드 | 비트 | 타입 | 기본 | 의미 |
|---|---|---|---|---|
| `device_id` | 0 | str≤15 | off | 보드 식별자 |
| `time_source` | 1 | str | on | `gnss`/`gnss_nmea`/`host_clock`/`device_clock` |
| `time_quality` | 2 | u8 | off | 0=미동기 |
| `raw` | 3 | **int32** | on | ADS1256 원시 카운트 (24bit 부호확장) |
| `ma` | 4 | float | on | 전류 환산 (mA) |
| `value` | 5 | float | on | 물리량 환산 |
| `unit` | 6 | str≤7 | off | 단위 문자열 |
| `status` | 7 | u16 | on | 0=정상 |
| `capture_counter` | 8 | **uint64** | off | 획득 순간 타이머 값 |
| `connector_id` | 9 | u16 | on | 3~9 (J3~J9) |

`float` 필드는 `tx.float_digits` 자릿수로 반올림된 십진수로 실린다.
`capture_counter`는 타이머 오버플로 확장을 담아야 하므로 **uint64**다 —
32비트로 두면 고해상도 타이머가 수 초 만에 되감긴다.

🔴 **`raw`가 원본이다.** `ma`·`value`는 편의용 파생값이며 `tx.float_digits`
자릿수로 반올림된다. 정밀도가 필요한 분석은 `raw`를 쓴다.

### 7.3 설정 카탈로그 (`$CFG,LIST` 응답)

한 항목당 한 줄, 마지막에 종료 줄.

```json
{"schema_ver":3,"seq":0,"t":0,"type":"cfg_item","key":"tx.period_ms","grp":"tx","vtype":"u16","min":10,"max":10000,"default":100,"cur":100,"unit":"ms","ro":false,"label":"전송 주기"}
{"schema_ver":3,"seq":0,"t":0,"type":"cfg_field","bit":3,"name":"raw","default":true,"label":"ADS1256 원시 카운트"}
{"schema_ver":3,"seq":0,"t":0,"type":"cfg_end","count":42}
```

`vtype`: `bool` | `u8` | `u16` | `u32` | `f32` | `str` | `enum`
`enum`인 경우 `choices` 배열이 함께 온다.
`ro`가 `true`면 변경 불가이며 `note`에 이유가 담긴다.
`cfg_end`의 `count`는 **`cfg_item` + `cfg_field` 합계**다. 수신측은 이를
대조해 전송이 중간에 잘렸는지 판정한다.

**호스트는 설정 항목을 하드코딩하지 않는다.** 이 응답만으로 화면을 구성한다.

### 7.4 상태 (`$STAT` 응답)

```json
{"schema_ver":3,"seq":0,"t":123456,"type":"stat","mode":"CONFIG",
 "fw":"0.1.0","board_rev":"2.0",
 "time_source":"device_clock","time_quality":0,"uptime_ms":123456,
 "rails":{"v24":false,"v14v9":false,"v5":true},
 "queues":[{"ch":0,"depth":0,"peak":3,"drops":0}]}
```

🔴 **`time_source`는 `$STAT`이 `t`의 기준점을 알려주는 자리다.** §7.1.2대로 `t`는
시간원에 따라 UTC epoch이거나 부팅 후 경과 ms다. 텔레메트리는 필드 마스크로
`time_source`를 실을 수 있지만 **명령 응답 레코드에는 그 자리가 없다.** 호스트는
연결 직후 `$STAT`을 한 번 물어 기준점을 확인해야 한다.

이게 없으면 `t: 0`을 보고 1970년인지 방금 부팅한 것인지 구분할 수 없다.

`rails` 값은 **명령 상태**이지 실측이 아니다. 피드백 회로가 없으므로 호스트는
`정상 ON`이 아니라 `ON 명령됨`으로 표시해야 한다.

## 8. 호환성

`schema_ver`가 올라가면 수신측은 모르는 필드를 무시하고 없는 필드는 기본값으로
채운다. 필드 삭제는 major 버전 상승을 요구한다.
````

- [ ] **Step 2: 체크섬 벡터 손으로 검증**

Run:
```bash
python -c "
for s in ('HB','ID'):
    cs=0
    for ch in s: cs ^= ord(ch)
    print(f'\${s}*{cs:02X}')
"
```
Expected:
```
$HB*0A
$ID*0D
```

규격서의 검증 벡터와 일치해야 한다. 다르면 규격서를 고친다.

> **참고:** Q2 `host_link.h` 주석에는 `$HB*0F`로 적혀 있으나 NMEA XOR 계산으로는
> `0x0A`다. Q2 주석이 오래됐거나 다른 계산을 쓴 것으로 보인다. **우리는 위 검증
> 벡터를 기준으로 하고 펌웨어도 여기에 맞춘다.**

- [ ] **Step 3: 커밋**

```bash
git add protocol/specification.md
git commit -m "docs(proto): 시리얼 프로토콜 규격 v3 작성

펌웨어와 호스트가 함께 참조하는 계약 문서다. 구현보다 먼저 못 박아
양쪽이 서로 다른 가정을 하지 않게 한다.

체크섬은 NMEA XOR 방식으로 확정하고 \$HB*0A / \$ID*0D 검증 벡터를 넣었다.
Q2 주석의 \$HB*0F 와는 다르며 우리 기준은 계산값이다."
```

---

## Task 3: 체크섬과 명령 프레이밍

**Files:**
- Create: `host/core/errors.py`
- Create: `host/core/framing.py`
- Test: `host/tests/test_framing.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `xor_checksum(payload: str) -> int`
  - `build_line(payload: str) -> str` — `"$<payload>*<CS>\r\n"`
  - `build_command(verb: str, *args: str) -> str`
  - `parse_line(line: str) -> Command` — 실패 시 `ChecksumError` / `MalformedLineError`
  - `Command(verb: str, args: tuple[str, ...])` — frozen dataclass
  - `Reason` — 거부 사유 상수 클래스
  - `ProtocolError`, `ChecksumError`, `MalformedLineError`

- [ ] **Step 1: 실패하는 테스트 작성**

`host/tests/test_framing.py`:
```python
import pytest

from host.core.framing import (
    Command,
    build_command,
    build_line,
    parse_line,
    xor_checksum,
)
from host.core.errors import ChecksumError, MalformedLineError


def test_xor_checksum_known_vectors():
    """규격서 protocol/specification.md §3 의 검증 벡터."""
    assert xor_checksum("HB") == 0x0A
    assert xor_checksum("ID") == 0x0D


def test_build_line_appends_checksum_and_crlf():
    assert build_line("HB") == "$HB*0A\r\n"


def test_build_command_joins_args_with_comma():
    line = build_command("CFG", "GET", "tx.period_ms")
    assert line.startswith("$CFG,GET,tx.period_ms*")
    assert line.endswith("\r\n")


def test_parse_line_returns_verb_and_args():
    cmd = parse_line(build_command("CFG", "SET", "tx.period_ms", "250"))
    assert cmd == Command(verb="CFG", args=("SET", "tx.period_ms", "250"))


def test_parse_line_accepts_bare_lf():
    """수신측은 \\n 만 와도 받아들인다 (규격 §2)."""
    assert parse_line("$HB*0A\n") == Command(verb="HB", args=())


def test_parse_line_rejects_bad_checksum():
    with pytest.raises(ChecksumError):
        parse_line("$HB*FF\r\n")


def test_parse_line_rejects_missing_dollar():
    with pytest.raises(MalformedLineError):
        parse_line("HB*0A\r\n")


def test_parse_line_rejects_missing_star():
    with pytest.raises(MalformedLineError):
        parse_line("$HB0A\r\n")


def test_roundtrip_preserves_args():
    original = ("SET", "ain0.unit", "bar")
    cmd = parse_line(build_command("CFG", *original))
    assert cmd.args == original


def test_checksum_matches_firmware_byte_basis():
    """펌웨어는 바이트를 XOR 한다. 정의를 바이트로 맞춰 둔다.

    전선 위 값은 ASCII 만 허용되므로(설정 계층이 강제한다) 실제로는
    ord() 계산과 결과가 같다. 그래도 바이트로 정의하는 이유는, 언젠가
    non-ASCII 가 새어 들어와도 체크섬이 3자리로 터지는 대신 조용한
    불일치로 잡히게 하기 위해서다.
    """
    payload = "CFG,SET,ain0.unit,degC"
    expected = 0
    for b in payload.encode("utf-8"):
        expected ^= b
    assert xor_checksum(payload) == expected


def test_checksum_stays_one_byte_even_for_non_ascii():
    """프레이밍은 전송 계층이다 — 무엇이 들어와도 형식을 깨뜨리지 않는다.

    ASCII 강제는 설정 계층(_coerce)의 책임이고 여기서 하지 않는다.
    이 시험은 그 방어선이 뚫렸을 때도 프레임이 살아남는지만 본다.
    """
    for payload in ("CFG,SET,ain0.unit,℃", "CFG,SET,ain0.unit,바"):
        assert xor_checksum(payload) <= 0xFF
        line = build_line(payload)
        star = line.rfind("*")
        assert len(line[star + 1 :].strip()) == 2


def test_lowercase_checksum_is_accepted():
    """수신은 관대하게, 송신은 엄격하게.

    규격은 대문자 2자리로 '만든다'고 정하지만, 수신측이 소문자를 거부할
    이유는 없다. 이 관용을 시험으로 못 박아 나중에 바뀌지 않게 한다.
    """
    assert parse_line("$HB*0a\r\n") == Command(verb="HB", args=())


def test_build_line_always_emits_uppercase():
    """송신은 규격대로 대문자다."""
    line = build_line("HB")
    assert line == "$HB*0A\r\n"


def test_parse_line_rejects_empty_payload():
    """'$*00' 은 체크섬이 맞아도 verb 가 없어 의미가 없다."""
    with pytest.raises(MalformedLineError):
        parse_line("$*00\r\n")


def test_build_line_refuses_control_characters():
    """🔴 한 번 호출 = 한 줄. 프레이밍 계층이 이를 보장한다."""
    for bad in ("a\rb", "a\nb", "a\x00b", "a\x1bb", "a\x7fb"):
        with pytest.raises(MalformedLineError):
            build_line(bad)


def test_build_command_blocks_injection_payload():
    """🔴 실제 주입 페이로드가 조립 단계에서 막힌다.

    이 값을 허용하면 조립 결과가 세 줄로 쪼개지고 가운데 줄
    '$HB*0A' 가 체크섬까지 유효한 하트비트가 되어 CONFIG 모드를 연다.

    설정 계층 검증만으로는 못 막는다 — 악성 값은 보드의 검증에 닿기 전에
    줄이 쪼개지므로 보드가 그것을 하나의 값으로 본 적이 없다.
    """
    with pytest.raises(MalformedLineError):
        build_command("CFG", "SET", "dev.id", "x\r\n$HB*0A\r\n")


def test_build_line_still_accepts_normal_payloads():
    """방어가 정상 경로를 막지 않는다."""
    assert build_line("HB") == "$HB*0A\r\n"
    assert build_command("CFG", "SET", "ain0.unit", "degC").startswith(
        "$CFG,SET,ain0.unit,degC*"
    )
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `python -m pytest host/tests/test_framing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'host.core.framing'`

- [ ] **Step 3: `errors.py` 작성**

`host/core/errors.py`:
```python
"""프로토콜 예외와 거부 사유 상수."""


class ProtocolError(Exception):
    """프로토콜 계층의 모든 예외의 기반."""


class ChecksumError(ProtocolError):
    """XOR 체크섬 불일치."""


class MalformedLineError(ProtocolError):
    """$ ... * 형태를 갖추지 못한 줄."""


class ConfigError(ProtocolError):
    """설정 요청 거부. reason 은 Reason 의 상수 중 하나."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}{': ' + detail if detail else ''}")
        self.reason = reason
        self.detail = detail


class Reason:
    """규격 §5 의 거부 사유. 이 8개가 전부다."""

    RANGE = "RANGE"
    UNKNOWN_KEY = "UNKNOWN_KEY"
    READONLY = "READONLY"
    INTERLOCK = "INTERLOCK"
    MODE = "MODE"
    CHECKSUM = "CHECKSUM"
    BUSY = "BUSY"
    #: 값은 범위 안이지만 조합이 물리적으로 달성 불가 (설계 §6.4)
    CAPACITY = "CAPACITY"

    ALL = (RANGE, UNKNOWN_KEY, READONLY, INTERLOCK, MODE, CHECKSUM, BUSY,
           CAPACITY)
```

- [ ] **Step 4: `framing.py` 작성**

`host/core/framing.py`:
```python
"""NMEA 방식 $ 줄의 조립과 파싱.

체크섬은 $ 와 * 사이 문자 전부의 XOR, 대문자 2자리 16진수다.
규격: protocol/specification.md §3
"""

from dataclasses import dataclass

from host.core.errors import ChecksumError, MalformedLineError

LINE_END = "\r\n"


@dataclass(frozen=True)
class Command:
    """파싱된 $ 줄. verb 는 첫 토큰, args 는 나머지."""

    verb: str
    args: tuple[str, ...] = ()


def xor_checksum(payload: str) -> int:
    """payload 를 UTF-8 로 인코딩한 **바이트**들의 XOR.

    전선 위 값은 전부 ASCII 이므로(§3) 실제로는 `ord(ch)` 와 결과가 같다.
    그래도 바이트로 정의하는 이유는 펌웨어가 바이트를 XOR 하기 때문이다 —
    정의를 일치시켜 두면 나중에 누가 non-ASCII 를 흘려보내도 체크섬이
    3자리로 터지는 대신 조용히 불일치로 잡힌다.
    """
    cs = 0
    for b in payload.encode("utf-8"):
        cs ^= b
    return cs


def build_line(payload: str) -> str:
    """payload 를 체크섬과 줄끝이 붙은 완성된 줄로 만든다.

    🔴 **한 번 호출하면 정확히 한 줄이 나온다는 것을 여기서 보장한다.**

    payload 에 제어문자가 섞이면 이 함수가 만든 결과가 전송 도중 여러 줄로
    쪼개진다. 그러면 그 조각 하나가 완결된 명령이 될 수 있다:

        build_command("CFG", "SET", "dev.id", "x\\r\\n$HB*0A\\r\\n")
        → '$CFG,SET,dev.id,x\\r\\n$HB*0A\\r\\n*75\\r\\n'
        → 세 줄로 쪼개짐: ['$CFG,SET,dev.id,x', '$HB*0A', '*75']
                                                 ^^^^^^^^ 유효한 하트비트

    가운데 줄은 체크섬까지 맞는 진짜 `$HB` 라서 보드가 CONFIG 모드를 연다.
    설정값 하나로 명령이 주입된다.

    설정 계층에도 같은 검사가 있지만 그것만으로는 부족하다. 위 경로에서
    악성 값은 **보드의 설정 검증에 도달하기 전에** 줄이 쪼개지므로, 보드는
    그것을 하나의 값으로 본 적이 없다. 프레이밍 계층에서 막아야 모든
    호출자가 보호된다.

    Raises:
        MalformedLineError: payload 에 제어문자가 있는 경우.
    """
    bad = [c for c in payload if ord(c) < 0x20 or ord(c) == 0x7F]
    if bad:
        raise MalformedLineError(f"제어문자는 줄을 쪼갠다: {bad!r}")
    return f"${payload}*{xor_checksum(payload):02X}{LINE_END}"


def build_command(verb: str, *args: str) -> str:
    """verb 와 인자를 쉼표로 이어 완성된 줄로 만든다."""
    payload = ",".join((verb, *args))
    return build_line(payload)


def parse_line(line: str) -> Command:
    """완성된 줄을 Command 로 파싱한다.

    Raises:
        MalformedLineError: $ 또는 * 가 없는 줄.
        ChecksumError: 체크섬 불일치.
    """
    stripped = line.strip()
    if not stripped.startswith("$"):
        raise MalformedLineError(f"'$' 로 시작하지 않음: {line!r}")

    body = stripped[1:]
    star = body.rfind("*")
    if star < 0:
        raise MalformedLineError(f"'*' 없음: {line!r}")

    payload = body[:star]
    if not payload:
        raise MalformedLineError(f"빈 payload: {line!r}")
    given = body[star + 1 :]
    expected = f"{xor_checksum(payload):02X}"
    if given.upper() != expected:
        raise ChecksumError(f"체크섬 불일치: 받음 {given!r}, 계산 {expected!r}")

    tokens = payload.split(",")
    return Command(verb=tokens[0], args=tuple(tokens[1:]))
```

- [ ] **Step 5: 테스트 실행해 통과 확인**

Run: `python -m pytest host/tests/test_framing.py -v`
Expected: PASS (9 passed)

- [ ] **Step 6: 커밋**

```bash
git add host/core/errors.py host/core/framing.py host/tests/test_framing.py
git commit -m "feat(proto): XOR 체크섬과 \$ 명령 프레이밍

규격 §3 의 검증 벡터(\$HB*0A, \$ID*0D)를 테스트에 박아 펌웨어 구현과
대조할 수 있게 했다. 거부 사유 7개는 Reason 상수로 모아 문자열 오타를 막는다."
```

---

## Task 4: NDJSON 레코드 파싱과 seq 누락 검출

**Files:**
- Create: `host/core/records.py`
- Test: `host/tests/test_records.py`

**Interfaces:**
- Consumes: `host.core.errors.ProtocolError`
- Produces:
  - `parse_record(line: str) -> dict` — `schema_ver` 검증 포함
  - `SeqTracker` — `.observe(seq: int) -> int` (이번에 발견한 누락 개수), `.missing_total`, `.received_total`
  - `SCHEMA_VER = 3`

seq 누락 검출은 이 프로젝트의 핵심 요구사항이다. Q2에서 STM32→Jetson 구간
2% 유실이 실측됐으므로(스펙 §11.3) 호스트가 이를 자동으로 세야 한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`host/tests/test_records.py`:
```python
import pytest

from host.core.errors import ProtocolError
from host.core.records import SCHEMA_VER, SeqTracker, parse_record


def _line(**fields) -> str:
    import json

    base = {"schema_ver": SCHEMA_VER, "seq": 1, "t": 1772200855875, "type": "ain"}
    base.update(fields)
    return json.dumps(base)


def test_parse_record_returns_dict():
    rec = parse_record(_line(raw=8388608))
    assert rec["type"] == "ain"
    assert rec["raw"] == 8388608


def test_parse_record_rejects_wrong_schema_ver():
    with pytest.raises(ProtocolError):
        parse_record('{"schema_ver":2,"seq":1,"t":0,"type":"ain"}')


def test_parse_record_rejects_missing_mandatory_field():
    """seq/t/type 는 마스크로 끌 수 없는 필수 필드다."""
    with pytest.raises(ProtocolError):
        parse_record('{"schema_ver":3,"t":0,"type":"ain"}')


def test_parse_record_rejects_broken_json():
    with pytest.raises(ProtocolError):
        parse_record('{"schema_ver":3,"seq":1,')


def test_parse_record_rejects_non_integer_seq():
    """펌웨어 직렬화 버그로 seq 가 문자열로 나올 수 있다.

    통과시키면 나중에 observe() 안에서 TypeError 가 터져 수집 루프가
    통째로 죽는다. ProtocolError 로 잡아야 그 줄만 버리고 계속할 수 있다.
    """
    with pytest.raises(ProtocolError):
        parse_record('{"schema_ver":3,"seq":"1","t":0,"type":"ain"}')


def test_parse_record_rejects_bool_seq():
    """bool 은 int 의 서브클래스라 isinstance 만으로는 안 걸린다."""
    with pytest.raises(ProtocolError):
        parse_record('{"schema_ver":3,"seq":true,"t":0,"type":"ain"}')


def test_parse_record_rejects_seq_outside_uint32():
    """음수나 uint32 를 넘는 값은 모듈로 연산을 조용히 망가뜨린다."""
    for bad in (-1, 1 << 32):
        with pytest.raises(ProtocolError):
            parse_record(f'{{"schema_ver":3,"seq":{bad},"t":0,"type":"ain"}}')


def test_parse_record_rejects_non_integer_t():
    with pytest.raises(ProtocolError):
        parse_record('{"schema_ver":3,"seq":1,"t":"now","type":"ain"}')


def test_parse_record_rejects_non_string_type():
    with pytest.raises(ProtocolError):
        parse_record('{"schema_ver":3,"seq":1,"t":0,"type":7}')


def test_seq_tracker_counts_no_loss():
    t = SeqTracker()
    for s in (10, 11, 12):
        assert t.observe(s) == 0
    assert t.missing_total == 0
    assert t.received_total == 3


def test_seq_tracker_detects_gap():
    t = SeqTracker()
    t.observe(10)
    assert t.observe(14) == 3          # 11, 12, 13 유실
    assert t.missing_total == 3


def test_seq_tracker_ignores_duplicate_and_reorder():
    """중복이나 역순은 유실이 아니다. 0 을 반환하고 누계도 늘지 않는다."""
    t = SeqTracker()
    t.observe(10)
    assert t.observe(10) == 0
    assert t.observe(9) == 0
    assert t.missing_total == 0


def test_seq_tracker_handles_uint32_wrap():
    """seq 는 uint32 라 4294967295 다음은 0 이다. 이를 유실로 세면 안 된다."""
    t = SeqTracker()
    t.observe(4294967295)
    assert t.observe(0) == 0
    assert t.missing_total == 0


def test_seq_tracker_reset_on_reconnect():
    t = SeqTracker()
    t.observe(100)
    t.reset()
    assert t.observe(5) == 0           # 재연결 후 첫 값은 기준점일 뿐
    assert t.missing_total == 0


def test_delta_of_exactly_wrap_tolerance_is_not_counted_as_loss():
    """🔴 delta 가 정확히 2^31 이면 전진 21억인지 후진 21억인지 알 수 없다.

    현재 경계가 `> _WRAP_TOLERANCE` 라 정확히 2^31 은 "전진" 으로 분류되고
    유실 2,147,483,647 개로 기록된다. 그 한 줄이 세션 전체 통계를 망친다.
    """
    from host.core.records import _WRAP_TOLERANCE

    t = SeqTracker()
    t.observe(0)
    assert t.observe(_WRAP_TOLERANCE) == 0
    assert t.missing_total == 0
    assert t.discontinuity_count == 1


def test_implausibly_large_gap_is_discontinuity_not_loss():
    """유실이라기엔 물리적으로 불가능한 크기는 통계에서 뺀다."""
    from host.core.records import MAX_PLAUSIBLE_GAP

    t = SeqTracker()
    t.observe(0)
    assert t.observe(MAX_PLAUSIBLE_GAP + 100) == 0
    assert t.missing_total == 0
    assert t.discontinuity_count == 1
    # 기준점은 옮겨졌으므로 이후 측정은 정상 동작한다
    assert t.observe(MAX_PLAUSIBLE_GAP + 103) == 2


def test_plausible_gap_is_still_counted():
    """경계 바로 아래는 정상적으로 유실로 센다."""
    t = SeqTracker()
    t.observe(0)
    assert t.observe(1000) == 999
    assert t.missing_total == 999
    assert t.discontinuity_count == 0


def test_three_duplicates_are_not_mistaken_for_reboot():
    """🔴 같은 값이 세 번 오는 것은 재전송이지 재시작이 아니다.

    구분하지 않으면 중복 3회가 재부팅으로 오판되어 resync_count 와
    유실률이 둘 다 신뢰할 수 없게 된다.
    """
    t = SeqTracker()
    t.observe(10)
    for _ in range(5):
        assert t.observe(10) == 0
    assert t.resync_count == 0
    assert t.duplicate_count == 5
    assert t.missing_total == 0
    # 이어지는 전진은 정상 측정된다
    assert t.observe(13) == 2


def test_non_advancing_backward_values_do_not_resync():
    """재시작은 값이 전진하는 형태다. 제자리 역방향은 잡음이다."""
    t = SeqTracker()
    t.observe(500)
    for _ in range(5):
        t.observe(400)          # 같은 자리로 계속 뒤로
    assert t.resync_count == 0


def test_seq_tracker_resyncs_after_unsignaled_board_reboot():
    """🔴 보드가 재부팅해 seq 가 0 부터 다시 시작해도 측정이 살아난다.

    이 방어가 없으면 _last=500 인 채로 모든 후속 값이 역순으로 분류되어
    남은 세션 내내 유실이 0 으로 보고된다. 유실 측정이 존재 이유인 모듈에서
    가장 나쁜 실패 방식이다.
    """
    from host.core.records import RESYNC_AFTER

    t = SeqTracker()
    t.observe(500)

    for s in range(RESYNC_AFTER):          # 재부팅 후 0, 1, 2 …
        assert t.observe(s) == 0
    assert t.resync_count == 1

    assert t.observe(RESYNC_AFTER) == 0            # 이어서 정상 전진
    assert t.observe(RESYNC_AFTER + 2) == 1        # 유실 검출이 되살아났다
    assert t.missing_total == 1


def test_single_duplicate_does_not_trigger_resync():
    """중복 하나로는 재동기화하지 않는다. 전진하면 카운터가 풀린다."""
    t = SeqTracker()
    t.observe(10)
    t.observe(10)                          # 중복
    t.observe(11)                          # 전진 → backward_run 리셋
    t.observe(11)
    assert t.resync_count == 0
    assert t.missing_total == 0


def test_is_telemetry_distinguishes_command_responses():
    """🔴 seq 는 텔레메트리 전용이다 (규격 §7.1.1).

    명령 응답 레코드의 seq 는 항상 0 이다. 그 0 을 시퀀스에 넣으면 매번
    거대한 역방향 점프로 보여 유실 통계가 망가진다.
    """
    from host.core.records import is_telemetry

    assert is_telemetry({"type": "ain"}) is True
    for t in ("id", "stat", "cfg_value", "cfg_item", "cfg_field", "cfg_end"):
        assert is_telemetry({"type": t}) is False, t


def test_seq_tracker_is_not_polluted_by_command_responses():
    """명령 응답을 걸러내면 텔레메트리 시퀀스가 온전히 유지된다."""
    from host.core.records import is_telemetry

    t = SeqTracker()
    stream = [
        {"type": "ain", "seq": 10},
        {"type": "cfg_value", "seq": 0},   # 응답이 중간에 끼어든다
        {"type": "ain", "seq": 11},
    ]
    for rec in stream:
        if is_telemetry(rec):
            t.observe(rec["seq"])
    assert t.missing_total == 0
    assert t.received_total == 2
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `python -m pytest host/tests/test_records.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'host.core.records'`

- [ ] **Step 3: `records.py` 작성**

`host/core/records.py`:
```python
"""NDJSON 텔레메트리 레코드 파싱과 seq 누락 추적.

규격: protocol/specification.md §7
"""

import json

from host.core.errors import ProtocolError

SCHEMA_VER = 3

#: 필드 마스크로 끌 수 없는 필드 (규격 §7.1)
MANDATORY_FIELDS = ("schema_ver", "seq", "t", "type")

#: seq 시퀀스에 참여하지 않는 레코드 (규격 §7.1.1).
#: 요청이 있을 때만 나가므로 "빠졌다"는 개념이 성립하지 않고,
#: 응답 자체가 $SACK 로 보증된다. seq 는 항상 0 이다.
COMMAND_RESPONSE_TYPES = frozenset(
    {"id", "stat", "cfg_value", "cfg_item", "cfg_field", "cfg_end"}
)

#: seq 는 uint32
SEQ_MODULO = 1 << 32

#: 이 값보다 큰 역방향 점프는 되감기(wrap)가 아니라 재시작으로 본다
_WRAP_TOLERANCE = 1 << 31

#: 이만큼 연속으로 "전진하지 않는" 관찰이 이어지면 스트림이 재시작된 것으로 본다.
#:
#: 🔴 이게 없으면 보드가 재부팅한 뒤 유실 측정이 영구히 침묵한다.
#: _last=500 인 상태에서 보드가 재부팅해 seq 가 0 부터 다시 시작하면, 이후
#: 모든 값이 "역순 도착"으로 분류되고 _last 는 갱신되지 않는다. 새 카운터가
#: 500 을 다시 넘을 때까지 — 사실상 세션이 끝날 때까지 — 유실이 0 으로
#: 보고된다. 유실 측정이 존재 이유인 모듈에서 가장 나쁜 실패 방식이다.
#:
#: 3 으로 잡는 근거: 시리얼 링크에는 순서 뒤바뀜이 없다(바이트 스트림이다).
#: 뒤로 간 값이 연달아 세 번 나오면 재전송이 아니라 재시작이다.
#:
#: 🔴 단, **완전한 중복(delta == 0)은 이 카운터에 넣지 않는다.** 같은 값이
#: 세 번 오는 것은 재전송이지 재시작이 아니다. 재시작은 0,1,2 처럼 값이
#: 전진하는 형태로 나타난다. 이를 구분하지 않으면 중복 3회가 재부팅으로
#: 오판되어 resync_count 와 유실률이 둘 다 신뢰할 수 없게 된다.
RESYNC_AFTER = 3

#: 이보다 큰 유실은 물리적으로 불가능하다고 보고 통계에 넣지 않는다.
#:
#: 🔴 delta 가 정확히 _WRAP_TOLERANCE(2^31)면 앞으로 21억 개를 잃은 것인지
#: 뒤로 21억 간 것인지 수학적으로 구분할 수 없다. 그런데 현재 규칙은
#: `delta > _WRAP_TOLERANCE` 이므로 정확히 2^31 은 "전진"으로 분류되어
#: **유실 2,147,483,647 개**로 기록된다. 그 한 줄이 세션 전체 통계를 망친다.
#:
#: 7채널 × 100 Hz = 700 레코드/초 기준으로 2^24 는 약 6.6 시간 분량이다.
#: 그보다 큰 값은 유실이 아니라 시퀀스 불연속(재시작·링크 재연결)으로 본다.
MAX_PLAUSIBLE_GAP = 1 << 24


def parse_record(line: str) -> dict:
    """NDJSON 한 줄을 dict 로 파싱한다.

    Raises:
        ProtocolError: JSON 이 깨졌거나, schema_ver 가 다르거나,
            필수 필드가 빠진 경우.
    """
    try:
        rec = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"JSON 파싱 실패: {exc}") from exc

    if not isinstance(rec, dict):
        raise ProtocolError(f"객체가 아님: {line!r}")

    missing = [f for f in MANDATORY_FIELDS if f not in rec]
    if missing:
        raise ProtocolError(f"필수 필드 누락: {missing}")

    if rec["schema_ver"] != SCHEMA_VER:
        raise ProtocolError(
            f"schema_ver 불일치: 받음 {rec['schema_ver']}, 기대 {SCHEMA_VER}"
        )

    # 🔴 필수 필드의 타입까지 검사한다. 존재만 확인하면 부족하다.
    #
    # 펌웨어의 직렬화 버그로 seq 가 문자열로 나오는 경우가 실제로 있다.
    # 그러면 parse_record 는 통과하고, 나중에 SeqTracker.observe() 안에서
    # TypeError 가 터진다. ProtocolError 가 아니라서 호출측이 "이 줄만 버리고
    # 계속" 처리를 못 하고 수집 루프 전체가 죽는다. 장시간 무인 수집이
    # 목적인데 한 줄 때문에 세션을 잃는다.
    #
    # bool 은 int 의 서브클래스라 따로 걸러야 한다.
    seq = rec["seq"]
    if isinstance(seq, bool) or not isinstance(seq, int):
        raise ProtocolError(f"seq 가 정수가 아님: {seq!r}")
    if not 0 <= seq < SEQ_MODULO:
        raise ProtocolError(f"seq 가 uint32 범위 밖: {seq}")

    t = rec["t"]
    if isinstance(t, bool) or not isinstance(t, int):
        raise ProtocolError(f"t 가 정수가 아님: {t!r}")

    if not isinstance(rec["type"], str):
        raise ProtocolError(f"type 이 문자열이 아님: {rec['type']!r}")

    return rec


def is_telemetry(rec: dict) -> bool:
    """이 레코드가 seq 시퀀스에 참여하는가 (규격 §7.1.1).

    명령 응답의 seq(항상 0)를 SeqTracker 에 넣으면 매번 거대한 역방향
    점프로 보여 유실 통계가 망가진다. 호출측은 observe() 앞에 이걸 건다.
    """
    return rec.get("type") not in COMMAND_RESPONSE_TYPES


class SeqTracker:
    """seq 연속성을 감시해 유실 개수를 센다.

    중복·역순은 유실로 세지 않는다. 링크 위에서 재전송이나 순서 뒤바뀜이
    일어나도 유실 통계가 오염되지 않게 하기 위해서다.
    """

    def __init__(self) -> None:
        self._last: int | None = None
        self._backward_run = 0
        #: 연속 역방향 관찰 중 마지막으로 본 seq. 재시작(전진하는 역방향)과
        #: 잡음(전진하지 않는 역방향)을 구분하는 데 쓴다.
        self._restart_from: int | None = None
        self.missing_total = 0
        self.received_total = 0
        #: 보드 재시작을 감지해 기준점을 다시 잡은 횟수.
        #: 0 이 아니면 그 사이 구간의 유실은 셀 수 없었다는 뜻이다.
        self.resync_count = 0
        #: 같은 seq 를 다시 받은 횟수 (재전송). 유실이 아니다.
        self.duplicate_count = 0
        #: 유실이라기엔 너무 큰 점프를 만난 횟수. 통계에서 제외했다는 표시다.
        self.discontinuity_count = 0

    def reset(self) -> None:
        """재연결 시 호출. 다음 seq 를 새 기준점으로 삼는다."""
        self._last = None
        self._backward_run = 0
        self._restart_from = None

    def observe(self, seq: int) -> int:
        """seq 하나를 관찰하고 이번에 발견한 누락 개수를 반환한다."""
        self.received_total += 1

        if self._last is None:
            self._last = seq
            return 0

        delta = (seq - self._last) % SEQ_MODULO

        if delta == 0:
            # 완전한 중복. 재전송이지 재시작이 아니므로 재시작 카운터를
            # 건드리지 않는다 — 안 그러면 중복 3회가 재부팅으로 오판된다.
            self.duplicate_count += 1
            return 0

        if delta > _WRAP_TOLERANCE:
            # 뒤로 갔다. 재시작 후보지만 아직 확정은 아니다.
            #
            # 재시작은 0,1,2 처럼 값이 **전진하는** 형태로 나타난다.
            # 앞선 역방향 값보다 크지 않으면 연속 재시작 신호가 아니므로
            # 카운터를 다시 세운다.
            if self._restart_from is None or seq > self._restart_from:
                self._backward_run += 1
            else:
                self._backward_run = 1
            self._restart_from = seq

            if self._backward_run >= RESYNC_AFTER:
                self._last = seq
                self._backward_run = 0
                self._restart_from = None
                self.resync_count += 1
            return 0

        # 전진했다.
        self._backward_run = 0
        self._restart_from = None
        missing = delta - 1

        if missing > MAX_PLAUSIBLE_GAP:
            # 물리적으로 불가능한 크기다. 유실이 아니라 시퀀스 불연속으로
            # 보고 기준점만 옮긴다. 이걸 유실로 세면 한 줄이 세션 전체
            # 통계를 망친다(MAX_PLAUSIBLE_GAP 주석 참조).
            self.discontinuity_count += 1
            self._last = seq
            return 0

        self._last = seq
        self.missing_total += missing
        return missing
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `python -m pytest host/tests/test_records.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: 커밋**

```bash
git add host/core/records.py host/tests/test_records.py
git commit -m "feat(proto): NDJSON 레코드 파싱과 seq 누락 검출

Q2 에서 STM32→Jetson 구간 2% 유실이 실측됐으므로 호스트가 유실을
자동으로 세게 한다.

중복·역순은 유실로 세지 않는다. 재전송이나 순서 뒤바뀜이 유실 통계를
오염시키면 실제 물리계층 문제를 판단할 수 없게 된다.
uint32 되감기도 유실이 아니다."
```

---

## Task 5: 설정 스키마 파싱과 값 검증

**Files:**
- Create: `host/core/config_schema.py`
- Test: `host/tests/test_config_schema.py`

**Interfaces:**
- Consumes: `host.core.errors.ConfigError`, `Reason`, `host.core.records.parse_record`
- Produces:
  - `ConfigItem` — frozen dataclass: `key, group, vtype, default, current, minimum, maximum, unit, readonly, label, note, choices`
  - `FieldBit` — frozen dataclass: `bit, name, default, label`
  - `ConfigSchema` — `.items: dict[str, ConfigItem]`, `.fields: dict[int, FieldBit]`, `.groups() -> list[str]`, `.validate(key, raw) -> object`
  - `parse_catalog(lines: Iterable[str]) -> ConfigSchema`

GUI가 설정 항목을 하드코딩하지 않게 만드는 층이다. 스펙 §5.4·§8.2의 근거.

- [ ] **Step 1: 실패하는 테스트 작성**

`host/tests/test_config_schema.py`:
```python
import json

import pytest

from host.core.config_schema import ConfigSchema, parse_catalog
from host.core.errors import ConfigError, Reason


def _item(**fields) -> str:
    base = {"schema_ver": 3, "seq": 0, "t": 0, "type": "cfg_item"}
    base.update(fields)
    return json.dumps(base)


def _field(**fields) -> str:
    base = {"schema_ver": 3, "seq": 0, "t": 0, "type": "cfg_field"}
    base.update(fields)
    return json.dumps(base)


def _end(count: int) -> str:
    return json.dumps(
        {"schema_ver": 3, "seq": 0, "t": 0, "type": "cfg_end", "count": count}
    )


CATALOG = [
    _item(key="tx.period_ms", grp="tx", vtype="u16", min=10, max=10000,
          default=100, cur=100, unit="ms", ro=False, label="전송 주기"),
    _item(key="pwr.5v", grp="pwr", vtype="bool", default=True, cur=True,
          ro=True, label="5V 레일", note="쿨링 팬 상시 동작 요구사항"),
    _item(key="adc.pga", grp="adc", vtype="enum",
          choices=[1, 2, 4, 8, 16, 32, 64], default=1, cur=1, ro=False,
          label="PGA"),
    _item(key="ain0.unit", grp="ain", vtype="str", max=7, default="",
          cur="bar", ro=False, label="단위"),
    _item(key="ain0.scale", grp="ain", vtype="f32", default=1.0, cur=1.0,
          ro=False, label="스케일"),
    _field(bit=3, name="raw", default=True, label="원시 카운트"),
    _end(6),                    # cfg_item 5 + cfg_field 1
]


def test_parse_catalog_collects_items_and_fields():
    schema = parse_catalog(CATALOG)
    assert set(schema.items) == {
        "tx.period_ms", "pwr.5v", "adc.pga", "ain0.unit", "ain0.scale",
    }
    assert schema.fields[3].name == "raw"


def test_parse_catalog_checks_declared_count():
    """cfg_end 의 count 가 실제 항목 수와 다르면 전송이 잘린 것이다."""
    with pytest.raises(ConfigError):
        parse_catalog([CATALOG[0], _end(99)])


def test_groups_are_ordered_by_first_appearance():
    schema = parse_catalog(CATALOG)
    assert schema.groups() == ["tx", "pwr", "adc", "ain"]


def test_validate_u16_in_range():
    schema = parse_catalog(CATALOG)
    assert schema.validate("tx.period_ms", "250") == 250


def test_validate_u16_below_min_raises_range():
    schema = parse_catalog(CATALOG)
    with pytest.raises(ConfigError) as exc:
        schema.validate("tx.period_ms", "5")
    assert exc.value.reason == Reason.RANGE


def test_validate_u16_non_numeric_raises_range():
    schema = parse_catalog(CATALOG)
    with pytest.raises(ConfigError) as exc:
        schema.validate("tx.period_ms", "abc")
    assert exc.value.reason == Reason.RANGE


def test_validate_unknown_key():
    schema = parse_catalog(CATALOG)
    with pytest.raises(ConfigError) as exc:
        schema.validate("nope.nope", "1")
    assert exc.value.reason == Reason.UNKNOWN_KEY


def test_validate_readonly_key():
    schema = parse_catalog(CATALOG)
    with pytest.raises(ConfigError) as exc:
        schema.validate("pwr.5v", "false")
    assert exc.value.reason == Reason.READONLY


def test_validate_enum_rejects_value_outside_choices():
    schema = parse_catalog(CATALOG)
    assert schema.validate("adc.pga", "8") == 8
    with pytest.raises(ConfigError) as exc:
        schema.validate("adc.pga", "3")
    assert exc.value.reason == Reason.RANGE


def test_validate_str_respects_max_length():
    schema = parse_catalog(CATALOG)
    assert schema.validate("ain0.unit", "kPa") == "kPa"
    with pytest.raises(ConfigError) as exc:
        schema.validate("ain0.unit", "12345678")
    assert exc.value.reason == Reason.RANGE


def test_validate_accepts_zero_padded_integer():
    """🔴 '08' 은 유효한 십진수다.

    int(raw, 0) 을 쓰면 파이썬이 앞의 0 을 8진수 접두사로 읽어 ValueError 가
    난다. 고정폭 습관으로 0 을 채워 넣는 건 지극히 자연스러운데, 그게
    '정수가 아님' 으로 거부되면 사용자가 원인을 짐작할 수도 없다.
    """
    schema = parse_catalog(CATALOG)
    assert schema.validate("tx.period_ms", "0100") == 100
    assert schema.validate("tx.period_ms", "0250") == 250


def test_zero_padded_value_fails_on_range_not_on_parsing():
    """거부되더라도 사유가 '범위' 여야 한다. 파싱 실패가 아니다."""
    schema = parse_catalog(CATALOG)
    with pytest.raises(ConfigError) as exc:
        schema.validate("tx.period_ms", "08")
    assert exc.value.reason == Reason.RANGE
    assert "최소" in exc.value.detail          # 파싱 오류가 아니라 범위 오류


def test_validate_rejects_nan_and_inf():
    """🔴 NaN 은 범위 검사를 그대로 뚫는다.

    nan < min 도 nan > max 도 False 다. 범위가 없는 항목이면 inf 도 통과한다.
    보드 저장소(Task 6)는 이미 막고 있으므로 호스트도 같은 규칙이어야 한다.
    """
    schema = parse_catalog(CATALOG)
    for bad in ("nan", "inf", "-inf", "Infinity", "NaN"):
        with pytest.raises(ConfigError) as exc:
            schema.validate("ain0.scale", bad)
        assert exc.value.reason == Reason.RANGE, bad


def test_validate_accepts_normal_float():
    schema = parse_catalog(CATALOG)
    assert schema.validate("ain0.scale", "2.5") == 2.5
    assert schema.validate("ain0.scale", "-1.25") == -1.25


def test_parse_catalog_requires_cfg_end():
    """🔴 종료 줄이 아예 없는 것도 절단이다.

    cfg_end 는 마지막에 보내므로 전송이 끊기면 '개수가 틀린 cfg_end' 가
    아니라 'cfg_end 자체가 없음' 이 된다. 이쪽이 더 흔한 형태다.
    통과시키면 GUI 가 설정이 빠진 화면을 경고 없이 정상처럼 그린다.
    """
    with pytest.raises(ConfigError) as exc:
        parse_catalog(CATALOG[:-1])            # cfg_end 만 뺀다
    assert exc.value.reason == Reason.RANGE
    assert "cfg_end" in exc.value.detail


def test_validate_rejects_command_injection_and_delimiters():
    """🔴 값에 줄바꿈·구분자를 넣어 명령을 주입하는 공격을 막는다.

    보드 쪽 저장소(Task 6)와 같은 규칙이다. 호스트에서 미리 걸러 왕복을
    아끼되, 보드가 최종 권위인 것은 변함없다.
    """
    schema = parse_catalog(CATALOG)
    for payload in ("x\r\n$HB*0A", "a,b", "a*b", "a$b", "a\x00b", "a\x1bb"):
        with pytest.raises(ConfigError) as exc:
            schema.validate("ain0.unit", payload)
        assert exc.value.reason == Reason.RANGE, repr(payload)


def test_validate_rejects_non_ascii_string():
    """단위는 'degC'·'kPa' 처럼 쓴다."""
    schema = parse_catalog(CATALOG)
    for bad in ("℃", "바", "Ω"):
        with pytest.raises(ConfigError) as exc:
            schema.validate("ain0.unit", bad)
        assert exc.value.reason == Reason.RANGE, bad


def test_validate_accepts_ascii_string():
    schema = parse_catalog(CATALOG)
    assert schema.validate("ain0.unit", "kPa") == "kPa"


def test_validate_bool_accepts_true_false_words():
    schema = parse_catalog([
        _item(key="pwr.24v", grp="pwr", vtype="bool", default=False,
              cur=False, ro=False, label="24V"),
        _end(1),
    ])
    assert schema.validate("pwr.24v", "true") is True
    assert schema.validate("pwr.24v", "false") is False
    assert schema.validate("pwr.24v", "1") is True
    assert schema.validate("pwr.24v", "0") is False
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `python -m pytest host/tests/test_config_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'host.core.config_schema'`

- [ ] **Step 3: `config_schema.py` 작성**

`host/core/config_schema.py`:
```python
"""$CFG,LIST 응답을 스키마 객체로 바꾸고 값을 검증한다.

호스트는 설정 항목을 하드코딩하지 않는다. 보드가 내려준 카탈로그만으로
화면을 구성하고 값을 검사한다.

규격: protocol/specification.md §7.3
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass, field

from host.core.errors import ConfigError, Reason
from host.core.records import parse_record

_INT_TYPES = ("u8", "u16", "u32")
_TRUE_WORDS = ("true", "1", "on", "yes")
_FALSE_WORDS = ("false", "0", "off", "no")

#: 프로토콜 구분자. 설정값에 들어가면 줄 구조가 깨진다.
_PROTOCOL_DELIMITERS = frozenset("$,*")

#: 🔴 설정 문자열에 허용되는 문자 — 인쇄 가능 ASCII 에서 구분자를 뺀 것.
#:
#: `isascii()` 만으로는 부족하다. 제어문자가 전부 ASCII 이기 때문이다:
#: '\n'.isascii() 도 '\x00'.isascii() 도 True 다.
#:
#: 이 한 줄이 막는 것들:
#:   '\r' '\n'  — 값 안에 줄바꿈을 넣어 `$CFG,SET,dev.id,x\r\n$HB*0A` 처럼
#:                완결된 명령을 주입하는 공격. 주입된 $HB 는 체크섬까지
#:                유효해서 보드가 진짜 하트비트로 받아 CONFIG 모드를 연다
#:   '\x00'     — C 펌웨어에서 문자열이 중간에 끝난 것처럼 처리됨.
#:                호스트가 검증한 값과 펌웨어가 해석한 값이 달라진다
#:   ','        — 명령 인자를 추가로 쪼갬
#:   '*'        — 체크섬 구분자로 오인
#:   '$'        — 명령 시작으로 오인
#:
#: 단위는 'degC'·'kPa'·'LPM'·'%' 처럼 쓴다. 비 ASCII 를 막는 부수 효과로
#: 문자 수 = 바이트 수가 되어 str<=7 이 펌웨어 고정폭 버퍼와 정확히 맞는다.
_ALLOWED_STR_CHARS = frozenset(
    chr(c) for c in range(0x20, 0x7F)
) - _PROTOCOL_DELIMITERS


@dataclass(frozen=True)
class ConfigItem:
    key: str
    group: str
    vtype: str
    default: object
    current: object
    minimum: float | None = None
    maximum: float | None = None
    unit: str = ""
    readonly: bool = False
    label: str = ""
    note: str = ""
    choices: tuple = ()


@dataclass(frozen=True)
class FieldBit:
    """NDJSON 필드 마스크의 비트 하나."""

    bit: int
    name: str
    default: bool
    label: str = ""


@dataclass
class ConfigSchema:
    items: dict[str, ConfigItem] = field(default_factory=dict)
    fields: dict[int, FieldBit] = field(default_factory=dict)
    _group_order: list[str] = field(default_factory=list)

    def groups(self) -> list[str]:
        """항목이 처음 나타난 순서대로 그룹 이름을 반환한다."""
        return list(self._group_order)

    def validate(self, key: str, raw: str) -> object:
        """문자열 값을 검사해 파싱된 값을 반환한다.

        검사 순서는 규격 §5 를 따른다 — 키 존재 → 읽기 전용 → 타입·범위.
        인터록은 보드가 판정하므로 호스트에서는 검사하지 않는다.

        Raises:
            ConfigError: reason 이 UNKNOWN_KEY / READONLY / RANGE 중 하나.
        """
        item = self.items.get(key)
        if item is None:
            raise ConfigError(Reason.UNKNOWN_KEY, key)
        if item.readonly:
            raise ConfigError(Reason.READONLY, item.note or key)
        return _coerce(item, raw)


def _coerce(item: ConfigItem, raw: str) -> object:
    """vtype 에 맞게 문자열을 변환하고 범위를 검사한다."""
    if item.vtype == "bool":
        low = raw.strip().lower()
        if low in _TRUE_WORDS:
            return True
        if low in _FALSE_WORDS:
            return False
        raise ConfigError(Reason.RANGE, f"불리언이 아님: {raw!r}")

    if item.vtype == "str":
        bad = [c for c in raw if c not in _ALLOWED_STR_CHARS]
        if bad:
            raise ConfigError(Reason.RANGE, f"허용되지 않는 문자: {bad!r}")
        if item.maximum is not None and len(raw) > int(item.maximum):
            raise ConfigError(
                Reason.RANGE, f"최대 {int(item.maximum)}자, 받음 {len(raw)}자"
            )
        return raw

    if item.vtype == "enum":
        try:
            value: object = int(raw)
        except ValueError:
            value = raw
        if value not in item.choices:
            raise ConfigError(Reason.RANGE, f"허용값 {list(item.choices)}")
        return value

    if item.vtype in _INT_TYPES:
        try:
            # 🔴 base 0 을 쓰면 안 된다. int("08", 0) 은 ValueError 다 —
            # 파이썬이 앞의 0 을 8진수 접두사로 읽기 때문이다. 사용자가
            # 고정폭 습관으로 "08" 을 넣는 건 지극히 자연스럽고, 그게
            # "정수가 아님" 으로 거부되면 원인을 짐작할 수도 없다.
            value = int(raw, 10)
        except ValueError:
            raise ConfigError(Reason.RANGE, f"정수가 아님: {raw!r}") from None
    elif item.vtype == "f32":
        try:
            value = float(raw)
        except ValueError:
            raise ConfigError(Reason.RANGE, f"실수가 아님: {raw!r}") from None
        # 🔴 보드 저장소(config_store)와 같은 규칙이어야 한다.
        # NaN 은 아래 범위 검사를 그대로 뚫는다 — nan < min 도 nan > max 도
        # False 다. 범위가 아예 없는 항목(ain*.zero/scale)은 inf 도 통과한다.
        if not math.isfinite(value):
            raise ConfigError(Reason.RANGE, f"유한한 실수가 아님: {raw!r}")
    else:
        raise ConfigError(Reason.RANGE, f"알 수 없는 타입: {item.vtype}")

    if item.minimum is not None and value < item.minimum:
        raise ConfigError(Reason.RANGE, f"최소 {item.minimum}, 받음 {value}")
    if item.maximum is not None and value > item.maximum:
        raise ConfigError(Reason.RANGE, f"최대 {item.maximum}, 받음 {value}")
    return value


def parse_catalog(lines: Iterable[str]) -> ConfigSchema:
    """$CFG,LIST 응답 줄들을 ConfigSchema 로 모은다.

    Raises:
        ConfigError: cfg_end 의 count 가 실제 항목 수와 다른 경우
            (전송이 중간에 잘린 것으로 본다).
    """
    schema = ConfigSchema()
    declared: int | None = None

    for line in lines:
        rec = parse_record(line)
        rtype = rec["type"]

        if rtype == "cfg_item":
            group = rec.get("grp", "")
            if group not in schema._group_order:
                schema._group_order.append(group)
            schema.items[rec["key"]] = ConfigItem(
                key=rec["key"],
                group=group,
                vtype=rec["vtype"],
                default=rec.get("default"),
                current=rec.get("cur"),
                minimum=rec.get("min"),
                maximum=rec.get("max"),
                unit=rec.get("unit", ""),
                readonly=bool(rec.get("ro", False)),
                label=rec.get("label", ""),
                note=rec.get("note", ""),
                choices=tuple(rec.get("choices", ())),
            )

        elif rtype == "cfg_field":
            schema.fields[rec["bit"]] = FieldBit(
                bit=rec["bit"],
                name=rec["name"],
                default=bool(rec.get("default", False)),
                label=rec.get("label", ""),
            )

        elif rtype == "cfg_end":
            declared = rec["count"]

    # 🔴 종료 줄이 아예 안 온 경우도 절단이다.
    #
    # 오히려 이쪽이 더 흔한 형태다 — cfg_end 는 마지막에 보내므로, 전송이
    # 중간에 끊기면 "개수가 틀린 cfg_end" 가 아니라 "cfg_end 자체가 없음" 이
    # 된다. 이걸 통과시키면 GUI 가 설정 몇 개가 빠진 화면을 아무 경고 없이
    # 정상인 것처럼 그린다.
    if declared is None:
        raise ConfigError(Reason.RANGE, "카탈로그에 cfg_end 가 없음 (전송 절단)")

    total = len(schema.items) + len(schema.fields)
    if declared != total:
        raise ConfigError(
            Reason.RANGE,
            f"카탈로그 개수 불일치: 선언 {declared}, 수신 {total}",
        )
    return schema
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `python -m pytest host/tests/test_config_schema.py -v`
Expected: PASS (11 passed)

- [ ] **Step 5: 커밋**

```bash
git add host/core/config_schema.py host/tests/test_config_schema.py
git commit -m "feat(proto): 설정 카탈로그 파싱과 값 검증

보드가 \$CFG,LIST 로 내려준 카탈로그만으로 항목을 알고 값을 검사한다.
호스트에 설정 항목을 하드코딩하지 않게 만드는 층이다 — 펌웨어에 설정이
늘어도 GUI 를 고치지 않는 것이 목표다.

cfg_end 의 count 를 대조해 전송이 중간에 잘린 경우를 잡는다.
인터록은 보드 권한이므로 호스트에서 검사하지 않는다."
```

---

## Task 6: 시뮬레이터 설정 저장소

**Files:**
- Create: `tools/simulator/config_store.py`
- Test: `host/tests/test_config_store.py`

**Interfaces:**
- Consumes: `host.core.errors.ConfigError`, `Reason`
- Produces:
  - `SimConfigItem` — dataclass (mutable `current`), 필드는 `ConfigItem`과 동일 + `interlocked: bool`
  - `ConfigStore` — `.items`, `.get(key)`, `.set(key, raw)`, `.save()`, `.load()`, `.reset()`, `.catalog_lines()`, `.field_mask`, `.dirty`
  - `default_store(path: Path | None = None) -> ConfigStore` — 스펙 §6.1 항목 전체

펌웨어 `config_store` 모듈의 기준 구현이다. 여기서 정한 항목·범위·기본값이
그대로 펌웨어로 간다.

- [ ] **Step 1: 실패하는 테스트 작성**

`host/tests/test_config_store.py`:
```python
import pytest

from host.core.errors import ConfigError, Reason
from tools.simulator.config_store import default_store


def test_default_store_has_spec_items():
    """스펙 §6.1 의 항목이 전부 있어야 한다."""
    store = default_store()
    for key in ("dev.id", "tx.fields", "tx.period_ms", "tx.float_digits",
                "adc.pga", "adc.drate", "pwr.24v", "pwr.14v9", "pwr.5v",
                "pwr.seq_delay_ms"):
        assert key in store.items, f"{key} 누락"
    for ch in range(7):
        for suffix in ("enabled", "period_ms", "zero", "scale", "unit"):
            assert f"ain{ch}.{suffix}" in store.items


def test_set_and_get_roundtrip():
    store = default_store()
    store.set("tx.period_ms", "250")
    assert store.get("tx.period_ms") == 250


def test_set_marks_dirty_until_save():
    store = default_store()
    assert store.dirty is False
    store.set("tx.period_ms", "250")
    assert store.dirty is True


def test_set_out_of_range_raises_range():
    store = default_store()
    with pytest.raises(ConfigError) as exc:
        store.set("tx.period_ms", "1")
    assert exc.value.reason == Reason.RANGE


def test_set_unknown_key():
    store = default_store()
    with pytest.raises(ConfigError) as exc:
        store.set("no.such.key", "1")
    assert exc.value.reason == Reason.UNKNOWN_KEY


def test_pwr_5v_is_readonly_and_true():
    """쿨링 팬이 5V 레일 직결이라 끌 수 없다 (데이터시트 §5.14)."""
    store = default_store()
    assert store.get("pwr.5v") is True
    assert store.items["pwr.5v"].readonly is True


def test_pwr_5v_off_raises_interlock_not_readonly():
    """읽기 전용이지만 사용자에게는 '안전상 거부'로 알려야 한다."""
    store = default_store()
    with pytest.raises(ConfigError) as exc:
        store.set("pwr.5v", "false")
    assert exc.value.reason == Reason.INTERLOCK
    assert "팬" in exc.value.detail


def test_pwr_5v_set_true_is_accepted_as_noop():
    """이미 참인 값을 참으로 두는 것은 거부할 이유가 없다."""
    store = default_store()
    store.set("pwr.5v", "true")
    assert store.get("pwr.5v") is True


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "cfg.json"
    store = default_store(path)
    store.set("tx.period_ms", "250")
    store.set("ain3.unit", "bar")
    store.save()
    assert store.dirty is False

    reloaded = default_store(path)
    reloaded.load()
    assert reloaded.get("tx.period_ms") == 250
    assert reloaded.get("ain3.unit") == "bar"


def test_load_missing_file_keeps_defaults(tmp_path):
    store = default_store(tmp_path / "absent.json")
    store.load()
    assert store.get("tx.period_ms") == 100


def test_load_corrupt_file_falls_back_to_defaults(tmp_path):
    """Flash 손상 시 공장 기본값으로 복구한다 (스펙 §6.2)."""
    path = tmp_path / "cfg.json"
    path.write_text("{ this is not json", encoding="utf-8")
    store = default_store(path)
    store.load()
    assert store.get("tx.period_ms") == 100
    assert store.load_failed is True


def test_load_refuses_stored_interlock_bypass(tmp_path):
    """🔴 저장 파일이 pwr.5v=false 를 담고 있어도 복원하지 않는다.

    Flash 손상이나 손편집으로 인터록이 뚫리면 쿨링 팬이 꺼진 채 부팅한다.
    저장 매체는 신뢰 대상이 아니다.
    """
    import json

    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"pwr.5v": False}), encoding="utf-8")
    store = default_store(path)
    store.load()
    assert store.get("pwr.5v") is True
    assert "pwr.5v" in store.rejected_keys


def test_load_rejects_out_of_range_stored_value(tmp_path):
    """저장값도 범위 검사를 받는다. 거부되면 기본값을 유지한다."""
    import json

    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"tx.period_ms": 999999}), encoding="utf-8")
    store = default_store(path)
    store.load()
    assert store.get("tx.period_ms") == 100
    assert "tx.period_ms" in store.rejected_keys


def test_load_rejects_nan_stored_value(tmp_path):
    """NaN 은 범위 검사를 뚫는다(nan<min 도 nan>max 도 False). 별도로 막는다."""
    path = tmp_path / "cfg.json"
    path.write_text('{"ain0.scale": NaN}', encoding="utf-8")   # json 모듈은 NaN 을 읽는다
    store = default_store(path)
    store.load()
    assert store.get("ain0.scale") == 1.0
    assert "ain0.scale" in store.rejected_keys


def test_set_accepts_zero_padded_integer():
    """🔴 '0250' 은 유효한 십진수다.

    int(raw, 0) 을 쓰면 파이썬이 앞의 0 을 8진수 접두사로 읽어 ValueError 가
    난다. GUI 의 고정폭 입력이나 사람의 습관으로 0 이 채워지는 건 흔하다.
    """
    store = default_store()
    store.set("tx.period_ms", "0250")
    assert store.get("tx.period_ms") == 250


def test_set_rejects_command_injection_via_newline():
    """🔴 값에 줄바꿈을 넣어 명령을 주입하는 공격을 막는다.

    `dev.id = "x\\r\\n$HB*0A"` 를 허용하면 조립된 줄이
    `$CFG,SET,dev.id,x\\r\\n$HB*0A*72\\r\\n` 가 된다. 가운데 `$HB*0A` 는
    체크섬까지 유효한 완결된 명령이라, 보드가 이를 진짜 하트비트로 받아
    CONFIG 모드를 유지한다. 설정값 하나로 명령을 심을 수 있다.
    """
    store = default_store()
    for payload in ("x\r\n$HB*0A", "x\n$HB*0A", "x\ry"):
        with pytest.raises(ConfigError) as exc:
            store.set("dev.id", payload)
        assert exc.value.reason == Reason.RANGE, payload
    assert store.get("dev.id") == "1"


def test_set_rejects_protocol_delimiters_in_value():
    """🔴 구분자가 값에 들어가면 호스트와 펌웨어의 해석이 갈린다.

    ',' 는 인자를 더 쪼개고, '*' 는 체크섬 구분자로, '$' 는 명령 시작으로
    오인된다.
    """
    store = default_store()
    for payload in ("a,b", "a*b", "a$b"):
        with pytest.raises(ConfigError) as exc:
            store.set("ain0.unit", payload)
        assert exc.value.reason == Reason.RANGE, payload


def test_set_rejects_nul_byte():
    """🔴 NUL 은 C 펌웨어에서 문자열을 중간에 끊는다.

    호스트는 'a\\0b' 를 3자로 보고 통과시키지만 펌웨어는 'a' 로 읽는다.
    검증한 값과 저장된 값이 달라진다.
    """
    store = default_store()
    with pytest.raises(ConfigError) as exc:
        store.set("ain0.unit", "a\x00b")
    assert exc.value.reason == Reason.RANGE


def test_set_rejects_all_control_characters():
    """isascii() 만으로는 부족하다 — 제어문자는 전부 ASCII 다."""
    store = default_store()
    for code in (0x00, 0x07, 0x09, 0x0A, 0x0D, 0x1B, 0x7F):
        with pytest.raises(ConfigError) as exc:
            store.set("ain0.unit", f"a{chr(code)}b")
        assert exc.value.reason == Reason.RANGE, hex(code)


def test_set_rejects_non_ascii_string_value():
    """단위는 'degC'·'kPa' 처럼 쓴다.

    '℃' 를 허용하면 펌웨어의 바이트 기준 고정폭 버퍼와 호스트의 문자 기준
    길이가 어긋난다.
    """
    store = default_store()
    for bad in ("℃", "바", "Ω", "µm"):
        with pytest.raises(ConfigError) as exc:
            store.set("ain0.unit", bad)
        assert exc.value.reason == Reason.RANGE, bad
    assert store.get("ain0.unit") == ""          # 아무것도 반영되지 않았다


def test_set_accepts_ascii_unit():
    store = default_store()
    for good in ("bar", "degC", "kPa", "LPM", "%"):
        store.set("ain0.unit", good)
        assert store.get("ain0.unit") == good


def test_str_length_limit_is_byte_safe_because_ascii_only():
    """ASCII 만 통과하므로 문자 수 == 바이트 수다. 펌웨어 버퍼와 일치한다."""
    store = default_store()
    store.set("ain0.unit", "1234567")            # 최대 7자
    with pytest.raises(ConfigError) as exc:
        store.set("ain0.unit", "12345678")
    assert exc.value.reason == Reason.RANGE


def test_load_rejects_non_ascii_stored_value(tmp_path):
    """저장 파일에 non-ASCII 가 들어 있어도 복원하지 않는다."""
    import json

    path = tmp_path / "cfg.json"
    path.write_text(
        json.dumps({"ain0.unit": "℃"}, ensure_ascii=False), encoding="utf-8"
    )
    store = default_store(path)
    store.load()
    assert store.get("ain0.unit") == ""
    assert "ain0.unit" in store.rejected_keys


def test_set_rejects_nan_and_inf():
    """$CFG,SET 경로도 동일하게 막힌다."""
    store = default_store()
    for bad in ("nan", "inf", "-inf", "Infinity"):
        with pytest.raises(ConfigError) as exc:
            store.set("ain0.scale", bad)
        assert exc.value.reason == Reason.RANGE, bad


def test_load_restores_normal_values_untouched(tmp_path):
    """거부 로직이 정상값 복원을 방해하지 않는다."""
    import json

    path = tmp_path / "cfg.json"
    path.write_text(
        json.dumps({"tx.period_ms": 250, "ain2.enabled": True, "ain2.unit": "bar"}),
        encoding="utf-8",
    )
    store = default_store(path)
    store.load()
    assert store.get("tx.period_ms") == 250
    assert store.get("ain2.enabled") is True
    assert store.get("ain2.unit") == "bar"
    assert store.rejected_keys == []


def test_load_rejects_individually_valid_but_infeasible_combination(tmp_path):
    """🔴 항목별로는 다 유효한데 조합이 불가능한 설정 파일.

    7채널 10ms(요구 700 SPS) + drate 2 SPS(가용 1.6 SPS). 항목 검사만
    하면 한 개도 안 걸리고 그대로 부팅한다. 큐가 영구히 넘치는데 아무
    신호도 없다 — `load()` 의 전제("저장 매체는 신뢰 대상이 아니다")를
    항목 단위로만 지킨 셈이다.

    부팅을 막지는 않는다. 기본값으로 되돌리고 표시만 남긴다.
    """
    import json

    cfg = {"adc.drate": 2}
    for ch in range(7):
        cfg[f"ain{ch}.enabled"] = True
        cfg[f"ain{ch}.period_ms"] = 10

    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    store = default_store(path)
    store.load()

    assert "<combination>" in store.rejected_keys
    assert store.load_failed is True
    assert store.get("adc.drate") == 60            # 기본값으로 복귀
    assert store.get("ain1.enabled") is False


def test_load_accepts_a_feasible_combination(tmp_path):
    """조합 검사가 정상 설정을 막지 않는다."""
    import json

    path = tmp_path / "cfg.json"
    path.write_text(
        json.dumps({"adc.drate": 1000, "ain1.enabled": True,
                    "ain1.period_ms": 200}),
        encoding="utf-8",
    )
    store = default_store(path)
    store.load()

    assert store.rejected_keys == []
    assert store.load_failed is False
    assert store.get("ain1.enabled") is True
    assert store.get("adc.drate") == 1000


def test_load_ignores_unknown_keys(tmp_path):
    """스키마가 내려간 경우에도 아는 항목만 복원한다 (전방 호환)."""
    import json

    path = tmp_path / "cfg.json"
    path.write_text(
        json.dumps({"tx.period_ms": 250, "gone.away": 1}), encoding="utf-8"
    )
    store = default_store(path)
    store.load()
    assert store.get("tx.period_ms") == 250


def test_reset_restores_defaults():
    store = default_store()
    store.set("tx.period_ms", "250")
    store.reset()
    assert store.get("tx.period_ms") == 100


def test_field_mask_default_matches_spec():
    """기본 on 비트: time_source(1), raw(3), ma(4), value(5), status(7), connector_id(9)"""
    store = default_store()
    assert store.field_mask == 0b1010111010


def test_catalog_lines_end_with_cfg_end_matching_count():
    from host.core.config_schema import parse_catalog

    store = default_store()
    lines = list(store.catalog_lines())
    schema = parse_catalog(lines)          # count 불일치면 여기서 예외
    assert len(schema.items) == len(store.items)
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `python -m pytest host/tests/test_config_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.simulator.config_store'`

- [ ] **Step 3: `config_store.py` 작성**

`tools/simulator/config_store.py`:
```python
"""시뮬레이터 설정 저장소 — 펌웨어 config_store 모듈의 기준 구현.

여기서 정한 항목·타입·범위·기본값이 그대로 펌웨어로 간다.

검증 순서 (규격 §5.2, 코드와 일치해야 한다):

    키 존재 → 타입·범위 → 현재값과 동일? → 인터록 → 읽기 전용 → 조합 검사

- **인터록이 범위 검사 뒤**인 이유: 값 자체가 틀린 것과 안전 정책상 거부된
  것은 사용자에게 다른 메시지여야 한다.
- **인터록이 읽기 전용보다 앞**인 이유: 둘 다 걸리는 항목(`pwr.5v`)에서
  READONLY 만 돌려주면 `note` 에 담긴 하드웨어 사유가 사라진다.
- **조합 검사가 마지막**인 이유: 어차피 거부될 변경을 미리 반영해 보고
  되돌리는 것은 낭비이고, 읽기 전용 값을 잠시라도 바꾸는 것은 위험하다.
- **현재값과 같은 값을 쓰는 것은 거부하지 않는다**: 호스트가 전체 설정을
  한꺼번에 되쓸 때 불필요한 거부가 나지 않게 한다.
"""

import json
import math
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from host.core.errors import ConfigError, Reason
from host.core.records import SCHEMA_VER

#: NDJSON 필드 마스크 비트 정의 (규격 §7.2)
FIELD_BITS: tuple[tuple[int, str, bool, str], ...] = (
    (0, "device_id", False, "보드 식별자"),
    (1, "time_source", True, "시간 소스"),
    (2, "time_quality", False, "시간 품질"),
    (3, "raw", True, "ADS1256 원시 카운트"),
    (4, "ma", True, "전류 (mA)"),
    (5, "value", True, "물리량 환산"),
    (6, "unit", False, "단위"),
    (7, "status", True, "채널 상태"),
    (8, "capture_counter", False, "획득 카운터"),
    (9, "connector_id", True, "커넥터 번호"),
)

#: ADS1256 지원 DRATE (SPS)
DRATE_CHOICES = (2, 5, 10, 15, 25, 30, 50, 60, 100, 500, 1000, 2000, 3750, 7500)

_TRUE_WORDS = ("true", "1", "on", "yes")
_FALSE_WORDS = ("false", "0", "off", "no")
_INT_TYPES = ("u8", "u16", "u32")

#: 프로토콜 구분자. 설정값에 들어가면 줄 구조가 깨진다.
_PROTOCOL_DELIMITERS = frozenset("$,*")

#: 🔴 설정 문자열에 허용되는 문자 — 인쇄 가능 ASCII 에서 구분자를 뺀 것.
#:
#: `isascii()` 만으로는 부족하다. 제어문자가 전부 ASCII 이기 때문이다:
#: '\n'.isascii() 도 '\x00'.isascii() 도 True 다.
#:
#: 이 한 줄이 막는 것들:
#:   '\r' '\n'  — 값 안에 줄바꿈을 넣어 `$CFG,SET,dev.id,x\r\n$HB*0A` 처럼
#:                완결된 명령을 주입하는 공격. 주입된 $HB 는 체크섬까지
#:                유효해서 보드가 진짜 하트비트로 받아 CONFIG 모드를 연다
#:   '\x00'     — C 펌웨어에서 문자열이 중간에 끝난 것처럼 처리됨.
#:                호스트가 검증한 값과 펌웨어가 해석한 값이 달라진다
#:   ','        — 명령 인자를 추가로 쪼갬
#:   '*'        — 체크섬 구분자로 오인
#:   '$'        — 명령 시작으로 오인
#:
#: 단위는 'degC'·'kPa'·'LPM'·'%' 처럼 쓴다. 비 ASCII 를 막는 부수 효과로
#: 문자 수 = 바이트 수가 되어 str<=7 이 펌웨어 고정폭 버퍼와 정확히 맞는다.
_ALLOWED_STR_CHARS = frozenset(
    chr(c) for c in range(0x20, 0x7F)
) - _PROTOCOL_DELIMITERS


@dataclass
class SimConfigItem:
    key: str
    group: str
    vtype: str
    default: object
    current: object
    minimum: float | None = None
    maximum: float | None = None
    unit: str = ""
    readonly: bool = False
    label: str = ""
    note: str = ""
    choices: tuple = ()
    #: 참이면 값 변경 시도를 INTERLOCK 으로 거부한다 (현재값과 같으면 통과)
    interlocked: bool = False


class ConfigStore:
    """설정 항목 보관, 검증, 영속."""

    def __init__(self, items: list[SimConfigItem], path: Path | None = None):
        self.items: dict[str, SimConfigItem] = {i.key: i for i in items}
        self.path = path
        self.dirty = False
        self.load_failed = False
        #: load() 가 거부하고 기본값을 유지한 키들
        self.rejected_keys: list[str] = []

    # ------------------------------------------------------------------ 조회
    def get(self, key: str) -> object:
        item = self.items.get(key)
        if item is None:
            raise ConfigError(Reason.UNKNOWN_KEY, key)
        return item.current

    @property
    def field_mask(self) -> int:
        return int(self.items["tx.fields"].current)

    # ------------------------------------------------------------------ 변경
    def set(self, key: str, raw: str) -> None:
        """문자열 값을 검증해 반영한다. 규격 §5 의 순서를 지킨다."""
        item = self.items.get(key)
        if item is None:
            raise ConfigError(Reason.UNKNOWN_KEY, key)

        value = _coerce(item, raw)

        if item.interlocked and value != item.current:
            raise ConfigError(Reason.INTERLOCK, item.note or key)
        if item.readonly and value != item.current:
            raise ConfigError(Reason.READONLY, item.note or key)

        if value != item.current:
            item.current = value
            self.dirty = True

    def reset(self) -> None:
        for item in self.items.values():
            item.current = item.default
        self.dirty = True

    # ---------------------------------------------------------------- 영속화
    def save(self) -> None:
        if self.path is None:
            self.dirty = False
            return
        payload = {k: i.current for k, i in self.items.items()}
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.dirty = False

    def load(self) -> None:
        """저장된 값을 복원한다. 파일이 없거나 깨졌으면 기본값을 유지한다.

        🔴 **저장값도 전부 재검증한다.** 저장 매체는 신뢰 대상이 아니다 —
        Flash 가 손상되거나 사람이 파일을 손으로 고칠 수 있다. 검증 없이
        대입하면 `pwr.5v = false` 같은 인터록 항목이나 범위 밖 값이
        그대로 살아난다. 그러면 쿨링 팬이 꺼진 채로 부팅한다.

        복원할 수 없는 항목은 **기본값을 유지하고** `rejected_keys` 에
        남긴다. 부팅을 막지는 않는다 — 설정 하나가 깨졌다고 보드가 죽으면
        복구 수단까지 사라진다.
        """
        self.load_failed = False
        self.rejected_keys = []
        if self.path is None or not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("객체가 아님")
        except (json.JSONDecodeError, ValueError, OSError):
            self.load_failed = True
            self.reset()
            self.dirty = False
            return

        for key, value in payload.items():
            item = self.items.get(key)
            if item is None:
                continue                         # 모르는 키는 조용히 무시

            if item.interlocked or item.readonly:
                # 정책·안전 항목은 저장값을 신뢰하지 않는다. 기본값을 쓴다.
                if value != item.default:
                    self.rejected_keys.append(key)
                continue

            try:
                item.current = _coerce(item, _to_raw(value))
            except ConfigError:
                self.rejected_keys.append(key)   # 기본값 유지

        # 🔴 개별 항목이 전부 유효해도 **조합**이 불가능할 수 있다.
        #
        # 손편집하거나 손상된 파일이 7채널 10ms + drate 2 SPS 를 담고 있으면
        # 위 루프는 한 항목도 거부하지 않는다. 그대로 부팅하면 큐가 영구히
        # 넘치는데 아무 신호도 없다. `load()` 의 전제("저장 매체는 신뢰 대상이
        # 아니다")를 항목 단위로만 지킨 셈이다.
        #
        # 부팅을 막지는 않는다 — 기본값으로 되돌리고 표시만 남긴다.
        try:
            from tools.simulator.capacity import check_capacity

            check_capacity(self)
        except ConfigError:
            self.rejected_keys.append("<combination>")
            self.load_failed = True
            self.reset()

        self.dirty = False

    # ---------------------------------------------------------------- 카탈로그
    def catalog_lines(self) -> Iterator[str]:
        """$CFG,LIST 응답 줄들을 생성한다 (규격 §7.3)."""
        for item in self.items.values():
            rec: dict = {
                "schema_ver": SCHEMA_VER,
                "seq": 0,
                "t": 0,
                "type": "cfg_item",
                "key": item.key,
                "grp": item.group,
                "vtype": item.vtype,
                "default": item.default,
                "cur": item.current,
                "ro": item.readonly,
                "label": item.label,
            }
            if item.minimum is not None:
                rec["min"] = item.minimum
            if item.maximum is not None:
                rec["max"] = item.maximum
            if item.unit:
                rec["unit"] = item.unit
            if item.note:
                rec["note"] = item.note
            if item.choices:
                rec["choices"] = list(item.choices)
            yield json.dumps(rec, ensure_ascii=False)

        for bit, name, default, label in FIELD_BITS:
            yield json.dumps(
                {
                    "schema_ver": SCHEMA_VER,
                    "seq": 0,
                    "t": 0,
                    "type": "cfg_field",
                    "bit": bit,
                    "name": name,
                    "default": default,
                    "label": label,
                },
                ensure_ascii=False,
            )

        yield json.dumps(
            {
                "schema_ver": SCHEMA_VER,
                "seq": 0,
                "t": 0,
                "type": "cfg_end",
                "count": len(self.items) + len(FIELD_BITS),
            }
        )


def _to_raw(value: object) -> str:
    """JSON 에서 읽은 값을 _coerce 가 받는 문자열 형태로 되돌린다.

    저장은 JSON 네이티브 타입으로 하지만 검증 경로는 하나뿐이어야 한다.
    파일에서 온 값과 $CFG,SET 으로 온 값이 서로 다른 검사를 받으면,
    한쪽만 막히는 구멍이 반드시 생긴다.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _coerce(item: SimConfigItem, raw: str) -> object:
    """문자열을 vtype 에 맞게 변환하고 범위를 검사한다."""
    if item.vtype == "bool":
        low = raw.strip().lower()
        if low in _TRUE_WORDS:
            return True
        if low in _FALSE_WORDS:
            return False
        raise ConfigError(Reason.RANGE, f"불리언이 아님: {raw!r}")

    if item.vtype == "str":
        bad = [c for c in raw if c not in _ALLOWED_STR_CHARS]
        if bad:
            raise ConfigError(Reason.RANGE, f"허용되지 않는 문자: {bad!r}")
        if item.maximum is not None and len(raw) > int(item.maximum):
            raise ConfigError(
                Reason.RANGE, f"최대 {int(item.maximum)}자, 받음 {len(raw)}자"
            )
        return raw

    if item.vtype == "enum":
        try:
            value: object = int(raw)
        except ValueError:
            value = raw
        if value not in item.choices:
            raise ConfigError(Reason.RANGE, f"허용값 {list(item.choices)}")
        return value

    if item.vtype in _INT_TYPES:
        try:
            # 🔴 base 0 을 쓰면 안 된다. int("08", 0) 은 ValueError 다 —
            # 파이썬이 앞의 0 을 8진수 접두사로 읽기 때문이다. 사용자가
            # 고정폭 습관으로 "08" 을 넣는 건 지극히 자연스럽고, 그게
            # "정수가 아님" 으로 거부되면 원인을 짐작할 수도 없다.
            value = int(raw, 10)
        except ValueError:
            raise ConfigError(Reason.RANGE, f"정수가 아님: {raw!r}") from None
    elif item.vtype == "f32":
        try:
            value = float(raw)
        except ValueError:
            raise ConfigError(Reason.RANGE, f"실수가 아님: {raw!r}") from None
        # 🔴 float("nan") / float("inf") 는 파싱을 통과한다. 그리고 NaN 은
        # 아래 범위 검사도 뚫는다 — nan < min 도 nan > max 도 False 이기
        # 때문이다. 여기서 막지 않으면 변환식에 NaN 이 들어가 모든 측정값이
        # 조용히 NaN 이 된다.
        if not math.isfinite(value):
            raise ConfigError(Reason.RANGE, f"유한한 실수가 아님: {raw!r}")
    else:
        raise ConfigError(Reason.RANGE, f"알 수 없는 타입: {item.vtype}")

    if item.minimum is not None and value < item.minimum:
        raise ConfigError(Reason.RANGE, f"최소 {item.minimum}, 받음 {value}")
    if item.maximum is not None and value > item.maximum:
        raise ConfigError(Reason.RANGE, f"최대 {item.maximum}, 받음 {value}")
    return value


def _default_field_mask() -> int:
    mask = 0
    for bit, _name, default, _label in FIELD_BITS:
        if default:
            mask |= 1 << bit
    return mask


def default_store(path: Path | None = None) -> ConfigStore:
    """스펙 §6.1 의 설정 항목 전체를 담은 저장소를 만든다."""
    mask = _default_field_mask()
    items: list[SimConfigItem] = [
        SimConfigItem("dev.id", "dev", "str", "1", "1", maximum=15,
                      label="장치 ID"),
        SimConfigItem("tx.fields", "tx", "u32", mask, mask,
                      minimum=0, maximum=0xFFFFFFFF, label="NDJSON 필드 마스크"),
        SimConfigItem("tx.period_ms", "tx", "u16", 100, 100,
                      minimum=10, maximum=10000, unit="ms", label="전송 주기"),
        SimConfigItem("tx.float_digits", "tx", "u8", 4, 4,
                      minimum=2, maximum=6, label="실수 자릿수"),
        SimConfigItem("adc.pga", "adc", "enum", 1, 1,
                      choices=(1, 2, 4, 8, 16, 32, 64), label="PGA"),
        SimConfigItem("adc.drate", "adc", "enum", 60, 60,
                      choices=DRATE_CHOICES, unit="SPS", label="데이터율"),
        SimConfigItem("pwr.24v", "pwr", "bool", False, False,
                      label="24V 레일 (PD8)"),
        SimConfigItem("pwr.14v9", "pwr", "bool", False, False,
                      label="14.9V 레일 (PD9)"),
        SimConfigItem(
            "pwr.5v", "pwr", "bool", True, True,
            readonly=True, interlocked=True, label="5V 레일 (PD10)",
            note="쿨링 팬이 5V 레일 직결이라 끌 수 없다 (데이터시트 §5.14)",
        ),
        SimConfigItem("pwr.seq_delay_ms", "pwr", "u16", 500, 500,
                      minimum=0, maximum=5000, unit="ms", label="레일 기동 간격"),
    ]

    for ch in range(7):
        connector = ch + 3                       # AIN0 → J3
        items += [
            SimConfigItem(f"ain{ch}.enabled", "ain", "bool",
                          ch == 0, ch == 0, label=f"J{connector} 사용"),
            SimConfigItem(f"ain{ch}.period_ms", "ain", "u16", 100, 100,
                          minimum=10, maximum=60000, unit="ms",
                          label=f"J{connector} 수집 주기"),
            SimConfigItem(f"ain{ch}.zero", "ain", "f32", 4.0, 4.0,
                          unit="mA", label=f"J{connector} 영점"),
            SimConfigItem(f"ain{ch}.scale", "ain", "f32", 1.0, 1.0,
                          label=f"J{connector} 스케일"),
            SimConfigItem(f"ain{ch}.unit", "ain", "str", "", "",
                          maximum=7, label=f"J{connector} 단위"),
        ]

    return ConfigStore(items, path)
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `python -m pytest host/tests/test_config_store.py -v`
Expected: PASS (15 passed)

- [ ] **Step 5: 커밋**

```bash
git add tools/simulator/config_store.py host/tests/test_config_store.py
git commit -m "feat(sim): 설정 저장소 — 검증, 인터록, 영속

펌웨어 config_store 모듈의 기준 구현이다. 여기서 정한 항목·범위·기본값이
그대로 펌웨어로 간다.

pwr.5v 는 읽기 전용이면서 인터록이다. 끄기 시도에 READONLY 가 아니라
INTERLOCK 을 돌려주는 이유는 사용자가 '왜 안 되는지'를 알아야 하기
때문이다 — 쿨링 팬이 5V 레일 직결이라는 하드웨어 사실이 사유가 된다.

파일이 깨지면 공장 기본값으로 복구하고 load_failed 로 알린다."
```

---

## Task 7: 시뮬레이터 텔레메트리와 필드 마스크

**Files:**
- Create: `tools/simulator/telemetry.py`
- Test: `host/tests/test_telemetry.py`

**Interfaces:**
- Consumes: `tools.simulator.config_store.ConfigStore`, `FIELD_BITS`
- Produces:
  - `build_ain_record(store, *, channel, seq, t_ms, raw, capture_counter) -> dict`
  - `render(rec: dict) -> str` — JSON 한 줄
  - `raw_to_ma(raw: int) -> float`, `ma_to_value(ma, zero, scale) -> float`
  - `ADS1256_FULL_SCALE`, `SHUNT_OHMS`, `VREF_V`

- [ ] **Step 1: 실패하는 테스트 작성**

`host/tests/test_telemetry.py`:
```python
import json

from tools.simulator.config_store import default_store
from tools.simulator.telemetry import (
    ADS1256_FULL_SCALE,
    build_ain_record,
    ma_to_value,
    raw_to_ma,
    render,
)

MANDATORY = {"schema_ver", "seq", "t", "type"}


def _rec(store, **kw):
    args = dict(channel=0, seq=1, t_ms=1772200855875, raw=ADS1256_FULL_SCALE // 2,
                capture_counter=123456789)
    args.update(kw)
    return build_ain_record(store, **args)


def test_mandatory_fields_always_present_even_with_zero_mask():
    store = default_store()
    store.set("tx.fields", "0")
    rec = _rec(store)
    assert MANDATORY <= set(rec)


def test_default_mask_includes_raw_and_excludes_device_id():
    store = default_store()
    rec = _rec(store)
    assert "raw" in rec
    assert "device_id" not in rec


def test_enabling_device_id_bit_adds_field():
    store = default_store()
    store.set("tx.fields", str(store.field_mask | (1 << 0)))
    assert "device_id" in _rec(store)


def test_connector_id_maps_channel_to_j_number():
    """AIN0 은 J3 이다 (데이터시트 §5.3)."""
    store = default_store()
    assert _rec(store, channel=0)["connector_id"] == 3
    assert _rec(store, channel=6)["connector_id"] == 9


def test_raw_to_ma_at_20ma_full_loop():
    """20 mA × 120 Ω = 2.40 V. VREF 2.5 V 기준 코드로 환산한 값."""
    raw = round(2.40 / 2.5 * ADS1256_FULL_SCALE)
    assert abs(raw_to_ma(raw) - 20.0) < 0.01


def test_raw_to_ma_at_4ma():
    raw = round(0.48 / 2.5 * ADS1256_FULL_SCALE)
    assert abs(raw_to_ma(raw) - 4.0) < 0.01


def test_ma_to_value_applies_zero_and_scale():
    assert abs(ma_to_value(12.0, zero=4.0, scale=0.5) - 4.0) < 1e-9


def test_float_digits_setting_controls_rounding():
    store = default_store()
    store.set("tx.float_digits", "2")
    rec = _rec(store)
    text = json.dumps(rec["ma"])
    assert len(text.split(".")[-1]) <= 2


def test_raw_is_integer_and_never_rounded():
    """raw 가 원본이다. 자릿수 설정이 raw 를 건드리면 안 된다."""
    store = default_store()
    store.set("tx.float_digits", "2")
    rec = _rec(store, raw=8388607)
    assert rec["raw"] == 8388607
    assert isinstance(rec["raw"], int)


def test_render_produces_single_line():
    store = default_store()
    line = render(_rec(store))
    assert "\n" not in line
    assert json.loads(line)["type"] == "ain"


def test_smaller_mask_produces_shorter_line():
    """필드 마스크가 대역폭 대책이라는 전제를 지킨다."""
    store = default_store()
    full = len(render(_rec(store)))
    store.set("tx.fields", "0")
    minimal = len(render(_rec(store)))
    assert minimal < full
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `python -m pytest host/tests/test_telemetry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.simulator.telemetry'`

- [ ] **Step 3: `telemetry.py` 작성**

`tools/simulator/telemetry.py`:
```python
"""텔레메트리 레코드 생성과 필드 마스크 적용.

전기적 근거 (데이터시트 §5.3):
  4~20 mA 루프 전류 → 120 Ω 0.1% 션트 → 전압
  4 mA = 0.48 V, 20 mA = 2.40 V
  ADS1256 외부 기준 2.5 V (ADR4525), 단일단 측정, PGA=1

raw 가 원본이다. ma·value 는 편의용 파생값이며 tx.float_digits 자릿수로
반올림된다. Q2 serializer 가 소수점 2자리로 고정돼 24비트 분해능을 버렸던
문제(스펙 §5.7)를 되풀이하지 않기 위해 raw 는 절대 반올림하지 않는다.
"""

import json

from tools.simulator.config_store import FIELD_BITS, ConfigStore
from host.core.records import SCHEMA_VER

#: ADS1256 은 24비트 양방향. 단일단 양의 전 범위 코드.
ADS1256_FULL_SCALE = (1 << 23) - 1

SHUNT_OHMS = 120.0
VREF_V = 2.5

#: AIN0 은 J3 에 대응 (데이터시트 §5.3)
CONNECTOR_OFFSET = 3

_BIT_OF = {name: bit for bit, name, _d, _l in FIELD_BITS}


def raw_to_ma(raw: int) -> float:
    """ADS1256 원시 코드를 루프 전류(mA)로 환산한다."""
    volts = raw / ADS1256_FULL_SCALE * VREF_V
    return volts / SHUNT_OHMS * 1000.0


def ma_to_value(ma: float, zero: float, scale: float) -> float:
    """전류를 물리량으로 환산한다."""
    return (ma - zero) * scale


def build_ain_record(
    store: ConfigStore,
    *,
    channel: int,
    seq: int,
    t_ms: int,
    raw: int,
    capture_counter: int,
) -> dict:
    """마스크에 따라 필드를 골라 담은 ain 레코드를 만든다."""
    mask = store.field_mask
    digits = int(store.get("tx.float_digits"))

    def on(name: str) -> bool:
        return bool(mask & (1 << _BIT_OF[name]))

    # 규격 §7.1 — 이 넷은 마스크와 무관하게 항상 들어간다.
    rec: dict = {
        "schema_ver": SCHEMA_VER,
        "seq": seq,
        "t": t_ms,
        "type": "ain",
    }

    if on("connector_id"):
        rec["connector_id"] = channel + CONNECTOR_OFFSET
    if on("raw"):
        rec["raw"] = int(raw)                      # 원본 — 반올림하지 않는다

    ma = raw_to_ma(raw)
    if on("ma"):
        rec["ma"] = round(ma, digits)
    if on("value"):
        zero = float(store.get(f"ain{channel}.zero"))
        scale = float(store.get(f"ain{channel}.scale"))
        rec["value"] = round(ma_to_value(ma, zero, scale), digits)
    if on("unit"):
        rec["unit"] = store.get(f"ain{channel}.unit")
    if on("status"):
        rec["status"] = 0
    if on("device_id"):
        rec["device_id"] = store.get("dev.id")
    if on("time_source"):
        rec["time_source"] = "device_clock"
    if on("time_quality"):
        rec["time_quality"] = 0
    if on("capture_counter"):
        rec["capture_counter"] = capture_counter

    return rec


def render(rec: dict) -> str:
    """레코드를 NDJSON 한 줄로 만든다 (줄바꿈 없음)."""
    return json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `python -m pytest host/tests/test_telemetry.py -v`
Expected: PASS (11 passed)

- [ ] **Step 5: 커밋**

```bash
git add tools/simulator/telemetry.py host/tests/test_telemetry.py
git commit -m "feat(sim): 텔레메트리 생성과 필드 마스크

4~20mA → 120Ω 션트 → ADS1256(VREF 2.5V) 환산을 데이터시트 §5.3 기준으로
구현하고, 4mA/20mA 지점을 테스트로 고정했다.

raw 는 절대 반올림하지 않는다. Q2 serializer 가 소수점 2자리 고정이라
24비트 분해능을 통째로 버렸던 문제를 되풀이하지 않기 위해서다.
ma·value 만 tx.float_digits 를 따른다.

seq/t/type/schema_ver 는 마스크가 0 이어도 항상 출력한다."
```

---

## Task 8: 시뮬레이터 본체 — 모드와 명령 디스패치

**Files:**
- Create: `tools/simulator/device_sim.py`
- Test: `host/tests/test_device_sim.py`

**Interfaces:**
- Consumes: `framing`, `errors`, `config_store`, `telemetry`
- Produces:
  - `Mode` — `CONFIG` / `RUN` 문자열 상수
  - `DeviceSim(store, *, fw="0.1.0", board_rev="2.0")`
  - `.feed(line: str) -> list[str]` — 명령 한 줄 → 응답 줄들
  - `.tick(now_ms: int) -> list[str]` — 주기 텔레메트리 + 모드 판정
  - `.mode -> str`

시계를 인자로 받는다. 시뮬레이터 안에서 시각을 읽지 않으므로 테스트가
결정적이다.

- [ ] **Step 1: 실패하는 테스트 작성**

`host/tests/test_device_sim.py`:
```python
from host.core.config_schema import parse_catalog
from host.core.framing import build_command, parse_line
from host.core.records import parse_record
from tools.simulator.config_store import default_store
from tools.simulator.device_sim import HB_TIMEOUT_MS, DeviceSim, Mode


def _sim() -> DeviceSim:
    return DeviceSim(default_store())


def _sack(lines: list[str]):
    """응답 줄들 중 첫 $SACK 를 Command 로 반환한다."""
    for line in lines:
        if line.startswith("$SACK"):
            return parse_line(line)
    raise AssertionError(f"$SACK 없음: {lines}")


# ------------------------------------------------------------------ 모드
def test_boots_in_run_mode():
    assert _sim().mode == Mode.RUN


def test_hb_switches_to_config():
    sim = _sim()
    sim.feed(build_command("HB"))
    assert sim.mode == Mode.CONFIG


def test_config_reverts_to_run_after_hb_timeout():
    sim = _sim()
    sim.feed(build_command("HB"))
    sim.tick(HB_TIMEOUT_MS + 1)
    assert sim.mode == Mode.RUN


def test_hb_before_timeout_keeps_config():
    sim = _sim()
    sim.feed(build_command("HB"))
    sim.tick(HB_TIMEOUT_MS - 1)
    assert sim.mode == Mode.CONFIG


def test_mode_boundary_is_strictly_greater_than():
    """🔴 경계는 > 이지 >= 가 아니다 (규격 §6.2).

    정확히 3000 ms 경과한 순간은 아직 CONFIG 다. 경계를 명시하지 않으면
    펌웨어와 호스트가 1 ms 차이로 다른 모드를 표시하고, 그 차이가
    ERR,MODE 로 나타나 재현 안 되는 버그가 된다.
    """
    sim = _sim()
    sim.feed(build_command("HB"))
    sim.tick(HB_TIMEOUT_MS)              # 정확히 경계
    assert sim.mode == Mode.CONFIG
    sim.tick(HB_TIMEOUT_MS + 1)          # 한 틱 넘김
    assert sim.mode == Mode.RUN


def test_hb_emits_no_sack():
    """정상 하트비트에는 응답하지 않는다 (규격 §4)."""
    assert _sim().feed(build_command("HB")) == []


def test_corrupt_hb_does_not_enter_config_mode():
    """🔴 회귀 시험 — 체크섬이 깨진 \\$HB 는 모드를 바꾸면 안 된다.

    Q2 host_link.c:183-187 은 체크섬 검증 전에 host_hb_last_ms 를 갱신한다.
    Q2 에서는 단순 링크 생존 신호라 무해했지만, 우리 설계에서 \\$HB 는
    CONFIG 모드를 여는 열쇠다. 그대로 이식하면 이 시험이 실패한다.
    """
    sim = _sim()
    sim.feed("$HB*FF\r\n")                  # 올바른 체크섬은 0A
    assert sim.mode == Mode.RUN


def test_corrupt_hb_cannot_unlock_config_writes():
    """깨진 하트비트 뒤의 설정 변경은 여전히 거부돼야 한다."""
    sim = _sim()
    sim.feed("$HB*FF\r\n")
    cmd = _sack(sim.feed(build_command("CFG", "SET", "tx.period_ms", "250")))
    assert cmd.args == ("CFG", "ERR", "MODE")
    assert sim.store.get("tx.period_ms") == 100


# --------------------------------------------------------------- 체크섬
def test_bad_checksum_returns_checksum_error():
    sim = _sim()
    cmd = _sack(sim.feed("$CFG,GET,tx.period_ms*00\r\n"))
    assert cmd.args == ("CFG", "ERR", "CHECKSUM")


def test_unparseable_line_is_dropped_silently():
    """verb 를 못 읽을 만큼 깨진 줄은 조용히 버린다. 링크는 유지."""
    assert _sim().feed("garbage without dollar\r\n") == []


def test_corrupt_line_with_control_char_does_not_kill_the_loop():
    """🔴 깨진 줄에 제어문자가 있어도 feed() 가 예외를 밖으로 내지 않는다.

    건져낸 verb 는 곧바로 build_line() 으로 들어가는데 거기서 제어문자가
    거부된다. 잡음으로 낀 TAB 하나면 충분하고, strip() 은 양끝만 지운다.
    serial_server 와 LoopbackTransport 가 feed() 를 맨몸으로 부르므로
    시뮬레이터 프로세스가 통째로 죽는다.

    규격 §3: "verb 를 읽을 수 없을 만큼 깨졌으면 조용히 버린다 (링크는 유지)".
    """
    sim = _sim()
    for bad in ("$C\x00FG,X*00\r\n", "$A\tB*00\r\n", "$X\x1bY*00\r\n"):
        assert sim.feed(bad) == [], bad
    assert sim.mode == Mode.RUN          # 링크가 살아 있다


def test_oversized_verb_is_not_echoed_back():
    """🔴 공격자가 정한 길이의 문자열을 응답에 되싣지 않는다.

    `"$" + "A"*4000 + "*00"` 은 4000개의 XOR 이 0x00 이라 **체크섬이 맞는
    정상 줄**로 파싱된다. 깨진 줄 경로가 아니라 "모르는 명령" 경로로 들어오고,
    방어가 없으면 UNKNOWN_KEY 응답에 4000자가 그대로 실린다.

    파이썬에서는 그저 긴 문자열이지만 C 로 옮기면 고정 버퍼 오버플로다.
    모르는 명령에 응답하는 것 자체는 맞으므로, verb 만 잘라서 돌려준다.
    """
    out = _sim().feed("$" + "A" * 4000 + "*00\r\n")
    assert len(out) == 1
    assert len(out[0]) < 40                      # 4000자가 되울리지 않는다
    assert parse_line(out[0]).args == ("?", "ERR", "UNKNOWN_KEY")


# ----------------------------------------------------------------- 조회
def test_cfg_list_is_allowed_in_run_mode():
    sim = _sim()
    assert sim.mode == Mode.RUN
    lines = sim.feed(build_command("CFG", "LIST"))
    catalog = [ln for ln in lines if ln.startswith("{")]
    schema = parse_catalog(catalog)
    assert "tx.period_ms" in schema.items
    assert _sack(lines).args == ("CFG", "OK")


def test_cfg_get_returns_current_value():
    sim = _sim()
    lines = sim.feed(build_command("CFG", "GET", "tx.period_ms"))
    rec = parse_record(next(ln for ln in lines if ln.startswith("{")))
    assert rec["type"] == "cfg_value"
    assert rec["key"] == "tx.period_ms"
    assert rec["cur"] == 100


def test_cfg_get_unknown_key():
    sim = _sim()
    assert _sack(sim.feed(build_command("CFG", "GET", "nope"))).args == (
        "CFG", "ERR", "UNKNOWN_KEY",
    )


def test_id_returns_firmware_info():
    sim = _sim()
    lines = sim.feed(build_command("ID"))
    rec = parse_record(next(ln for ln in lines if ln.startswith("{")))
    assert rec["type"] == "id"
    assert rec["fw"] == "0.1.0"


def test_stat_reports_mode_and_rails():
    sim = _sim()
    lines = sim.feed(build_command("STAT"))
    rec = parse_record(next(ln for ln in lines if ln.startswith("{")))
    assert rec["mode"] == Mode.RUN
    assert rec["rails"]["v5"] is True


def test_stat_declares_the_time_base():
    """🔴 `t` 의 기준점을 호스트가 알 수 있어야 한다 (규격 §7.1.2).

    `t` 는 time_source 에 따라 UTC epoch 이거나 부팅 후 경과 ms 다.
    명령 응답 레코드에는 time_source 를 실을 필드 마스크가 없으므로
    $STAT 이 그 답을 주는 유일한 곳이다. 없으면 호스트가 t=0 을 보고
    1970년인지 방금 부팅한 것인지 구분할 수 없다.
    """
    rec = parse_record(
        next(ln for ln in _sim().feed(build_command("STAT")) if ln.startswith("{"))
    )
    assert rec["time_source"] == "device_clock"
    assert rec["time_quality"] == 0


def test_command_response_t_is_uptime_not_epoch():
    """부팅 직후 t=0 은 '1970년' 이 아니라 '부팅 후 0ms' 로 정확하다."""
    sim = _sim()
    rec0 = parse_record(
        next(ln for ln in sim.feed(build_command("ID")) if ln.startswith("{"))
    )
    assert rec0["t"] == 0
    sim.tick(500)
    rec1 = parse_record(
        next(ln for ln in sim.feed(build_command("ID")) if ln.startswith("{"))
    )
    assert rec1["t"] == 500


# ----------------------------------------------------------------- 변경
def test_cfg_set_rejected_in_run_mode():
    sim = _sim()
    cmd = _sack(sim.feed(build_command("CFG", "SET", "tx.period_ms", "250")))
    assert cmd.args == ("CFG", "ERR", "MODE")


def test_cfg_set_accepted_in_config_mode():
    sim = _sim()
    sim.feed(build_command("HB"))
    assert _sack(sim.feed(build_command("CFG", "SET", "tx.period_ms", "250"))).args == (
        "CFG", "OK",
    )
    assert sim.store.get("tx.period_ms") == 250


def test_cfg_set_out_of_range():
    sim = _sim()
    sim.feed(build_command("HB"))
    assert _sack(sim.feed(build_command("CFG", "SET", "tx.period_ms", "1"))).args == (
        "CFG", "ERR", "RANGE",
    )


def test_cfg_set_pwr_5v_off_is_interlocked():
    sim = _sim()
    sim.feed(build_command("HB"))
    assert _sack(sim.feed(build_command("CFG", "SET", "pwr.5v", "false"))).args == (
        "CFG", "ERR", "INTERLOCK",
    )


def test_cfg_save_and_reset_require_config_mode():
    sim = _sim()
    assert _sack(sim.feed(build_command("CFG", "SAVE"))).args[-1] == "MODE"
    assert _sack(sim.feed(build_command("CFG", "RESET"))).args[-1] == "MODE"


# ----------------------------------------------------- 텔레메트리 / seq
def test_tick_emits_telemetry_for_enabled_channels_only():
    sim = _sim()
    lines = sim.tick(100)
    recs = [parse_record(ln) for ln in lines if ln.startswith("{")]
    ains = [r for r in recs if r["type"] == "ain"]
    assert len(ains) == 1                      # 기본은 ain0 만 enabled
    assert ains[0]["connector_id"] == 3


def test_enabling_second_channel_adds_record():
    sim = _sim()
    sim.feed(build_command("HB"))
    sim.feed(build_command("CFG", "SET", "ain1.enabled", "true"))
    recs = [parse_record(ln) for ln in sim.tick(100) if ln.startswith("{")]
    assert {r["connector_id"] for r in recs if r["type"] == "ain"} == {3, 4}


def test_seq_increases_monotonically_across_ticks():
    sim = _sim()
    seqs = []
    for now in (100, 200, 300):
        seqs += [
            parse_record(ln)["seq"]
            for ln in sim.tick(now)
            if ln.startswith("{")
        ]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


def test_tick_respects_period_setting():
    """주기가 안 됐으면 텔레메트리를 내지 않는다."""
    sim = _sim()
    sim.tick(100)
    assert [ln for ln in sim.tick(150) if ln.startswith("{")] == []
    assert [ln for ln in sim.tick(200) if ln.startswith("{")] != []


def test_tick_emits_hb_once_per_second():
    sim = _sim()
    assert any(ln.startswith("$HB") for ln in sim.tick(1000))
    assert not any(ln.startswith("$HB") for ln in sim.tick(1500))
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `python -m pytest host/tests/test_device_sim.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.simulator.device_sim'`

- [ ] **Step 3: `device_sim.py` 작성**

`tools/simulator/device_sim.py`:
```python
"""가상 STM32 v2.0 보드.

펌웨어와 같은 상태 기계를 구현한다 — CONFIG/RUN 모드, 설정 검증, 인터록,
필드 마스크, seq 증가. 이후 펌웨어의 기준 구현이자 GUI 의 개발용 상대역이다.

시각을 스스로 읽지 않고 tick(now_ms) 로 받는다. 테스트가 결정적이 되고,
시리얼 어댑터가 시계를 소유하게 되어 계층이 깔끔해진다.
"""

import json
import math

from host.core.errors import ConfigError, ProtocolError, Reason
from host.core.framing import build_command, parse_line
from host.core.records import SCHEMA_VER
from tools.simulator.config_store import ConfigStore
from tools.simulator.telemetry import ADS1256_FULL_SCALE, build_ain_record, render

#: 규격 §6 — 이 시간 안에 $HB 를 못 받으면 RUN 으로 내려간다.
HB_TIMEOUT_MS = 3000

#: 보드가 호스트에게 보내는 생존 신호 주기
HB_EMIT_PERIOD_MS = 1000

AIN_CHANNELS = 7


class Mode:
    CONFIG = "CONFIG"
    RUN = "RUN"


#: CONFIG 모드에서만 받는 $CFG 하위 명령 (규격 §4)
_CONFIG_ONLY = frozenset({"SET", "SAVE", "RESET"})

#: 깨진 줄에서 건져낸 verb 에 허용되는 문자. 실제 verb 는 전부 대문자다.
_ALLOWED_VERB_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ")


class DeviceSim:
    def __init__(self, store: ConfigStore, *, fw: str = "0.1.0",
                 board_rev: str = "2.0"):
        self.store = store
        self.fw = fw
        self.board_rev = board_rev

        self._seq = 0
        self._now_ms = 0
        self._last_hb_rx_ms: int | None = None
        self._last_hb_tx_ms = 0
        self._last_emit_ms = 0
        self._boot_ms = 0

    # ------------------------------------------------------------------ 모드
    @property
    def mode(self) -> str:
        if self._last_hb_rx_ms is None:
            return Mode.RUN
        if self._now_ms - self._last_hb_rx_ms > HB_TIMEOUT_MS:
            return Mode.RUN
        return Mode.CONFIG

    # ------------------------------------------------------------- 수신 처리
    def feed(self, line: str) -> list[str]:
        """호스트가 보낸 줄 하나를 처리하고 응답 줄들을 반환한다."""
        try:
            cmd = parse_line(line)
        except ProtocolError:
            # verb 를 알 수 없으면 어느 명령에 대한 거부인지 말할 수 없다.
            # 체크섬만 틀린 경우는 verb 를 읽을 수 있으므로 아래에서 처리한다.
            verb = _verb_of_broken_line(line)
            if verb is None:
                return []
            return [self._sack(verb, "ERR", Reason.CHECKSUM)]

        handler = {
            "HB": self._on_hb,
            "ID": self._on_id,
            "STAT": self._on_stat,
            "CFG": self._on_cfg,
        }.get(cmd.verb)

        if handler is None:
            return [self._sack(cmd.verb, "ERR", Reason.UNKNOWN_KEY)]
        return handler(cmd.args)

    def _on_hb(self, _args: tuple[str, ...]) -> list[str]:
        self._last_hb_rx_ms = self._now_ms
        return []                                  # 하트비트에는 응답하지 않는다

    def _on_id(self, _args: tuple[str, ...]) -> list[str]:
        return [
            self._json(
                type="id",
                device_id=self.store.get("dev.id"),
                fw=self.fw,
                board_rev=self.board_rev,
            ),
            self._sack("ID", "OK"),
        ]

    def _on_stat(self, _args: tuple[str, ...]) -> list[str]:
        return [
            self._json(
                type="stat",
                mode=self.mode,
                fw=self.fw,
                board_rev=self.board_rev,
                # 🔴 `t` 의 기준점을 호스트가 알 수 있게 여기서 알려준다.
                # 규격 §7.1.2 대로 `t` 는 time_source 에 따라 UTC epoch 이거나
                # 부팅 후 경과 ms 다. 텔레메트리는 필드 마스크로 time_source 를
                # 실어 보낼 수 있지만 명령 응답에는 그 자리가 없다. $STAT 이
                # 그 답을 주는 곳이다 — 호스트는 연결 직후 한 번 물어보면 된다.
                time_source="device_clock",
                time_quality=0,
                uptime_ms=self._now_ms - self._boot_ms,
                rails={
                    "v24": self.store.get("pwr.24v"),
                    "v14v9": self.store.get("pwr.14v9"),
                    "v5": self.store.get("pwr.5v"),
                },
                queues=[
                    {"ch": ch, "depth": 0, "peak": 0, "drops": 0}
                    for ch in range(AIN_CHANNELS)
                    if self.store.get(f"ain{ch}.enabled")
                ],
            ),
            self._sack("STAT", "OK"),
        ]

    def _on_cfg(self, args: tuple[str, ...]) -> list[str]:
        if not args:
            return [self._sack("CFG", "ERR", Reason.UNKNOWN_KEY)]

        sub = args[0].upper()

        if sub in _CONFIG_ONLY and self.mode != Mode.CONFIG:
            return [self._sack("CFG", "ERR", Reason.MODE)]

        try:
            if sub == "LIST":
                return [*self.store.catalog_lines(), self._sack("CFG", "OK")]

            if sub == "GET":
                key = args[1] if len(args) > 1 else ""
                value = self.store.get(key)
                return [
                    self._json(type="cfg_value", key=key, cur=value),
                    self._sack("CFG", "OK"),
                ]

            if sub == "SET":
                if len(args) < 3:
                    return [self._sack("CFG", "ERR", Reason.RANGE)]
                # 값에 쉼표가 들어갈 수 있으므로 나머지를 전부 붙인다.
                self.store.set(args[1], ",".join(args[2:]))
                return [self._sack("CFG", "OK")]

            if sub == "SAVE":
                self.store.save()
                return [self._sack("CFG", "OK")]

            if sub == "RESET":
                self.store.reset()
                return [self._sack("CFG", "OK")]

        except ConfigError as exc:
            return [self._sack("CFG", "ERR", exc.reason)]

        return [self._sack("CFG", "ERR", Reason.UNKNOWN_KEY)]

    # --------------------------------------------------------------- 주기 처리
    def tick(self, now_ms: int) -> list[str]:
        """시각을 진행시키고 이번에 내보낼 줄들을 반환한다."""
        self._now_ms = now_ms
        out: list[str] = []

        if now_ms - self._last_hb_tx_ms >= HB_EMIT_PERIOD_MS:
            self._last_hb_tx_ms = now_ms
            out.append(build_command("HB").rstrip("\r\n"))

        period = int(self.store.get("tx.period_ms"))
        if now_ms - self._last_emit_ms >= period:
            self._last_emit_ms = now_ms
            out.extend(self._emit_telemetry(now_ms))

        return out

    def _emit_telemetry(self, now_ms: int) -> list[str]:
        lines: list[str] = []
        for ch in range(AIN_CHANNELS):
            if not self.store.get(f"ain{ch}.enabled"):
                continue
            self._seq += 1
            lines.append(
                render(
                    build_ain_record(
                        self.store,
                        channel=ch,
                        seq=self._seq,
                        t_ms=now_ms,
                        raw=_synthetic_raw(ch, now_ms),
                        capture_counter=now_ms * 1000,
                    )
                )
            )
        return lines

    # ------------------------------------------------------------------ 보조
    def _sack(self, verb: str, *rest: str) -> str:
        """$SACK 를 만든다. **모든 응답이 여기를 지난다.**

        🔴 verb 를 그대로 되싣지 않는다.

        길고 이상한 verb 는 깨진 줄로만 오는 게 아니다. `"$" + "A"*4000 + "*00"`
        은 4000개의 XOR 이 0x00 이라 **체크섬이 맞는 정상 줄**로 파싱되고,
        모르는 명령이므로 UNKNOWN_KEY 응답에 그 4000자가 그대로 실린다.

        파이썬에서는 그저 긴 문자열이지만, C 로 옮기면 공격자가 길이를 정하는
        데이터를 고정 버퍼에 넣는 전형적인 오버플로다. 이 모듈이 펌웨어 참조라
        길목에서 잘라둔다.
        """
        if len(verb) > _MAX_SALVAGED_VERB or any(
            c not in _ALLOWED_VERB_CHARS for c in verb
        ):
            verb = "?"
        return build_command("SACK", verb, *rest).rstrip("\r\n")

    def _json(self, **fields) -> str:
        rec = {"schema_ver": SCHEMA_VER, "seq": 0, "t": self._now_ms}
        rec.update(fields)
        return json.dumps(rec, ensure_ascii=False, separators=(",", ":"))


#: 건져낸 verb 의 최대 길이. 실제 verb 중 가장 긴 것이 `SACK`(4자)이므로
#: 넉넉히 잡아도 이 정도면 충분하다.
_MAX_SALVAGED_VERB = 12


def _verb_of_broken_line(line: str) -> str | None:
    """체크섬이 틀린 줄에서 verb 만 건져낸다. 못 건지면 None.

    🔴 **건져낸 verb 를 검증하지 않고 돌려주면 안 된다.**

    이 값은 곧바로 `_sack()` → `build_command()` → `build_line()` 로 들어가는데,
    `build_line` 은 제어문자를 거부하며 예외를 던진다. 잡음으로 깨진 줄 안에
    TAB 하나만 섞여 있어도 그 예외가 `feed()` 밖으로 튀어나가 **수신 루프가
    통째로 죽는다.** `strip()` 은 양끝만 지우므로 중간에 낀 제어문자는 남는다.

    규격 §3 은 이 경로를 "verb 를 읽을 수 없을 만큼 깨졌으면 조용히 버린다
    (링크는 유지)" 로 정한다. 깨진 입력에 크래시하는 것은 이 모듈이 펌웨어
    참조라는 점에서 가장 베끼면 안 되는 동작이다.

    길이도 제한한다. 파이썬에서는 무해하지만 C 로 옮기면 공격자가 정한
    길이의 문자열을 고정 버퍼에 넣는 전형적인 오버플로가 된다.
    """
    stripped = line.strip()
    if not stripped.startswith("$") or "*" not in stripped:
        return None
    payload = stripped[1 : stripped.rfind("*")]
    verb = payload.split(",")[0]
    if not verb or len(verb) > _MAX_SALVAGED_VERB:
        return None
    if any(c not in _ALLOWED_VERB_CHARS for c in verb):
        return None
    return verb


def _synthetic_raw(channel: int, now_ms: int) -> int:
    """채널마다 위상이 다른 사인파. 4~20 mA 범위를 오간다."""
    phase = now_ms / 5000.0 + channel * 0.7
    ma = 12.0 + 8.0 * math.sin(phase)             # 4 ~ 20 mA
    volts = ma / 1000.0 * 120.0                   # 션트 120 Ω
    return int(volts / 2.5 * ADS1256_FULL_SCALE)
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `python -m pytest host/tests/test_device_sim.py -v`
Expected: PASS (21 passed)

- [ ] **Step 5: 전체 테스트 실행**

Run: `python -m pytest -v`
Expected: PASS (전체 통과, 76 passed 내외)

- [ ] **Step 6: 커밋**

```bash
git add tools/simulator/device_sim.py host/tests/test_device_sim.py
git commit -m "feat(sim): 가상 보드 — 모드 전환과 명령 디스패치

펌웨어와 같은 상태 기계를 구현한다. CONFIG/RUN 은 케이블이 아니라
\$HB 3초 타임아웃으로 판정한다 — H723 은 VBUS 가 배선되지 않아 USB
연결을 감지할 수 없기 때문이다(넷리스트 확인).

조회(LIST/GET/ID/STAT)는 RUN 에서도 열려 있고 변경(SET/SAVE/RESET)만
막힌다. 응답 없이 설정이 바뀌는 것을 막기 위해서다.

시각은 tick(now_ms) 으로 주입받아 테스트가 결정적이다."
```

---

## Task 9: 시리얼 어댑터와 Board Service

**Files:**
- Create: `tools/simulator/serial_server.py`
- Create: `host/service/board_service.py`
- Test: `host/tests/test_board_service.py`

**Interfaces:**
- Consumes: `DeviceSim`, `framing`, `records`
- Produces:
  - `Transport` — 프로토콜 클래스: `.write(data: str)`, `.read_lines() -> Iterator[str]`, `.close()`
  - `LoopbackTransport(sim: DeviceSim)` — 시뮬레이터를 직접 물리는 테스트용 트랜스포트
  - `SerialTransport(port: str, baud: int = 115200)`
  - `BoardService(transport, *, clock)` — `.start()`, `.stop()`, `.send(verb, *args) -> Command`, `.records: list[dict]`, `.seq_tracker`, `.mode`
  - `run_simulator_server(port: str)` — 시뮬레이터를 시리얼에 붙여 돌린다

- [ ] **Step 1: 실패하는 테스트 작성**

`host/tests/test_board_service.py`:
```python
import pytest

from host.core.errors import ProtocolError
from host.core.framing import build_command
from host.service.board_service import BoardService, LoopbackTransport
from tools.simulator.config_store import default_store
from tools.simulator.device_sim import HB_TIMEOUT_MS, DeviceSim, Mode


class FakeClock:
    def __init__(self) -> None:
        self.now_ms = 0

    def __call__(self) -> int:
        return self.now_ms

    def advance(self, ms: int) -> None:
        self.now_ms += ms


@pytest.fixture
def rig():
    clock = FakeClock()
    sim = DeviceSim(default_store())
    svc = BoardService(LoopbackTransport(sim), clock=clock)
    return svc, sim, clock


def test_send_returns_matching_sack(rig):
    svc, _sim, _clock = rig
    ack = svc.send("CFG", "GET", "tx.period_ms")
    assert ack.args == ("CFG", "OK")


def test_send_ignores_a_stale_ack_for_another_command(rig):
    """🔴 verb 를 대조하지 않으면 남의 응답을 내 성공으로 착각한다.

    직렬 링크에서 앞선 명령의 응답이 한 박자 늦게 도착하는 것은 정상이다.
    그게 버퍼 앞자리에 있으면 보드가 RANGE 로 거부한 설정 쓰기를
    성공으로 보고한다. 설정 쓰기의 조용한 거짓 성공은 최악의 실패 방식이고
    GUI 가 이 계층 위에 올라간다.
    """
    svc, sim, _clock = rig
    svc.heartbeat()
    svc.transport._pending.append(
        build_command("SACK", "STAT", "OK").rstrip("\r\n")
    )
    ack = svc.send("CFG", "SET", "tx.period_ms", "999999")
    assert ack.args == ("CFG", "ERR", "RANGE")     # 내 명령의 응답이다
    assert sim.store.get("tx.period_ms") == 100    # 보드는 실제로 거부했다


def test_send_raises_when_only_foreign_acks_arrive():
    """대응하는 응답이 아예 없으면 남의 것을 돌려주지 않고 예외를 던진다.

    시뮬레이터는 언제나 응답하므로 이 상황을 만들려면 스텁이 필요하다.
    실기기에서는 명령이 유실되고 앞선 응답만 도착하면 그대로 재현된다.
    """

    class OnlyForeignAcks:
        """무엇을 보내든 남의 $SACK 하나만 돌려주는 트랜스포트."""

        def __init__(self):
            self._sent = False

        def write(self, data: str) -> None:
            self._sent = True

        def read_lines(self):
            if self._sent:
                self._sent = False
                yield build_command("SACK", "STAT", "OK").rstrip("\r\n")

        def close(self) -> None:
            pass

    svc = BoardService(OnlyForeignAcks(), clock=lambda: 0, timeout_s=0.05)
    with pytest.raises(ProtocolError):
        svc.send("CFG", "GET", "tx.period_ms")


def test_send_keeps_pumping_until_the_response_arrives():
    """🔴 한 번만 pump 하면 실기기에서 거의 항상 실패한다.

    `$CFG,LIST` 응답은 7 KB 라 115200 baud 에서 600 ms 넘게 걸린다.
    시뮬레이터는 즉시 답하므로 이 결함은 `--port COM7` 로 바꾸는 순간에만
    드러난다 — 시험이 그걸 흉내내야 한다.
    """

    class SlowTransport:
        """세 번째 read 에서야 응답을 내놓는 트랜스포트."""

        def __init__(self):
            self.reads = 0
            self._pending = None

        def write(self, data: str) -> None:
            self._pending = build_command("SACK", "CFG", "OK").rstrip("\r\n")

        def read_lines(self):
            self.reads += 1
            if self.reads >= 3 and self._pending:
                yield self._pending
                self._pending = None

        def close(self) -> None:
            pass

    tr = SlowTransport()
    svc = BoardService(tr, clock=lambda: 0, timeout_s=1.0)
    ack = svc.send("CFG", "GET", "tx.period_ms")
    assert ack.args == ("CFG", "OK")
    assert tr.reads >= 3                       # 한 번으로 끝내지 않았다


def test_catalog_collection_does_not_swallow_telemetry(rig):
    """🔴 $CFG,LIST 를 받는 동안 흘러온 텔레메트리가 사라지면 안 된다.

    타입을 안 보고 전부 _catalog 로 보내면 수집에 구멍이 뚫리는데,
    parse_catalog 가 모르는 타입을 무시하므로 아무도 눈치채지 못한다.

    ⚠️ **시계를 진행시켜야 실제로 끼어들기가 생긴다.** fetch_schema 안의
    pump 가 tick 을 부를 때 방출 주기가 지나 있어야 카탈로그 줄과 텔레메트리가
    같은 pump 에서 섞인다. 시계를 안 옮기면 끼어드는 텔레메트리가 애초에
    없어서, 이 시험은 수정을 되돌려도 통과한다 — 아무것도 검증하지 않는다.
    """
    svc, _sim, clock = rig
    clock.advance(100)
    svc.pump()                                     # 텔레메트리가 먼저 쌓인다
    before = len(svc.records)
    assert before > 0

    clock.advance(100)                             # 카탈로그와 겹치도록 주기를 넘긴다
    svc.fetch_schema()
    assert len(svc.records) > before               # 끼어든 텔레메트리를 잃지 않았다


def test_catalog_is_still_complete_when_telemetry_interleaves(rig):
    """끼어들기가 있어도 카탈로그 자체는 온전히 조립된다."""
    svc, _sim, clock = rig
    clock.advance(100)
    svc.pump()
    clock.advance(100)
    schema = svc.fetch_schema()
    assert "pwr.5v" in schema.items
    assert len(schema.items) > 40


def test_send_surfaces_error_reason(rig):
    svc, _sim, _clock = rig
    ack = svc.send("CFG", "GET", "nope")
    assert ack.args == ("CFG", "ERR", "UNKNOWN_KEY")


def test_send_collects_json_payload(rig):
    svc, _sim, _clock = rig
    svc.send("CFG", "GET", "tx.period_ms")
    assert svc.last_payload["cur"] == 100


def test_fetch_schema_builds_from_catalog(rig):
    svc, _sim, _clock = rig
    schema = svc.fetch_schema()
    assert "pwr.5v" in schema.items
    assert schema.items["pwr.5v"].readonly is True


def test_heartbeat_puts_board_in_config_mode(rig):
    svc, sim, _clock = rig
    svc.heartbeat()
    assert sim.mode == Mode.CONFIG


def test_pump_collects_telemetry_records(rig):
    svc, _sim, clock = rig
    clock.advance(100)
    svc.pump()
    assert any(r["type"] == "ain" for r in svc.records)


def test_pump_tracks_seq_gaps(rig):
    """전송 중 유실이 생기면 서비스가 세어야 한다."""
    svc, _sim, _clock = rig
    svc.seq_tracker.observe(10)
    svc.seq_tracker.observe(14)
    assert svc.seq_tracker.missing_total == 3


def test_mode_follows_heartbeat_timeout(rig):
    svc, _sim, clock = rig
    svc.heartbeat()
    clock.advance(HB_TIMEOUT_MS + 1)
    svc.pump()
    assert svc.mode == Mode.RUN


def test_set_config_helper_returns_reason_on_reject(rig):
    svc, _sim, _clock = rig
    svc.heartbeat()
    ok, reason = svc.set_config("pwr.5v", "false")
    assert ok is False
    assert reason == "INTERLOCK"


def test_set_config_helper_succeeds(rig):
    svc, sim, _clock = rig
    svc.heartbeat()
    ok, reason = svc.set_config("tx.period_ms", "250")
    assert ok is True and reason == ""
    assert sim.store.get("tx.period_ms") == 250


def test_malformed_telemetry_is_counted_not_raised(rig):
    """깨진 줄이 서비스를 죽이면 안 된다. 세고 넘어간다."""
    svc, _sim, _clock = rig
    svc._ingest('{"schema_ver":3,"seq":')
    assert svc.corrupt_total == 1
    assert svc.records == []
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `python -m pytest host/tests/test_board_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'host.service.board_service'`

- [ ] **Step 3: `board_service.py` 작성**

`host/service/board_service.py`:
```python
"""보드와의 대화를 관리한다 — 명령/응답 매칭, 텔레메트리 수집, 유실 집계.

GUI 와 분리돼 있다. GUI 가 없어도 이 계층만으로 수집·저장이 가능하며,
나중에 Jetson 에서 GUI 없이 서비스만 돌리는 구성이 그대로 가능하다.
"""

import time
from collections.abc import Callable, Iterator
from typing import Protocol

#: 응답을 기다리는 동안 다시 읽기까지의 간격. 짧게 잡아 응답 지연을 줄이되
#: CPU 를 태우지는 않는다.
_POLL_INTERVAL_S = 0.005

from host.core.config_schema import ConfigSchema, parse_catalog
from host.core.errors import ProtocolError
from host.core.framing import Command, build_command, parse_line
from host.core.records import SeqTracker, is_telemetry, parse_record


class Transport(Protocol):
    """보드와 줄 단위로 주고받는 통로."""

    def write(self, data: str) -> None: ...
    def read_lines(self) -> Iterator[str]: ...
    def close(self) -> None: ...


class LoopbackTransport:
    """DeviceSim 을 직접 물리는 트랜스포트. 시리얼 없이 전 구간을 시험한다."""

    def __init__(self, sim) -> None:
        self.sim = sim
        self._pending: list[str] = []

    def write(self, data: str) -> None:
        for line in data.splitlines():
            if line.strip():
                self._pending.extend(self.sim.feed(line + "\r\n"))

    def read_lines(self) -> Iterator[str]:
        pending, self._pending = self._pending, []
        yield from pending

    def tick(self, now_ms: int) -> None:
        self._pending.extend(self.sim.tick(now_ms))

    def close(self) -> None:
        pass


class SerialTransport:
    """pyserial 기반 트랜스포트."""

    def __init__(self, port: str, baud: int = 115200, timeout: float = 0.1):
        import serial  # 지연 import — 시리얼 없이도 테스트가 돌게 한다

        self._ser = serial.Serial(port, baud, timeout=timeout)
        self._buf = ""

    def write(self, data: str) -> None:
        self._ser.write(data.encode("utf-8"))

    def read_lines(self) -> Iterator[str]:
        chunk = self._ser.read(self._ser.in_waiting or 1)
        if chunk:
            self._buf += chunk.decode("utf-8", errors="replace")
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                yield line.strip()

    def close(self) -> None:
        self._ser.close()


class BoardService:
    def __init__(self, transport: Transport, *, clock: Callable[[], int],
                 timeout_s: float = 2.0):
        self.transport = transport
        self.clock = clock
        #: 명령 응답을 기다리는 최대 벽시계 시간. `$CFG,LIST` 는 7 KB 라
        #: 115200 baud 에서 600 ms 넘게 걸린다 — 여유를 두고 잡는다.
        self.timeout_s = timeout_s

        self.records: list[dict] = []
        self.seq_tracker = SeqTracker()
        self.corrupt_total = 0
        self.mode = "RUN"
        self.last_payload: dict | None = None

        self._acks: list[Command] = []
        self._catalog: list[str] = []
        self._collect_catalog = False

    # ------------------------------------------------------------- 명령 송신
    def send(self, verb: str, *args: str) -> Command:
        """명령을 보내고 **그 명령에 대응하는** $SACK 를 반환한다.

        🔴 verb 를 대조하지 않고 `_acks[0]` 을 돌려주면 안 된다.

        직렬 링크에서는 앞선 명령의 응답이 한 박자 늦게 도착하는 일이 흔하다.
        그 지연 응답이 버퍼 앞자리에 있으면, 지금 보낸 `$CFG,SET` 이 보드에서
        `RANGE` 로 거부됐는데도 남의 `$SACK,STAT,OK` 를 집어 **성공으로
        보고한다.** 설정 쓰기에서 조용한 거짓 성공은 이 시스템의 최악의
        실패 방식이고, GUI 가 이 계층 위에 올라간다.

        Raises:
            ProtocolError: 이 명령에 대응하는 응답이 오지 않은 경우.
        """
        self.last_payload = None
        self._acks.clear()
        self.transport.write(build_command(verb, *args))

        # 🔴 한 번만 pump 하면 실기기에서 거의 항상 실패한다.
        #
        # SerialTransport.read_lines() 는 그때 도착해 있는 바이트만 읽는다.
        # `$CFG,LIST` 응답은 약 7 KB 라 115200 baud 에서 600 ms 넘게 걸린다.
        # 한 번 읽고 판정하면 응답이 오는 중인데 "응답 없음" 이 된다.
        # 시뮬레이터는 즉시 답하므로 시험이 이걸 못 잡는다 — 즉 `--port COM7`
        # 로 바꾸는 순간에만 드러나는 종류의 결함이다.
        #
        # 마감시각은 **벽시계**로 잰다. `self.clock()` 은 시뮬레이터에 주입하는
        # 장치 시간이라 시험에서 멈춰 있을 수 있고, 그걸로 마감을 재면 영원히
        # 돈다. 전송 타임아웃은 장치 시간이 아니라 실제 경과 시간의 문제다.
        deadline = time.monotonic() + self.timeout_s
        while True:
            self.pump()
            for ack in self._acks:
                if ack.args and ack.args[0] == verb:
                    return ack
            if time.monotonic() >= deadline:
                break
            time.sleep(_POLL_INTERVAL_S)

        if self._acks:
            others = [a.args[0] if a.args else "?" for a in self._acks]
            raise ProtocolError(
                f"{verb} 응답이 오지 않음. 받은 응답: {others}"
            )
        raise ProtocolError(f"응답 없음: {verb} {args} ({self.timeout_s}s 초과)")

    def heartbeat(self) -> None:
        """$HB 를 보낸다. 응답은 없다."""
        self.transport.write(build_command("HB"))
        self.pump()

    def set_config(self, key: str, value: str) -> tuple[bool, str]:
        """설정 변경을 시도하고 (성공여부, 거부사유) 를 반환한다."""
        ack = self.send("CFG", "SET", key, value)
        if ack.args[-1] == "OK":
            return True, ""
        return False, ack.args[-1]

    def fetch_schema(self) -> ConfigSchema:
        """$CFG,LIST 로 카탈로그를 받아 스키마를 만든다."""
        self._catalog = []
        self._collect_catalog = True
        try:
            self.send("CFG", "LIST")
        finally:
            self._collect_catalog = False
        return parse_catalog(self._catalog)

    # ------------------------------------------------------------- 수신 처리
    def pump(self) -> None:
        """트랜스포트에 쌓인 줄을 전부 처리한다."""
        now = self.clock()
        tick = getattr(self.transport, "tick", None)
        if tick is not None:
            tick(now)
        for line in self.transport.read_lines():
            self._ingest(line)

        # LoopbackTransport 로 시뮬레이터를 직접 물린 경우에는 모드를 바로
        # 읽어 온다. 실제 보드(SerialTransport)에는 `sim` 이 없으므로 이
        # 경로를 건너뛰고, 모드는 $STAT 응답으로만 갱신된다(_ingest 참조).
        sim = getattr(self.transport, "sim", None)
        if sim is not None:
            self.mode = sim.mode

    def _ingest(self, line: str) -> None:
        line = line.strip()
        if not line:
            return

        if line.startswith("$"):
            try:
                cmd = parse_line(line)
            except ProtocolError:
                self.corrupt_total += 1
                return
            if cmd.verb == "SACK":
                self._acks.append(cmd)
            return

        try:
            rec = parse_record(line)
        except ProtocolError:
            self.corrupt_total += 1
            return

        rtype = rec.get("type")

        # 🔴 카탈로그 수집 중이라도 **카탈로그 줄만** 가로챈다.
        #
        # 타입을 안 보고 전부 _catalog 로 보내면, $CFG,LIST 응답이 오는 동안
        # 흘러들어온 텔레메트리가 통째로 사라진다. parse_catalog 는 모르는
        # 타입을 무시하므로 아무도 눈치채지 못한다. GUI 가 카탈로그를 새로
        # 고칠 때마다 수집에 구멍이 뚫린다.
        if self._collect_catalog and rtype in ("cfg_item", "cfg_field", "cfg_end"):
            self._catalog.append(line)
            return

        # 🔴 명령 응답은 seq 시퀀스에 넣지 않는다 (규격 §7.1.1).
        # 타입을 손으로 나열하지 않고 is_telemetry() 를 쓴다 — 손으로 적으면
        # cfg_item/cfg_field/cfg_end 처럼 빠뜨리는 것이 생긴다.
        if not is_telemetry(rec):
            self.last_payload = rec
            if rtype == "stat":
                self.mode = rec.get("mode", self.mode)
            return

        self.seq_tracker.observe(rec["seq"])
        self.records.append(rec)

    def close(self) -> None:
        self.transport.close()
```

- [ ] **Step 4: `serial_server.py` 작성**

`tools/simulator/serial_server.py`:
```python
"""시뮬레이터를 실제 시리얼 포트에 붙여 돌린다.

가상 시리얼 포트 쌍(Windows: com0com, Linux: socat)을 만든 뒤 한쪽을 이
서버에, 다른 쪽을 GUI 나 CLI 에 연결하면 보드 없이 전 구간을 시험할 수 있다.
"""

import time
from pathlib import Path

from tools.simulator.config_store import default_store
from tools.simulator.device_sim import DeviceSim


def run_simulator_server(port: str, baud: int = 115200,
                         state: Path | None = None) -> None:
    """시뮬레이터를 시리얼 포트에 붙이고 무한 루프로 돌린다."""
    import serial

    store = default_store(state)
    store.load()
    sim = DeviceSim(store)

    ser = serial.Serial(port, baud, timeout=0.05)
    buf = ""
    t0 = time.monotonic()
    print(f"시뮬레이터 시작: {port} @ {baud}")

    try:
        while True:
            now_ms = int((time.monotonic() - t0) * 1000)

            chunk = ser.read(ser.in_waiting or 1)
            if chunk:
                buf += chunk.decode("utf-8", errors="replace")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    for out in sim.feed(line + "\n"):
                        ser.write((out + "\r\n").encode("utf-8"))

            for out in sim.tick(now_ms):
                ser.write((out + "\r\n").encode("utf-8"))

            time.sleep(0.005)
    except KeyboardInterrupt:
        print("\n시뮬레이터 종료")
    finally:
        ser.close()
```

- [ ] **Step 5: 테스트 실행해 통과 확인**

Run: `python -m pytest host/tests/test_board_service.py -v`
Expected: PASS (11 passed)

- [ ] **Step 6: 커밋**

```bash
git add host/service/board_service.py tools/simulator/serial_server.py host/tests/test_board_service.py
git commit -m "feat(host): Board Service 와 시리얼 어댑터

GUI 와 분리된 계층이다. 나중에 Jetson 에서 GUI 없이 서비스만 돌리는
구성이 그대로 가능하다.

LoopbackTransport 로 시뮬레이터를 직접 물려 시리얼 없이 전 구간을
시험한다. serial_server 는 가상 시리얼 포트 쌍에 붙여 GUI 를 보드 없이
개발할 때 쓴다.

깨진 줄은 예외로 올리지 않고 corrupt_total 로 센다. 한 줄이 깨졌다고
수집이 멈추면 안 된다."
```

---

## Task 10: CLI

**Files:**
- Create: `tools/cli/markon_cli.py`
- Test: `host/tests/test_cli.py`

**Interfaces:**
- Consumes: `BoardService`, `LoopbackTransport`, `SerialTransport`, `DeviceSim`
- Produces:
  - `build_parser() -> argparse.ArgumentParser`
  - `cmd_list(svc) -> int`, `cmd_get(svc, key) -> int`, `cmd_set(svc, key, value) -> int`, `cmd_monitor(svc, seconds) -> int`, `cmd_fields(svc) -> int`
  - `main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: 실패하는 테스트 작성**

`host/tests/test_cli.py`:
```python
from tools.cli.markon_cli import build_parser, cmd_get, cmd_list, cmd_set
from host.service.board_service import BoardService, LoopbackTransport
from tools.simulator.config_store import default_store
from tools.simulator.device_sim import DeviceSim


def _svc():
    clock = lambda: 0  # noqa: E731
    return BoardService(LoopbackTransport(DeviceSim(default_store())), clock=clock)


def test_parser_requires_subcommand():
    parser = build_parser()
    args = parser.parse_args(["--port", "sim", "get", "tx.period_ms"])
    assert args.command == "get"
    assert args.key == "tx.period_ms"


def test_parser_defaults_to_simulator_port():
    args = build_parser().parse_args(["list"])
    assert args.port == "sim"


def test_cmd_list_prints_grouped_items(capsys):
    assert cmd_list(_svc()) == 0
    out = capsys.readouterr().out
    assert "tx.period_ms" in out
    assert "pwr.5v" in out
    assert "[읽기전용]" in out


def test_cmd_get_prints_value(capsys):
    assert cmd_get(_svc(), "tx.period_ms") == 0
    assert "100" in capsys.readouterr().out


def test_cmd_get_unknown_key_returns_nonzero(capsys):
    assert cmd_get(_svc(), "nope") != 0
    assert "UNKNOWN_KEY" in capsys.readouterr().out


def test_cmd_set_sends_heartbeat_first(capsys):
    """설정 변경은 CONFIG 모드가 필요하므로 CLI 가 먼저 \\$HB 를 보내야 한다."""
    svc = _svc()
    assert cmd_set(svc, "tx.period_ms", "250") == 0
    assert svc.transport.sim.store.get("tx.period_ms") == 250


def test_cmd_set_interlock_prints_reason(capsys):
    assert cmd_set(_svc(), "pwr.5v", "false") != 0
    out = capsys.readouterr().out
    assert "INTERLOCK" in out
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `python -m pytest host/tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.cli.markon_cli'`

- [ ] **Step 3: `markon_cli.py` 작성**

`tools/cli/markon_cli.py`:
```python
"""MarkON Studio 명령줄 도구.

GUI 없이 보드를 설정하고 텔레메트리를 확인한다. --port sim 이면 내장
시뮬레이터를 쓰므로 보드 없이도 전 기능을 시험할 수 있다.

  python -m tools.cli.markon_cli list
  python -m tools.cli.markon_cli --port COM7 get tx.period_ms
  python -m tools.cli.markon_cli --port COM7 set tx.period_ms 250
  python -m tools.cli.markon_cli --port COM7 monitor --seconds 10
"""

import argparse
import sys
import time

from host.core.errors import ProtocolError
from host.service.board_service import BoardService, LoopbackTransport, SerialTransport
from tools.simulator.config_store import default_store
from tools.simulator.device_sim import DeviceSim

SIM_PORT = "sim"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="markon", description="STM32 v2.0 보드 설정·모니터 도구"
    )
    parser.add_argument(
        "--port", default=SIM_PORT,
        help="시리얼 포트 (예: COM7, /dev/ttyUSB0). 기본 'sim' = 내장 시뮬레이터",
    )
    parser.add_argument("--baud", type=int, default=115200)

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="설정 카탈로그 출력")
    sub.add_parser("fields", help="NDJSON 필드 마스크 현황 출력")

    p_get = sub.add_parser("get", help="설정값 조회")
    p_get.add_argument("key")

    p_set = sub.add_parser("set", help="설정값 변경")
    p_set.add_argument("key")
    p_set.add_argument("value")

    p_mon = sub.add_parser("monitor", help="텔레메트리 수신")
    p_mon.add_argument("--seconds", type=float, default=5.0)

    return parser


def make_service(port: str, baud: int) -> BoardService:
    if port == SIM_PORT:
        transport = LoopbackTransport(DeviceSim(default_store()))
        start = time.monotonic()
        return BoardService(
            transport, clock=lambda: int((time.monotonic() - start) * 1000)
        )
    start = time.monotonic()
    return BoardService(
        SerialTransport(port, baud),
        clock=lambda: int((time.monotonic() - start) * 1000),
    )


# ------------------------------------------------------------------- 하위 명령
def cmd_list(svc: BoardService) -> int:
    schema = svc.fetch_schema()
    for group in schema.groups():
        print(f"\n[{group}]")
        for item in schema.items.values():
            if item.group != group:
                continue
            flags = " [읽기전용]" if item.readonly else ""
            unit = f" {item.unit}" if item.unit else ""
            print(f"  {item.key:24} = {item.current}{unit}{flags}")
            if item.note:
                print(f"  {'':24}   → {item.note}")
    print()
    return 0


def cmd_fields(svc: BoardService) -> int:
    schema = svc.fetch_schema()
    mask = int(schema.items["tx.fields"].current)
    print("NDJSON 필드 마스크")
    for bit in sorted(schema.fields):
        f = schema.fields[bit]
        state = "ON " if mask & (1 << bit) else "off"
        print(f"  bit{bit:>2}  {state}  {f.name:16} {f.label}")
    return 0


def cmd_get(svc: BoardService, key: str) -> int:
    ack = svc.send("CFG", "GET", key)
    if ack.args[-1] != "OK":
        print(f"실패: {ack.args[-1]}")
        return 1
    print(svc.last_payload["cur"])
    return 0


def cmd_set(svc: BoardService, key: str, value: str) -> int:
    # 설정 변경은 CONFIG 모드에서만 받는다 (규격 §6).
    svc.heartbeat()
    ok, reason = svc.set_config(key, value)
    if not ok:
        print(f"거부됨: {reason}")
        return 1
    print(f"{key} = {value}")
    return 0


def cmd_monitor(svc: BoardService, seconds: float) -> int:
    deadline = time.monotonic() + seconds
    seen = 0
    while time.monotonic() < deadline:
        svc.pump()
        while seen < len(svc.records):
            rec = svc.records[seen]
            seen += 1
            print(rec)
        time.sleep(0.02)

    t = svc.seq_tracker
    print(
        f"\n수신 {t.received_total}건, 누락 {t.missing_total}건, "
        f"깨짐 {svc.corrupt_total}건"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    svc = make_service(args.port, args.baud)
    try:
        if args.command == "list":
            return cmd_list(svc)
        if args.command == "fields":
            return cmd_fields(svc)
        if args.command == "get":
            return cmd_get(svc, args.key)
        if args.command == "set":
            return cmd_set(svc, args.key, args.value)
        if args.command == "monitor":
            return cmd_monitor(svc, args.seconds)
    except ProtocolError as exc:
        print(f"프로토콜 오류: {exc}", file=sys.stderr)
        return 2
    finally:
        svc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `python -m pytest host/tests/test_cli.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: CLI 를 손으로 돌려 확인**

Run: `python -m tools.cli.markon_cli list`
Expected: `[dev] [tx] [adc] [pwr] [ain]` 그룹별 설정 항목 목록. `pwr.5v` 줄에 `[읽기전용]`과 쿨링 팬 사유가 보인다.

Run: `python -m tools.cli.markon_cli set pwr.5v false`
Expected: `거부됨: INTERLOCK`, 종료 코드 1

Run: `python -m tools.cli.markon_cli monitor --seconds 3`
Expected: `ain` 레코드가 흐르고, 마지막에 `수신 N건, 누락 0건, 깨짐 0건`

- [ ] **Step 6: 전체 테스트 실행**

Run: `python -m pytest -v`
Expected: 전체 PASS

- [ ] **Step 7: 커밋**

```bash
git add tools/cli/markon_cli.py host/tests/test_cli.py
git commit -m "feat(tools): 설정·모니터 CLI

--port sim 이 기본이라 보드 없이 전 기능을 시험할 수 있다. 실물은
--port COM7 처럼 지정한다.

set 은 먼저 \$HB 를 보내 CONFIG 모드로 올린 뒤 변경한다. 거부되면
사유(INTERLOCK/RANGE/MODE)를 그대로 보여준다 — 사용자가 왜 안 되는지
알아야 하기 때문이다.

monitor 는 종료 시 수신·누락·깨짐 건수를 요약한다. Q2 의 2% 유실을
정량화하던 방식과 같다."
```

---

## Task 11: ADC 스케줄 용량 검증

**Files:**
- Create: `tools/simulator/capacity.py`
- Modify: `tools/simulator/config_store.py` — `ConfigStore.set()`에 용량 검증 삽입
- Test: `host/tests/test_capacity.py`

**Interfaces:**
- Consumes: `ConfigStore`, `Reason.CAPACITY`
- Produces:
  - `SETTLING_MS` — 채널 전환 정착시간 (실측 전 잠정값)
  - `required_sps(store) -> float`
  - `available_sps(drate: int) -> float`
  - `check_capacity(store) -> None` — 초과 시 `ConfigError(Reason.CAPACITY, detail)`
  - `SAFETY_MARGIN`

개별 값이 전부 범위 안이어도 조합이 물리적으로 불가능할 수 있다. 7채널을
각각 10 ms 주기로 두고 DRATE를 30 SPS로 잡으면 요구 700 SPS 대 가용 30 SPS로
큐가 영구히 넘친다. 설계 §6.4.

- [ ] **Step 1: 실패하는 테스트 작성**

`host/tests/test_capacity.py`:
```python
import pytest

from host.core.errors import ConfigError, Reason
from tools.simulator.capacity import (
    SAFETY_MARGIN,
    available_sps,
    check_capacity,
    required_sps,
)
from tools.simulator.config_store import default_store


def _force(store, key, value):
    """검증을 건너뛰고 값을 직접 밀어 넣는다.

    Step 4 이후 ConfigStore.set() 은 스스로 용량을 검사하므로, 초과 상태를
    set() 으로는 만들 수 없다. check_capacity() 자체를 시험하려면 저장소를
    우회해 초과 상태를 구성해야 한다.
    """
    store.items[key].current = value


def _load_channels(store, *, count, period_ms):
    """count 개 채널을 period_ms 주기로 켠 상태를 검증 없이 구성한다.

    ⚠️ 꺼진 채널의 주기는 건드리지 않는다. 건드리면 나중에 그 채널을 켤 때
    기본 주기(100ms)가 아니라 period_ms 로 켜져서, "채널 하나 추가" 의
    부하 증가량이 시험이 의도한 값과 달라진다.
    """
    for ch in range(7):
        enabled = ch < count
        _force(store, f"ain{ch}.enabled", enabled)
        if enabled:
            _force(store, f"ain{ch}.period_ms", period_ms)


# --------------------------------------------------------------- 계산 함수
def test_required_sps_counts_only_enabled_channels():
    store = default_store()                    # 기본은 ain0 만 enabled, 100ms
    assert required_sps(store) == pytest.approx(10.0)


def test_required_sps_sums_across_channels():
    store = default_store()
    _force(store, "ain1.enabled", True)
    _force(store, "ain1.period_ms", 50)
    assert required_sps(store) == pytest.approx(10.0 + 20.0)


def test_required_sps_ignores_disabled_channels():
    store = default_store()
    _force(store, "ain1.enabled", False)
    _force(store, "ain1.period_ms", 10)        # 꺼져 있으면 세지 않는다
    assert required_sps(store) == pytest.approx(10.0)


def test_available_sps_accounts_for_settling_time():
    """가용률은 DRATE 만이 아니라 채널 전환 정착시간에도 구속된다.

    정착시간이 0 이면 available == DRATE 여야 하지만, 실제로는 채널 전환
    비용이 있으므로 반드시 DRATE 보다 작다.
    """
    assert available_sps(1000) < 1000.0
    assert available_sps(2000) > available_sps(1000)   # 빠를수록 여유가 는다


# --------------------------------------------------------------- 판정
def test_default_config_is_within_capacity():
    check_capacity(default_store())            # 예외가 없어야 한다


def test_seven_channels_at_10ms_exceeds_capacity():
    """DRATE 2000 SPS 기준 가용 약 533 SPS 인데 7채널×100 SPS = 700 SPS."""
    store = default_store()
    _force(store, "adc.drate", 2000)
    _load_channels(store, count=7, period_ms=10)
    with pytest.raises(ConfigError) as exc:
        check_capacity(store)
    assert exc.value.reason == Reason.CAPACITY


def test_five_channels_at_10ms_is_within_capacity():
    """같은 조건에서 5채널 = 500 SPS 는 가용 안이다. 경계가 실제로 있다."""
    store = default_store()
    _force(store, "adc.drate", 2000)
    _load_channels(store, count=5, period_ms=10)
    check_capacity(store)                      # 예외가 없어야 한다


def test_capacity_error_reports_required_and_available():
    """사용자가 무엇을 줄여야 하는지 알아야 하므로 두 값을 모두 담는다."""
    store = default_store()
    _force(store, "adc.drate", 2000)
    _load_channels(store, count=7, period_ms=10)
    with pytest.raises(ConfigError) as exc:
        check_capacity(store)
    assert "요구" in exc.value.detail
    assert "가용" in exc.value.detail


def test_safety_margin_is_applied():
    """여유 계수가 실제로 판정을 좁힌다."""
    assert 0.0 < SAFETY_MARGIN <= 1.0


# --------------------------------------------------------------- 저장소 연동
def test_store_set_rejects_change_that_breaks_capacity():
    """저장소가 스스로 막는다. 개별 값은 범위 안이어도 조합이 불가하면 거부.

    5채널×10ms = 500 SPS 로 가용(약 533) 안에 있는 상태에서, 6번째 채널을
    10ms 로 올리면 600 SPS 가 되어 초과한다.
    """
    store = default_store()
    _force(store, "adc.drate", 2000)
    _load_channels(store, count=5, period_ms=10)

    store.set("ain5.enabled", "true")          # +10 SPS (주기 100ms) — 통과
    with pytest.raises(ConfigError) as exc:
        store.set("ain5.period_ms", "10")      # +90 SPS — 초과
    assert exc.value.reason == Reason.CAPACITY


def test_rejected_change_is_rolled_back():
    """거부된 설정이 저장소에 남으면 안 된다."""
    store = default_store()
    _force(store, "adc.drate", 2000)
    _load_channels(store, count=5, period_ms=10)
    store.set("ain5.enabled", "true")

    with pytest.raises(ConfigError):
        store.set("ain5.period_ms", "10")
    assert store.get("ain5.period_ms") == 100  # 이전 값 그대로


def test_lowering_drate_cannot_bypass_the_capacity_check():
    """🔴 drate 는 수요가 아니라 **공급**이다.

    `required_sps` 만 비교하면 drate 를 낮추는 변경이 "부하가 안 늘었다"로
    통과해 용량 검사가 통째로 무력화된다. 사용자가 가장 흔히 만지는 노브다.
    """
    store = default_store()
    _force(store, "adc.drate", 2000)
    _load_channels(store, count=7, period_ms=10)   # 700 SPS, 가용 533 → 이미 초과
    with pytest.raises(ConfigError) as exc:
        store.set("adc.drate", "30")               # 공급을 더 줄인다
    assert exc.value.reason == Reason.CAPACITY
    assert store.get("adc.drate") == 2000          # 롤백


def test_raising_drate_is_always_allowed():
    """공급을 늘리는 변경은 초과 상태에서도 통과해야 한다 — 빠져나갈 길."""
    store = default_store()
    _force(store, "adc.drate", 30)
    _load_channels(store, count=7, period_ms=10)   # 크게 초과 상태
    store.set("adc.drate", "7500")                 # 예외 없이 통과
    assert store.get("adc.drate") == 7500


def test_disabling_a_channel_is_never_capacity_rejected():
    """부하를 줄이는 변경은 막을 이유가 없다."""
    store = default_store()
    _force(store, "adc.drate", 2000)
    _load_channels(store, count=7, period_ms=10)   # 이미 초과 상태
    store.set("ain0.enabled", "false")             # 예외 없이 통과해야 한다
    assert store.get("ain0.enabled") is False
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `python -m pytest host/tests/test_capacity.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.simulator.capacity'`

- [ ] **Step 3: `capacity.py` 작성**

`tools/simulator/capacity.py`:
```python
"""ADC 스케줄 가능성 검증.

개별 설정이 전부 유효 범위 안이어도 조합이 물리적으로 달성 불가능할 수 있다.
그런 설정을 수락하면 큐가 영구히 넘치고 데이터가 계속 유실된다.

ADS1256 은 멀티플렉스된 8채널 ΔΣ ADC 다. 채널을 바꿀 때마다
MUX 갱신 → SYNC → WAKEUP → 변환 완료 대기가 필요하므로, 선택한 DRATE 와
채널 전환 절차가 실제 달성 가능한 총 수집률을 구속한다.

설계 §6.4
"""

from host.core.errors import ConfigError, Reason

#: 채널 전환 정착시간 (ms). 🔴 잠정값 — 실기기 실측으로 확정해야 한다.
#: 설계 §14 열린 항목 5번.
SETTLING_MS = 1.0

#: 계산값과 실제 사이의 여유. 1.0 = 여유 없음.
SAFETY_MARGIN = 0.8

AIN_CHANNELS = 7


def required_sps(store) -> float:
    """활성 채널들이 요구하는 총 샘플률 (SPS)."""
    total = 0.0
    for ch in range(AIN_CHANNELS):
        if not store.get(f"ain{ch}.enabled"):
            continue
        period_ms = float(store.get(f"ain{ch}.period_ms"))
        total += 1000.0 / period_ms
    return total


def available_sps(drate: int) -> float:
    """DRATE 와 채널 전환 정착시간으로 계산한 달성 가능 총 샘플률 (SPS)."""
    conversion_ms = 1000.0 / float(drate)
    per_sample_ms = conversion_ms + SETTLING_MS
    return 1000.0 / per_sample_ms


def margin_sps(store) -> float:
    """실현 가능성 여유 = 요구 − 가용(안전여유 반영). 양수면 초과다.

    🔴 수요와 공급을 **한 식에** 담는 것이 요점이다. 수요만 비교하면
    `adc.drate` 를 낮추는 변경(공급 감소)이 "부하가 안 늘었다"로 통과해
    용량 검사가 통째로 무력화된다. `ConfigStore._check_combination` 주석 참조.
    """
    return required_sps(store) - available_sps(int(store.get("adc.drate"))) * SAFETY_MARGIN


def check_capacity(store) -> None:
    """설정 조합이 달성 가능한지 검사한다.

    Raises:
        ConfigError: reason=CAPACITY. detail 에 요구·가용 수치를 담아
            사용자가 무엇을 줄여야 하는지 알 수 있게 한다.
    """
    required = required_sps(store)
    available = available_sps(int(store.get("adc.drate"))) * SAFETY_MARGIN

    if required > available:
        raise ConfigError(
            Reason.CAPACITY,
            f"요구 {required:.1f} SPS > 가용 {available:.1f} SPS "
            f"(DRATE {store.get('adc.drate')} SPS, 정착 {SETTLING_MS} ms). "
            f"채널을 줄이거나 주기를 늘리거나 DRATE 를 올려야 한다",
        )
```

- [ ] **Step 4: `ConfigStore.set()` 에 용량 검증 삽입**

`tools/simulator/config_store.py`의 `set()` 을 다음으로 교체한다.

```python
    def set(self, key: str, raw: str) -> None:
        """문자열 값을 검증해 반영한다. 규격 §5 의 순서를 지킨다."""
        item = self.items.get(key)
        if item is None:
            raise ConfigError(Reason.UNKNOWN_KEY, key)

        value = _coerce(item, raw)

        if value == item.current:
            return                          # 변화 없음 — 거부할 이유가 없다

        if item.interlocked:
            raise ConfigError(Reason.INTERLOCK, item.note or key)
        if item.readonly:
            raise ConfigError(Reason.READONLY, item.note or key)

        previous = item.current
        item.current = value
        try:
            self._check_combination(previous_margin=_margin_of(self, previous, item))
        except ConfigError:
            item.current = previous
            raise

        self.dirty = True

    def _check_combination(self, *, previous_margin: float) -> None:
        """여러 항목이 얽힌 제약을 검사한다 (설계 §6.4).

        🔴 **실현 가능성 여유(margin)를 비교한다. 수요만 보면 안 된다.**

        이미 초과 상태에 빠진 저장소에서 빠져나올 길은 열어둬야 한다 — 채널을
        끄거나 주기를 늘리거나 **DRATE 를 올리는** 변경은 무조건 통과해야 한다.

        그런데 `required_sps` 만 비교하면 `adc.drate` 를 **낮추는** 변경도
        "수요가 안 늘었다"로 판정되어 검사를 건너뛴다. drate 는 수요가 아니라
        **공급**이기 때문이다. 그러면 7채널 10ms(700 SPS) 상태에서 drate 를
        2 SPS 로 내리는 것이 수락되고, 용량 검사가 막으려던 바로 그 상태가
        된다. 사용자가 가장 흔히 만지는 노브로 기능 전체가 무력화된다.

        여유 = 요구 − 가용 으로 보면 양쪽이 한 식에 들어온다. 여유가 나빠지지
        않는 변경만 검사를 건너뛴다.
        """
        from tools.simulator.capacity import check_capacity, margin_sps

        if margin_sps(self) <= previous_margin:
            return                          # 실현 가능성이 나빠지지 않았다

        check_capacity(self)
```

같은 파일 맨 아래에 보조 함수를 추가한다.

```python
def _margin_of(store: "ConfigStore", previous_value: object,
               item: SimConfigItem) -> float:
    """변경 직전의 실현 가능성 여유(요구 − 가용)를 계산한다.

    수요(`required_sps`)만이 아니라 공급(`available_sps`)까지 포함해야
    `adc.drate` 를 낮추는 변경도 "나빠졌다"로 잡힌다. `_check_combination`
    주석 참조.
    """
    from tools.simulator.capacity import margin_sps

    current = item.current
    item.current = previous_value
    try:
        return margin_sps(store)
    finally:
        item.current = current
```

`capacity` 를 함수 안에서 import 하는 이유는 `capacity.py` 가 `config_store` 를
참조하지 않더라도 순환 import 위험을 아예 없애기 위해서다.

- [ ] **Step 5: 테스트 실행해 통과 확인**

Run: `python -m pytest host/tests/test_capacity.py host/tests/test_config_store.py -v`
Expected: PASS (25 passed)

`test_disabling_a_channel_is_never_capacity_rejected` 가 통과하는 이유: 채널을
끄면 `required_sps` 가 줄어 검사를 자연히 통과한다. 별도 예외 처리가 필요 없다.

- [ ] **Step 6: 전체 테스트 실행**

Run: `python -m pytest -v`
Expected: 전체 PASS

- [ ] **Step 7: 커밋**

```bash
git add tools/simulator/capacity.py tools/simulator/config_store.py host/tests/test_capacity.py
git commit -m "feat(sim): ADC 스케줄 용량 검증 (ERR,CAPACITY)

개별 값이 전부 범위 안이어도 조합이 물리적으로 불가능할 수 있다.
7채널을 각각 10ms 주기로 두고 DRATE 를 30 SPS 로 잡으면 요구 700 SPS 대
가용 30 SPS 라 큐가 영구히 넘친다.

ADS1256 은 멀티플렉스 ADC 라 채널 전환마다 MUX→SYNC→WAKEUP→변환대기가
필요하다. DRATE 만으로 가용률을 계산하면 안 되므로 정착시간을 함께 넣었다.

거부 메시지에 요구·가용 수치를 담아 무엇을 줄여야 하는지 알 수 있게 했다.
거부된 변경은 되돌린다.

SETTLING_MS 는 잠정값이며 실기기 실측으로 확정해야 한다."
```

---

## 완료 확인

계획 1이 끝나면 다음이 가능해야 한다.

- [ ] `python -m pytest -v` 전체 통과 (보드 불필요)
- [ ] `python -m tools.cli.markon_cli list` 로 설정 카탈로그가 그룹별로 출력된다
- [ ] `set pwr.5v false` 가 `INTERLOCK`으로 거부되고 사유가 표시된다
- [ ] `set tx.period_ms 250` 이 수락되고 `get` 으로 확인된다
- [ ] `monitor` 가 텔레메트리를 받고 누락 통계를 낸다
- [ ] 체크섬이 깨진 `$HB` 가 CONFIG 모드를 열지 못한다
- [ ] 달성 불가능한 채널 주기 조합이 `CAPACITY` 로 거부되고 요구·가용 수치가 표시된다
- [ ] `protocol/specification.md` 가 펌웨어 구현의 계약으로 쓸 수 있을 만큼 구체적이다

## Codex 감사 대응 기록

이 계획에 반영된 감사 지적 (2026-08-13 14:00 판). 각 항목을 원본 코드로 직접
확인한 뒤 반영했다.

| 지적 | 확인 결과 | 반영 |
|---|---|---|
| `$HB*0F` 체크섬 오류 | `0x48^0x42 = 0x0A`. Q2 주석이 틀림 | Task 2 검증 벡터, Global Constraints |
| 하트비트 방향 모호 | Q2 는 양방향 송신 | Global Constraints 에 방향별 목적 명시 |
| Q2 가 체크섬 검증 전 HB 갱신 | `host_link.c:183-187` 확인 — 사실 | Task 8 회귀 시험 2개 추가 |
| ADC 스케줄 용량 검증 없음 | 사실 | Task 11 신설 |
| PD10 READONLY/INTERLOCK 모순 | 사실 | 인터록 우선으로 규격 정정 |
| ADS1256 참고 드라이버 블로킹 | `spi_write_byte` GPIO 비트뱅, `wait_drdy(500)` busy-wait, 7채널 순차 — 최악 3.5초 정지. 사실 | 설계 §7.3 (계획 2 범위) |
| Flash 2슬롯 erase 단위 | RM0468 사용자 섹터 128 KB | 설계 §6.2 — 서로 다른 섹터 2개 (계획 2 범위) |

## 다음 계획

| 계획 | 내용 | 이 계획에 의존하는 부분 |
|---|---|---|
| 2 · 펌웨어 | H723 CubeMX, host_link, config_store, power_mgr, ads1256, serializer | `protocol/specification.md` 를 계약으로 구현. `markon_cli --port COM7` 로 검증 |
| 3 · GUI | PyQt6 화이트 톤, 스키마 기반 동적 폼, 대시보드, 그래프 | `BoardService` 를 그대로 사용. `serial_server` 로 보드 없이 개발 |
