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

static void test_short_escapes_match_python(void)
{
    /* 🔴 Python 의 json.dumps 는 이 다섯을 짧은 형태로 쓴다. 전부
     *    \u00XX 로 쓰면 JSON 값은 같아도 바이트가 달라져, 이 계층의
     *    계약("C 와 Python 이 같은 바이트를 낸다")이 깨진다. */
    char b[64];
    MkJson j;
    struct { const char *in; const char *want; const char *msg; } cases[] = {
        { "a\bb", "{\"s\":\"a\\bb\"}", "0x08 -> \\b" },
        { "a\tb", "{\"s\":\"a\\tb\"}", "0x09 -> \\t" },
        { "a\nb", "{\"s\":\"a\\nb\"}", "0x0A -> \\n" },
        { "a\fb", "{\"s\":\"a\\fb\"}", "0x0C -> \\f" },
        { "a\rb", "{\"s\":\"a\\rb\"}", "0x0D -> \\r" },
        /* 짧은 형태가 없는 것은 \u00XX 그대로다 — Python 과 같다. */
        { "a\x0b" "b", "{\"s\":\"a\\u000bb\"}", "0x0B -> \\u000b" },
        { "a\x1f" "b", "{\"s\":\"a\\u001fb\"}", "0x1F -> \\u001f" },
    };
    for (size_t i = 0; i < sizeof cases / sizeof *cases; i++) {
        mk_json_begin(&j, b, sizeof b);
        mk_json_str(&j, "s", cases[i].in);
        mk_json_end(&j);
        CHECK_EQ(b, cases[i].want, cases[i].msg);
    }

    /* 0x7F 는 Python 도 이스케이프하지 않는다(ensure_ascii=False). */
    mk_json_begin(&j, b, sizeof b);
    mk_json_str(&j, "s", "a\x7f" "b");
    mk_json_end(&j);
    CHECK_EQ(b, "{\"s\":\"a\x7f" "b\"}", "0x7F 는 그대로");
}

/* ---- mk_json_fixed — 부동소수를 한 번도 거치지 않는 십진 소수 ------------
 *
 * 🔴 이것이 생긴 이유는 위·경도다(규격 §7.8.2). `float`(가수 24비트)은
 *    유효숫자가 약 7자리라 `127.3405907`(10자리)을 담지 못한다 — 담으면
 *    약 1 m 가 조용히 날아가고, 소수점은 여전히 그럴듯하게 붙어 있어
 *    아무도 눈치채지 못한다. `mk_json_f32` 로는 이 값을 낼 수 없다. */
static void test_fixed_point(void)
{
    char b[64];
    MkJson j;

    /* 실기기(UM981, 2026-08-20)가 낸 좌표. 1e-7 도 정수 그대로. */
    mk_json_begin(&j, b, sizeof b);
    mk_json_fixed(&j, "lat", 373190694LL, 7);
    mk_json_end(&j);
    CHECK_EQ(b, "{\"lat\":37.3190694}", "위도 7자리");

    /* 🔴 float 로는 못 담는 자릿수다 — 이 값이 이 함수의 존재 이유다. */
    mk_json_begin(&j, b, sizeof b);
    mk_json_fixed(&j, "lon", 1273405907LL, 7);
    mk_json_end(&j);
    CHECK_EQ(b, "{\"lon\":127.3405907}", "경도 10자리 유효숫자");

    mk_json_begin(&j, b, sizeof b);
    mk_json_fixed(&j, "lat", -373190694LL, 7);
    mk_json_end(&j);
    CHECK_EQ(b, "{\"lat\":-37.3190694}", "남위는 음수");

    /* 앞자리 0 을 채운다 — 안 채우면 0.3190694 가 0.319069 처럼 보인다. */
    mk_json_begin(&j, b, sizeof b);
    mk_json_fixed(&j, "lat", 3190694LL, 7);
    mk_json_end(&j);
    CHECK_EQ(b, "{\"lat\":0.3190694}", "정수부가 0 이어도 소수 7자리");

    mk_json_begin(&j, b, sizeof b);
    mk_json_fixed(&j, "alt", 100852LL, 3);
    mk_json_end(&j);
    CHECK_EQ(b, "{\"alt\":100.852}", "고도 mm -> m");

    mk_json_begin(&j, b, sizeof b);
    mk_json_fixed(&j, "speed", 72LL, 3);
    mk_json_end(&j);
    CHECK_EQ(b, "{\"speed\":0.072}", "0.072 — 앞자리 0 두 개");

    mk_json_begin(&j, b, sizeof b);
    mk_json_fixed(&j, "hdop", 120LL, 2);
    mk_json_end(&j);
    CHECK_EQ(b, "{\"hdop\":1.20}", "자릿수만큼 0 을 채운다(f32 와 같은 관례)");

    mk_json_begin(&j, b, sizeof b);
    mk_json_fixed(&j, "n", -1LL, 0);
    mk_json_end(&j);
    CHECK_EQ(b, "{\"n\":-1}", "digits 0 이면 정수 그대로");

    /* 🔴 클램프. 없으면 POW10 배열 밖을 읽는다(mk_json.c 머리말). */
    mk_json_begin(&j, b, sizeof b);
    mk_json_fixed(&j, "v", 5LL, 20);
    mk_json_end(&j);
    CHECK_EQ(b, "{\"v\":0.000000005}", "digits 는 9 로 클램프된다");

    mk_json_begin(&j, b, sizeof b);
    mk_json_fixed(&j, "v", 5LL, -3);
    mk_json_end(&j);
    CHECK_EQ(b, "{\"v\":5}", "음수 digits 는 0 으로 클램프된다");

    /* 🔴 -2^63 은 부호를 뒤집을 수 없다(같은 값으로 되돌아온다). 부호 없는
     *    쪽으로 옮겨 담지 않으면 이 한 값에서만 값이 깨진다. */
    mk_json_begin(&j, b, sizeof b);
    mk_json_fixed(&j, "v", (int64_t)(-9223372036854775807LL - 1LL), 0);
    mk_json_end(&j);
    CHECK_EQ(b, "{\"v\":-9223372036854775808}", "int64 최솟값도 깨지지 않는다");
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

static void test_float_digits_are_clamped(void)
{
    /* 🔴 POW10 은 원소가 7개다. 클램프가 없으면 digits=10 이 배열 밖을
     *    읽는다. 이 시험이 없으면 클램프를 지워도 아무것도 실패하지
     *    않는다 — 실제로 리뷰에서 그렇게 지적받았다. */
    char b[64];
    MkJson j;

    mk_json_begin(&j, b, sizeof b);
    mk_json_f32(&j, "v", 1.0f, 10);
    mk_json_end(&j);
    CHECK_EQ(b, "{\"v\":1.000000}", "digits 10 은 6 으로 깎인다");

    mk_json_begin(&j, b, sizeof b);
    mk_json_f32(&j, "v", 1.0f, -3);
    mk_json_end(&j);
    CHECK_EQ(b, "{\"v\":1}", "음수 digits 는 0 으로 깎인다");

    mk_json_begin(&j, b, sizeof b);
    mk_json_f32(&j, "v", 1.0f, 6);
    mk_json_end(&j);
    CHECK_EQ(b, "{\"v\":1.000000}", "digits 6 은 그대로");
}

static void test_tiny_and_null_buffers(void)
{
    /* mk_json_begin 이 cap 을 따로 막지 않는 대신, put() 의 경계 검사가
     * 같은 결과를 낸다는 것을 못박는다. 범위 밖 쓰기가 없어야 한다. */
    MkJson j;
    for (size_t cap = 0; cap <= 2u; cap++) {
        char tiny[4];
        memset(tiny, 0x5A, sizeof tiny);
        mk_json_begin(&j, tiny, cap);
        CHECK(mk_json_end(&j) < 0, "너무 작은 버퍼는 실패");
        CHECK((unsigned char)tiny[3] == 0x5Au, "너무 작아도 범위 밖을 쓰지 않는다");
    }

    char exact[3];
    mk_json_begin(&j, exact, sizeof exact);
    CHECK(mk_json_end(&j) == 2, "cap 3 이면 {} 가 들어간다");
    CHECK_EQ(exact, "{}", "cap 3 의 내용");

    /* buf 가 NULL 이면 아무것도 쓰지 않는다 — 이 검사는 살아 있어야 한다. */
    mk_json_begin(&j, NULL, 64u);
    mk_json_str(&j, "s", "x");
    CHECK(mk_json_end(&j) < 0, "NULL 버퍼는 실패");
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

    /* 짧은 이스케이프가 필요한 제어문자들. Python 과 바이트가 같아야 한다. */
    mk_json_begin(&j, b, sizeof b);
    mk_json_u32(&j, "schema_ver", 3u);
    mk_json_u32(&j, "seq", 0u);
    mk_json_i64(&j, "t", 0LL);
    mk_json_str(&j, "type", "id");
    mk_json_str(&j, "device_id", "a\b\t\n\f\r\x0b" "z");
    mk_json_str(&j, "fw", "0.1.0");
    mk_json_str(&j, "board_rev", "2.0");
    mk_json_end(&j);
    printf("id_controls\t%s\n", b);

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

    /* 🔴 실수 경계들. 여기가 비어 있으면 대조가 정상 범위만 보고 지나간다.
     *    실제로 음수 0 처리에서 Python 쪽 기준이 틀린 것을 뒤늦게 잡았다. */
    mk_json_begin(&j, b, sizeof b);
    mk_json_u32(&j, "schema_ver", 3u);
    mk_json_u32(&j, "seq", 0u);
    mk_json_i64(&j, "t", 0LL);
    mk_json_str(&j, "type", "ain");
    mk_json_f32(&j, "neg_to_zero", -0.0001f, 2);   /* 0 이 된다 — 부호를 버린다 */
    mk_json_f32(&j, "neg_zero", -0.0f, 2);
    mk_json_f32(&j, "half_up", -1.5f, 0);          /* 0 에서 먼 쪽 */
    mk_json_f32(&j, "half_up_pos", 2.5f, 0);
    mk_json_f32(&j, "pad", 12.5f, 4);
    mk_json_f32(&j, "d0", 3.7f, 0);
    mk_json_f32(&j, "d6", 1.0f, 6);
    mk_json_f32(&j, "tiny", 0.0000004f, 6);
    mk_json_end(&j);
    printf("float_edges\t%s\n", b);

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

/* ---- 문자열 배열 (규격 §7.3 choice_labels) ------------------------------ */

static void test_str_array(void)
{
    char b[256];
    MkJson j;

    static const char *const LABELS[] = { "없음", "조도", "온습도" };
    mk_json_begin(&j, b, sizeof b);
    mk_json_str_array(&j, "choice_labels", LABELS, 3);
    mk_json_end(&j);
    CHECK(strcmp(b, "{\"choice_labels\":[\"없음\",\"조도\",\"온습도\"]}") == 0,
          "이름표 배열이 그대로 실린다");

    /* 🔴 이름표에도 이스케이프가 걸려야 한다. 따옴표 하나가 새면 그 줄부터
     *    끝까지 파싱이 깨지는데, 깨지는 것은 그 항목이 아니라 **카탈로그
     *    전체**다 — 화면이 통째로 안 뜬다. */
    static const char *const NASTY[] = { "a\"b" };
    mk_json_begin(&j, b, sizeof b);
    mk_json_str_array(&j, "x", NASTY, 1);
    mk_json_end(&j);
    CHECK(strcmp(b, "{\"x\":[\"a\\\"b\"]}") == 0, "이름표도 이스케이프된다");

    mk_json_begin(&j, b, sizeof b);
    mk_json_str_array(&j, "x", LABELS, 0);
    mk_json_end(&j);
    CHECK(strcmp(b, "{\"x\":[]}") == 0, "빈 배열");
}

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

int main(int argc, char **argv)
{
    /* 🔴 `--records` 보다 뒤에 둔다. 앞에 두면 시험이 찍는 "ok ..." 줄이
     *    레코드 출력에 섞여 crosscheck_json.py 가 그것을 레코드로 읽는다 —
     *    한글이 섞여 있어 인코딩 오류로 먼저 죽는다 [2026-08-17]. */
    if (argc > 1 && strcmp(argv[1], "--records") == 0) {
        print_records();
        return 0;
    }
    test_str_array();
    printf("mk_json\n");
    test_empty_object();
    test_scalars();
    test_int64_extremes();
    test_string_escaping();
    test_short_escapes_match_python();
    test_float_digits();
    test_float_digits_are_clamped();
    test_fixed_point();
    test_tiny_and_null_buffers();
    test_float_non_finite_is_null();
    test_overflow_is_sticky_and_yields_nothing();
    test_null_is_not_zero_and_not_a_string();
    printf(failures ? "\nFAILED (%d)\n" : "\nPASSED\n", failures);
    return failures ? 1 : 0;
}
