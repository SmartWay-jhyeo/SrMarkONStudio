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

/* ---- 측위 레코드 (규격 §7.8) --------------------------------------------
 *
 * 🔴 시험 벡터는 **실기기가 낸 문장 그대로**다(UM981, 2026-08-20 02:31:15
 *    UTC, 위성 20개, RTK fix). 지어낸 좌표로 시험하면 도-분 변환이 틀려도
 *    "그럴듯한 숫자"가 나와서 통과한다 — 그것이 이 절이 잡으려는 실패다.
 *
 *    기대값은 규격 §7.8.2 의 표에서 왔고, Python `decimal` 로 독립 계산해
 *    못박았다(소수 8자리, 2026-08-20 확장 — §0):
 *        3719.14416560 N -> 3731906943  (37.31906943°,  참값 37.31906942666…)
 *        12720.43544199 E -> 12734059070 (127.34059070°, 참값 127.34059069983…)
 */

#define REAL_GGA "$GNGGA,023115.00,3719.14416560,N,12720.43544199,E,1,20,1.2,100.8517,M,23.8989,M,,*70\r\n"
#define REAL_RMC "$GNRMC,023115.00,A,3719.14416560,N,12720.43544199,E,0.139,208.1,200826,8.6,W,A,C*5E\r\n"

static void test_ddmm_conversion_known_values(void)
{
    int64_t v = 0;
    CHECK(mk_gnss_ddmm_to_1e8("3719.14416560", 13, 'N', 1, &v) == 1 &&
          v == 3731906943LL, "위도 3719.14416560 N = 3731906943 (37.31906943도)");
    CHECK(mk_gnss_ddmm_to_1e8("12720.43544199", 14, 'E', 0, &v) == 1 &&
          v == 12734059070LL, "경도 12720.43544199 E = 12734059070 (127.34059070도)");

    /* 🔴 되돌림 검사 — 도-분을 그대로 십진도로 읽으면 3719.144… 다.
     *    그 실수를 하면 위 기대값과 자릿수부터 다르다. */
    CHECK(v != 12720435442LL, "도-분을 그대로 읽지 않는다");

    /* 남위·서경은 음수. */
    CHECK(mk_gnss_ddmm_to_1e8("3719.14416560", 13, 'S', 1, &v) == 1 &&
          v == -3731906943LL, "S 는 음수");
    CHECK(mk_gnss_ddmm_to_1e8("12720.43544199", 14, 'W', 0, &v) == 1 &&
          v == -12734059070LL, "W 는 음수");

    /* 분이 0 이면 도만 남는다 — 자릿수를 가르는 경계. */
    CHECK(mk_gnss_ddmm_to_1e8("0000.00000000", 13, 'N', 1, &v) == 1 && v == 0,
          "0000.0 = 적도");
    CHECK(mk_gnss_ddmm_to_1e8("9000.0", 6, 'N', 1, &v) == 1 && v == 9000000000LL,
          "9000.0 = 북극(위도 상한)");
    CHECK(mk_gnss_ddmm_to_1e8("18000.0", 7, 'E', 0, &v) == 1 && v == 18000000000LL,
          "18000.0 = 날짜변경선(경도 상한)");

    /* 🔴 [2026-08-20] int32 넘침 확인 — 경도 상한 18,000,000,000 은 int32
     *    상한(2,147,483,647)의 8배가 넘는다. `int32_t` 로 되돌리면 이 값이
     *    조용히 다른 수로 잘린다(18000000000 mod 2^32 = 820130816, 부호
     *    있는 32비트로는 양수라 오류로도 안 잡힌다) — 그래서 `mk_gnss_h`
     *    쪽 타입을 되돌리면 이 CHECK 가 가장 먼저, 그리고 조용하지 않게
     *    깨진다. 179°59.99999999′E(거의 날짜변경선 직전)도 같은 상한에
     *    닿는 값이라 함께 확인한다. */
    CHECK(mk_gnss_ddmm_to_1e8("17959.99999999", 14, 'E', 0, &v) == 1 &&
          v == 18000000000LL,
          "179도 59.99999999분 E, int32 상한을 훌쩍 넘는 값");

    /* 범위 밖·형식 오류는 거부한다 — 지어내지 않는다. */
    int64_t before = 12345;
    v = before;
    CHECK(mk_gnss_ddmm_to_1e8("9100.0", 6, 'N', 1, &v) == 0 && v == before,
          "위도 91도는 거부");
    CHECK(mk_gnss_ddmm_to_1e8("18100.0", 7, 'E', 0, &v) == 0 && v == before,
          "경도 181도는 거부");
    CHECK(mk_gnss_ddmm_to_1e8("", 0, 'N', 1, &v) == 0 && v == before,
          "빈 필드는 거부");
    CHECK(mk_gnss_ddmm_to_1e8("19.1", 4, 'N', 1, &v) == 0 && v == before,
          "정수부가 3자리 미만이면 거부(ddmm 이 아니다)");
    CHECK(mk_gnss_ddmm_to_1e8("3719.14416560", 13, 'X', 1, &v) == 0 &&
          v == before, "모르는 반구 문자는 거부");
}

static void test_real_device_fix_record(void)
{
    MkGnss g;
    mk_gnss_init(&g);
    feed_str(&g, REAL_GGA);

    MkGnssFix fix;
    CHECK(mk_gnss_take_fix(&g, &fix) == 0, "GGA 만으로는 레코드가 안 나간다");

    feed_str(&g, REAL_RMC);
    CHECK(mk_gnss_take_fix(&g, &fix) == 1, "RMC 가 오면 레코드가 하나 나온다");

    CHECK(fix.have_pos == 1, "fix 유효 — 위치가 있다");
    CHECK(fix.lat_1e8 == 3731906943LL, "위도 37.31906943");
    CHECK(fix.lon_1e8 == 12734059070LL, "경도 127.34059070");
    /* 2026-08-20 02:31:15.000 UTC */
    CHECK(fix.have_epoch == 1 && fix.epoch_ms == 1787193075000LL,
          "fix_t = 문장이 말하는 UTC");

    /* GGA 에서 온 것들 — 시각(023115.00)이 같아서 실린다. */
    CHECK(fix.have_alt == 1 && fix.alt_mm == 100852, "고도 100.852 m");
    CHECK(fix.have_sats == 1 && fix.sats == 20, "위성 20개");
    CHECK(fix.have_fix == 1 && fix.fix_quality == 1, "측위 품질 1");
    CHECK(fix.have_hdop == 1 && fix.hdop_1e2 == 120, "HDOP 1.20");

    /* RMC 에서 온 것들. 0.139 kn x 1852 / 3600 = 0.0715 m/s -> 72 mm/s */
    CHECK(fix.have_speed == 1 && fix.speed_mm_s == 72,
          "속도 0.139 노트 = 72 mm/s (노트가 아니라 m/s 로 낸다)");
    CHECK(fix.have_course == 1 && fix.course_1e2 == 20810, "방위 208.10도");

    CHECK(mk_gnss_take_fix(&g, &fix) == 0, "한 번 꺼내면 비었다");
}

static void test_gga_of_a_different_second_is_not_merged(void)
{
    /* 🔴 두 문장의 순서는 규격이 정하지 않는다. 시각으로 짝을 안 맞추면
     *    1초 전 고도가 지금 위치에 붙고, 화면에는 아무 이상이 없어 보인다
     *    (규격 §7.8.1). */
    MkGnss g;
    mk_gnss_init(&g);
    /* 023114 GGA — RMC(023115)보다 한 초 앞선다. */
    feed_str(&g, "$GNGGA,023114.00,3719.14416560,N,12720.43544199,E,1,20,1.2,100.8517,M,23.8989,M,,*71\r\n");
    feed_str(&g, REAL_RMC);

    MkGnssFix fix;
    CHECK(mk_gnss_take_fix(&g, &fix) == 1, "레코드는 나온다");
    CHECK(fix.have_pos == 1, "위치는 RMC 에서 오므로 그대로 있다");
    CHECK(fix.have_alt == 0 && fix.have_sats == 0 && fix.have_fix == 0 &&
          fix.have_hdop == 0,
          "시각이 다른 GGA 의 값은 안 붙는다 (null)");
}

static void test_no_fix_still_emits_a_record(void)
{
    /* 🔴 규격 §7.8.4 — 문장이 오는데 fix 가 없으면 **레코드를 낸다.**
     *    침묵하면 "모듈이 안 꽂혔다"와 "하늘이 안 보인다"가 화면에서
     *    똑같아 보인다(설계 원칙 4). 실기기 실내 관측이 정확히 이 경우다. */
    MkGnss g;
    mk_gnss_init(&g);
    feed_str(&g, "$GNGGA,120000.00,,,,,0,00,,,M,,M,,*55\r\n");
    feed_str(&g, "$GNRMC,120000.000,V,,,,,,,190826,,,N*54\r\n");

    MkGnssFix fix;
    CHECK(mk_gnss_take_fix(&g, &fix) == 1, "fix 가 없어도 레코드는 나간다");
    CHECK(fix.have_pos == 0, "위치는 null");
    CHECK(fix.have_fix == 1 && fix.fix_quality == 0, "측위 품질 0 은 실린다");
    CHECK(fix.have_sats == 1 && fix.sats == 0, "위성 0개도 실린다");
    CHECK(fix.have_alt == 0, "고도는 null");
    CHECK(fix.have_speed == 0 && fix.have_course == 0,
          "빈 속도·방위 필드는 null (0 을 지어내지 않는다)");
    CHECK(fix.have_epoch == 1, "날짜·시각은 읽혔으므로 fix_t 는 있다");
}

static void test_fix_record_needs_no_date(void)
{
    /* 날짜 필드가 비어 있는 RMC(수신기가 아직 날짜를 모를 때). 시간축은
     * 이것을 못 쓰지만(epoch 을 만들 수 없다), 위치는 여전히 값이 있다. */
    MkGnss g;
    mk_gnss_init(&g);
    feed_str(&g, "$GNRMC,023115.00,A,3719.14416560,N,12720.43544199,E,0.139,208.1,,8.6,W,A,C*50\r\n");

    MkGnssRmc rmc;
    CHECK(mk_gnss_take_rmc(&g, &rmc) == 0,
          "날짜가 없으면 시간축용 RMC 는 안 나온다 (예전 그대로)");

    MkGnssFix fix;
    CHECK(mk_gnss_take_fix(&g, &fix) == 1, "그래도 측위 레코드는 나간다");
    CHECK(fix.have_pos == 1 && fix.lat_1e8 == 3731906943LL, "위치는 그대로");
    CHECK(fix.have_epoch == 0, "fix_t 는 null");
}

static void test_bad_checksum_makes_no_fix_record(void)
{
    MkGnss g;
    mk_gnss_init(&g);
    feed_str(&g, "$GNRMC,023115.00,A,3719.14416560,N,12720.43544199,E,0.139,208.1,200826,8.6,W,A,C*5F\r\n");

    MkGnssFix fix;
    CHECK(mk_gnss_take_fix(&g, &fix) == 0,
          "체크섬이 틀린 문장에서는 레코드가 안 나온다");
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
    printf("-- 측위 레코드 (규격 §7.8) --\n");
    test_ddmm_conversion_known_values();
    test_real_device_fix_record();
    test_gga_of_a_different_second_is_not_merged();
    test_no_fix_still_emits_a_record();
    test_fix_record_needs_no_date();
    test_bad_checksum_makes_no_fix_record();

    printf(failures ? "\nFAILED (%d)\n" : "\nPASSED\n", failures);
    return failures ? 1 : 0;
}
