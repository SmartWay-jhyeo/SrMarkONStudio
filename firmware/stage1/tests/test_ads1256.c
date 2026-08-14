/* mk_ads1256 상태머신 단위 시험. 보드도 SPI 도 필요 없다.
 *
 * 가짜 IO 가 전송을 기록하고, 시험이 손으로 사건을 넣는다:
 *   tick -> (SETUP 3전송) -> drdy -> (READ 전송) -> 표본
 *
 * 🔴 이 층이 **기다리지 않는다**는 것을 시험이 증명한다. 어떤 함수도
 *    호출자를 붙잡지 않고, 사건이 안 오면 상태가 그대로 있을 뿐이다.
 */
#include <stdio.h>
#include <string.h>
#include "../app/mk_ads1256.h"

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
    if (!(cond)) { printf("  FAIL %s\n", msg); failures++; }    \
    else         { printf("  ok   %s\n", msg); }                \
} while (0)

/* ---- 가짜 SPI ----------------------------------------------------------- */

#define MAX_XFER 32
typedef struct {
    uint8_t bytes[MAX_XFER][8];
    size_t  len[MAX_XFER];
    int     n;
    int     cs_low;
    int     cs_changes;
} Fake;

static Fake FK;
static MkAds A;
static uint8_t TX[8], RX[8];
static MkSample QBUF[4][8];

static void fake_cs(void *ctx, int low)
{
    Fake *f = (Fake *)ctx;
    if (f->cs_low != low) { f->cs_changes++; }
    f->cs_low = low;
}

static void fake_transfer(void *ctx, const uint8_t *tx, uint8_t *rx, size_t n)
{
    Fake *f = (Fake *)ctx;
    if (f->n < MAX_XFER) {
        size_t k = n < 8u ? n : 8u;
        memcpy(f->bytes[f->n], tx, k);
        f->len[f->n] = n;
        f->n++;
    }
    (void)rx;   /* 수신값은 시험이 RX 에 직접 채운다 (deliver 참조) */
}

static void setup(int64_t now)
{
    memset(&FK, 0, sizeof FK);
    MkAdsIo io = { fake_cs, fake_transfer, &FK };
    mk_ads_init(&A, &io, TX, RX, sizeof TX, 500);
    for (int i = 0; i < 4; i++) {
        mk_ads_attach_queue(&A, i, QBUF[i], 8);
    }
    mk_ads_configure(&A, 0, 1, 100, now);
}

/* SETUP 3전송을 완료시켜 CONVERTING 까지 민다. */
static void drive_setup(int64_t now)
{
    for (int i = 0; i < 3; i++) {
        mk_ads_on_spi_done(&A, now);
    }
}

/* 원시값을 실어 READ 를 완료시킨다.
 *
 * 🔴 수신 버퍼를 **먼저** 채우고 나서 완료를 알린다. 실기기의 순서가
 *    그렇다 — DMA 가 버퍼를 다 채운 뒤에 완료 인터럽트가 뜬다.
 *    처음에 순서를 뒤집어 놓았더니 값이 0 으로 나왔고, 그것을 드라이버
 *    결함으로 오해할 뻔했다. */
static void deliver(int32_t code, int64_t now)
{
    RX[0] = 0xFF;                               /* 명령 나가는 동안의 쓰레기 */
    RX[1] = (uint8_t)((code >> 16) & 0xFF);
    RX[2] = (uint8_t)((code >> 8) & 0xFF);
    RX[3] = (uint8_t)(code & 0xFF);
    mk_ads_on_spi_done(&A, now);
}

/* ---- 시험 --------------------------------------------------------------- */

static void test_idle_until_due(void)
{
    setup(0);
    mk_ads_tick(&A, 50);
    CHECK(mk_ads_state(&A) == MK_ADS_IDLE, "때가 안 되면 아무것도 안 한다");
    CHECK(FK.n == 0, "전송도 없다");

    mk_ads_tick(&A, 100);
    CHECK(mk_ads_state(&A) == MK_ADS_SETUP, "때가 되면 시작한다");
}

static void test_setup_sequence_matches_the_datasheet(void)
{
    /* 데이터시트의 멀티플렉서 사이클링: MUX 갱신 -> SYNC -> WAKEUP */
    setup(0);
    mk_ads_configure(&A, 3, 1, 100, 0);
    mk_ads_configure(&A, 0, 0, 0, 0);
    mk_ads_tick(&A, 100);

    CHECK(FK.n == 1 && FK.len[0] == 3, "먼저 WREG 3바이트");
    CHECK(FK.bytes[0][0] == (MK_ADS_CMD_WREG | MK_ADS_REG_MUX), "WREG|MUX");
    CHECK(FK.bytes[0][2] == ((3u << 4) | 0x08u), "PSEL=AIN3, NSEL=AINCOM");

    mk_ads_on_spi_done(&A, 100);
    CHECK(FK.n == 2 && FK.bytes[1][0] == MK_ADS_CMD_SYNC, "다음은 SYNC");

    mk_ads_on_spi_done(&A, 100);
    CHECK(FK.n == 3 && FK.bytes[2][0] == MK_ADS_CMD_WAKEUP, "다음은 WAKEUP");

    mk_ads_on_spi_done(&A, 100);
    CHECK(mk_ads_state(&A) == MK_ADS_CONVERTING, "그리고 변환을 기다린다");
    CHECK(FK.n == 3, "🔴 기다리는 동안 아무것도 보내지 않는다");
}

static void test_does_not_block_while_converting(void)
{
    /* 🔴 이 시험이 이 파일의 존재 이유다. 변환 16.84 ms 동안 tick 을
     *    아무리 불러도 상태가 그대로이고 즉시 돌아온다 — CPU 가 묶이지
     *    않는다는 뜻이다. 참고 구현은 여기서 `while` 을 돌았다. */
    setup(0);
    mk_ads_tick(&A, 100);
    drive_setup(100);
    for (int64_t t = 100; t < 117; t++) {
        mk_ads_tick(&A, t);
    }
    CHECK(mk_ads_state(&A) == MK_ADS_CONVERTING, "17 ms 동안 그대로 기다린다");
    CHECK(FK.n == 3, "그동안 전송이 늘지 않는다");
}

static void test_drdy_starts_the_read(void)
{
    setup(0);
    mk_ads_tick(&A, 100);
    drive_setup(100);
    mk_ads_on_drdy(&A, 117);
    CHECK(mk_ads_state(&A) == MK_ADS_READING, "DRDY 가 읽기를 연다");
    CHECK(FK.n == 4 && FK.bytes[3][0] == MK_ADS_CMD_RDATA, "RDATA 를 보낸다");
    CHECK(FK.len[3] == 4, "명령 1 + 데이터 3 바이트");
}

static void test_sample_lands_in_the_queue(void)
{
    setup(0);
    mk_ads_tick(&A, 100);
    drive_setup(100);
    mk_ads_on_drdy(&A, 117);
    deliver(0x0012D687, 130);

    MkQueue *q = mk_ads_queue(&A, 0);
    MkSample s;
    CHECK(mk_queue_count(q) == 1, "표본이 하나 들어왔다");
    CHECK(mk_queue_pop(q, &s), "꺼낼 수 있다");
    CHECK(s.raw == 0x0012D687, "원시값이 그대로");
    CHECK(mk_ads_state(&A) == MK_ADS_IDLE, "한 바퀴가 끝나면 쉰다");
}

static void test_timestamp_is_the_drdy_moment(void)
{
    /* 🔴 설계 원칙 2 (CLAUDE.md §3) — 획득 시각은 STM32 가 확정한다.
     *    DRDY 가 117 ms 에 떨어졌고 SPI 가 130 ms 에 끝났다면 측정 시각은
     *    117 이다. 130 을 쓰면 SPI·DMA 지연이 타임스탬프를 오염시킨다. */
    setup(0);
    mk_ads_tick(&A, 100);
    drive_setup(100);
    mk_ads_on_drdy(&A, 117);
    deliver(1234, 130);

    MkSample s;
    mk_queue_pop(mk_ads_queue(&A, 0), &s);
    CHECK(s.t_ms == 117, "SPI 가 끝난 시각(130)이 아니라 DRDY 시각(117)");
}

static void test_negative_code_is_sign_extended(void)
{
    setup(0);
    mk_ads_tick(&A, 100);
    drive_setup(100);
    mk_ads_on_drdy(&A, 117);
    deliver(-1, 130);                       /* 0xFFFFFF */

    MkSample s;
    mk_queue_pop(mk_ads_queue(&A, 0), &s);
    CHECK(s.raw == -1, "24비트 2의 보수가 부호확장된다");
}

static void test_round_robin_does_not_starve_later_channels(void)
{
    /* 주기가 크게 다른 두 채널을 섞어도 느린 쪽이 굶지 않는지 본다.
     * ch0 은 10 ms 주기라 한 바퀴(20 ms)가 도는 사이에 이미 다시 밀린다.
     *
     * 🔴 굶주림을 막는 것은 채널 고르는 순서가 아니라 finish() 의
     *    **따라잡기 포기**다. 방금 읽은 채널은 next_due 가 지금 기준으로
     *    다시 잡히므로, 아무리 밀려 있었어도 곧바로 또 이기지 못한다.
     *
     *    처음에는 이 시험에 "첨자 순서로 고르면 ch3 이 굶는다" 고 적어
     *    두었는데, 되돌림 검사에서 정렬을 지워도 안 깨졌다. 확인해 보니
     *    따라잡기 포기가 이미 막고 있었다 — 주석이 과장이었다.
     *    정렬이 실제로 하는 일은 아래 시험이 따로 본다. */
    setup(0);
    mk_ads_configure(&A, 0, 1, 10, 0);      /* 늘 밀려 있는 채널 */
    mk_ads_configure(&A, 3, 1, 100, 0);     /* 느긋한 채널 */

    int seen[MK_ADS_CHANNELS] = {0};
    int64_t t = 100;
    for (int round = 0; round < 20; round++) {
        mk_ads_tick(&A, t);
        int ch = mk_ads_current_channel(&A);
        if (ch < 0) { t += 10; continue; }
        seen[ch]++;
        drive_setup(t);
        mk_ads_on_drdy(&A, t + 17);
        deliver(round, t + 18);
        t += 20;                            /* 한 바퀴에 20 ms */
    }
    CHECK(seen[0] > 0, "빠른 채널은 당연히 돈다");
    CHECK(seen[3] > 0, "느린 채널도 차례가 온다 (굶지 않는다)");
}

static void test_the_longest_waiting_channel_goes_first(void)
{
    /* 🔴 여러 채널이 한꺼번에 밀렸을 때 **가장 오래 기다린 것**부터 읽는다.
     *
     *    굶주림 방지가 아니다 — 굶지 않는 것은 따라잡기 포기 덕분이고,
     *    첨자 순서로 골라도 굶지는 않는다(위 시험이 그것을 본다).
     *    이 시험이 보는 것은 **최악 지연**이다. 첨자 순서로 고르면 뒤
     *    채널이 늘 더 기다리게 되고, 그 편차가 쌓인다.
     *
     *    ch3 을 더 일찍 예약해 두고 둘 다 밀린 시점에 tick 한다. */
    setup(0);
    mk_ads_configure(&A, 0, 1, 100, 0);       /* 예정 100 */
    mk_ads_configure(&A, 3, 1, 100, -50);     /* 예정 50 — 더 오래 기다렸다 */

    mk_ads_tick(&A, 200);                     /* 둘 다 밀린 시점 */
    CHECK(mk_ads_current_channel(&A) == 3,
          "첨자가 뒤라도 더 오래 기다린 쪽이 먼저다");
}

static void test_drdy_timeout_frees_the_other_channels(void)
{
    /* 🔴 배선이 빠지거나 칩이 죽어 DRDY 가 영영 안 오면, 상태머신이
     *    갇히면서 **다른 채널도 전부 멈춘다.** 한 채널의 고장이 전체를
     *    세우는 것이 설계 원칙이 막으려는 것이다. */
    setup(0);
    mk_ads_configure(&A, 0, 1, 100, 0);
    mk_ads_configure(&A, 1, 1, 100, 0);

    mk_ads_tick(&A, 100);
    int stuck = mk_ads_current_channel(&A);
    drive_setup(100);
    CHECK(mk_ads_state(&A) == MK_ADS_CONVERTING, "변환을 기다리는 중");

    mk_ads_tick(&A, 500);
    CHECK(mk_ads_state(&A) == MK_ADS_CONVERTING, "타임아웃 전에는 기다린다");

    mk_ads_tick(&A, 700);                   /* 500 ms 초과 */
    CHECK(mk_ads_state(&A) == MK_ADS_IDLE, "포기하고 풀려난다");
    CHECK(mk_ads_timeouts(&A, stuck) == 1, "그 채널에 타임아웃을 센다");

    /* 다른 채널은 계속 돈다 — 이것이 요점이다. */
    mk_ads_tick(&A, 700);
    CHECK(mk_ads_state(&A) == MK_ADS_SETUP, "다음 채널이 시작된다");
    CHECK(mk_ads_current_channel(&A) != stuck, "막힌 채널이 아니다");
}

static void test_stray_drdy_is_ignored(void)
{
    /* 🔴 DRDY 는 우리가 안 시켜도 데이터율마다 계속 떨어진다. 상태를 안
     *    보고 반응하면 SETUP 도중에 읽기를 시작한다. */
    setup(0);
    mk_ads_on_drdy(&A, 50);
    CHECK(mk_ads_state(&A) == MK_ADS_IDLE, "쉬는 중의 DRDY 는 무시");
    CHECK(FK.n == 0, "전송하지 않는다");

    mk_ads_tick(&A, 100);
    mk_ads_on_drdy(&A, 101);
    CHECK(mk_ads_state(&A) == MK_ADS_SETUP, "SETUP 중의 DRDY 도 무시");
    CHECK(FK.n == 1, "WREG 하나만 나가 있다");
}

static void test_period_does_not_drift(void)
{
    /* 🔴 다음 예정을 `now + period` 로 잡으면 한 바퀴에 걸린 시간만큼
     *    주기가 늘어나 조금씩 뒤로 흐른다. 100 ms 주기가 117 ms 가 된다. */
    setup(0);
    int64_t t = 100;
    for (int round = 0; round < 5; round++) {
        mk_ads_tick(&A, t);
        if (mk_ads_current_channel(&A) < 0) {
            printf("  FAIL %d 번째 주기를 놓쳤다 (t=%lld)\n", round, (long long)t);
            failures++;
            return;
        }
        drive_setup(t);
        mk_ads_on_drdy(&A, t + 17);
        deliver(round, t + 18);
        t += 100;                            /* 정확히 주기마다 */
    }
    CHECK(1, "주기가 뒤로 밀리지 않는다");
}

static void test_far_behind_gives_up_catching_up(void)
{
    /* 너무 밀렸으면 따라잡기를 포기한다. 안 그러면 밀린 만큼 몰아서
     * 읽으려 들며 큐를 터뜨린다. */
    setup(0);
    mk_ads_tick(&A, 100);
    drive_setup(100);
    mk_ads_on_drdy(&A, 117);
    deliver(1, 5000);                        /* 한참 뒤에야 끝났다 */

    mk_ads_tick(&A, 5000);
    CHECK(mk_ads_state(&A) == MK_ADS_IDLE, "곧바로 다음 것을 몰아 읽지 않는다");
    mk_ads_tick(&A, 5100);
    CHECK(mk_ads_state(&A) == MK_ADS_SETUP, "한 주기 뒤부터 다시 돈다");
}

static void test_channel_without_a_queue_still_counts_drops(void)
{
    /* 큐를 안 붙인 채널(4~6)도 조용히 사라지면 안 된다. */
    setup(0);
    mk_ads_configure(&A, 0, 0, 0, 0);
    mk_ads_configure(&A, 5, 1, 100, 0);
    mk_ads_tick(&A, 100);
    drive_setup(100);
    mk_ads_on_drdy(&A, 117);
    deliver(42, 118);
    CHECK(mk_queue_drops(mk_ads_queue(&A, 5)) == 1,
          "저장소가 없어도 버린 수는 센다");
}

static void test_disabled_channel_is_never_read(void)
{
    setup(0);
    mk_ads_configure(&A, 0, 0, 100, 0);
    for (int64_t t = 0; t < 1000; t += 10) {
        mk_ads_tick(&A, t);
    }
    CHECK(FK.n == 0, "꺼진 채널은 건드리지 않는다");
    CHECK(mk_ads_state(&A) == MK_ADS_IDLE, "계속 쉰다");
}

static void test_null_io_does_not_crash(void)
{
    /* 아직 SPI 가 준비되지 않은 부팅 초기. */
    MkAds a;
    mk_ads_init(&a, NULL, NULL, NULL, 0, 500);
    mk_ads_configure(&a, 0, 1, 100, 0);
    mk_ads_tick(&a, 100);
    mk_ads_on_spi_done(&a, 100);
    mk_ads_on_drdy(&a, 117);
    mk_ads_tick(&a, 1000);
    CHECK(1, "IO 가 없어도 죽지 않는다");
}

int main(void)
{
    printf("mk_ads1256\n");
    test_idle_until_due();
    test_setup_sequence_matches_the_datasheet();
    test_does_not_block_while_converting();
    test_drdy_starts_the_read();
    test_sample_lands_in_the_queue();
    test_timestamp_is_the_drdy_moment();
    test_negative_code_is_sign_extended();
    test_round_robin_does_not_starve_later_channels();
    test_the_longest_waiting_channel_goes_first();
    test_drdy_timeout_frees_the_other_channels();
    test_stray_drdy_is_ignored();
    test_period_does_not_drift();
    test_far_behind_gives_up_catching_up();
    test_channel_without_a_queue_still_counts_drops();
    test_disabled_channel_is_never_read();
    test_null_io_does_not_crash();
    printf(failures ? "FAILED (%d)\n" : "PASSED\n", failures);
    return failures ? 1 : 0;
}
