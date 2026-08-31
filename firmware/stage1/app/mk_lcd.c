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
 *
 * 🔴 CASET·PASET·RAMWR 은 여기 없다. 2단계부터는 그것이 **직사각형마다
 *    달라지는 값**이라 정적 표에 둘 수 없다 — mk_lcd_paint() 가 세운다.
 *    보내는 순서(3A · 36 · 11 · 2A · 2B · 2C · 29)는 1단계와 같다.
 */
static const MkLcdCmd INIT_CMDS[] = {
    /* 3Ah COLMOD — p.200 §5.2.34. 파라미터는 `X DPI[2:0] X DBI[2:0]` 이고
     * 110 = 18 bits/pixel. RGB(DPI)·MCU(DBI) 둘 다 110 → 0x66.
     * 🔴 이것을 SLPOUT 앞에 두는 이유: Availability 표가 "Sleep In: Yes"
     *    라 지금 보낼 수 있고, 잠에서 깬 뒤에 화소 형식이 바뀌는 순간이
     *    없어야 첫 프레임이 깨지지 않는다. */
    { 0x3Au, 1u, { MK_LCD_COLMOD, 0u, 0u, 0u }, 0u },

    /* 36h MADCTL — p.192 §5.2.30. **MX(D6)=1, BGR(D3)=1**, 나머지 0 → 0x48.
     *
     * 🔴 두 비트 다 데이터시트가 아니라 **실물이 정했다.** 리셋 기본값은
     *    00h(p.194)인데 그것은 칩 기준이고, 이 모듈의 컬러필터 배열과 유리
     *    부착 방향은 거기 안 적혀 있다. 그래서 실기기에서 두 번에 걸쳐
     *    갈랐다:
     *
     *    [실증 2026-08-19 ①] D3=0 으로 주황(255,128,0)을 채웠더니 화면이
     *      **파랑**으로 나왔다 — 패널이 첫 바이트를 B 로 받는다. BGR 이다.
     *
     *    [실증 2026-08-19 ②] D3 만 세우고 글자를 그렸더니 **거울처럼 좌우가
     *      뒤집혀** 보였다 — 열 주소가 반대 방향으로 증가한다. MX 를 세운다.
     *
     *    🔴 한 색으로 채우는 1단계로는 ②를 못 잡는다. 방향이 틀려도 같은
     *       색이라 화면이 똑같다. 글자가 나온 뒤에야 보였다 — 그래서 색
     *       확인과 방향 확인은 **다른 그림이 필요한 서로 다른 시험**이다.
     *
     *    그리고 방향이 틀리면 색만 이상한 게 아니다. CASET/PASET 이 세우는
     *    직사각형도 거울상으로 찍혀서 부분 갱신 자리가 어긋나고, 화면에
     *    **겹쳐 그려진 자국**이 남는다 — 실제로 그 증상이 함께 나왔다.
     *
     *    반전은 여기 한 곳에서만 한다. mk_lcd_pixel() 의 색 순서도,
     *    mk_screen 의 좌표 계산도 건드리지 않는다 — 뒤집는 곳이 둘이 되면
     *    나중에 한쪽만 고쳐 놓고 왜 도로 틀렸는지 못 찾는다(mk_solctl 의
     *    flip_polarity 와 같은 규칙). */
    { 0x36u, 1u, { MK_LCD_MADCTL, 0u, 0u, 0u }, 0u },

    /* 11h SLPOUT — p.166 §5.2.13. DC/DC 컨버터·내부 발진기·패널 주사를
     * 켠다. 뒤에 5 ms 를 쉰다(위 상수 주석). */
    { 0x11u, 0u, { 0u, 0u, 0u, 0u }, MK_LCD_SLPOUT_DELAY_MS },
};

/* 첫 그림을 다 그린 뒤. 🔴 DISPON 을 앞에 두지 않는 이유: GRAM 은 전원
 * 인가 직후 임의값이라, 먼저 켜면 사용자가 보는 첫 화면이 잡동사니다.
 * 29h DISPON — p.174 §5.2.21. */
static const MkLcdCmd POST_CMDS[] = {
    { 0x29u, 0u, { 0u, 0u, 0u, 0u }, 0u },
};

#define N_INIT   (sizeof INIT_CMDS / sizeof INIT_CMDS[0])
#define N_POST   (sizeof POST_CMDS / sizeof POST_CMDS[0])

/* ── 되읽기 표 ────────────────────────────────────────────────────────────
 *
 * 🔴 우리가 써 넣은 값을 그대로 되묻는 것뿐이다. 그 이상은 안 묻는다 —
 *    0Ah(RDDPM)·09h(RDDST)도 읽을 수 있지만(p.155 §5.2.6 · p.153 §5.2.5)
 *    거기서 나오는 booster/sleep 비트는 우리가 고칠 방법이 없다. 고칠 수
 *    없는 것을 재는 계기는 화면만 어지럽힌다.
 *
 * 🔴 명령 뒤에 **더미 1바이트**가 붙는다. p.122 Figure 108 "4-Line SPI
 *    Mode Read Data" 의 "8 Dummy Clock" 이고, 명령표의 "1st Parameter is
 *    a dummy data" 와 같은 말이다. 그래서 늘 (1 + 값) 바이트를 받는다. */
static const uint8_t VERIFY_CMDS[2] = { 0x0Bu, 0x0Cu };
#define N_VERIFY  2u

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

/* 되읽기 한 번을 시작한다. `send` 와 같은 완료 규약이다. */
static void xfer(MkLcd *l, size_t n)
{
    l->busy = 1;
    if (l->io.xfer == NULL || !l->io.xfer(l->io.ctx, l->rd_tx, l->rd_rx, n)) {
        l->busy = 0;
    }
}

/* 하드웨어 리셋부터 다시 한다.
 *
 * 🔴 `post_done` 을 반드시 되돌린다. 안 그러면 리셋으로 꺼진 패널에
 *    DISPON 을 다시 안 보내고, 증상은 "회복했다는데 화면이 까맣다" 가
 *    된다 — 회복 장치가 고장을 하나 더 만드는 셈이다. */
static void restart(MkLcd *l)
{
    l->phase = MK_LCD_OFF;
    l->step = 0u;
    l->sub = 0u;
    l->row = 0u;
    l->post_done = 0;
    l->job_pending = 0;
    l->clear_filled = 0;
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

/* 바탕 지우기의 행 채우기. 🔴 한 색이므로 첫 행만 만들고 480번 다시 쓴다 —
 * 화소 계산을 153,600번 반복할 이유가 없다. */
static void clear_row(void *ctx, unsigned y_rel, unsigned w, uint8_t *out)
{
    MkLcd *l = (MkLcd *)ctx;
    (void)y_rel;
    if (l->clear_filled) {
        return;
    }
    for (unsigned x = 0; x < w; x++) {
        mk_lcd_pixel(MK_LCD_BG_R, MK_LCD_BG_G, MK_LCD_BG_B,
                     &out[x * MK_LCD_BYTES_PER_PIXEL]);
    }
    l->clear_filled = 1;
}

/* 전면을 바탕색으로 지우는 의뢰를 세운다.
 *
 * 🔴 초기화 직후와 주기적 전면 갱신이 같은 코드를 쓴다. 갈라 두면 한쪽만
 *    고쳐 놓고 "재초기화 뒤에는 멀쩡한데 주기 갱신 뒤에는 자국이 남는다"
 *    같은 것을 쫓게 된다. */
static void start_clear(MkLcd *l)
{
    l->job_x = 0u;
    l->job_y = 0u;
    l->job_w = MK_LCD_WIDTH;
    l->job_h = MK_LCD_HEIGHT;
    l->job_fill = clear_row;
    l->job_ctx = l;
    l->clear_filled = 0;
    l->job_pending = 1;
}

/* 지금 직사각형의 주소창 명령 셋을 만든다.
 *
 * 2Ah CASET — p.175 §5.2.22 · 2Bh PASET — p.177 §5.2.23 ·
 * 2Ch RAMWR — p.179 §5.2.24. RAMWR 이 마지막이어야 하고, 그 뒤로 CS 를
 * 올리지 않고 화소가 이어진다 (p.44 §4.2.1: CSX 가 High 이면 SDA·SCL 이
 * 무효다). */
static void build_window(MkLcd *l)
{
    unsigned x1 = l->job_x + l->job_w - 1u;
    unsigned y1 = l->job_y + l->job_h - 1u;

    l->win[0].cmd = 0x2Au;
    l->win[0].n_args = 4u;
    l->win[0].args[0] = (uint8_t)(l->job_x >> 8);
    l->win[0].args[1] = (uint8_t)(l->job_x & 0xFFu);
    l->win[0].args[2] = (uint8_t)(x1 >> 8);
    l->win[0].args[3] = (uint8_t)(x1 & 0xFFu);
    l->win[0].delay_ms = 0u;

    l->win[1].cmd = 0x2Bu;
    l->win[1].n_args = 4u;
    l->win[1].args[0] = (uint8_t)(l->job_y >> 8);
    l->win[1].args[1] = (uint8_t)(l->job_y & 0xFFu);
    l->win[1].args[2] = (uint8_t)(y1 >> 8);
    l->win[1].args[3] = (uint8_t)(y1 & 0xFFu);
    l->win[1].delay_ms = 0u;

    l->win[2].cmd = 0x2Cu;
    l->win[2].n_args = 0u;
    l->win[2].delay_ms = 0u;
}

/* ---- 회복 ---------------------------------------------------------------- */

/* 설정에서 주기를 읽는다. 항목이 없거나 0 이면 "안 한다". */
static uint32_t period_of(MkConfig *cfg, const char *key)
{
    MkCfgItem *it = mk_cfg_find(cfg, key);
    return it != NULL ? it->cur.u : 0u;
}

static int verify_due(const MkLcd *l, MkConfig *cfg, int64_t now)
{
    /* 되읽기 창구가 없는 빌드(MISO 를 안 열었다)에서는 아예 안 한다. */
    if (l->io.xfer == NULL) {
        return 0;
    }
    /* 🔴 첫 대조가 틀렸으면 그 뒤로 묻지 않는다. 되읽기를 못 믿는 판에서
     *    계속 물으면 버스만 쓰고, 그 답으로 무엇을 할 수도 없다. */
    if (l->verify_primed && !l->read_trusted) {
        return 0;
    }
    uint32_t period = period_of(cfg, "lcd.verify_ms");
    if (period == 0u) {
        return 0;
    }
    return now - l->verify_mark_ms >= (int64_t)period;
}

static int redraw_due(const MkLcd *l, MkConfig *cfg, int64_t now)
{
    uint32_t period = period_of(cfg, "lcd.redraw_ms");
    if (period == 0u) {
        return 0;
    }
    return now - l->redraw_mark_ms >= (int64_t)period;
}

/* 되읽은 값으로 무엇을 할지 정한다.
 *
 * 🔴 이 함수가 이번 작업의 요지다. 두 고장을 가른다:
 *
 *      다르다  → **명령이 깨졌다.** MADCTL 이 바뀌면 방향·색이 통째로
 *                틀어지고 CASET/PASET 이 세우는 직사각형도 거울상으로
 *                찍힌다(2026-08-19 실기기에서 실제로 본 증상). COLMOD 가
 *                바뀌면 화소 폭이 어긋나 화면 전체가 사선으로 밀린다.
 *                전면 다시 그리기로는 못 고친다 — 레지스터를 되세워야
 *                하고, 그러려면 하드웨어 리셋부터가 확정적이다.
 *
 *      같다    → 레지스터는 멀쩡하다. 그래도 화면이 이상하다면 GRAM 쓰기
 *                포인터가 밀린 것이고, 그것은 `lcd.redraw_ms` 의 주기적
 *                전면 갱신이 덮는다. 여기서는 아무것도 안 한다.
 */
static void finish_verify(MkLcd *l)
{
    int match = (l->got_madctl == MK_LCD_MADCTL)
                && (l->got_colmod == MK_LCD_COLMOD);

    if (!l->verify_primed) {
        /* 🔴 첫 대조는 **방금 우리가 써 넣은 값**을 되읽는 것이다. 여기서
         *    어긋나면 패널이 값을 잃은 것이 아니라 되읽기 경로를 못 믿는
         *    것이다 — 그때 재초기화로 대응하면 멀쩡한 화면을 몇 초마다
         *    다시 켜게 되고, 사용자가 보는 증상이 원래 고장보다 나빠진다.
         *    검사만 끄고 그 사실을 $STAT 으로 알린다. */
        l->verify_primed = 1;
        l->read_trusted = match;
        if (match) { l->verify_ok++; } else { l->verify_fail++; }
        l->phase = MK_LCD_READY;
        return;
    }

    if (match) {
        l->verify_ok++;
        l->phase = MK_LCD_READY;
        return;
    }

    l->verify_fail++;
    l->reinit++;
    restart(l);        /* epoch 은 OFF 분기가 올린다 */
}

/* 되읽기 한 걸음. 🔴 한 바퀴에 하나다 — 6바이트짜리라도 여기에 완료를
 * 기다리는 코드를 넣으면 그것이 곧 슈퍼루프가 서는 자리가 된다. */
static void step_verify(MkLcd *l, int64_t now)
{
    (void)now;
    switch (l->sub) {
    case 0u:
        set_cs(l, 1);
        if (l->io.dc != NULL) { l->io.dc(l->io.ctx, 0); }   /* 명령 */
        l->rd_tx[0] = VERIFY_CMDS[l->verify_step];
        xfer(l, 1u);
        l->sub = 1u;
        return;

    case 1u:
        if (l->io.dc != NULL) { l->io.dc(l->io.ctx, 1); }   /* 데이터 */
        /* 🔴 2바이트를 받는다: 더미 1 + 값 1. p.122 Figure 108 의
         *    "8 Dummy Clock", 명령표의 "1st Parameter is a dummy data".
         *    더미를 안 건너뛰면 되읽은 값이 늘 틀리고, 그러면 이 장치가
         *    멀쩡한 패널을 주기적으로 다시 켜는 장치가 된다. */
        l->rd_tx[0] = 0u;
        l->rd_tx[1] = 0u;
        xfer(l, 2u);
        l->sub = 2u;
        return;

    default:
        if (l->verify_step == 0u) {
            l->got_madctl = l->rd_rx[1];
        } else {
            l->got_colmod = l->rd_rx[1];
        }
        /* 명령 하나가 끝났으면 CS 를 올린다 (p.44 §4.2.1 — CSX 가 High 로
         * 올라가야 그 명령이 닫힌다). */
        set_cs(l, 0);
        l->sub = 0u;
        l->verify_step++;
        if (l->verify_step < N_VERIFY) {
            return;
        }
        finish_verify(l);
        return;
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
    return l->phase == MK_LCD_READY;
}

uint32_t mk_lcd_epoch(const MkLcd *l)
{
    return l->epoch;
}

void mk_lcd_stat(const MkLcd *l, MkLcdStat *out)
{
    out->epoch       = l->epoch;
    out->reinit      = l->reinit;
    out->redraw      = l->redraw;
    out->verify_ok   = l->verify_ok;
    out->verify_fail = l->verify_fail;
    out->rejected    = l->rejected;
    /* 🔴 아직 안 물어본 것과 "못 믿는다" 를 가른다. 둘을 같은 값으로 두면
     *    화면이 "되읽기 안 됨" 이라고 말하는데 실은 아직 한 번도 안 물어본
     *    상태일 수 있다 — pps_age_ms 의 null 과 같은 결. */
    out->readback    = l->verify_primed ? (int8_t)(l->read_trusted ? 1 : 0)
                                        : (int8_t)-1;
}

int mk_lcd_idle(const MkLcd *l)
{
    return l->phase == MK_LCD_READY && !l->job_pending && !l->busy;
}

int mk_lcd_paint(MkLcd *l, unsigned x, unsigned y, unsigned w, unsigned h,
                 MkLcdRowFill fill, void *ctx)
{
    if (!mk_lcd_idle(l)) {
        return 0;               /* 정상 — 다음 바퀴에 다시 부르면 된다 */
    }
    /* 🔴 범위를 넘는 의뢰는 조용히 잘라 그리지 않는다. 잘라 그리면 화면이
     *    한 칸 밀린 채 채워져 원인이 배선처럼 보이고, DMA 는 행 버퍼 밖을
     *    읽는다. 거절하고 세어 둔다 — 그 수가 0 이 아니면 배치가 틀렸다. */
    if (fill == NULL || w == 0u || h == 0u
        || x + w > MK_LCD_WIDTH || y + h > MK_LCD_HEIGHT
        || (size_t)w * MK_LCD_BYTES_PER_PIXEL > l->row_cap) {
        l->rejected++;
        return 0;
    }

    l->job_x = x;
    l->job_y = y;
    l->job_w = w;
    l->job_h = h;
    l->job_fill = fill;
    l->job_ctx = ctx;
    l->job_pending = 1;
    return 1;
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
        restart(l);
        return;
    }

    /* 🔴 완료를 기다리지 않고 그냥 돌아간다. 이 한 줄이 이 파일의 요지다 —
     *    460,800 바이트를 동기로 밀면 ADS1256 표본과 텔레메트리가 통째로
     *    밀린다. */
    if (l->busy) {
        return;
    }

    /* 🔴 SPI 쓰기 클럭. **버스가 노는 지금** 바꾼다 — 전송 중에 분주비를
     *    건드리면 그 전송의 나머지가 어떤 클럭으로 나갔는지 아무도 모르고,
     *    증상은 "가끔 한 행만 깨진다" 라 배선처럼 보인다.
     *
     *    항목이 없으면 8 MHz 로 본다. 설정표에서 항목이 사라지는 것은
     *    실수이고, 실수했을 때는 느린 쪽이 안전하다. */
    {
        MkCfgItem *sk = mk_cfg_find(cfg, "lcd.spi_khz");
        uint32_t khz = sk != NULL ? sk->cur.u : 8000u;
        if (khz != l->spi_khz) {
            l->spi_khz = khz;
            if (l->io.set_clock != NULL) {
                l->io.set_clock(l->io.ctx, khz);
            }
        }
    }

    switch (l->phase) {
    case MK_LCD_OFF:
        /* 하드웨어 리셋부터. 소프트 리셋(01h)을 따로 쓰지 않는다 —
         * RESX 선이 실제로 있고(PD13), 하드웨어 리셋이 같은 일을 하면서
         * 이전 상태(Sleep Out 이었는지)에 관계없이 확정적이다. */
        if (l->io.backlight != NULL) { l->io.backlight(l->io.ctx, 0); }
        set_cs(l, 0);
        if (l->io.reset != NULL) { l->io.reset(l->io.ctx, 1); }
        /* 🔴 여기서 epoch 이 오른다. 이 순간부터 패널의 GRAM 은 우리가
         *    아는 내용이 아니다 — 화면 내용을 들고 있는 쪽이 이 값을 보고
         *    전부 다시 그린다 (mk_lcd_epoch). */
        l->epoch++;
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
        set_cs(l, 0);            /* 명령 사이에는 올려 둔다 (p.44 §4.2.1) */
        if (l->step >= (unsigned)N_INIT) {
            /* 🔴 첫 일은 전면을 바탕색으로 지우는 것이다. GRAM 은 전원
             *    인가 직후 임의값이고, 그 위에 칸만 그리면 나머지 자리에
             *    잡동사니가 남는다. */
            start_clear(l);
            l->phase = MK_LCD_READY;   /* 아래 READY 분기가 곧바로 집어간다 */
        }
        return;

    case MK_LCD_WINDOW:
        if (!step_cmd(l, &l->win[l->step], now_ms)) {
            return;
        }
        l->step++;
        if (l->step >= 3u) {
            /* 마지막 줄이 RAMWR 이다. CS 를 **올리지 않고** 화소로 잇는다. */
            l->row = 0u;
            l->phase = MK_LCD_FILL;
        } else {
            set_cs(l, 0);
        }
        return;

    case MK_LCD_FILL:
        if (l->row < l->job_h) {
            /* 🔴 행 하나를 만들고 전송을 **시작만** 한다. 완료는 다음
             *    바퀴에 mk_lcd_on_tx_done() 이 알려 준다. */
            l->job_fill(l->job_ctx, l->row, l->job_w, l->row_buf);
            if (l->io.dc != NULL) { l->io.dc(l->io.ctx, 1); }
            send(l, l->row_buf, (size_t)l->job_w * MK_LCD_BYTES_PER_PIXEL);
            l->row++;
            return;
        }
        set_cs(l, 0);
        if (!l->post_done) {
            l->phase = MK_LCD_POST;
            l->step = 0u;
            l->sub = 0u;
        } else {
            l->phase = MK_LCD_READY;
        }
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
            l->post_done = 1;
            /* 회복 시계를 여기서 맞춘다 — 화면이 제대로 선 순간이다. */
            l->verify_mark_ms = now_ms;
            l->redraw_mark_ms = now_ms;
            l->phase = MK_LCD_READY;
        }
        return;

    case MK_LCD_VERIFY:
        step_verify(l, now_ms);
        return;

    case MK_LCD_READY:
    default:
        /* 🔴 순서가 뜻이 있다. 되읽기가 먼저다 — 맡겨 둔 칸이 있을 때
         *    미루면, 값이 자주 바뀌는 화면에서는 되읽기가 영영 차례를
         *    못 받는다. 되읽기는 6바이트라 칸 하나를 두어 바퀴 늦출 뿐이다.
         *
         * 🔴 전면 갱신도 맡겨 둔 칸을 **기다리지 않는다.** 처음에는
         *    `!job_pending` 일 때만 시작하게 짰는데, 값이 쉬지 않고 바뀌는
         *    화면에서는 mk_screen 이 매 바퀴 다음 칸을 맡기므로 그 조건이
         *    한 번도 성립하지 않았다 — 주기 갱신이 통째로 굶었고, 마침
         *    test_no_sample_is_lost_while_the_screen_redraws 가 그것을
         *    잡았다. 맡겨 둔 칸을 덮어도 잃는 것이 없다: 바로 아래에서
         *    epoch 이 올라 mk_screen 이 그 칸을 다시 맡긴다. */
        if (verify_due(l, cfg, now_ms)) {
            l->verify_mark_ms = now_ms;
            l->verify_step = 0u;
            l->sub = 0u;
            l->phase = MK_LCD_VERIFY;
            return;
        }
        if (redraw_due(l, cfg, now_ms)) {
            /* 🔴 **마지막 그물.** 되읽기가 맞는데도 화면이 이상한 경우 —
             *    GRAM 쓰기 포인터가 밀린 경우 — 는 레지스터로 알 방법이
             *    없다. 그래서 값이 안 바뀌어도 주기적으로 전면을 다시
             *    그린다. 바탕까지 다시 칠해야 칸 바깥에 밀려 찍힌 화소가
             *    지워진다. */
            l->redraw_mark_ms = now_ms;
            l->redraw++;
            l->epoch++;              /* mk_screen 이 칸을 전부 다시 그린다 */
            start_clear(l);
        }
        /* 맡겨 둔 직사각형이 있으면 집어간다. 없으면 **한 바이트도 안
         * 나간다** — 이 조용함이 2단계의 요지다. */
        if (l->job_pending) {
            l->job_pending = 0;
            build_window(l);
            l->phase = MK_LCD_WINDOW;
            l->step = 0u;
            l->sub = 0u;
        }
        return;
    }
}
