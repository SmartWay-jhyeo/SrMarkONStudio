/* mk_telem 단위 시험. 보드도 크로스 툴체인도 필요 없다.
 *
 * `--emit` 로 돌리면 만들어진 레코드를 그대로 찍는다.
 * crosscheck_telem.py 가 그것을 시뮬레이터의 레코드와 대조한다. */
#include <stdio.h>
#include <string.h>
#include "../app/mk_telem.h"
#include "../app/mk_cfgtable.h"
#include "../app/mk_i2c.h"
#include "../app/mk_i2c_bh1750.h"

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
    if (!(cond)) { printf("  FAIL %s\n", msg); failures++; }    \
    else         { printf("  ok   %s\n", msg); }                \
} while (0)

#define CHECK_HAS(hay, needle, msg) \
    CHECK(strstr((hay), (needle)) != NULL, msg)

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

static MkConfig CFG;
static MkAds    ADS;
static MkTelem  T;
static MkSample BUF[4][8];

static void set_u32(const char *key, uint32_t v)
{
    MkCfgItem *it = mk_cfg_find(&CFG, key);
    if (it) { it->cur.u = v; }
}

/* 🔴 [2026-08-19] 수집과 송신을 뗀 뒤(사용자 설계) mk_telem 은 더 이상
 *    mk_queue 를 비우지 않는다 — MkAdsChannel.last 만 읽는다. 큐(mk_queue_
 *    push)는 이제 이 파일의 시험 목적("송신이 무엇을 보는가")과 무관하다.
 *    ADS 는 불투명 구조체가 아니므로(test_ads1256.c 도 이렇게 한다) 시험이
 *    "수집이 방금 끝났다"를 흉내 내려면 이 자리를 직접 채운다 — mk_i2c 의
 *    시험이 MkI2c.last[][] 를 직접 채우는 것과 같은 방식이다. */
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
    size_t n_fields = 0;
    const MkFieldBit *fields = mk_cfgtable_fields(&n_fields);
    mk_telem_init(&T, &CFG, &ADS, fields, n_fields, "1");
}

/* ---- I2C 가짜 버스·헬퍼 --------------------------------------------------- */

static int fake_xfer_ok(void *ctx, uint8_t bus, uint8_t addr,
                        const uint8_t *tx, size_t ntx, uint8_t *rx, size_t nrx)
{
    (void)ctx; (void)bus; (void)addr; (void)tx; (void)ntx;
    for (size_t k = 0; k < nrx; k++) { rx[k] = (uint8_t)(k + 1u); }
    return 0;
}

static int fake_xfer_nak(void *ctx, uint8_t bus, uint8_t addr,
                         const uint8_t *tx, size_t ntx, uint8_t *rx, size_t nrx)
{
    (void)ctx; (void)bus; (void)addr; (void)tx; (void)ntx; (void)rx; (void)nrx;
    return -1;
}

static void enable_lux_port_in(MkConfig *cfg, unsigned jack)
{
    char key[20];
    int n;
    #define K(sfx) do {                                             \
        n = 0; key[n++]='i'; key[n++]='2'; key[n++]='c';            \
        key[n++]=(char)('0'+jack/10u); key[n++]=(char)('0'+jack%10u);\
        for (const char *q=(sfx); *q; q++) { key[n++]=*q; }         \
        key[n]='\0';                                                \
    } while (0)
    MkCfgItem *it;
    K(".enabled");   it = mk_cfg_find(cfg, key); if (it) it->cur.u = 1u;
    K(".kind");      it = mk_cfg_find(cfg, key); if (it) it->cur.u = 1u;
    K(".addr");      it = mk_cfg_find(cfg, key); if (it) it->cur.u = 0x23u;
    K(".period_ms"); it = mk_cfg_find(cfg, key); if (it) it->cur.u = 200u;
    #undef K
}

static uint32_t parse_seq(const char *line)
{
    const char *p = strstr(line, "\"seq\":");
    if (p == NULL) { return 0u; }
    p += 6;
    uint32_t v = 0u;
    while (*p >= '0' && *p <= '9') { v = v * 10u + (uint32_t)(*p - '0'); p++; }
    return v;
}

/* ---- 환산 --------------------------------------------------------------- */

static void test_raw_to_ma_matches_the_shunt(void)
{
    /* 120 Ω 0.1% 션트, 외부 기준 2.5 V(ADR4525), 단일단, PGA=1.
     *
     * 🔴 만재는 VREF 가 아니라 **2·VREF** 다.
     *
     *      ADS1256.pdf p.11: "full-scale input range is ±2VREF (for PGA = 1)"
     *      ADS1256.pdf p.23: LSB = 2VREF/(PGA(2^23 − 1))
     *
     *    처음에 VREF 로 두어 실기기에서 4 mA 짜리 신호가 1.99 mA 로 나왔다
     *    [실증 2026-08-17]. 딱 절반이라 "센서가 이상한가" 로 보이기 쉽다.
     *
     *   20 mA -> 2.40 V -> 코드 2.40/5.0 x 8388607 = 4026531
     *    4 mA -> 0.48 V -> 코드 0.48/5.0 x 8388607 =  805306 */
    float ma20 = mk_telem_raw_to_ma(4026531);
    float ma4  = mk_telem_raw_to_ma(805306);
    CHECK(ma20 > 19.99f && ma20 < 20.01f, "만재 근처가 20 mA");
    CHECK(ma4 > 3.99f && ma4 < 4.01f, "살아 있는 0 점이 4 mA");
    CHECK(mk_telem_raw_to_ma(0) == 0.0f, "0 코드는 0 mA — 4 mA 미만은 단선");
}

/* ---- 주기 (수집·송신 분리 — 사용자 설계 2026-08-19) ---------------------
 *
 *   "수집은 수집대로 하고 송신은 내가 원하는 간격에 맞춰서 하는거지.
 *    변수 a 라는 곳에 수집된 값을 계속 넣고, 송신 할 때는 변수 a 를
 *    사용하면 어쨋든 그 안에 있던 값이 날라갈거 아니야"
 *
 *   set_last() 가 "변수 a 에 값을 넣는" 수집 쪽을 흉내 낸다. mk_telem_tick
 *   은 그 a 를 tx.period_ms 마다 읽기만 한다 — 큐(mk_queue)는 더 이상
 *   보지 않는다. */

static void test_nothing_before_the_period(void)
{
    /* 🔴 값이 있어도 주기 전에는 안 보낸다 — 안 그러면 `tx.period_ms` 가
     *    뜻을 잃는다. */
    setup();
    set_last(0, 1000, 4000000);
    CHECK(mk_telem_tick(&T, 50, sink, NULL) == 0, "주기 전에는 안 보낸다");
    CHECK(mk_telem_tick(&T, 100, sink, NULL) == 1, "주기가 되면 보낸다");
}

static void test_no_data_sends_nothing(void)
{
    /* 🔴 채널이 켜져 있어도 한 번도 수집되지 않았으면(set_last 를 안
     *    불렀으면) 0 을 지어내지 않는다 — 설계 원칙 3·4. */
    setup();
    CHECK(mk_telem_tick(&T, 100, sink, NULL) == 0, "값이 없으면 아무것도 안 나간다");
}

static void test_channel_with_no_data_is_never_sent_even_over_many_periods(void)
{
    /* 🔴 채널 3개 중 하나(ch1)는 끝까지 값을 못 받는다 — 다른 채널이
     *    여러 주기를 도는 동안에도 그 채널만은 한 줄도 나가면 안 된다. */
    setup();
    set_last(0, 1000, 4000000);
    /* ch1 은 절대 set_last() 를 안 부른다. */
    for (int64_t t = 100; t <= 500; t += 100) {
        mk_telem_tick(&T, t, sink, NULL);
    }
    for (int k = 0; k < N; k++) {
        CHECK(strstr(LINES[k], "\"connector_id\":4") == NULL,
              "ch1(J4)은 값이 없어 한 번도 안 나간다");
    }
    CHECK(N > 0, "그래도 ch0(J3)은 나간다 — 시험 자체가 헛돌지 않았다");
}

/* ---- 레코드 ------------------------------------------------------------- */

static void test_record_shape(void)
{
    setup();
    set_last(0, 1772200855875LL, 4026531);
    mk_telem_tick(&T, 100, sink, NULL);

    CHECK(N == 1, "한 줄");
    CHECK_HAS(LINES[0], "\"schema_ver\":3", "schema_ver");
    CHECK_HAS(LINES[0], "\"type\":\"ain\"", "type");
    CHECK_HAS(LINES[0], "\"t\":1772200855875", "획득 시각이 그대로 실린다");
    CHECK_HAS(LINES[0], "\"connector_id\":3", "채널 0 은 J3");
    CHECK_HAS(LINES[0], "\"raw\":4026531", "원본이 실린다");
    CHECK_HAS(LINES[0], "\"ma\":20.0", "전류 환산");
    CHECK(LINES[0][strlen(LINES[0]) - 1] == '\n', "줄바꿈으로 끝난다");
}

/* time_quality 비트는 기본 꺼짐(FIELDS 의 def=0)이다 — 켜서 시험한다.
 * 비트 번호는 표에서 끌어온다(test_field_mask_selects 와 같은 이유 —
 * 숫자를 박지 않는다). */
static void enable_time_quality_field(void)
{
    size_t n = 0;
    const MkFieldBit *f = mk_cfgtable_fields(&n);
    MkCfgItem *mask_item = mk_cfg_find(&CFG, "tx.fields");
    uint32_t mask = mask_item ? mask_item->cur.u : 0u;
    for (size_t i = 0; i < n; i++) {
        if (strcmp(f[i].name, "time_quality") == 0) {
            mask |= (1u << f[i].bit);
        }
    }
    set_u32("tx.fields", mask);
}

static void test_time_source_defaults_to_device_clock_without_timeax(void)
{
    /* 🔴 Phase 3 이전(또는 timeax 를 안 붙인 빌드)과 같은 동작 — 회귀
     * 방지. */
    setup();
    enable_time_quality_field();
    set_last(0, 1000, 4000000);
    mk_telem_tick(&T, 100, sink, NULL);
    CHECK_HAS(LINES[0], "\"time_source\":\"device_clock\"",
              "timeax 를 안 붙이면 device_clock 고정");
    CHECK_HAS(LINES[0], "\"time_quality\":0", "품질도 0 고정");
}

static void test_time_source_follows_attached_timeax_grade(void)
{
    setup();
    enable_time_quality_field();
    MkTimeAx tx;
    mk_timeax_init(&tx);
    mk_telem_attach_timeax(&T, &tx);

    mk_timeax_on_pps(&tx, 1000000ULL);
    MkGnssRmc rmc;
    memset(&rmc, 0, sizeof rmc);
    rmc.valid = 1;
    rmc.epoch_ms = 1772200855000LL;
    mk_timeax_on_rmc(&tx, &rmc, 1050000ULL);   /* PPS 와 짝지어 gnss_pps 로 */

    set_last(0, 1000, 4000000);
    mk_telem_tick(&T, 100, sink, NULL);

    CHECK_HAS(LINES[0], "\"time_source\":\"gnss_pps\"",
              "timeax 를 붙이면 실제 등급을 싣는다");
    CHECK_HAS(LINES[0], "\"time_quality\":2", "품질도 등급 숫자를 싣는다");
}

static void test_seq_increases_so_the_host_can_find_gaps(void)
{
    /* 규격 §7.1 — 호스트가 누락을 검출하는 유일한 근거다. 채널 셋이 한
     * 주기에 함께 나가면서 seq 가 1씩 오르는지 본다. */
    setup();
    set_last(0, 1000, 4000000);
    set_last(1, 1000, 4000000);
    set_last(2, 1000, 4000000);
    mk_telem_tick(&T, 100, sink, NULL);
    CHECK(N == 3, "세 채널이 함께 나간다");
    CHECK_HAS(LINES[0], "\"seq\":1", "seq 1");
    CHECK_HAS(LINES[1], "\"seq\":2", "seq 2");
    CHECK_HAS(LINES[2], "\"seq\":3", "seq 3");
}

static void test_repeats_the_last_value_without_new_acquisition(void)
{
    /* 🔴 이 시험이 이번 변경의 핵심이다. 수집이 없어도 주기마다 마지막
     *    값이 반복해서 나가고, `t` 는 **획득 시각 그대로**다(송신 시각으로
     *    바뀌면 안 된다) — 그래야 호스트가 "이 값이 몇 ms 묵었나"를 잰다. */
    setup();
    set_last(0, 1234, 4026531);           /* 딱 한 번만 수집됐다고 하자 */
    mk_telem_tick(&T, 100, sink, NULL);   /* 1회차 */
    mk_telem_tick(&T, 200, sink, NULL);   /* 2회차 — 새 수집 없음 */
    mk_telem_tick(&T, 300, sink, NULL);   /* 3회차 — 새 수집 없음 */

    CHECK(N == 3, "세 번 다 나간다 — 같은 값이라도 반복한다");
    for (int k = 0; k < N; k++) {
        CHECK_HAS(LINES[k], "\"raw\":4026531", "값이 그대로 반복된다");
        CHECK_HAS(LINES[k], "\"t\":1234",
                  "t 는 송신 시각(100·200·300)이 아니라 획득 시각(1234) 그대로다");
    }
}

static void test_only_the_latest_sample_survives_when_acquisition_outpaces_tx(void)
{
    /* 🔴 송신 사이에 여러 번 수집돼도(수집이 송신보다 빠르면) 중간 표본은
     *    조용히 버려지고 최신 값만 나간다 — 큐가 아니라 "변수 a" 이기
     *    때문이다. */
    setup();
    set_last(0, 111, 1000);   /* 중간 표본 — 버려져야 한다 */
    set_last(0, 222, 2000);
    set_last(0, 1234, 4026531);   /* 이것만 남아야 한다 */
    mk_telem_tick(&T, 100, sink, NULL);

    CHECK(N == 1, "채널당 한 줄");
    CHECK_HAS(LINES[0], "\"raw\":4026531", "최신 값만 나간다");
    CHECK_HAS(LINES[0], "\"t\":1234", "최신 획득 시각");
    CHECK(strstr(LINES[0], "\"raw\":1000") == NULL, "중간 표본 1000 은 안 보인다");
    CHECK(strstr(LINES[0], "\"raw\":2000") == NULL, "중간 표본 2000 도 안 보인다");
}

static void test_changing_tx_period_ms_changes_the_interval(void)
{
    /* 🔴 되돌림 관점의 반대쪽 — tx.period_ms 를 바꾸면 송신 간격이
     *    그대로 따라 바뀌는지 본다. */
    setup();
    set_u32("tx.period_ms", 50u);
    set_last(0, 1000, 4000000);

    CHECK(mk_telem_tick(&T, 40, sink, NULL) == 0, "50ms 미만이면 안 보낸다");
    CHECK(mk_telem_tick(&T, 50, sink, NULL) == 1, "50ms 가 되면 보낸다");
    CHECK(mk_telem_tick(&T, 90, sink, NULL) == 0, "다음 50ms(=100) 전에는 또 안 보낸다");
    CHECK(mk_telem_tick(&T, 100, sink, NULL) == 1, "100ms 에 다시 보낸다");
}

static void test_field_mask_selects(void)
{
    /* 🔴 비트 번호를 시험에 적지 않는다. 필드 표가 유일한 출처이고,
     *    여기에 숫자를 박으면 표를 고칠 때 시험이 거짓으로 통과한다. */
    setup();
    size_t n = 0;
    const MkFieldBit *f = mk_cfgtable_fields(&n);
    uint32_t only_ma = 0u;
    for (size_t i = 0; i < n; i++) {
        if (strcmp(f[i].name, "ma") == 0) { only_ma = (1u << f[i].bit); }
    }
    set_u32("tx.fields", only_ma);

    set_last(0, 1000, 4026531);
    mk_telem_tick(&T, 100, sink, NULL);

    CHECK_HAS(LINES[0], "\"ma\":", "켠 필드는 실린다");
    CHECK(strstr(LINES[0], "\"raw\":") == NULL, "끈 필드는 안 실린다");
    CHECK(strstr(LINES[0], "\"connector_id\":") == NULL, "끈 필드는 안 실린다");
    CHECK_HAS(LINES[0], "\"seq\":", "seq 는 마스크로 못 끈다 (규격 §7.1)");
    CHECK_HAS(LINES[0], "\"type\":\"ain\"", "type 도 마찬가지");
}

static void test_value_follows_the_spec_formula(void)
{
    /* 규격 §7.2.1 — value = (ma - zero) * scale.
     * 0~150 bar 센서: zero 4, scale 9.375 -> 20 mA 에서 150. */
    setup();
    MkCfgItem *z = mk_cfg_find(&CFG, "ain0.zero");
    MkCfgItem *s = mk_cfg_find(&CFG, "ain0.scale");
    z->cur.f = 4.0f;
    s->cur.f = 9.375f;

    set_last(0, 1000, 4026531);
    mk_telem_tick(&T, 100, sink, NULL);
    CHECK_HAS(LINES[0], "\"value\":150.0", "20 mA 가 150 bar 로");
}

static void test_float_digits_is_honoured(void)
{
    setup();
    set_u32("tx.float_digits", 2u);
    set_last(0, 1000, 4026531);
    mk_telem_tick(&T, 100, sink, NULL);
    CHECK(strstr(LINES[0], "\"ma\":20.00,") != NULL
          || strstr(LINES[0], "\"ma\":20.0,") != NULL,
          "자릿수 설정을 따른다");
}

/* ---- 격리 --------------------------------------------------------------- */

static void test_every_active_channel_sends_exactly_one_line_per_period(void)
{
    /* 🔴 큐를 비우지 않는 새 설계에서는 "밀린 채널이 나머지를 굶긴다"는
     *    상황 자체가 없다 — 채널마다 마지막 값 자리가 하나씩이라 매
     *    주기에 정확히 한 줄씩만 나간다. 세 채널 모두, 딱 한 줄씩. */
    setup();
    for (int ch = 0; ch < 3; ch++) {
        set_last(ch, 1000 + ch, 4000000);
    }
    mk_telem_tick(&T, 100, sink, NULL);

    int seen[3] = {0, 0, 0};
    for (int i = 0; i < N; i++) {
        for (int ch = 0; ch < 3; ch++) {
            char want[24];
            snprintf(want, sizeof want, "\"connector_id\":%d", ch + 3);
            if (strstr(LINES[i], want)) { seen[ch]++; }
        }
    }
    CHECK(N == 3, "채널마다 정확히 한 줄 — 셋이니 셋");
    CHECK(seen[0] == 1 && seen[1] == 1 && seen[2] == 1,
          "한 바퀴에 세 채널이 모두, 각각 한 번씩 나간다");
}

static void test_all_seven_channels_fit_in_one_tick(void)
{
    /* 🔴 예전의 "burst" 개념(밀린 표본을 나눠 쏟는 것)은 없어졌지만, 채널
     *    전부(7)+포트×양(최대 12)을 합쳐도 MK_TELEM_MAX_LINES(16) 안에
     *    드는지는 여전히 볼 값어치가 있다 — din 루프가 이 상수를 계속
     *    쓰기 때문에 값을 낮추면 여기서 먼저 걸린다. */
    setup();
    for (int ch = 0; ch < MK_ADS_CHANNELS; ch++) {
        mk_ads_configure(&ADS, ch, 1, 100, 0);
        set_last(ch, 1000 + ch, 4000000);
    }
    int sent = mk_telem_tick(&T, 100, sink, NULL);
    CHECK(sent == MK_ADS_CHANNELS, "일곱 채널 모두 한 번에 나간다");
    CHECK(sent <= MK_TELEM_MAX_LINES, "그래도 상한 안에 든다");
}

static void test_disabled_channel_is_silent(void)
{
    /* 설계 원칙 3 — 센서 미연결은 정상 상태다. 꺼진 채널을 싣지 않는다.
     * 값이 있어도(set_last) 꺼져 있으면 안 나간다 — 다른(켜진) 채널과
     * 함께 두어, "값이 없어서"가 아니라 "꺼져 있어서" 안 나가는 것임을
     * 분명히 한다. */
    setup();
    mk_ads_configure(&ADS, 1, 0, 100, 0);
    set_last(0, 500, 4000000);   /* 켜진 채널 — 이건 나가야 한다 */
    set_last(1, 1000, 4000000);  /* 꺼진 채널 — 값이 있어도 안 나가야 한다 */

    int sent = mk_telem_tick(&T, 100, sink, NULL);
    CHECK(sent == 1, "켜진 채널 하나만 나간다");
    for (int k = 0; k < N; k++) {
        CHECK(strstr(LINES[k], "\"connector_id\":4") == NULL,
              "꺼진 채널(J4)은 값이 있어도 안 나간다");
    }
}

/* ---- I2C --------------------------------------------------------------- */

/* 🔴 seq 는 레코드 종류를 가리지 않고 하나로 이어진다. 따로 세면 호스트의
 *    누락 검출이 무너진다 (규격 §7.1). */
static void test_i2c_records_share_the_sequence_with_ain(void)
{
    setup();
    static MkI2c I2C;
    MkI2cIo io = { fake_xfer_ok, NULL };     /* 아래에 정의. ctx 는 안 쓴다 */
    mk_i2c_init(&I2C, &io);
    mk_telem_attach_i2c(&T, &I2C);
    enable_lux_port_in(&CFG, 10u);
    /* 🔴 브리프 원문에는 없었지만 ain 쪽에 마지막 값이 없으면 한 줄도 안
     *    나가 "두 종류가 함께 나간다" 를 어떤 구현으로도 통과시킬 수 없다
     *    (실제로 이 줄 없이 돌려 FAIL 을 확인했다 — task-6-report.md 참고).
     *    seq 공유를 보는 시험이므로 ain 쪽에도 한 건은 있어야 한다. */
    set_last(0, 500, 4000000);

    for (int64_t t = 0; t <= 400; t += 10) {
        mk_i2c_tick(&I2C, &CFG, t);
        mk_telem_tick(&T, t, sink, NULL);
    }

    int ain = 0, i2c = 0;
    uint32_t last_seq = 0;
    int monotonic = 1;
    for (int k = 0; k < N; k++) {
        if (strstr(LINES[k], "\"type\":\"ain\"")) { ain++; }
        if (strstr(LINES[k], "\"type\":\"i2c\"")) { i2c++; }
        uint32_t seq = parse_seq(LINES[k]);   /* 아래에 정의 */
        if (k > 0 && seq != last_seq + 1u) { monotonic = 0; }
        last_seq = seq;
    }
    CHECK(ain > 0 && i2c > 0, "두 종류가 함께 나간다");
    CHECK(monotonic, "seq 가 종류를 가리지 않고 1씩 오른다");
}

/* 🔴 값이 없으면 null 이다. 그리고 [2026-08-19] 그 null 도 **마지막 값**이라
 *    tx.period_ms 마다 반복해서 나간다 — 죽은 센서가 죽었다는 사실을 계속
 *    말하는 것이지, 예전처럼 "재시도가 실제로 일어난 순간에만" 알리는 게
 *    아니다(규격 §7.5).
 *
 * 🔴 [검토 지적 C2 — 이름을 고쳤다] enable_lux_port_in 은 드라이버가
 *    있는 LUX 를 켜고 fake_xfer_nak 는 **첫 xfer(Power On)부터** 실패
 *    시키므로, 실제로 타는 것은 READY 의 read 실패가 아니라 START 의
 *    시작 실패 경로다.
 *
 *    enable_lux_port_in 이 고정하는 period_ms=200(i2c 재시도 간격)과
 *    tx.period_ms=100(기본값, 송신 간격)이 이제 서로 다른 일을 한다 —
 *    재시도는 실패를 새로 확인하는 주기, 송신은 마지막 값을 내보내는
 *    주기다. 0~400ms 를 10ms 간격으로 돌리면:
 *      t=10   첫 시도 실패 → last_valid 가 된다
 *      t=100,200,300,400  송신 주기마다 그때까지의 마지막 값을 낸다(4번)
 *      t=210  두 번째 재시도(여전히 실패) → t=300 송신부터 갱신된 값
 *    즉 송신 횟수(4)는 tx.period_ms 가 정하고, 그 안의 값은 i2c 재시도가
 *    정한다 — 두 주기가 분리됐다는 것 자체가 이 시험의 요지다. */
static void test_failure_repeats_at_the_tx_period_not_the_retry_period(void)
{
    setup();
    static MkI2c I2C;
    MkI2cIo io = { fake_xfer_nak, NULL };
    mk_i2c_init(&I2C, &io);
    mk_telem_attach_i2c(&T, &I2C);
    enable_lux_port_in(&CFG, 10u);

    for (int64_t t = 0; t <= 400; t += 10) {
        mk_i2c_tick(&I2C, &CFG, t);
        mk_telem_tick(&T, t, sink, NULL);
    }

    int found = 0;
    int n_i2c = 0;
    for (int k = 0; k < N; k++) {
        if (strstr(LINES[k], "\"type\":\"i2c\"") == NULL) { continue; }
        found = 1;
        n_i2c++;
        CHECK_HAS(LINES[k], "\"value\":null", "값 자리가 null");
        CHECK_HAS(LINES[k], "\"status\":1", "응답 없음은 status=1");
        CHECK(strstr(LINES[k], "\"unit\"") == NULL,
              "unit 을 싣지 않는다 (규격 §7.5)");
    }
    CHECK(found, "실패해도 레코드가 나간다");
    CHECK(n_i2c == 4,
          "송신은 tx.period_ms(100) 를 따른다 — 400ms 에 네 번(100·200·300·400), "
          "i2c 재시도(200ms)와는 별개다");
}

/* 🔴 [2026-08-19] mk_telem 은 out[]/mk_i2c_take() 없이 last[][] 만 읽는다
 *    — 그 배출 큐 자체가 mk_i2c.c 에서 걷어내졌다. 이 시험은 last[][] 를
 *    직접 채운다(포트 2(=J12), 슬롯 0·1 — 온습도 자리). mk_i2c.c 의
 *    store_last() 가 실제로 두 자리를 같이 채우는 것은 tests/test_i2c.c 의
 *    몫이고, 여기서는 mk_telem 이 last[][] 를 정확히 읽어 레코드로
 *    만드는지만 본다. */
static void test_reads_i2c_values_straight_from_the_last_value_cache(void)
{
    setup();
    static MkI2c I2C;
    MkI2cIo io = { fake_xfer_ok, NULL };
    mk_i2c_init(&I2C, &io);
    mk_telem_attach_i2c(&T, &I2C);

    I2C.last[2][0] = (MkI2cOut){ .connector_id = 12u, .quantity = "temp",
                                 .value = 20.0f, .have_value = 1,
                                 .status = 0u, .t_ms = 1000 };
    I2C.last_valid[2][0] = 1u;
    I2C.last[2][1] = (MkI2cOut){ .connector_id = 12u, .quantity = "humidity",
                                 .value = 55.0f, .have_value = 1,
                                 .status = 0u, .t_ms = 1000 };
    I2C.last_valid[2][1] = 1u;

    mk_telem_tick(&T, 100, sink, NULL);   /* 딱 한 번 */

    int i2c = 0;
    for (int k = 0; k < N; k++) {
        if (strstr(LINES[k], "\"type\":\"i2c\"")) { i2c++; }
    }
    CHECK(i2c == 2, "한 바퀴에 쌓인 i2c 레코드 둘이 한 번의 tick 으로 다 나간다");
}

/* ---- 디지털 입력 din (규격 §7.6) -----------------------------------------
 *
 * 🔴 mk_solctl_tick() 의 디바운스를 다시 거치지 않는다 — i2c 시험의
 *    emit_i2c() 와 같은 방식으로 MkSolCtl.out 에 직접 채운다. 여기서
 *    보는 것은 오직 mk_telem 이 그것을 어떻게 줄로 만드는가다. 디바운스
 *    자체는 tests/test_sol.c 의 몫이다. */

static void test_din_records_share_the_sequence_with_ain(void)
{
    setup();
    static MkSolCtl SOL;
    mk_solctl_init(&SOL, NULL, NULL);   /* 레벨 폴링 없음 — out 을 직접 채운다 */
    mk_telem_attach_sol(&T, &SOL);
    set_last(0, 500, 4000000);   /* ain 쪽에도 한 건 */

    SOL.out[0] = (MkSolOut){ .connector_id = 18u, .state = 1u, .t_ms = 1000 };
    SOL.n_out = 1;

    mk_telem_tick(&T, 100, sink, NULL);

    int ain = 0, din = 0;
    uint32_t last_seq = 0;
    int monotonic = 1;
    for (int k = 0; k < N; k++) {
        if (strstr(LINES[k], "\"type\":\"ain\"")) { ain++; }
        if (strstr(LINES[k], "\"type\":\"din\"")) { din++; }
        uint32_t seq = parse_seq(LINES[k]);
        if (k > 0 && seq != last_seq + 1u) { monotonic = 0; }
        last_seq = seq;
    }
    CHECK(ain > 0 && din > 0, "두 종류가 함께 나간다");
    CHECK(monotonic, "seq 가 종류를 가리지 않고 1씩 오른다");
}

static void test_din_record_shape(void)
{
    setup();
    static MkSolCtl SOL;
    mk_solctl_init(&SOL, NULL, NULL);   /* 레벨 폴링 없음 — out 을 직접 채운다 */
    mk_telem_attach_sol(&T, &SOL);

    SOL.out[0] = (MkSolOut){ .connector_id = 20u, .state = 0u, .t_ms = 4242 };
    SOL.n_out = 1;

    mk_telem_tick(&T, 100, sink, NULL);

    CHECK(N == 1, "레코드 하나가 나간다");
    if (N == 1) {
        CHECK_HAS(LINES[0], "\"type\":\"din\"", "type");
        CHECK_HAS(LINES[0], "\"t\":4242", "t 는 엣지를 잡은 시각(o->t_ms)");
        CHECK_HAS(LINES[0], "\"connector_id\":20", "connector_id");
        CHECK_HAS(LINES[0], "\"state\":0", "state");
    }
}

static void test_din_is_not_gated_by_tx_period(void)
{
    /* 🔴 i2c 와 같은 이유 — 상태 변화는 이벤트라 tx.period_ms 를 기다리면
     *    "언제 들어왔나" 가 다음 주기까지 묻힌다(규격 §7.6). 주기를 크게
     *    두고도 즉시 나가는지 본다. */
    setup();
    set_u32("tx.period_ms", 10000u);
    static MkSolCtl SOL;
    mk_solctl_init(&SOL, NULL, NULL);   /* 레벨 폴링 없음 — out 을 직접 채운다 */
    mk_telem_attach_sol(&T, &SOL);

    SOL.out[0] = (MkSolOut){ .connector_id = 19u, .state = 1u, .t_ms = 10 };
    SOL.n_out = 1;

    mk_telem_tick(&T, 10, sink, NULL);   /* 주기가 한참 남았다 */

    int din = 0;
    for (int k = 0; k < N; k++) {
        if (strstr(LINES[k], "\"type\":\"din\"")) { din++; }
    }
    CHECK(din == 1, "tx.period_ms 를 기다리지 않고 즉시 나간다");
}

/* ---- 카탈로그 덤프 ------------------------------------------------------ */

static void emit_samples(void)
{
    setup();
    /* 4 · 12 · 20 mA 에 해당하는 코드 (만재 2xVREF). 셋을 서로 다른
     * 송신 주기에 하나씩 "수집"해 세 줄을 얻는다 — 수집·송신이 갈린
     * 뒤로는(2026-08-19) 한 주기에는 채널당 마지막 값 한 줄뿐이다. */
    static const int32_t CODES[] = { 805306, 2415918, 4026531 };
    int64_t t = 100;
    for (size_t k = 0; k < sizeof CODES / sizeof *CODES; k++) {
        set_last(0, 1000 + (int64_t)k, CODES[k]);
        mk_telem_tick(&T, t, sink, NULL);
        t += 100;
    }
    for (int i = 0; i < N; i++) {
        fputs(LINES[i], stdout);
    }
}

/* crosscheck_i2c.py 가 대조하는 세 벡터. 상태기계를 거치지 않고 last[][] 에
 * 직접 채워 레코드 조립(build_i2c_record)만 시뮬레이터와 맞춘다 —
 * [2026-08-19] mk_telem 은 이제 out[] 이 아니라 last[][] 를 읽는다.
 *
 * 🔴 crosscheck_i2c.py 는 정확히 세 줄, seq 1·2·3 을 기대한다. 마지막 값은
 *    한 번 서면 계속 반복되므로(hold-and-send), 매번 last_valid 를 전부
 *    지우고 그 벡터 하나만 세워야 딱 한 줄만 나간다. */
static void emit_i2c(void)
{
    setup();
    static MkI2c I2C;
    MkI2cIo io = { fake_xfer_ok, NULL };
    mk_i2c_init(&I2C, &io);
    mk_telem_attach_i2c(&T, &I2C);

    /* 1) 정상값 — 커넥터 10 = 포트 0 */
    memset(I2C.last_valid, 0, sizeof I2C.last_valid);
    I2C.last[0][0] = (MkI2cOut){ .connector_id = 10u, .quantity = "lux",
                                 .value = 401.5f, .have_value = 1,
                                 .status = 0u, .t_ms = 1000 };
    I2C.last_valid[0][0] = 1u;
    mk_telem_tick(&T, 100, sink, NULL);

    /* 2) 응답 없음 — 커넥터 11 = 포트 1 */
    memset(I2C.last_valid, 0, sizeof I2C.last_valid);
    I2C.last[1][0] = (MkI2cOut){ .connector_id = 11u, .quantity = "",
                                 .value = 0.0f, .have_value = 0,
                                 .status = 1u, .t_ms = 2000 };
    I2C.last_valid[1][0] = 1u;
    mk_telem_tick(&T, 300, sink, NULL);

    /* 3) 지원 안 하는 종류 — 커넥터 12 = 포트 2 */
    memset(I2C.last_valid, 0, sizeof I2C.last_valid);
    I2C.last[2][0] = (MkI2cOut){ .connector_id = 12u, .quantity = "",
                                 .value = 0.0f, .have_value = 0,
                                 .status = 3u, .t_ms = 3000 };
    I2C.last_valid[2][0] = 1u;
    mk_telem_tick(&T, 500, sink, NULL);

    for (int i = 0; i < N; i++) {
        fputs(LINES[i], stdout);
    }
}

/* crosscheck_din.py 가 대조하는 세 벡터. 상태기계(디바운스)를 거치지 않고
 * MkSolCtl.out 에 직접 채워 레코드 조립(build_din_record)만 시뮬레이터와
 * 맞춘다 — emit_i2c() 와 같은 방식(검토 지적 I2: din 에는 crosscheck_i2c.py
 * 같은 바이트 대조가 없어서 필드 순서 일치가 양쪽 주석에 손으로 옮겨 적은
 * 문장뿐이었다).
 *
 * 벡터 셋 — 켜짐(state 1)·꺼짐(state 0)·세 커넥터(18·19·20)가 모두 나오게: */
static void emit_din(void)
{
    setup();
    static MkSolCtl SOL;
    mk_solctl_init(&SOL, NULL, NULL);   /* 레벨 폴링 없음 — out 을 직접 채운다 */
    mk_telem_attach_sol(&T, &SOL);

    /* 1) 켜짐, J18 */
    SOL.out[0] = (MkSolOut){ .connector_id = 18u, .state = 1u, .t_ms = 1000 };
    SOL.n_out = 1;
    mk_telem_tick(&T, 100, sink, NULL);

    /* 2) 꺼짐, J19 */
    SOL.out[0] = (MkSolOut){ .connector_id = 19u, .state = 0u, .t_ms = 2000 };
    SOL.n_out = 1;
    mk_telem_tick(&T, 300, sink, NULL);

    /* 3) 켜짐, J20 — 세 커넥터가 다 나오도록 */
    SOL.out[0] = (MkSolOut){ .connector_id = 20u, .state = 1u, .t_ms = 3000 };
    SOL.n_out = 1;
    mk_telem_tick(&T, 500, sink, NULL);

    for (int i = 0; i < N; i++) {
        fputs(LINES[i], stdout);
    }
}

int main(int argc, char **argv)
{
    if (argc > 1 && strcmp(argv[1], "--emit") == 0) {
        emit_samples();
        return 0;
    }
    if (argc > 1 && strcmp(argv[1], "--emit-i2c") == 0) {
        emit_i2c();
        return 0;
    }
    if (argc > 1 && strcmp(argv[1], "--emit-din") == 0) {
        emit_din();
        return 0;
    }

    printf("mk_telem\n");
    test_raw_to_ma_matches_the_shunt();
    test_nothing_before_the_period();
    test_no_data_sends_nothing();
    test_channel_with_no_data_is_never_sent_even_over_many_periods();
    test_record_shape();
    test_time_source_defaults_to_device_clock_without_timeax();
    test_time_source_follows_attached_timeax_grade();
    test_seq_increases_so_the_host_can_find_gaps();
    test_repeats_the_last_value_without_new_acquisition();
    test_only_the_latest_sample_survives_when_acquisition_outpaces_tx();
    test_changing_tx_period_ms_changes_the_interval();
    test_field_mask_selects();
    test_value_follows_the_spec_formula();
    test_float_digits_is_honoured();
    test_every_active_channel_sends_exactly_one_line_per_period();
    test_all_seven_channels_fit_in_one_tick();
    test_disabled_channel_is_silent();
    test_i2c_records_share_the_sequence_with_ain();
    test_failure_repeats_at_the_tx_period_not_the_retry_period();
    test_din_records_share_the_sequence_with_ain();
    test_din_record_shape();
    test_din_is_not_gated_by_tx_period();
    test_reads_i2c_values_straight_from_the_last_value_cache();

    printf(failures ? "FAILED (%d)\n" : "PASSED\n", failures);
    return failures ? 1 : 0;
}
