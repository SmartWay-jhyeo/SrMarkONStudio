/* mk_gnss 단위 시험 — NMEA 파서, HAL 비의존.
 *
 * 대상: firmware/stage1/app/mk_gnss.c
 *
 * 🔴 문장은 UM981(ArduSimple AS-RTK3B-UM981-L125-NH)이 낼 표준 NMEA0183
 *    RMC/GGA다. 모델 고유 명령이 아니라 표준 문장만 다룬다 — CLAUDE.md의
 *    지시대로(UM982 참고 구현을 그대로 믿지 말라) 우리는 UM981 데이터시트를
 *    확보하지 못해 독자 문장은 아예 다루지 않는 게 안전하다.
 *
 * 체크섬은 실제로 계산해 박아 두었다(스크립트로 계산, 손으로 하나씩 XOR을
 * 짚어 검산 가능). epoch_ms 기준값은 Python `datetime`으로 UTC 기준
 * timestamp 를 뽑아 못박았다 — 윤년(2024-02-29), 2월 말 자정 경계
 * (2023-02-28→03-01), 세기 규칙(2100-03-01 은 윤년이 아니다, 2000-02-29 는
 * 윤년이다 — 그레고리력 400 규칙), 1970 epoch 원점을 모두 짚는다. */
#include <stdio.h>
#include <string.h>
#include "../app/mk_gnss.h"

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

static void feed_str(MkGnss *g, const char *s)
{
    for (const char *p = s; *p; p++) {
        mk_gnss_feed(g, (uint8_t)*p);
    }
}

/* ---- UTC -> epoch_ms 손으로 박은 기준값 ---------------------------------- */

static void test_epoch_known_values(void)
{
    CHECK(mk_gnss_utc_to_epoch_ms(1970, 1, 1, 0, 0, 0, 0) == 0,
          "1970-01-01 00:00:00.000 = epoch 0");
    CHECK(mk_gnss_utc_to_epoch_ms(2000, 1, 1, 0, 0, 0, 0) == 946684800000LL,
          "2000-01-01 00:00:00.000");
    CHECK(mk_gnss_utc_to_epoch_ms(1999, 12, 31, 23, 59, 59, 0) == 946684799000LL,
          "1999-12-31 23:59:59.000 — 2000년 경계 1초 전");
    /* 윤년: 2024 는 4의 배수라 윤년이다. */
    CHECK(mk_gnss_utc_to_epoch_ms(2024, 2, 29, 23, 59, 59, 500) == 1709251199500LL,
          "2024-02-29(윤일) 23:59:59.500");
    /* 2023 은 윤년이 아니다 — 2/28 다음은 바로 3/1. */
    CHECK(mk_gnss_utc_to_epoch_ms(2023, 2, 28, 23, 59, 59, 0) == 1677628799000LL,
          "2023-02-28(평년) 23:59:59.000");
    CHECK(mk_gnss_utc_to_epoch_ms(2023, 3, 1, 0, 0, 0, 0) == 1677628800000LL,
          "2023-03-01 00:00:00.000 — 평년 2월 말 경계");
    /* 자정 경계, 1 ms 차이. */
    CHECK(mk_gnss_utc_to_epoch_ms(2026, 8, 18, 23, 59, 59, 999) == 1787097599999LL,
          "2026-08-18 23:59:59.999");
    CHECK(mk_gnss_utc_to_epoch_ms(2026, 8, 19, 0, 0, 0, 0) == 1787097600000LL,
          "2026-08-19 00:00:00.000 — 1ms 뒤");
    /* 그레고리력 세기 규칙: 100 의 배수는 400 의 배수가 아니면 평년이다.
     * 2100 은 평년이므로 2/29 가 없다 — 3/1 로 확인한다. */
    CHECK(mk_gnss_utc_to_epoch_ms(2100, 3, 1, 0, 0, 0, 0) == 4107542400000LL,
          "2100-03-01 — 세기 평년(2100 은 400 의 배수가 아니다)");
    /* 2000 은 400 의 배수라 윤년이다 — 2/29 가 존재해야 한다. */
    CHECK(mk_gnss_utc_to_epoch_ms(2000, 2, 29, 0, 0, 0, 0) == 951782400000LL,
          "2000-02-29 — 세기 윤년(2000 은 400 의 배수)");
}

/* ---- 정상 RMC/GGA 파싱 ---------------------------------------------------- */

static void test_valid_rmc_is_parsed(void)
{
    MkGnss g;
    mk_gnss_init(&g);
    /* 2026-08-18 23:59:59.999 UTC, fix 유효(A). */
    feed_str(&g, "$GNRMC,235959.999,A,4807.038,N,01131.000,E,022.4,084.4,180826,,,A*73\r\n");

    MkGnssRmc rmc;
    CHECK(mk_gnss_take_rmc(&g, &rmc) == 1, "RMC 를 하나 꺼낼 수 있다");
    CHECK(rmc.valid == 1, "상태 필드 A = 유효");
    CHECK(rmc.year == 2026 && rmc.month == 8 && rmc.day == 18, "날짜 파싱");
    CHECK(rmc.hour == 23 && rmc.minute == 59 && rmc.sec == 59, "시각 파싱");
    CHECK(rmc.msec == 999, "밀리초 파싱");
    CHECK(rmc.epoch_ms == 1787097599999LL, "epoch_ms 계산");
    CHECK(mk_gnss_take_rmc(&g, &rmc) == 0, "한 번 꺼내면 비었다");
}

static void test_valid_gga_is_parsed(void)
{
    MkGnss g;
    mk_gnss_init(&g);
    feed_str(&g, "$GNGGA,120000.00,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*79\r\n");

    MkGnssGga gga;
    CHECK(mk_gnss_take_gga(&g, &gga) == 1, "GGA 를 하나 꺼낼 수 있다");
    CHECK(gga.fix_quality == 1, "fix 품질 = 1(GPS fix)");
    CHECK(gga.sats == 8, "위성 수 = 8");
}

static void test_gga_no_fix(void)
{
    MkGnss g;
    mk_gnss_init(&g);
    feed_str(&g, "$GNGGA,120000.00,4807.038,N,01131.000,E,0,00,,,M,,M,,*6C\r\n");

    MkGnssGga gga;
    CHECK(mk_gnss_take_gga(&g, &gga) == 1, "fix 없어도 문장 자체는 파싱된다");
    CHECK(gga.fix_quality == 0, "fix 품질 0 = 무효");
    CHECK(gga.sats == 0, "위성 수 0");
}

/* ---- 되돌림 검사 1: 체크섬 --------------------------------------------- */

static void test_bad_checksum_is_dropped(void)
{
    MkGnss g;
    mk_gnss_init(&g);
    /* 마지막 자리 하나만 틀렸다(73 -> 74). */
    feed_str(&g, "$GNRMC,235959.999,A,4807.038,N,01131.000,E,022.4,084.4,180826,,,A*74\r\n");

    MkGnssRmc rmc;
    CHECK(mk_gnss_take_rmc(&g, &rmc) == 0, "체크섬 틀리면 버려진다");
    CHECK(mk_gnss_checksum_fail_count(&g) == 1, "체크섬 실패가 세어진다");
}

static void test_missing_checksum_star_is_dropped(void)
{
    MkGnss g;
    mk_gnss_init(&g);
    feed_str(&g, "$GNRMC,235959.999,A,4807.038,N,01131.000,E,022.4,084.4,180826,,,A\r\n");

    MkGnssRmc rmc;
    CHECK(mk_gnss_take_rmc(&g, &rmc) == 0, "'*' 가 없으면 버려진다");
}

/* ---- 되돌림 검사 2: 잘린 문장 ------------------------------------------- */

static void test_truncated_sentence_never_completes(void)
{
    MkGnss g;
    mk_gnss_init(&g);
    /* 줄바꿈이 영원히 안 온다 — 완성되지 않아야 한다. */
    feed_str(&g, "$GNRMC,235959.999,A,4807.038,N,01131.000,E,022.4,084.4,180826,,,A*73");

    MkGnssRmc rmc;
    CHECK(mk_gnss_take_rmc(&g, &rmc) == 0, "줄바꿈 전에는 아무것도 안 나온다");

    /* 그 뒤 온전한 문장이 오면 정상 처리돼야 한다(잘린 자리가 다음 문장을
     * 오염시키면 안 된다). */
    feed_str(&g, "$GNRMC,000000.000,A,4807.038,N,01131.000,E,022.4,084.4,190826,,,A*7A\r\n");
    CHECK(mk_gnss_take_rmc(&g, &rmc) == 1, "이어지는 온전한 문장은 파싱된다");
    CHECK(rmc.epoch_ms == 1787097600000LL, "그 문장의 값이 맞다");
}

static void test_overlong_line_is_discarded_and_recovers(void)
{
    MkGnss g;
    mk_gnss_init(&g);

    /* MK_GNSS_LINE_MAX 를 넘는 쓰레기 — '$' 없이 그냥 잡음이라고 하자. */
    char junk[300];
    memset(junk, 'X', sizeof junk - 1);
    junk[sizeof junk - 1] = '\0';
    feed_str(&g, "$");
    feed_str(&g, junk);
    feed_str(&g, "\n");

    MkGnssRmc rmc;
    CHECK(mk_gnss_take_rmc(&g, &rmc) == 0, "너무 긴 줄은 버려진다");

    /* 다음 온전한 문장은 영향받지 않는다. */
    feed_str(&g, "$GNRMC,000000.000,A,4807.038,N,01131.000,E,022.4,084.4,190826,,,A*7A\r\n");
    CHECK(mk_gnss_take_rmc(&g, &rmc) == 1, "그다음 온전한 문장은 정상 파싱된다");
}

/* ---- 되돌림 검사 3: 문장이 이어 붙은 경우 -------------------------------- */

static void test_concatenated_sentences_both_parse(void)
{
    MkGnss g;
    mk_gnss_init(&g);
    feed_str(&g,
        "$GNRMC,235959.999,A,4807.038,N,01131.000,E,022.4,084.4,180826,,,A*73\r\n"
        "$GNGGA,120000.00,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*79\r\n");

    MkGnssRmc rmc;
    MkGnssGga gga;
    CHECK(mk_gnss_take_rmc(&g, &rmc) == 1, "붙어 온 첫 문장(RMC)이 파싱된다");
    CHECK(mk_gnss_take_gga(&g, &gga) == 1, "붙어 온 둘째 문장(GGA)도 파싱된다");
}

static void test_new_dollar_mid_sentence_restarts(void)
{
    MkGnss g;
    mk_gnss_init(&g);
    /* 첫 문장이 잡음에 잘리고 그 자리에서 새 '$' 가 바로 시작된다 —
     * 실제로는 링크 잡음으로 바이트가 섞였을 때의 모양이다. 첫 조각은
     * 버려지고 두 번째는 온전히 파싱돼야 한다. */
    feed_str(&g, "$GNRMC,235959.999,A,4807");   /* 중간에 끊김 */
    feed_str(&g, "$GNRMC,000000.000,A,4807.038,N,01131.000,E,022.4,084.4,190826,,,A*7A\r\n");

    MkGnssRmc rmc;
    CHECK(mk_gnss_take_rmc(&g, &rmc) == 1, "새 '$' 이후 문장만 온전히 파싱된다");
    CHECK(rmc.epoch_ms == 1787097600000LL, "두 번째(온전한) 문장의 값이 나온다");
}

/* ---- 되돌림 검사 4: 유효하지 않은 fix ------------------------------------ */

static void test_invalid_fix_status_is_reported(void)
{
    MkGnss g;
    mk_gnss_init(&g);
    feed_str(&g, "$GNRMC,120000.000,V,,,,,,,190826,,,N*54\r\n");

    MkGnssRmc rmc;
    /* 🔴 checksum 은 맞으므로 문장 자체는 꺼낼 수 있다 — 그래야 timeax 가
     * "fix 가 없다" 는 사실 자체를 알 수 있다. valid=0 이 그 신호다. */
    CHECK(mk_gnss_take_rmc(&g, &rmc) == 1, "V 상태도 문장으로는 나온다");
    CHECK(rmc.valid == 0, "상태 필드 V = 무효로 표시된다");
}

/* ---- 원시 문장 에코 (규격 §7.7, gnss.echo) -------------------------------- */

static void test_raw_line_is_captured_verbatim(void)
{
    /* 🔴 '그대로' 다 — '$' 는 담고 CR/LF 는 빠진다. 체크섬 통과 여부와
     * 무관하게 담아야 진단(§7.7)이 성립한다. */
    MkGnss g;
    char out[128];
    size_t len = 0;
    mk_gnss_init(&g);
    feed_str(&g, "$GNRMC,235959.999,A,4807.038,N,01131.000,E,022.4,084.4,180826,,,A*73\r\n");

    CHECK(mk_gnss_take_raw(&g, out, sizeof out, &len) == 1, "원시 줄을 하나 꺼낼 수 있다");
    CHECK_EQ(out,
             "$GNRMC,235959.999,A,4807.038,N,01131.000,E,022.4,084.4,180826,,,A*73",
             "'$' 포함, CR/LF 제외 그대로");
    CHECK(mk_gnss_take_raw(&g, out, sizeof out, &len) == 0, "한 번 꺼내면 비었다");
}

static void test_raw_line_captured_even_on_bad_checksum(void)
{
    /* 🔴 파싱 성공 여부와 무관하다 — "왜 파싱이 안 되는지" 를 보려고
     * 만든 채널이다. RMC/GGA 로도 못 만들 텐데도 원문은 남아야 한다. */
    MkGnss g;
    char out[128];
    mk_gnss_init(&g);
    feed_str(&g, "$GNRMC,235959.999,A,4807.038,N,01131.000,E,022.4,084.4,180826,,,A*74\r\n");

    CHECK(mk_gnss_take_raw(&g, out, sizeof out, NULL) == 1,
          "체크섬이 틀려도 원문은 그대로 남는다");
    CHECK(strstr(out, "*74") != NULL, "틀린 체크섬 문자 그대로");
}

static void test_raw_queue_drops_oldest_when_full(void)
{
    /* 🔴 진단용이라 최신이 더 값지다 — 비우지 않고 계속 먹이면 오래된
     * 것부터 밀려난다(가장 오래된 것을 버린다). 큐가 죽지 않고 계속
     * 동작하는지만 본다 — 정확한 용량은 구현 세부다. */
    MkGnss g;
    char out[128];
    size_t got = 0;
    mk_gnss_init(&g);
    for (int i = 0; i < 20; i++) {
        feed_str(&g, "$GNRMC,000000.000,A,4807.038,N,01131.000,E,022.4,084.4,190826,,,A*7A\r\n");
    }
    while (mk_gnss_take_raw(&g, out, sizeof out, NULL)) { got++; }
    CHECK(got > 0, "큐가 넘쳐도 최소한 최근 것은 남아 있다");
    CHECK(got < 20u, "다 담지는 못한다 — 큐는 무한이 아니다");
}

/* ---- 문장 수신 여부 (규격 §4.1.1 초기화 재시도의 근거) -------------------- */

static void test_sentence_seen_starts_false(void)
{
    MkGnss g;
    mk_gnss_init(&g);
    CHECK(mk_gnss_any_sentence_seen(&g) == 0, "부팅 직후에는 아직 아무것도 못 받았다");
}

static void test_sentence_seen_becomes_true_on_valid_sentence(void)
{
    /* 🔴 체크섬만 통과하면 된다 — RMC 의 상태 필드(A/V)와 무관하다.
     * "모듈이 말은 하고 있다" 는 사실이 전부다. */
    MkGnss g;
    mk_gnss_init(&g);
    feed_str(&g, "$GNRMC,120000.000,V,,,,,,,190826,,,N*54\r\n");
    CHECK(mk_gnss_any_sentence_seen(&g) == 1, "체크섬 통과 문장을 받으면 켜진다");
}

static void test_sentence_seen_stays_false_on_checksum_failure(void)
{
    MkGnss g;
    mk_gnss_init(&g);
    feed_str(&g, "$GNRMC,235959.999,A,4807.038,N,01131.000,E,022.4,084.4,180826,,,A*74\r\n");
    CHECK(mk_gnss_any_sentence_seen(&g) == 0, "체크섬이 틀리면 아직 안 켜진다");
}

static void test_sentence_seen_never_resets(void)
{
    /* 🔴 "받은 적 있는가" 는 부팅 이후 누적이다 — 한 번 켜지면 그 뒤로
     * 다시 조용해져도 꺼지지 않는다. */
    MkGnss g;
    mk_gnss_init(&g);
    feed_str(&g, "$GNRMC,235959.999,A,4807.038,N,01131.000,E,022.4,084.4,180826,,,A*73\r\n");
    CHECK(mk_gnss_any_sentence_seen(&g) == 1, "한 번 받았다");
    feed_str(&g, "$GNRMC,235959.999,A,4807.038,N,01131.000,E,022.4,084.4,180826,,,A*74\r\n");
    CHECK(mk_gnss_any_sentence_seen(&g) == 1, "그 뒤로 깨진 줄이 와도 유지된다");
}

int main(void)
{
    printf("-- UTC -> epoch_ms 손으로 박은 기준값 --\n");
    test_epoch_known_values();
    printf("-- 정상 RMC/GGA --\n");
    test_valid_rmc_is_parsed();
    test_valid_gga_is_parsed();
    test_gga_no_fix();
    printf("-- 되돌림: 체크섬 --\n");
    test_bad_checksum_is_dropped();
    test_missing_checksum_star_is_dropped();
    printf("-- 되돌림: 잘린 문장 --\n");
    test_truncated_sentence_never_completes();
    test_overlong_line_is_discarded_and_recovers();
    printf("-- 되돌림: 이어 붙은 문장 --\n");
    test_concatenated_sentences_both_parse();
    test_new_dollar_mid_sentence_restarts();
    printf("-- 되돌림: 유효하지 않은 fix --\n");
    test_invalid_fix_status_is_reported();
    printf("-- 원시 문장 에코 --\n");
    test_raw_line_is_captured_verbatim();
    test_raw_line_captured_even_on_bad_checksum();
    test_raw_queue_drops_oldest_when_full();
    printf("-- 문장 수신 여부 --\n");
    test_sentence_seen_starts_false();
    test_sentence_seen_becomes_true_on_valid_sentence();
    test_sentence_seen_stays_false_on_checksum_failure();
    test_sentence_seen_never_resets();

    printf(failures ? "\nFAILED (%d)\n" : "\nPASSED\n", failures);
    return failures ? 1 : 0;
}
