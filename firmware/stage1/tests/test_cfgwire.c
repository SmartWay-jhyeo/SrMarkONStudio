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

/* 🔴 이 표는 mk_cfgtable.c 의 실제 FIELDS 와 무관한 시험용 6항목 표다 —
 *    mk_cfgwire 의 줄 조립 자체만 본다. `kinds` 는 이 파일의 시험이 보지
 *    않으므로 아무 레코드에도 안 속한다는 뜻으로 0 을 명시한다. */
static const MkFieldBit FIELDS[] = {
    { 0, "device_id",   0, "보드 식별자", 0 },
    { 1, "time_source", 1, "시간 소스",   0 },
    /* 🔴 [신규, 2026-08-19] raw 만 kinds 를 채운다 — test_field_bits_carry_
     *    records() 가 이 값으로 cfg_field 의 records 배열을 확인한다. */
    { 3, "raw",         1, "ADS1256 원시 카운트", MK_FIELD_AIN | MK_FIELD_I2C },
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

/* 🔴 [신규, 2026-08-19] `records` 가 규격 §7.3 개정대로 실리는지 본다 —
 *    호스트가 이것으로 마스크 카드를 ain·i2c·din 셋으로 나눠 그린다. */
static void test_field_bits_carry_records(void)
{
    run_list();
    const char *raw_ln = find("raw");
    CHECK_HAS(raw_ln, "\"records\":[\"ain\",\"i2c\"]",
              "raw 는 ain·i2c 두 레코드에 해당한다");
    /* device_id 는 kinds=0 으로 등록했다 — 빈 배열이어야 한다. */
    const char *id_ln = find("device_id");
    CHECK_HAS(id_ln, "\"records\":[]",
              "해당 레코드가 없으면 빈 배열이지 생략되지 않는다");
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

/* 🔴 [신규, 2026-08-19] `pps_raw_age_ms`·`pps_raw_count`·`pps_unpaired_reason`
 *    — "펄스가 오는가"(원시)와 "그 펄스를 시간축이 받아들였는가"(짝지어
 *    채택된 `pps_age_ms`)를 가른다. 실기기 관측(UM981, 실내·fix 없음):
 *    PPS 는 1초 간격으로 계속 들어왔는데(TIM8 CCR3·PC8 확인) RMC 가 전부
 *    무효(V)라 한 번도 짝지어지지 않아 `pps_age_ms` 가 계속 null 이었다 —
 *    배선·캡처는 멀쩡한데 "PPS 가 안 온다"로 보였다. 근거: mk_timeax.c. */
static void test_stat_shape(void)
{
    /* 🔴 din 세 항목이 늘면서 400 이 모자라졌다(레코드가 418자) — 잘리면
     *    mk_json 이 ok=0 으로 떨어져 이 시험이 아니라 다른 이유로 실패한
     *    것처럼 보인다. gnss.init_* 세 필드가 늘면서 512 도 모자라졌다
     *    (515자). pps_raw_* 세 필드가 늘며 600 도 모자라졌다 — 700 으로
     *    둔다. */
    char buf[820];
    setup();
    ITEMS[5].cur.u = 1;                     /* pwr.24v 를 켠 것으로 */

    MkQueueStat q[2] = { {0, 3, 9, 0}, {1, 0, 1, 7} };
    MkDinState d[3] = { {18, 0}, {19, 0}, {20, 1} };
    int n = mk_cfgwire_stat(1772200855875LL, "CONFIG", "ACTIVE", "0.1.0", "2.0",
                            123456u, "hse_pll", 64000000u,
                            "device_clock", 0u,
                            842, 842, 118u, NULL, 11,
                            1, 0, 1,
                            &RS, d, 3, q, 2, NULL,
                            buf, sizeof buf);
    CHECK(n > 0, "stat 을 만든다");
    CHECK_HAS(buf, "\"type\":\"stat\"", "type");
    CHECK_HAS(buf, "\"mode\":\"CONFIG\"", "mode");
    CHECK_HAS(buf, "\"time_source\":\"device_clock\"", "시간 소스");
    CHECK_HAS(buf, "\"time_quality\":0", "시간 품질");
    CHECK_HAS(buf, "\"uptime_ms\":123456", "uptime");
    CHECK_HAS(buf,
              "\"gnss\":{\"pps_age_ms\":842,\"pps_raw_age_ms\":842,"
              "\"pps_raw_count\":118,\"pps_unpaired_reason\":null,"
              "\"sats\":11,"
              "\"init_sent\":true,\"init_exhausted\":false,"
              "\"sentence_seen\":true}",
              "gnss 진단은 중첩 객체 — 짝지어진 나이 바로 옆에 원시 나이가 나란히 있다"
              "(규격 §7.4·§4.1.1)");
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
    char buf[620];
    setup();
    int n = mk_cfgwire_stat(0, "RUN", "ACTIVE", "0.1.0", "2.0", 0,
                            "hse_pll", 64000000u, "device_clock", 0u,
                            -1, -1, 0u, NULL, -1,
                            0, 0, 0,
                            &RS, NULL, 0, NULL, 0, NULL,
                            buf, sizeof buf);
    CHECK(n > 0, "큐가 없어도 만든다");
    CHECK_HAS(buf, "\"queues\":[]", "빈 배열");
    CHECK_HAS(buf,
              "\"gnss\":{\"pps_age_ms\":null,\"pps_raw_age_ms\":null,"
              "\"pps_raw_count\":0,\"pps_unpaired_reason\":null,"
              "\"sats\":null,"
              "\"init_sent\":false,\"init_exhausted\":false,"
              "\"sentence_seen\":false}",
              "GNSS 를 아예 안 붙였으면 -1 이 null 로 나간다 — 0 을 지어내지 않는다. "
              "pps_raw_count 는 카운터라 0 이 곧 '본 적 없음'이라 null 이 필요 없다");
}

/* 🔴 되돌림 검사 — 이 시험이 이번 작업의 핵심이다(작업 지시 원문). 실기기가
 *    실제로 겪은 상태를 그대로 만든다: 원시 펄스는 온다(원시 나이·카운트가
 *    값을 가짐)는데 짝짓기가 안 돼(`pps_age_ms` 는 null) 시간축이 못 받아
 *    들인 상태. 누가 `pps_age_ms` 와 `pps_raw_age_ms` 를 다시 하나로 합치면
 *    이 CHECK 가 바로 깨진다. */
static void test_stat_pps_raw_present_while_paired_age_is_null(void)
{
    char buf[720];
    setup();
    int n = mk_cfgwire_stat(0, "RUN", "ACTIVE", "0.1.0", "2.0", 0,
                            "hse_pll", 64000000u, "device_clock", 0u,
                            -1, 300, 7u, "no_valid_nmea", 3,
                            0, 0, 1,
                            &RS, NULL, 0, NULL, 0, NULL,
                            buf, sizeof buf);
    CHECK(n > 0, "원시만 있어도 만든다");
    CHECK_HAS(buf,
              "\"gnss\":{\"pps_age_ms\":null,\"pps_raw_age_ms\":300,"
              "\"pps_raw_count\":7,\"pps_unpaired_reason\":\"no_valid_nmea\","
              "\"sats\":3,",
              "펄스는 오는데(원시 나이·카운트) 짝짓기가 안 된(null) 상태 — "
              "실기기가 겪은 바로 그 상황(UM981, 실내·fix 없음)을 그대로 싣는다");
}

static void test_stat_with_no_din(void)
{
    /* 🔴 sol 이 안 붙어 있는 경우와 같은 규칙 — queues 와 마찬가지로
     *    0 을 채워 보내지 않고 빈 배열이다. */
    char buf[620];
    setup();
    int n = mk_cfgwire_stat(0, "RUN", "ACTIVE", "0.1.0", "2.0", 0,
                            "hse_pll", 64000000u, "device_clock", 0u,
                            -1, -1, 0u, NULL, -1,
                            0, 0, 0,
                            &RS, NULL, 0, NULL, 0, NULL,
                            buf, sizeof buf);
    CHECK(n > 0, "din 이 없어도 만든다");
    CHECK_HAS(buf, "\"din\":[]", "빈 배열");
}

static void test_queue_channel_comes_from_the_struct(void)
{
    /* 🔴 꺼진 채널은 목록에서 빠진다. 그때 배열 첨자를 채널 번호로 쓰면
     *    6번 채널의 유실이 0번 것으로 보고된다. 유실을 찾으려고 보는
     *    창구가 채널을 헷갈리면 없느니만 못하다.
     *
     *    gnss.init_* 세 필드가 늘면서 400 이 빠듯해졌다. pps_raw_* 세
     *    필드가 늘며 512 도 빠듯해졌다 — 600 으로 둔다. */
    char buf[720];
    setup();
    MkQueueStat q[2] = { {2, 0, 0, 0}, {6, 0, 0, 41} };
    mk_cfgwire_stat(0, "RUN", "ACTIVE", "0.1.0", "2.0", 0,
                    "hse_pll", 64000000u, "device_clock", 0u,
                    -1, -1, 0u, NULL, -1,
                    0, 0, 0,
                    &RS, NULL, 0, q, 2, NULL, buf, sizeof buf);
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
    char buf[620];
    setup();
    mk_cfgwire_stat(0, "RUN", "ACTIVE", "0.1.0", "2.0", 0, "hse_pll", 64000000u, "device_clock", 0u,
                    -1, -1, 0u, NULL, -1,
                    0, 0, 0, NULL, NULL, 0, NULL, 0, NULL, buf, sizeof buf);
    CHECK_HAS(buf, "\"rails\":{\"v24\":false,\"v14v9\":false,\"v5\":false}",
              "제어기가 없으면 전부 꺼진 것으로");

    /* 설정표가 5V 를 켜라고 해도, 아직 안 냈으면 꺼진 것으로 나간다. */
    MkCfgItem *v5 = mk_cfg_find(&CFG, "pwr.5v");
    if (v5 != NULL) { v5->cur.u = 1; }
    mk_cfgwire_stat(0, "RUN", "ACTIVE", "0.1.0", "2.0", 0, "hse_pll", 64000000u, "device_clock", 0u,
                    -1, -1, 0u, NULL, -1,
                    0, 0, 0, NULL, NULL, 0, NULL, 0, NULL, buf, sizeof buf);
    CHECK_HAS(buf, "\"v5\":false",
              "설정이 ON 이어도 아직 안 냈으면 false — 설정표를 안 읽는다");
}

/* 🔴 화면 회복 계수기 (규격 §7.4).
 *
 *    실기기에서 "노이즈 타면 픽셀이 다 깨지는데?" 가 나온 뒤 회복 장치를
 *    넣었다. 그러면 **몇 번 깨졌고 몇 번 되살렸는지**가 밖에서 보여야
 *    한다 — 그 수가 없으면 문제가 해결됐는지 덮였는지 아무도 모른다.
 *    PPS 의 `pps_raw_count` 와 같은 이유다. */
static void test_stat_carries_the_lcd_recovery_counters(void)
{
    char buf[820];
    setup();
    MkLcdStat ls = { 3u, 2u, 41u, 128u, 2u, 0u, 1 };
    int n = mk_cfgwire_stat(0, "RUN", "ACTIVE", "0.1.0", "2.0", 0,
                            "hse_pll", 64000000u, "device_clock", 0u,
                            -1, -1, 0u, NULL, -1,
                            0, 0, 0,
                            &RS, NULL, 0, NULL, 0, &ls,
                            buf, sizeof buf);
    CHECK(n > 0, "계수기를 실어도 만든다");
    CHECK_HAS(buf,
              "\"lcd\":{\"epoch\":3,\"reinit\":2,\"redraw\":41,"
              "\"verify_ok\":128,\"verify_fail\":2,\"rejected\":0,"
              "\"readback\":true}",
              "회복 계수기가 그대로 나간다");
}

/* 🔴 아직 한 번도 안 물어본 것과 "못 믿는다" 는 다르다.
 *
 *    되읽기 대조를 껐거나 아직 첫 대조 전이면 `readback` 은 null 이다.
 *    false 로 내보내면 화면이 "되읽기 안 됨" 이라고 단정하게 되고,
 *    사용자는 멀쩡한 배선을 뜯어 본다 (pps_age_ms 의 null 과 같은 결). */
static void test_unknown_readback_goes_out_as_null(void)
{
    char buf[820];
    setup();
    MkLcdStat ls = { 1u, 0u, 0u, 0u, 0u, 0u, -1 };
    mk_cfgwire_stat(0, "RUN", "ACTIVE", "0.1.0", "2.0", 0,
                    "hse_pll", 64000000u, "device_clock", 0u,
                    -1, -1, 0u, NULL, -1,
                    0, 0, 0,
                    &RS, NULL, 0, NULL, 0, &ls, buf, sizeof buf);
    CHECK_HAS(buf, "\"readback\":null",
              "안 물어봤으면 null 이다 — false 를 지어내지 않는다");

    /* 화면 자체가 안 붙은 빌드. */
    mk_cfgwire_stat(0, "RUN", "ACTIVE", "0.1.0", "2.0", 0,
                    "hse_pll", 64000000u, "device_clock", 0u,
                    -1, -1, 0u, NULL, -1,
                    0, 0, 0,
                    &RS, NULL, 0, NULL, 0, NULL, buf, sizeof buf);
    CHECK_HAS(buf,
              "\"lcd\":{\"epoch\":0,\"reinit\":0,\"redraw\":0,"
              "\"verify_ok\":0,\"verify_fail\":0,\"rejected\":0,"
              "\"readback\":null}",
              "화면을 안 붙였으면 전부 0 · readback 은 null");
}

/* 🔴 클럭 출처는 시간축 신뢰도의 일부다(규격 §7.4).
 *
 *    크리스털이 안 떠서 내부 RC 로 폴백하면 초 안쪽 보간이 ±1 % 까지
 *    흔들린다 — 1초 끝에서 10 ms 이고, 이 시스템이 노리는 분해능 전체와
 *    같은 크기다. 호스트가 그것을 모르고 저장하면 안 된다.
 *
 *    그러니 폴백한 보드는 **말할 수 있어야 한다.** 이 시험이 그 경로를
 *    지킨다 — 누가 `clock` 을 빼거나 "hse_pll" 로 못박으면 여기서 깨진다. */
static void test_stat_carries_the_clock_source(void)
{
    char buf[820];
    setup();
    mk_cfgwire_stat(0, "RUN", "ACTIVE", "0.1.0", "2.0", 0,
                    "hse_pll", 64000000u, "gnss_pps", 2u,
                    -1, -1, 0u, NULL, -1,
                    0, 0, 0,
                    &RS, NULL, 0, NULL, 0, NULL, buf, sizeof buf);
    CHECK_HAS(buf, "\"clock\":{\"src\":\"hse_pll\",\"sysclk_hz\":64000000}",
              "크리스털로 돌면 그렇게 말한다");

    /* 🔴 폴백했어도 `time_quality` 는 그대로 2 다. 낮추면 호스트가
     *    "PPS 를 못 쓴다" 로 읽어 멀쩡한 절대 시각까지 버린다 — 두 사실은
     *    다른 축이고, 한 숫자로 합치면 어느 쪽이 나빠졌는지 되물을 방법이
     *    없어진다(규격 §7.4). */
    mk_cfgwire_stat(0, "RUN", "ACTIVE", "0.1.0", "2.0", 0,
                    "hsi", 64000000u, "gnss_pps", 2u,
                    -1, -1, 0u, NULL, -1,
                    0, 0, 0,
                    &RS, NULL, 0, NULL, 0, NULL, buf, sizeof buf);
    CHECK_HAS(buf, "\"clock\":{\"src\":\"hsi\",\"sysclk_hz\":64000000}",
              "폴백했으면 그것도 그대로 말한다");
    CHECK_HAS(buf, "\"time_quality\":2",
              "폴백은 time_quality 를 건드리지 않는다 — 다른 축이다");
}

/* 클럭을 안 붙인 빌드(그리고 시뮬레이터)는 둘 다 null 이다.
 *
 * 🔴 0 을 지어내면 "클럭이 0 Hz" 라는 말이 되고, "hsi" 를 지어내면 있지도
 *    않은 폴백을 보고하는 것이 된다. lcd 의 readback=null 과 같은 결이다. */
static void test_missing_clock_goes_out_as_null(void)
{
    char buf[820];
    setup();
    mk_cfgwire_stat(0, "RUN", "ACTIVE", "0.1.0", "2.0", 0,
                    NULL, 0u, "device_clock", 0u,
                    -1, -1, 0u, NULL, -1,
                    0, 0, 0,
                    &RS, NULL, 0, NULL, 0, NULL, buf, sizeof buf);
    CHECK_HAS(buf, "\"clock\":{\"src\":null,\"sysclk_hz\":null}",
              "답할 수 없으면 null — 값을 지어내지 않는다");
}

static void test_stat_rejects_small_buffer(void)
{
    char tiny[24];
    setup();
    CHECK(mk_cfgwire_stat(0, "RUN", "ACTIVE", "0.1.0", "2.0", 0, "hse_pll", 64000000u,
                          "device_clock",
                          0u, -1, -1, 0u, NULL, -1, 0, 0, 0, &RS, NULL, 0, NULL, 0,
                          NULL, tiny, sizeof tiny) < 0,
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
    test_field_bits_carry_records();
    test_stat_shape();
    test_stat_with_no_queues();
    test_stat_pps_raw_present_while_paired_age_is_null();
    test_stat_with_no_din();
    test_queue_channel_comes_from_the_struct();
    test_missing_rail_reads_as_off();
    test_stat_carries_the_lcd_recovery_counters();
    test_stat_carries_the_clock_source();
    test_missing_clock_goes_out_as_null();
    test_unknown_readback_goes_out_as_null();
    test_stat_rejects_small_buffer();
    test_cfg_value();
    test_cfg_value_rejects_small_buffer();
    printf(failures ? "\nFAILED (%d)\n" : "\nPASSED\n", failures);
    return failures ? 1 : 0;
}
