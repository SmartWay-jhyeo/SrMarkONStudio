/* ILI9488 LCD (J25) — HAL 비의존.
 *
 * 이 층이 아는 것은 **명령 순서와 대기시간, 그리고 직사각형 하나를 어떻게
 * 밀어 넣는가**뿐이다. SPI·GPIO·DMA 는 bsp/mk_lcd_io.c 가 한다
 * (mk_ads1256 / mk_ads_io 와 같은 구조). 무엇을 그릴지는 app/mk_screen.c 가
 * 정한다 — 그래서 초기화 순서도 화면 내용도 보드 없이 시험된다.
 *
 * ── 결선 [넷리스트 확인 2026-08-19,
 *      docs/superpowers/specs/2026-08-19-lcd-hardware-facts.md] ─────────
 *
 *      PB12  LCD_CS    (J25.3)   GPIO 출력 — SPI2_NSS 를 쓰지 않는다
 *      PB13  SCK       (J25.7)   SPI2_SCK  = AF5, 직렬 22Ω(R91)
 *      PB15  MOSI      (J25.6)   SPI2_MOSI = AF5, 직렬 22Ω(R92)
 *      PB14  MISO      (J25.9)   SPI2_MISO = AF5 — 아직 안 쓴다
 *      PD15  D/CX      (J25.5)   GPIO 출력
 *      PD13  RESX      (J25.4)   GPIO 출력, 10k 풀업(R90)
 *      PB6   백라이트  (J25.8)   GPIO 출력
 *      PD14  터치 CS   (J25.11)  🔴 LCD 와 **같은 버스**다. 비선택(High)
 *                                으로 고정한다 — 안 그러면 XPT2046 이 같은
 *                                클럭에 반응해 MISO 를 물고 늘어진다
 *
 * ── 화소 형식 ────────────────────────────────────────────────────────
 *
 * 🔴 18bpp(화소당 3바이트)다. ILI9488.pdf p.121 §4.7.2 는 4선 SPI 에서
 *    565 도 쓸 수 있다고 적지만, 바로 아래 하위 절에는 3bpp(§4.7.2.1)와
 *    18bpp(§4.7.2.2)의 데이터 배치 그림만 있고 **16bpp 그림이 없다**.
 *    근거가 있는 쪽으로 간다 (사용자 확정 2026-08-19).
 *
 *    p.122 Figure 107: 바이트마다 D7..D2 = R5..R0 이고 D1·D0 은 void.
 *    8비트 색 c 는 (c>>2)<<2 = c & 0xFC 가 된다.
 *
 * ── 왜 상태기계인가 · 왜 부분 갱신인가 ────────────────────────────────
 *
 * 🔴 전면 갱신이 320 x 480 x 3 = 460,800 바이트다. SPI2 16 MHz 에서 약
 *    230 ms — 동기로 보내면 그 사이 ADS1256 표본과 텔레메트리가 통째로
 *    밀린다. 그래서 **완료를 기다리지 않고 빠져나온다**: 한 바퀴에 걸음
 *    하나만 나아가고, DMA 완료는 mk_lcd_on_tx_done() 이 알려 준다
 *    (app/mk_i2c.c 의 포트 상태기계와 같은 결).
 *
 * 🔴 그리고 **직사각형 단위로만 그린다.** 1단계는 한 색으로 전면을 채우는
 *    것이 전부였지만, 2단계는 값 하나가 바뀔 때마다 그린다. 그때마다
 *    460,800 바이트를 밀면 초당 4번에 SPI2 가 92% 를 쓰고, 1단계에서
 *    실기기로 확인한 성질(ain 간격 중앙값 100 ms, 큐 drops 0)을 그대로
 *    잃는다. 바뀐 칸의 직사각형(258 x 16 = 12,384 바이트, 약 6 ms)만
 *    보낸다 — 전면의 2.7% 다.
 *
 *    사용자가 못박은 선이 이것이다 (2026-08-19): "뭘 하던지 센서 수집에는
 *    방해가 안된다면 뭐든지 해도 돼", "센서 값을 늦게 보내도 돼. 무조건
 *    수집을 정상적으로 타임스탬프 찍어서 가지고 있어야해."
 *
 * 🔴 프레임버퍼를 들지 않는다. 460,800 바이트를 이 MCU 에 둘 수 없고,
 *    둘 이유도 없다 — 그리는 쪽(mk_screen)이 행 하나씩 만들어 준다.
 *    행 버퍼는 하나(최대 960바이트)뿐이고 DMA 가 그것을 읽는다.
 *
 * ── 회복 — 깨진 화면이 저절로 안 돌아오는 문제 ────────────────────────
 *
 * 🔴 [실기기 2026-08-19, 사용자] "LCD는 가끔 리셋을 해줘야겠다. 노이즈
 *    타면 픽셀이 다 깨지는데?" — 요지는 **깨진 뒤 저절로 안 돌아온다**는
 *    것이다. 부분 갱신은 값이 바뀐 칸만 다시 그리므로, 한 번 어긋난
 *    그림은 그 칸의 값이 바뀔 때까지 남는다. 부분 갱신의 효율이 곧
 *    회복 불능의 원인이다.
 *
 *    증상은 **무작위**다(사용자 확인 2026-08-19). 24V 스위칭·유압 동작과
 *    무관하고 케이블을 만질 때도 아니다. 캐시 일관성도 아니다 — 이
 *    펌웨어는 SCB_EnableDCache 를 아예 부르지 않는다.
 *
 * 🔴 그래서 **패널에게 되물어본다.** MISO(PB14)가 실제로 물려 있고
 *    (J25.9, 넷리스트 확인 2026-08-19), ILI9488 은 레지스터를 되읽는다:
 *
 *      0Bh RDDMADCTL   p.157 §5.2.7 — 우리가 넣은 0x48 이 남아 있나
 *      0Ch RDDCOLMOD   p.159 §5.2.8 — 우리가 넣은 0x66 이 남아 있나
 *
 *    되읽기에는 **더미 1바이트가 앞에 붙는다**. p.122 Figure 108
 *    "4-Line SPI Mode Read Data" 가 명령 뒤에 "8 Dummy Clock" 을 그려
 *    두었고, 명령표들도 "1st Parameter is a dummy data" 라고 적는다.
 *
 *    이 대조가 사용자에게 원인을 알려 주는 유일한 창구다:
 *      다르다 → **명령이 깨졌다.** 방향·색이 통째로 틀어진다. 초기화부터
 *      같은데 화면이 이상하다 → **GRAM 동기가 어긋났다.** 전면 다시 그리기
 *
 * 🔴 되읽기 클럭 상한은 6.6 MHz 다(trc MIN 150 ns, p.332 §17.4.3). 쓰기
 *    상한 20 MHz 와 다르므로 bsp 가 되읽기 동안만 분주비를 낮춘다.
 *
 * 🔴 **첫 대조가 되읽기 경로를 판정한다.** 초기화 직후의 MADCTL·COLMOD 는
 *    방금 우리가 써 넣은 값이라 반드시 맞아야 한다. 거기서 어긋나면 패널이
 *    값을 잃은 것이 아니라 되읽기를 못 믿는 것이다(흔한 3.5" 모듈은 SDO 가
 *    안 물려 있거나 저항 하나를 건너 나온다). 그때 재초기화로 대응하면
 *    멀쩡한 화면을 몇 초마다 다시 켜게 된다 — 지어낸 회복이 원래 고장보다
 *    나쁘다. 그래서 검사만 끄고 그 사실을 $STAT 으로 알린다.
 */
#ifndef MK_LCD_H
#define MK_LCD_H

#include <stddef.h>
#include <stdint.h>

#include "mk_config.h"     /* MkConfig · mk_cfg_find — tick 이 설정을 읽는다 */

/* 패널 크기. ILI9488 은 320RGB x 480 이다 (데이터시트 표지). */
#define MK_LCD_WIDTH        320u
#define MK_LCD_HEIGHT       480u

/* 화소당 바이트. 18bpp = 3 (위 화소 형식 절 참고). */
#define MK_LCD_BYTES_PER_PIXEL  3u

/* 한 행의 바이트 수 — DMA 한 번의 최대 크기이기도 하다. */
#define MK_LCD_ROW_BYTES    (MK_LCD_WIDTH * MK_LCD_BYTES_PER_PIXEL)

/* 명령 파라미터의 최대 개수. 지금 가장 긴 것은 CASET·PASET 의 4바이트다. */
#define MK_LCD_ARGS_MAX     4

/* bsp 가 대 줘야 하는 명령 버퍼의 크기. 명령 1바이트와 파라미터를 따로
 * 보내므로 파라미터 최대치면 충분하다. */
#define MK_LCD_CMD_BUF_BYTES  (MK_LCD_ARGS_MAX + 1u)

/* 되읽기 버퍼. 지금 가장 긴 것은 더미 1 + 값 1 = 2 바이트다. */
#define MK_LCD_READ_MAX     4u

/* 🔴 우리가 패널에 써 넣는 값. **한 곳에만 적는다** — 초기화 표와 되읽기
 *    대조가 같은 상수를 봐야 한다. 둘로 나뉘면 초기화만 고치고 대조는
 *    옛 값을 기다리게 되고, 그 순간 이 회복 장치가 멀쩡한 패널을 몇 초마다
 *    다시 켜는 장치가 된다.
 *
 *    MADCTL 0x48 = MX(D6) + BGR(D3). 둘 다 실물이 정했다 — 아래 INIT_CMDS
 *    주석에 실증 기록이 있다.
 *    COLMOD 0x66 = DPI·DBI 둘 다 110 = 18 bits/pixel (p.200 §5.2.34).
 *
 * 🔴 되읽기가 돌려주는 값은 D1·D0 이 항상 0 이다(p.157 §5.2.7 · p.159
 *    §5.2.8 의 "2nd Parameter" 행). 0x48·0x66 은 이미 하위 2비트가 0 이라
 *    마스크가 필요 없다. */
#define MK_LCD_MADCTL       0x48u
#define MK_LCD_COLMOD       0x66u

/* ---- 색 ------------------------------------------------------------------ */

typedef struct {
    uint8_t r, g, b;
} MkLcdColor;

/* 🔴 바탕색. **검정이 아니다.**
 *
 *    검정으로 두면 "SPI 가 아예 안 나갔다"(GRAM 이 어둡거나 백라이트만
 *    켜진 상태)와 "정상적으로 바탕을 칠했다"가 화면에서 구분되지 않는다.
 *    1단계에서 주황으로 채운 이유와 같은 종류의 문제다 — 화면이 유일한
 *    진단 수단인 자리에서 두 고장을 같은 그림으로 만들면 안 된다.
 *
 *    짙은 남색이면 글자(흰색) 대비도 충분하고, 한눈에 "무언가 그려졌다"를
 *    알 수 있다. */
#define MK_LCD_BG_R  0x00u
#define MK_LCD_BG_G  0x0Cu
#define MK_LCD_BG_B  0x28u

/* ---- 하드웨어 창구 ------------------------------------------------------- */

typedef struct {
    /* LCD 칩 선택. low != 0 이면 CS 를 Low(선택)로. */
    void (*cs)(void *ctx, int low);
    /* D/CX. data != 0 이면 데이터, 0 이면 명령. */
    void (*dc)(void *ctx, int data);
    /* 하드웨어 리셋. low != 0 이면 RESX 를 Low 로. */
    void (*reset)(void *ctx, int low);
    /* 백라이트. */
    void (*backlight)(void *ctx, int on);
    /* 🔴 **즉시 돌아와야 한다.** 완료는 mk_lcd_on_tx_done() 으로 알린다.
     *    여기서 블로킹 전송을 부르면 비차단으로 만든 뜻이 통째로 사라진다.
     *    시작했으면 1, 못 했으면 0. */
    int  (*send)(void *ctx, const uint8_t *buf, size_t n);

    /* 되읽기 — 전이중 한 번. `tx` 를 내보내면서 같은 길이의 `rx` 를 받는다.
     *
     * 🔴 NULL 이면 되읽기를 **아예 하지 않는다** (MISO 를 안 연 빌드).
     *
     * 🔴 읽기 클럭 상한이 쓰기와 다르다 — trc MIN 150 ns = 6.6 MHz
     *    (p.332 §17.4.3). 그것을 지키는 것은 bsp 의 일이다. 이 층은
     *    "무엇을 언제 묻는가" 만 안다.
     *
     * 🔴 `send` 와 같은 완료 규약이다: 시작했으면 1, 완료는
     *    mk_lcd_on_tx_done(). 최대 2바이트라 bsp 가 동기로 끝내고 그 안에서
     *    완료를 알려도 된다 — 4 MHz 에서 4 us 다. */
    int  (*xfer)(void *ctx, const uint8_t *tx, uint8_t *rx, size_t n);

    /* SPI 쓰기 클럭을 바꾼다(kHz). NULL 이면 `lcd.spi_khz` 가 무시된다.
     *
     * 🔴 버스가 노는 순간에만 불린다 — 전송 중에 분주비를 바꾸면 그
     *    전송의 나머지가 어떤 클럭으로 나갔는지 아무도 모른다. */
    void (*set_clock)(void *ctx, uint32_t khz);

    void *ctx;
} MkLcdIo;

/* ---- 그리기 의뢰 --------------------------------------------------------- */

/* 직사각형의 한 행을 화소로 채워 준다.
 *
 *   y_rel : 직사각형 위 끝에서 몇 번째 행인가 (0 .. h-1)
 *   w     : 이 직사각형의 폭(화소)
 *   out   : w * MK_LCD_BYTES_PER_PIXEL 바이트. 18bpp 로 채운다
 *           (mk_lcd_pixel 을 쓰면 된다)
 *
 * 🔴 즉시 돌아와야 한다. 이 콜백은 전송 하나를 시작하기 직전에 불리고,
 *    여기서 오래 머물면 그만큼 슈퍼루프가 선다. */
typedef void (*MkLcdRowFill)(void *ctx, unsigned y_rel, unsigned w,
                             uint8_t *out);

/* ---- 초기화 표 ----------------------------------------------------------- */

typedef struct {
    uint8_t  cmd;
    uint8_t  n_args;
    uint8_t  args[MK_LCD_ARGS_MAX];
    uint16_t delay_ms;              /* 이 명령을 보낸 뒤 쉬는 시간 */
} MkLcdCmd;

/* ---- 상태기계 ------------------------------------------------------------ */

typedef enum {
    MK_LCD_OFF = 0,
    MK_LCD_RESET_LOW,      /* RESX Low — tRW 를 채운다 */
    MK_LCD_RESET_WAIT,     /* RESX High — tRT(리셋 취소)를 기다린다 */
    MK_LCD_INIT,           /* 초기화 표를 걷는다 (COLMOD·MADCTL·SLPOUT) */
    MK_LCD_WINDOW,         /* 주소창을 세운다 (CASET·PASET·RAMWR) */
    MK_LCD_FILL,           /* 직사각형의 행을 h 번 보낸다 */
    MK_LCD_POST,           /* 첫 그림 뒤의 표 (DISPON) */
    MK_LCD_VERIFY,         /* 되읽기 — MADCTL·COLMOD 가 남아 있나 */
    MK_LCD_READY,          /* 패널이 살아 있다 — 의뢰를 받는다 */
    MK_LCD_UNUSABLE        /* 버퍼가 모자란다. 시작조차 하지 않는다 */
} MkLcdPhase;

/* 회복 계수기 — `$STAT` 의 `lcd` 객체가 그대로 싣는다.
 *
 * 🔴 "몇 번 깨졌고 몇 번 되살렸나" 를 모르면 이 문제가 해결됐는지 덮였는지
 *    알 수 없다. PPS 에서 `pps_raw_count` 로 같은 일을 했다 — 짝짓기가
 *    안 되던 것과 펄스가 안 오던 것을 그 계수기 하나가 갈랐다. */
typedef struct {
    uint32_t epoch;        /* 그린 것을 못 믿게 된 횟수 (재초기화 + 주기 갱신) */
    uint32_t reinit;       /* 되읽기 불일치로 하드웨어 리셋부터 다시 한 횟수 */
    uint32_t redraw;       /* 주기적 전면 다시 그리기 횟수 */
    uint32_t verify_ok;    /* 되읽기 대조 성공 */
    uint32_t verify_fail;  /* 되읽기 대조 실패 */
    uint32_t rejected;     /* 받아들이지 못한 그리기 의뢰 (배치 실수의 신호) */
    /* 🔴 -1 = 아직 안 물어봤다 · 0 = 되읽기를 못 믿는다 · 1 = 믿는다.
     *    0 이면 위 verify_* 는 더 이상 늘지 않는다 — 검사를 껐다는 뜻이다. */
    int8_t   readback;
} MkLcdStat;

typedef struct MkLcd {
    MkLcdIo    io;
    uint8_t   *cmd_buf;
    size_t     cmd_cap;
    uint8_t   *row_buf;
    size_t     row_cap;

    MkLcdPhase phase;
    unsigned   step;        /* 표에서 지금 줄 */
    unsigned   sub;         /* 0 = 명령 바이트, 1 = 파라미터, 2·3 = 대기 */
    unsigned   row;         /* FILL 에서 지금 행 */
    int64_t    mark_ms;     /* 마지막 전이·전송 시각 */

    /* 🔴 volatile 이다 — mk_lcd_on_tx_done() 은 DMA 완료 인터럽트에서
     *    불리고 mk_lcd_tick() 은 슈퍼루프에서 읽는다. */
    volatile int busy;

    int        cs_low;
    int        post_done;   /* DISPON·백라이트를 이미 했나 */

    /* 지금 그리고 있는 직사각형. */
    unsigned     job_x, job_y, job_w, job_h;
    MkLcdRowFill job_fill;
    void        *job_ctx;
    int          job_pending;   /* 받아 놓고 아직 시작 안 한 의뢰가 있다 */
    int          clear_filled;  /* 바탕 지우기: 행 버퍼를 이미 채웠다 */

    /* 주소창 명령 셋(CASET·PASET·RAMWR). 직사각형마다 인자가 달라지므로
     * 정적 표로 둘 수 없다. */
    MkLcdCmd   win[3];

    /* 🔴 초기화를 몇 번째 하고 있나. 화면 내용을 들고 있는 쪽이 "내가
     *    그린 것이 아직 패널에 남아 있나" 를 판단하는 유일한 근거다.
     *    자세한 것은 mk_lcd_epoch() 주석. */
    uint32_t   epoch;

    /* 진단 — 받아들이지 못한 의뢰 수. 0 이 아니면 배치나 호출 순서가
     * 틀린 것이다(그 칸은 영영 안 그려진다). */
    uint32_t   rejected;

    /* ── 회복 ─────────────────────────────────────────────────────── */

    uint8_t    rd_tx[MK_LCD_READ_MAX];
    uint8_t    rd_rx[MK_LCD_READ_MAX];
    unsigned   verify_step;      /* 0 = MADCTL · 1 = COLMOD · 2 = 끝 */
    uint8_t    got_madctl;
    uint8_t    got_colmod;
    int        verify_primed;    /* 첫 대조를 마쳤나 */
    int        read_trusted;     /* 첫 대조가 맞았다 = 되읽기를 믿는다 */
    int64_t    verify_mark_ms;
    int64_t    redraw_mark_ms;

    uint32_t   reinit;
    uint32_t   redraw;
    uint32_t   verify_ok;
    uint32_t   verify_fail;

    uint32_t   spi_khz;          /* 마지막으로 bsp 에 넘긴 값. 0 = 아직 */
} MkLcd;

/* 🔴 버퍼를 bsp 가 대 준다. app/ 은 `__attribute__((section))` 을 쓸 수
 *    없고(호스트 시험이 MSVC 로 돈다), DMA 가 닿는 자리는 bsp/mk_dma_mem.h
 *    가 안다 — mk_ads_init() 이 tx/rx 버퍼를 받는 것과 같은 이유다.
 *
 *    row_cap 이 MK_LCD_ROW_BYTES 보다 작으면 MK_LCD_UNUSABLE 로 남는다.
 *    짧은 버퍼로 그리기 시작하면 화면이 어긋난 채 채워져, 원인이 배선처럼
 *    보인다. */
void mk_lcd_init(MkLcd *l, const MkLcdIo *io,
                 uint8_t *cmd_buf, size_t cmd_cap,
                 uint8_t *row_buf, size_t row_cap);

/* 한 바퀴에 걸음 하나. 매 루프 불러도 된다 — 전송이 떠 있으면 즉시
 * 돌아온다. `lcd.enabled` 가 꺼져 있으면 아무것도 하지 않는다. */
void mk_lcd_tick(MkLcd *l, MkConfig *cfg, int64_t now_ms);

/* DMA/SPI 완료 인터럽트에서 부른다. */
void mk_lcd_on_tx_done(MkLcd *l);

/* 패널이 켜져 있고 초기화·첫 바탕칠이 끝났는가. 직사각형 하나를 그리는
 * 동안에는 거짓이다(그동안은 새 의뢰를 못 받는다). */
int  mk_lcd_ready(const MkLcd *l);

/* 그린 것을 못 믿게 된 세대. 아래 둘 중 하나가 일어날 때마다 1씩 오른다.
 *
 *   ① 하드웨어 리셋 (첫 부팅 · 되읽기 불일치로 인한 재초기화)
 *   ② 주기적 전면 다시 그리기 (`lcd.redraw_ms`)
 *
 * 🔴 화면 내용을 들고 있는 쪽(mk_screen)이 "내가 그린 것이 아직 패널에
 *    남아 있나" 를 판단하는 근거다. `mk_lcd_ready()` 로는 못 한다 —
 *    칸 하나를 그리는 동안에도 거짓이 되므로, 그것으로 판단하면 한 칸을
 *    그릴 때마다 전부 다시 그리는 무한 반복이 된다(실제로 그렇게 짰다가
 *    이 함수를 만들었다).
 *
 * 🔴 ②를 여기에 묶은 이유: mk_lcd 가 바탕을 다시 칠하고 mk_screen 이 칸을
 *    다시 그려야 **전면** 갱신이 된다. 칸만 다시 그리면 칸 바깥에 밀려
 *    찍힌 화소가 그대로 남는다 — 그것이 사용자가 본 "픽셀이 다 깨진다" 의
 *    모습이다. 세대 하나로 두면 mk_screen 은 원인을 몰라도 되고, 원인별
 *    횟수는 mk_lcd_stat() 이 따로 답한다. */
uint32_t mk_lcd_epoch(const MkLcd *l);

/* 회복 계수기를 담아 준다 (`$STAT` 의 `lcd` 객체). */
void mk_lcd_stat(const MkLcd *l, MkLcdStat *out);

/* 지금 새 의뢰를 받을 수 있는가 (준비됐고, 그리는 중이 아니다). */
int  mk_lcd_idle(const MkLcd *l);

/* 직사각형 하나를 그려 달라고 맡긴다.
 *
 * 받아들이면 1, 지금 못 받으면 0 — 0 이면 **다음 바퀴에 다시 부른다**.
 * 실패를 무시하면 그 칸은 영영 안 그려진다.
 *
 * 🔴 받아들여도 이 함수 안에서 그리지 않는다. 다음 tick 부터 한 바퀴에
 *    한 행씩 나간다. */
int  mk_lcd_paint(MkLcd *l, unsigned x, unsigned y, unsigned w, unsigned h,
                  MkLcdRowFill fill, void *ctx);

/* 8비트 RGB 를 18bpp 3바이트로. p.122 Figure 107 — 상위 6비트만 쓴다. */
void mk_lcd_pixel(uint8_t r, uint8_t g, uint8_t b, uint8_t out[3]);

#endif /* MK_LCD_H */
