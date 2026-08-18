# I2C 센서 펌웨어 1차 — 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** J10~J15에 꽂은 조도 센서(BH1750)의 값이 보드에서 읽혀 `i2c` 레코드로 나가고 GUI 대시보드에 뜬다.

**Architecture:** 기존 ADS1256과 같은 갈래로 나눈다 — `app/mk_i2c`(포트 상태기계·드라이버 표, HAL 비의존)와 `bsp/mk_i2c_io`(I2C1·I2C3·I2C5 HAL). 드라이버는 구조체 하나를 채워 표에 등록하는 플러그인이고, 새 칩이 늘어도 스케줄러·송신은 안 바뀐다. 송신은 `mk_telem`이 `ain`과 같은 `seq`·마스크로 낸다.

**Tech Stack:** C11 (arm-none-eabi-gcc / MSVC 호스트 시험), Python 3 시뮬레이터·대조 도구, PyQt6 호스트

**Spec:** `docs/superpowers/specs/2026-08-18-i2c-firmware-design.md`

## Global Constraints

- **`app/`은 HAL을 모른다.** `stm32h7xx` · `HAL_` · `GPIO_` · `USART` 문자열이 들어가면 `host/tests/test_firmware_safety.py`가 막는다.
- **`app/`은 libc stdio를 안 쓴다.** `printf`·`snprintf` 금지 — 키 문자열은 손으로 조립한다.
- **새 `app/*.c`는 `Makefile`의 `C_SRC`와 `tests/run_tests.ps1`의 `$suites` 양쪽에 넣는다.** `tests/check_sources.py`가 검사한다.
- **카탈로그를 고치면 시뮬레이터(`tools/simulator/config_store.py`)와 펌웨어(`firmware/stage1/app/mk_cfgtable.c`) 둘 다 고친다.** `crosscheck_cfgtable.py`가 대조한다.
- **추정 금지** (CLAUDE.md §5). 핀·레지스터·환산식은 데이터시트 인용으로만 확정한다. 확인 불가면 멈추고 알린다.
- 시험 실행:
  - `python -m pytest -q` (현재 536 passed, 44 skipped)
  - `powershell -ExecutionPolicy Bypass -File firmware/stage1/tests/run_tests.ps1` (C 12묶음 + 대조 6종)
  - 보드용 빌드:
    ```bash
    CUBE=/c/ST/STM32CubeIDE_2.1.1/STM32CubeIDE/plugins
    PATH="$CUBE/com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.14.3.rel1.win32_1.0.100.202602081740/tools/bin:$CUBE/com.st.stm32cube.ide.mcu.externaltools.make.win32_2.2.100.202601091506/tools/bin:$PATH" \
      make -C firmware/stage1
    ```
- 커밋 형식: `<type>(<scope>): <한국어 요약>` + 본문에 **왜**. 끝에
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

---

### Task 1: `mk_json_null` — 값이 없는 레코드를 낼 수 있게

**왜 먼저인가:** 규격 §7.5는 `status`가 0이 아니면 `value`에 `null`을 넣으라고 한다. 지금 `mk_json`에는 null을 내는 함수가 없어서, 이것 없이는 오류 레코드를 만들 수 없다.

**Files:**
- Modify: `firmware/stage1/app/mk_json.h`
- Modify: `firmware/stage1/app/mk_json.c`
- Test: `firmware/stage1/tests/test_json.c`
- Modify: `firmware/stage1/tests/crosscheck_json.py` (C ↔ 시뮬레이터 대조에 null 벡터 추가)

**Interfaces:**
- Consumes: 없음
- Produces: `void mk_json_null(MkJson *j, const char *key);`

- [ ] **Step 1: 실패하는 시험을 쓴다**

`firmware/stage1/tests/test_json.c`의 마지막 시험 함수 뒤에 넣고, `main()`에 호출을 추가한다.

```c
/* 🔴 값이 없다는 것과 0 은 다르다. 규격 §7.5 는 센서가 대답하지 않을 때
 *    마지막 값을 다시 싣는 대신 null 을 넣으라고 한다 — 옛 값을 실으면
 *    화면이 살아 있는 센서처럼 보인다. */
static void test_null_is_not_zero_and_not_a_string(void)
{
    char buf[64];
    MkJson j;
    mk_json_begin(&j, buf, sizeof buf);
    mk_json_str(&j, "type", "i2c");
    mk_json_null(&j, "value");
    int len = mk_json_end(&j);

    CHECK(len > 0, "null 을 넣어도 JSON 이 완성된다");
    CHECK(strcmp(buf, "{\"type\":\"i2c\",\"value\":null}") == 0, buf);
}
```

- [ ] **Step 2: 시험이 깨지는 것을 본다**

```
powershell -ExecutionPolicy Bypass -File firmware/stage1/tests/run_tests.ps1
```

기대: `빌드 실패: test_json.exe` (`mk_json_null` 미정의).

- [ ] **Step 3: 구현한다**

`mk_json.h`에 `mk_json_bool` 선언 바로 아래:

```c
/* 🔴 "값이 없다" 를 싣는다. 0 이나 "" 로 대신하지 않는다 — 호스트가 그것을
 *    측정값으로 받는다 (규격 §7.5). */
void mk_json_null(MkJson *j, const char *key);
```

`mk_json.c`에서 `mk_json_bool` 구현 옆에 같은 모양으로:

```c
void mk_json_null(MkJson *j, const char *key)
{
    key_begin(j, key);
    put(j, "null");
}
```

> 기존 `mk_json_bool`의 구현을 그대로 보고 내부 헬퍼 이름(`key_begin`/`put`)을 맞춘다. 이름이 다르면 그 파일의 것을 쓴다.

- [ ] **Step 4: 시험이 통과하는 것을 본다**

```
powershell -ExecutionPolicy Bypass -File firmware/stage1/tests/run_tests.ps1
```

기대: `test_json` PASSED, 나머지 11묶음도 그대로 PASSED, 대조 6종 MATCH.

- [ ] **Step 5: 커밋**

```bash
git add firmware/stage1/app/mk_json.c firmware/stage1/app/mk_json.h firmware/stage1/tests/test_json.c
git commit -m "feat(fw): JSON 에 null 을 실을 수 있게 — 값 없음과 0 은 다르다"
```

---

### Task 2: 규격에 `status=3`(지원하지 않는 종류)을 더한다 — 호스트·시뮬레이터까지

**왜 지금인가:** 1차에는 조도 드라이버만 있다. 사용자가 온습도를 골라 두면 "아무것도 안 보내기" 말고는 할 수 있는 것이 없고, 그러면 값이 왜 없는지 화면 어디에도 답이 없다. 펌웨어가 이 값을 내기 전에 받는 쪽을 먼저 세운다.

**Files:**
- Modify: `protocol/specification.md` (§7.5 `status` 표)
- Modify: `tools/simulator/telemetry.py` (`build_i2c_record` 주석 — 값 자체는 이미 인자)
- Modify: `host/gui/screen.py` (`SensorState`)
- Modify: `host/gui/qt/sensor_card.py`
- Test: `host/tests/test_sensors.py`

**Interfaces:**
- Consumes: 없음
- Produces: `SensorState.unsupported` (property, `status == 3`), 규격 §7.5의 `status=3`

- [ ] **Step 1: 실패하는 시험을 쓴다**

`host/tests/test_sensors.py` 끝에:

```python
def test_unsupported_kind_is_not_the_same_as_broken():
    """🔴 `status=3` 은 고장이 아니다.

    센서가 죽은 것과 펌웨어에 그 종류의 드라이버가 없는 것은 사용자가 할
    일이 다르다 — 앞은 배선을 보고, 뒤는 기다리거나 종류를 바꾼다. 같은
    빨간 글씨로 보이면 배선을 뜯게 된다.
    """
    rec = {"type": "i2c", "connector_id": 11, "quantity": "temp",
           "value": None, "status": 3, "t": 1000}
    (s,) = build_sensors([rec], reachable=True)

    assert s.unsupported is True
    assert s.broken is False


def test_no_response_is_broken_but_not_unsupported():
    rec = {"type": "i2c", "connector_id": 10, "quantity": "lux",
           "value": None, "status": 1, "t": 1000}
    (s,) = build_sensors([rec], reachable=True)

    assert s.broken is True
    assert s.unsupported is False
```

- [ ] **Step 2: 시험이 깨지는 것을 본다**

```
python -m pytest host/tests/test_sensors.py -q -k unsupported
```

기대: `AttributeError: 'SensorState' object has no attribute 'unsupported'`.

- [ ] **Step 3: 구현한다**

`host/gui/screen.py`의 `SensorState.broken`을 고치고 옆에 하나 더 둔다:

```python
    @property
    def broken(self) -> bool:
        """센서가 대답을 안 하거나 값이 깨졌다.

        🔴 꺼진 포트는 여기 오지 않는다. 규격 §7.5 대로 아예 레코드를 안
           보내기 때문이다 — 미연결은 정상 상태다(설계 원칙 3).

        🔴 `status=3`(지원하지 않는 종류)은 고장이 아니다. 배선을 뜯을
           일이 아니라 펌웨어가 아직 그 칩을 모르는 것이다.
        """
        return self.status not in (0, 3)

    @property
    def unsupported(self) -> bool:
        """펌웨어에 이 종류의 드라이버가 없다 (규격 §7.5, status=3)."""
        return self.status == 3
```

`host/gui/qt/sensor_card.py`의 132행 근처, 색을 고르는 자리를 셋으로 가른다:

```python
        if sensor.broken:
            tone = Color.FAULT
        elif sensor.unsupported:
            tone = Color.INK_FAINT          # 고장이 아니다 — 흐리게만
        else:
            tone = Color.INK_FAINT
        ...
                f"color: {tone}; "
```

그리고 값 자리에 뜨는 글월을 종류에 맞춘다 (같은 파일에서 값이 `None`일 때 쓰는 문자열을 찾아 갈래를 넣는다):

```python
        if sensor.unsupported:
            text = "지원 안 함"
        elif sensor.value is None:
            text = "값 없음"
        else:
            text = f"{sensor.value:.2f}"
```

`protocol/specification.md` §7.5의 `status` 줄을 고친다:

```
| `status` | u16 | 0=정상 · 1=응답 없음 · 2=데이터 오류 · 3=지원하지 않는 종류 |
```

그 표 아래에 근거를 남긴다:

```
🔴 **`status=3`은 고장이 아니다.** 카탈로그에 있는 종류인데 펌웨어에 그
드라이버가 아직 없을 때 쓴다. 아무것도 안 보내면 값이 왜 없는지 화면 어디에도
답이 없고, `status=1`로 보내면 사용자가 배선을 뜯는다. 호스트는 이것을 고장이
아니라 "지원 안 함"으로 보여야 한다.
```

`tools/simulator/telemetry.py`의 `build_i2c_record` docstring 아래에 한 줄:

```python
    #    status: 0=정상 · 1=응답 없음 · 2=데이터 오류 · 3=지원하지 않는 종류
```

- [ ] **Step 4: 시험이 통과하는 것을 본다**

```
python -m pytest -q
```

기대: 새 시험 2개 포함 전부 통과. `test_spec_sync.py`가 규격과 호스트 상수를 대조하므로 함께 초록인지 본다.

- [ ] **Step 5: 커밋**

```bash
git add protocol/specification.md host/gui/screen.py host/gui/qt/sensor_card.py host/tests/test_sensors.py tools/simulator/telemetry.py
git commit -m "feat(proto): status=3 지원하지 않는 종류 — 조용한 빈칸 대신 이유를 말한다"
```

---

### Task 3: `mk_i2c.h` — 공유 상수를 핀 쪽 층으로 옮긴다

**왜:** `MK_I2C_COUNT`(6), 포트→버스 표, 종류 열거값이 지금 `mk_cfgtable.c`에만 있다. 드라이버 층이 같은 것을 다시 적으면 카탈로그가 말하는 것과 실제로 도는 것이 갈린다 — `MK_LED_COUNT`·`MK_SOL_COUNT`에서 이미 정한 규칙이다.

**Files:**
- Create: `firmware/stage1/app/mk_i2c.h`
- Modify: `firmware/stage1/app/mk_cfgtable.c`
- Test: `firmware/stage1/tests/test_cfgtable.c`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `#define MK_I2C_COUNT 6`
  - `MkI2cKind` — `MK_I2C_KIND_NONE=0` `LUX=1` `HUMID=2` `IR_TEMP=3` `WATER_TEMP=4`
  - `uint8_t mk_i2c_bus_of(unsigned port);` — 포트 0~5 → 버스 번호 (1·3·5)
  - `unsigned mk_i2c_connector_of(unsigned port);` — 포트 0~5 → 10~15

- [ ] **Step 1: 실패하는 시험을 쓴다**

`firmware/stage1/tests/test_cfgtable.c`에 추가하고 `main()`에서 부른다.

```c
/* 🔴 카탈로그의 버스 이름표와 드라이버가 쓰는 버스 번호가 갈리면, 화면에는
 *    I2C3 이라고 뜨는데 보드는 I2C1 을 두드린다. 한 곳에서 나오는지 본다. */
static void test_bus_table_matches_the_catalog(void)
{
    MkConfig cfg;
    mk_cfgtable_init(&cfg);

    static const char *const EXPECT[MK_I2C_COUNT] = {
        "I2C3", "I2C3", "I2C5", "I2C5", "I2C1", "I2C1"
    };
    for (unsigned p = 0; p < MK_I2C_COUNT; p++) {
        char key[16];
        int n = 0;
        key[n++] = 'i'; key[n++] = '2'; key[n++] = 'c';
        unsigned jack = mk_i2c_connector_of(p);
        key[n++] = (char)('0' + jack / 10u);
        key[n++] = (char)('0' + jack % 10u);
        const char *suffix = ".bus";
        for (const char *q = suffix; *q; q++) { key[n++] = *q; }
        key[n] = '\0';

        MkCfgItem *it = mk_cfg_find(&cfg, key);
        CHECK(it != NULL, key);
        CHECK(it != NULL && strcmp(it->def.s, EXPECT[p]) == 0, "버스 이름표");

        /* "I2C3" 의 숫자와 mk_i2c_bus_of 가 같아야 한다 */
        uint8_t want = (uint8_t)(EXPECT[p][3] - '0');
        CHECK(mk_i2c_bus_of(p) == want, "버스 번호가 이름표와 같다");
    }
}
```

- [ ] **Step 2: 시험이 깨지는 것을 본다**

```
powershell -ExecutionPolicy Bypass -File firmware/stage1/tests/run_tests.ps1
```

기대: `빌드 실패: test_cfgtable.exe` (`mk_i2c_bus_of` 미정의).

- [ ] **Step 3: 구현한다**

`firmware/stage1/app/mk_i2c.h`를 만든다 (드라이버 계약은 Task 4에서 이 파일에 더 붙는다):

```c
/* I2C 센서 포트 J10~J15 — HAL 비의존.
 *
 * 🔴 짝 커넥터는 같은 버스다 (넷리스트 확인 2026-08-18):
 *
 *      I2C3  SCL=PA8(100)  SDA=PC9(99)    J10 · J11
 *      I2C5  SCL=PC11(112) SDA=PC10(111)  J12 · J13
 *      I2C1  SCL=PB8(139)  SDA=PB9(140)   J14 · J15
 *
 *    같은 주소를 짝에 함께 꽂으면 충돌한다. 펌웨어가 막을 수 없다 —
 *    주소는 사용자가 설정으로 넣는다.
 *
 * 🔴 개수와 버스 표를 여기 두는 이유는 mk_cfgtable 이 이것을 쓰기
 *    때문이다. 두 곳에 적으면 카탈로그가 말하는 버스와 실제로 두드리는
 *    버스가 갈린다 (MK_LED_COUNT·MK_SOL_COUNT 와 같은 규칙).
 */
#ifndef MK_I2C_H
#define MK_I2C_H

#include <stddef.h>
#include <stdint.h>

#define MK_I2C_COUNT   6

/* 🔴 카탈로그의 choices 와 같은 값이어야 한다 (mk_cfgtable 의
 *    I2C_KIND_CHOICES, 시뮬레이터의 I2C_KINDS). 종류는 칩 모델이 아니라
 *    무엇을 재는가다 — 사용자 확정 2026-08-17. */
typedef enum {
    MK_I2C_KIND_NONE       = 0,
    MK_I2C_KIND_LUX        = 1,
    MK_I2C_KIND_HUMID      = 2,
    MK_I2C_KIND_IR_TEMP    = 3,
    MK_I2C_KIND_WATER_TEMP = 4
} MkI2cKind;

/* 포트 0~5 → 버스 번호 (1 · 3 · 5). 범위 밖이면 0. */
uint8_t  mk_i2c_bus_of(unsigned port);

/* 포트 0~5 → 커넥터 번호 (10~15). 범위 밖이면 0. */
unsigned mk_i2c_connector_of(unsigned port);

#endif /* MK_I2C_H */
```

`firmware/stage1/app/mk_i2c.c`를 만든다 (Task 4에서 상태기계가 여기 붙는다):

```c
#include "mk_i2c.h"

/* J10·J11 = I2C3 · J12·J13 = I2C5 · J14·J15 = I2C1 (넷리스트 확인) */
static const uint8_t BUS_OF[MK_I2C_COUNT] = { 3u, 3u, 5u, 5u, 1u, 1u };

uint8_t mk_i2c_bus_of(unsigned port)
{
    return port < MK_I2C_COUNT ? BUS_OF[port] : 0u;
}

unsigned mk_i2c_connector_of(unsigned port)
{
    return port < MK_I2C_COUNT ? 10u + port : 0u;
}
```

`mk_cfgtable.c`에서 자기 `#define MK_I2C_COUNT 6`을 지우고 헤더를 include 한다 (sol 때와 같다):

```c
#include "mk_ws2812.h"      /* MK_LED_COUNT — 정의는 저쪽이 들고 있다 */
#include "mk_solctl.h"      /* MK_SOL_COUNT — 정의는 저쪽이 들고 있다 */
#include "mk_i2c.h"         /* MK_I2C_COUNT · 버스 표 — 정의는 저쪽이 들고 있다 */
```

그리고 `add_i2c()`의 `I2C_BUS[]` 문자열 표는 남기되, **버스 번호에서 만들어지도록** 두지 말고 그대로 둔다 — 시험이 둘을 대조한다.

빌드·시험 목록에 새 모듈을 넣는다:
- `firmware/stage1/Makefile`의 `C_SRC`에 `app/mk_i2c.c \`
- `firmware/stage1/tests/run_tests.ps1`의 `test_cfgtable.exe` 줄 소스 목록 끝에 `..\app\mk_i2c.c`
- `firmware/stage1/tests/Makefile`의 `test_cfgtable` 규칙에도 같은 파일

- [ ] **Step 4: 시험이 통과하는 것을 본다**

```
powershell -ExecutionPolicy Bypass -File firmware/stage1/tests/run_tests.ps1
```

기대: 12묶음 PASSED, `check_sources.py`가 `app/ 모듈 14개`라고 말하고, `crosscheck_cfgtable.py`가 `MATCH (93 items, 10 fields)` 그대로.

- [ ] **Step 5: 커밋**

```bash
git add firmware/stage1/app/mk_i2c.c firmware/stage1/app/mk_i2c.h firmware/stage1/app/mk_cfgtable.c firmware/stage1/Makefile firmware/stage1/tests/Makefile firmware/stage1/tests/run_tests.ps1 firmware/stage1/tests/test_cfgtable.c
git commit -m "refactor(fw): I2C 포트 개수와 버스 표를 드라이버 층이 소유한다"
```

---

### Task 4: 드라이버 계약과 포트 상태기계

**왜:** 이 층이 있어야 "언제 무엇을 두드릴지"가 정해진다. 칩이 무엇인지 몰라도 전부 시험된다 — 가짜 드라이버와 가짜 io로 돌린다.

**Files:**
- Modify: `firmware/stage1/app/mk_i2c.h`
- Modify: `firmware/stage1/app/mk_i2c.c`
- Create: `firmware/stage1/tests/test_i2c.c`
- Modify: `firmware/stage1/tests/run_tests.ps1`, `firmware/stage1/tests/Makefile`

**Interfaces:**
- Consumes: `MK_I2C_COUNT` · `MkI2cKind` · `mk_i2c_bus_of` · `mk_i2c_connector_of` (Task 3), `MkConfig`/`mk_cfg_find` (기존 `mk_config.h`)
- Produces:
  - `MkI2cIo` `{ MkI2cXfer xfer; void *ctx; }`
  - `MkI2cValue` `{ const char *quantity; float value; }`
  - `MkI2cDriver` `{ kind, default_addr, warmup_ms, start, read }`
  - `MkI2c` (상태 구조체), `mk_i2c_init(MkI2c*, const MkI2cIo*)`, `mk_i2c_tick(MkI2c*, MkConfig*, int64_t now_ms)`
  - `int mk_i2c_take(MkI2c *i, MkI2cOut *out);` — 내보낼 것이 있으면 1
  - `MkI2cOut` `{ unsigned connector_id; const char *quantity; float value; int have_value; uint16_t status; int64_t t_ms; }`

- [ ] **Step 1: 실패하는 시험을 쓴다**

`firmware/stage1/tests/test_i2c.c`:

```c
/* mk_i2c 단위 시험 — 보드도 센서도 필요 없다.
 *
 * 🔴 여기서 지키는 것은 "언제 두드리는가" 다. 칩이 무엇인지는 드라이버가
 *    알고, 이 층은 순서·주기·격리만 안다.
 */
#include <stdio.h>
#include <string.h>
#include "../app/mk_i2c.h"
#include "../app/mk_cfgtable.h"

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
    if (!(cond)) { printf("  FAIL %s\n", msg); failures++; }    \
    else         { printf("  ok   %s\n", msg); }                \
} while (0)

/* ---- 가짜 버스 ---------------------------------------------------------- */

#define MAX_EV 64
typedef struct {
    uint8_t bus[MAX_EV];
    uint8_t addr[MAX_EV];
    uint8_t first_tx[MAX_EV];
    size_t  ntx[MAX_EV];
    size_t  nrx[MAX_EV];
    int     n;
    int     ret;                 /* xfer 가 돌려줄 값 */
} Bus;

static Bus     BUS;
static MkI2c   I2C;
static MkConfig CFG;

static int fake_xfer(void *ctx, uint8_t bus, uint8_t addr,
                     const uint8_t *tx, size_t ntx, uint8_t *rx, size_t nrx)
{
    Bus *b = (Bus *)ctx;
    if (b->n < MAX_EV) {
        b->bus[b->n] = bus;
        b->addr[b->n] = addr;
        b->first_tx[b->n] = ntx ? tx[0] : 0u;
        b->ntx[b->n] = ntx;
        b->nrx[b->n] = nrx;
        b->n++;
    }
    for (size_t k = 0; k < nrx; k++) { rx[k] = (uint8_t)(k + 1u); }
    return b->ret;
}

static void set_u(const char *key, uint32_t v)
{
    MkCfgItem *it = mk_cfg_find(&CFG, key);
    if (it) { it->cur.u = v; }
}

static void setup(void)
{
    memset(&BUS, 0, sizeof BUS);
    mk_cfgtable_init(&CFG);
    MkI2cIo io = { fake_xfer, &BUS };
    mk_i2c_init(&I2C, &io);
}

/* 포트를 켜고 종류를 고른다. 조도 = MK_I2C_KIND_LUX */
static void enable_lux_port(unsigned jack, uint32_t addr, uint32_t period_ms)
{
    char key[20];
    int n;
    #define KEY(suffix) do {                                        \
        n = 0;                                                      \
        key[n++]='i'; key[n++]='2'; key[n++]='c';                   \
        key[n++]=(char)('0'+jack/10u); key[n++]=(char)('0'+jack%10u);\
        for (const char *q = (suffix); *q; q++) { key[n++] = *q; }  \
        key[n] = '\0';                                              \
    } while (0)
    KEY(".enabled");   set_u(key, 1u);
    KEY(".kind");      set_u(key, (uint32_t)MK_I2C_KIND_LUX);
    KEY(".addr");      set_u(key, addr);
    KEY(".period_ms"); set_u(key, period_ms);
    #undef KEY
}

/* ---- 시험 ---------------------------------------------------------------- */

/* 🔴 꺼진 포트는 버스를 아예 안 건드린다. 안 꽂힌 센서를 두드리면 매 주기
 *    NACK 이 나고, 그것을 오류로 세면 "미연결은 정상" 이라는 원칙이 깨진다. */
static void test_disabled_ports_never_touch_the_bus(void)
{
    setup();
    for (int64_t t = 0; t < 2000; t += 10) { mk_i2c_tick(&I2C, &CFG, t); }
    CHECK(BUS.n == 0, "꺼진 포트는 버스를 안 건드린다");
}

/* 🔴 한 바퀴에 포트 하나. 여섯이 한 바퀴에 다 돌면 최악에 전송이 여섯 번
 *    겹쳐 슈퍼루프가 길어진다. */
static void test_one_port_per_tick(void)
{
    setup();
    for (unsigned jack = 10u; jack <= 15u; jack++) {
        enable_lux_port(jack, 0x23u, 200u);
    }
    BUS.n = 0;
    mk_i2c_tick(&I2C, &CFG, 0);
    CHECK(BUS.n <= 1, "한 바퀴에 전송은 많아야 한 번");
}

/* 🔴 드라이버가 없는 종류는 status=3 이다. 아무것도 안 보내면 값이 왜
 *    없는지 화면 어디에도 답이 없다. */
static void test_unsupported_kind_reports_status_three(void)
{
    setup();
    enable_lux_port(10u, 0x23u, 200u);
    MkCfgItem *k = mk_cfg_find(&CFG, "i2c10.kind");
    k->cur.u = (uint32_t)MK_I2C_KIND_HUMID;      /* 1차에는 드라이버가 없다 */

    for (int64_t t = 0; t < 400; t += 10) { mk_i2c_tick(&I2C, &CFG, t); }

    MkI2cOut out;
    CHECK(mk_i2c_take(&I2C, &out) == 1, "지원 안 하는 종류도 레코드를 낸다");
    CHECK(out.status == 3u, "status=3");
    CHECK(BUS.n == 0, "지원 안 하면 버스를 두드리지 않는다");
}

int main(void)
{
    printf("-- 꺼진 포트 --\n");        test_disabled_ports_never_touch_the_bus();
    printf("-- 라운드로빈 --\n");       test_one_port_per_tick();
    printf("-- 지원 안 하는 종류 --\n"); test_unsupported_kind_reports_status_three();

    printf(failures ? "\nFAILED (%d)\n" : "\nPASSED\n", failures);
    return failures ? 1 : 0;
}
```

시험 목록에 넣는다:
- `run_tests.ps1`: `@{ exe = "test_i2c.exe"; src = "test_i2c.c ..\app\mk_i2c.c ..\app\mk_i2c_bh1750.c ..\app\mk_cfgtable.c ..\app\mk_cfgwire.c ..\app\mk_config.c ..\app\mk_json.c ..\app\mk_ws2812.c ..\app\mk_solctl.c" }`
  (BH1750은 Task 5에서 생긴다. 이 Task에서는 `..\app\mk_i2c_bh1750.c`를 뺀 채로 넣고, Task 5에서 더한다.)
- `tests/Makefile`에 같은 규칙

- [ ] **Step 2: 시험이 깨지는 것을 본다**

```
powershell -ExecutionPolicy Bypass -File firmware/stage1/tests/run_tests.ps1
```

기대: `빌드 실패: test_i2c.exe` — `MkI2c` 등이 없다.

- [ ] **Step 3: 구현한다 — 계약**

`mk_i2c.h`에 Task 3의 내용 아래로 붙인다:

```c
/* ---- 버스 ---------------------------------------------------------------- */

/* 한 번의 전송. ntx>0 && nrx>0 이면 repeated start 로 잇는다.
 *
 * 🔴 반환값이 곧 규격 §7.5 의 status 다:
 *      0 = OK · -1 = 응답 없음(NACK/타임아웃) · -2 = 버스 오류
 *    상태 판정을 이 한 곳으로 모아, 드라이버마다 다르게 세지 않는다. */
typedef int (*MkI2cXfer)(void *ctx, uint8_t bus, uint8_t addr,
                         const uint8_t *tx, size_t ntx,
                         uint8_t *rx, size_t nrx);

typedef struct {
    MkI2cXfer xfer;
    void     *ctx;
} MkI2cIo;

/* ---- 드라이버 ------------------------------------------------------------ */

typedef struct {
    const char *quantity;        /* 규격 §7.5.1 어휘 */
    float       value;
} MkI2cValue;

/* 🔴 값이 둘인 종류는 온습도뿐이다 (규격 §7.5.1). 그래서 2 다. */
#define MK_I2C_VALUES_MAX  2

typedef struct {
    uint8_t  kind;               /* MkI2cKind */
    uint8_t  default_addr;       /* 설정의 addr 이 0(미지정)일 때 */
    uint16_t warmup_ms;          /* 시작 명령 후 첫 값까지 */
    int (*start)(const MkI2cIo *io, uint8_t bus, uint8_t addr);
    int (*read )(const MkI2cIo *io, uint8_t bus, uint8_t addr,
                 MkI2cValue out[MK_I2C_VALUES_MAX], int *n_out);
} MkI2cDriver;

/* ---- 상태기계 ------------------------------------------------------------ */

typedef enum {
    MK_I2C_OFF = 0,
    MK_I2C_START,
    MK_I2C_WARMUP,
    MK_I2C_READY,
    MK_I2C_FAULT                 /* 지원 안 하는 종류 · 시작 실패 */
} MkI2cState;

typedef struct {
    unsigned    connector_id;
    const char *quantity;
    float       value;
    int         have_value;      /* 0 이면 JSON 에 null 을 넣는다 */
    uint16_t    status;
    int64_t     t_ms;
} MkI2cOut;

/* 내보낼 것을 담아 두는 자리. 한 바퀴에 포트 하나만 읽으므로 2 면 넉넉하다. */
#define MK_I2C_OUT_MAX  MK_I2C_VALUES_MAX

typedef struct {
    MkI2cState state;
    uint8_t    kind;             /* 지금 물려 있는 종류 (바뀌면 되돌린다) */
    uint8_t    addr;             /* 지금 쓰는 주소 */
    int64_t    step_ms;          /* 마지막 상태 전이 시각 */
    int64_t    last_read_ms;
} MkI2cPort;

typedef struct MkI2c {
    MkI2cIo   io;
    MkI2cPort port[MK_I2C_COUNT];
    unsigned  next_port;         /* 라운드로빈 출발점 */

    MkI2cOut  out[MK_I2C_OUT_MAX];
    int       n_out;
    int       out_head;
} MkI2c;

void mk_i2c_init(MkI2c *i, const MkI2cIo *io);

/* 한 바퀴에 포트 **하나**만 나아간다. 매 루프 불러도 된다. */
void mk_i2c_tick(MkI2c *i, MkConfig *cfg, int64_t now_ms);

/* 내보낼 것이 있으면 1 을 돌려주고 out 을 채운다. 없으면 0. */
int mk_i2c_take(MkI2c *i, MkI2cOut *out);
```

`mk_i2c.h` 위쪽 include에 `#include "mk_config.h"`를 더한다.

- [ ] **Step 4: 구현한다 — 상태기계**

`mk_i2c.c`에 Task 3의 내용 아래로:

```c
#include <string.h>

#include "mk_i2c_drivers.h"      /* 드라이버 표 — Task 5 에서 채운다 */

/* 설정 키 "i2c1N.<suffix>" 를 손으로 만든다. app/ 은 snprintf 를 안 쓴다. */
static MkCfgItem *port_item(MkConfig *cfg, unsigned port, const char *suffix)
{
    char key[20];
    int n = 0;
    unsigned jack = mk_i2c_connector_of(port);
    key[n++] = 'i'; key[n++] = '2'; key[n++] = 'c';
    key[n++] = (char)('0' + jack / 10u);
    key[n++] = (char)('0' + jack % 10u);
    for (const char *q = suffix; *q; q++) { key[n++] = *q; }
    key[n] = '\0';
    return mk_cfg_find(cfg, key);
}

static uint32_t port_u(MkConfig *cfg, unsigned port, const char *suffix,
                       uint32_t fallback)
{
    MkCfgItem *it = port_item(cfg, port, suffix);
    return it != NULL ? it->cur.u : fallback;
}

static void push_out(MkI2c *i, unsigned connector_id, const char *quantity,
                     float value, int have_value, uint16_t status, int64_t t_ms)
{
    if (i->n_out >= MK_I2C_OUT_MAX) {
        return;                  /* 한 바퀴에 포트 하나라 여기 오지 않는다 */
    }
    MkI2cOut *o = &i->out[i->n_out++];
    o->connector_id = connector_id;
    o->quantity = quantity;
    o->value = value;
    o->have_value = have_value;
    o->status = status;
    o->t_ms = t_ms;
}

void mk_i2c_init(MkI2c *i, const MkI2cIo *io)
{
    memset(i, 0, sizeof *i);
    if (io != NULL) { i->io = *io; }
}

int mk_i2c_take(MkI2c *i, MkI2cOut *out)
{
    if (i->out_head >= i->n_out) {
        i->out_head = 0;
        i->n_out = 0;
        return 0;
    }
    *out = i->out[i->out_head++];
    return 1;
}

/* 한 포트를 한 걸음 나아가게 한다. 버스를 건드렸으면 1 을 돌려준다. */
static int step_port(MkI2c *i, MkConfig *cfg, unsigned p, int64_t now)
{
    MkI2cPort *st = &i->port[p];
    uint8_t bus = mk_i2c_bus_of(p);
    unsigned jack = mk_i2c_connector_of(p);

    int enabled = port_u(cfg, p, ".enabled", 0u) != 0u;
    uint8_t kind = (uint8_t)port_u(cfg, p, ".kind", 0u);
    uint8_t addr = (uint8_t)port_u(cfg, p, ".addr", 0u);

    /* 🔴 꺼졌거나 종류가 "없음" 이면 아무것도 안 한다 — 버스도, 레코드도.
     *    미연결은 정상 상태다 (설계 원칙 3, 규격 §7.5). */
    if (!enabled || kind == (uint8_t)MK_I2C_KIND_NONE) {
        st->state = MK_I2C_OFF;
        return 0;
    }

    /* 설정이 바뀌면 그 포트만 처음으로 되돌린다. */
    if (st->state != MK_I2C_OFF && (st->kind != kind || st->addr != addr)) {
        st->state = MK_I2C_OFF;
    }

    const MkI2cDriver *drv = mk_i2c_driver_for(kind);
    if (drv == NULL) {
        /* 🔴 카탈로그에는 있는데 드라이버가 없다. 조용히 빈칸으로 두지
         *    않는다 — 규격 §7.5 의 status=3. 주기마다 한 번씩만 말한다. */
        if (st->state != MK_I2C_FAULT ||
            now - st->last_read_ms >= (int64_t)port_u(cfg, p, ".period_ms", 200u)) {
            st->state = MK_I2C_FAULT;
            st->last_read_ms = now;
            push_out(i, jack, "", 0.0f, 0, 3u, now);
        }
        return 0;
    }

    uint8_t use_addr = addr != 0u ? addr : drv->default_addr;

    switch (st->state) {
    case MK_I2C_OFF:
        st->kind = kind;
        st->addr = addr;
        st->state = MK_I2C_START;
        st->step_ms = now;
        return 0;                /* 다음 바퀴에 시작 명령을 낸다 */

    case MK_I2C_START: {
        int rc = 0;
        if (drv->start != NULL) {
            rc = drv->start(&i->io, bus, use_addr);
        }
        st->step_ms = now;
        if (rc != 0) {
            /* 시작부터 대답이 없다. 알리고 다음 주기에 다시 시도한다. */
            st->state = MK_I2C_OFF;
            st->last_read_ms = now;
            push_out(i, jack, "", 0.0f, 0, rc == -1 ? 1u : 2u, now);
            return 1;
        }
        st->state = MK_I2C_WARMUP;
        return 1;
    }

    case MK_I2C_WARMUP:
        if (now - st->step_ms < (int64_t)drv->warmup_ms) {
            return 0;            /* 🔴 여기서 기다리지 않는다. 그냥 돌아간다 */
        }
        st->state = MK_I2C_READY;
        st->last_read_ms = now - (int64_t)drv->warmup_ms;   /* 곧 읽는다 */
        return 0;

    case MK_I2C_READY: {
        /* 🔴 실효 주기 = max(period_ms, warmup_ms). 변환보다 빨리 읽으면
         *    같은 값이 여러 줄 나가고 화면이 멈춘 값을 갱신처럼 보여 준다. */
        uint32_t period = port_u(cfg, p, ".period_ms", 200u);
        if (period < drv->warmup_ms) { period = drv->warmup_ms; }
        if (now - st->last_read_ms < (int64_t)period) {
            return 0;
        }
        st->last_read_ms = now;

        MkI2cValue v[MK_I2C_VALUES_MAX];
        int n = 0;
        int rc = drv->read(&i->io, bus, use_addr, v, &n);
        if (rc != 0) {
            push_out(i, jack, "", 0.0f, 0, rc == -1 ? 1u : 2u, now);
            return 1;
        }
        for (int k = 0; k < n && k < MK_I2C_VALUES_MAX; k++) {
            /* 🔴 타임스탬프는 읽기가 끝난 지금이다 (설계 원칙 2). */
            push_out(i, jack, v[k].quantity, v[k].value, 1, 0u, now);
        }
        return 1;
    }

    case MK_I2C_FAULT:
    default:
        st->state = MK_I2C_OFF;
        return 0;
    }
}

void mk_i2c_tick(MkI2c *i, MkConfig *cfg, int64_t now_ms)
{
    if (cfg == NULL) { return; }

    /* 🔴 한 바퀴에 포트 하나. 버스를 건드린 포트가 나오면 거기서 멈춘다.
     *    출발점을 매번 옮겨 한 포트가 나머지를 굶기지 않게 한다. */
    for (unsigned n = 0; n < MK_I2C_COUNT; n++) {
        unsigned p = (i->next_port + n) % MK_I2C_COUNT;
        int touched = step_port(i, cfg, p, now_ms);
        if (touched) {
            i->next_port = (p + 1u) % MK_I2C_COUNT;
            return;
        }
    }
    i->next_port = (i->next_port + 1u) % MK_I2C_COUNT;
}
```

드라이버 표는 별도 파일로 둔다 — `firmware/stage1/app/mk_i2c_drivers.h`:

```c
/* 종류 → 드라이버. 새 칩은 여기 한 줄과 파일 하나가 전부다. */
#ifndef MK_I2C_DRIVERS_H
#define MK_I2C_DRIVERS_H

#include "mk_i2c.h"

/* 없으면 NULL — 부르는 쪽이 status=3 으로 말한다. */
const MkI2cDriver *mk_i2c_driver_for(uint8_t kind);

#endif /* MK_I2C_DRIVERS_H */
```

`firmware/stage1/app/mk_i2c_drivers.c` — 이 Task에서는 빈 표로 둔다 (Task 5에서 BH1750을 넣는다):

```c
#include "mk_i2c_drivers.h"

const MkI2cDriver *mk_i2c_driver_for(uint8_t kind)
{
    (void)kind;
    return NULL;                 /* Task 5 에서 조도를 넣는다 */
}
```

> 🔴 **이 Task의 시험은 드라이버 없이 도는 것만 담는다.** 버스를 실제로 두드리는 동작(시작 명령·변환 대기·주기·격리·status 판정)은 드라이버가 있어야 확인되므로 Task 5로 갔다 — 거기서는 시험이 드라이버의 상수(`warmup_ms`)를 빌려 쓸 수 있어 시간 경계를 손으로 다시 적지 않아도 된다.

빌드 목록에 `app/mk_i2c_drivers.c`도 넣는다 (`Makefile`·`run_tests.ps1`·`tests/Makefile`).

- [ ] **Step 5: 시험이 통과하는 것을 본다**

```
powershell -ExecutionPolicy Bypass -File firmware/stage1/tests/run_tests.ps1
```

기대: `test_i2c` PASSED — 세 시험 전부 ok. 다른 12묶음과 대조 6종도 그대로 통과.

- [ ] **Step 6: 커밋**

```bash
git add firmware/stage1/app/mk_i2c.c firmware/stage1/app/mk_i2c.h firmware/stage1/app/mk_i2c_drivers.c firmware/stage1/app/mk_i2c_drivers.h firmware/stage1/tests/test_i2c.c firmware/stage1/Makefile firmware/stage1/tests/Makefile firmware/stage1/tests/run_tests.ps1
git commit -m "feat(fw): I2C 포트 상태기계 — 기다리지 않고 한 바퀴에 하나씩"
```

---

### Task 5: BH1750 드라이버 — 첫 칩

**데이터시트 확보 완료** — `docs/datasheet/BH1750FVI.pdf` (ROHM Technical Note, Rev.C, 2010.04). 아래는 전부 원본에서 확인한 것이고, **참고 구현과 두 군데가 다르다.**

| 사실 | 근거 |
|---|---|
| 주소: ADDR='L' → `0100011`(0x23) · ADDR='H' → `1011100`(0x5C) | p.10 "2) Slave Address" |
| Continuously H-Resolution Mode = `0001_0000`(0x10), "Measurement Time is typically 120ms" | p.5 "Instruction Set Architecture" |
| 🔴 **첫 측정 대기는 최대 180 ms** — "Wait to complete 1st H-resolution mode measurement.( max. 180ms. )" | p.7 ex1) ② |
| 🔴 **전원을 먼저 켜야 한다** — "Initial state is Power Down mode after VCC and DVI supply", Power On = `0000_0001`, "Waiting for measurement command" | p.4·p.5 |
| 명령마다 STOP 이 필요하다 — "BH1750FVI is not able to accept plural command without stop condition. Please insert SP every 1 Opecode." | p.10 "3) Write Format" |
| 읽기는 High Byte[15:8] → Low Byte[7:0], `raw / 1.2` = lx | p.10 "4) Read Format" |

🔴 **계획 수정 둘** (데이터시트가 참고 구현보다 우선한다):

1. `start` 는 **Power On(0x01) 을 먼저 보내고, 그다음 모드(0x10)** 를 보낸다. 칩은 전원 인가 직후 Power Down 상태라, 모드 명령만 보내면 아무 일도 안 일어날 수 있다. 두 명령은 **각각 STOP 으로 끝나야 한다** — 우리 `xfer` 가 한 번 부를 때마다 STOP 을 내므로 두 번 부르면 된다.
2. `warmup_ms` 는 **120 이 아니라 180** 이다. 120 은 typ 이고 첫 측정의 max 는 180 이다 — 120 에 읽으면 변환이 안 끝난 값을 읽는다. 값이 나오기는 나오므로 눈으로는 못 가린다.

**Files:**
- Create: `firmware/stage1/app/mk_i2c_bh1750.c`
- Create: `firmware/stage1/app/mk_i2c_bh1750.h`
- Modify: `firmware/stage1/app/mk_i2c_drivers.c`
- Test: `firmware/stage1/tests/test_i2c.c` (드라이버 전용 시험 추가)
- Modify: `Makefile` · `tests/Makefile` · `tests/run_tests.ps1`

**Interfaces:**
- Consumes: `MkI2cIo` · `MkI2cValue` · `MkI2cDriver` (Task 4)
- Produces: `extern const MkI2cDriver MK_I2C_BH1750;`, `float mk_bh1750_lux(uint16_t raw);` (그리고 `MK_I2C_BH1750.warmup_ms` 를 시험이 상수로 빌려 쓴다)

- [ ] **Step 1: 실패하는 시험을 쓴다**

`test_i2c.c`에 추가하고 `main()`에서 부른다.

```c
#include "../app/mk_i2c_bh1750.h"

/* 🔴 환산 상수를 시험이 손으로 다시 적지 않는다. 구현과 시험이 같은 오해를
 *    나눠 가지면 둘이 늘 일치하므로 영원히 통과한다 — 전류 환산이 정확히
 *    절반으로 틀렸는데 시험이 못 잡았던 것이 그것이다. */
static void test_bh1750_conversion_uses_the_implementation_constant(void)
{
    CHECK(mk_bh1750_lux(0u) == 0.0f, "0 은 0 lx");

    /* 1.2 로 나눈다는 사실 자체는 여기서 한 번만 못 박는다 */
    float one = mk_bh1750_lux(12u);
    CHECK(one > 9.9f && one < 10.1f, "raw 12 = 10 lx (raw / 1.2)");

    float full = mk_bh1750_lux(0xFFFFu);
    CHECK(full > 54611.0f && full < 54613.0f, "만재 65535 → 약 54612 lx");
}

/* 🔴 바이트 순서를 못 박는다. BH1750 은 MSB 먼저이고 MLX90614 는 LSB
 *    먼저다 — 순서가 뒤집혀도 값은 나오므로 눈으로는 못 가린다. */
static void test_bh1750_reads_two_bytes_msb_first(void)
{
    setup();
    /* fake_xfer 는 rx 를 1,2,... 로 채운다 → MSB=1 LSB=2 → raw=0x0102 */
    MkI2cIo io = { fake_xfer, &BUS };
    MkI2cValue v[MK_I2C_VALUES_MAX];
    int n = 0;
    int rc = MK_I2C_BH1750.read(&io, 3u, 0x23u, v, &n);

    CHECK(rc == 0, "읽기 성공");
    CHECK(n == 1, "값은 하나 (조도)");
    CHECK(n == 1 && strcmp(v[0].quantity, "lux") == 0, "quantity 는 lux");
    CHECK(n == 1 && v[0].value > 215.0f && v[0].value < 216.0f,
          "0x0102 = 258 → 215 lx (MSB 먼저)");
    CHECK(BUS.n == 1 && BUS.ntx[0] == 0u && BUS.nrx[0] == 2u,
          "명령 없이 2바이트만 읽는다");
}

/* 🔴 전원을 먼저 켠다. 칩은 전원 인가 직후 Power Down 이라(p.4) 모드 명령만
 *    보내면 받지 않을 수 있다. 순서가 뒤바뀌어도 "안 켜진다" 로만 보인다. */
static void test_bh1750_start_powers_on_before_selecting_the_mode(void)
{
    setup();
    MkI2cIo io = { fake_xfer, &BUS };
    int rc = MK_I2C_BH1750.start(&io, 3u, 0x23u);

    CHECK(rc == 0, "시작 성공");
    CHECK(BUS.n == 2, "명령을 두 번 나눠 보낸다 (STOP 이 사이에 든다)");
    CHECK(BUS.n == 2 && BUS.first_tx[0] == 0x01u, "먼저 Power On 0x01");
    CHECK(BUS.n == 2 && BUS.first_tx[1] == 0x10u, "그다음 연속 고해상도 0x10");
    for (int k = 0; k < BUS.n; k++) {
        CHECK(BUS.ntx[k] == 1u && BUS.nrx[k] == 0u, "각각 1바이트 쓰기");
    }
    /* 🔴 180 이다. 120(typ)이 아니라 첫 측정의 max 를 기다린다 (p.7). */
    CHECK(MK_I2C_BH1750.warmup_ms == 180u, "변환 대기 180 ms");
    CHECK(MK_I2C_BH1750.default_addr == 0x23u, "기본 주소 0x23");
}

/* 🔴 전원 켜기가 실패하면 모드를 보내지 않는다. 대답 없는 버스에 계속
 *    쓰면 실패가 어디서 났는지 흐려진다. */
static void test_bh1750_start_stops_if_power_on_fails(void)
{
    setup();
    BUS.ret = -1;
    MkI2cIo io = { fake_xfer, &BUS };
    int rc = MK_I2C_BH1750.start(&io, 3u, 0x23u);

    CHECK(rc == -1, "실패를 그대로 돌려준다");
    CHECK(BUS.n == 1, "두 번째 명령을 보내지 않는다");
}
```

- [ ] **Step 2: 시험이 깨지는 것을 본다**

```
powershell -ExecutionPolicy Bypass -File firmware/stage1/tests/run_tests.ps1
```

기대: `빌드 실패: test_i2c.exe` — `MK_I2C_BH1750`이 없다.

- [ ] **Step 3: 구현한다**

`firmware/stage1/app/mk_i2c_bh1750.h`:

```c
/* BH1750FVI 조도 센서 — HAL 비의존.
 *
 * 🔴 근거: BH1750 데이터시트(확보본). 참고 구현은
 *    LaneControlSystemQ2/STM32Code/App/Drivers/bh1750.{c,h} 에 있다.
 *
 *      주소   0x23 (ADDR 핀 Low) · 0x5C (High)
 *      0x10   연속 고해상도 모드, 1 lx, 변환 120 ms
 *      읽기   2바이트 MSB 먼저, lux = raw / 1.2
 *
 * 🔴 레지스터 주소가 없다. 명령은 1바이트를 그냥 쓰고, 읽기는 명령 없이
 *    2바이트를 받는다 — MLX90614 처럼 repeated start 를 쓰지 않는다.
 */
#ifndef MK_I2C_BH1750_H
#define MK_I2C_BH1750_H

#include "mk_i2c.h"

extern const MkI2cDriver MK_I2C_BH1750;

/* 원시값을 lx 로. 시험이 이 함수를 통해 상수를 빌려 쓴다. */
float mk_bh1750_lux(uint16_t raw);

#endif /* MK_I2C_BH1750_H */
```

`firmware/stage1/app/mk_i2c_bh1750.c`:

```c
#include "mk_i2c_bh1750.h"

/* 🔴 근거는 전부 docs/datasheet/BH1750FVI.pdf (ROHM Rev.C, 2010.04) 다. */
#define BH1750_ADDR_DEFAULT   0x23u   /* p.10 ADDR='L' → 0100011 */
#define BH1750_OP_POWER_ON    0x01u   /* p.5 "Waiting for measurement command" */
#define BH1750_OP_CONT_HRES   0x10u   /* p.5 연속 고해상도, typ 120ms */
/* 🔴 120 이 아니라 180 이다. 120 은 typ 이고, p.7 ex1) 는 첫 측정을
 *    "max. 180ms." 기다리라고 한다. 120 에 읽으면 변환이 안 끝난 값을
 *    읽는데 값은 나오므로 눈으로 못 가린다. */
#define BH1750_WARMUP_MS      180u
#define BH1750_LUX_DIVISOR    1.2f

float mk_bh1750_lux(uint16_t raw)
{
    return (float)raw / BH1750_LUX_DIVISOR;
}

static int bh1750_start(const MkI2cIo *io, uint8_t bus, uint8_t addr)
{
    /* 🔴 전원부터 켠다. "Initial state is Power Down mode after VCC and DVI
     *    supply"(p.4) 라 모드 명령만 보내면 칩이 받지 않을 수 있다.
     *
     * 🔴 두 번 나눠 보내는 것이 요건이다 — "not able to accept plural
     *    command without stop condition. Please insert SP every 1
     *    Opecode."(p.10) 우리 xfer 는 한 번 부를 때마다 STOP 을 낸다. */
    uint8_t on = BH1750_OP_POWER_ON;
    int rc = io->xfer(io->ctx, bus, addr, &on, 1u, NULL, 0u);
    if (rc != 0) {
        return rc;
    }
    uint8_t mode = BH1750_OP_CONT_HRES;
    return io->xfer(io->ctx, bus, addr, &mode, 1u, NULL, 0u);
}

static int bh1750_read(const MkI2cIo *io, uint8_t bus, uint8_t addr,
                       MkI2cValue out[MK_I2C_VALUES_MAX], int *n_out)
{
    uint8_t rx[2] = {0u, 0u};
    int rc = io->xfer(io->ctx, bus, addr, NULL, 0u, rx, sizeof rx);
    if (rc != 0) {
        *n_out = 0;
        return rc;
    }
    /* 🔴 MSB 먼저다. 뒤집어도 값은 나오므로 눈으로는 못 가린다. */
    uint16_t raw = (uint16_t)(((uint16_t)rx[0] << 8) | (uint16_t)rx[1]);
    out[0].quantity = "lux";     /* 규격 §7.5.1 어휘 */
    out[0].value = mk_bh1750_lux(raw);
    *n_out = 1;
    return 0;
}

const MkI2cDriver MK_I2C_BH1750 = {
    .kind = (uint8_t)MK_I2C_KIND_LUX,
    .default_addr = BH1750_ADDR_DEFAULT,
    .warmup_ms = BH1750_WARMUP_MS,
    .start = bh1750_start,
    .read = bh1750_read,
};
```

`mk_i2c_drivers.c`를 채운다:

```c
#include "mk_i2c_drivers.h"
#include "mk_i2c_bh1750.h"

/* 🔴 종류 하나에 드라이버 하나. 새 칩은 여기 한 줄과 파일 하나다. */
static const MkI2cDriver *const TABLE[] = {
    &MK_I2C_BH1750,
};

const MkI2cDriver *mk_i2c_driver_for(uint8_t kind)
{
    for (size_t k = 0; k < sizeof TABLE / sizeof TABLE[0]; k++) {
        if (TABLE[k]->kind == kind) {
            return TABLE[k];
        }
    }
    return NULL;                 /* 부르는 쪽이 status=3 으로 말한다 */
}
```

빌드·시험 목록에 `app/mk_i2c_bh1750.c`를 넣는다 (세 곳).

- [ ] **Step 4: 시험이 통과하는 것을 본다**

```
powershell -ExecutionPolicy Bypass -File firmware/stage1/tests/run_tests.ps1
```

기대: `test_i2c` PASSED — Task 4에서 빨간 채로 두었던 것들이 여기서 전부 초록이 된다. 13묶음 PASSED, 대조 6종 MATCH.

- [ ] **Step 5: 되돌림 검사**

`mk_i2c_bh1750.c`의 바이트 조립을 일부러 뒤집어(`rx[1] << 8 | rx[0]`) 시험이 무는지 본다. 확인했으면 되돌린다.

```
powershell -ExecutionPolicy Bypass -File firmware/stage1/tests/run_tests.ps1
```

기대: `0x0102 = 258 → 215 lx (MSB 먼저)` FAIL. 되돌리면 다시 PASSED.

- [ ] **Step 6: 커밋**

```bash
git add firmware/stage1/app/mk_i2c_bh1750.c firmware/stage1/app/mk_i2c_bh1750.h firmware/stage1/app/mk_i2c_drivers.c firmware/stage1/tests/test_i2c.c firmware/stage1/Makefile firmware/stage1/tests/Makefile firmware/stage1/tests/run_tests.ps1
git commit -m "feat(fw): BH1750 조도 드라이버 — 첫 I2C 칩"
```

---

### Task 6: `mk_telem`이 `i2c` 레코드를 낸다

**Files:**
- Modify: `firmware/stage1/app/mk_telem.h`
- Modify: `firmware/stage1/app/mk_telem.c`
- Test: `firmware/stage1/tests/test_telem.c`
- Create: `firmware/stage1/tests/crosscheck_i2c.py`
- Modify: `firmware/stage1/tests/run_tests.ps1` (대조 목록에 추가)

**Interfaces:**
- Consumes: `MkI2c` · `MkI2cOut` · `mk_i2c_take` (Task 4), `mk_json_null` (Task 1)
- Produces: `void mk_telem_attach_i2c(MkTelem *t, MkI2c *i2c);`

- [ ] **Step 1: 실패하는 시험을 쓴다**

`firmware/stage1/tests/test_telem.c`에 추가한다.

```c
#include "../app/mk_i2c.h"
#include "../app/mk_i2c_bh1750.h"

/* 🔴 seq 는 레코드 종류를 가리지 않고 하나로 이어진다. 따로 세면 호스트의
 *    누락 검출이 무너진다 (규격 §7.1). */
static void test_i2c_records_share_the_sequence_with_ain(void)
{
    setup();
    static MkI2c I2C;
    MkI2cIo io = { fake_xfer_ok, NULL };     /* 아래에 정의. ctx 는 안 쓴다 */
    mk_i2c_init(&I2C, &io);
    mk_telem_attach_i2c(&T, &I2C);
    enable_lux_port_in(&CFG, 10u);

    for (int64_t t = 0; t <= 400; t += 10) {
        mk_i2c_tick(&I2C, &CFG, t);
        mk_telem_tick(&T, t, sink, NULL);
    }

    int ain = 0, i2c = 0;
    uint32_t last_seq = 0;
    int monotonic = 1;
    for (int k = 0; k < N; k++) {
        if (strstr(LINES[k], "\"type\":\"ain\"")) { ain++; }
        if (strstr(LINES[k], "\"type\":\"i2c\"")) { i2c++; }
        uint32_t seq = parse_seq(LINES[k]);   /* 아래에 정의 */
        if (k > 0 && seq != last_seq + 1u) { monotonic = 0; }
        last_seq = seq;
    }
    CHECK(ain > 0 && i2c > 0, "두 종류가 함께 나간다");
    CHECK(monotonic, "seq 가 종류를 가리지 않고 1씩 오른다");
}

/* 🔴 값이 없으면 null 이다. 마지막 값을 다시 실으면 화면이 살아 있는
 *    센서처럼 보인다 (규격 §7.5). */
static void test_failed_read_emits_null_not_the_last_value(void)
{
    setup();
    static MkI2c I2C;
    MkI2cIo io = { fake_xfer_nak, NULL };
    mk_i2c_init(&I2C, &io);
    mk_telem_attach_i2c(&T, &I2C);
    enable_lux_port_in(&CFG, 10u);

    for (int64_t t = 0; t <= 400; t += 10) {
        mk_i2c_tick(&I2C, &CFG, t);
        mk_telem_tick(&T, t, sink, NULL);
    }

    int found = 0;
    for (int k = 0; k < N; k++) {
        if (strstr(LINES[k], "\"type\":\"i2c\"") == NULL) { continue; }
        found = 1;
        CHECK_HAS(LINES[k], "\"value\":null", "값 자리가 null");
        CHECK_HAS(LINES[k], "\"status\":1", "응답 없음은 status=1");
        CHECK(strstr(LINES[k], "\"unit\"") == NULL,
              "unit 을 싣지 않는다 (규격 §7.5)");
    }
    CHECK(found, "실패해도 레코드가 나간다");
}
```

`fake_xfer_ok` / `fake_xfer_nak` / `enable_lux_port_in` / `parse_seq`는 이 파일 위쪽에 헬퍼로 둔다:

```c
static int fake_xfer_ok(void *ctx, uint8_t bus, uint8_t addr,
                        const uint8_t *tx, size_t ntx, uint8_t *rx, size_t nrx)
{
    (void)ctx; (void)bus; (void)addr; (void)tx; (void)ntx;
    for (size_t k = 0; k < nrx; k++) { rx[k] = (uint8_t)(k + 1u); }
    return 0;
}

static int fake_xfer_nak(void *ctx, uint8_t bus, uint8_t addr,
                         const uint8_t *tx, size_t ntx, uint8_t *rx, size_t nrx)
{
    (void)ctx; (void)bus; (void)addr; (void)tx; (void)ntx; (void)rx; (void)nrx;
    return -1;
}

static void enable_lux_port_in(MkConfig *cfg, unsigned jack)
{
    char key[20];
    int n;
    #define K(sfx) do {                                             \
        n = 0; key[n++]='i'; key[n++]='2'; key[n++]='c';            \
        key[n++]=(char)('0'+jack/10u); key[n++]=(char)('0'+jack%10u);\
        for (const char *q=(sfx); *q; q++) { key[n++]=*q; }         \
        key[n]='\0';                                                \
    } while (0)
    MkCfgItem *it;
    K(".enabled");   it = mk_cfg_find(cfg, key); if (it) it->cur.u = 1u;
    K(".kind");      it = mk_cfg_find(cfg, key); if (it) it->cur.u = 1u;
    K(".addr");      it = mk_cfg_find(cfg, key); if (it) it->cur.u = 0x23u;
    K(".period_ms"); it = mk_cfg_find(cfg, key); if (it) it->cur.u = 200u;
    #undef K
}

static uint32_t parse_seq(const char *line)
{
    const char *p = strstr(line, "\"seq\":");
    if (p == NULL) { return 0u; }
    p += 6;
    uint32_t v = 0u;
    while (*p >= '0' && *p <= '9') { v = v * 10u + (uint32_t)(*p - '0'); p++; }
    return v;
}
```

- [ ] **Step 2: 시험이 깨지는 것을 본다**

```
powershell -ExecutionPolicy Bypass -File firmware/stage1/tests/run_tests.ps1
```

기대: `빌드 실패: test_telem.exe` — `mk_telem_attach_i2c`가 없다.

- [ ] **Step 3: 구현한다**

`mk_telem.h`:

```c
#include "mk_i2c.h"
...
    MkI2c *i2c;                  /* 없으면 NULL — ain 만 낸다 */
...
/* I2C 층을 물린다. 🔴 seq 와 tx.fields 를 ain 과 나눠 쓰기 위해서다.
 *    따로 내보내면 두 곳이 갈린다 (규격 §7.5). */
void mk_telem_attach_i2c(MkTelem *t, MkI2c *i2c);
```

`mk_telem.c`에 레코드 조립을 더한다. **필드 순서는 시뮬레이터 `build_i2c_record`와 같게** 둔다 (`schema_ver` `seq` `t` `type` `connector_id` `quantity` `value` `status` `device_id` `time_source` `time_quality`):

```c
static int build_i2c_record(MkTelem *t, const MkI2cOut *o,
                            char *out, size_t cap)
{
    uint32_t mask = cfg_u32(t->cfg, "tx.fields", 0u);
    uint32_t digits = cfg_u32(t->cfg, "tx.float_digits", 4u);

    MkJson j;
    mk_json_begin(&j, out, cap);
    mk_json_u32(&j, "schema_ver", 3u);
    mk_json_u32(&j, "seq", t->seq);
    mk_json_i64(&j, "t", o->t_ms);
    mk_json_str(&j, "type", "i2c");

    if (field_on(t, mask, "connector_id")) {
        mk_json_u32(&j, "connector_id", (uint32_t)o->connector_id);
    }
    /* 🔴 quantity·value 는 마스크로 끌 수 없다 (규격 §7.5). 둘이 빠지면
     *    레코드가 아무 말도 안 한다. */
    mk_json_str(&j, "quantity", o->quantity ? o->quantity : "");
    if (o->have_value) {
        mk_json_f32(&j, "value", o->value, (int)digits);
    } else {
        mk_json_null(&j, "value");
    }
    /* 🔴 unit 을 싣지 않는다 — quantity 가 단위를 이미 정한다 (규격 §7.5). */
    if (field_on(t, mask, "status")) {
        mk_json_u32(&j, "status", o->status);
    }
    if (field_on(t, mask, "device_id")) {
        mk_json_str(&j, "device_id", t->device_id ? t->device_id : "");
    }
    if (field_on(t, mask, "time_source")) {
        mk_json_str(&j, "time_source", "device_clock");
    }
    if (field_on(t, mask, "time_quality")) {
        mk_json_u32(&j, "time_quality", 0u);
    }
    return mk_json_end(&j);
}
```

`mk_telem_tick`의 **맨 앞**(주기 검사보다 먼저)에 I2C 배출을 넣는다:

```c
    /* 🔴 tx.period_ms 를 기다리지 않는다. I2C 는 포트마다 자기 주기가
     *    있고(i2cN.period_ms), 읽은 즉시 내보내는 것이 그 주기를 지키는
     *    유일한 방법이다. 여기서 큐를 또 두면 주기가 둘이 되어 느린 쪽이
     *    빠른 쪽을 덮어쓴다. */
    int sent_i2c = 0;
    if (t->i2c != NULL) {
        MkI2cOut o;
        while (sent_i2c < MK_TELEM_MAX_LINES && mk_i2c_take(t->i2c, &o)) {
            char body[MK_LINE_MAX + 8];
            t->seq++;
            int len = build_i2c_record(t, &o, body, sizeof body);
            if (len <= 0 || (size_t)len + 2u > sizeof body) { continue; }
            body[len] = '\n';
            body[len + 1] = '\0';
            emit(ctx, body, (size_t)len + 1u);
            sent_i2c++;
        }
    }
```

그리고 함수 끝의 반환에 `sent_i2c`를 더한다. `mk_telem_attach_i2c`는 대입 한 줄이다.

- [ ] **Step 4: 시험이 통과하는 것을 본다**

```
powershell -ExecutionPolicy Bypass -File firmware/stage1/tests/run_tests.ps1
```

기대: 13묶음 PASSED.

- [ ] **Step 5: 시뮬레이터와 대조하는 도구를 쓴다**

`firmware/stage1/tests/crosscheck_i2c.py` — 기존 `crosscheck_json.py`의 뼈대를 그대로 따른다 (경로 처리·출력 형식·`sys.stdout.reconfigure`).

```python
"""C 의 i2c 레코드와 시뮬레이터의 build_i2c_record 를 바이트로 맞춘다.

🔴 두 구현이 갈리면 GUI 는 시뮬레이터에서 멀쩡하다가 보드에서만 틀어진다.
   카탈로그 대조(crosscheck_cfgtable.py)가 잡아낸 것과 같은 종류의 사고다.
"""
```

`test_telem.c`에 `--emit-i2c` 인자를 더해 레코드를 그대로 찍게 하고(기존 `--emit`과 같은 방식), Python 쪽에서 같은 입력으로 `build_i2c_record`를 불러 문자열을 비교한다. 비교 벡터 최소 3개:

1. 정상값 (`status=0`, `value` 있음)
2. 응답 없음 (`status=1`, `value=null`)
3. 지원 안 하는 종류 (`status=3`, `value=null`, `quantity=""`)

`run_tests.ps1`의 `$checks` 배열에 `"crosscheck_i2c.py"`를 더한다.

- [ ] **Step 6: 대조가 통과하는 것을 본다**

```
powershell -ExecutionPolicy Bypass -File firmware/stage1/tests/run_tests.ps1
```

기대: `crosscheck_i2c.py` → `MATCH (3 records)`. 대조가 7종이 된다.

- [ ] **Step 7: 커밋**

```bash
git add firmware/stage1/app/mk_telem.c firmware/stage1/app/mk_telem.h firmware/stage1/tests/test_telem.c firmware/stage1/tests/crosscheck_i2c.py firmware/stage1/tests/run_tests.ps1
git commit -m "feat(fw): i2c 레코드를 내보낸다 — seq 와 마스크를 ain 과 나눠 쓴다"
```

---

### Task 7: BSP — 버스를 실제로 연다

**선행 조건 (BLOCKING, CLAUDE.md §5):** 아래 둘을 확보하기 전에는 이 Task를 시작하지 않는다. 확보 못 하면 멈추고 사용자에게 알린다.

1. **AF 번호** — PA8·PC9(I2C3), PC11·PC10(I2C5), PB8·PB9(I2C1). STM32H723 데이터시트(DS13313)의 Alternate function 표를 인용한다. WS2812 때 PA7 AF2를 `DS13313 Rev 1 p.72 Table 8`로 확인한 것과 같은 절차.
2. ~~**`I2C_TIMINGR` 값**~~ — **확정됐다 (2026-08-18)**. 아래 §7.1 참조.

#### 7.1 `I2C_TIMINGR` = `0x60702729` (100 kHz) — 근거

| 단계 | 확인한 것 | 근거 |
|---|---|---|
| 커널 클럭 **선택** | I2C1·2·3·**5**가 한 선택자를 쓴다 (`RCC_D2CCIP2R_I2C1235SEL`) | ST CMSIS `stm32h723xx.h` (Cube FW_H7 V1.13.0) |
| 그 기본값 | `RCC_I2C123CLKSOURCE_D2PCLK1 = 0` = 리셋값 → **APB1** | ST HAL `stm32h7xx_hal_rcc_ex.h` |
| APB1 주파수 | HSI 64 MHz, PLL 없음, 모든 분주 1 → **64 MHz** | 이 저장소 `main.c` 의 `SystemClock_Config` |
| TIMINGR | `I2C_GetTiming(64000000, 100000)` = **`0x60702729`** | ST 자체 유틸리티 `i2c_timing_utility.c` (Cube FW_H7 V1.13.0 의 `Projects/NUCLEO-H723ZG/Examples/I2C/I2C_TwoBoards_ComPolling`) 를 호스트에서 컴파일해 계산 |

🔴 **`SystemClock_Config` 를 바꾸면 이 값이 틀어진다.** 클럭을 손대면 같은
유틸리티로 다시 계산한다 (400 kHz 는 `0x30D00A13` 이었다).

🔴 값을 손으로 지어내지 않았다. ST 알고리즘이 아날로그 필터 지연·SDADEL·
SCLDEL 제약까지 함께 푸는데, 그것을 눈대중으로 맞추면 파형이 규격을 벗어나도
통신은 되는 상태가 나온다 — 나중에 다른 센서에서만 실패한다.

**Files:**
- Create: `firmware/stage1/bsp/mk_i2c_io.c` · `firmware/stage1/bsp/mk_i2c_io.h`
- Modify: `firmware/stage1/main.c`
- Modify: `firmware/stage1/Makefile` (`$(HAL)/Src/stm32h7xx_hal_i2c.c` 추가 — 지금 없다)
- Test: `host/tests/test_firmware_safety.py`

**Interfaces:**
- Consumes: `MkI2cIo` · `MkI2c` · `mk_i2c_init` · `mk_i2c_tick` (Task 4), `mk_telem_attach_i2c` (Task 6)
- Produces: `void mk_i2c_io_init(void);`, `int mk_i2c_io_xfer(void *ctx, uint8_t bus, uint8_t addr, const uint8_t *tx, size_t ntx, uint8_t *rx, size_t nrx);`

- [ ] **Step 1: 안전 검사부터 쓴다 (실패하는 시험)**

`host/tests/test_firmware_safety.py`에 추가한다.

```python
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
```

- [ ] **Step 2: 시험이 깨지는 것을 본다**

```
python -m pytest host/tests/test_firmware_safety.py -q -k i2c
```

기대: `AssertionError: mk_i2c_io.c 이 없다`.

- [ ] **Step 3: BSP를 쓴다**

`firmware/stage1/bsp/mk_i2c_io.h`:

```c
/* I2C1 · I2C3 · I2C5 — 이 핀들을 만지는 유일한 파일.
 *
 *      I2C3  SCL=PA8  SDA=PC9    J10 · J11
 *      I2C5  SCL=PC11 SDA=PC10   J12 · J13
 *      I2C1  SCL=PB8  SDA=PB9    J14 · J15
 *
 * 🔴 PA8 은 GPIOA 다. 같은 포트에 sol(PA4~PA6)과 WS2812(PA7)가 있다 —
 *    핀별로만 초기화한다. OR 로 묶어 넣으면 밸브가 열리거나 체인이 죽는다.
 *
 * 🔴 풀업은 온보드 4.7 kΩ 이다(R25~R30). 내부 풀업을 켜지 않는다.
 */
#ifndef MK_I2C_IO_H
#define MK_I2C_IO_H

#include "../app/mk_i2c.h"

void mk_i2c_io_init(void);

/* mk_i2c 에 넘길 콜백. 0 / -1(응답 없음) / -2(버스 오류) */
int mk_i2c_io_xfer(void *ctx, uint8_t bus, uint8_t addr,
                   const uint8_t *tx, size_t ntx, uint8_t *rx, size_t nrx);

#endif /* MK_I2C_IO_H */
```

`firmware/stage1/bsp/mk_i2c_io.c` — 뼈대는 아래와 같고, `AF`·`TIMINGR`은 **선행 조건에서 확인한 값**으로 채운다. 확인 전에는 이 파일을 커밋하지 않는다.

```c
#include "mk_i2c_io.h"

#include "stm32h7xx_hal.h"

/* 🔴 전송 타임아웃. 슬레이브가 클럭을 늘려 잡는 경우까지만 기다린다 —
 *    길게 잡으면 슈퍼루프가 그만큼 선다. 100 kHz 에서 3바이트가 약
 *    0.4 ms 이므로 5 ms 면 12배 여유다. */
#define XFER_TIMEOUT_MS   5u

static I2C_HandleTypeDef s_i2c1, s_i2c3, s_i2c5;

static I2C_HandleTypeDef *handle_of(uint8_t bus)
{
    switch (bus) {
    case 1u: return &s_i2c1;
    case 3u: return &s_i2c3;
    case 5u: return &s_i2c5;
    default: return NULL;
    }
}

/* 🔴 HAL 의 **블로킹 판**만 쓴다. 이 층은 동기 계약이다 — IT/DMA 판을
 *    쓰면 완료를 기다릴 곳이 없다. 대신 타임아웃을 짧게 잡는다. */
static int map_rc(I2C_HandleTypeDef *h, HAL_StatusTypeDef rc)
{
    if (rc == HAL_OK) {
        return 0;
    }
    /* 🔴 주소 NACK 은 "센서가 없다" 이지 고장이 아니다. 규격 §7.5 의
     *    status=1 로 가야 하고, 버스 고장(status=2)과 갈라야 한다. */
    if ((HAL_I2C_GetError(h) & HAL_I2C_ERROR_AF) != 0u) {
        return -1;
    }
    return -2;
}

int mk_i2c_io_xfer(void *ctx, uint8_t bus, uint8_t addr,
                   const uint8_t *tx, size_t ntx, uint8_t *rx, size_t nrx)
{
    (void)ctx;
    I2C_HandleTypeDef *h = handle_of(bus);
    if (h == NULL) {
        return -2;
    }

    /* HAL 은 8비트 주소를 받는다 — 7비트를 왼쪽으로 한 칸 민다. */
    uint16_t a8 = (uint16_t)((uint16_t)addr << 1);
    HAL_StatusTypeDef rc;

    if (ntx > 0u && nrx > 0u) {
        /* 명령을 쓰고 STOP 없이 읽는다 (repeated start). MLX90614 형태이고,
         * HAL 에서는 Mem_Read 가 그 파형을 낸다 — 명령 1바이트가 "레지스터
         * 주소" 자리에 들어간다. */
        if (ntx != 1u) {
            return -2;           /* 지금 쓰는 칩 중 2바이트 명령은 없다 */
        }
        rc = HAL_I2C_Mem_Read(h, a8, (uint16_t)tx[0], I2C_MEMADD_SIZE_8BIT,
                              rx, (uint16_t)nrx, XFER_TIMEOUT_MS);
    } else if (ntx > 0u) {
        rc = HAL_I2C_Master_Transmit(h, a8, (uint8_t *)tx, (uint16_t)ntx,
                                     XFER_TIMEOUT_MS);
    } else if (nrx > 0u) {
        /* 🔴 명령 없이 그냥 읽는다. BH1750 은 레지스터 주소가 없어서
         *    Mem_Read 를 쓰면 안 된다 — 없는 주소 바이트가 하나 더 나간다. */
        rc = HAL_I2C_Master_Receive(h, a8, rx, (uint16_t)nrx, XFER_TIMEOUT_MS);
    } else {
        return 0;                /* 할 일이 없다 */
    }

    return map_rc(h, rc);
}
```

같은 파일의 초기화. 🔴 `I2C_AF`와 `I2C_TIMINGR_100K`는 **선행 조건에서 확인한 값**으로 채운다. 확인 전에는 커밋하지 않는다 — 그 두 줄이 이 파일에서 유일하게 근거가 필요한 곳이다.

```c
/* 🔴 선행 조건. 확인 전에는 채우지 않는다.
 *   AF: STM32H723 데이터시트(DS13313) Alternate function 표 — PA8·PC9·
 *       PC11·PC10·PB8·PB9 각각. 인용을 여기 남긴다.
 *   TIMINGR: RM0468 의 I2C 타이밍 절. 커널 클럭(RCC D2CCIP2R 이 고른다)을
 *       먼저 읽고 100 kHz 로 산출한 과정을 여기 남긴다. */
/* 🔴 I2C1 의 PB8/PB9 = AF4 는 ST 예제로 확인됐다 (Cube FW_H7 V1.13.0,
 *    Projects/NUCLEO-H723ZG/Examples/I2C/.../Inc/main.h: I2Cx_SCL_PIN =
 *    GPIO_PIN_8, I2Cx_SDA_PIN = GPIO_PIN_9, I2Cx_SCL_SDA_AF =
 *    GPIO_AF4_I2C1). I2C3(PA8·PC9)·I2C5(PC11·PC10)는 아직 미확인이다. */
#define I2C1_AF              GPIO_AF4_I2C1
#define I2C3_AF              /* ← DS13313 AF 표로 확인 후 채운다 */
#define I2C5_AF              /* ← DS13313 AF 표로 확인 후 채운다 */

/* 커널 클럭 64 MHz(APB1) 에서 100 kHz. 산출 근거는 위 §7.1 표. */
#define I2C_TIMINGR_100K     0x60702729u

static void open_pins(GPIO_TypeDef *port, uint16_t pins, uint8_t af)
{
    GPIO_InitTypeDef g = {0};
    g.Pin = pins;
    /* 🔴 오픈 드레인이다. 푸시풀로 두면 두 포트가 동시에 말할 때
     *    단락된다 — I2C 는 서로 Low 로만 끌어당긴다. */
    g.Mode = GPIO_MODE_AF_OD;
    /* 🔴 내부 풀업을 켜지 않는다. 온보드 4.7 kΩ(R25~R30)이 이미 있고,
     *    그 풀업 전압은 JP1~JP3 가 고른 V_I2Cx 다. 내부 풀업을 켜면
     *    3.3V 를 5V 버스에 얹는 꼴이 된다. */
    g.Pull = GPIO_NOPULL;
    g.Speed = GPIO_SPEED_FREQ_LOW;
    g.Alternate = af;
    HAL_GPIO_Init(port, &g);
}

static void open_bus(I2C_HandleTypeDef *h, I2C_TypeDef *inst)
{
    h->Instance = inst;
    h->Init.Timing = I2C_TIMINGR_100K;
    h->Init.OwnAddress1 = 0;
    h->Init.AddressingMode = I2C_ADDRESSINGMODE_7BIT;
    h->Init.DualAddressMode = I2C_DUALADDRESS_DISABLE;
    h->Init.OwnAddress2 = 0;
    h->Init.OwnAddress2Masks = I2C_OA2_NOMASK;
    h->Init.GeneralCallMode = I2C_GENERALCALL_DISABLE;
    h->Init.NoStretchMode = I2C_NOSTRETCH_DISABLE;
    (void)HAL_I2C_Init(h);
}

void mk_i2c_io_init(void)
{
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_GPIOB_CLK_ENABLE();
    __HAL_RCC_GPIOC_CLK_ENABLE();

    /* 🔴 핀별로만 연다. GPIOA 에는 sol(PA4~PA6)과 WS2812(PA7)가 있어
     *    포트를 통째로 초기화하면 서로를 덮는다. */
    open_pins(GPIOA, GPIO_PIN_8,  I2C3_AF);              /* I2C3 SCL */
    open_pins(GPIOC, GPIO_PIN_9,  I2C3_AF);              /* I2C3 SDA */
    open_pins(GPIOC, GPIO_PIN_11 | GPIO_PIN_10, I2C5_AF); /* I2C5 SCL·SDA */
    open_pins(GPIOB, GPIO_PIN_8 | GPIO_PIN_9,   I2C1_AF); /* I2C1 SCL·SDA */

    __HAL_RCC_I2C1_CLK_ENABLE();
    __HAL_RCC_I2C3_CLK_ENABLE();
    __HAL_RCC_I2C5_CLK_ENABLE();

    open_bus(&s_i2c1, I2C1);
    open_bus(&s_i2c3, I2C3);
    open_bus(&s_i2c5, I2C5);
}
```

`main.c` 배선 — sol과 같은 자리에:

```c
#include "app/mk_i2c.h"
#include "bsp/mk_i2c_io.h"
...
static MkI2c s_i2c;
...
    mk_i2c_io_init();
    MkI2cIo i2c_io = { mk_i2c_io_xfer, NULL };
    mk_i2c_init(&s_i2c, &i2c_io);
    mk_telem_attach_i2c(&s_telem, &s_i2c);
...
        /* 🔴 I2C 도 매 바퀴 민다. 한 바퀴에 포트 하나만 나아가므로
         *    전송이 겹치지 않는다. */
        mk_i2c_tick(&s_i2c, &s_cfg, now);
```

`Makefile`의 HAL 소스 목록에 한 줄:

```
  $(HAL)/Src/stm32h7xx_hal_i2c.c \
```

- [ ] **Step 4: 시험과 빌드를 확인한다**

```
python -m pytest -q
powershell -ExecutionPolicy Bypass -File firmware/stage1/tests/run_tests.ps1
CUBE=/c/ST/STM32CubeIDE_2.1.1/STM32CubeIDE/plugins
PATH="$CUBE/com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.14.3.rel1.win32_1.0.100.202602081740/tools/bin:$CUBE/com.st.stm32cube.ide.mcu.externaltools.make.win32_2.2.100.202601091506/tools/bin:$PATH" \
  make -C firmware/stage1
```

기대: pytest 전부 통과, C 13묶음 + 대조 7종, 보드용 이미지가 **경고 없이** 링크된다. 🔴 `-Wtype-limits` 같은 경고는 ARM 빌드에서만 나온다 — MSVC가 조용해도 여기서 본다.

- [ ] **Step 5: 커밋**

```bash
git add firmware/stage1/bsp/mk_i2c_io.c firmware/stage1/bsp/mk_i2c_io.h firmware/stage1/main.c firmware/stage1/Makefile host/tests/test_firmware_safety.py
git commit -m "feat(fw): I2C 버스 셋을 연다 — 카탈로그에만 있던 포트가 실제로 열린다"
```

---

### Task 8: 실기기 확인과 문서

**보드와 BH1750 센서가 있어야 한다.** 없으면 Step 1~4를 건너뛰고 Step 5에서 **[미검증]**으로 남긴다 — sol과 같은 처리다.

- [ ] **Step 1: 굽는다**

```bash
cd firmware/stage1 && python tools/flash_verified.py
```

🔴 `flash.gdb`를 직접 쓰지 않는다. 되읽기가 흔들리므로 세 번 읽어 다수결을 내는 이 도구로만 굽는다 (HANDOFF §5.1).

- [ ] **Step 2: 값이 뜨는지 본다**

J10에 BH1750을 꽂고 (🔴 **JP1 점퍼가 3.3V인지 먼저 확인** — 5V로 두면 풀업도 5V가 된다):

```bash
python -m host.gui.app --port COM23
```

설정 탭에서 `i2c10.종류` = 조도, `i2c10.사용` = 켬, `i2c10.주소` = 0x23(35). 대시보드에 lx 카드가 뜨는지 본다.

- [ ] **Step 3: 뽑았다 꽂아 본다**

센서를 뽑으면 `status=1`이 되어 카드가 "값 없음"으로, 다시 꽂으면 값이 돌아오는지 확인한다.

- [ ] **Step 4: 다른 것을 방해하지 않는지 본다**

I2C가 도는 동안 아날로그 값이 계속 오고 `$STAT`의 `drops`가 오르지 않는지 확인한다:

```bash
python -m tools.cli.markon_cli --port COM23 monitor --seconds 20
```

- [ ] **Step 5: HANDOFF를 갱신한다**

`HANDOFF.md`에서:
- §2 표에 I2C 줄 추가 (실기기 확인했으면 ✅, 못 했으면 🟡 + 못 한 이유)
- §3의 "I2C 실동작 — 펌웨어에 소스가 하나도 없다" 줄을 지금 상태로
- §4의 모듈 목록에 `mk_i2c` · `mk_i2c_drivers` · `mk_i2c_bh1750` · `mk_i2c_io`
- §7.2를 남은 것(온습도·적외·방수 드라이버, 직접 설정)으로 다시 쓴다
- 시험 개수 갱신 (C 13묶음 + 대조 7종)

- [ ] **Step 6: 커밋**

```bash
git add HANDOFF.md
git commit -m "docs: I2C 1차 결과를 HANDOFF 에 남긴다"
```

---

## 남은 것 (이 계획 밖)

- **2차 — 직접 설정(raw) 종류**: 설계 문서 §13. 드라이버 하나로 들어가므로 이 계획의 구조는 안 바뀐다
- 온습도·적외 온도·방수 온도 드라이버: 칩 모델이 정해지면 파일 하나씩
- `$STAT`에 I2C 포트 상태 싣기
- 버스 잠김 복구(SCL 토글): 실제로 겪은 뒤에 넣는다 — 안 겪은 고장에 코드를 쓰지 않는다
