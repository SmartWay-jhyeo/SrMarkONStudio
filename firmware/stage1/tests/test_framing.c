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

static void test_build_line_buffer_boundary(void)
{
    /* 🔴 여유가 큰 payload 로만 시험하면 딱 한 바이트 모자란 경우를 놓친다.
     *    "HB" 는 "$HB*0A\r\n" + NUL = 9 바이트가 필요하다. */
    char canary[16];
    memset(canary, 0x5A, sizeof canary);
    CHECK(mk_build_line(canary, 8, "HB") < 0, "정확히 1바이트 부족 → 거부");
    CHECK((unsigned char)canary[8] == 0x5Au, "거부했으면 8번째 바이트 무손상");

    char exact[9];
    CHECK(mk_build_line(exact, sizeof exact, "HB") == 8, "딱 맞는 버퍼 → 성공");
    CHECK(strcmp(exact, "$HB*0A\r\n") == 0, "딱 맞는 버퍼 내용");
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

static void test_parse_strips_both_ends(void)
{
    /* Python 쪽은 line.strip() 이라 앞뒤를 다 뗀다. 뒤만 떼면 갈린다. */
    MkCommand c;
    const char *s = " \t$HB*0A\r\n";
    CHECK(mk_parse_line(s, strlen(s), &c) == MK_OK, "앞쪽 공백도 떼어낸다");
    CHECK(strcmp(c.verb, "HB") == 0, "앞쪽 공백 제거 후 verb=HB");
}

static void test_checksum_error_keeps_verb(void)
{
    /* 🔴 규격 §3: 체크섬이 틀리면 $SACK,<verb>,ERR,CHECKSUM 을 보낸다.
     *    그러려면 CHECKSUM 을 돌려줄 때 호출자에게 verb 가 있어야 한다. */
    MkCommand c;
    CHECK(mk_parse_line("$HB*FF\r\n", 8, &c) == MK_ERR_CHECKSUM, "불일치 → CHECKSUM");
    CHECK(strcmp(c.verb, "HB") == 0, "CHECKSUM 이어도 verb 는 남는다");

    const char *s = "$CFG,GET,tx.period_ms*00\r\n";
    CHECK(mk_parse_line(s, strlen(s), &c) == MK_ERR_CHECKSUM, "인자 있는 줄도 CHECKSUM");
    CHECK(strcmp(c.verb, "CFG") == 0, "인자 있는 줄의 verb 도 남는다");
}

static void test_parse_bad_checksum_field_is_checksum_error(void)
{
    /* verb 를 읽을 수 있으면 체크섬 오류다 — 조용히 버리는 것이 아니다.
     * 16진수가 아니어도, 자릿수가 달라도 마찬가지다. */
    MkCommand c;
    CHECK(mk_parse_line("$HB*XY\r\n", 8, &c) == MK_ERR_CHECKSUM, "16진수 아님 → CHECKSUM");
    CHECK(mk_parse_line("$HB*0A0\r\n", 9, &c) == MK_ERR_CHECKSUM, "3자리 → CHECKSUM");
    CHECK(mk_parse_line("$HB*\r\n", 6, &c) == MK_ERR_CHECKSUM, "0자리 → CHECKSUM");
    CHECK(mk_parse_line("$HB*0\r\n", 7, &c) == MK_ERR_CHECKSUM, "1자리 → CHECKSUM");
}

static void test_parse_unreadable_verb_is_silent_drop(void)
{
    /* verb 를 담을 수 없으면 SACK 에 실을 것이 없다. 체크섬이 틀려도
     * CHECKSUM 이 아니라 MALFORMED 여야 조용히 버려진다. */
    char payload[100], line[128];
    MkCommand c;
    memset(payload, 'A', sizeof payload - 1);
    payload[sizeof payload - 1] = '\0';
    int n = snprintf(line, sizeof line, "$%s*00\r\n", payload);  /* 체크섬 틀림 */
    CHECK(mk_parse_line(line, (size_t)n, &c) == MK_ERR_MALFORMED,
          "긴 verb + 틀린 체크섬 → 조용히 버림");
}

static void test_parse_short_inputs_do_not_crash(void)
{
    /* 길이 0~3 이 경계 검사를 지나가지 않는지. 순서가 바뀌면 line[0] 을
     * 읽으며 범위를 벗어난다. */
    MkCommand c;
    CHECK(mk_parse_line("", 0, &c) == MK_ERR_MALFORMED, "빈 입력");
    CHECK(mk_parse_line("$", 1, &c) == MK_ERR_MALFORMED, "$ 하나");
    CHECK(mk_parse_line("$*", 2, &c) == MK_ERR_MALFORMED, "$*");
    CHECK(mk_parse_line("\r\n", 2, &c) == MK_ERR_MALFORMED, "줄끝만");
    CHECK(mk_parse_line("   ", 3, &c) == MK_ERR_MALFORMED, "공백만");
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

/* 🔴 파싱 쪽 벡터. build 만 대조하면 두 구현이 같은 줄을 다르게 거부해도
 *    드러나지 않는다 — 실제로 앞쪽 공백과 체크섬 오류 분류에서 갈렸다.
 *    이스케이프 표기는 아래 unescape() 가 푼다. */
static const char *const PARSE_VECTORS[] = {
    "$HB*0A\\r\\n",                 /* 정상 */
    "$HB*0a\\r\\n",                 /* 소문자 체크섬 */
    "$HB*0A\\n",                    /* LF 만 */
    "$CFG,GET,tx.period_ms*72\\r\\n",
    " \\t$HB*0A\\r\\n",             /* 앞쪽 공백 */
    "$HB*0A  \\r\\n",               /* 뒤쪽 공백 */
    "$HB*FF\\r\\n",                 /* 체크섬 불일치 */
    "$HB*XY\\r\\n",                 /* 16진수 아님 */
    "$HB*0A0\\r\\n",                /* 3자리 */
    "$HB*\\r\\n",                   /* 0자리 */
    "HB*0A\\r\\n",                  /* $ 없음 */
    "$HB0A\\r\\n",                  /* * 없음 */
    "$*00\\r\\n",                   /* 빈 payload */
    "$A*B*41\\r\\n",                /* payload 안의 * */
    "",                             /* 빈 입력 */
};

static size_t unescape(const char *s, char *out, size_t cap)
{
    size_t w = 0u;
    for (const char *p = s; *p && w + 1u < cap; p++) {
        if (p[0] == '\\' && p[1] == 'r') { out[w++] = '\r'; p++; }
        else if (p[0] == '\\' && p[1] == 'n') { out[w++] = '\n'; p++; }
        else if (p[0] == '\\' && p[1] == 't') { out[w++] = '\t'; p++; }
        else out[w++] = *p;
    }
    out[w] = '\0';
    return w;
}

static void print_vectors(void)
{
    char buf[256];
    for (size_t i = 0; i < sizeof VECTORS / sizeof *VECTORS; i++) {
        const char *p = VECTORS[i];
        int n = mk_build_line(buf, sizeof buf, p);
        printf("B\t%s\t%02X\t%d\t", p, mk_xor_checksum(p, strlen(p)), n);
        for (char *q = buf; *q; q++) {
            if      (*q == '\r') printf("\\r");
            else if (*q == '\n') printf("\\n");
            else                 putchar(*q);
        }
        putchar('\n');
    }

    for (size_t i = 0; i < sizeof PARSE_VECTORS / sizeof *PARSE_VECTORS; i++) {
        char line[256];
        MkCommand c;
        size_t n = unescape(PARSE_VECTORS[i], line, sizeof line);
        MkParseResult r = mk_parse_line(line, n, &c);
        const char *name = r == MK_OK ? "OK"
                         : r == MK_ERR_CHECKSUM ? "CHECKSUM" : "MALFORMED";
        /* verb 는 OK 와 CHECKSUM 에서만 의미가 있다. */
        printf("P\t%s\t%s\t%s\t%d", PARSE_VECTORS[i], name,
               r == MK_ERR_MALFORMED ? "-" : c.verb,
               r == MK_ERR_MALFORMED ? -1 : c.argc);
        if (r == MK_OK) {
            for (int a = 0; a < c.argc; a++) printf("\t%s", c.args[a]);
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
    test_build_line_buffer_boundary();
    test_parse_ok();
    test_parse_args();
    test_parse_accepts_bare_lf();
    test_parse_accepts_lowercase_checksum();
    test_parse_rejects();
    test_parse_strips_both_ends();
    test_checksum_error_keeps_verb();
    test_parse_bad_checksum_field_is_checksum_error();
    test_parse_unreadable_verb_is_silent_drop();
    test_parse_short_inputs_do_not_crash();
    test_parse_rejects_oversized_verb();
    test_parse_rejects_oversized_arg();
    test_parse_rejects_too_many_args();
    printf(failures ? "\nFAILED (%d)\n" : "\nPASSED\n", failures);
    return failures ? 1 : 0;
}
