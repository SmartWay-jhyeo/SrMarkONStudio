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

#define MAX_EV 1024

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

static void setup(void)
{
    memset(&BUS, 0, sizeof BUS);
    BUS.reset_low_ms = -1;
    BUS.reset_high_ms = -1;
    BUS.backlight_on_at_ev = -1;
    mk_cfgtable_init(&CFG);

    MkLcdIo io = { fake_cs, fake_dc, fake_reset, fake_backlight,
                   fake_send, &BUS };
    mk_lcd_init(&LCD, &io, CMDBUF, sizeof CMDBUF, ROWBUF, sizeof ROWBUF);
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

/* 🔴 MADCTL 의 BGR 비트는 **실물이 정했다**. [실증 2026-08-19]
 *
 *    D3=0 으로 굽고 주황(255,128,0)을 채웠더니 화면이 파랑으로 나왔다.
 *    패널이 첫 바이트를 B 로 받는다 — 컬러필터가 BGR 이다. 데이터시트의
 *    리셋 기본값 00h(p.194)는 칩 기준이고 모듈의 필터 배열은 거기 없다.
 *
 *    이 시험이 있는 이유: 누군가 "데이터시트 기본값이 00h 인데 왜 08h 지"
 *    하고 되돌리면 화면 색이 통째로 뒤집힌다. 그런데 그 사실은 **실물을
 *    봐야만** 안다 — 시험이 없으면 아무것도 안 막는다. */
static void test_madctl_sets_bgr_because_the_panel_is_bgr(void)
{
    setup();
    set_enabled(1);
    run_ms(4000, 1);
    int e = index_of_command(0x36u);
    CHECK(e >= 0 && e + 1 < BUS.n, "MADCTL 뒤에 파라미터가 있다");
    if (e >= 0 && e + 1 < BUS.n) {
        CHECK(BUS.dc[e + 1] == 1 && BUS.len[e + 1] == 1u
              && BUS.first[e + 1] == 0x08u,
              "MADCTL 파라미터가 0x08 (BGR=1, 나머지 0)");
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
                   fake_send, &BUS };
    static uint8_t small[16];
    mk_lcd_init(&LCD, &io, CMDBUF, sizeof CMDBUF, small, sizeof small);
    set_enabled(1);
    run_ms(4000, 1);
    CHECK(BUS.n == 0, "행 버퍼가 짧으면 아무것도 안 보낸다");
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
    printf(failures ? "FAILED %d\n" : "all passed\n", failures);
    return failures ? 1 : 0;
}
