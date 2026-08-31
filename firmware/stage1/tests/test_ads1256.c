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
    /* 🔴 지연도 기록한다. ADS1256 은 명령과 데이터 사이에 **SCLK 이 쉬는
     *    시간**을 요구하는데(t6·t11), 그것은 보낸 바이트로는 드러나지
     *    않는다. 기록하지 않으면 시험이 통과하면서 실기기에서만 값이 튄다
     *    — 실제로 그랬다 [2026-08-17]. */
    uint32_t delay_us[MAX_XFER];
    int      n_delay;
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

static void fake_delay(void *ctx, uint32_t us)
{
    Fake *f = (Fake *)ctx;
    if (f->n_delay < MAX_XFER) { f->delay_us[f->n_delay++] = us; }
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
    MkAdsIo io = { fake_cs, fake_transfer, fake_delay, &FK };
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
    /* RDATA 전송 완료 → 드라이버가 t6 를 쉬고 데이터 3바이트를 시작한다. */
    mk_ads_on_spi_done(&A, now);

    RX[0] = (uint8_t)((code >> 16) & 0xFF);
    RX[1] = (uint8_t)((code >> 8) & 0xFF);
    RX[2] = (uint8_t)(code & 0xFF);
    mk_ads_on_spi_done(&A, now);
}


/* ---- 데이터시트가 요구하는 쉬는 시간 ------------------------------------ */

static uint32_t total_delay(void)
{
    uint32_t sum = 0;
    for (int i = 0; i < FK.n_delay; i++) { sum += FK.delay_us[i]; }
    return sum;
}

static void test_t6_gap_before_reading_data(void)
{
    /* 🔴 ADS1256 데이터시트 (SBAS288K) p.6, Timing Characteristics:
     *
     *      t6  "Delay from last SCLK edge for DIN to first SCLK rising edge
     *           for DOUT: RDATA, RDATAC, RREG Commands"   MIN 50 tCLKIN
     *
     *    이 보드의 크리스털은 7.68 MHz 이므로 50 x 130.2ns = 6.51us 다.
     *
     *    SCLK 이 500 kHz 라 한 주기가 2us 뿐이다. RDATA 에 데이터 3바이트를
     *    이어 붙이면 간격이 2us 밖에 안 되어 **3배 넘게 모자란다.**
     *
     *    지키지 않으면 칩이 DOUT 을 아직 안 실은 상태에서 클럭을 받는다.
     *    ADS1256 의 7.68 MHz 는 우리 클럭과 비동기라, 얼마나 실렸는지가
     *    매번 달라진다 — 값이 무작위로 튄다. 실기기에서 그 증상을 봤다
     *    [2026-08-17]. 잡음처럼 보이지만 잡음이 아니다. */
    setup(0);
    mk_ads_tick(&A, 100);
    drive_setup(100);

    int before = FK.n_delay;
    mk_ads_on_drdy(&A, 117);          /* RDATA 를 보낸다 */
    mk_ads_on_spi_done(&A, 118);      /* RDATA 완료 → 데이터 읽기 시작 */

    CHECK(FK.n_delay > before, "RDATA 와 데이터 사이에 쉰다");
    uint32_t gap = 0;
    for (int i = before; i < FK.n_delay; i++) { gap += FK.delay_us[i]; }
    CHECK(gap >= 7u, "쉬는 시간이 t6(6.51us) 이상이다");
    CHECK(FK.n == 5 && FK.len[4] == 3u, "그 뒤에 데이터 3바이트를 읽는다");
}

static void test_t11_gap_after_sync(void)
{
    /* 🔴 같은 표의 t11: SYNC 뒤 다음 명령의 첫 SCLK 상승까지 24 tCLKIN
     *    = 3.13us. WAKEUP 을 바로 붙이면 SYNC 가 먹지 않아 변환이 예전
     *    채널의 것으로 남는다. */
    setup(0);
    mk_ads_tick(&A, 100);
    mk_ads_on_spi_done(&A, 100);      /* WREG 완료 → SYNC 전송 */
    int before = FK.n_delay;
    mk_ads_on_spi_done(&A, 100);      /* SYNC 완료 → WAKEUP 전송 */

    uint32_t gap = 0;
    for (int i = before; i < FK.n_delay; i++) { gap += FK.delay_us[i]; }
    CHECK(gap >= 4u, "SYNC 와 WAKEUP 사이가 t11(3.13us) 이상이다");
}

static void test_delay_is_optional(void)
{
    /* 🔴 delay 를 안 준 io 로도 죽지 않아야 한다. 시험용 가짜나 옛 호출부가
     *    NULL 을 두고 갈 수 있는데, 거기서 널 역참조가 나면 원인이 이
     *    타이밍과 무관해 보인다. */
    memset(&FK, 0, sizeof FK);
    MkAdsIo io = { fake_cs, fake_transfer, NULL, &FK };
    MkAds a;
    mk_ads_init(&a, &io, TX, RX, sizeof TX, 500);
    mk_ads_attach_queue(&a, 0, QBUF[0], 8);
    mk_ads_configure(&a, 0, 1, 100, 0);
    mk_ads_tick(&a, 100);
    for (int i = 0; i < 3; i++) { mk_ads_on_spi_done(&a, 100); }
    mk_ads_on_drdy(&a, 117);
    mk_ads_on_spi_done(&a, 118);
    mk_ads_on_spi_done(&a, 119);
    CHECK(1, "delay 가 NULL 이어도 살아 있다");
    (void)total_delay();
}


/* ---- 칩 설정 (PGA · 데이터율) -------------------------------------------- */

static void test_chip_config_is_written_before_the_first_sample(void)
{
    /* 🔴 리셋 직후 칩은 CLKOUT 켜짐 · 30,000 SPS 다(데이터시트 p.31·p.32
     *    Reset Value). 우리가 안 쓰면 화면에 60 SPS 라고 떠 있어도 칩은
     *    30 kSPS 로 돈다 — 값이 나오기는 하니 아무도 눈치채지 못한다.
     *    실제로 그 상태로 한참 갔다 [2026-08-17]. */
    setup(0);
    mk_ads_tick(&A, 100);

    CHECK(FK.n == 1, "첫 전송 하나");
    CHECK(FK.bytes[0][0] == (MK_ADS_CMD_WREG | 0x00u),
          "STATUS(0x00) 부터 쓴다");
    CHECK(FK.bytes[0][1] == 3u, "n-1 = 3 -> STATUS·MUX·ADCON·DRATE 넷");
    CHECK(FK.len[0] == 6u, "명령 2 + 데이터 4 바이트");
    CHECK(FK.bytes[0][2] == 0x04u, "STATUS: ACAL=1 (설정이 바뀌면 자동 재교정)");
}

static void test_chip_config_is_not_rewritten_when_unchanged(void)
{
    /* 🔴 매 표본마다 다시 쓰지 않는다. ACAL 이 켜져 있어 값이 바뀌면
     *    자동 재교정이 도는데, 칩이 정말 값을 비교하는지 WREG 마다 도는지에
     *    설계를 걸 이유가 없다. 바뀔 때만 쓴다. */
    setup(0);
    mk_ads_tick(&A, 100);
    drive_setup(100);
    mk_ads_on_drdy(&A, 117);
    deliver(1000, 130);

    int before = FK.n;
    mk_ads_tick(&A, 200);
    CHECK(FK.n == before + 1, "다음 바퀴도 전송 하나로 시작한다");
    CHECK(FK.bytes[before][0] == (MK_ADS_CMD_WREG | MK_ADS_REG_MUX),
          "이번엔 MUX 만 쓴다");
    CHECK(FK.len[before] == 3u, "MUX 만이면 3바이트");
}

static void test_pga_and_drate_use_the_datasheet_codes(void)
{
    /* 데이터시트 p.31 ADCON: PGA 비트[2:0] 000=1 001=2 010=4 ... 110=64
     *              p.32 DRATE: 60 SPS = 01110010b = 0x72
     *
     * 🔴 ADCON 의 CLK 비트를 0 으로 둔다. 리셋값은 01(=fCLKIN 출력)인데,
     *    "When not using CLKOUT, it is recommended that it be turned off"
     *    (p.31). 안 쓰는 클럭이 나가면 잡음만 는다. */
    setup(0);
    mk_ads_set_chip(&A, 8u, 60u);
    mk_ads_tick(&A, 100);
    CHECK(FK.bytes[0][4] == 0x03u, "PGA 8배 -> ADCON 0x03 (CLKOUT 꺼짐)");
    CHECK(FK.bytes[0][5] == 0x72u, "60 SPS -> DRATE 0x72");

    setup(0);
    mk_ads_set_chip(&A, 64u, 2u);
    mk_ads_tick(&A, 100);
    CHECK(FK.bytes[0][4] == 0x06u, "PGA 64배 -> 0x06");
    CHECK(FK.bytes[0][5] == 0x03u, "2.5 SPS -> 0x03 (카탈로그엔 정수 2)");
}

static void test_changing_the_setting_rewrites_the_chip(void)
{
    setup(0);
    mk_ads_set_chip(&A, 1u, 60u);
    mk_ads_tick(&A, 100);
    drive_setup(100);
    mk_ads_on_drdy(&A, 117);
    deliver(1000, 130);

    mk_ads_set_chip(&A, 16u, 100u);
    int before = FK.n;
    mk_ads_tick(&A, 200);
    CHECK(FK.len[before] == 6u, "설정이 바뀌면 다시 쓴다");
    CHECK(FK.bytes[before][4] == 0x04u, "PGA 16배");
    CHECK(FK.bytes[before][5] == 0x82u, "100 SPS -> 0x82");
}

static void test_unknown_setting_falls_back_quietly(void)
{
    /* 🔴 모르는 값에 큰 이득을 넣지 않는다. 64배로 잘못 가면 입력이 즉시
     *    포화해 "센서가 이상하다" 로 보인다. 모르면 1배다. */
    setup(0);
    mk_ads_set_chip(&A, 7u, 12345u);
    mk_ads_tick(&A, 100);
    CHECK(FK.bytes[0][4] == 0x00u, "모르는 이득은 1배로");
    CHECK(FK.bytes[0][5] == 0x72u, "모르는 데이터율은 60 SPS 로");
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

    /* 🔴 첫 바퀴에는 칩 설정(STATUS·ADCON·DRATE)이 함께 나간다. MUX 는 그
     *    묶음 안에 들어 있다 — WREG 가 연속 레지스터를 이어 쓰기 때문이다.
     *    그래서 MUX 가 세 번째 데이터 바이트 자리에 온다. */
    CHECK(FK.n == 1 && FK.len[0] == 6, "먼저 WREG 6바이트 (칩 설정 포함)");
    CHECK(FK.bytes[0][0] == (MK_ADS_CMD_WREG | MK_ADS_REG_STATUS),
          "WREG|STATUS 부터");
    CHECK(FK.bytes[0][3] == ((3u << 4) | 0x08u), "PSEL=AIN3, NSEL=AINCOM");

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
    CHECK(mk_ads_state(&A) == MK_ADS_RDATA_SENT,
          "DRDY 가 읽기를 연다 — 먼저 RDATA 만 나간다");
    CHECK(FK.n == 4 && FK.bytes[3][0] == MK_ADS_CMD_RDATA, "RDATA 를 보낸다");
    /* 🔴 RDATA 만 홀로 나가야 한다. 데이터 3바이트를 이어 붙이면 SCLK 이
     *    쉬지 않아 t6 를 지킬 수 없다. */
    CHECK(FK.len[3] == 1, "RDATA 는 혼자 나간다 — 데이터는 t6 뒤에");
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

/* ---- 마지막 표본 (수집·송신 분리, 사용자 설계 2026-08-19) --------------- */

static void test_no_last_sample_before_the_first_acquisition(void)
{
    /* 🔴 설계 원칙 3·4 — 없는 값을 지어내지 않는다. mk_telem 이 이것으로
     *    "아직 한 번도 못 받은 채널은 안 보낸다"를 판단한다. */
    setup(0);
    MkSample s;
    CHECK(mk_ads_last(&A, 0, &s) == 0, "첫 획득 전에는 마지막 표본이 없다");
}

static void test_last_sample_updates_alongside_the_queue(void)
{
    setup(0);
    mk_ads_tick(&A, 100);
    drive_setup(100);
    mk_ads_on_drdy(&A, 117);
    deliver(4026531, 130);

    MkSample s;
    CHECK(mk_ads_last(&A, 0, &s) == 1, "이제 마지막 표본이 있다");
    CHECK(s.raw == 4026531, "값이 같다");
    CHECK(s.t_ms == 117, "시각도 DRDY 시각 그대로다 — 송신 시각이 아니다");
}

static void test_last_sample_survives_the_queue_being_drained(void)
{
    /* 🔴 이것이 "수집은 수집대로, 송신은 송신대로" 의 핵심이다 — 큐를
     *    비워도(mk_telem 이 예전에 하던 일, 또는 $STAT 진단) 마지막 표본
     *    자리는 남는다. 큐와 독립된 저장소라는 뜻이다. */
    setup(0);
    mk_ads_tick(&A, 100);
    drive_setup(100);
    mk_ads_on_drdy(&A, 117);
    deliver(4026531, 130);

    MkSample popped;
    CHECK(mk_queue_pop(mk_ads_queue(&A, 0), &popped) == 1, "큐에서 꺼낸다");
    CHECK(mk_queue_count(mk_ads_queue(&A, 0)) == 0, "큐는 이제 비었다");

    MkSample s;
    CHECK(mk_ads_last(&A, 0, &s) == 1, "그래도 마지막 표본은 남아 있다");
    CHECK(s.raw == 4026531, "값도 그대로");
}

static void test_last_sample_is_overwritten_by_the_next_acquisition(void)
{
    /* 수집이 송신보다 빠르면 중간 표본은 버려지고 최신 값만 남는다 —
     * "변수 a 에 계속 넣는다"는 사용자 설계 그대로. */
    setup(0);
    mk_ads_tick(&A, 100);
    drive_setup(100);
    mk_ads_on_drdy(&A, 117);
    deliver(111, 118);

    mk_ads_tick(&A, 200);
    drive_setup(200);
    mk_ads_on_drdy(&A, 217);
    deliver(222, 218);

    MkSample s;
    CHECK(mk_ads_last(&A, 0, &s) == 1, "표본이 있다");
    CHECK(s.raw == 222, "최신 값만 남는다 — 111 은 조용히 사라진다");
    CHECK(s.t_ms == 217, "시각도 최신 획득 시각으로 갱신된다");
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

static void test_reconfiguring_with_the_same_values_does_not_starve(void)
{
    /* 🔴 실기기에서 찾은 결함의 짝이다.
     *
     *    설정은 슈퍼루프가 매 바퀴 mk_ads_configure 로 밀어 넣는다 —
     *    GUI 에서 값이 바뀐 것을 알아챌 다른 통로가 없다. 그때마다
     *    next_due_ms 를 `now + period` 로 다시 잡으면 예정이 영원히
     *    도착하지 않고 채널이 **한 번도** 읽히지 않는다.
     *
     *    보드에 올리기 전에는 드러나지 않는다. 시험에서는 configure 를
     *    한 번만 부르기 때문이다. */
    setup(0);
    mk_ads_configure(&A, 0, 1, 100, 0);

    /* 슈퍼루프가 도는 흉내 — 매 ms 마다 같은 설정을 다시 밀어 넣는다. */
    for (int64_t t = 0; t <= 100; t++) {
        mk_ads_configure(&A, 0, 1, 100, t);
        mk_ads_tick(&A, t);
    }
    CHECK(mk_ads_state(&A) != MK_ADS_IDLE,
          "같은 값을 계속 밀어 넣어도 예정이 도착한다");
}

static void test_changing_the_period_does_reschedule(void)
{
    /* 위 최적화가 진짜 변경까지 무시하면 안 된다. */
    setup(0);
    mk_ads_configure(&A, 0, 1, 100, 0);
    mk_ads_configure(&A, 0, 1, 500, 0);
    mk_ads_tick(&A, 100);
    CHECK(mk_ads_state(&A) == MK_ADS_IDLE, "주기를 늘리면 그만큼 미뤄진다");
    mk_ads_tick(&A, 500);
    CHECK(mk_ads_state(&A) == MK_ADS_SETUP, "새 주기에 맞춰 돈다");
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
    CHECK(mk_ads_timeouts(&A, stuck) == 1, "그 채널에 타임아웃을 센다");

    /* 다른 채널은 계속 돈다 — 이것이 요점이다.
     *
     * 🔴 예전에는 여기서 한 번 IDLE 로 돌아갔다가 다음 tick 에 시작했다.
     *    지금은 finish() 가 밀린 채널로 곧바로 이어 간다 — 막힌 채널
     *    하나가 다음 채널의 시작까지 슈퍼루프 한 바퀴만큼 더 늦추지
     *    않는다. */
    CHECK(mk_ads_state(&A) == MK_ADS_SETUP, "포기한 자리에서 다음 채널이 시작된다");
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

/* ---- 채널당 10 ms — 슈퍼루프에 매이지 않는다 --------------------------- */

static void test_the_next_due_channel_starts_without_waiting_for_a_tick(void)
{
    /* 🔴 채널당 10 ms 를 요구하면 한 채널에 쓸 수 있는 시간이 1.43 ms 다
     *    (7채널 × 10 ms). 그런데 예전 판은 한 바퀴가 끝나면 IDLE 로 돌아가
     *    **슈퍼루프가 mk_ads_tick 을 부를 때까지** 다음 채널을 시작하지
     *    않았다.
     *
     *    슈퍼루프 한 바퀴는 그보다 훨씬 길다 — mk_i2c 의 HAL 블로킹이 최악
     *    60 ms(bsp/mk_i2c_io.c), 텔레메트리의 HAL_UART_Transmit 도 블로킹
     *    이라 한 줄에 1.8 ms 다. 즉 예전 판에서는 **채널당 10 ms 가 구조적
     *    으로 불가능**했고, 100 ms 에서도 밀린 만큼이 조용히 사라졌다
     *    (finish() 가 따라잡기를 포기하므로 큐의 drops 에도 안 잡힌다).
     *
     *    그래서 한 바퀴가 끝나는 자리(= 인터럽트 안)에서 다음에 밀린 채널을
     *    곧바로 시작한다. */
    setup(0);
    mk_ads_configure(&A, 0, 1, 10, 0);
    mk_ads_configure(&A, 1, 1, 10, 0);

    mk_ads_tick(&A, 10);                     /* 마지막 tick — 이후로는 안 부른다 */
    int first = mk_ads_current_channel(&A);
    drive_setup(10);
    mk_ads_on_drdy(&A, 11);
    deliver(111, 11);

    CHECK(mk_ads_state(&A) == MK_ADS_SETUP,
          "tick 없이도 다음 채널이 곧바로 시작된다");
    CHECK(mk_ads_current_channel(&A) != first, "다른 채널이다");
}

static void test_chaining_still_runs_the_full_sync_sequence(void)
{
    /* 🔴 이어 돌리면서 절차를 줄이면 **정착 시간이 사라진다.**
     *
     *    ADS1256.pdf p.21 "Settling Time Using the Input Multiplexer":
     *      "restart the conversion process by issuing the SYNC and WAKEUP
     *       commands ... There is no need to ignore or discard data while
     *       cycling through the channels of the input multiplexer because
     *       the ADS1256 fully settles before DRDY goes low"
     *
     *    즉 "버릴 변환이 없다"는 보장은 **MUX 를 바꾼 뒤 SYNC/WAKEUP 으로
     *    필터를 다시 채웠을 때만** 성립한다. SYNC 를 빼고 이어서 도는
     *    DRDY 를 그냥 읽으면 그 값은 이전 채널과 섞인 값이다(같은 문서
     *    Figure 21). 배선상 J3 에만 신호가 있는 지금은 그 오염이 "이웃
     *    채널이 J3 을 따라 움직인다" 로 나타난다. */
    setup(0);
    mk_ads_configure(&A, 0, 1, 10, 0);
    mk_ads_configure(&A, 2, 1, 10, 0);

    mk_ads_tick(&A, 10);
    drive_setup(10);
    mk_ads_on_drdy(&A, 11);
    deliver(111, 11);

    int base = FK.n;                          /* 이어 돌린 채널의 첫 전송 */
    CHECK(base >= 1 && FK.bytes[base - 1][0] ==
              (uint8_t)(MK_ADS_CMD_WREG | MK_ADS_REG_MUX),
          "이어 도는 채널도 MUX 부터 다시 쓴다");

    mk_ads_on_spi_done(&A, 12);
    CHECK(FK.n == base + 1 && FK.bytes[base][0] == MK_ADS_CMD_SYNC,
          "SYNC 를 건너뛰지 않는다");
    mk_ads_on_spi_done(&A, 12);
    CHECK(FK.n == base + 2 && FK.bytes[base + 1][0] == MK_ADS_CMD_WAKEUP,
          "WAKEUP 도 보낸다");
    mk_ads_on_spi_done(&A, 12);
    CHECK(mk_ads_state(&A) == MK_ADS_CONVERTING,
          "그리고 자기 DRDY 를 기다린다 — 정착 시간이 여기 있다");
}

static void test_chaining_does_not_run_ahead_of_the_schedule(void)
{
    /* 🔴 이어 돌리기가 "예정" 을 무시하면 안 된다. 무시하면 주기 설정이
     *    뜻을 잃고, 링크가 감당 못 할 만큼 표본이 쏟아져 큐가 넘친다 —
     *    고치려던 것과 정확히 반대 방향의 유실이다. */
    setup(0);
    mk_ads_configure(&A, 0, 1, 100, 0);
    mk_ads_configure(&A, 1, 1, 100, 0);

    mk_ads_tick(&A, 100);
    drive_setup(100);
    mk_ads_on_drdy(&A, 101);
    deliver(111, 101);
    CHECK(mk_ads_state(&A) == MK_ADS_SETUP, "함께 밀린 채널은 이어 돈다");

    drive_setup(101);
    mk_ads_on_drdy(&A, 102);
    deliver(222, 102);
    CHECK(mk_ads_state(&A) == MK_ADS_IDLE,
          "둘 다 냈으면 다음 예정까지 쉰다 — 앞질러 읽지 않는다");
}

static void test_a_seven_channel_round_needs_no_tick_at_all(void)
{
    /* 🔴 7채널 × 10 ms 의 실제 모양. 첫 tick 하나만 주고, 그다음은
     *    인터럽트 사건만으로 일곱 채널이 전부 표본을 남겨야 한다.
     *
     *    이것이 "수집에는 방해가 안 된다" 의 시험 형태다 — 슈퍼루프가
     *    통째로 멈춰 있어도(사용자 절대 제약) 한 바퀴가 끝난다. */
    static MkSample tail[3][8];
    setup(0);
    for (int ch = 0; ch < MK_ADS_CHANNELS; ch++) {
        if (ch >= 4) { mk_ads_attach_queue(&A, ch, tail[ch - 4], 8); }
        mk_ads_configure(&A, ch, 1, 10, 0);
    }

    mk_ads_tick(&A, 10);                      /* 딱 한 번 */

    int seen[MK_ADS_CHANNELS] = {0};
    int64_t t = 10;
    for (int i = 0; i < MK_ADS_CHANNELS; i++) {
        int ch = mk_ads_current_channel(&A);
        if (ch < 0) { break; }
        seen[ch]++;
        drive_setup(t);
        mk_ads_on_drdy(&A, t + 1);
        deliver(1000 + i, t + 1);
        t += 1;                               /* 채널 하나에 약 1 ms */
    }

    int covered = 0;
    for (int ch = 0; ch < MK_ADS_CHANNELS; ch++) {
        MkSample s;
        if (seen[ch] == 1 && mk_ads_last(&A, ch, &s)) { covered++; }
    }
    CHECK(covered == MK_ADS_CHANNELS,
          "tick 한 번으로 일곱 채널이 모두 한 표본씩 남긴다");
    CHECK(mk_ads_state(&A) == MK_ADS_IDLE, "다 돌면 다음 예정까지 쉰다");
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
    test_t6_gap_before_reading_data();
    test_t11_gap_after_sync();
    test_delay_is_optional();
    test_chip_config_is_written_before_the_first_sample();
    test_chip_config_is_not_rewritten_when_unchanged();
    test_pga_and_drate_use_the_datasheet_codes();
    test_changing_the_setting_rewrites_the_chip();
    test_unknown_setting_falls_back_quietly();
    test_sample_lands_in_the_queue();
    test_timestamp_is_the_drdy_moment();
    test_negative_code_is_sign_extended();
    test_no_last_sample_before_the_first_acquisition();
    test_last_sample_updates_alongside_the_queue();
    test_last_sample_survives_the_queue_being_drained();
    test_last_sample_is_overwritten_by_the_next_acquisition();
    test_round_robin_does_not_starve_later_channels();
    test_reconfiguring_with_the_same_values_does_not_starve();
    test_changing_the_period_does_reschedule();
    test_the_longest_waiting_channel_goes_first();
    test_drdy_timeout_frees_the_other_channels();
    test_stray_drdy_is_ignored();
    test_period_does_not_drift();
    test_far_behind_gives_up_catching_up();
    test_channel_without_a_queue_still_counts_drops();
    test_disabled_channel_is_never_read();
    test_the_next_due_channel_starts_without_waiting_for_a_tick();
    test_chaining_still_runs_the_full_sync_sequence();
    test_chaining_does_not_run_ahead_of_the_schedule();
    test_a_seven_channel_round_needs_no_tick_at_all();
    test_null_io_does_not_crash();
    printf(failures ? "FAILED (%d)\n" : "PASSED\n", failures);
    return failures ? 1 : 0;
}
