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
     * 실패해 어차피 버려지는데, 무엇이 잘렸는지는 남지 않는다. */
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
