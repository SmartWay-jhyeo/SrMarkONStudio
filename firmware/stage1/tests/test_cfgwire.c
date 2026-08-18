/* mk_cfgwire 단위 시험 + 카탈로그 덤프.
 *
 * `--catalog` 로 돌리면 $CFG,LIST 응답 본문을 그대로 찍는다.
 * crosscheck_cfg.py 가 그것을 host/core/config_schema.parse_catalog 로
 * 읽어 시뮬레이터의 카탈로그와 대조한다. */
#include <stdio.h>
#include <string.h>
#include "../app/mk_cfgwire.h"

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
    if (!(cond)) { printf("  FAIL %s\n", msg); failures++; }    \
    else         { printf("  ok   %s\n", msg); }                \
} while (0)

#define CHECK_HAS(hay, needle, msg) \
    CHECK(strstr((hay), (needle)) != NULL, msg)

static void put(char *dst, size_t cap, const char *src)
{
    size_t n = strlen(src);
    if (n + 1u > cap) { n = cap - 1u; }
    memcpy(dst, src, n);
    dst[n] = '\0';
}

static const uint32_t DRATE[] = {2, 5, 10, 15, 25, 30, 50, 60, 100, 500,
                                 1000, 2000, 3750, 7500};

static MkCfgItem ITEMS[6];
static MkConfig CFG;

/* 레일 명령 상태. 이제 설정표가 아니라 이것이 실린다. */
static MkRailState RS = { 1, 0, 1 };

static const MkFieldBit FIELDS[] = {
    { 0, "device_id",   0, "보드 식별자" },
    { 1, "time_source", 1, "시간 소스" },
    { 3, "raw",         1, "ADS1256 원시 카운트" },
};

static void setup(void)
{
    memset(ITEMS, 0, sizeof ITEMS);

    ITEMS[0] = (MkCfgItem){ .key = "dev.id", .group = "dev", .vtype = MK_VT_STR,
                            .max = 15, .has_max = 1, .label = "장치 ID" };
    put(ITEMS[0].def.s, sizeof ITEMS[0].def.s, "1");
    put(ITEMS[0].cur.s, sizeof ITEMS[0].cur.s, "1");

    ITEMS[1] = (MkCfgItem){ .key = "tx.period_ms", .group = "tx",
                            .vtype = MK_VT_U16, .min = 10, .max = 10000,
                            .has_min = 1, .has_max = 1,
                            .unit = "ms", .label = "전송 주기" };
    ITEMS[1].def.u = 100;
    ITEMS[1].cur.u = 100;

    ITEMS[2] = (MkCfgItem){ .key = "pwr.5v", .group = "pwr",
                            .vtype = MK_VT_BOOL, .readonly = 1, .interlocked = 1,
                            .label = "5V 전원",
                            .note = "쿨링 팬이 5V 전원에 직결이라 끌 수 없다" };
    ITEMS[2].def.u = 1;
    ITEMS[2].cur.u = 1;

    ITEMS[3] = (MkCfgItem){ .key = "adc.drate", .group = "adc",
                            .vtype = MK_VT_ENUM, .unit = "SPS",
                            .choices = DRATE, .n_choices = 14,
                            .label = "데이터율" };
    ITEMS[3].def.u = 60;
    ITEMS[3].cur.u = 60;

    /* 범위가 없는 실수 항목 — min·max 가 나가면 안 된다. */
    ITEMS[4] = (MkCfgItem){ .key = "ain0.zero", .group = "ain",
                            .vtype = MK_VT_F32, .unit = "mA",
                            .label = "J3 영점" };
    ITEMS[4].def.f = 4.0f;
    ITEMS[4].cur.f = 4.0f;

    /* 🔴 인터록만 있고 읽기 전용은 아닌 항목.
     *
     *    pwr.5v 는 둘 다 걸려 있어서 `ro` 를 readonly 로만 계산해도
     *    참이 나온다 — 그 항목만으로는 두 구현을 구분할 수 없다.
     *    되돌림 검사에서 실제로 못 잡는 것을 보고 이 항목을 넣었다. */
    ITEMS[5] = (MkCfgItem){ .key = "pwr.24v", .group = "pwr",
                            .vtype = MK_VT_BOOL, .interlocked = 1,
                            .label = "24V 전원",
                            .note = "RUN 모드에서는 바꿀 수 없다" };

    CFG.items = ITEMS;
    CFG.count = 6;
    CFG.dirty = 0;
}

/* ---- 모은 줄 ------------------------------------------------------------ */

#define CAP 32
static char LINES[CAP][400];
static int  N;

static void sink(void *ctx, const char *line, size_t len)
{
    (void)ctx;
    if (N >= CAP) return;
    size_t n = len < sizeof LINES[0] - 1u ? len : sizeof LINES[0] - 1u;
    memcpy(LINES[N], line, n);
    LINES[N][n] = '\0';
    N++;
}

static const char *find(const char *needle)
{
    for (int i = 0; i < N; i++) {
        if (strstr(LINES[i], needle)) return LINES[i];
    }
    return "";
}

static void run_list(void)
{
    N = 0;
    setup();
    mk_cfgwire_list(&CFG, FIELDS, 3, 0, sink, NULL);
}

/* ---- 시험 --------------------------------------------------------------- */

static void test_record_counts(void)
{
    run_list();
    CHECK(N == 6 + 3 + 1, "cfg_item 6 + cfg_field 3 + cfg_end 1");
}

static void test_cfg_end_count_is_the_sum(void)
{
    /* 🔴 수신측이 이것을 대조해 전송이 중간에 잘렸는지 판정한다(규격 §7.3).
     *    링크가 나쁠 때 절반만 온 카탈로그로 화면을 그리면 안 된다. */
    run_list();
    CHECK_HAS(find("cfg_end"), "\"count\":9", "count 는 item + field 합계");
}

static void test_common_fields(void)
{
    /* 🔴 한 줄만 보고 넘어가지 않는다. cfg_item·cfg_field·cfg_end 가
     *    각각 다른 코드 경로로 만들어지므로, 한 종류만 확인하면 나머지
     *    둘이 공통 필드를 빠뜨려도 모른다. */
    run_list();
    int missing_ver = 0, missing_seq = 0;
    for (int i = 0; i < N; i++) {
        if (strstr(LINES[i], "\"schema_ver\":3") == NULL) missing_ver++;
        /* 명령 응답의 seq 는 항상 0 (규격 §5.2). */
        if (strstr(LINES[i], "\"seq\":0") == NULL) missing_seq++;
    }
    CHECK(missing_ver == 0, "모든 줄에 schema_ver 3");
    CHECK(missing_seq == 0, "모든 줄에 seq 0");
}

static void test_bool_is_json_bool(void)
{
    /* 🔴 bool 을 1/0 으로 내면 호스트가 정수로 읽어 체크박스가 아니라
     *    숫자 입력이 된다. 카탈로그만으로 화면을 만들기 때문에 타입이
     *    곧 위젯이다. */
    run_list();
    const char *ln = find("pwr.5v");
    CHECK_HAS(ln, "\"vtype\":\"bool\"", "vtype bool");
    CHECK_HAS(ln, "\"default\":true", "default 가 참/거짓");
    CHECK_HAS(ln, "\"cur\":true", "cur 가 참/거짓");
}

static void test_interlocked_is_readonly_on_the_wire(void)
{
    /* 사용자가 못 바꾸는 것은 마찬가지다. 왜 못 바꾸는지는 note 가 말한다. */
    run_list();
    const char *ln = find("pwr.5v");
    CHECK_HAS(ln, "\"ro\":true", "인터록도 ro 로 나간다");

    /* 🔴 인터록만 있고 읽기 전용은 아닌 항목으로도 확인한다. pwr.5v 는
     *    둘 다 걸려 있어 `ro` 를 readonly 로만 계산해도 참이 나온다. */
    CHECK_HAS(find("pwr.24v"), "\"ro\":true", "인터록만 있어도 ro 다");
    CHECK_HAS(ln, "\"note\":\"쿨링 팬", "사유가 함께 나간다");
}

static void test_enum_carries_choices(void)
{
    /* 🔴 없으면 호스트가 콤보를 만들 수 없다(규격 §7.3). */
    run_list();
    const char *ln = find("adc.drate");
    CHECK_HAS(ln, "\"vtype\":\"enum\"", "vtype enum");
    CHECK_HAS(ln, "\"choices\":[2,5,10,15,25,30,50,60,100,500,1000,2000,3750,7500]",
              "허용값 배열");
}

static void test_range_only_when_it_exists(void)
{
    /* 🔴 없는 것을 0 으로 실어 보내면 화면이 "최소 0" 이라고 잘못 말한다. */
    run_list();
    CHECK_HAS(find("tx.period_ms"), "\"min\":10", "범위가 있으면 나간다");
    CHECK_HAS(find("tx.period_ms"), "\"max\":10000", "최대도 나간다");
    CHECK(strstr(find("ain0.zero"), "\"min\":") == NULL,
          "범위가 없으면 min 이 안 나간다");
    CHECK(strstr(find("ain0.zero"), "\"max\":") == NULL,
          "범위가 없으면 max 도 안 나간다");
}

static void test_unit_and_label(void)
{
    run_list();
    CHECK_HAS(find("tx.period_ms"), "\"unit\":\"ms\"", "단위");
    CHECK_HAS(find("tx.period_ms"), "\"label\":\"전송 주기\"", "라벨");
    CHECK(strstr(find("dev.id"), "\"unit\":") == NULL, "단위가 없으면 안 나간다");
    CHECK(strstr(find("dev.id"), "\"note\":") == NULL, "사유가 없으면 안 나간다");
}

static void test_field_bits(void)
{
    run_list();
    const char *ln = find("time_source");
    CHECK_HAS(ln, "\"type\":\"cfg_field\"", "cfg_field");
    CHECK_HAS(ln, "\"bit\":1", "비트 번호");
    CHECK_HAS(ln, "\"default\":true", "기본값이 참/거짓");
}

static void test_cfg_value(void)
{
    char buf[256];
    setup();

    int n = mk_cfgwire_value(&ITEMS[1], 1772200855875LL, buf, sizeof buf);
    CHECK(n > 0, "cfg_value 를 만든다");
    CHECK_HAS(buf, "\"type\":\"cfg_value\"", "type");
    CHECK_HAS(buf, "\"key\":\"tx.period_ms\"", "key");
    CHECK_HAS(buf, "\"cur\":100", "정수 cur");

    mk_cfgwire_value(&ITEMS[0], 0, buf, sizeof buf);
    CHECK_HAS(buf, "\"cur\":\"1\"", "문자열 cur 은 따옴표");

    mk_cfgwire_value(&ITEMS[2], 0, buf, sizeof buf);
    CHECK_HAS(buf, "\"cur\":true", "불리언 cur 은 참/거짓");
}

static void test_cfg_value_rejects_small_buffer(void)
{
    char tiny[16];
    setup();
    CHECK(mk_cfgwire_value(&ITEMS[1], 0, tiny, sizeof tiny) < 0,
          "버퍼가 작으면 실패하고 잘린 줄을 내지 않는다");
}

/* ---- $STAT (규격 §7.4) -------------------------------------------------- */

static void test_stat_shape(void)
{
    /* 🔴 din 세 항목이 늘면서 400 이 모자라졌다(레코드가 418자) — 잘리면
     *    mk_json 이 ok=0 으로 떨어져 이 시험이 아니라 다른 이유로 실패한
     *    것처럼 보인다. 넉넉히 512 로 둔다. */
    char buf[512];
    setup();
    ITEMS[5].cur.u = 1;                     /* pwr.24v 를 켠 것으로 */

    MkQueueStat q[2] = { {0, 3, 9, 0}, {1, 0, 1, 7} };
    MkDinState d[3] = { {18, 0}, {19, 0}, {20, 1} };
    int n = mk_cfgwire_stat(1772200855875LL, "CONFIG", "ACTIVE", "0.1.0", "2.0",
                            123456u, "device_clock", 0u, &RS, d, 3, q, 2,
                            buf, sizeof buf);
    CHECK(n > 0, "stat 을 만든다");
    CHECK_HAS(buf, "\"type\":\"stat\"", "type");
    CHECK_HAS(buf, "\"mode\":\"CONFIG\"", "mode");
    CHECK_HAS(buf, "\"time_source\":\"device_clock\"", "시간 소스");
    CHECK_HAS(buf, "\"time_quality\":0", "시간 품질");
    CHECK_HAS(buf, "\"uptime_ms\":123456", "uptime");
    CHECK_HAS(buf, "\"rails\":{\"v24\":true,\"v14v9\":false,\"v5\":true}",
              "rails 는 중첩 객체");
    CHECK_HAS(buf,
              "\"din\":[{\"connector_id\":18,\"state\":0},"
              "{\"connector_id\":19,\"state\":0},"
              "{\"connector_id\":20,\"state\":1}]",
              "din 은 객체 배열 (규격 §7.4 예시와 같은 모양)");
    CHECK_HAS(buf,
              "\"queues\":[{\"ch\":0,\"depth\":3,\"peak\":9,\"drops\":0},"
              "{\"ch\":1,\"depth\":0,\"peak\":1,\"drops\":7}]",
              "queues 는 객체 배열");
}

static void test_stat_with_no_queues(void)
{
    /* 🔴 3단계 전에는 큐가 없다. 없는 것을 있는 척하지 않는다 —
     *    빈 배열이라야 호스트가 "채널이 없다" 를 정확히 읽는다. */
    char buf[400];
    setup();
    int n = mk_cfgwire_stat(0, "RUN", "ACTIVE", "0.1.0", "2.0", 0,
                            "device_clock", 0u, &RS, NULL, 0, NULL, 0,
                            buf, sizeof buf);
    CHECK(n > 0, "큐가 없어도 만든다");
    CHECK_HAS(buf, "\"queues\":[]", "빈 배열");
}

static void test_stat_with_no_din(void)
{
    /* 🔴 sol 이 안 붙어 있는 경우와 같은 규칙 — queues 와 마찬가지로
     *    0 을 채워 보내지 않고 빈 배열이다. */
    char buf[400];
    setup();
    int n = mk_cfgwire_stat(0, "RUN", "ACTIVE", "0.1.0", "2.0", 0,
                            "device_clock", 0u, &RS, NULL, 0, NULL, 0,
                            buf, sizeof buf);
    CHECK(n > 0, "din 이 없어도 만든다");
    CHECK_HAS(buf, "\"din\":[]", "빈 배열");
}

static void test_queue_channel_comes_from_the_struct(void)
{
    /* 🔴 꺼진 채널은 목록에서 빠진다. 그때 배열 첨자를 채널 번호로 쓰면
     *    6번 채널의 유실이 0번 것으로 보고된다. 유실을 찾으려고 보는
     *    창구가 채널을 헷갈리면 없느니만 못하다. */
    char buf[400];
    setup();
    MkQueueStat q[2] = { {2, 0, 0, 0}, {6, 0, 0, 41} };
    mk_cfgwire_stat(0, "RUN", "ACTIVE", "0.1.0", "2.0", 0,
                    "device_clock", 0u, &RS, NULL, 0, q, 2, buf, sizeof buf);
    CHECK_HAS(buf,
              "\"queues\":[{\"ch\":2,\"depth\":0,\"peak\":0,\"drops\":0},"
              "{\"ch\":6,\"depth\":0,\"peak\":0,\"drops\":41}]",
              "채널 번호는 첨자가 아니라 구조체가 말한다");
}

static void test_missing_rail_reads_as_off(void)
{
    /* 🔴 레일 제어기가 없으면 전부 꺼진 것으로 보고한다. 설정표를 대신
     *    읽지 않는다.
     *
     *    예전에는 이 함수가 설정표에서 pwr.* 를 읽었다. 그래서 부팅 직후
     *    pwr.5v 의 기본값(true)이 그대로 나가, 핀은 0 인데 $STAT 이
     *    "5V ON" 이라고 말했다 — 실기기에서 확인했다(2026-08-14).
     *    설정은 "원하는 것", rails 는 "낸 것" 이다. */
    char buf[400];
    setup();
    mk_cfgwire_stat(0, "RUN", "ACTIVE", "0.1.0", "2.0", 0, "device_clock", 0u,
                    NULL, NULL, 0, NULL, 0, buf, sizeof buf);
    CHECK_HAS(buf, "\"rails\":{\"v24\":false,\"v14v9\":false,\"v5\":false}",
              "제어기가 없으면 전부 꺼진 것으로");

    /* 설정표가 5V 를 켜라고 해도, 아직 안 냈으면 꺼진 것으로 나간다. */
    MkCfgItem *v5 = mk_cfg_find(&CFG, "pwr.5v");
    if (v5 != NULL) { v5->cur.u = 1; }
    mk_cfgwire_stat(0, "RUN", "ACTIVE", "0.1.0", "2.0", 0, "device_clock", 0u,
                    NULL, NULL, 0, NULL, 0, buf, sizeof buf);
    CHECK_HAS(buf, "\"v5\":false",
              "설정이 ON 이어도 아직 안 냈으면 false — 설정표를 안 읽는다");
}

static void test_stat_rejects_small_buffer(void)
{
    char tiny[24];
    setup();
    CHECK(mk_cfgwire_stat(0, "RUN", "ACTIVE", "0.1.0", "2.0", 0, "device_clock",
                          0u, &RS, NULL, 0, NULL, 0, tiny, sizeof tiny) < 0,
          "버퍼가 작으면 실패하고 잘린 줄을 내지 않는다");
}

/* ---- 카탈로그 덤프 ------------------------------------------------------ */

static void dump_catalog(void)
{
    run_list();
    for (int i = 0; i < N; i++) {
        printf("%s\n", LINES[i]);
    }
}

int main(int argc, char **argv)
{
    if (argc > 1 && strcmp(argv[1], "--catalog") == 0) {
        dump_catalog();
        return 0;
    }
    printf("mk_cfgwire\n");
    test_record_counts();
    test_cfg_end_count_is_the_sum();
    test_common_fields();
    test_bool_is_json_bool();
    test_interlocked_is_readonly_on_the_wire();
    test_enum_carries_choices();
    test_range_only_when_it_exists();
    test_unit_and_label();
    test_field_bits();
    test_stat_shape();
    test_stat_with_no_queues();
    test_stat_with_no_din();
    test_queue_channel_comes_from_the_struct();
    test_missing_rail_reads_as_off();
    test_stat_rejects_small_buffer();
    test_cfg_value();
    test_cfg_value_rejects_small_buffer();
    printf(failures ? "\nFAILED (%d)\n" : "\nPASSED\n", failures);
    return failures ? 1 : 0;
}
