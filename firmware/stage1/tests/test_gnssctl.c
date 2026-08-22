/* mk_gnssctl 단위 시험 — GNSS 초기화 명령 재시도, HAL 비의존.
 *
 * 대상: firmware/stage1/app/mk_gnssctl.c
 *
 * 규격: protocol/specification.md §4.1.1
 */
#include <stdio.h>
#include <string.h>
#include "../app/mk_gnssctl.h"

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

/* ---- 보낸 명령을 모으는 통 --------------------------------------------- */

#define CAP 32
static char SENT[CAP][32];
static int  N;

static void sink_reset(void) { N = 0; }

static int fake_send(void *ctx, const char *text, size_t len)
{
    (void)ctx;
    if (N >= CAP) { return 0; }
    size_t n = len < sizeof SENT[0] - 1u ? len : sizeof SENT[0] - 1u;
    memcpy(SENT[N], text, n);
    SENT[N][n] = '\0';
    N++;
    return 1;
}

/* ---- 기본 상태 ---------------------------------------------------------- */

static void test_starts_idle(void)
{
    MkGnssCtl c;
    mk_gnssctl_init(&c);
    CHECK(mk_gnssctl_sent(&c) == 0, "아직 아무것도 안 보냈다");
    CHECK(mk_gnssctl_exhausted(&c) == 0, "재시도 상한도 아직");
    CHECK(mk_gnssctl_sentence_seen(&c) == 0, "받은 적도 없다");
}

static void test_disabled_sends_nothing(void)
{
    MkGnssCtl c;
    mk_gnssctl_init(&c);
    sink_reset();
    mk_gnssctl_tick(&c, 0, 0, 0, fake_send, NULL);
    CHECK(N == 0, "gnss.enabled 이 꺼져 있으면 아무것도 안 보낸다");
    CHECK(mk_gnssctl_sent(&c) == 0, "sent 도 그대로 거짓");
}

/* ---- 켤 때 즉시 보낸다 --------------------------------------------------- */

static void test_enabling_sends_the_two_log_commands_immediately(void)
{
    MkGnssCtl c;
    mk_gnssctl_init(&c);
    sink_reset();
    mk_gnssctl_tick(&c, 1, 0, 1000, fake_send, NULL);
    CHECK(N == 2, "켜지는 즉시 두 LOG 명령을 보낸다");
    if (N == 2) {
        /* 🔴 줄 끝 CR/LF 는 여기(app/)가 붙인다 — bsp 의 send 콜백은 받은
         * 바이트를 그대로 내보내기만 하는 얇은 관이다(규격 §4.1). */
        /* 🔴 0.05 = 20 Hz — mk_gnssctl.c 가 2026-08-20 에 실측 상한으로
         *    올린 값이다(그쪽 주석 참고). 이 시험이 1 인 채 낡아 있었다
         *    (2026-08-21 정정). */
        CHECK_EQ(SENT[0], "LOG GPRMC ONTIME 0.05\r\n", "시각에 필요한 문장");
        CHECK_EQ(SENT[1], "LOG GPGGA ONTIME 0.05\r\n", "위성 수를 보려면 GGA도");
    }

    /* IMU 를 켜면(클라우드 설계 §4.8) RAWIMUXA 가 묶음에 붙는다. */
    MkGnssCtl c2;
    mk_gnssctl_init(&c2);
    mk_gnssctl_set_imu(&c2, 1);
    sink_reset();
    mk_gnssctl_tick(&c2, 1, 0, 1000, fake_send, NULL);
    CHECK(N == 3, "IMU 켜짐 = 명령이 셋");
    if (N == 3) {
        CHECK_EQ(SENT[2], "RAWIMUXA 0.1\r\n", "10 Hz IMU 요구");
    }
    CHECK(mk_gnssctl_sent(&c) == 1, "sent 가 참이 된다");
    CHECK(mk_gnssctl_exhausted(&c) == 0, "아직 상한에 안 닿았다");
}

static void test_never_sends_saveconfig(void)
{
    /* 🔴 CLAUDE.md 규칙 — SAVECONFIG 는 사용자 모듈의 FLASH 를 바꾸는
     * 일이라 보드가 자동으로 보내면 안 된다. 필요하면 사용자가 $GNSS 로
     * 직접 보낸다. */
    MkGnssCtl c;
    mk_gnssctl_init(&c);
    sink_reset();
    mk_gnssctl_tick(&c, 1, 0, 1000, fake_send, NULL);
    for (int i = 0; i < N; i++) {
        CHECK(strstr(SENT[i], "SAVECONFIG") == NULL,
              "자동 초기화는 SAVECONFIG 를 보내지 않는다");
    }
}

/* ---- 재시도 간격·상한 ----------------------------------------------------- */

static void test_retries_are_spaced_and_capped(void)
{
    MkGnssCtl c;
    mk_gnssctl_init(&c);

    sink_reset();
    mk_gnssctl_tick(&c, 1, 0, 0, fake_send, NULL);       /* 1차 — 즉시 */
    CHECK(N == 2, "1차 시도");

    sink_reset();
    mk_gnssctl_tick(&c, 1, 0, 500, fake_send, NULL);     /* 500ms 뒤 — 아직 */
    CHECK(N == 0, "재시도 간격(2000ms) 전에는 다시 안 보낸다");

    sink_reset();
    mk_gnssctl_tick(&c, 1, 0, 2000, fake_send, NULL);    /* 정확히 2000ms 뒤 */
    CHECK(N == 2, "2000ms 지나면 2차 시도");

    sink_reset();
    mk_gnssctl_tick(&c, 1, 0, 4000, fake_send, NULL);    /* 3차 — 상한 소진 */
    CHECK(N == 2, "3차 시도");
    /* 🔴 3회가 상한이므로 3번째를 보낸 순간 이미 다 썼다 — 4번째 시도가
     * 실패로 돌아오기를 기다릴 필요가 없다. */
    CHECK(mk_gnssctl_exhausted(&c) == 1, "3차를 보낸 순간 상한을 다 썼다");

    sink_reset();
    mk_gnssctl_tick(&c, 1, 0, 6000, fake_send, NULL);    /* 4차 — 더는 안 보낸다 */
    CHECK(N == 0, "3회를 넘기면 더 이상 자동으로 안 보낸다");
    CHECK(mk_gnssctl_exhausted(&c) == 1, "재시도 상한에 닿은 채다");

    sink_reset();
    mk_gnssctl_tick(&c, 1, 0, 999999999, fake_send, NULL); /* 무한 재시도 금지 */
    CHECK(N == 0, "아무리 시간이 지나도 다시 보내지 않는다");
}

/* ---- 문장을 받으면 즉시 멈춘다 -------------------------------------------- */

static void test_receiving_a_sentence_stops_retries_immediately(void)
{
    MkGnssCtl c;
    mk_gnssctl_init(&c);
    sink_reset();
    mk_gnssctl_tick(&c, 1, 0, 0, fake_send, NULL);       /* 1차 */
    CHECK(N == 2, "1차 시도");

    sink_reset();
    /* 재시도 간격이 지나기 전에 문장이 왔다 — 그래도 즉시 멈춰야 한다. */
    mk_gnssctl_tick(&c, 1, 1, 100, fake_send, NULL);
    CHECK(N == 0, "문장을 받으면 재시도 간격을 기다리지 않고 바로 멈춘다");
    CHECK(mk_gnssctl_sentence_seen(&c) == 1, "받았다는 사실이 남는다");
    CHECK(mk_gnssctl_exhausted(&c) == 0, "성공했으니 상한과 무관하다");

    sink_reset();
    mk_gnssctl_tick(&c, 1, 1, 99999, fake_send, NULL);
    CHECK(N == 0, "그 뒤로도 다시 보내지 않는다");
}

/* ---- gnss.enabled 를 끄면 다시 처음부터 --------------------------------- */

static void test_disabling_resets_attempts_but_not_the_ever_seen_flag(void)
{
    MkGnssCtl c;
    mk_gnssctl_init(&c);
    sink_reset();
    mk_gnssctl_tick(&c, 1, 0, 0, fake_send, NULL);
    CHECK(mk_gnssctl_sent(&c) == 1, "한 번 보냈다");

    /* 🔴 mk_gnss 의 sentences_seen_count 는 부팅 이후 누적이라 gnss.enabled
     * 를 꺼도 내려가지 않는다(mk_gnss.h 주석) — 그래서 호출 쪽은 disabled
     * tick 에도 그 실제 값을 그대로 넘긴다. sentence_seen_cached 는 그
     * 값을 그대로 반영해야지, "꺼졌으니 모른다"로 되돌리면 안 된다. */
    sink_reset();
    mk_gnssctl_tick(&c, 0, 1, 1000, fake_send, NULL);
    CHECK(N == 0, "꺼지면 아무것도 안 보낸다");
    CHECK(mk_gnssctl_sent(&c) == 0, "attempts 는 다시 0 이다");
    CHECK(mk_gnssctl_sentence_seen(&c) == 1,
          "그런데 '받은 적 있다'는 사실은 그대로 반영된다");

    /* 다시 켜면 처음부터 재시도한다. */
    sink_reset();
    mk_gnssctl_tick(&c, 1, 0, 2000, fake_send, NULL);
    CHECK(N == 2, "재활성화하면 다시 즉시 보낸다");
}

/* ---- send 콜백이 없으면(1단계 빌드 등) 죽지 않는다 ----------------------- */

static void test_no_send_callback_does_not_crash_and_never_marks_sent(void)
{
    MkGnssCtl c;
    mk_gnssctl_init(&c);
    mk_gnssctl_tick(&c, 1, 0, 0, NULL, NULL);
    mk_gnssctl_tick(&c, 1, 0, 5000, NULL, NULL);
    CHECK(mk_gnssctl_sent(&c) == 0, "보낼 길이 없으면 sent 도 계속 거짓");
    CHECK(mk_gnssctl_exhausted(&c) == 0, "시도 자체를 안 했으니 상한도 아니다");
}

int main(void)
{
    printf("mk_gnssctl\n");
    test_starts_idle();
    test_disabled_sends_nothing();
    test_enabling_sends_the_two_log_commands_immediately();
    test_never_sends_saveconfig();
    test_retries_are_spaced_and_capped();
    test_receiving_a_sentence_stops_retries_immediately();
    test_disabling_resets_attempts_but_not_the_ever_seen_flag();
    test_no_send_callback_does_not_crash_and_never_marks_sent();
    printf(failures ? "\nFAILED (%d)\n" : "\nPASSED\n", failures);
    return failures ? 1 : 0;
}
