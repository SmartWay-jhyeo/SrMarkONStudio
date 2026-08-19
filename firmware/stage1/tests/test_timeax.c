/* mk_timeax 단위 시험 — GNSS/PPS 시간축, HAL 비의존.
 *
 * 대상: firmware/stage1/app/mk_timeax.c
 *
 * 시간은 전부 `dev_us`(µs 단조 카운터) 하나로만 진행한다 — 실기기의
 * mk_gnss_io.c 가 TIM8 캡처를 이 값으로 펼쳐 넘기는 것을 흉내 낸다.
 */
#include <stdio.h>
#include <string.h>
#include "../app/mk_timeax.h"

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
    if (!(cond)) { printf("  FAIL %s\n", msg); failures++; }    \
    else         { printf("  ok   %s\n", msg); }                \
} while (0)

static MkGnssRmc make_rmc(int valid, int64_t epoch_ms)
{
    MkGnssRmc r;
    memset(&r, 0, sizeof r);
    r.valid = valid;
    r.epoch_ms = epoch_ms;
    return r;
}

/* ---- 초기 상태 ------------------------------------------------------------ */

static void test_initial_state_is_device_clock(void)
{
    MkTimeAx tx;
    mk_timeax_init(&tx);
    CHECK(mk_timeax_grade(&tx) == MK_TIMEAX_DEVICE_CLOCK, "초기 등급은 device_clock");
    CHECK(strcmp(mk_timeax_grade_name(mk_timeax_grade(&tx)), "device_clock") == 0,
          "등급 이름 device_clock");
    CHECK(mk_timeax_now_ms(&tx, 5000) == 5, "GNSS 없으면 dev_us/1000 을 그대로 쓴다");
    CHECK(mk_timeax_pps_age_us(&tx, 5000) == -1, "PPS 를 한 번도 못 봤으면 -1");
}

/* ---- PPS + RMC 짝짓기 -> gnss_pps ----------------------------------------- */

static void test_pps_then_rmc_within_window_reaches_gnss_pps(void)
{
    MkTimeAx tx;
    mk_timeax_init(&tx);

    mk_timeax_on_pps(&tx, 1000000ULL);                       /* PPS @ 1.000000s */
    MkGnssRmc rmc = make_rmc(1, 1700000000123LL);             /* msec=123 (버려진다) */
    mk_timeax_on_rmc(&tx, &rmc, 1050000ULL);                   /* RMC 50ms 뒤 */

    CHECK(mk_timeax_grade(&tx) == MK_TIMEAX_GNSS_PPS, "짝지어지면 gnss_pps");
    CHECK(mk_timeax_now_ms(&tx, 1000000ULL) == 1700000000000LL,
          "PPS 에지 순간 = 그 초의 .000(문장의 msec 은 버려진다)");
    CHECK(mk_timeax_now_ms(&tx, 1500000ULL) == 1700000000500LL,
          "500ms 뒤에는 500ms 를 더한다");
    CHECK(mk_timeax_pps_age_us(&tx, 1200000ULL) == 200000LL,
          "마지막 PPS 이후 경과(µs)");

    mk_timeax_tick(&tx, 1300000ULL);
    CHECK(mk_timeax_pps_age_us_now(&tx) == 300000LL,
          "_now 버전은 마지막 tick() 시각을 쓴다($STAT 처럼 '지금'이 없을 때)");
}

/* ---- RMC 단독 -> gnss_nmea ------------------------------------------------- */

static void test_rmc_alone_without_pps_reaches_gnss_nmea_only(void)
{
    MkTimeAx tx;
    mk_timeax_init(&tx);

    MkGnssRmc rmc = make_rmc(1, 1700000005000LL);              /* msec=0 */
    mk_timeax_on_rmc(&tx, &rmc, 2000000ULL);                    /* PPS 짝 없음 */

    CHECK(mk_timeax_grade(&tx) == MK_TIMEAX_GNSS_NMEA,
          "PPS 짝이 없으면 gnss_nmea 까지만");
    CHECK(mk_timeax_now_ms(&tx, 2100000ULL) == 1700000005100LL,
          "NMEA 단독 anchor 로부터 100ms 뒤");
}

/* ---- 짝짓기 창을 넘기면 PPS 로 안 오른다 ----------------------------------- */

static void test_pairing_window_exceeded_falls_back_to_nmea(void)
{
    MkTimeAx tx;
    mk_timeax_init(&tx);

    mk_timeax_on_pps(&tx, 0ULL);
    MkGnssRmc rmc = make_rmc(1, 1700000009000LL);
    /* 짝짓기 창(MK_TIMEAX_PAIR_WINDOW_US) 을 1 초과. */
    mk_timeax_on_rmc(&tx, &rmc, MK_TIMEAX_PAIR_WINDOW_US + 1ULL);

    CHECK(mk_timeax_grade(&tx) == MK_TIMEAX_GNSS_NMEA,
          "짝짓기 창을 넘기면 PPS 로 못 올라가고 gnss_nmea 에 머문다");
}

/* ---- 되돌림 검사 1: PPS 가 끊기면 내려간다 --------------------------------- */

static void test_pps_going_stale_downgrades_then_nmea_going_stale_downgrades_further(void)
{
    MkTimeAx tx;
    mk_timeax_init(&tx);

    mk_timeax_on_pps(&tx, 1000000ULL);
    MkGnssRmc rmc = make_rmc(1, 1700000000000LL);
    mk_timeax_on_rmc(&tx, &rmc, 1050000ULL);
    CHECK(mk_timeax_grade(&tx) == MK_TIMEAX_GNSS_PPS, "짝지어 gnss_pps 로 시작");

    /* PPS 가 STALE 을 넘어설 만큼 조용해졌지만 RMC 는 아직 신선하다. */
    uint64_t t1 = 1000000ULL + MK_TIMEAX_PPS_STALE_US + 1ULL;
    mk_timeax_tick(&tx, t1);
    CHECK(mk_timeax_grade(&tx) == MK_TIMEAX_GNSS_NMEA,
          "PPS 만 끊기면 gnss_nmea 로 한 단계만 내려간다");

    /* RMC 마저 STALE 을 넘어선다. */
    uint64_t t2 = 1050000ULL + MK_TIMEAX_NMEA_STALE_US + 1ULL;
    mk_timeax_tick(&tx, t2);
    CHECK(mk_timeax_grade(&tx) == MK_TIMEAX_DEVICE_CLOCK,
          "RMC 마저 끊기면 device_clock 까지 내려간다");
}

/* ---- 되돌림 검사 2: 무효 fix 는 신선도를 갱신하지 않는다 ------------------- */

static void test_invalid_fix_does_not_refresh_freshness(void)
{
    MkTimeAx tx;
    mk_timeax_init(&tx);

    MkGnssRmc valid_rmc = make_rmc(1, 1700000000000LL);
    mk_timeax_on_rmc(&tx, &valid_rmc, 0ULL);
    CHECK(mk_timeax_grade(&tx) == MK_TIMEAX_GNSS_NMEA, "유효한 RMC 로 gnss_nmea");

    /* STALE 문턱 바로 앞에서 무효(V) 문장이 온다 — 신선도를 되살리면
     * 안 된다. */
    MkGnssRmc invalid_rmc = make_rmc(0, 1700000002900LL);
    mk_timeax_on_rmc(&tx, &invalid_rmc, MK_TIMEAX_NMEA_STALE_US - 100ULL);

    /* STALE 문턱을 넘겼다 — 무효 문장이 신선도를 되살렸다면 아직
     * gnss_nmea 여야 하지만, 되살리지 않았다면 device_clock 이어야 한다. */
    mk_timeax_tick(&tx, MK_TIMEAX_NMEA_STALE_US + 1ULL);
    CHECK(mk_timeax_grade(&tx) == MK_TIMEAX_DEVICE_CLOCK,
          "무효 fix 는 신선도를 되살리지 않는다 — 원래 시각대로 내려간다");
}

/* ---- 되돌림 검사 3: 16비트 캡처 확장, 롤오버 보정 -------------------------- */

static void test_extend_ticks_plain(void)
{
    uint64_t v = mk_timeax_extend_ticks(5u, 1234u, 65536u, 0);
    CHECK(v == (uint64_t)5 * 65536 + 1234, "겹침 없는 평범한 확장");
}

static void test_extend_ticks_monotonic_across_rollover(void)
{
    uint64_t before = mk_timeax_extend_ticks(0u, 65500u, 65536u, 0);
    uint64_t after  = mk_timeax_extend_ticks(1u, 100u, 65536u, 0);
    CHECK(after > before, "롤오버를 지나도 시각이 뒤로 가지 않는다");
    CHECK(after - before == 136, "롤오버 앞뒤 간격이 정확하다(65536-65500+100)");
}

static void test_extend_ticks_corrects_back_half_overlap(void)
{
    /* Update ISR 이 캡처보다 먼저 wrap_count 를 올렸는데, CCR 은 아직
     * 이전 주기의 뒤쪽 절반(롤오버 직전)이다 — 한 주기 보정해야 한다. */
    uint64_t corrected = mk_timeax_extend_ticks(5u, 65000u, 65536u, 1);
    uint64_t naive = (uint64_t)5 * 65536 + 65000;
    CHECK(corrected == naive - 65536, "뒤쪽 절반 + update_pending 은 한 주기 보정된다");
}

static void test_extend_ticks_front_half_overlap_no_correction(void)
{
    /* CCR 이 앞쪽 절반이면 캡처가 이미 롤오버 이후다 — wrap_count 를
     * 그대로 믿는다. */
    uint64_t v = mk_timeax_extend_ticks(5u, 100u, 65536u, 1);
    CHECK(v == (uint64_t)5 * 65536 + 100, "앞쪽 절반은 보정하지 않는다");
}

/* ---- 위성 수 --------------------------------------------------------------- */

static void test_gga_updates_sats_only(void)
{
    MkTimeAx tx;
    mk_timeax_init(&tx);
    CHECK(mk_timeax_sats(&tx) == 0, "초기 위성 수 0");

    MkGnssGga gga;
    memset(&gga, 0, sizeof gga);
    gga.fix_quality = 1;
    gga.sats = 11;
    mk_timeax_on_gga(&tx, &gga);

    CHECK(mk_timeax_sats(&tx) == 11, "GGA 로 위성 수만 갱신된다");
    CHECK(mk_timeax_grade(&tx) == MK_TIMEAX_DEVICE_CLOCK,
          "GGA 만으로는 등급이 오르지 않는다(RMC 가 시각의 근거다)");
}

int main(void)
{
    printf("-- 초기 상태 --\n");
    test_initial_state_is_device_clock();
    printf("-- PPS+RMC 짝짓기 -> gnss_pps --\n");
    test_pps_then_rmc_within_window_reaches_gnss_pps();
    printf("-- RMC 단독 -> gnss_nmea --\n");
    test_rmc_alone_without_pps_reaches_gnss_nmea_only();
    printf("-- 짝짓기 창 초과 --\n");
    test_pairing_window_exceeded_falls_back_to_nmea();
    printf("-- 되돌림: 신선도 하락 --\n");
    test_pps_going_stale_downgrades_then_nmea_going_stale_downgrades_further();
    printf("-- 되돌림: 무효 fix는 신선도를 못 되살린다 --\n");
    test_invalid_fix_does_not_refresh_freshness();
    printf("-- 되돌림: 16비트 캡처 확장 --\n");
    test_extend_ticks_plain();
    test_extend_ticks_monotonic_across_rollover();
    test_extend_ticks_corrects_back_half_overlap();
    test_extend_ticks_front_half_overlap_no_correction();
    printf("-- 위성 수 --\n");
    test_gga_updates_sats_only();

    printf(failures ? "\nFAILED (%d)\n" : "\nPASSED\n", failures);
    return failures ? 1 : 0;
}
