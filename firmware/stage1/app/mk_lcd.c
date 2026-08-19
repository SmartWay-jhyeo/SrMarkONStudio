#include "mk_lcd.h"

#include <string.h>

/* ── 대기시간 (ILI9488.pdf, Version 100) ──────────────────────────────────
 *
 * 🔴 전부 원문에서 왔다. 인터넷의 초기화 배열을 베끼지 않았다.
 *
 *   p.308 Table 39 "Reset Timing": tRW(리셋 펄스) MIN 10 us.
 *        Table 40 "Reset Description": 5us 보다 짧으면 "Reset Rejected",
 *        9us 보다 길어야 "Reset". 우리 시간 단위가 ms 라 1 ms 를 준다 —
 *        최소치의 100배지만, 부팅에 한 번 쓰는 시간이라 아깝지 않다.
 *
 *   p.309 Table 39 주석 7: "It is necessary to wait 5msec after releasing
 *        RESX before sending commands. The Sleep Out command also cannot
 *        be sent in 120msec." → 둘 중 큰 값 하나로 묶어 120 ms 를 기다린
 *        뒤에 첫 명령을 낸다. 5 ms 뒤에 COLMOD 를 먼저 보내고 120 ms 에
 *        SLPOUT 을 보내는 방법도 되지만, 두 시계를 따로 세는 값이 없다.
 */
#define MK_LCD_RESET_LOW_MS      1u
#define MK_LCD_RESET_CANCEL_MS   120u

/* 🔴 SLPOUT 뒤의 대기. p.166 §5.2.13 "Restriction": "It is necessary to
 *    wait 5msec before sending the next command; this is to allow time for
 *    supply voltages and clock circuits to stabilize."
 *
 *    세간의 초기화 배열이 여기에 120 ms 를 두는 것은 같은 절의 **다른**
 *    문장(Sleep In 을 보내기까지 120 ms)을 옮겨 온 것으로 보인다. 원문이
 *    요구하는 것은 5 ms 다. */
#define MK_LCD_SLPOUT_DELAY_MS   5u

/* ── 채울 색 ──────────────────────────────────────────────────────────────
 *
 * 🔴 세 성분을 일부러 다 다르게 둔다. 주황(255,128,0)이 파랑 계열로 나오면
 *    이 패널의 컬러필터가 BGR 이라는 뜻이고, 그때는 MADCTL(36h)의 D3(BGR)을
 *    세우면 된다 (p.192 §5.2.30). 회색이나 흰색으로 채우면 이것을 못 가린다
 *    — WS2812 색 순서(led.grb)에서 겪은 것과 같은 종류의 문제다. */
#define MK_LCD_FILL_R  0xFFu
#define MK_LCD_FILL_G  0x80u
#define MK_LCD_FILL_B  0x00u

/* ── 초기화 표 ────────────────────────────────────────────────────────────
 *
 * 🔴 근거를 못 찾은 명령은 넣지 않았다. 특히 아래 셋은 **일부러 없다**:
 *
 *   F7h (Adjust Control 3) — p.276 §5.3.39. 파라미터의 뜻이 DSI_18_option
 *       하나뿐이고 설명이 "DSI write DCS command" 다. MIPI DSI 전용이라
 *       SPI 로는 아무 뜻이 없다. 세간의 초기화 배열이 거의 다 들고 있다.
 *
 *   B0h (Interface Mode Control) — p.219 §5.3.1. 기본값 00h 이면
 *       SDA_EN = 0 = "DIN and SDO pins are used for 3/4 wire serial
 *       interface" 이고, 이 보드가 정확히 그 결선이다(MOSI=PB15, MISO=PB14
 *       가 따로 나온다). 나머지 비트는 RGB 인터페이스용이라 안 쓴다.
 *
 *   감마·전원(E0h/E1h/C0h/C1h/C5h 등) — p.308 Table 39 주석 1: 리셋 취소
 *       구간에 "loading ID bytes, VCOM setting and other settings from the
 *       EEPROM to registers" 가 일어난다. 그 값은 **모듈 제조사가 넣은
 *       것**이고 우리가 아는 것보다 이 패널에 맞다. 덮어쓸 근거가 없다.
 */
static const MkLcdCmd INIT_CMDS[] = {
    /* 3Ah COLMOD — p.200 §5.2.34. 파라미터는 `X DPI[2:0] X DBI[2:0]` 이고
     * 110 = 18 bits/pixel. RGB(DPI)·MCU(DBI) 둘 다 110 → 0x66.
     * 🔴 이것을 SLPOUT 앞에 두는 이유: Availability 표가 "Sleep In: Yes"
     *    라 지금 보낼 수 있고, 잠에서 깬 뒤에 화소 형식이 바뀌는 순간이
     *    없어야 첫 프레임이 깨지지 않는다. */
    { 0x3Au, 1u, { 0x66u, 0u, 0u, 0u }, 0u },

    /* 36h MADCTL — p.192 §5.2.30. MY·MX·MV·ML·MH 는 0, **BGR(D3)만 1**.
     *
     * 🔴 [실증 2026-08-19] D3=0 으로 굽고 주황(255,128,0)을 채웠더니 화면이
     *    **파랑**으로 나왔다. 패널이 첫 바이트를 B 로 받는다는 뜻이다 —
     *    컬러필터가 BGR 이다. 데이터시트의 리셋 기본값(00h, p.194)은 칩
     *    기준이고 이 모듈의 필터 배열은 거기 안 적혀 있다. 그래서 이 값은
     *    데이터시트가 아니라 **실물이 정한 것**이다.
     *
     *    이 한 비트를 세우면 mk_lcd_pixel_bytes() 가 만든 R·G·B 순서가
     *    패널의 필터 순서와 맞는다 — 화소 변환 쪽을 뒤집지 않는 이유는,
     *    뒤집는 곳이 둘이 되면 나중에 한쪽만 고쳐 놓고 왜 색이 도로
     *    틀렸는지 못 찾게 되기 때문이다(mk_solctl 의 flip_polarity 와 같은
     *    이유로 반전은 한 곳에서만 한다). */
    { 0x36u, 1u, { 0x08u, 0u, 0u, 0u }, 0u },

    /* 11h SLPOUT — p.166 §5.2.13. DC/DC 컨버터·내부 발진기·패널 주사를
     * 켠다. 뒤에 5 ms 를 쉰다(위 상수 주석). */
    { 0x11u, 0u, { 0u, 0u, 0u, 0u }, MK_LCD_SLPOUT_DELAY_MS },

    /* 2Ah CASET — p.175 §5.2.22. SC=0, EC=319(0x013F).
     * 주석: MADCTL D5(MV)=0 이면 013Fh 를 넘는 값은 무시된다. */
    { 0x2Au, 4u, { 0x00u, 0x00u, 0x01u, 0x3Fu }, 0u },

    /* 2Bh PASET — p.177 §5.2.23. SP=0, EP=479(0x01DF). */
    { 0x2Bu, 4u, { 0x00u, 0x00u, 0x01u, 0xDFu }, 0u },

    /* 2Ch RAMWR — p.179 §5.2.24. 이 뒤로 화소가 이어진다.
     * 🔴 표의 **마지막** 줄이어야 한다. 여기서 CS 를 올리지 않고 그대로
     *    FILL 로 넘어간다 (p.44 §4.2.1: CSX 가 High 이면 SDA·SCL 이 무효). */
    { 0x2Cu, 0u, { 0u, 0u, 0u, 0u }, 0u },
};

/* 다 그린 뒤. 🔴 DISPON 을 앞에 두지 않는 이유: GRAM 은 전원 인가 직후
 * 임의값이라, 먼저 켜면 사용자가 보는 첫 화면이 잡동사니다.
 * 29h DISPON — p.174 §5.2.21. */
static const MkLcdCmd POST_CMDS[] = {
    { 0x29u, 0u, { 0u, 0u, 0u, 0u }, 0u },
};

#define N_INIT   (sizeof INIT_CMDS / sizeof INIT_CMDS[0])
#define N_POST   (sizeof POST_CMDS / sizeof POST_CMDS[0])

/* ---- 도우미 -------------------------------------------------------------- */

void mk_lcd_pixel(uint8_t r, uint8_t g, uint8_t b, uint8_t out[3])
{
    /* p.122 Figure 107 — 바이트마다 D7..D2 가 색이고 D1·D0 은 void. */
    out[0] = (uint8_t)(r & 0xFCu);
    out[1] = (uint8_t)(g & 0xFCu);
    out[2] = (uint8_t)(b & 0xFCu);
}

static void set_cs(MkLcd *l, int low)
{
    if (l->cs_low == low) {
        return;
    }
    l->cs_low = low;
    if (l->io.cs != NULL) {
        l->io.cs(l->io.ctx, low);
    }
}

static void send(MkLcd *l, const uint8_t *buf, size_t n)
{
    l->busy = 1;
    if (l->io.send == NULL || !l->io.send(l->io.ctx, buf, n)) {
        /* 🔴 시작조차 못 했으면 완료 통보가 오지 않는다. 여기서 풀지
         *    않으면 상태기계가 영영 선다 — 그리고 그것은 "화면만 안 나온다"
         *    가 아니라 다음 바퀴마다 조용히 아무 일도 안 하는 상태다. */
        l->busy = 0;
    }
}

static void fill_row(MkLcd *l)
{
    for (unsigned x = 0; x < MK_LCD_WIDTH; x++) {
        mk_lcd_pixel(MK_LCD_FILL_R, MK_LCD_FILL_G, MK_LCD_FILL_B,
                     &l->row_buf[x * MK_LCD_BYTES_PER_PIXEL]);
    }
    l->row_ready = 1;
}

/* 표 한 줄을 한 걸음씩 나아간다. 그 줄이 끝났으면 1. */
static int step_cmd(MkLcd *l, const MkLcdCmd *e, int64_t now)
{
    switch (l->sub) {
    case 0u:
        set_cs(l, 1);
        if (l->io.dc != NULL) { l->io.dc(l->io.ctx, 0); }   /* 명령 */
        l->cmd_buf[0] = e->cmd;
        send(l, l->cmd_buf, 1u);
        l->sub = 1u;
        return 0;

    case 1u:
        if (e->n_args > 0u) {
            if (l->io.dc != NULL) { l->io.dc(l->io.ctx, 1); }  /* 데이터 */
            memcpy(l->cmd_buf, e->args, (size_t)e->n_args);
            send(l, l->cmd_buf, (size_t)e->n_args);
        }
        l->sub = 2u;
        return 0;

    case 2u:
        /* 🔴 대기의 기준 시각을 **여기서** 찍는다. 전송이 끝난 것을 확인한
         *    바퀴이기 때문이다 — 전송을 시작한 시각에서 재면 명령이 아직
         *    선로에 있는 동안 대기가 흐른다. */
        l->mark_ms = now;
        l->sub = 3u;
        return 0;

    default:
        if (e->delay_ms > 0u && now - l->mark_ms < (int64_t)e->delay_ms) {
            return 0;
        }
        l->sub = 0u;
        return 1;
    }
}

/* ---- 바깥 문 ------------------------------------------------------------- */

void mk_lcd_init(MkLcd *l, const MkLcdIo *io,
                 uint8_t *cmd_buf, size_t cmd_cap,
                 uint8_t *row_buf, size_t row_cap)
{
    memset(l, 0, sizeof *l);
    if (io != NULL) { l->io = *io; }
    l->cmd_buf = cmd_buf;
    l->cmd_cap = cmd_cap;
    l->row_buf = row_buf;
    l->row_cap = row_cap;

    if (cmd_buf == NULL || cmd_cap < MK_LCD_CMD_BUF_BYTES
        || row_buf == NULL || row_cap < MK_LCD_ROW_BYTES) {
        /* 🔴 조용히 반쪽으로 굴리지 않는다. 짧은 행 버퍼로 그리면 화면이
         *    어긋난 채 채워져 원인이 배선처럼 보인다. */
        l->phase = MK_LCD_UNUSABLE;
    }
}

void mk_lcd_on_tx_done(MkLcd *l)
{
    l->busy = 0;
}

int mk_lcd_ready(const MkLcd *l)
{
    return l->phase == MK_LCD_DONE;
}

void mk_lcd_tick(MkLcd *l, MkConfig *cfg, int64_t now_ms)
{
    if (cfg == NULL || l->phase == MK_LCD_UNUSABLE) {
        return;
    }

    /* 🔴 항목을 못 찾으면 **끈 것으로** 본다 — main.c 의 sync_rails() 와
     *    같은 규칙이다. 설정표에서 항목이 사라지는 것은 실수이고, 실수했을
     *    때 230 ms 짜리 전송이 저절로 도는 편이 나쁘다. */
    MkCfgItem *en = mk_cfg_find(cfg, "lcd.enabled");
    int enabled = en != NULL && en->cur.u != 0u;

    if (!enabled) {
        if (l->phase == MK_LCD_OFF) {
            return;
        }
        if (l->busy) {
            return;              /* 전송 중에는 못 멈춘다 — 다음 바퀴에 */
        }
        if (l->io.backlight != NULL) { l->io.backlight(l->io.ctx, 0); }
        set_cs(l, 0);
        l->phase = MK_LCD_OFF;
        l->step = 0u;
        l->sub = 0u;
        l->row = 0u;
        l->row_ready = 0;
        return;
    }

    /* 🔴 완료를 기다리지 않고 그냥 돌아간다. 이 한 줄이 이 파일의 요지다 —
     *    460,800 바이트를 동기로 밀면 ADS1256 표본과 텔레메트리가 통째로
     *    밀린다. */
    if (l->busy) {
        return;
    }

    switch (l->phase) {
    case MK_LCD_OFF:
        /* 하드웨어 리셋부터. 소프트 리셋(01h)을 따로 쓰지 않는다 —
         * RESX 선이 실제로 있고(PD13), 하드웨어 리셋이 같은 일을 하면서
         * 이전 상태(Sleep Out 이었는지)에 관계없이 확정적이다. */
        if (l->io.backlight != NULL) { l->io.backlight(l->io.ctx, 0); }
        set_cs(l, 0);
        if (l->io.reset != NULL) { l->io.reset(l->io.ctx, 1); }
        l->mark_ms = now_ms;
        l->phase = MK_LCD_RESET_LOW;
        return;

    case MK_LCD_RESET_LOW:
        if (now_ms - l->mark_ms < (int64_t)MK_LCD_RESET_LOW_MS) {
            return;
        }
        if (l->io.reset != NULL) { l->io.reset(l->io.ctx, 0); }
        l->mark_ms = now_ms;
        l->phase = MK_LCD_RESET_WAIT;
        return;

    case MK_LCD_RESET_WAIT:
        if (now_ms - l->mark_ms < (int64_t)MK_LCD_RESET_CANCEL_MS) {
            return;
        }
        l->phase = MK_LCD_INIT;
        l->step = 0u;
        l->sub = 0u;
        return;

    case MK_LCD_INIT:
        if (!step_cmd(l, &INIT_CMDS[l->step], now_ms)) {
            return;
        }
        l->step++;
        if (l->step >= (unsigned)N_INIT) {
            /* 마지막 줄이 RAMWR 이다. CS 를 **올리지 않고** 화소로 잇는다. */
            l->row = 0u;
            fill_row(l);
            l->phase = MK_LCD_FILL;
        } else {
            set_cs(l, 0);        /* 명령 사이에는 올려 둔다 (p.44 §4.2.1) */
        }
        return;

    case MK_LCD_FILL:
        if (l->row < MK_LCD_HEIGHT) {
            if (l->io.dc != NULL) { l->io.dc(l->io.ctx, 1); }
            send(l, l->row_buf, MK_LCD_ROW_BYTES);
            l->row++;
            return;
        }
        set_cs(l, 0);
        l->phase = MK_LCD_POST;
        l->step = 0u;
        l->sub = 0u;
        return;

    case MK_LCD_POST:
        if (!step_cmd(l, &POST_CMDS[l->step], now_ms)) {
            return;
        }
        l->step++;
        set_cs(l, 0);
        if (l->step >= (unsigned)N_POST) {
            /* 🔴 백라이트는 맨 마지막이다. 먼저 켜면 GRAM 의 임의값이
             *    사용자 눈에 그대로 보인다. */
            if (l->io.backlight != NULL) { l->io.backlight(l->io.ctx, 1); }
            l->phase = MK_LCD_DONE;
        }
        return;

    case MK_LCD_DONE:
    default:
        /* 한 번 칠하고 만다. 다시 그릴 이유가 생기면(부분 갱신) 여기서
         * 다음 직사각형을 꺼내게 된다 — 그 자리를 비워 둔다. */
        return;
    }
}
