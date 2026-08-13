# 펌웨어 1단계 구현 계획 — `$ID` + `$HB`

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** H723 펌웨어가 `protocol/specification.md` v3 대로 `$ID` 와 `$HB` 를 주고받는다. 이미 있는 `markon_cli --port COM23` 으로 실기기 검증한다.

**Architecture:** CubeMX 없이 HAL 라이브러리만 쓰는 Makefile 프로젝트. `h723_sensor_read` 의 빌드 구조를 뼈대로 쓰되 애플리케이션 코드는 새로 짠다. 프로토콜 계층(`framing`)은 HAL 에 의존하지 않아 **호스트에서 단위 시험**한다.

**Tech Stack:** arm-none-eabi-gcc 14.3 (CubeIDE 동봉), STM32Cube_FW_H7_V1.13.0, GNU make (CubeIDE 동봉). 전부 설치 확인됨.

**Spec:** `docs/superpowers/specs/2026-08-13-config-control-gui-design.md`
**Contract:** `protocol/specification.md` (v3)

## Global Constraints

- **전원 레일을 켜지 않는다.** `$ID`/`$HB` 에 24V 가 필요 없고, 센서 미연결에 J30/J1 상태를 모른다. `MX_Rails_Init()` 을 이식하지 않는다.
- **UART3 = PB10(TX) / PB11(RX), 115200 8N1.** 넷리스트 확인: `VCP_TX` = H723 PB10 ↔ F103 PA3, `VCP_RX` = H723 PB11 ↔ F103 PA2. 실기기에서 COM23 으로 통신 확인됨.
- 체크섬은 **NMEA XOR, `$` 와 `*` 사이 바이트**, 대문자 2자리. 검증 벡터 `$HB*0A`, `$ID*0D`.
- **줄 조립 시 제어문자를 넣지 않는다.** 한 번 만들면 정확히 한 줄이어야 한다.
- **체크섬 검증을 통과한 뒤에만** `$HB` 수신 시각을 갱신한다. Q2 `host_link.c:183-187` 은 반대로 돼 있다 — 이식 금지.
- `$HB` 에는 어느 방향이든 `$SACK` 를 보내지 않는다.
- 조회(`$ID`)는 RUN 에서도 응답한다.
- 응답 본문은 NDJSON, `schema_ver` **3**. 명령 응답 레코드의 `seq` 는 항상 0.
- **HAL 비의존 코드와 HAL 의존 코드를 파일로 분리한다.** 전자는 호스트에서 시험한다.

## 왜 이 범위인가

가장 작으면서 **전 구간을 한 번에 증명**하는 조합이다. 이게 되면 빌드·굽기·UART
경로·C 와 Python 의 체크섬 일치·CLI 의 실물 경로가 동시에 확인된다. 계약이
실기기에서 맞는지는 여기서 판가름 난다.

반대로 여기서 어긋나면 그 위에 쌓는 모든 것이 어긋난다.

## 파일 구조

```
firmware/stage1/
├─ Makefile                 h723_sensor_read 것을 뼈대로
├─ STM32H723ZGTX_FLASH.ld   그대로 가져옴
├─ bsp/
│  ├─ stm32h7xx_hal_conf.h  그대로 가져옴
│  └─ stm32h7xx_it.c        UART 인터럽트만 추가
├─ app/
│  ├─ mk_framing.c/h        🔴 HAL 비의존 — 체크섬·줄 조립·파싱
│  ├─ mk_hostlink.c/h       🔴 HAL 비의존 — 명령 디스패치·모드
│  └─ mk_json.c/h           🔴 HAL 비의존 — NDJSON 조립 (newlib-nano 안전)
├─ main.c                   HAL 초기화, UART, 슈퍼루프
└─ tests/                   호스트 네이티브 gcc 로 app/ 만 시험
```

`app/` 은 `stm32h7xx_hal.h` 를 include 하지 않는다. 그래서 PC 에서 그대로
컴파일해 시험할 수 있고, Python 쪽 `framing.py` 와 **같은 벡터로 대조**할 수 있다.

---

## Task 1: 프레이밍 — 호스트에서 시험되는 C

**Files:**
- Create: `firmware/stage1/app/mk_framing.h`, `firmware/stage1/app/mk_framing.c`
- Create: `firmware/stage1/tests/test_framing.c`, `firmware/stage1/tests/Makefile`
- Create: `firmware/stage1/tests/run_tests.ps1`, `firmware/stage1/tests/crosscheck.py`

**Interfaces:**
- Produces:
  - `uint8_t mk_xor_checksum(const char *payload, size_t len)`
  - `int mk_build_line(char *out, size_t cap, const char *payload)` — 음수면 실패
  - `typedef struct { char verb[13]; char args[4][24]; int argc; } MkCommand;`
  - `MkParseResult mk_parse_line(const char *line, size_t len, MkCommand *out)` — enum: `MK_OK`, `MK_ERR_MALFORMED`, `MK_ERR_CHECKSUM`

**`app/mk_framing.{h,c}` 는 HAL 을 include 하지 않는다.** `<stdint.h>`, `<stddef.h>`,
`<string.h>` 만 쓴다. 시험 파일(`tests/test_framing.c`)은 이 제약 밖이라
`<stdio.h>`, `<assert.h>` 를 써도 된다 — 보드에 올라가지 않기 때문이다.

**빌드 도구:** 이 개발 호스트에는 `gcc` 도 `clang` 도 없다(2026-08-13 확인).
`arm-none-eabi-gcc` 는 크로스 전용이라 호스트 시험에 못 쓴다. 실제로 도는 것은
MSVC(`cl.exe`) 뿐이므로 `run_tests.ps1` 이 정식 실행 경로다. `Makefile` 은
gcc 가 있는 환경(CI·리눅스)을 위해 함께 둔다.

- [ ] **Step 1: 실패하는 테스트 작성**

`firmware/stage1/tests/test_framing.c`:
```c
/* 호스트 네이티브 gcc 로 돌린다. 보드도 크로스 툴체인도 필요 없다.
 *
 * 🔴 검증 벡터가 Python 쪽 host/tests/test_framing.py 와 같아야 한다.
 *    두 구현이 같은 답을 내는지가 이 시험의 존재 이유다. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "../app/mk_framing.h"

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
    if (!(cond)) { printf("  FAIL %s\n", msg); failures++; }    \
    else         { printf("  ok   %s\n", msg); }                \
} while (0)

static void test_checksum_vectors(void)
{
    /* 규격 §3 — Python 쪽과 동일한 벡터 */
    CHECK(mk_xor_checksum("HB", 2) == 0x0A, "xor(\"HB\") == 0x0A");
    CHECK(mk_xor_checksum("ID", 2) == 0x0D, "xor(\"ID\") == 0x0D");
}

static void test_checksum_is_byte_based(void)
{
    /* 문자가 아니라 바이트다. ASCII 만 오므로 결과는 같지만
     * 정의를 맞춰 두어야 나중에 어긋나지 않는다. */
    const char *p = "CFG,SET,ain0.unit,degC";
    uint8_t expect = 0;
    for (const char *c = p; *c; c++) expect ^= (uint8_t)*c;
    CHECK(mk_xor_checksum(p, strlen(p)) == expect, "byte-basis XOR");
}

static void test_build_line(void)
{
    char buf[64];
    int n = mk_build_line(buf, sizeof buf, "HB");
    CHECK(n == 8, "build_line(\"HB\") 길이 8");
    CHECK(strcmp(buf, "$HB*0A\r\n") == 0, "build_line(\"HB\") 내용");
}

static void test_build_line_rejects_control_chars(void)
{
    /* 🔴 한 번 만들면 정확히 한 줄이어야 한다.
     * 제어문자가 섞이면 전송 중 여러 줄로 쪼개지고, 그 조각이
     * 완결된 명령이 될 수 있다. */
    char buf[64];
    CHECK(mk_build_line(buf, sizeof buf, "A\r\nB") < 0, "CR/LF 거부");
    CHECK(mk_build_line(buf, sizeof buf, "A\tB")  < 0, "TAB 거부");
    /* "\x7fB" 로 쓰면 컴파일러가 \x7fB 를 한 이스케이프로 읽어 범위를
     * 넘는다. 16진 이스케이프는 자릿수 제한이 없다 — 끊어 써야 한다. */
    CHECK(mk_build_line(buf, sizeof buf, "A\x7f" "B") < 0, "DEL 거부");
}

static void test_build_line_rejects_overflow(void)
{
    char small[8];
    CHECK(mk_build_line(small, sizeof small, "LONGPAYLOAD") < 0, "버퍼 초과 거부");
}

static void test_parse_ok(void)
{
    MkCommand c;
    CHECK(mk_parse_line("$HB*0A\r\n", 8, &c) == MK_OK, "parse $HB*0A");
    CHECK(strcmp(c.verb, "HB") == 0 && c.argc == 0, "verb=HB argc=0");
}

static void test_parse_args(void)
{
    MkCommand c;
    const char *s = "$CFG,GET,tx.period_ms*72\r\n";
    CHECK(mk_parse_line(s, strlen(s), &c) == MK_OK, "parse $CFG,GET,...");
    CHECK(strcmp(c.verb, "CFG") == 0, "verb=CFG");
    CHECK(c.argc == 2, "argc=2");
    CHECK(strcmp(c.args[0], "GET") == 0, "args[0]=GET");
    CHECK(strcmp(c.args[1], "tx.period_ms") == 0, "args[1]=tx.period_ms");
}

static void test_parse_accepts_bare_lf(void)
{
    MkCommand c;
    CHECK(mk_parse_line("$HB*0A\n", 7, &c) == MK_OK, "LF 만 와도 받는다");
}

static void test_parse_accepts_lowercase_checksum(void)
{
    MkCommand c;
    CHECK(mk_parse_line("$HB*0a\r\n", 8, &c) == MK_OK, "소문자 체크섬 수용");
}

static void test_parse_rejects(void)
{
    MkCommand c;
    CHECK(mk_parse_line("$HB*FF\r\n", 8, &c) == MK_ERR_CHECKSUM, "체크섬 불일치");
    CHECK(mk_parse_line("HB*0A\r\n", 7, &c) == MK_ERR_MALFORMED, "$ 없음");
    CHECK(mk_parse_line("$HB0A\r\n", 7, &c) == MK_ERR_MALFORMED, "* 없음");
    CHECK(mk_parse_line("$*00\r\n", 6, &c) == MK_ERR_MALFORMED, "빈 payload");
}

/* payload 로 온전한 줄을 만든다. 체크섬을 손으로 적으면 길이 검사에
 * 닿기도 전에 CHECKSUM 으로 걸려 시험이 헛돈다. */
static size_t make_line(char *out, const char *payload)
{
    uint8_t cs = mk_xor_checksum(payload, strlen(payload));
    int n = snprintf(out, 128, "$%s*%02X\r\n", payload, cs);
    return (size_t)n;
}

static void test_parse_rejects_oversized_verb(void)
{
    /* 고정폭 버퍼다. 넘치는 입력을 잘라 담지 말고 거부한다. */
    char payload[100], line[128];
    MkCommand c;
    memset(payload, 'A', sizeof payload - 1);
    payload[sizeof payload - 1] = '\0';      /* 99자 > MK_VERB_MAX(12) */
    size_t n = make_line(line, payload);
    CHECK(mk_parse_line(line, n, &c) == MK_ERR_MALFORMED, "긴 verb 거부");
}

static void test_parse_rejects_oversized_arg(void)
{
    char payload[64], line[128];
    MkCommand c;
    memset(payload, 'B', sizeof payload - 1);
    payload[sizeof payload - 1] = '\0';
    memcpy(payload, "CFG,", 4);              /* 59자 arg > MK_ARG_MAX(23) */
    size_t n = make_line(line, payload);
    CHECK(mk_parse_line(line, n, &c) == MK_ERR_MALFORMED, "긴 arg 거부");
}

static void test_parse_rejects_too_many_args(void)
{
    char line[128];
    MkCommand c;
    /* verb + 5개 = MK_ARGS_MAX(4) 초과 */
    size_t n = make_line(line, "CFG,a,b,c,d,e");
    CHECK(mk_parse_line(line, n, &c) == MK_ERR_MALFORMED, "인자 개수 초과 거부");
}

/* 🔴 Python 과 기계로 대조하기 위한 출력. 눈으로 두 표를 맞춰보면
 *    조용히 어긋난다. crosscheck.py 가 이 출력을 그대로 비교한다. */
static const char *const VECTORS[] = {
    "HB", "ID", "CFG,GET,tx.period_ms", "CFG,SET,ain0.unit,degC",
    "ACK,CFG,OK", "CFG,LIST", "RUN", "SACK,CFG,RANGE",
};

static void print_vectors(void)
{
    char buf[256];
    for (size_t i = 0; i < sizeof VECTORS / sizeof *VECTORS; i++) {
        const char *p = VECTORS[i];
        int n = mk_build_line(buf, sizeof buf, p);
        printf("%s\t%02X\t%d\t", p, mk_xor_checksum(p, strlen(p)), n);
        for (char *q = buf; *q; q++) {
            if      (*q == '\r') printf("\\r");
            else if (*q == '\n') printf("\\n");
            else                 putchar(*q);
        }
        putchar('\n');
    }
}

int main(int argc, char **argv)
{
    if (argc > 1 && strcmp(argv[1], "--vectors") == 0) {
        print_vectors();
        return 0;
    }
    printf("mk_framing\n");
    test_checksum_vectors();
    test_checksum_is_byte_based();
    test_build_line();
    test_build_line_rejects_control_chars();
    test_build_line_rejects_overflow();
    test_parse_ok();
    test_parse_args();
    test_parse_accepts_bare_lf();
    test_parse_accepts_lowercase_checksum();
    test_parse_rejects();
    test_parse_rejects_oversized_verb();
    test_parse_rejects_oversized_arg();
    test_parse_rejects_too_many_args();
    printf(failures ? "\nFAILED (%d)\n" : "\nPASSED\n", failures);
    return failures ? 1 : 0;
}
```

`firmware/stage1/tests/Makefile`:
```make
# 호스트 네이티브 빌드. 크로스 툴체인도 보드도 필요 없다.
CC ?= gcc
CFLAGS = -std=c11 -Wall -Wextra -Werror -O1 -g

all: test_framing
	./test_framing

test_framing: test_framing.c ../app/mk_framing.c
	$(CC) $(CFLAGS) -o $@ $^

clean:
	rm -f test_framing test_framing.exe
.PHONY: all clean
```

`firmware/stage1/tests/run_tests.ps1`:
```powershell
# 🔴 이 개발 호스트에는 gcc 도 clang 도 없다 (2026-08-13 확인). arm-none-eabi-gcc
#    는 크로스 전용이라 호스트 시험에 못 쓴다. 있는 것은 MSVC 뿐이다.
#    Makefile 은 gcc 가 있는 환경(CI·리눅스)을 위해 남겨 둔다.
#
#    /utf-8 이 필수다. 이 파일들은 UTF-8 인데 MSVC 는 기본으로 CP949 로 읽어,
#    한글 바이트열 안의 0x5C 를 백슬래시로 오인하고 문자열을 깨뜨린다.
$ErrorActionPreference = "Stop"
$vcvars = "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
if (-not (Test-Path $vcvars)) { throw "vcvars64.bat 없음: $vcvars" }
Set-Location $PSScriptRoot
$cmd = "chcp 65001 >nul && `"$vcvars`" >nul 2>&1 && " +
       "cl /nologo /W4 /WX /utf-8 /std:c11 /Fe:test_framing.exe " +
       "test_framing.c ..\app\mk_framing.c >nul && .\test_framing.exe"
cmd /c $cmd
exit $LASTEXITCODE
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `powershell -File firmware/stage1/tests/run_tests.ps1`
(gcc 가 있는 환경이면 `cd firmware/stage1/tests && make` 도 같은 결과)
Expected: FAIL — `mk_framing.h: 그런 파일이 없습니다`

- [ ] **Step 3: `mk_framing.h` 작성**

`firmware/stage1/app/mk_framing.h`:
```c
/* MarkON 시리얼 프레이밍 — HAL 비의존.
 *
 * 🔴 이 파일은 stm32h7xx_hal.h 를 include 하지 않는다.
 *    그래서 호스트 gcc 로 그대로 컴파일해 시험할 수 있고, Python 쪽
 *    host/core/framing.py 와 같은 벡터로 대조할 수 있다. 두 구현이
 *    같은 답을 내는지가 이 계층의 전부다.
 *
 * 규격: protocol/specification.md §3
 */
#ifndef MK_FRAMING_H
#define MK_FRAMING_H

#include <stddef.h>
#include <stdint.h>

/* 고정폭 버퍼. 넘치는 입력은 잘라 담지 않고 거부한다. */
#define MK_VERB_MAX   12
#define MK_ARG_MAX    23
#define MK_ARGS_MAX   4
#define MK_LINE_MAX   192

typedef enum {
    MK_OK = 0,
    MK_ERR_MALFORMED,
    MK_ERR_CHECKSUM
} MkParseResult;

typedef struct {
    char verb[MK_VERB_MAX + 1];
    char args[MK_ARGS_MAX][MK_ARG_MAX + 1];
    int  argc;
} MkCommand;

/* payload 바이트들의 XOR. '$' 와 '*' 는 포함하지 않는다. */
uint8_t mk_xor_checksum(const char *payload, size_t len);

/* "$<payload>*<CS>\r\n" 을 만든다.
 *
 * 반환: 쓴 바이트 수, 실패면 음수.
 * 🔴 payload 에 제어문자가 있으면 거부한다 — 한 번 만들면 정확히 한 줄이어야
 *    한다. 제어문자가 섞이면 전송 중 여러 줄로 쪼개지고, 그 조각 하나가
 *    완결된 명령이 될 수 있다. */
int mk_build_line(char *out, size_t cap, const char *payload);

/* 완성된 줄을 파싱한다. 줄끝은 \r\n 또는 \n 둘 다 받는다.
 * 체크섬은 대소문자를 가리지 않는다(수신은 관대하게, 송신은 대문자로). */
MkParseResult mk_parse_line(const char *line, size_t len, MkCommand *out);

#endif /* MK_FRAMING_H */
```

- [ ] **Step 4: `mk_framing.c` 작성**

`firmware/stage1/app/mk_framing.c`:
```c
#include "mk_framing.h"

#include <string.h>

static int is_control(char c)
{
    unsigned char u = (unsigned char)c;
    return u < 0x20u || u == 0x7Fu;
}

static int hex_digit(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

uint8_t mk_xor_checksum(const char *payload, size_t len)
{
    uint8_t cs = 0u;
    for (size_t i = 0; i < len; i++) {
        cs ^= (uint8_t)payload[i];
    }
    return cs;
}

int mk_build_line(char *out, size_t cap, const char *payload)
{
    static const char HEX[] = "0123456789ABCDEF";
    size_t plen = strlen(payload);

    if (plen == 0u) {
        return -1;                       /* verb 없는 줄은 의미가 없다 */
    }
    for (size_t i = 0; i < plen; i++) {
        if (is_control(payload[i])) {
            return -1;                   /* 줄이 쪼개진다 */
        }
    }
    /* '$' + payload + '*' + 2 + "\r\n" + NUL */
    if (cap < plen + 6u) {
        return -1;
    }

    uint8_t cs = mk_xor_checksum(payload, plen);
    size_t n = 0;
    out[n++] = '$';
    memcpy(out + n, payload, plen);
    n += plen;
    out[n++] = '*';
    out[n++] = HEX[(cs >> 4) & 0x0Fu];
    out[n++] = HEX[cs & 0x0Fu];
    out[n++] = '\r';
    out[n++] = '\n';
    out[n]   = '\0';
    return (int)n;
}

MkParseResult mk_parse_line(const char *line, size_t len, MkCommand *out)
{
    /* 양끝 공백·줄끝을 떼어낸다. */
    while (len > 0u && (line[len - 1u] == '\r' || line[len - 1u] == '\n' ||
                        line[len - 1u] == ' '  || line[len - 1u] == '\t')) {
        len--;
    }
    if (len < 4u || line[0] != '$') {    /* 최소 "$X*CS" 도 안 된다 */
        return MK_ERR_MALFORMED;
    }

    /* 마지막 '*' 를 찾는다. payload 안에 '*' 가 있어도 체크섬은 항상 끝이다. */
    size_t star = 0u;
    int found = 0;
    for (size_t i = len; i > 1u; i--) {
        if (line[i - 1u] == '*') { star = i - 1u; found = 1; break; }
    }
    if (!found || star + 3u != len) {    /* '*' 뒤에 정확히 2자리 */
        return MK_ERR_MALFORMED;
    }

    size_t plen = star - 1u;             /* '$' 제외 */
    if (plen == 0u || plen > MK_LINE_MAX) {
        return MK_ERR_MALFORMED;
    }

    int hi = hex_digit(line[star + 1u]);
    int lo = hex_digit(line[star + 2u]);
    if (hi < 0 || lo < 0) {
        return MK_ERR_MALFORMED;
    }
    uint8_t given = (uint8_t)((hi << 4) | lo);
    if (given != mk_xor_checksum(line + 1, plen)) {
        return MK_ERR_CHECKSUM;
    }

    /* payload 를 쉼표로 쪼갠다. 넘치면 잘라 담지 말고 거부한다. */
    memset(out, 0, sizeof *out);
    size_t i = 1u;                       /* '$' 다음 */
    size_t end = star;
    size_t field = 0u;
    char *dst = out->verb;
    size_t dst_cap = MK_VERB_MAX;
    size_t w = 0u;

    for (; i < end; i++) {
        if (line[i] == ',') {
            dst[w] = '\0';
            if (field >= MK_ARGS_MAX) {
                return MK_ERR_MALFORMED;
            }
            dst = out->args[field];
            dst_cap = MK_ARG_MAX;
            w = 0u;
            field++;
            continue;
        }
        if (w >= dst_cap) {
            return MK_ERR_MALFORMED;     /* 고정폭 초과 */
        }
        dst[w++] = line[i];
    }
    dst[w] = '\0';

    if (out->verb[0] == '\0') {
        return MK_ERR_MALFORMED;
    }
    out->argc = (int)field;
    return MK_OK;
}
```

- [ ] **Step 5: 테스트 실행해 통과 확인**

Run: `powershell -File firmware/stage1/tests/run_tests.ps1`
Expected: `PASSED`, 실패 0 (전 25건)

- [ ] **Step 6: Python 구현과 교차 검증**

같은 입력에 두 구현이 같은 답을 내는지 확인한다. **이 계층의 존재 이유다.**

`firmware/stage1/tests/crosscheck.py`:
```python
"""C 프레이밍과 Python 프레이밍이 같은 답을 내는지 기계로 대조한다.

🔴 이 대조가 stage 1 프레이밍 계층의 존재 이유다. 보드와 호스트가 체크섬을
   다르게 계산하면 모든 명령이 조용히 거부되고, 원인은 프로토콜 어디에도
   드러나지 않는다.

C 시험 바이너리를 `--vectors` 로 돌려 그 출력을 Python 계산과 비교한다.
사람이 두 표를 눈으로 맞춰보는 방식은 조용히 실패한다.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))

from host.core.framing import build_line, xor_checksum  # noqa: E402


def _find_binary() -> Path:
    for name in ("test_framing.exe", "test_framing"):
        p = HERE / name
        if p.exists():
            return p
    raise SystemExit("시험 바이너리가 없다. 먼저 run_tests.ps1 (또는 make) 을 돌려라.")


def main() -> int:
    binary = _find_binary()
    out = subprocess.run(
        [str(binary), "--vectors"], capture_output=True, text=True, check=True
    ).stdout

    rows = [ln for ln in out.splitlines() if ln.strip()]
    if not rows:
        raise SystemExit("--vectors 출력이 비었다.")

    mismatches = []
    for row in rows:
        payload, c_cs, c_len, c_line = row.split("\t")
        py_cs = f"{xor_checksum(payload):02X}"
        py_raw = build_line(payload)
        py_line = py_raw.replace("\r", "\\r").replace("\n", "\\n")
        py_len = str(len(py_raw))
        if (c_cs, c_len, c_line) != (py_cs, py_len, py_line):
            mismatches.append(
                f"  {payload}\n"
                f"    C : cs={c_cs} len={c_len} {c_line}\n"
                f"    py: cs={py_cs} len={py_len} {py_line}"
            )
        else:
            print(f"  ok   {payload:24} cs={c_cs} len={c_len}")

    if mismatches:
        print("\n두 구현이 어긋난다:", file=sys.stderr)
        print("\n".join(mismatches), file=sys.stderr)
        return 1
    print(f"\nMATCH ({len(rows)} vectors)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Run: `python firmware/stage1/tests/crosscheck.py`
Expected: `MATCH (8 vectors)`, 종료 코드 0

- [ ] **Step 7: 커밋**

```bash
git add firmware/stage1/app/mk_framing.h firmware/stage1/app/mk_framing.c \
        firmware/stage1/tests/test_framing.c firmware/stage1/tests/Makefile \
        firmware/stage1/tests/run_tests.ps1 firmware/stage1/tests/crosscheck.py
git commit -m "feat(fw): 프레이밍 계층 — 호스트에서 시험되는 C

HAL 을 include 하지 않아 호스트 gcc 로 그대로 컴파일해 시험한다.
Python 쪽 host/core/framing.py 와 같은 벡터(\$HB*0A, \$ID*0D)로 대조하는
것이 이 계층의 존재 이유다 — 두 구현이 같은 답을 내야 한다.

제어문자가 든 payload 로는 줄을 만들지 않는다. 한 번 만들면 정확히 한
줄이어야 하는데, 제어문자가 섞이면 전송 중 쪼개지고 그 조각이 완결된
명령이 될 수 있다.

고정폭 버퍼를 넘치는 입력은 잘라 담지 않고 거부한다. C 에서 잘라 담으면
호스트가 검증한 값과 보드가 저장한 값이 달라진다."
```

---

## Task 2~5 (개요)

Task 1 검토 후 상세화한다. 프레이밍이 굳어야 나머지 인터페이스가 정해진다.

| Task | 내용 | 핵심 |
|---|---|---|
| 2 | `mk_json.c` — NDJSON 조립 | newlib-nano 는 `%f`·`%lld` 를 지원하지 않는다. 자체 정수·실수 출력이 필요하다 |
| 3 | `mk_hostlink.c` — 명령 디스패치·모드 | 체크섬 통과 후에만 HB 갱신. `$HB` 에 SACK 없음 |
| 4 | `main.c` + Makefile — HAL 초기화·UART3·슈퍼루프 | **레일을 켜지 않는다** |
| 5 | 실기기 검증 | 굽고 `markon_cli --port COM23` 으로 확인. 실패 시 복구 지점으로 되돌린다 |

## Task 1 사전 실행 기록 (2026-08-13)

구현자에게 넘기기 전에 이 계획서의 C 코드를 그대로 추출해 컴파일하고 돌려봤다.
아래 4건은 그 과정에서 드러나 이미 계획서에 반영한 것들이다. 구현자가 다시
만날 함정이 아니라, **왜 지금 형태인지**의 근거다.

1. **`"A\x7fB"` 는 컴파일되지 않는다.** C 의 16진 이스케이프는 자릿수 제한이
   없어 컴파일러가 `\x7fB` 를 한 이스케이프로 읽고 범위 초과로 거부한다.
   `"A\x7f" "B"` 로 끊어 쓴다.

2. **긴 verb 거부 시험이 헛돌았다.** 체크섬을 `*00` 으로 손으로 적었는데,
   파서는 길이 검사보다 체크섬을 먼저 본다. `MK_ERR_CHECKSUM` 이 먼저
   반환되어 verb 길이 검사에는 닿지도 못했다 — 구현이 길이를 아예 검사하지
   않아도 통과했을 시험이다. `make_line()` 이 올바른 체크섬을 계산해 넣는다.

3. **같은 고정폭 경로의 나머지 경계가 비어 있었다.** 긴 arg(`MK_ARG_MAX`
   초과)와 인자 개수 초과(`MK_ARGS_MAX` 초과) 시험을 더했다. 되돌림 검사로
   확인했다 — 두 가드를 무력화하면 세 시험이 모두 실패한다.

4. **교차 검증을 사람 눈에 맡기고 있었다.** 두 표를 나란히 찍어놓고 맞춰
   보라는 방식은 조용히 실패한다. 시험 바이너리에 `--vectors` 모드를 넣고
   `crosscheck.py` 가 기계로 비교한다. C 쪽 체크섬 시드를 1 로 바꿔 실제로
   어긋남을 잡는지 확인했다.

**빌드 도구도 바로잡았다.** 이 호스트에는 `gcc` 도 `clang` 도 없어서 원래
계획서의 `make` 는 실행 불가능했다. `run_tests.ps1`(MSVC)이 정식 경로다.
MSVC 에는 `/utf-8` 이 필수다 — 이 파일들은 UTF-8 인데 MSVC 는 기본으로
CP949 로 읽어 한글 바이트열 안의 `0x5C` 를 백슬래시로 오인하고 문자열
상수를 깨뜨린다.

검증 결과: `/W4 /WX` 로 경고 없이 컴파일, 시험 25건 전부 통과, 벡터 8개가
`host/core/framing.py` 와 바이트 단위로 일치.

## 완료 확인

- [ ] `powershell -File firmware/stage1/tests/run_tests.ps1` 이 호스트에서
      통과 (보드 불필요, 25건)
- [ ] `python firmware/stage1/tests/crosscheck.py` 가 `MATCH (8 vectors)`
- [ ] `make` 로 `.bin` 이 나온다
- [ ] 보드에 굽고 `$ID` 에 응답한다
- [ ] `$HB` 를 1 Hz 로 내보낸다
- [ ] 체크섬이 깨진 `$HB` 는 모드를 바꾸지 않는다
- [ ] **전원 레일이 올라가지 않는다** (LED2/3/4 소등 유지)
