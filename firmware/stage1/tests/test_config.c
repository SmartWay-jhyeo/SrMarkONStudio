/* mk_config 단위 시험. 보드 없이 돈다. */
#include <stdio.h>
#include <string.h>
#include "../app/mk_config.h"

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

/* MSVC 가 strcpy 를 막는다. 시험용 짧은 복사기 — 컴파일러 정의를 푸는 것보다
 * 이쪽이 낫다. 정의를 풀면 진짜 위험한 호출까지 함께 조용해진다. */
static void put(char *dst, size_t cap, const char *src)
{
    size_t n = strlen(src);
    if (n + 1u > cap) { n = cap - 1u; }
    memcpy(dst, src, n);
    dst[n] = '\0';
}

#define CHECK_R(got, want, msg) do {                                        \
    if ((got) != (want)) {                                                  \
        printf("  FAIL %s\n        got  %s\n        want %s\n", msg,        \
               mk_cfg_reason_text(got), mk_cfg_reason_text(want));          \
        failures++;                                                         \
    } else { printf("  ok   %s\n", msg); }                                  \
} while (0)

static const uint32_t DRATE[] = {2, 5, 10, 15, 25, 30, 50, 60, 100, 500,
                                 1000, 2000, 3750, 7500};

static MkCfgItem ITEMS[8];
static MkConfig CFG;

static void setup(void)
{
    memset(ITEMS, 0, sizeof ITEMS);

    ITEMS[0] = (MkCfgItem){ .key = "dev.id", .group = "dev", .vtype = MK_VT_STR,
                            .max = 15, .label = "장치 ID" };
    put(ITEMS[0].def.s, sizeof ITEMS[0].def.s, "1");
    put(ITEMS[0].cur.s, sizeof ITEMS[0].cur.s, "1");

    ITEMS[1] = (MkCfgItem){ .key = "tx.period_ms", .group = "tx",
                            .vtype = MK_VT_U16, .min = 10, .max = 10000,
                            .unit = "ms", .label = "전송 주기" };
    ITEMS[1].def.u = 100;
    ITEMS[1].cur.u = 100;

    ITEMS[2] = (MkCfgItem){ .key = "pwr.24v", .group = "pwr",
                            .vtype = MK_VT_BOOL, .label = "24V 전원" };

    /* 🔴 읽기 전용이면서 인터록인 항목. 규격 §5.2 가 INTERLOCK 이 이긴다고
     *    못박은 바로 그 경우다. */
    ITEMS[3] = (MkCfgItem){ .key = "pwr.5v", .group = "pwr",
                            .vtype = MK_VT_BOOL, .readonly = 1, .interlocked = 1,
                            .label = "5V 전원",
                            .note = "쿨링 팬이 5V 전원에 직결이라 끌 수 없다" };
    ITEMS[3].def.u = 1;
    ITEMS[3].cur.u = 1;

    ITEMS[4] = (MkCfgItem){ .key = "ain0.zero", .group = "ain",
                            .vtype = MK_VT_F32, .min = 0.0f, .max = 25.0f,
                            .unit = "mA", .label = "J3 영점" };
    ITEMS[4].def.f = 4.0f;
    ITEMS[4].cur.f = 4.0f;

    ITEMS[5] = (MkCfgItem){ .key = "adc.drate", .group = "adc",
                            .vtype = MK_VT_ENUM, .unit = "SPS",
                            .choices = DRATE, .n_choices = 14,
                            .label = "데이터율" };
    ITEMS[5].def.u = 60;
    ITEMS[5].cur.u = 60;

    /* 읽기 전용이지만 인터록은 아닌 항목 */
    ITEMS[6] = (MkCfgItem){ .key = "dev.rev", .group = "dev",
                            .vtype = MK_VT_STR, .max = 8, .readonly = 1,
                            .label = "보드 리비전" };
    put(ITEMS[6].def.s, sizeof ITEMS[6].def.s, "2.0");
    put(ITEMS[6].cur.s, sizeof ITEMS[6].cur.s, "2.0");

    ITEMS[7] = (MkCfgItem){ .key = "tx.fields", .group = "tx",
                            .vtype = MK_VT_U32, .min = 0, .max = 4294967295.0f,
                            .label = "NDJSON 필드 마스크" };
    ITEMS[7].def.u = 698;
    ITEMS[7].cur.u = 698;

    CFG.items = ITEMS;
    CFG.count = 8;
    CFG.dirty = 0;
}

/* ---- 검증 순서 (규격 §5.2) --------------------------------------------- */

static void test_unknown_key(void)
{
    setup();
    CHECK_R(mk_cfg_set(&CFG, "없는키", "1"), MK_CFG_UNKNOWN_KEY, "모르는 키");
    CHECK(!mk_cfg_dirty(&CFG), "거부는 dirty 로 치지 않는다");
}

static void test_range_is_checked_before_interlock(void)
{
    /* 🔴 규격 §5.2 — 값이 틀린 것과 안전상 거부된 것은 사용자에게 다른
     *    메시지여야 한다. 순서를 바꾸면 범위 밖 값을 넣었을 때 INTERLOCK 이
     *    나와, 사용자는 값이 아니라 안전 정책 문제로 읽는다. */
    setup();
    CHECK_R(mk_cfg_set(&CFG, "pwr.5v", "아무거나"), MK_CFG_RANGE,
            "인터록 항목이라도 값이 틀리면 RANGE");
}

static void test_interlock_beats_readonly(void)
{
    /* 🔴 규격 §5.2 — 둘 다 해당하면 INTERLOCK 이다. READONLY 만 돌려주면
     *    note 에 담긴 하드웨어 사실(쿨링 팬 직결)이 사유로 전달되지 않는다. */
    setup();
    CHECK_R(mk_cfg_set(&CFG, "pwr.5v", "false"), MK_CFG_INTERLOCK,
            "인터록이 읽기 전용을 이긴다");
}

static void test_plain_readonly(void)
{
    setup();
    CHECK_R(mk_cfg_set(&CFG, "dev.rev", "3.0"), MK_CFG_READONLY,
            "인터록이 아닌 읽기 전용은 READONLY");
}

static void test_same_value_is_accepted(void)
{
    /* 🔴 현재값과 같은 값을 쓰는 것은 거부하지 않는다 (규격 §5.2).
     *    호스트가 전체 설정을 한꺼번에 되돌려 쓸 때 pwr.5v 를 true 로 두는
     *    요청까지 거부되면 안 된다. */
    setup();
    CHECK_R(mk_cfg_set(&CFG, "pwr.5v", "true"), MK_CFG_OK,
            "인터록 항목도 같은 값이면 OK");
    CHECK(!mk_cfg_dirty(&CFG), "아무것도 안 바뀌었으므로 dirty 아님");
    CHECK_R(mk_cfg_set(&CFG, "dev.rev", "2.0"), MK_CFG_OK,
            "읽기 전용도 같은 값이면 OK");
}

/* ---- 타입별 검증 -------------------------------------------------------- */

static void test_bool(void)
{
    setup();
    CHECK_R(mk_cfg_set(&CFG, "pwr.24v", "true"), MK_CFG_OK, "true");
    CHECK(ITEMS[2].cur.u == 1u, "true 가 1 로 들어간다");
    CHECK_R(mk_cfg_set(&CFG, "pwr.24v", "false"), MK_CFG_OK, "false");
    CHECK_R(mk_cfg_set(&CFG, "pwr.24v", "1"), MK_CFG_OK, "1 도 받는다");
    CHECK_R(mk_cfg_set(&CFG, "pwr.24v", "yes"), MK_CFG_RANGE, "yes 는 안 받는다");
    CHECK_R(mk_cfg_set(&CFG, "pwr.24v", ""), MK_CFG_RANGE, "빈 값");
}

static void test_integer_range(void)
{
    setup();
    CHECK_R(mk_cfg_set(&CFG, "tx.period_ms", "250"), MK_CFG_OK, "범위 안");
    CHECK_R(mk_cfg_set(&CFG, "tx.period_ms", "10"), MK_CFG_OK, "최소 경계");
    CHECK_R(mk_cfg_set(&CFG, "tx.period_ms", "10000"), MK_CFG_OK, "최대 경계");
    CHECK_R(mk_cfg_set(&CFG, "tx.period_ms", "9"), MK_CFG_RANGE, "최소 미만");
    CHECK_R(mk_cfg_set(&CFG, "tx.period_ms", "10001"), MK_CFG_RANGE, "최대 초과");
    CHECK_R(mk_cfg_set(&CFG, "tx.period_ms", "-5"), MK_CFG_RANGE, "음수");
    CHECK_R(mk_cfg_set(&CFG, "tx.period_ms", "12a"), MK_CFG_RANGE, "숫자 아님");
    CHECK_R(mk_cfg_set(&CFG, "tx.period_ms", " 250"), MK_CFG_RANGE, "앞 공백");
}

static void test_integer_overflow_is_rejected(void)
{
    /* 🔴 넘침을 검사하지 않으면 4294967296 이 0 이 되어 범위 검사를
     *    통과한다 — 사용자가 넣은 값과 저장된 값이 달라진다. */
    setup();
    CHECK_R(mk_cfg_set(&CFG, "tx.fields", "4294967295"), MK_CFG_OK, "U32 최대");
    CHECK(ITEMS[7].cur.u == 4294967295u, "U32 최대가 그대로 들어간다");
    CHECK_R(mk_cfg_set(&CFG, "tx.fields", "4294967296"), MK_CFG_RANGE,
            "U32 를 넘으면 거부");
    CHECK_R(mk_cfg_set(&CFG, "tx.fields", "99999999999999999999"),
            MK_CFG_RANGE, "훨씬 큰 값도 거부");
}

static void test_float(void)
{
    setup();
    CHECK_R(mk_cfg_set(&CFG, "ain0.zero", "4.25"), MK_CFG_OK, "실수");
    CHECK(ITEMS[4].cur.f > 4.24f && ITEMS[4].cur.f < 4.26f, "4.25 가 들어간다");
    CHECK_R(mk_cfg_set(&CFG, "ain0.zero", "0"), MK_CFG_OK, "정수 표기");
    CHECK_R(mk_cfg_set(&CFG, "ain0.zero", "25"), MK_CFG_OK, "최대 경계");
    CHECK_R(mk_cfg_set(&CFG, "ain0.zero", "25.1"), MK_CFG_RANGE, "최대 초과");
    CHECK_R(mk_cfg_set(&CFG, "ain0.zero", "-1"), MK_CFG_RANGE, "최소 미만");
    CHECK_R(mk_cfg_set(&CFG, "ain0.zero", "4.5.6"), MK_CFG_RANGE, "점 두 개");
    CHECK_R(mk_cfg_set(&CFG, "ain0.zero", "."), MK_CFG_RANGE, "점만");
    CHECK_R(mk_cfg_set(&CFG, "ain0.zero", "1e3"), MK_CFG_RANGE, "지수 표기는 안 받는다");
}

static void test_enum(void)
{
    setup();
    CHECK_R(mk_cfg_set(&CFG, "adc.drate", "1000"), MK_CFG_OK, "허용값");
    CHECK_R(mk_cfg_set(&CFG, "adc.drate", "999"), MK_CFG_RANGE, "허용값이 아님");
    CHECK_R(mk_cfg_set(&CFG, "adc.drate", "7500"), MK_CFG_OK, "마지막 허용값");
}

static void test_string(void)
{
    setup();
    CHECK_R(mk_cfg_set(&CFG, "dev.id", "boardA"), MK_CFG_OK, "문자열");
    CHECK_EQ(ITEMS[0].cur.s, "boardA", "문자열이 들어간다");
    CHECK_R(mk_cfg_set(&CFG, "dev.id", "0123456789ABCDE"), MK_CFG_OK, "15자");
    CHECK_R(mk_cfg_set(&CFG, "dev.id", "0123456789ABCDEF"), MK_CFG_RANGE, "16자");
}

static void test_string_rejects_protocol_delimiters(void)
{
    /* 🔴 값 하나가 줄 구조를 깨뜨리면 그 조각이 완결된 명령이 될 수 있다.
     *    호스트 쪽 framing.build_line 이 막는 것과 같은 부류다. */
    setup();
    CHECK_R(mk_cfg_set(&CFG, "dev.id", "a,b"), MK_CFG_RANGE, "쉼표 거부");
    CHECK_R(mk_cfg_set(&CFG, "dev.id", "a$b"), MK_CFG_RANGE, "$ 거부");
    CHECK_R(mk_cfg_set(&CFG, "dev.id", "a*b"), MK_CFG_RANGE, "* 거부");
    CHECK_R(mk_cfg_set(&CFG, "dev.id", "a\tb"), MK_CFG_RANGE, "TAB 거부");
    CHECK_R(mk_cfg_set(&CFG, "dev.id", "a\x7f" "b"), MK_CFG_RANGE, "DEL 거부");
    CHECK_EQ(ITEMS[0].cur.s, "1", "거부된 값은 저장되지 않는다");
}

/* ---- 출력 --------------------------------------------------------------- */

static void test_format(void)
{
    char buf[32];
    setup();

    mk_cfg_format(&ITEMS[2], buf, sizeof buf);
    CHECK_EQ(buf, "false", "bool false");
    ITEMS[2].cur.u = 1;
    mk_cfg_format(&ITEMS[2], buf, sizeof buf);
    CHECK_EQ(buf, "true", "bool true");

    mk_cfg_format(&ITEMS[1], buf, sizeof buf);
    CHECK_EQ(buf, "100", "정수");

    mk_cfg_format(&ITEMS[0], buf, sizeof buf);
    CHECK_EQ(buf, "1", "문자열");

    mk_cfg_format(&ITEMS[4], buf, sizeof buf);
    CHECK_EQ(buf, "4.0000", "실수는 소수 4자리");

    ITEMS[4].cur.f = 12.5f;
    mk_cfg_format(&ITEMS[4], buf, sizeof buf);
    CHECK_EQ(buf, "12.5000", "자릿수만큼 0 을 채운다");

    ITEMS[4].cur.f = 0.0625f;
    mk_cfg_format(&ITEMS[4], buf, sizeof buf);
    CHECK_EQ(buf, "0.0625", "소수부 앞자리 0");

    ITEMS[7].cur.u = 4294967295u;
    mk_cfg_format(&ITEMS[7], buf, sizeof buf);
    CHECK_EQ(buf, "4294967295", "U32 최대");
}

static void test_format_round_trip(void)
{
    /* 🔴 낸 값을 다시 넣으면 같은 값이어야 한다. 그렇지 않으면 호스트가
     *    전체 설정을 되읽어 쓸 때 값이 조금씩 흘러간다. */
    char buf[32];
    setup();
    const char *values[] = {"250", "10", "10000"};
    for (size_t i = 0; i < 3; i++) {
        mk_cfg_set(&CFG, "tx.period_ms", values[i]);
        mk_cfg_format(&ITEMS[1], buf, sizeof buf);
        CHECK_EQ(buf, values[i], "정수 왕복");
    }
    mk_cfg_set(&CFG, "ain0.zero", "4.2500");
    mk_cfg_format(&ITEMS[4], buf, sizeof buf);
    CHECK_EQ(buf, "4.2500", "실수 왕복");
}

static void test_format_rejects_small_buffer(void)
{
    char tiny[3];
    setup();
    CHECK(mk_cfg_format(&ITEMS[7], tiny, sizeof tiny) < 0, "버퍼가 작으면 실패");
}

/* ---- 살림 --------------------------------------------------------------- */

static void test_reset(void)
{
    setup();
    mk_cfg_set(&CFG, "tx.period_ms", "250");
    mk_cfg_set(&CFG, "dev.id", "x");
    mk_cfg_reset(&CFG);
    CHECK(ITEMS[1].cur.u == 100u, "정수가 기본값으로");
    CHECK_EQ(ITEMS[0].cur.s, "1", "문자열이 기본값으로");
    CHECK(mk_cfg_dirty(&CFG), "reset 도 저장이 필요하다");
}

static void test_dirty_tracking(void)
{
    /* 🔴 바뀐 것이 없는데 Flash 를 지웠다 쓰면 수명만 깎는다. */
    setup();
    CHECK(!mk_cfg_dirty(&CFG), "처음엔 깨끗하다");
    mk_cfg_set(&CFG, "tx.period_ms", "250");
    CHECK(mk_cfg_dirty(&CFG), "바꾸면 dirty");
    mk_cfg_mark_saved(&CFG);
    CHECK(!mk_cfg_dirty(&CFG), "저장하면 깨끗해진다");
    mk_cfg_set(&CFG, "tx.period_ms", "250");
    CHECK(!mk_cfg_dirty(&CFG), "같은 값을 다시 써도 dirty 아님");
}

static void test_reason_text(void)
{
    CHECK_EQ(mk_cfg_reason_text(MK_CFG_INTERLOCK), "INTERLOCK", "INTERLOCK");
    CHECK_EQ(mk_cfg_reason_text(MK_CFG_UNKNOWN_KEY), "UNKNOWN_KEY", "UNKNOWN_KEY");
    CHECK_EQ(mk_cfg_reason_text(MK_CFG_RANGE), "RANGE", "RANGE");
    CHECK_EQ(mk_cfg_reason_text(MK_CFG_READONLY), "READONLY", "READONLY");
    CHECK_EQ(mk_cfg_reason_text(MK_CFG_CAPACITY), "CAPACITY", "CAPACITY");
}

int main(void)
{
    printf("mk_config\n");
    test_unknown_key();
    test_range_is_checked_before_interlock();
    test_interlock_beats_readonly();
    test_plain_readonly();
    test_same_value_is_accepted();
    test_bool();
    test_integer_range();
    test_integer_overflow_is_rejected();
    test_float();
    test_enum();
    test_string();
    test_string_rejects_protocol_delimiters();
    test_format();
    test_format_round_trip();
    test_format_rejects_small_buffer();
    test_reset();
    test_dirty_tracking();
    test_reason_text();
    printf(failures ? "\nFAILED (%d)\n" : "\nPASSED\n", failures);
    return failures ? 1 : 0;
}
