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
