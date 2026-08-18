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

/* ---- 주기 --------------------------------------------------------------- */

static void test_nothing_before_the_period(void)
{
    /* 🔴 큐에 있는 것을 즉시 쏟으면 `tx.period_ms` 가 뜻을 잃는다. */
    setup();
    mk_queue_push(mk_ads_queue(&ADS, 0), 1000, 4000000);
    CHECK(mk_telem_tick(&T, 50, sink, NULL) == 0, "주기 전에는 안 보낸다");
    CHECK(mk_telem_tick(&T, 100, sink, NULL) == 1, "주기가 되면 보낸다");
}

static void test_empty_queue_sends_nothing(void)
{
    setup();
    CHECK(mk_telem_tick(&T, 100, sink, NULL) == 0, "빈 큐면 아무것도 안 나간다");
}

/* ---- 레코드 ------------------------------------------------------------- */

static void test_record_shape(void)
{
    setup();
    mk_queue_push(mk_ads_queue(&ADS, 0), 1772200855875LL, 4026531);
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

static void test_seq_increases_so_the_host_can_find_gaps(void)
{
    /* 규격 §7.1 — 호스트가 누락을 검출하는 유일한 근거다. */
    setup();
    for (int k = 0; k < 3; k++) {
        mk_queue_push(mk_ads_queue(&ADS, 0), 1000 + k, 4000000);
    }
    mk_telem_tick(&T, 100, sink, NULL);
    CHECK(N == 3, "쌓인 것을 비운다");
    CHECK_HAS(LINES[0], "\"seq\":1", "seq 1");
    CHECK_HAS(LINES[1], "\"seq\":2", "seq 2");
    CHECK_HAS(LINES[2], "\"seq\":3", "seq 3");
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

    mk_queue_push(mk_ads_queue(&ADS, 0), 1000, 4026531);
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

    mk_queue_push(mk_ads_queue(&ADS, 0), 1000, 4026531);
    mk_telem_tick(&T, 100, sink, NULL);
    CHECK_HAS(LINES[0], "\"value\":150.0", "20 mA 가 150 bar 로");
}

static void test_float_digits_is_honoured(void)
{
    setup();
    set_u32("tx.float_digits", 2u);
    mk_queue_push(mk_ads_queue(&ADS, 0), 1000, 4026531);
    mk_telem_tick(&T, 100, sink, NULL);
    CHECK(strstr(LINES[0], "\"ma\":20.00,") != NULL
          || strstr(LINES[0], "\"ma\":20.0,") != NULL,
          "자릿수 설정을 따른다");
}

/* ---- 격리 --------------------------------------------------------------- */

static void test_channels_take_turns(void)
{
    /* 🔴 큐가 채널마다 따로인 이유가 격리인데 송신이 그것을 깨면 안 된다.
     *    0 번부터 매번 훑으면 앞 채널이 밀려 있을 때 뒤가 영영 못 나간다. */
    setup();
    for (int ch = 0; ch < 3; ch++) {
        for (int k = 0; k < 8; k++) {
            mk_queue_push(mk_ads_queue(&ADS, ch), 1000 + k, 4000000);
        }
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
    CHECK(seen[0] > 0 && seen[1] > 0 && seen[2] > 0,
          "한 바퀴에 세 채널이 모두 나간다");
}

static void test_burst_is_capped(void)
{
    /* 🔴 밀린 것을 한 번에 다 쏟으면 UART 가 막히고, 그동안 하트비트가
     *    밀려 보드가 RUN 으로 떨어진다 — 링크가 나쁠 때 정확히 터진다. */
    setup();
    for (int ch = 0; ch < 4; ch++) {
        for (int k = 0; k < 8; k++) {
            mk_queue_push(mk_ads_queue(&ADS, ch), 1000 + k, 4000000);
        }
    }
    int sent = mk_telem_tick(&T, 100, sink, NULL);
    CHECK(sent <= MK_TELEM_MAX_LINES, "한 번에 보내는 양에 상한이 있다");
    CHECK(sent > 0, "그래도 보내기는 한다");
}

static void test_disabled_channel_is_silent(void)
{
    /* 설계 원칙 3 — 센서 미연결은 정상 상태다. 꺼진 채널을 싣지 않는다. */
    setup();
    mk_ads_configure(&ADS, 1, 0, 100, 0);
    mk_queue_push(mk_ads_queue(&ADS, 1), 1000, 4000000);
    CHECK(mk_telem_tick(&T, 100, sink, NULL) == 0, "꺼진 채널은 안 나간다");
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
    /* 🔴 브리프 원문에는 없었지만 ADS 큐가 비어 있으면 ain 이 한 줄도 안
     *    나가 "두 종류가 함께 나간다" 를 어떤 구현으로도 통과시킬 수 없다
     *    (실제로 이 줄 없이 돌려 FAIL 을 확인했다 — task-6-report.md 참고).
     *    seq 공유를 보는 시험이므로 ain 쪽에도 한 건은 있어야 한다. */
    mk_queue_push(mk_ads_queue(&ADS, 0), 500, 4000000);

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

/* 🔴 값이 없으면 null 이다. 마지막 값을 다시 실으면 화면이 살아 있는
 *    센서처럼 보인다 (규격 §7.5).
 *
 * 🔴 [검토 지적 C2 — 이름을 고쳤다] enable_lux_port_in 은 드라이버가
 *    있는 LUX 를 켜고 fake_xfer_nak 는 **첫 xfer(Power On)부터** 실패
 *    시키므로, 실제로 타는 것은 READY 의 read 실패가 아니라 START 의
 *    시작 실패 경로다 — 예전 이름(test_failed_read_emits_null_not_
 *    the_last_value)이 그것을 숨겼다.
 *
 *    예전 판은 각 줄의 **내용**만 보고 몇 줄이 왔는지는 안 셌다. 그래서
 *    시작 실패에 주기 가드가 없어(안 꽂힌 포트 하나가 슈퍼루프 두
 *    바퀴마다 레코드를 만드는 결함, 실측 링크의 약 2/3) 이 시험을
 *    그대로 통과시켰다. 이제 줄 수를 못 박는다 —
 *    enable_lux_port_in 이 고정하는 period_ms=200 에서 0~400ms 를
 *    10ms 간격으로 돌리면 t=10(첫 시도 실패)과 t=210(첫 재시도) 두
 *    번만 나가야 한다(t=410 은 범위 밖). */
static void test_start_failure_is_rate_limited_and_emits_null(void)
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
    CHECK(n_i2c == 2,
          "주기 가드가 있어 400ms/period_ms=200 에 두 번만 나간다 (C2 — "
          "가드 없이 매 두 바퀴 재시도하면 이보다 훨씬 많이 나간다)");
}

/* 🔴 [검토 지적 — 근거가 틀렸다] "while → if 로 바꿔도 시험이 안 깨진다" 는
 *    공백을 처음에는 "test_i2c.c 의 시험들이 그 경로를 이미 덮는다" 는
 *    근거로 넘겼는데, 사실이 아니었다 — test_one_port_per_tick 은 여섯
 *    포트를 전부 지원 종류로 켜서 매 바퀴 touched=1 로 조기 반환하는
 *    경로만 돌고, 지원 안 하는 종류 시험은 포트 하나만 켜서 n_out 이
 *    2 가 되는 상황을 만들지 않는다. 저장소 전체에 "한 바퀴에 out 이
 *    둘 쌓인 채로 mk_telem_tick 이 한 번만 불린다" 를 보는 시험이
 *    없었다.
 *
 *    mk_i2c_tick·상태기계는 건드리지 않는다 — out 버퍼를 직접 채워
 *    mk_telem_tick 의 배출 계약(mk_i2c.h: "매 바퀴 뒤 0 이 나올 때까지
 *    비운다")만 좁혀서 본다. emit_i2c() 가 이미 쓰는 방식이다. */
static void test_tick_drains_every_pending_i2c_record_in_one_call(void)
{
    setup();
    static MkI2c I2C;
    MkI2cIo io = { fake_xfer_ok, NULL };
    mk_i2c_init(&I2C, &io);
    mk_telem_attach_i2c(&T, &I2C);

    I2C.out[0] = (MkI2cOut){ .connector_id = 12u, .quantity = "temp",
                             .value = 20.0f, .have_value = 1,
                             .status = 0u, .t_ms = 1000 };
    I2C.out[1] = (MkI2cOut){ .connector_id = 12u, .quantity = "humidity",
                             .value = 55.0f, .have_value = 1,
                             .status = 0u, .t_ms = 1000 };
    I2C.n_out = 2;

    mk_telem_tick(&T, 100, sink, NULL);   /* 딱 한 번 */

    int i2c = 0;
    for (int k = 0; k < N; k++) {
        if (strstr(LINES[k], "\"type\":\"i2c\"")) { i2c++; }
    }
    CHECK(i2c == 2, "한 바퀴에 쌓인 i2c 레코드 둘이 한 번의 tick 으로 다 나간다");
}

/* ---- 카탈로그 덤프 ------------------------------------------------------ */

static void emit_samples(void)
{
    setup();
    /* 4 · 12 · 20 mA 에 해당하는 코드 */
    /* 4 mA · 12 mA · 20 mA 에 해당하는 코드 (만재 2xVREF). */
    static const int32_t CODES[] = { 805306, 2415918, 4026531 };
    for (size_t k = 0; k < sizeof CODES / sizeof *CODES; k++) {
        mk_queue_push(mk_ads_queue(&ADS, 0), 1000 + (int)k, CODES[k]);
    }
    mk_telem_tick(&T, 100, sink, NULL);
    for (int i = 0; i < N; i++) {
        fputs(LINES[i], stdout);
    }
}

/* crosscheck_i2c.py 가 대조하는 세 벡터. 상태기계를 거치지 않고 out 큐에
 * 직접 채워 레코드 조립(build_i2c_record)만 시뮬레이터와 맞춘다. */
static void emit_i2c(void)
{
    setup();
    static MkI2c I2C;
    MkI2cIo io = { fake_xfer_ok, NULL };
    mk_i2c_init(&I2C, &io);
    mk_telem_attach_i2c(&T, &I2C);

    /* 1) 정상값 */
    I2C.out[0] = (MkI2cOut){ .connector_id = 10u, .quantity = "lux",
                             .value = 401.5f, .have_value = 1,
                             .status = 0u, .t_ms = 1000 };
    I2C.n_out = 1;
    mk_telem_tick(&T, 100, sink, NULL);

    /* 2) 응답 없음 */
    I2C.out[0] = (MkI2cOut){ .connector_id = 11u, .quantity = "",
                             .value = 0.0f, .have_value = 0,
                             .status = 1u, .t_ms = 2000 };
    I2C.n_out = 1;
    mk_telem_tick(&T, 300, sink, NULL);

    /* 3) 지원 안 하는 종류 */
    I2C.out[0] = (MkI2cOut){ .connector_id = 12u, .quantity = "",
                             .value = 0.0f, .have_value = 0,
                             .status = 3u, .t_ms = 3000 };
    I2C.n_out = 1;
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

    printf("mk_telem\n");
    test_raw_to_ma_matches_the_shunt();
    test_nothing_before_the_period();
    test_empty_queue_sends_nothing();
    test_record_shape();
    test_seq_increases_so_the_host_can_find_gaps();
    test_field_mask_selects();
    test_value_follows_the_spec_formula();
    test_float_digits_is_honoured();
    test_channels_take_turns();
    test_burst_is_capped();
    test_disabled_channel_is_silent();
    test_i2c_records_share_the_sequence_with_ain();
    test_start_failure_is_rate_limited_and_emits_null();
    test_tick_drains_every_pending_i2c_record_in_one_call();

    printf(failures ? "FAILED (%d)\n" : "PASSED\n", failures);
    return failures ? 1 : 0;
}
