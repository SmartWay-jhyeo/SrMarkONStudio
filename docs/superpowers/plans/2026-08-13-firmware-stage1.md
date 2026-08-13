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

## Task 2: NDJSON 조립 — libc 의 printf 를 쓰지 않는다

**Files:**
- Create: `firmware/stage1/app/mk_json.h`, `firmware/stage1/app/mk_json.c`
- Create: `firmware/stage1/tests/test_json.c`, `firmware/stage1/tests/crosscheck_json.py`
- Modify: `firmware/stage1/tests/run_tests.ps1` — 시험 두 벌을 돌린다
- Modify: `firmware/stage1/tests/Makefile` — 같은 이유

**Interfaces:**
- Consumes: 없음. `mk_framing` 과 독립이며, 둘은 Task 3 의 `mk_hostlink` 에서 만난다.
- Produces:
  - `typedef struct { char *buf; size_t cap; size_t len; int ok; int nfield; } MkJson;`
  - `void mk_json_begin(MkJson *j, char *buf, size_t cap)`
  - `void mk_json_str(MkJson *j, const char *key, const char *val)`
  - `void mk_json_i64(MkJson *j, const char *key, int64_t val)`
  - `void mk_json_u64(MkJson *j, const char *key, uint64_t val)`
  - `void mk_json_i32(MkJson *j, const char *key, int32_t val)`
  - `void mk_json_u32(MkJson *j, const char *key, uint32_t val)`
  - `void mk_json_bool(MkJson *j, const char *key, int val)`
  - `void mk_json_f32(MkJson *j, const char *key, float val, int digits)`
  - `int  mk_json_end(MkJson *j)` — 길이, 넘쳤으면 음수

**`app/mk_json.{h,c}` 는 HAL 도 `stdio.h` 도 include 하지 않는다.**
`<stddef.h>`, `<stdint.h>`, `<string.h>` 만 쓴다. 시험 파일은 이 제약 밖이다.

### 왜 정수·실수 출력을 직접 짜는가

계획서 초안은 "newlib-nano 는 `%f`·`%lld` 를 지원하지 않는다"고 단정했다.
근거 없는 단정이라 실제로 링크해 재봤다
(`docs/measurements/2026-08-13_newlib_nano_printf.md`).

| 빌드 | text | data | bss |
|---|---:|---:|---:|
| `nano.specs` 만 | 5,068 | 104 | 496 |
| `nano.specs` + `-u _printf_float` | 15,524 | 468 | 500 |

- **`-u _printf_float` 는 플래시 10,456 바이트를 더 쓴다.** [실증]
- **`%lld` 가 nano 에서 옳은 값을 내는지는 확인하지 못했다.** [미확인]
  두 빌드 모두 링크는 되지만, 링크 성공은 출력이 옳다는 증거가 아니다.
  이 호스트에는 ARM 코드를 돌릴 수단이 없다.

그 두 번째 항목이 곧 이유다. libc 에 맡기면 **이 호스트에서 검증할 수 없는
출력을 보드에 실어 보내게 된다.** 직접 짠 코드는 호스트에서 Python 의
`json.dumps` 와 바이트 단위로 대조된다.

규격 §7.1 의 `t` 는 `int64` epoch_ms 라 32비트로 담기지 않으므로 `%lld`
회피는 선택이 아니다.

### 반드시 지켜야 하는 것 세 가지

1. **문자열 이스케이프는 선택이 아니다.** `dev.id` 같은 설정값에 `"` 와
   `\` 가 실제로 들어온다 — 호스트의 설정 검증(`_ALLOWED_STR_CHARS`)이
   프로토콜 구분자 `$,*` 만 막고 이 둘은 통과시킨다. 시뮬레이터에 실제로
   `a"b\c` 를 넣어 보드까지 도달하는 것을 확인했다. 이스케이프하지 않으면
   그 값 하나가 NDJSON 한 줄을 깨뜨린다.

2. **넘치면 아무것도 내보내지 않는다.** 잘린 JSON 은 호스트에서 파싱에
   실패해 어차피 버려지는데, 무엇이 잘렸는지는 아무 데도 남지 않는다.
   보내지 않는 편이 낫다 — 최소한 `seq` 가 건너뛴 것으로 유실이 드러난다.
   `ok` 는 한 번 0 이 되면 되돌아오지 않고, `mk_json_end` 가 `buf[0]` 을
   비운다.

3. **유한하지 않은 실수는 `null` 이다.** JSON 에 NaN 은 없다. 레코드 전체를
   실패시키지 않는 이유는 **채널 장애 격리**다 — 센서 하나가 이상한 값을
   내도 나머지 필드는 살아야 한다. 호스트의 `loop_gauge` 가 이미 유한하지
   않은 값을 `값 없음` 으로 표시한다.

- [ ] **Step 1: 실패하는 시험 작성**

`firmware/stage1/tests/test_json.c`:
```c
/* mk_json 단위 시험. 보드도 크로스 툴체인도 필요 없다.
 *
 * 🔴 --records 모드가 규격 §7 의 레코드를 그대로 찍는다. crosscheck_json.py
 *    가 그 출력을 Python 의 json.dumps 와 **바이트 단위로** 대조한다. */
#include <stdio.h>
#include <string.h>
#include "../app/mk_json.h"

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
    if (!(cond)) { printf("  FAIL %s\n", msg); failures++; }    \
    else         { printf("  ok   %s\n", msg); }                \
} while (0)

#define CHECK_EQ(got, want, msg) do {                                       \
    if (strcmp((got), (want)) != 0) {                                       \
        printf("  FAIL %s\n        got  %s\n        want %s\n",             \
               msg, (got), (want));                                         \
        failures++;                                                         \
    } else { printf("  ok   %s\n", msg); }                                  \
} while (0)

static void test_empty_object(void)
{
    char b[8];
    MkJson j;
    mk_json_begin(&j, b, sizeof b);
    CHECK(mk_json_end(&j) == 2, "빈 객체 길이 2");
    CHECK_EQ(b, "{}", "빈 객체 내용");
}

static void test_scalars(void)
{
    char b[128];
    MkJson j;
    mk_json_begin(&j, b, sizeof b);
    mk_json_u32(&j, "seq", 1234u);
    mk_json_i64(&j, "t", 1772200855875LL);
    mk_json_bool(&j, "ro", 0);
    mk_json_i32(&j, "raw", -8388608);
    mk_json_end(&j);
    CHECK_EQ(b, "{\"seq\":1234,\"t\":1772200855875,\"ro\":false,\"raw\":-8388608}",
             "정수·불리언 필드");
}

static void test_int64_extremes(void)
{
    /* 🔴 -v 로 부호를 뒤집으면 INT64_MIN 에서 넘친다. */
    char b[64];
    MkJson j;
    mk_json_begin(&j, b, sizeof b);
    mk_json_i64(&j, "min", (int64_t)(-9223372036854775807LL - 1LL));
    mk_json_end(&j);
    CHECK_EQ(b, "{\"min\":-9223372036854775808}", "INT64_MIN");

    mk_json_begin(&j, b, sizeof b);
    mk_json_u64(&j, "max", 18446744073709551615ULL);
    mk_json_end(&j);
    CHECK_EQ(b, "{\"max\":18446744073709551615}", "UINT64_MAX");

    mk_json_begin(&j, b, sizeof b);
    mk_json_i64(&j, "z", 0);
    mk_json_end(&j);
    CHECK_EQ(b, "{\"z\":0}", "0");
}

static void test_string_escaping(void)
{
    /* 🔴 dev.id 에 `"` 와 `\` 가 실제로 들어올 수 있다 — 호스트의 설정
     *    검증이 프로토콜 구분자 $,* 만 막고 이 둘은 통과시킨다. */
    char b[128];
    MkJson j;
    mk_json_begin(&j, b, sizeof b);
    mk_json_str(&j, "device_id", "a\"b\\c");
    mk_json_end(&j);
    CHECK_EQ(b, "{\"device_id\":\"a\\\"b\\\\c\"}", "따옴표·역슬래시 이스케이프");

    mk_json_begin(&j, b, sizeof b);
    mk_json_str(&j, "s", "x\x01y");
    mk_json_end(&j);
    CHECK_EQ(b, "{\"s\":\"x\\u0001y\"}", "제어문자 이스케이프");

    mk_json_begin(&j, b, sizeof b);
    mk_json_str(&j, "s", "");
    mk_json_end(&j);
    CHECK_EQ(b, "{\"s\":\"\"}", "빈 문자열");
}

static void test_float_digits(void)
{
    char b[64];
    MkJson j;

    mk_json_begin(&j, b, sizeof b);
    mk_json_f32(&j, "ma", 12.0041f, 4);
    mk_json_end(&j);
    CHECK_EQ(b, "{\"ma\":12.0041}", "12.0041 자릿수 4");

    mk_json_begin(&j, b, sizeof b);
    mk_json_f32(&j, "v", 12.5f, 4);
    mk_json_end(&j);
    CHECK_EQ(b, "{\"v\":12.5000}", "자릿수만큼 0 을 채운다");

    mk_json_begin(&j, b, sizeof b);
    mk_json_f32(&j, "v", 12.0005f, 4);
    mk_json_end(&j);
    CHECK_EQ(b, "{\"v\":12.0005}", "소수부 앞자리 0");

    mk_json_begin(&j, b, sizeof b);
    mk_json_f32(&j, "v", 0.0f, 0);
    mk_json_end(&j);
    CHECK_EQ(b, "{\"v\":0}", "자릿수 0 이면 소수점이 없다");

    mk_json_begin(&j, b, sizeof b);
    mk_json_f32(&j, "v", -4.25f, 2);
    mk_json_end(&j);
    CHECK_EQ(b, "{\"v\":-4.25}", "음수");

    /* -0.0001 을 두 자리로 반올림하면 0 이다. "-0.00" 은 만들지 않는다. */
    mk_json_begin(&j, b, sizeof b);
    mk_json_f32(&j, "v", -0.0001f, 2);
    mk_json_end(&j);
    CHECK_EQ(b, "{\"v\":0.00}", "0 으로 반올림된 음수는 부호를 버린다");
}

static void test_float_non_finite_is_null(void)
{
    /* 🔴 JSON 에 NaN 은 없다. 레코드 전체를 실패시키지 않는 이유는
     *    채널 장애 격리다 — 센서 하나 때문에 나머지 필드까지 잃지 않는다. */
    char b[64];
    MkJson j;
    float zero = 0.0f;
    float nan = zero / zero;
    float inf = 1.0f / zero;

    mk_json_begin(&j, b, sizeof b);
    mk_json_f32(&j, "ma", nan, 4);
    mk_json_end(&j);
    CHECK_EQ(b, "{\"ma\":null}", "NaN 은 null");

    mk_json_begin(&j, b, sizeof b);
    mk_json_f32(&j, "ma", inf, 4);
    mk_json_end(&j);
    CHECK_EQ(b, "{\"ma\":null}", "Inf 는 null");

    mk_json_begin(&j, b, sizeof b);
    mk_json_f32(&j, "ma", -inf, 4);
    mk_json_end(&j);
    CHECK_EQ(b, "{\"ma\":null}", "-Inf 는 null");

    mk_json_begin(&j, b, sizeof b);
    mk_json_f32(&j, "ma", 1.0e30f, 6);
    mk_json_end(&j);
    CHECK_EQ(b, "{\"ma\":null}", "int64 를 넘는 값은 null");
}

static void test_overflow_is_sticky_and_yields_nothing(void)
{
    /* 🔴 잘린 JSON 을 흘려보내지 않는다. 잘린 줄은 호스트에서 파싱에
     *    실패해 어차피 버려지는데, 무엇이 잘렸는지는 남지 않는다. */
    char b[16];
    MkJson j;
    mk_json_begin(&j, b, sizeof b);
    mk_json_str(&j, "device_id", "0123456789ABCDEF");
    mk_json_u32(&j, "seq", 1u);
    CHECK(mk_json_end(&j) < 0, "넘치면 음수");
    CHECK(b[0] == '\0', "넘치면 빈 문자열을 남긴다");

    /* 경계 바로 안팎을 둘 다 본다. 여유가 큰 경우만 시험하면 딱 한
     * 바이트 모자란 경우를 놓친다 — mk_framing 에서 실제로 놓쳤다.
     * {"s":1234} 는 10자이므로 NUL 까지 11 바이트가 필요하다. */
    char exact[11];
    mk_json_begin(&j, exact, sizeof exact);
    mk_json_u32(&j, "s", 1234u);
    CHECK(mk_json_end(&j) == 10, "딱 맞으면 통과");
    CHECK_EQ(exact, "{\"s\":1234}", "딱 맞는 내용");

    char tight[10];
    memset(tight, 0x5A, sizeof tight);
    mk_json_begin(&j, tight, sizeof tight);
    mk_json_u32(&j, "s", 1234u);
    CHECK(mk_json_end(&j) < 0, "정확히 1바이트 부족 → 실패");
}

/* ---- Python 과 바이트 단위로 대조할 레코드 ---------------------------- */

static void print_records(void)
{
    char b[512];
    MkJson j;

    /* 규격 §5.2 `id` 레코드 */
    mk_json_begin(&j, b, sizeof b);
    mk_json_u32(&j, "schema_ver", 3u);
    mk_json_u32(&j, "seq", 0u);
    mk_json_i64(&j, "t", 1772200855875LL);
    mk_json_str(&j, "type", "id");
    mk_json_str(&j, "device_id", "1");
    mk_json_str(&j, "fw", "0.1.0");
    mk_json_str(&j, "board_rev", "2.0");
    mk_json_end(&j);
    printf("id\t%s\n", b);

    /* device_id 에 이스케이프가 필요한 값이 들어온 경우 */
    mk_json_begin(&j, b, sizeof b);
    mk_json_u32(&j, "schema_ver", 3u);
    mk_json_u32(&j, "seq", 0u);
    mk_json_i64(&j, "t", 0LL);
    mk_json_str(&j, "type", "id");
    mk_json_str(&j, "device_id", "a\"b\\c");
    mk_json_str(&j, "fw", "0.1.0");
    mk_json_str(&j, "board_rev", "2.0");
    mk_json_end(&j);
    printf("id_escaped\t%s\n", b);

    /* 규격 §7.2 텔레메트리 — 실수·큰 정수가 다 들어간다 */
    mk_json_begin(&j, b, sizeof b);
    mk_json_u32(&j, "schema_ver", 3u);
    mk_json_u32(&j, "seq", 1234u);
    mk_json_i64(&j, "t", 1772200855875LL);
    mk_json_str(&j, "type", "ain");
    mk_json_u32(&j, "connector_id", 3u);
    mk_json_i32(&j, "raw", 8388608);
    mk_json_f32(&j, "ma", 12.0041f, 4);
    mk_json_f32(&j, "value", 3.4210f, 4);
    mk_json_str(&j, "unit", "bar");
    mk_json_u32(&j, "status", 0u);
    mk_json_u64(&j, "capture_counter", 123456789ULL);
    mk_json_end(&j);
    printf("ain\t%s\n", b);

    /* 센서가 죽어 NaN 이 올라온 경우 — 나머지 필드는 살아야 한다 */
    mk_json_begin(&j, b, sizeof b);
    mk_json_u32(&j, "schema_ver", 3u);
    mk_json_u32(&j, "seq", 1235u);
    mk_json_i64(&j, "t", 1772200855885LL);
    mk_json_str(&j, "type", "ain");
    mk_json_u32(&j, "connector_id", 4u);
    mk_json_i32(&j, "raw", -1);
    {
        float zero = 0.0f;
        mk_json_f32(&j, "ma", zero / zero, 4);
        mk_json_f32(&j, "value", zero / zero, 4);
    }
    mk_json_u32(&j, "status", 1u);
    mk_json_end(&j);
    printf("ain_nan\t%s\n", b);
}

int main(int argc, char **argv)
{
    if (argc > 1 && strcmp(argv[1], "--records") == 0) {
        print_records();
        return 0;
    }
    printf("mk_json\n");
    test_empty_object();
    test_scalars();
    test_int64_extremes();
    test_string_escaping();
    test_float_digits();
    test_float_non_finite_is_null();
    test_overflow_is_sticky_and_yields_nothing();
    printf(failures ? "\nFAILED (%d)\n" : "\nPASSED\n", failures);
    return failures ? 1 : 0;
}
```

- [ ] **Step 2: 시험 실행해 실패 확인**

Run: `powershell -File firmware/stage1/tests/run_tests.ps1`
Expected: FAIL — `mk_json.h: 그런 파일이 없습니다`

- [ ] **Step 3: `mk_json.h` 작성**

`firmware/stage1/app/mk_json.h`:
```c
/* MarkON NDJSON 조립 — HAL 비의존, libc 의 printf 비의존.
 *
 * 🔴 이 파일은 stm32h7xx_hal.h 를 include 하지 않는다. 호스트 컴파일러로
 *    그대로 컴파일해 Python 의 json.dumps 와 바이트 단위로 대조한다.
 *
 * 🔴 정수·실수 출력을 직접 짠다. snprintf 에 맡기지 않는 이유는 두 가지다.
 *
 *    1. newlib-nano 에서 %f 를 쓰려면 -u _printf_float 가 필요하고, 이
 *       툴체인에서 실측한 비용이 플래시 10,456 바이트다
 *       (docs/measurements/2026-08-13_newlib_nano_printf.md).
 *
 *    2. 더 중요한 것 — 시험할 수 있다. nano 의 %lld 가 올바른 값을 내는지는
 *       이 개발 호스트에서 확인할 방법이 없다(ARM 코드를 돌릴 수단이 없다).
 *       libc 에 맡기면 검증하지 못한 출력을 보드에 실어 보내게 된다.
 *       직접 짠 코드는 호스트에서 Python 과 대조된다.
 *
 *    규격 §7.1 의 `t` 는 int64 epoch_ms 라 32비트로 담기지 않는다.
 *    %lld 회피는 선택이 아니라 필수다.
 *
 * 규격: protocol/specification.md §7
 */
#ifndef MK_JSON_H
#define MK_JSON_H

#include <stddef.h>
#include <stdint.h>

typedef struct {
    char  *buf;
    size_t cap;
    size_t len;
    int    ok;      /* 0 이 되면 이후 호출은 전부 무시된다 */
    int    nfield;  /* 이미 쓴 필드 수 — 쉼표를 넣을지 판단한다 */
} MkJson;

/* 🔴 넘치면 잘라 담지 않는다. 한 번 넘치면 ok 가 0 이 되고 그 뒤 모든
 *    호출이 무시되며 mk_json_end 가 음수를 돌려준다.
 *
 *    잘린 JSON 을 보내면 호스트의 json.loads 가 실패해 레코드가 통째로
 *    버려지는데, 무엇이 잘렸는지는 아무 데도 남지 않는다. 보내지 않는
 *    편이 낫다 — 최소한 seq 가 건너뛴 것으로 유실이 드러난다. */
void mk_json_begin(MkJson *j, char *buf, size_t cap);

/* 문자열 값. `"` `\` 와 제어문자를 이스케이프한다.
 *
 * 🔴 이스케이프는 선택이 아니다. dev.id 같은 설정값에 `"` 와 `\` 가 실제로
 *    들어올 수 있다 — 호스트의 설정 검증(_ALLOWED_STR_CHARS)이 프로토콜
 *    구분자 `$,*` 만 막고 이 둘은 통과시킨다. 이스케이프하지 않으면 그
 *    값 하나가 NDJSON 한 줄을 깨뜨린다. */
void mk_json_str(MkJson *j, const char *key, const char *val);

void mk_json_i64(MkJson *j, const char *key, int64_t val);
void mk_json_u64(MkJson *j, const char *key, uint64_t val);
void mk_json_i32(MkJson *j, const char *key, int32_t val);
void mk_json_u32(MkJson *j, const char *key, uint32_t val);
void mk_json_bool(MkJson *j, const char *key, int val);

/* 고정 소수점 자릿수로 반올림한 십진수. digits 는 0~6.
 *
 * 🔴 유한하지 않거나(NaN/Inf) 자릿수를 곱했을 때 int64 를 넘는 값은
 *    `null` 로 낸다. JSON 에는 NaN 이 없다.
 *
 *    레코드 전체를 실패시키지 않는 이유는 채널 장애 격리다 — 센서 하나가
 *    이상한 값을 내도 나머지 필드는 살아야 한다. 호스트 쪽 loop_gauge 가
 *    이미 유한하지 않은 값을 "값 없음" 으로 표시하도록 되어 있다. */
void mk_json_f32(MkJson *j, const char *key, float val, int digits);

/* `}` 를 닫는다. 줄끝(`\n`)은 붙이지 않는다 — 전송 계층이 붙인다.
 * 반환: NUL 을 뺀 길이. 넘쳤으면 음수. */
int mk_json_end(MkJson *j);

#endif /* MK_JSON_H */
```

- [ ] **Step 4: `mk_json.c` 작성**

`firmware/stage1/app/mk_json.c`:
```c
#include "mk_json.h"

#include <string.h>

/* 10^0 .. 10^6 — digits 상한이 6인 이유가 이 표다. */
static const int64_t POW10[7] = {
    1, 10, 100, 1000, 10000, 100000, 1000000
};

static void put(MkJson *j, char c)
{
    if (!j->ok) {
        return;
    }
    if (j->len + 1u >= j->cap) {     /* NUL 자리를 남긴다 */
        j->ok = 0;
        return;
    }
    j->buf[j->len++] = c;
}

static void puts_raw(MkJson *j, const char *s)
{
    for (; *s; s++) {
        put(j, *s);
    }
}

/* uint64 를 10진수로. libc 를 쓰지 않는다. */
static void put_u64(MkJson *j, uint64_t v)
{
    char tmp[20];                    /* 2^64-1 은 20자리 */
    int n = 0;
    if (v == 0u) {
        put(j, '0');
        return;
    }
    while (v > 0u && n < (int)sizeof tmp) {
        tmp[n++] = (char)('0' + (v % 10u));
        v /= 10u;
    }
    while (n > 0) {
        put(j, tmp[--n]);
    }
}

static void put_i64(MkJson *j, int64_t v)
{
    /* 🔴 -v 로 뒤집으면 INT64_MIN 에서 넘친다. 부호 없는 쪽으로 옮긴다. */
    uint64_t mag;
    if (v < 0) {
        put(j, '-');
        mag = (uint64_t)(-(v + 1)) + 1u;
    } else {
        mag = (uint64_t)v;
    }
    put_u64(j, mag);
}

static void put_key(MkJson *j, const char *key)
{
    if (j->nfield > 0) {
        put(j, ',');
    }
    j->nfield++;
    put(j, '"');
    puts_raw(j, key);               /* 키는 우리가 정한 ASCII 리터럴이다 */
    put(j, '"');
    put(j, ':');
}

void mk_json_begin(MkJson *j, char *buf, size_t cap)
{
    j->buf = buf;
    j->cap = cap;
    j->len = 0u;
    j->ok = (buf != NULL && cap >= 3u);   /* 최소 "{}" + NUL */
    j->nfield = 0;
    put(j, '{');
}

void mk_json_str(MkJson *j, const char *key, const char *val)
{
    static const char HEX[] = "0123456789abcdef";
    put_key(j, key);
    put(j, '"');
    for (const unsigned char *p = (const unsigned char *)val; *p; p++) {
        unsigned char c = *p;
        if (c == '"' || c == '\\') {
            put(j, '\\');
            put(j, (char)c);
        } else if (c < 0x20u) {
            /* 설정 계층이 제어문자를 막지만, 여기서 한 번 더 막는다.
             * 값의 출처가 설정만은 아니게 될 수 있다. */
            put(j, '\\');
            put(j, 'u');
            put(j, '0');
            put(j, '0');
            put(j, HEX[(c >> 4) & 0x0Fu]);
            put(j, HEX[c & 0x0Fu]);
        } else {
            put(j, (char)c);
        }
    }
    put(j, '"');
}

void mk_json_i64(MkJson *j, const char *key, int64_t val)
{
    put_key(j, key);
    put_i64(j, val);
}

void mk_json_u64(MkJson *j, const char *key, uint64_t val)
{
    put_key(j, key);
    put_u64(j, val);
}

void mk_json_i32(MkJson *j, const char *key, int32_t val)
{
    mk_json_i64(j, key, (int64_t)val);
}

void mk_json_u32(MkJson *j, const char *key, uint32_t val)
{
    mk_json_u64(j, key, (uint64_t)val);
}

void mk_json_bool(MkJson *j, const char *key, int val)
{
    put_key(j, key);
    puts_raw(j, val ? "true" : "false");
}

void mk_json_f32(MkJson *j, const char *key, float val, int digits)
{
    put_key(j, key);

    if (digits < 0) {
        digits = 0;
    }
    if (digits > 6) {
        digits = 6;
    }

    double scaled = (double)val * (double)POW10[digits];

    /* 🔴 이 한 줄이 NaN·±Inf·범위초과를 모두 잡는다. 부정형으로 쓴 것이
     *    핵심이다 — NaN 은 어떤 비교도 거짓이므로 `scaled > -9e18` 과
     *    `scaled < 9e18` 이 둘 다 거짓이 되고, 부정하면 참이 된다.
     *    `if (val != val)` 로 NaN 을 따로 거르는 코드를 앞에 두었더니
     *    되돌림 검사에서 그 분기를 지워도 아무 시험도 실패하지 않았다.
     *    아무것도 지키지 않는 분기였다.
     *
     *    int64 로 옮길 수 없는 값은 지어내지 않는다. 경계를 int64 최대치
     *    (약 9.22e18)보다 낮게 잡아 부동소수점 오차 여유를 둔다. */
    if (!(scaled > -9.0e18 && scaled < 9.0e18)) {
        puts_raw(j, "null");
        return;
    }

    /* 0 에서 먼 쪽으로 반올림 — Python 의 round() 는 짝수 쪽이지만,
     * 여기서 맞춰야 하는 것은 json.dumps 가 아니라 사람이 읽는 값이다.
     * 대조 시험은 Python 쪽도 같은 규칙으로 계산한다. */
    int neg = scaled < 0.0;
    if (neg) {
        scaled = -scaled;
    }
    int64_t units = (int64_t)(scaled + 0.5);

    int64_t ip = units / POW10[digits];
    int64_t fp = units % POW10[digits];

    if (neg && (ip != 0 || fp != 0)) {
        put(j, '-');
    }
    put_u64(j, (uint64_t)ip);

    if (digits > 0) {
        put(j, '.');
        /* 앞자리 0 을 채운다. 12.5 를 digits=4 로 내면 12.5000 이다. */
        for (int d = digits - 1; d > 0; d--) {
            if (fp < POW10[d]) {
                put(j, '0');
            } else {
                break;
            }
        }
        put_u64(j, (uint64_t)fp);
    }
}

int mk_json_end(MkJson *j)
{
    put(j, '}');
    if (!j->ok) {
        if (j->buf != NULL && j->cap > 0u) {
            j->buf[0] = '\0';        /* 반쪽짜리 줄을 흘리지 않는다 */
        }
        return -1;
    }
    j->buf[j->len] = '\0';
    return (int)j->len;
}
```

- [ ] **Step 5: 시험 실행해 통과 확인**

`run_tests.ps1` 이 두 벌을 다 돌리도록 고친다.

`firmware/stage1/tests/run_tests.ps1`:
```powershell
# 🔴 이 개발 호스트에는 gcc 도 clang 도 없다 (2026-08-13 확인). arm-none-eabi-gcc
#    는 크로스 전용이라 호스트 시험에 못 쓴다. 있는 것은 MSVC 뿐이다.
#    Makefile 은 gcc 가 있는 환경(CI·리눅스)을 위해 남겨 둔다.
#
#    /utf-8 이 필수다. 이 파일들은 UTF-8 인데 MSVC 는 기본으로 CP949 로 읽어,
#    한글 바이트열 안의 0x5C 를 백슬래시로 오인하고 문자열을 깨뜨린다.
#
# 🔴 이 파일은 BOM 이 붙은 UTF-8 로 저장해야 한다. Windows PowerShell 5.1 은
#    BOM 이 없으면 스크립트를 CP949 로 읽어 한글이 깨지고, 아래 throw 문의
#    따옴표가 어긋나면 구문 오류로 죽는다. MSVC 문제의 거울상이다.
$ErrorActionPreference = "Stop"
$vcvars = "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
if (-not (Test-Path $vcvars)) { throw "vcvars64.bat 없음: $vcvars" }
Set-Location $PSScriptRoot

# 시험 묶음: 실행 파일 이름 -> 소스들
$suites = @(
    @{ exe = "test_framing.exe"; src = "test_framing.c ..\app\mk_framing.c" },
    @{ exe = "test_json.exe";    src = "test_json.c ..\app\mk_json.c" }
)

# 빌드와 실행을 나눈다. 한 사슬로 묶으면 컴파일 실패와 시험 실패가 같은
# 종료 코드로 나와, 무엇이 깨졌는지 종료 코드만 보고는 알 수 없다.
foreach ($s in $suites) {
    $build = "chcp 65001 >nul && `"$vcvars`" >nul 2>&1 && " +
             "cl /nologo /W4 /WX /utf-8 /std:c11 /Fe:$($s.exe) $($s.src) >nul"
    cmd /c $build
    if ($LASTEXITCODE -ne 0) {
        Write-Output "빌드 실패: $($s.exe) (exit $LASTEXITCODE) — 시험을 돌리지 않는다."
        exit 2
    }
}

$failed = 0
foreach ($s in $suites) {
    cmd /c "chcp 65001 >nul && .\$($s.exe)"
    if ($LASTEXITCODE -ne 0) { $failed = 1 }
    Write-Output ""
}
exit $failed
```

`firmware/stage1/tests/Makefile`:
```make
# 호스트 네이티브 빌드. 크로스 툴체인도 보드도 필요 없다.
CC ?= gcc
CFLAGS = -std=c11 -Wall -Wextra -Werror -O1 -g

TESTS = test_framing test_json

all: $(TESTS)
	./test_framing
	./test_json

test_framing: test_framing.c ../app/mk_framing.c
	$(CC) $(CFLAGS) -o $@ $^

test_json: test_json.c ../app/mk_json.c
	$(CC) $(CFLAGS) -o $@ $^

clean:
	rm -f $(TESTS) $(addsuffix .exe,$(TESTS))
.PHONY: all clean
```

Run: `powershell -File firmware/stage1/tests/run_tests.ps1`
Expected: framing 45건 + json 24건, 둘 다 `PASSED`, exit 0

- [ ] **Step 6: Python 과 바이트 단위 대조**

`firmware/stage1/tests/crosscheck_json.py`:
```python
"""C 가 만든 NDJSON 이 Python 이 만드는 것과 **바이트 단위로** 같은지 대조한다.

🔴 보드와 호스트가 같은 레코드를 서로 다른 바이트로 쓰면, 호스트 시험은
   전부 통과하는데 실기기에서만 어긋난다. 그런 어긋남은 대개 실수 자릿수나
   이스케이프처럼 눈으로는 잘 안 보이는 곳에서 난다.

C 는 libc 의 printf 를 쓰지 않고 정수·실수 출력을 직접 짠다
(docs/measurements/2026-08-13_newlib_nano_printf.md). 그 직접 짠 출력이
Python 의 json.dumps 와 같은지가 이 대조의 전부다.

실수는 json.dumps 에 맡길 수 없다 — Python 은 float 를 repr 로 찍고 C 는
고정 자릿수로 반올림한다. 같은 반올림 규칙(0 에서 먼 쪽)을 Python 으로도
구현해 문자열을 만든 뒤 끼워 넣는다.
"""
from __future__ import annotations

import json
import math
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _find_binary() -> Path:
    for name in ("test_json.exe", "test_json"):
        p = HERE / name
        if p.exists():
            return p
    raise SystemExit("시험 바이너리가 없다. 먼저 run_tests.ps1 (또는 make) 을 돌려라.")


class _Raw:
    """json.dumps 가 손대지 않고 그대로 흘려보낼 조각."""

    def __init__(self, text: str) -> None:
        self.text = text


class _Encoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, _Raw):
            return f"@@{id(o)}@@"
        return super().default(o)


def f32(value: float, digits: int) -> _Raw:
    """C 의 mk_json_f32 와 같은 규칙으로 십진 문자열을 만든다.

    float32 로 한 번 접어 넣는 것이 중요하다 — C 쪽은 float 이므로,
    Python 의 double 로 계산하면 마지막 자리가 갈릴 수 있다.
    """
    v = struct.unpack("<f", struct.pack("<f", value))[0]
    if not math.isfinite(v):
        return _Raw("null")
    scaled = v * (10 ** digits)
    if not (-9.0e18 < scaled < 9.0e18):
        return _Raw("null")

    neg = scaled < 0
    if neg:
        scaled = -scaled
    units = int(scaled + 0.5)          # 0 에서 먼 쪽 반올림 (C 와 동일)

    ip, fp = divmod(units, 10 ** digits)
    sign = "-" if (neg and units != 0) else ""
    if digits == 0:
        return _Raw(f"{sign}{ip}")
    return _Raw(f"{sign}{ip}.{fp:0{digits}d}")


def dumps(obj: dict) -> str:
    """C 와 같은 압축 형식으로. _Raw 조각은 그대로 박아 넣는다."""
    raws = {f"@@{id(v)}@@": v.text for v in obj.values() if isinstance(v, _Raw)}
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), cls=_Encoder)
    for placeholder, literal in raws.items():
        text = text.replace(f'"{placeholder}"', literal)
    return text


EXPECTED: dict[str, dict] = {
    "id": {
        "schema_ver": 3, "seq": 0, "t": 1772200855875, "type": "id",
        "device_id": "1", "fw": "0.1.0", "board_rev": "2.0",
    },
    "id_escaped": {
        "schema_ver": 3, "seq": 0, "t": 0, "type": "id",
        "device_id": 'a"b\\c', "fw": "0.1.0", "board_rev": "2.0",
    },
    "ain": {
        "schema_ver": 3, "seq": 1234, "t": 1772200855875, "type": "ain",
        "connector_id": 3, "raw": 8388608,
        "ma": f32(12.0041, 4), "value": f32(3.4210, 4),
        "unit": "bar", "status": 0, "capture_counter": 123456789,
    },
    "ain_nan": {
        "schema_ver": 3, "seq": 1235, "t": 1772200855885, "type": "ain",
        "connector_id": 4, "raw": -1,
        "ma": f32(float("nan"), 4), "value": f32(float("nan"), 4),
        "status": 1,
    },
}


def main() -> int:
    out = subprocess.run(
        [str(_find_binary()), "--records"],
        capture_output=True, text=True, check=True,
    ).stdout

    rows = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        name, payload = line.split("\t", 1)
        rows[name] = payload

    mismatches: list[str] = []
    for name, obj in EXPECTED.items():
        if name not in rows:
            mismatches.append(f"  {name}: C 출력에 없다")
            continue
        want = dumps(obj)
        got = rows[name]
        if got != want:
            mismatches.append(f"  {name}\n    C : {got}\n    py: {want}")
            continue
        # 형태만 같아서는 부족하다. 실제로 파싱되는지도 본다.
        try:
            json.loads(got)
        except Exception as exc:
            mismatches.append(f"  {name}: C 출력이 JSON 으로 파싱되지 않는다: {exc}")
            continue
        print(f"  ok   {name:12} {len(got):4}B")

    extra = set(rows) - set(EXPECTED)
    if extra:
        mismatches.append(f"  C 만 내놓은 레코드가 있다(기대값 없음): {sorted(extra)}")

    if mismatches:
        print("\n두 구현이 어긋난다:", file=sys.stderr)
        print("\n".join(mismatches), file=sys.stderr)
        return 1
    print(f"\nMATCH ({len(EXPECTED)} records)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Run: `python firmware/stage1/tests/crosscheck_json.py`
Expected: `MATCH (4 records)`, exit 0

- [ ] **Step 7: 커밋**

커밋 메시지는 파일로 써서 `git commit -F` 로 넘긴다. PowerShell 5.1 이
큰따옴표가 든 `-m` 인자를 쪼갠다.

```bash
git add firmware/stage1/app/mk_json.h firmware/stage1/app/mk_json.c \
        firmware/stage1/tests/test_json.c \
        firmware/stage1/tests/crosscheck_json.py \
        firmware/stage1/tests/run_tests.ps1 firmware/stage1/tests/Makefile
git commit -F <메시지 파일>
```

### Task 2 사전 실행 기록 (2026-08-13)

구현자에게 넘기기 전에 위 코드를 그대로 컴파일해 돌렸다.

- `/W4 /WX` 무경고, 단위 시험 **24건 통과**, `MATCH (4 records)`
- 규격 §7.2 의 예시와 필드 순서·형태가 일치한다

되돌림 검사로 각 방어 장치가 실제로 무언가를 지키는지 확인했다.

| 되돌린 것 | 결과 |
|---|---|
| 문자열 이스케이프 제거 | 단위 시험·교차 대조 둘 다 실패 — 잡는다 |
| 넘침 시 `buf[0]` 비우기 제거 | 단위 시험 실패 — 잡는다 |
| 소수부 앞자리 0 채우기 제거 | 둘 다 실패 — 잡는다 |
| **NaN 을 따로 거르는 분기 제거** | **아무것도 실패하지 않았다** |
| `INT64_MIN` 안전 변환을 `-v` 로 | 아무것도 실패하지 않았다 |

네 번째는 **내 코드가 중복이었다.** 범위 검사를 부정형으로 쓰면 NaN 은
어떤 비교도 거짓이라 자동으로 걸린다. 아무것도 지키지 않는 분기였으므로
지웠다. 지금 코드에는 NaN 전용 분기가 없다.

다섯 번째는 시험으로 구분할 수 없다. `-v` 는 `INT64_MIN` 에서 정의되지 않은
동작이지만, 2의 보수 기계에서는 안전한 형태와 같은 값이 나온다. 그래도
안전한 형태를 쓴다 — 정의되지 않은 동작은 최적화기가 언제든 다르게 쓸 수
있다. **이 항목은 시험이 지켜주지 않는다는 것을 알고 두는 것이다.**

- 시험 쪽 결함도 하나 잡혔다. 경계 시험에서 `{"s":1234}` 를 10자가 아니라
  9자로 세어 버퍼를 잘못 잡았다. Task 1 에서 겪은 것과 같은 부류라 경계
  바로 안팎을 둘 다 보도록 고쳤다.

---

## Task 3~5 (개요)

Task 2 검토 후 상세화한다.

| Task | 내용 | 핵심 |
|---|---|---|
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
