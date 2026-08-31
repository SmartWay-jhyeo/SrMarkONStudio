/* mk_lcd 단위 시험 — 보드도 패널도 필요 없다.
 *
 * 🔴 여기서 지키는 것은 **순서와 대기시간**이다. ILI9488 은 명령을 받고
 *    실제로 준비되기까지 시간이 필요한 칩이라(리셋 취소 120 ms, Sleep Out
 *    5 ms), 순서만 맞고 대기가 빠지면 **가끔** 안 켜진다 — 실기기에서 가장
 *    가리기 어려운 형태의 고장이다. 대기시간을 여기서 못박는다.
 *
 * 🔴 그리고 "슈퍼루프를 막지 않는가" 를 본다. 전면 갱신이 460,800 바이트라
 *    동기 전송으로 짜면 한 번에 200 ms 넘게 서고, 그동안 ADS1256 표본과
 *    텔레메트리가 통째로 밀린다. 가짜 전송은 **저절로 끝나지 않는다** —
 *    시험이 손으로 mk_lcd_on_tx_done() 을 불러야만 다음 걸음이 나간다.
 *    상태기계가 완료를 기다리며 도는 순간 이 시험들이 멈춰 선다.
 */
#include <stdio.h>
#include <string.h>

#include "../app/mk_lcd.h"
#include "../app/mk_cfgtable.h"

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
    if (!(cond)) { printf("  FAIL %s\n", msg); failures++; }    \
    else         { printf("  ok   %s\n", msg); }                \
} while (0)

/* ---- 가짜 버스 ---------------------------------------------------------- */

/* 🔴 전면 한 장이 480행 + 명령 몇 개다. 주기 갱신 시험은 그것을 네 번쯤
 *    보므로 1024 로는 통째로 모자란다 — 기록이 넘치면 시험이 "갱신이 안
 *    됐다" 고 거짓말한다(실제로 그렇게 한 번 속았다). */
#define MAX_EV 8192

typedef struct {
    /* 전송 기록 */
    int      dc[MAX_EV];        /* 0 = 명령, 1 = 데이터 */
    size_t   len[MAX_EV];
    uint8_t  first[MAX_EV];
    int      cs_low[MAX_EV];    /* 그 전송 시점의 CS 상태 */
    int64_t  at[MAX_EV];        /* 그 전송을 시작한 시각 */
    int      n;

    /* 지금 상태 */
    int      cur_dc;
    int      cur_cs_low;
    int      cur_reset_low;
    int      backlight_on;
    int64_t  now;

    int64_t  reset_low_ms;      /* RESX 를 내린 시각 (없으면 -1) */
    int64_t  reset_high_ms;
    int      reset_pulses;

    int      backlight_on_at_ev;  /* 백라이트를 켤 때까지 나간 전송 수 */

    /* ── 되읽기 기록 ────────────────────────────────────────────────
     *
     * 🔴 `send` 와 **따로** 센다. 되읽기는 그림을 안 그리므로, 같은 통에
     *    넣으면 "다 그린 뒤에는 조용하다" 같은 시험이 되읽기 때문에
     *    깨지는지 되읽기가 그림을 밀어낸 것인지 구분되지 않는다. */
    int      xf_dc[MAX_EV];
    size_t   xf_len[MAX_EV];
    uint8_t  xf_first[MAX_EV];
    int      xf_cs_low[MAX_EV];
    int      xf_n;

    /* 패널이 되돌려줄 값. 시험이 바꿔 가며 "깨진 패널" 을 흉내 낸다. */
    uint8_t  reply_madctl;
    uint8_t  reply_colmod;
    uint8_t  last_read_cmd;

    /* SPI 클럭 창구 */
    uint32_t clock_khz;
    int      clock_calls;
} Bus;

static Bus     BUS;
static MkLcd   LCD;
static MkConfig CFG;

static uint8_t CMDBUF[MK_LCD_CMD_BUF_BYTES];
static uint8_t ROWBUF[MK_LCD_ROW_BYTES];

static void fake_cs(void *ctx, int low)
{
    Bus *b = (Bus *)ctx;
    b->cur_cs_low = low;
}

static void fake_dc(void *ctx, int data)
{
    Bus *b = (Bus *)ctx;
    b->cur_dc = data;
}

static void fake_reset(void *ctx, int low)
{
    Bus *b = (Bus *)ctx;
    if (low && !b->cur_reset_low) {
        b->reset_low_ms = b->now;
        b->reset_pulses++;
    }
    if (!low && b->cur_reset_low) {
        b->reset_high_ms = b->now;
    }
    b->cur_reset_low = low;
}

static void fake_backlight(void *ctx, int on)
{
    Bus *b = (Bus *)ctx;
    if (on && !b->backlight_on) {
        b->backlight_on_at_ev = b->n;
    }
    b->backlight_on = on;
}

/* 🔴 저절로 끝나지 않는다. 시험이 mk_lcd_on_tx_done() 을 불러야 다음 걸음이
 *    나간다 — 상태기계가 완료를 기다리며 돌면 시험이 멈춘다. */
static int fake_send(void *ctx, const uint8_t *buf, size_t n)
{
    Bus *b = (Bus *)ctx;
    if (b->n < MAX_EV) {
        b->dc[b->n] = b->cur_dc;
        b->len[b->n] = n;
        b->first[b->n] = n ? buf[0] : 0u;
        b->cs_low[b->n] = b->cur_cs_low;
        b->at[b->n] = b->now;
        b->n++;
    }
    return 1;
}

/* 🔴 되읽기도 저절로 끝나지 않는다. `send` 와 같은 규약이다 — 시험이
 *    mk_lcd_on_tx_done() 을 불러야 다음 걸음이 나간다. 되읽기 중에도
 *    상태기계가 완료를 기다리며 돌면 시험이 멈춘다.
 *
 *    ILI9488.pdf p.122 Figure 108 "4-Line SPI Mode Read Data": 명령
 *    바이트 뒤에 **8 Dummy Clock** 이 붙고 그 다음이 실제 데이터다.
 *    명령표(p.155 §5.2.6 등)의 "1st Parameter is a dummy data" 와 같은
 *    말이다. 그래서 여기서도 첫 바이트를 쓰레기로 돌려준다. */
static int fake_xfer(void *ctx, const uint8_t *tx, uint8_t *rx, size_t n)
{
    Bus *b = (Bus *)ctx;
    if (b->xf_n < MAX_EV) {
        b->xf_dc[b->xf_n] = b->cur_dc;
        b->xf_len[b->xf_n] = n;
        b->xf_first[b->xf_n] = n ? tx[0] : 0u;
        b->xf_cs_low[b->xf_n] = b->cur_cs_low;
        b->xf_n++;
    }
    if (b->cur_dc == 0 && n > 0u) {
        b->last_read_cmd = tx[0];       /* 명령 단계 */
        rx[0] = 0xFFu;
        return 1;
    }
    /* 데이터 단계 — 첫 바이트는 더미다. */
    for (size_t k = 0; k < n; k++) { rx[k] = 0x5Au; }
    if (n >= 2u) {
        rx[1] = (b->last_read_cmd == 0x0Bu) ? b->reply_madctl
              : (b->last_read_cmd == 0x0Cu) ? b->reply_colmod
              : 0x00u;
    }
    return 1;
}

static void fake_set_clock(void *ctx, uint32_t khz)
{
    Bus *b = (Bus *)ctx;
    b->clock_khz = khz;
    b->clock_calls++;
}

static void setup(void)
{
    memset(&BUS, 0, sizeof BUS);
    BUS.reset_low_ms = -1;
    BUS.reset_high_ms = -1;
    BUS.backlight_on_at_ev = -1;
    /* 기본은 "패널이 우리가 써 넣은 값을 그대로 돌려준다". */
    BUS.reply_madctl = MK_LCD_MADCTL;
    BUS.reply_colmod = MK_LCD_COLMOD;
    mk_cfgtable_init(&CFG);

    MkLcdIo io = { fake_cs, fake_dc, fake_reset, fake_backlight,
                   fake_send, fake_xfer, fake_set_clock, &BUS };
    mk_lcd_init(&LCD, &io, CMDBUF, sizeof CMDBUF, ROWBUF, sizeof ROWBUF);
}

static void set_u32(const char *key, uint32_t v)
{
    MkCfgItem *it = mk_cfg_find(&CFG, key);
    if (it) { it->cur.u = v; }
}

static void set_enabled(int on)
{
    MkCfgItem *it = mk_cfg_find(&CFG, "lcd.enabled");
    if (it) { it->cur.u = on ? 1u : 0u; }
}

/* 전송 하나가 나갈 때마다 완료를 알려 주며 시간을 흘린다.
 *
 * 🔴 한 바퀴에 완료를 최대 하나만 알린다. mk_lcd_tick() 이 걸음 하나만
 *    나아가는지 확인하는 것이 이 방식의 부수 효과다 — 한 바퀴에 여러 걸음을
 *    나가면 아래 "한 걸음" 시험이 잡는다. */
static void run_ms(int64_t ms, int64_t step)
{
    for (int64_t t = 0; t < ms; t += step) {
        BUS.now += step;
        mk_lcd_tick(&LCD, &CFG, BUS.now);
        mk_lcd_on_tx_done(&LCD);
    }
}

/* 명령(dc==0) 바이트만 순서대로 모은다. */
static int commands(uint8_t *out, int cap)
{
    int k = 0;
    for (int e = 0; e < BUS.n && k < cap; e++) {
        if (BUS.dc[e] == 0) { out[k++] = BUS.first[e]; }
    }
    return k;
}

/* cmd 명령이 나간 전송 번호. 없으면 -1. */
static int index_of_command(uint8_t cmd)
{
    for (int e = 0; e < BUS.n; e++) {
        if (BUS.dc[e] == 0 && BUS.first[e] == cmd) { return e; }
    }
    return -1;
}

/* cmd 가 몇 번 나갔나. */
static int count_command(uint8_t cmd)
{
    int k = 0;
    for (int e = 0; e < BUS.n; e++) {
        if (BUS.dc[e] == 0 && BUS.first[e] == cmd) { k++; }
    }
    return k;
}

/* cmd 가 나간 시각들. 반환 = 개수. */
static int times_of_command(uint8_t cmd, int64_t *out, int cap)
{
    int k = 0;
    for (int e = 0; e < BUS.n && k < cap; e++) {
        if (BUS.dc[e] == 0 && BUS.first[e] == cmd) { out[k++] = BUS.at[e]; }
    }
    return k;
}

/* ---- 시험 ---------------------------------------------------------------- */

/* 🔴 18bpp 는 화소당 3바이트이고, 각 바이트의 상위 6비트만 쓴다.
 *
 *    ILI9488.pdf p.122 Figure 107 "SPI Data for 18-bit/pixel (RGB 6-6-6
 *    Bits Input)": D7..D2 = R5..R0, D1·D0 = void. 8비트 색 c 를 6비트로
 *    줄이면 c>>2 이고 그것을 다시 D7 쪽으로 붙이면 (c>>2)<<2 = c & 0xFC 다. */
static void test_pixel_bytes_keep_the_top_six_bits(void)
{
    uint8_t px[3];
    mk_lcd_pixel(0xFFu, 0x80u, 0x00u, px);
    CHECK(px[0] == 0xFCu, "R 0xFF -> 0xFC (하위 2비트는 void)");
    CHECK(px[1] == 0x80u, "G 0x80 -> 0x80");
    CHECK(px[2] == 0x00u, "B 0x00 -> 0x00");

    mk_lcd_pixel(0x03u, 0x07u, 0xFFu, px);
    CHECK(px[0] == 0x00u && px[1] == 0x04u && px[2] == 0xFCu,
          "하위 2비트가 항상 0 이다");
}

/* 🔴 기본이 꺼짐이다. 패널이 안 물린 보드에서 460,800 바이트를 밀면
 *    슈퍼루프가 통째로 늦어진다. */
static void test_disabled_lcd_never_touches_anything(void)
{
    setup();
    run_ms(5000, 1);
    CHECK(BUS.n == 0, "꺼져 있으면 SPI 로 한 바이트도 안 나간다");
    CHECK(BUS.reset_pulses == 0, "꺼져 있으면 RESX 도 안 건드린다");
    CHECK(BUS.backlight_on == 0, "꺼져 있으면 백라이트도 안 켠다");
    CHECK(BUS.cur_cs_low == 0, "꺼져 있으면 CS 를 안 내린다");
}

/* 🔴 하드웨어 리셋이 먼저다. ILI9488.pdf p.308 Table 39 tRW = 리셋 펄스
 *    최소 10 us, Table 40 "Shorter than 5us: Reset Rejected". */
static void test_reset_pulse_comes_first_and_is_long_enough(void)
{
    setup();
    set_enabled(1);
    run_ms(400, 1);
    CHECK(BUS.reset_pulses == 1, "RESX 를 정확히 한 번 내렸다 올린다");
    CHECK(BUS.reset_high_ms - BUS.reset_low_ms >= 1,
          "RESX Low 유지가 tRW(10us) 이상이다");
    CHECK(BUS.n > 0 && BUS.at[0] > BUS.reset_high_ms,
          "첫 명령은 RESX 를 올린 뒤에 나간다");
}

/* 🔴 리셋 취소 대기. ILI9488.pdf p.309 Table 39 주석 7:
 *    "It is necessary to wait 5msec after releasing RESX before sending
 *     commands. The Sleep Out command also cannot be sent in 120msec."
 *
 *    이 대기를 빼도 화면이 켜지는 판이 있다 — 그래서 눈으로는 못 잡는다. */
static void test_no_command_before_the_reset_cancel_time(void)
{
    setup();
    set_enabled(1);
    run_ms(400, 1);
    CHECK(BUS.n > 0, "결국 명령이 나간다");
    CHECK(BUS.at[0] - BUS.reset_high_ms >= 120,
          "RESX 상승 후 120 ms 가 지나야 첫 명령이 나간다");
}

/* 🔴 초기화 명령 순서. 전부 근거가 있는 것만 넣었다 —
 *      3Ah COLMOD  p.200 §5.2.34
 *      36h MADCTL  p.192 §5.2.30
 *      11h SLPOUT  p.166 §5.2.13
 *      2Ah CASET   p.175 §5.2.22
 *      2Bh PASET   p.177 §5.2.23
 *      2Ch RAMWR   p.179 §5.2.24
 *      29h DISPON  p.174 §5.2.21
 *
 *    F7h(Adjust Control 3)·B0h(Interface Mode Control)·감마·전원 명령은
 *    **일부러 없다**. F7h 는 DSI 전용이고(p.276), B0h 의 기본값 00h 이
 *    이 보드의 결선(DIN·SDO 분리)과 이미 맞으며(p.219), 감마·VCOM 은
 *    리셋 때 모듈 EEPROM 에서 실린다(p.308 Table 39 주석 1). */
static void test_init_command_order_is_exactly_what_the_datasheet_needs(void)
{
    setup();
    set_enabled(1);
    run_ms(4000, 1);

    uint8_t got[32];
    int n = commands(got, 32);
    static const uint8_t want[] = { 0x3Au, 0x36u, 0x11u, 0x2Au, 0x2Bu,
                                    0x2Cu, 0x29u };
    CHECK(n == (int)(sizeof want), "명령 개수가 표와 같다");
    int same = (n == (int)(sizeof want));
    for (int k = 0; same && k < n; k++) {
        if (got[k] != want[k]) { same = 0; }
    }
    CHECK(same, "3A 36 11 2A 2B 2C ... 29 순서로 나간다");
}

/* 🔴 MADCTL 의 두 비트는 **실물이 정했다**. [실증 2026-08-19]
 *
 *    ① D3(BGR)=0 으로 주황(255,128,0)을 채웠더니 화면이 파랑으로 나왔다.
 *       패널이 첫 바이트를 B 로 받는다 — 컬러필터가 BGR 이다.
 *    ② D3 만 세우고 글자를 그렸더니 거울처럼 좌우가 뒤집혔다. 열 주소가
 *       반대로 증가한다 — D6(MX)도 세워야 한다. 함께 부분 갱신 자리가
 *       어긋나 겹쳐 그린 자국도 남았다.
 *
 *    데이터시트의 리셋 기본값 00h(p.194)는 칩 기준이고, 이 모듈의 필터
 *    배열과 유리 부착 방향은 거기 없다.
 *
 *    🔴 ①은 한 색으로 채워서 잡았지만 ②는 **글자가 나온 뒤에야** 보였다.
 *       같은 색으로 채우면 방향이 틀려도 화면이 똑같기 때문이다. 두 시험이
 *       아니라 두 그림이 필요했다는 뜻이고, 그래서 이 값은 앞으로도
 *       데이터시트를 근거로 되돌리면 안 된다. */
static void test_madctl_sets_bgr_because_the_panel_is_bgr(void)
{
    setup();
    set_enabled(1);
    run_ms(4000, 1);
    int e = index_of_command(0x36u);
    CHECK(e >= 0 && e + 1 < BUS.n, "MADCTL 뒤에 파라미터가 있다");
    if (e >= 0 && e + 1 < BUS.n) {
        CHECK(BUS.dc[e + 1] == 1 && BUS.len[e + 1] == 1u
              && BUS.first[e + 1] == 0x48u,
              "MADCTL 파라미터가 0x48 (MX=1, BGR=1, 나머지 0)");
    }
}

/* 🔴 18bpp 를 고른다. ILI9488.pdf p.200 §5.2.34: 파라미터는
 *    `X DPI[2:0] X DBI[2:0]` 이고 110 = 18 bits/pixel. 둘 다 110 → 0x66. */
static void test_colmod_selects_eighteen_bits_per_pixel(void)
{
    setup();
    set_enabled(1);
    run_ms(4000, 1);
    int e = index_of_command(0x3Au);
    CHECK(e >= 0 && e + 1 < BUS.n, "COLMOD 뒤에 파라미터가 있다");
    if (e >= 0 && e + 1 < BUS.n) {
        CHECK(BUS.dc[e + 1] == 1 && BUS.len[e + 1] == 1u
              && BUS.first[e + 1] == 0x66u,
              "COLMOD 파라미터가 0x66 (DPI·DBI 둘 다 18bpp)");
    }
}

/* 🔴 주소창이 화면 전체다. p.175 §5.2.22 주석: MADCTL D5=0 이면 열은
 *    013Fh(319)까지, p.177 §5.2.23 은 행이 01DFh(479)까지. */
static void test_address_window_covers_the_whole_panel(void)
{
    setup();
    set_enabled(1);
    run_ms(4000, 1);

    int ca = index_of_command(0x2Au);
    int pa = index_of_command(0x2Bu);
    CHECK(ca >= 0 && pa >= 0, "CASET·PASET 이 나간다");
    if (ca >= 0 && pa >= 0) {
        CHECK(BUS.dc[ca + 1] == 1 && BUS.len[ca + 1] == 4u,
              "CASET 파라미터가 4바이트");
        CHECK(BUS.dc[pa + 1] == 1 && BUS.len[pa + 1] == 4u,
              "PASET 파라미터가 4바이트");
    }
    /* 상수끼리 비교하면 MSVC 가 C4127(조건식이 상수)로 /WX 를 문다 —
     * 변수에 한 번 담는다. */
    unsigned w = MK_LCD_WIDTH;
    unsigned h = MK_LCD_HEIGHT;
    CHECK(w == 320u && h == 480u, "패널이 320 x 480 이다");
}

/* 🔴 슈퍼루프를 막지 않는다. 완료를 알리기 전에는 아무리 tick 을 불러도
 *    다음 걸음이 나가지 않아야 한다 — 상태기계가 안에서 기다리면 이
 *    시험은 아예 돌아오지 않는다(무한루프). */
static void test_tick_never_waits_for_the_transfer(void)
{
    setup();
    set_enabled(1);

    /* 완료를 안 알리고 오래 돌린다. */
    for (int64_t t = 0; t < 4000; t += 1) {
        BUS.now += 1;
        mk_lcd_tick(&LCD, &CFG, BUS.now);
    }
    CHECK(BUS.n == 1, "완료를 안 알리면 전송이 하나에서 멈춘다");

    /* 이제 하나만 알린다. */
    mk_lcd_on_tx_done(&LCD);
    BUS.now += 1;
    mk_lcd_tick(&LCD, &CFG, BUS.now);
    CHECK(BUS.n == 2, "완료를 알린 만큼만 나아간다");
}

/* 🔴 Sleep Out 뒤에는 쉬어야 한다. ILI9488.pdf p.166 §5.2.13:
 *    "It is necessary to wait 5msec before sending the next command;
 *     this is to allow time for supply voltages and clock circuits to
 *     stabilize." */
static void test_sleep_out_is_followed_by_the_datasheet_wait(void)
{
    setup();
    set_enabled(1);
    run_ms(4000, 1);

    int e = index_of_command(0x11u);
    CHECK(e >= 0 && e + 1 < BUS.n, "SLPOUT 뒤에 다음 명령이 있다");
    if (e >= 0 && e + 1 < BUS.n) {
        CHECK(BUS.at[e + 1] - BUS.at[e] >= 5,
              "SLPOUT 과 다음 명령 사이가 5 ms 이상");
    }
}

/* 🔴 화면 한 장 = 320 x 480 x 3 = 460,800 바이트. 한 행(960바이트)씩
 *    480번 보낸다 — 프레임버퍼를 들 필요가 없고, DMA 한 번이 0.5 ms 라
 *    수집이 밀리지 않는다. */
static void test_the_fill_sends_every_row_exactly_once(void)
{
    setup();
    set_enabled(1);
    run_ms(8000, 1);

    int ram = index_of_command(0x2Cu);
    int dis = index_of_command(0x29u);
    CHECK(ram >= 0 && dis > ram, "RAMWR 뒤에 DISPON 이 온다");

    int rows = 0;
    for (int e = ram + 1; e < dis; e++) {
        if (BUS.dc[e] == 1 && BUS.len[e] == MK_LCD_ROW_BYTES) { rows++; }
    }
    unsigned rowb = MK_LCD_ROW_BYTES;
    CHECK(rows == (int)MK_LCD_HEIGHT, "행을 480번 보낸다");
    CHECK(rowb == 960u, "한 행이 960 바이트(320 x 3)");
}

/* 🔴 화소 스트림 내내 CS 를 내린 채로 둔다. p.44 §4.2.1: "The SDA and SCL
 *    are invalid when the CSX is in high level." 중간에 올리면 그 다음
 *    바이트부터 화소 경계가 어긋난다. */
static void test_chip_select_stays_low_through_the_pixel_stream(void)
{
    setup();
    set_enabled(1);
    run_ms(8000, 1);

    int ram = index_of_command(0x2Cu);
    int dis = index_of_command(0x29u);
    CHECK(ram >= 0 && dis > ram, "RAMWR·DISPON 이 다 나갔다");

    int all_low = 1;
    for (int e = ram; e < dis; e++) {
        if (!BUS.cs_low[e]) { all_low = 0; }
    }
    CHECK(all_low, "RAMWR 부터 마지막 행까지 CS 가 계속 Low");
}

/* 🔴 백라이트는 맨 마지막이다. GRAM 은 전원 인가 직후 임의값이라, 먼저
 *    켜면 사용자가 보는 첫 화면이 잡동사니다. */
static void test_backlight_comes_on_only_after_the_screen_is_painted(void)
{
    setup();
    set_enabled(1);
    run_ms(8000, 1);

    int dis = index_of_command(0x29u);
    CHECK(BUS.backlight_on == 1, "다 그린 뒤에는 백라이트가 켜져 있다");
    CHECK(dis >= 0 && BUS.backlight_on_at_ev > dis,
          "백라이트는 DISPON 뒤에 켠다");
}

/* 🔴 도중에 꺼도 멈춘다 — 그리고 백라이트를 내린다. */
static void test_turning_it_off_stops_and_darkens(void)
{
    setup();
    set_enabled(1);
    run_ms(8000, 1);
    int painted = BUS.n;
    CHECK(BUS.backlight_on == 1, "먼저 켜져 있다");

    set_enabled(0);
    run_ms(2000, 1);
    CHECK(BUS.n == painted, "끈 뒤에는 SPI 로 아무것도 안 나간다");
    CHECK(BUS.backlight_on == 0, "끈 뒤에는 백라이트가 꺼진다");
    CHECK(BUS.cur_cs_low == 0, "끈 뒤에는 CS 가 비선택(High)");
}

/* 🔴 다 그린 뒤에는 조용하다. 한 번 칠하고 마는 단계라, 계속 다시 그리면
 *    SPI 와 DMA 를 이유 없이 물고 있게 된다. */
static void test_it_paints_once_and_then_goes_quiet(void)
{
    setup();
    set_enabled(1);
    run_ms(8000, 1);
    int painted = BUS.n;
    run_ms(8000, 1);
    CHECK(BUS.n == painted, "다 그린 뒤에는 더 안 보낸다");
    CHECK(mk_lcd_ready(&LCD), "상태가 READY 다");
}

/* 🔴 설정표를 **진짜 카탈로그**로 시험한다. 가짜 키로 시험하면 카탈로그
 *    에서 이름이 바뀌어도 통과한다 (test_sol.c 와 같은 관례). */
static void test_the_catalog_has_the_lcd_enabled_item(void)
{
    MkConfig cfg;
    mk_cfgtable_init(&cfg);
    MkCfgItem *it = mk_cfg_find(&cfg, "lcd.enabled");
    CHECK(it != NULL, "카탈로그에 lcd.enabled 가 있다");
    if (it != NULL) {
        CHECK(it->vtype == MK_VT_BOOL, "bool 이다");
        CHECK(it->def.u == 0u, "기본값이 꺼짐이다");
        CHECK(it->label != NULL && it->note != NULL, "라벨·안내문이 있다");
    }
}

/* 🔴 버퍼가 모자라면 아예 시작하지 않는다. 행 버퍼가 짧은데 그리기
 *    시작하면 화면이 어긋난 채로 채워져, 원인이 배선처럼 보인다. */
static void test_a_short_row_buffer_stops_it_before_it_starts(void)
{
    memset(&BUS, 0, sizeof BUS);
    mk_cfgtable_init(&CFG);
    MkLcdIo io = { fake_cs, fake_dc, fake_reset, fake_backlight,
                   fake_send, fake_xfer, fake_set_clock, &BUS };
    static uint8_t small[16];
    mk_lcd_init(&LCD, &io, CMDBUF, sizeof CMDBUF, small, sizeof small);
    set_enabled(1);
    run_ms(4000, 1);
    CHECK(BUS.n == 0, "행 버퍼가 짧으면 아무것도 안 보낸다");
}

/* ══ 회복 ══════════════════════════════════════════════════════════════════
 *
 * 🔴 왜 이것이 필요한가 [실기기 2026-08-19, 사용자]: "LCD는 가끔 리셋을
 *    해줘야겠다. 노이즈 타면 픽셀이 다 깨지는데?" — 깨진 뒤 **저절로 안
 *    돌아온다**는 것이 요지다. 부분 갱신은 값이 바뀐 칸만 다시 그리므로,
 *    한 번 어긋난 그림은 그 칸의 값이 바뀔 때까지 그대로 남는다. 즉
 *    부분 갱신의 효율이 곧 회복 불능의 원인이다.
 *
 * 🔴 그래서 **패널에게 되물어본다.** ILI9488 은 레지스터를 되읽을 수 있고
 *    (0Bh RDDMADCTL p.157 §5.2.7 · 0Ch RDDCOLMOD p.159 §5.2.8),
 *    MISO(PB14)가 실제로 물려 있다(J25.9, 넷리스트 확인 2026-08-19).
 *
 *    되읽은 값이 다르면 **명령이 깨진 것**이고 초기화부터 다시 한다.
 *    같은데 화면이 이상하면 **GRAM 동기가 어긋난 것**이라 전면 다시
 *    그리기로 족하다 — 그 둘을 가르는 것이 이 되읽기의 존재 이유다.
 */

/* 🔴 되읽기는 데이터시트가 시키는 모양이어야 한다.
 *
 *    ILI9488.pdf p.122 Figure 108 "4-Line SPI Mode Read Data": 명령
 *    바이트 뒤에 **8 Dummy Clock** 이 붙고 그 다음에 실제 데이터가 나온다.
 *    명령표도 같은 말을 한다 — p.155 §5.2.6(0Ah)·p.157 §5.2.7(0Bh)·
 *    p.159 §5.2.8(0Ch) 모두 "1st Parameter" 가 더미이고 "2nd Parameter"
 *    가 값이다.
 *
 *    더미를 안 건너뛰면 되읽은 값이 늘 틀리고, 그러면 이 회복 장치가
 *    **멀쩡한 패널을 1초마다 다시 켜는** 장치가 된다. 지어낸 회복이
 *    고장보다 나쁘다. */
static void test_readback_asks_the_panel_the_way_the_datasheet_says(void)
{
    setup();
    set_enabled(1);
    set_u32("lcd.verify_ms", 1000);
    set_u32("lcd.redraw_ms", 0);
    run_ms(4000, 1);

    CHECK(BUS.xf_n >= 4, "되읽기가 네 걸음(명령·데이터 × 2) 이상 나갔다");
    if (BUS.xf_n >= 4) {
        CHECK(BUS.xf_dc[0] == 0 && BUS.xf_len[0] == 1u
              && BUS.xf_first[0] == 0x0Bu,
              "먼저 0Bh(RDDMADCTL)를 명령으로 보낸다");
        CHECK(BUS.xf_dc[1] == 1 && BUS.xf_len[1] == 2u,
              "그 다음 데이터 2바이트를 받는다 — 더미 1 + 값 1");
        CHECK(BUS.xf_dc[2] == 0 && BUS.xf_len[2] == 1u
              && BUS.xf_first[2] == 0x0Cu,
              "이어서 0Ch(RDDCOLMOD)");
        CHECK(BUS.xf_dc[3] == 1 && BUS.xf_len[3] == 2u,
              "역시 더미 1 + 값 1");
        int all_low = BUS.xf_cs_low[0] && BUS.xf_cs_low[1]
                      && BUS.xf_cs_low[2] && BUS.xf_cs_low[3];
        CHECK(all_low, "되읽기 내내 CS 가 Low (p.44 §4.2.1)");
    }
}

/* 🔴 값이 맞으면 아무것도 하지 않는다. 되읽기가 맞는데도 다시 켜면
 *    그것은 진단이 아니라 주기적 재부팅이다. */
static void test_a_matching_readback_does_not_reinitialize(void)
{
    setup();
    set_enabled(1);
    set_u32("lcd.verify_ms", 500);
    set_u32("lcd.redraw_ms", 0);
    run_ms(6000, 1);

    MkLcdStat st;
    mk_lcd_stat(&LCD, &st);
    CHECK(BUS.reset_pulses == 1, "되읽기가 맞으면 RESX 를 다시 안 내린다");
    CHECK(st.verify_ok >= 5u, "대조 성공이 주기만큼 쌓인다");
    CHECK(st.verify_fail == 0u, "실패는 없다");
    CHECK(st.reinit == 0u, "재초기화 0");
    CHECK(st.readback == 1, "되읽기를 믿을 수 있다");
}

/* 🔴 값이 다르면 초기화부터 다시 한다. MADCTL 이 바뀌면 방향과 색이
 *    통째로 틀어지고(실기기 2026-08-19 로 확인한 바로 그 증상), COLMOD 가
 *    바뀌면 화소 폭이 어긋나 화면 전체가 사선으로 밀린다. 전면 다시
 *    그리기로는 못 고친다 — 레지스터를 되세워야 한다. */
static void test_a_mismatching_readback_reinitializes_the_panel(void)
{
    setup();
    set_enabled(1);
    set_u32("lcd.verify_ms", 500);
    set_u32("lcd.redraw_ms", 0);

    /* 먼저 한 번은 맞아야 한다 — 되읽기 경로를 믿을 수 있다는 근거가
     * 거기서 나온다(아래 시험 참고). */
    run_ms(3000, 1);
    uint32_t epoch0 = mk_lcd_epoch(&LCD);

    BUS.reply_madctl = 0x00u;          /* 패널이 값을 잃었다 */
    run_ms(3000, 1);

    MkLcdStat st;
    mk_lcd_stat(&LCD, &st);
    CHECK(BUS.reset_pulses >= 2, "RESX 를 다시 내린다 — 하드웨어 리셋부터다");
    CHECK(st.reinit >= 1u, "재초기화 횟수가 오른다");
    CHECK(st.verify_fail >= 1u, "대조 실패가 세어진다");
    CHECK(mk_lcd_epoch(&LCD) > epoch0,
          "epoch 이 올라 화면 쪽이 전부 다시 그린다");
    CHECK(index_of_command(0x36u) >= 0 && count_command(0x36u) >= 2,
          "MADCTL 을 다시 써넣는다");
    CHECK(count_command(0x29u) >= 2, "DISPON 도 다시 보낸다");
}

/* 🔴 **첫 대조가 되읽기 경로를 판정한다.**
 *
 *    초기화 직후의 MADCTL·COLMOD 는 방금 우리가 써 넣은 값이다. 그것이
 *    안 맞으면 패널이 값을 잃은 것이 아니라 **되읽기를 못 믿는 것**이다
 *    — 흔한 3.5" 모듈은 SDO 가 안 물려 있거나 저항 하나를 건너 나온다.
 *
 *    그때 재초기화로 대응하면 멀쩡한 화면을 몇 초마다 다시 켜게 되고,
 *    사용자가 보는 증상은 원래 고장보다 나빠진다. 그래서 첫 대조가
 *    틀리면 **검사만 끈다** — 그리고 그 사실을 $STAT 으로 알린다. */
static void test_a_panel_that_cannot_be_read_only_disables_the_check(void)
{
    setup();
    set_enabled(1);
    set_u32("lcd.verify_ms", 500);
    set_u32("lcd.redraw_ms", 0);
    BUS.reply_madctl = 0xFFu;          /* MISO 가 안 물린 판 */
    BUS.reply_colmod = 0xFFu;
    run_ms(8000, 1);

    MkLcdStat st;
    mk_lcd_stat(&LCD, &st);
    CHECK(BUS.reset_pulses == 1, "재초기화하지 않는다");
    CHECK(st.reinit == 0u, "재초기화 횟수가 0 이다");
    CHECK(st.readback == 0, "되읽기를 못 믿는다고 표시한다");
    CHECK(st.verify_fail == 1u,
          "첫 실패 한 번만 세고 그 뒤로는 되묻지 않는다");
    CHECK(BUS.xf_n == 4, "되읽기 자체를 멈춘다 — 버스를 이유 없이 안 쓴다");
}

/* 🔴 되읽기 중에도 한 바퀴에 한 걸음이다. 되읽기는 6바이트뿐이지만,
 *    완료를 기다리는 코드를 여기 하나 넣으면 그것이 곧 슈퍼루프가 서는
 *    자리가 된다 — 그리기 쪽에서 그렇게 안 짠 이유와 같다. */
static void test_the_readback_never_waits_either(void)
{
    setup();
    set_enabled(1);
    set_u32("lcd.verify_ms", 500);
    set_u32("lcd.redraw_ms", 0);
    run_ms(2000, 1);                   /* 첫 그림까지 마친다 */
    int before = BUS.xf_n;

    /* 이제 완료를 안 알리고 오래 돈다. */
    for (int64_t t = 0; t < 2000; t++) {
        BUS.now += 1;
        mk_lcd_tick(&LCD, &CFG, BUS.now);
    }
    CHECK(BUS.xf_n == before + 1,
          "완료를 안 알리면 되읽기도 한 걸음에서 멈춘다");
}

/* 🔴 **마지막 그물.** 되읽기가 맞는데 화면이 이상한 경우 — GRAM 쓰기
 *    포인터가 밀린 경우 — 는 레지스터로 알 방법이 없다. 그래서 값이 안
 *    바뀌어도 주기적으로 전면을 다시 그린다.
 *
 *    주기는 설정 항목이다. 사용자가 증상 빈도를 보고 조절해야 한다. */
static void test_the_periodic_full_redraw_keeps_its_period(void)
{
    setup();
    set_enabled(1);
    set_u32("lcd.verify_ms", 0);       /* 되읽기는 끄고 주기만 본다 */
    set_u32("lcd.redraw_ms", 2000);
    run_ms(9000, 1);

    int64_t at[16];
    int n = times_of_command(0x2Cu, at, 16);   /* RAMWR = 전면 한 장 */
    CHECK(n >= 4, "첫 그림 + 주기 갱신 3회 이상");
    int spaced = 1;
    for (int k = 2; k < n; k++) {
        int64_t gap = at[k] - at[k - 1];
        if (gap < 2000 || gap > 2100) { spaced = 0; }
    }
    CHECK(spaced, "주기 갱신 간격이 설정한 2000 ms 다");
    CHECK(BUS.reset_pulses == 1, "주기 갱신은 재초기화가 아니다");

    MkLcdStat st;
    mk_lcd_stat(&LCD, &st);
    CHECK(st.redraw == (uint32_t)(n - 1), "전면 갱신 횟수가 $STAT 과 맞는다");
    CHECK(st.reinit == 0u, "재초기화는 0 이다");
}

/* 🔴 주기 갱신이 **바탕까지** 다시 칠하는지. 칸만 다시 그리면 칸 바깥에
 *    밀려 찍힌 화소가 그대로 남는다 — 그것이 사용자가 본 "픽셀이 다
 *    깨진다" 의 모습이다. */
static void test_the_periodic_redraw_repaints_the_whole_panel(void)
{
    setup();
    set_enabled(1);
    set_u32("lcd.verify_ms", 0);
    set_u32("lcd.redraw_ms", 2000);
    /* 🔴 4000 ms 다. 주기 갱신은 2630 ms 쯤 시작해 480 바퀴를 쓰므로,
     *    3000 ms 로 끊으면 갱신이 끝나기 전에 세게 된다. */
    run_ms(4000, 1);

    /* 두 번째 RAMWR 뒤로 480행이 다시 나가는지 센다. */
    int seen = 0, ram2 = -1;
    for (int e = 0; e < BUS.n; e++) {
        if (BUS.dc[e] == 0 && BUS.first[e] == 0x2Cu) {
            seen++;
            if (seen == 2) { ram2 = e; break; }
        }
    }
    CHECK(ram2 > 0, "두 번째 RAMWR 이 있다");
    int rows = 0;
    for (int e = ram2 + 1; e < BUS.n; e++) {
        if (BUS.dc[e] == 1 && BUS.len[e] == MK_LCD_ROW_BYTES) { rows++; }
    }
    CHECK(rows == (int)MK_LCD_HEIGHT, "바탕을 480행 전부 다시 칠한다");
}

/* 🔴 전면 갱신도 **한 바퀴에 한 행**이다. 8 MHz 에서 460,800 바이트는
 *    약 461 ms 인데, 한 번에 밀면 그동안 ADS1256 표본과 텔레메트리가
 *    통째로 밀린다. 사용자가 못박은 선이 그것이다 (2026-08-19): "센서 값을
 *    늦게 보내도 돼. 무조건 수집을 정상적으로 타임스탬프 찍어서 가지고
 *    있어야해." */
static void test_the_full_redraw_still_moves_one_row_per_tick(void)
{
    setup();
    set_enabled(1);
    set_u32("lcd.verify_ms", 0);
    set_u32("lcd.redraw_ms", 2000);
    run_ms(2500, 1);                   /* 첫 그림 + 첫 주기 갱신 진입 */

    int worst = 0;
    for (int64_t t = 0; t < 600; t++) {
        int before = BUS.n;
        BUS.now += 1;
        mk_lcd_tick(&LCD, &CFG, BUS.now);
        mk_lcd_on_tx_done(&LCD);
        int d = BUS.n - before;
        if (d > worst) { worst = d; }
    }
    CHECK(worst <= 1, "전면 갱신 중에도 한 바퀴에 전송 하나뿐이다");
}

/* 🔴 SPI 클럭이 설정 항목이다.
 *
 *    [실기기 2026-08-19] 사용자가 "무작위로 깨진다" 고 했다 — 24V 스위칭과
 *    무관하고 케이블을 만질 때도 아니다. 남은 유력 후보는 **핀 헤더 + 점퍼
 *    선에 16 MHz 가 빠른 것**이다. 낮춰서 증상이 사라지면 그것 자체가
 *    신호 무결성 문제라는 진단이 된다. 그래서 기본을 8 MHz 로 둔다
 *    (사용자 결정 2026-08-19: "8mhz로 낮춰서 해보자"). */
static void test_the_spi_clock_comes_from_the_catalog(void)
{
    setup();
    set_enabled(1);
    run_ms(500, 1);
    CHECK(BUS.clock_calls >= 1, "클럭을 한 번은 세운다");
    CHECK(BUS.clock_khz == 8000u, "기본이 8 MHz 다");

    int calls = BUS.clock_calls;
    run_ms(500, 1);
    CHECK(BUS.clock_calls == calls, "안 바뀌면 다시 세우지 않는다");

    set_u32("lcd.spi_khz", 16000u);
    run_ms(500, 1);
    CHECK(BUS.clock_khz == 16000u, "설정을 바꾸면 따라간다");
}

/* 🔴 회복 항목이 카탈로그에 있어야 한다. 없으면 사용자가 증상 빈도를 보고
 *    조절할 수 없고, 그러면 이 장치는 우리가 정한 숫자 하나로 고정된다. */
static void test_the_catalog_has_the_recovery_items(void)
{
    MkConfig cfg;
    mk_cfgtable_init(&cfg);

    MkCfgItem *hz = mk_cfg_find(&cfg, "lcd.spi_khz");
    CHECK(hz != NULL, "카탈로그에 lcd.spi_khz 가 있다");
    if (hz != NULL) {
        CHECK(hz->vtype == MK_VT_ENUM, "enum 이다 — 분주비로 낼 수 있는 값만");
        CHECK(hz->def.u == 8000u, "기본이 8 MHz");
        CHECK(hz->n_choices == 4u, "2·4·8·16 MHz 넷");
    }

    MkCfgItem *vf = mk_cfg_find(&cfg, "lcd.verify_ms");
    CHECK(vf != NULL, "카탈로그에 lcd.verify_ms 가 있다");
    if (vf != NULL) {
        CHECK(vf->vtype == MK_VT_U16, "u16 이다");
        CHECK(vf->min == 0.0f, "0 = 끔이 가능하다");
    }

    MkCfgItem *rd = mk_cfg_find(&cfg, "lcd.redraw_ms");
    CHECK(rd != NULL, "카탈로그에 lcd.redraw_ms 가 있다");
    if (rd != NULL) {
        CHECK(rd->vtype == MK_VT_U32, "u32 다 — 분 단위까지 잡을 수 있다");
        CHECK(rd->min == 0.0f, "0 = 끔이 가능하다");
    }
}

/* 🔴 되읽기·주기 갱신을 껐으면 정말 아무 일도 없어야 한다. 회복 장치가
 *    "끌 수 없는 것" 이 되면 그것 자체가 새로운 방해 요인이다. */
static void test_recovery_can_be_turned_off_completely(void)
{
    setup();
    set_enabled(1);
    set_u32("lcd.verify_ms", 0);
    set_u32("lcd.redraw_ms", 0);
    run_ms(4000, 1);
    int painted = BUS.n;
    run_ms(60000, 1);
    CHECK(BUS.n == painted, "둘 다 끄면 다 그린 뒤 한 바이트도 안 나간다");
    CHECK(BUS.xf_n == 0, "되읽기도 아예 안 한다");
}

int main(void)
{
    printf("mk_lcd\n");
    test_pixel_bytes_keep_the_top_six_bits();
    test_disabled_lcd_never_touches_anything();
    test_reset_pulse_comes_first_and_is_long_enough();
    test_no_command_before_the_reset_cancel_time();
    test_init_command_order_is_exactly_what_the_datasheet_needs();
    test_madctl_sets_bgr_because_the_panel_is_bgr();
    test_colmod_selects_eighteen_bits_per_pixel();
    test_address_window_covers_the_whole_panel();
    test_tick_never_waits_for_the_transfer();
    test_sleep_out_is_followed_by_the_datasheet_wait();
    test_the_fill_sends_every_row_exactly_once();
    test_chip_select_stays_low_through_the_pixel_stream();
    test_backlight_comes_on_only_after_the_screen_is_painted();
    test_turning_it_off_stops_and_darkens();
    test_it_paints_once_and_then_goes_quiet();
    test_the_catalog_has_the_lcd_enabled_item();
    test_a_short_row_buffer_stops_it_before_it_starts();
    test_readback_asks_the_panel_the_way_the_datasheet_says();
    test_a_matching_readback_does_not_reinitialize();
    test_a_mismatching_readback_reinitializes_the_panel();
    test_a_panel_that_cannot_be_read_only_disables_the_check();
    test_the_readback_never_waits_either();
    test_the_periodic_full_redraw_keeps_its_period();
    test_the_periodic_redraw_repaints_the_whole_panel();
    test_the_full_redraw_still_moves_one_row_per_tick();
    test_the_spi_clock_comes_from_the_catalog();
    test_the_catalog_has_the_recovery_items();
    test_recovery_can_be_turned_off_completely();
    printf(failures ? "FAILED %d\n" : "all passed\n", failures);
    return failures ? 1 : 0;
}
