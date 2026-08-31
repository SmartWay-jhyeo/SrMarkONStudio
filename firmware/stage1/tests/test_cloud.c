/* mk_cloud 단위 시험 — J29(젯슨) 링크로 나가는 Cloud 스키마 직렬화.
 *
 * 계약: docs/데이터 스키마 명세서 v1.7.x. 선택 필드의 켬/끔은 본선과 같은
 * 마스크(tx.fields_*)가 정한다(사용자 확정 2026-08-22 — "기존 NDJSON 필드
 * 선택 화면이 곧 이 링크의 구성이다"). 보드도 크로스 툴체인도 필요 없다.
 */
#include <stdio.h>
#include <string.h>
#include "../app/mk_cloud.h"
#include "../app/mk_cfgtable.h"
#include "../app/mk_gnss.h"
#include "../app/mk_i2c.h"

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
    if (!(cond)) { printf("  FAIL %s\n", msg); failures++; }    \
    else         { printf("  ok   %s\n", msg); }                \
} while (0)

#define CHECK_HAS(hay, needle, msg) \
    CHECK((hay) != NULL && strstr((hay), (needle)) != NULL, msg)

/* ---- 모은 줄 ------------------------------------------------------------ */

#define CAP 32
static char LINES[CAP][512];
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

static const char *find_line(const char *type)
{
    char want[64];
    snprintf(want, sizeof want, "\"type\":\"%s\"", type);
    for (int k = 0; k < N; k++) {
        if (strstr(LINES[k], want)) { return LINES[k]; }
    }
    return NULL;
}

static MkConfig CFG;
static MkAds    ADS;
static MkCloud  C;
static MkSample BUF[4][8];

static void set_u32(const char *key, uint32_t v)
{
    MkCfgItem *it = mk_cfg_find(&CFG, key);
    if (it) { it->cur.u = v; }
}

static void set_f32(const char *key, float v)
{
    MkCfgItem *it = mk_cfg_find(&CFG, key);
    if (it) { it->cur.f = v; }
}

static void set_str(const char *key, const char *v)
{
    MkCfgItem *it = mk_cfg_find(&CFG, key);
    if (it) { snprintf(it->cur.s, sizeof it->cur.s, "%s", v); }
}

/* 필드 표에서 이름으로 비트값을 얻는다 — 비트 번호를 시험에 적지 않는다
 * (test_telem.c 의 test_field_mask_selects 와 같은 이유). */
static uint32_t bit_of(const char *name)
{
    size_t n = 0;
    const MkFieldBit *f = mk_cfgtable_fields(&n);
    for (size_t i = 0; i < n; i++) {
        if (strcmp(f[i].name, name) == 0) { return 1u << f[i].bit; }
    }
    return 0u;
}

static void mask_on(const char *mask_key, const char *field)
{
    MkCfgItem *it = mk_cfg_find(&CFG, mask_key);
    if (it) { it->cur.u |= bit_of(field); }
}

static void set_last(int ch, int64_t t_ms, int32_t raw)
{
    ADS.ch[ch].last.t_ms = t_ms;
    ADS.ch[ch].last.raw = raw;
    ADS.ch[ch].has_last = 1u;
}

static void setup(void)
{
    N = 0;
    mk_cfgtable_init(&CFG);
    memset(&ADS, 0, sizeof ADS);
    for (int ch = 0; ch < 4; ch++) {
        mk_ads_attach_queue(&ADS, ch, BUF[ch], 8);
        mk_ads_configure(&ADS, ch, 1, 100, 0);
    }
    mk_cloud_init(&C, &CFG, &ADS, "1", "0.1.0");
}

static void drain_capability(void)
{
    mk_cloud_tick(&C, 1, sink, NULL);
    N = 0;
}

/* ---- ain ---------------------------------------------------------------- */

static void test_ain_pressure_paint_record(void)
{
    setup();
    drain_capability();
    /* 🔴 타입은 사용자가 치는 문자열이다 (사용자 확정 2026-08-26 — 유량
     *    두 개를 flow_front 처럼 이름으로 가르기 위해). 값 필드 이름은
     *    단위 칸이 정한다. */
    set_str("ain0.cloud", "pressure_paint");
    set_str("ain0.unit", "bar");
    set_f32("ain0.zero", 4.0f);
    set_f32("ain0.scale", 9.375f);
    set_last(0, 1000, 4026531);

    mk_cloud_tick(&C, 100, sink, NULL);

    const char *ln = find_line("pressure_paint");
    CHECK(ln != NULL, "선택한 타입으로 나간다");
    CHECK_HAS(ln, "\"schema_ver\":1", "schema_ver 기본 1");
    CHECK_HAS(ln, "\"device_id\":\"1\"", "device_id = dev.id 기본값");
    CHECK_HAS(ln, "\"bar\":150.0", "(ma-zero)*scale, 소수 1자리");
    CHECK_HAS(ln, "\"time_source\":\"device_clock\"", "timeax 없으면 device_clock");
    CHECK(strstr(ln, "\"valve\":") == NULL, "밸브 비트 꺼짐 = 태깅 없음");
    /* 🔴 [개정 2026-08-31] seq 는 v1.7 개정으로 계약의 선택 필드가 됐다
     *    (HANDOFF_0831 결정 2 — 유실 검출을 두 링크 공통으로). 기본 켜짐. */
    CHECK_HAS(ln, "\"seq\":", "seq 가 기본으로 실린다 (tx.seq 기본 켜짐)");
}

static void test_common_field_values_are_config(void)
{
    /* 🔴 사용자 확정 2026-08-22 — 공통 필드 중 값을 바꿀 수 있는 것은
     *    스키마 버전과 장치 식별자다. 장치 식별자는 기존 dev.id 를 쓴다. */
    setup();
    drain_capability();
    set_u32("tx.schema_ver", 2u);
    MkCfgItem *dev = mk_cfg_find(&CFG, "dev.id");
    if (dev) { snprintf(dev->cur.s, sizeof dev->cur.s, "car-7"); }
    set_str("ain0.cloud", "flow");
    set_str("ain0.unit", "lpm");
    set_last(0, 1000, 4026531);

    mk_cloud_tick(&C, 100, sink, NULL);

    const char *ln = find_line("flow");
    CHECK_HAS(ln, "\"schema_ver\":2", "스키마 버전이 설정을 따른다");
    CHECK_HAS(ln, "\"device_id\":\"car-7\"", "장치 식별자 = dev.id");
}

static void test_ain_optional_fields_follow_the_mask(void)
{
    /* 🔴 선택 필드는 본선과 같은 마스크가 정한다 — 화면 하나로 양쪽이
     *    같이 움직인다(사용자 확정 2026-08-22). */
    setup();
    drain_capability();
    set_str("ain0.cloud", "pressure_paint");
    set_str("ain0.unit", "bar");
    set_last(0, 1000, 4026531);
    set_u32("tx.fields_ain", 0u);
    mask_on("tx.fields_ain", "ma");
    mask_on("tx.fields_ain", "raw");

    mk_cloud_tick(&C, 100, sink, NULL);

    const char *ln = find_line("pressure_paint");
    CHECK_HAS(ln, "\"ma\":", "ma 비트를 켜면 전류가 실린다");
    CHECK_HAS(ln, "\"raw\":4026531", "raw 비트를 켜면 원시 카운트");
}

static void test_ain_none_is_not_published(void)
{
    setup();
    drain_capability();                    /* ain0.cloud 기본 = 빈 문자열 */
    set_last(0, 1000, 4026531);
    CHECK(mk_cloud_tick(&C, 100, sink, NULL) == 0, "빈 타입 = 미발행 (계약 §16.6)");
}

static void test_ain_type_string_is_the_users(void)
{
    /* 🔴 이 기능의 존재 이유 — 유량계 두 대가 같은 "flow" 로 나가면
     *    젯슨에서 구분이 안 된다(실측 2026-08-26). 사용자가 친 문자열이
     *    그대로 type 이 된다. */
    setup();
    drain_capability();
    set_str("ain0.cloud", "flow_front");
    set_str("ain0.unit", "lpm");
    set_last(0, 1000, 4026531);
    mk_cloud_tick(&C, 100, sink, NULL);
    const char *ln = find_line("flow_front");
    CHECK(ln != NULL, "사용자 문자열이 그대로 type 으로 나간다");
    CHECK_HAS(ln, "\"lpm\":", "값 필드 이름은 단위 칸을 따른다");
}

static void test_ain_empty_unit_falls_back_to_value(void)
{
    setup();
    drain_capability();
    set_str("ain0.cloud", "flow_front");
    set_str("ain0.unit", "");
    set_last(0, 1000, 4026531);
    mk_cloud_tick(&C, 100, sink, NULL);
    CHECK_HAS(find_line("flow_front"), "\"value\":",
              "단위가 비면 값 필드는 value");
}

static void test_moving_the_sensor_is_a_config_change(void)
{
    setup();
    drain_capability();
    set_str("ain1.cloud", "pressure_paint");   /* J4 로 옮겼다 */
    set_str("ain1.unit", "bar");
    set_last(1, 1000, 4026531);
    mk_cloud_tick(&C, 100, sink, NULL);
    CHECK(find_line("pressure_paint") != NULL, "타입이 채널을 따라간다");
}

static void test_ain_last_repeats_at_tx_period(void)
{
    /* 🔴 [개정 2026-08-31] "같은 표본은 두 번 안 낸다"(설계 §4.7)는 폐기
     *    (HANDOFF_0831 결정 1·2). 수집이 송신보다 느린 채널은 큐가 비고,
     *    그때 last 가 tx.period_ms 마다 반복된다 — 본선 mk_telem 의 검증된
     *    규칙 그대로. 반복 줄의 t 는 획득 시각 그대로다(설계 원칙 2). */
    setup();
    set_str("ain0.cloud", "flow");
    set_str("ain0.unit", "lpm");
    drain_capability();
    set_last(0, 1000, 4026531);

    N = 0;
    CHECK(mk_cloud_tick(&C, 100, sink, NULL) == 1, "첫 주기에 1줄");
    CHECK(mk_cloud_tick(&C, 150, sink, NULL) == 0, "주기 전에는 침묵");
    CHECK(mk_cloud_tick(&C, 200, sink, NULL) == 1, "주기가 차면 last 를 반복");
    CHECK_HAS(LINES[N - 1], "\"t\":1000", "반복 줄의 t 는 획득 시각 그대로");
    CHECK_HAS(find_line("flow"), "\"lpm\":", "유량은 lpm 필드");
}

/* ---- ain 큐 드레인 (HANDOFF_0831 검토 1 — 세어지지 않는 유실 봉쇄) ------- */

static const char *find_line_with(const char *needle)
{
    for (int k = 0; k < N; k++) {
        if (strstr(LINES[k], needle)) { return LINES[k]; }
    }
    return NULL;
}

/* 실제 수집(mk_ads1256.c)은 큐 push 와 last 갱신을 함께 한다 — 그대로 흉내. */
static void push_ain(int ch, int64_t t_ms, int32_t raw)
{
    mk_queue_push(mk_ads_queue(&ADS, ch), t_ms, raw);
    set_last(ch, t_ms, raw);
}

static void test_ain_drains_queue_not_just_last(void)
{
    setup();
    set_str("ain0.cloud", "flow");
    set_str("ain0.unit", "lpm");
    drain_capability();
    set_u32("tx.period_ms", 10u);
    /* 틱 없이 표본 3개 — 슈퍼루프가 한눈판 상황. last 만 읽으면 마지막
     * 하나만 나가고 앞의 둘은 덮여 사라진다(어디에도 세어지지 않는다). */
    push_ain(0, 1000, 850000);
    push_ain(0, 1010, 850100);
    push_ain(0, 1020, 850200);
    N = 0;
    mk_cloud_tick(&C, 2000, sink, NULL);
    int flow_lines = 0;
    for (int k = 0; k < N; k++) {
        if (strstr(LINES[k], "\"type\":\"flow\"")) { flow_lines++; }
    }
    CHECK(flow_lines == 3, "표본 3개 = 줄 3개 (덮어쓰기 유실 없음)");
    CHECK(find_line_with("\"t\":1000") != NULL &&
          find_line_with("\"t\":1010") != NULL,
          "밀렸던 표본도 자기 획득 시각을 달고 나온다");
}

static void test_ain_round_robin_no_starvation(void)
{
    setup();
    set_str("ain0.cloud", "flow_a");
    set_str("ain0.unit", "lpm");
    set_str("ain1.cloud", "flow_b");
    set_str("ain1.unit", "lpm");
    drain_capability();
    set_u32("tx.period_ms", 10u);
    /* ch0 큐를 넘치게 채워도(깊이 8) ch1 이 같은 틱 안에 나와야 한다 —
     * 본선에서 실기기로 잡았던 기아 결함(688ce00)의 회귀 방지다. */
    for (int k = 0; k < 40; k++) { push_ain(0, 2000 + k, 850000 + k); }
    push_ain(1, 2000, 900000);
    N = 0;
    mk_cloud_tick(&C, 3000, sink, NULL);
    CHECK(find_line("flow_b") != NULL,
          "앞 채널이 밀려 있어도 뒤 채널이 같은 틱에 나온다");
}

/* ---- i2c ---------------------------------------------------------------- */

static MkI2c I2C;

static int fake_xfer_ok(void *ctx, uint8_t bus, uint8_t addr,
                        const uint8_t *tx, size_t ntx, uint8_t *rx, size_t nrx)
{
    (void)ctx; (void)bus; (void)addr; (void)tx; (void)ntx;
    for (size_t k = 0; k < nrx; k++) { rx[k] = (uint8_t)(k + 1u); }
    return 0;
}

static void setup_with_i2c(void)
{
    setup();
    static MkI2cIo io;
    io = (MkI2cIo){ fake_xfer_ok, NULL };
    mk_i2c_init(&I2C, &io);
    mk_cloud_attach_i2c(&C, &I2C);
    /* 🔴 표본은 drain **뒤에** 채운다 — 앞에 채우면 drain tick 이 표본까지
     *    소비해 버려("같은 표본은 두 번 안 낸다") 본 시험이 침묵을 본다. */
    drain_capability();
    /* AM2320(J13 = 포트 3): slot0 = 온도, slot1 = 습도 */
    I2C.last[3][0] = (MkI2cOut){ .connector_id = 13u, .quantity = "temp",
                                 .value = 23.5f, .have_value = 1,
                                 .status = 0u, .t_ms = 1000 };
    I2C.last[3][1] = (MkI2cOut){ .connector_id = 13u, .quantity = "humidity",
                                 .value = 42.6f, .have_value = 1,
                                 .status = 0u, .t_ms = 1000 };
    I2C.last_valid[3][0] = 1u;
    I2C.last_valid[3][1] = 1u;
}

static void test_i2c_values_flow_without_a_switch(void)
{
    /* 별도 발행 스위치가 없다(사용자 확정 2026-08-22) — 값이 오고 있으면
     * 나간다. */
    setup_with_i2c();
    mk_cloud_tick(&C, 100, sink, NULL);
    CHECK_HAS(find_line("temp_air"), "\"degc\":23.5", "온도 → temp_air");
    CHECK_HAS(find_line("humidity"), "\"pct\":42.6", "습도 → humidity");
}

static void test_dewpoint_is_computed_when_the_bit_is_on(void)
{
    setup_with_i2c();
    mask_on("tx.fields_i2c", "dewpoint");
    mk_cloud_tick(&C, 100, sink, NULL);
    /* 23.5°C·42.6% → Magnus 10.054°C (파이썬 독립 계산 2026-08-22) */
    const char *ln = find_line("humidity");
    CHECK_HAS(ln, "\"dewpoint_degc\":10.1", "이슬점이 계산돼 붙는다");
}

static void test_ambient_rides_on_temp_road(void)
{
    setup_with_i2c();
    /* MLX90614(J12 = 포트 2): 대상 + 주변 */
    I2C.last[2][0] = (MkI2cOut){ .connector_id = 12u, .quantity = "temp_object",
                                 .value = 42.5f, .have_value = 1,
                                 .status = 0u, .t_ms = 1000 };
    I2C.last[2][1] = (MkI2cOut){ .connector_id = 12u, .quantity = "temp_ambient",
                                 .value = 24.0f, .have_value = 1,
                                 .status = 0u, .t_ms = 1000 };
    I2C.last_valid[2][0] = 1u;
    I2C.last_valid[2][1] = 1u;
    mask_on("tx.fields_i2c", "temp_ambient");

    mk_cloud_tick(&C, 100, sink, NULL);

    const char *ln = find_line("temp_road");
    CHECK_HAS(ln, "\"degc\":42.5", "대상 온도가 본값");
    CHECK_HAS(ln, "\"ambient_degc\":24.0", "주변 온도는 선택 필드로 붙는다");
    CHECK(find_line("temp_ambient") == NULL,
          "주변 온도가 제 레코드로 나가지는 않는다");
}

static void test_ambient_off_means_no_field(void)
{
    setup_with_i2c();
    I2C.last[2][0] = (MkI2cOut){ .connector_id = 12u, .quantity = "temp_object",
                                 .value = 42.5f, .have_value = 1,
                                 .status = 0u, .t_ms = 1000 };
    I2C.last[2][1] = (MkI2cOut){ .connector_id = 12u, .quantity = "temp_ambient",
                                 .value = 24.0f, .have_value = 1,
                                 .status = 0u, .t_ms = 1000 };
    I2C.last_valid[2][0] = 1u;
    I2C.last_valid[2][1] = 1u;

    mk_cloud_tick(&C, 100, sink, NULL);
    CHECK(strstr(find_line("temp_road"), "ambient") == NULL,
          "비트 꺼짐 = 주변 온도 없음");
}

/* ---- valve -------------------------------------------------------------- */

static MkSolCtl SOL;

static void sol_set_confirmed(int ch, int on)
{
    SOL.confirmed_valid[ch] = 1u;
    SOL.confirmed_state[ch] = (uint8_t)(on ? 1 : 0);
}

static void setup_with_sol(void)
{
    setup();
    memset(&SOL, 0, sizeof SOL);
    mk_cloud_attach_sol(&C, &SOL);
    drain_capability();
}

static void test_din_cloud_string_emits_state_record(void)
{
    /* 🔴 [개정 2026-08-31, HANDOFF_0831 검토 5] din 도 ain 처럼 사용자
     *    문자열이 record type 이다. "OR 합성 valve 레코드" 는 폐기 —
     *    J20 에 "valve" 를 치면 기존 젯슨 수신분과 동일한 레코드가 나온다.
     *    (OR 은 gnss 등 태깅 소스로만 남는다 — 아래 태깅 시험.) */
    setup_with_sol();
    set_str("din20.cloud", "valve");
    sol_set_confirmed(2, 1);               /* ch2 = J20 확정 ON */
    N = 0;
    mk_cloud_tick(&C, 100, sink, NULL);
    const char *ln = find_line("valve");
    CHECK(ln != NULL, "din20.cloud 문자열이 type 이 된다");
    CHECK_HAS(ln, "\"state\":1", "state=1");
    N = 0;
    CHECK(mk_cloud_tick(&C, 200, sink, NULL) == 0,
          "상태가 안 바뀌면 다시 안 낸다");
    sol_set_confirmed(2, 0);
    N = 0;
    mk_cloud_tick(&C, 300, sink, NULL);
    CHECK_HAS(find_line("valve"), "\"state\":0", "확정 변화마다 한 줄");
}

static void test_din_channels_are_independent(void)
{
    setup_with_sol();
    set_str("din18.cloud", "valve_left");
    set_str("din20.cloud", "valve_right");
    sol_set_confirmed(0, 1);               /* J18 */
    sol_set_confirmed(2, 0);               /* J20 */
    N = 0;
    mk_cloud_tick(&C, 100, sink, NULL);
    CHECK_HAS(find_line("valve_left"), "\"state\":1", "J18 은 제 이름으로 1");
    CHECK_HAS(find_line("valve_right"), "\"state\":0", "J20 은 제 이름으로 0");
}

static void test_no_confirmed_inputs_stay_silent(void)
{
    setup_with_sol();
    set_str("din20.cloud", "valve");       /* 타입은 있지만 확정이 없다 */
    CHECK(mk_cloud_tick(&C, 100, sink, NULL) == 0,
          "모르는 상태를 0 이라 말하지 않는다");
    /* 빈 타입(기본)은 확정돼도 미발행 — ain 의 "없음 = 미발행" 과 같은 규칙 */
    setup_with_sol();
    sol_set_confirmed(2, 1);
    N = 0;
    CHECK(mk_cloud_tick(&C, 100, sink, NULL) == 0, "빈 타입 = 미발행");
}

static void test_valve_mask_bit_tags_records(void)
{
    setup_with_sol();
    sol_set_confirmed(2, 1);
    set_str("ain0.cloud", "pressure_paint");
    mask_on("tx.fields_ain", "valve");
    set_last(0, 1000, 4026531);

    mk_cloud_tick(&C, 100, sink, NULL);

    CHECK_HAS(find_line("pressure_paint"), "\"valve\":1",
              "밸브 비트를 켠 레코드에 상태가 붙는다");
}

/* ---- device_capability -------------------------------------------------- */

static void test_capability_is_first_and_reflects_config(void)
{
    setup();
    set_str("ain0.cloud", "pressure_paint");
    set_u32("i2c13.enabled", 1u);
    set_u32("i2c13.kind", 2u);            /* 온습도 */

    mk_cloud_tick(&C, 100, sink, NULL);

    const char *ln = find_line("device_capability");
    CHECK(ln != NULL, "첫 발행에 capability 가 있다");
    CHECK(ln == LINES[0], "capability 가 맨 앞이다 (계약 §16.1)");
    CHECK_HAS(ln, "\"pressure_paint\"", "ain 타입 선택이 sensors 에");
    CHECK_HAS(ln, "\"temp_air\"", "온습도 → temp_air");
    CHECK_HAS(ln, "\"humidity\"", "온습도 → humidity");
    CHECK_HAS(ln, "\"fw_version\":\"0.1.0\"", "펌웨어 버전 동봉");
    CHECK(strstr(ln, "\"valve\"") == NULL,
          "valve 는 sensors 에 안 넣는다 (계약 §16.3)");

    N = 0;
    CHECK(mk_cloud_tick(&C, 200, sink, NULL) == 0,
          "설정이 그대로면 재발행하지 않는다 — 부팅 1회");
}

static void test_capability_reemits_on_config_change(void)
{
    setup();
    drain_capability();
    set_str("ain1.cloud", "flow");         /* 유량 추가 */
    mk_cloud_tick(&C, 200, sink, NULL);
    CHECK_HAS(find_line("device_capability"), "\"flow\"",
              "설정이 바뀌면 즉시 재발행 (계약 §16.4)");
}

/* ---- gnss --------------------------------------------------------------- */

static MkGnss GNSS;

static void gnss_feed_str(const char *s)
{
    for (const char *p = s; *p; p++) { mk_gnss_feed(&GNSS, (uint8_t)*p); }
}

/* test_gnss.c 의 실기기 문장 쌍 — 같은 시각이라 GGA 값이 fix 에 짝지어진다.
 * GGA 13열(보정 나이)은 비어 있다 — 실기기(단독 측위)가 실제로 그렇다. */
#define REAL_GGA "$GNGGA,023115.00,3719.14416560,N,12720.43544199,E,1,20,1.2,100.8517,M,23.8989,M,,*70\r\n"
#define REAL_RMC "$GNRMC,023115.00,A,3719.14416560,N,12720.43544199,E,0.139,208.1,200826,8.6,W,A,C*5E\r\n"
/* RTK 흉내 — 13열 1.2 s, 14열 기준국 0421. 체크섬은 XOR 로 계산해 박았다. */
#define RTK_GGA "$GNGGA,023116.00,3719.14416560,N,12720.43544199,E,4,20,0.8,100.8517,M,23.8989,M,1.2,0421*57\r\n"
#define RTK_RMC "$GNRMC,023116.00,A,3719.14416560,N,12720.43544199,E,0.139,208.1,200826,8.6,W,A,C*5D\r\n"

static void setup_with_gnss(void)
{
    setup();
    mk_gnss_init(&GNSS);
    mk_cloud_attach_gnss(&C, &GNSS);
    set_u32("gnss.enabled", 1u);
    drain_capability();
}

static void test_gnss_record_matches_the_contract(void)
{
    setup_with_gnss();
    gnss_feed_str(REAL_GGA);
    gnss_feed_str(REAL_RMC);
    mk_cloud_tick(&C, 100, sink, NULL);

    const char *ln = find_line("gnss");
    CHECK(ln != NULL, "유효 fix 가 gnss 레코드로 나간다");
    CHECK_HAS(ln, "\"lat_e8\":3731906943", "1e-8 도 정수 그대로");
    CHECK_HAS(ln, "\"lon_e8\":12734059070", "경도도");
    CHECK_HAS(ln, "\"hdop_x100\":120", "1.2 -> 120");
    CHECK_HAS(ln, "\"cs\":\"70\"", "원 GGA 체크섬 보존");
    CHECK_HAS(ln, "\"valve\":0", "gnss 는 valve 태깅 필수 (계약 §3)");
    /* 기본 마스크에서 alt 는 켜져 있다(규격 §7.2 기본 on) — 100.8517 m */
    CHECK_HAS(ln, "\"alt\":100.85", "alt 비트(기본 켬)가 실린다");
    CHECK(strstr(ln, "\"diff_age\"") == NULL, "빈 13열은 안 실린다");

    N = 0;
    CHECK(mk_cloud_tick(&C, 200, sink, NULL) == 0, "새 fix 없으면 침묵");
}

static void test_gnss_rtk_extras_follow_the_mask(void)
{
    setup_with_gnss();
    mask_on("tx.fields_gnss", "diff_age");
    mask_on("tx.fields_gnss", "station_id");

    gnss_feed_str(RTK_GGA);
    gnss_feed_str(RTK_RMC);
    mk_cloud_tick(&C, 100, sink, NULL);

    const char *ln = find_line("gnss");
    CHECK_HAS(ln, "\"diff_age\":1.2", "보정 나이 (GGA 13열)");
    CHECK_HAS(ln, "\"station_id\":421", "기준국 (GGA 14열)");
    CHECK_HAS(ln, "\"fix\":4", "RTK fixed 품질");
}

static void test_gnss_disabled_is_silent(void)
{
    setup();
    mk_gnss_init(&GNSS);
    mk_cloud_attach_gnss(&C, &GNSS);   /* gnss.enabled 기본 = 꺼짐 */
    drain_capability();
    gnss_feed_str(REAL_GGA);
    gnss_feed_str(REAL_RMC);
    CHECK(mk_cloud_tick(&C, 100, sink, NULL) == 0, "꺼져 있으면 미발행");
}

/* ---- imu ---------------------------------------------------------------- */

static MkImu IMU;

#define IMU_WIRE \
    "#RAWIMUXA,COM1,0,60.0,FINE,2261,366772.050,0,0,0;" \
    "00,64,2261,366772.050,0ac00000,16278,-70,172,-1044,-90,-200" \
    "*bffb7522\r\n"

static void feed_imu(void)
{
    for (const char *p = IMU_WIRE; *p; p++) {
        mk_imu_feed(&IMU, (uint8_t)*p, 5000);
    }
}

static void test_imu_record_from_um981(void)
{
    setup();
    mk_imu_init(&IMU);
    mk_cloud_attach_imu(&C, &IMU);
    set_u32("gnss.imu", 1u);
    drain_capability();

    feed_imu();
    mk_cloud_tick(&C, 100, sink, NULL);

    const char *ln = find_line("imu");
    CHECK(ln != NULL, "RAWIMUX 가 imu 레코드로 나간다");
    CHECK_HAS(ln, "\"az\":0.994", "환산 2/32767 (거의 1 g)");
    CHECK_HAS(ln, "\"gz\":-7.96", "자이로 250/32767");
    CHECK(strstr(ln, "\"mx\":") == NULL, "지자기는 없다 — v1.7 완화");
    CHECK(strstr(ln, "\"degc\":") == NULL, "온도는 기본 꺼짐");
}

static void test_imu_temp_when_enabled(void)
{
    setup();
    mk_imu_init(&IMU);
    mk_cloud_attach_imu(&C, &IMU);
    set_u32("gnss.imu", 1u);
    mask_on("tx.fields_imu", "imu_temp");    /* 전송 탭의 IMU 필드 카드 */
    drain_capability();

    feed_imu();
    mk_cloud_tick(&C, 100, sink, NULL);

    /* status 0x0ac00000 -> bit21~31 = 0x056 = 86 -> 86*0.125+23 = 33.75 */
    CHECK_HAS(find_line("imu"), "\"degc\":33.8",
              "status 워드의 온도 (Table 2-15, x0.125+23)");
}

static void test_imu_off_is_silent(void)
{
    setup();
    mk_imu_init(&IMU);
    mk_cloud_attach_imu(&C, &IMU);     /* gnss.imu 기본 = 꺼짐 */
    drain_capability();
    IMU.last.valid = 1;
    IMU.last.seq = 1u;
    CHECK(mk_cloud_tick(&C, 100, sink, NULL) == 0, "꺼져 있으면 미발행");
}

/* ---- i2c 송신 주기 (HANDOFF_0831 결정 1 — 수집·송신 분리) ---------------- */

static void test_i2c_repeats_at_tx_period(void)
{
    /* 수집은 센서 되는 대로(온습도 2초), 송신은 캐시 최신값을 포트별
     * 송신 주기마다 반복 — "변수 a 에 수집된 값을 계속 넣고 송신할 때는
     * 변수 a 를 쓴다"(사용자, 2026-08-30). */
    setup_with_i2c();
    set_u32("i2c13.tx_period_ms", 100u);
    N = 0;
    mk_cloud_tick(&C, 100, sink, NULL);          /* 새 표본 → 즉시 발행 */
    int first = N;
    CHECK(first >= 2, "temp_air·humidity 가 즉시 나간다");
    mk_cloud_tick(&C, 150, sink, NULL);          /* 주기 미달 */
    CHECK(N == first, "주기 전에는 같은 표본을 반복하지 않는다");
    mk_cloud_tick(&C, 200, sink, NULL);          /* 100ms 도달 */
    CHECK(N > first, "주기가 차면 캐시 최신값이 반복된다");
    CHECK_HAS(LINES[N - 1], "\"t\":1000",
              "반복 줄의 t 는 획득 시각 그대로 (설계 원칙 2)");
}

/* ---- 시간축 (mk_telem 은퇴로 이식, 2026-08-31 — 원본 test_telem.c) ------- */

static void test_t_stays_boot_ms_on_device_clock_with_timeax(void)
{
    /* timeax 를 붙였어도 아직 아무 GNSS 신호가 없으면(device_clock) t 를
     * 손대지 않는다 — UTC 를 지어내지 않는다(설계 원칙 3·4). */
    setup();
    set_str("ain0.cloud", "flow");
    set_str("ain0.unit", "lpm");
    static MkTimeAx TX;
    mk_timeax_init(&TX);
    mk_cloud_attach_timeax(&C, &TX);
    drain_capability();
    set_last(0, 4242, 4026531);
    mk_cloud_tick(&C, 100, sink, NULL);
    const char *ln = find_line("flow");
    CHECK_HAS(ln, "\"t\":4242", "device_clock 이면 t 는 부팅 ms 그대로");
    CHECK_HAS(ln, "\"time_source\":\"device_clock\"", "등급도 그대로");
}

static void test_t_becomes_utc_once_gnss_pps_locks(void)
{
    setup();
    set_str("ain0.cloud", "flow");
    set_str("ain0.unit", "lpm");
    static MkTimeAx TX;
    mk_timeax_init(&TX);
    mk_cloud_attach_timeax(&C, &TX);
    drain_capability();

    mk_timeax_on_pps(&TX, 1000000ULL);             /* PPS @ dev_us=1.000s */
    MkGnssRmc rmc;
    memset(&rmc, 0, sizeof rmc);
    rmc.valid = 1;
    rmc.epoch_ms = 1772200855000LL;
    mk_timeax_on_rmc(&TX, &rmc, 1050000ULL);       /* 짝지어져 gnss_pps */

    set_last(0, 1500, 4026531);                    /* 획득: 부팅 1.5s */
    mk_cloud_tick(&C, 100, sink, NULL);

    const char *ln = find_line("flow");
    CHECK_HAS(ln, "\"t\":1772200855500",
              "t 는 획득 시각을 UTC 로 바꾼 값 — 송신 시각이 아니다");
    CHECK_HAS(ln, "\"time_source\":\"gnss\"",
              "계약 매핑 — gnss_pps 는 전선에서 gnss 다 (설계 §4.1)");
}

/* ---- seq (HANDOFF_0831 결정 2 — 유실 검출을 두 링크 공통으로) ------------ */

static void test_seq_increments_per_line(void)
{
    setup();
    /* 🔴 cloud 설정을 capability 비우기 **전에** 한다 — 뒤에 바꾸면 지문이
     *    달라져 capability 가 재발행되며 번호를 하나 더 소비한다. */
    set_str("ain0.cloud", "flow");
    set_str("ain0.unit", "lpm");
    drain_capability();                 /* capability 가 seq=0 을 쓴다 */
    set_last(0, 1000, 4026531);
    mk_cloud_tick(&C, 100, sink, NULL);
    set_last(0, 1010, 4026600);
    mk_cloud_tick(&C, 200, sink, NULL);   /* 다음 tx 주기 */
    CHECK(N >= 2, "두 줄이 나왔다");
    CHECK_HAS(LINES[0], "\"seq\":1", "capability 다음이라 첫 ain 줄은 seq=1");
    CHECK_HAS(LINES[1], "\"seq\":2", "둘째 줄은 seq=2 — 줄마다 1 증가");
}

static void test_seq_checkbox_off_omits_field(void)
{
    /* 🔴 사용자 결정 2026-08-31 — seq 는 체크박스(tx.seq, 기본 켜짐).
     *    끄면 필드가 빠지고, 카운터는 계속 올라 다시 켤 때 번호가 이어진다. */
    setup();
    set_str("ain0.cloud", "flow");
    set_str("ain0.unit", "lpm");
    drain_capability();                 /* seq=0 소비 — 지문은 이후 불변 */
    set_u32("tx.seq", 0u);
    set_last(0, 1000, 4026531);
    mk_cloud_tick(&C, 100, sink, NULL);
    CHECK(N >= 1 && strstr(LINES[0], "\"seq\"") == NULL,
          "tx.seq 를 끄면 seq 필드가 빠진다");
    set_u32("tx.seq", 1u);
    set_last(0, 1010, 4026600);
    mk_cloud_tick(&C, 200, sink, NULL);   /* 다음 tx 주기 */
    CHECK_HAS(LINES[N - 1], "\"seq\":2",
              "끔 동안에도 카운터는 올라 번호가 이어진다");
}

int main(void)
{
    setvbuf(stdout, NULL, _IONBF, 0);   /* 크래시 지점을 놓치지 않게 */
    test_ain_pressure_paint_record();
    test_common_field_values_are_config();
    test_ain_optional_fields_follow_the_mask();
    test_ain_none_is_not_published();
    test_ain_type_string_is_the_users();
    test_ain_empty_unit_falls_back_to_value();
    test_moving_the_sensor_is_a_config_change();
    test_ain_last_repeats_at_tx_period();
    test_ain_drains_queue_not_just_last();
    test_ain_round_robin_no_starvation();
    test_i2c_values_flow_without_a_switch();
    test_dewpoint_is_computed_when_the_bit_is_on();
    test_ambient_rides_on_temp_road();
    test_ambient_off_means_no_field();
    test_din_cloud_string_emits_state_record();
    test_din_channels_are_independent();
    test_no_confirmed_inputs_stay_silent();
    test_valve_mask_bit_tags_records();
    test_capability_is_first_and_reflects_config();
    test_capability_reemits_on_config_change();
    test_gnss_record_matches_the_contract();
    test_gnss_rtk_extras_follow_the_mask();
    test_gnss_disabled_is_silent();
    test_imu_record_from_um981();
    test_imu_temp_when_enabled();
    test_imu_off_is_silent();
    test_t_stays_boot_ms_on_device_clock_with_timeax();
    test_t_becomes_utc_once_gnss_pps_locks();
    test_seq_increments_per_line();
    test_seq_checkbox_off_omits_field();
    test_i2c_repeats_at_tx_period();

    if (failures) { printf("%d failure(s)\n", failures); return 1; }
    printf("all ok\n");
    return 0;
}
