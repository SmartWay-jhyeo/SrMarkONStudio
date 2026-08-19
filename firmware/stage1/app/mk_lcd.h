/* ILI9488 LCD (J25) — HAL 비의존.
 *
 * 이 층이 아는 것은 **명령 순서와 대기시간**뿐이다. SPI·GPIO·DMA 는
 * bsp/mk_lcd_io.c 가 한다 (mk_ads1256 / mk_ads_io 와 같은 구조).
 * 그래서 초기화 순서를 보드 없이 호스트에서 시험할 수 있다.
 *
 * ── 결선 [넷리스트 확인 2026-08-19,
 *      docs/superpowers/specs/2026-08-19-lcd-hardware-facts.md] ─────────
 *
 *      PB12  LCD_CS    (J25.3)   GPIO 출력 — SPI2_NSS 를 쓰지 않는다
 *      PB13  SCK       (J25.7)   SPI2_SCK  = AF5, 직렬 22Ω(R91)
 *      PB15  MOSI      (J25.6)   SPI2_MOSI = AF5, 직렬 22Ω(R92)
 *      PB14  MISO      (J25.9)   SPI2_MISO = AF5 — 1단계는 안 쓴다
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
 * ── 왜 상태기계인가 ──────────────────────────────────────────────────
 *
 * 🔴 전면 갱신이 320 x 480 x 3 = 460,800 바이트다. SPI2 16 MHz 에서 약
 *    230 ms — 동기로 보내면 그 사이 ADS1256 표본과 텔레메트리가 통째로
 *    밀린다. 그래서 **완료를 기다리지 않고 빠져나온다**: 한 바퀴에 걸음
 *    하나만 나아가고, DMA 완료는 mk_lcd_on_tx_done() 이 알려 준다
 *    (app/mk_i2c.c 의 포트 상태기계와 같은 결).
 *
 * 🔴 프레임버퍼를 들지 않는다. 한 색으로 채우는 것이 1단계의 전부라,
 *    한 행(960바이트)만 만들어 480번 보낸다. DMA 한 번이 0.5 ms 라
 *    수집이 밀리지 않고, 나중에 부분 갱신을 붙일 때 이 구조가 그대로
 *    "직사각형 하나를 행 단위로 보낸다" 가 된다.
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

/* 한 행의 바이트 수 — DMA 한 번의 크기이기도 하다. */
#define MK_LCD_ROW_BYTES    (MK_LCD_WIDTH * MK_LCD_BYTES_PER_PIXEL)

/* 명령 파라미터의 최대 개수. 지금 가장 긴 것은 CASET·PASET 의 4바이트다. */
#define MK_LCD_ARGS_MAX     4

/* bsp 가 대 줘야 하는 명령 버퍼의 크기. 명령 1바이트와 파라미터를 따로
 * 보내므로 파라미터 최대치면 충분하다. */
#define MK_LCD_CMD_BUF_BYTES  (MK_LCD_ARGS_MAX + 1u)

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
    void *ctx;
} MkLcdIo;

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
    MK_LCD_INIT,           /* 초기화 표를 걷는다 (RAMWR 까지) */
    MK_LCD_FILL,           /* 행을 MK_LCD_HEIGHT 번 보낸다 */
    MK_LCD_POST,           /* 그린 뒤의 표 (DISPON) */
    MK_LCD_DONE,           /* 다 그렸다 — 더 보내지 않는다 */
    MK_LCD_UNUSABLE        /* 버퍼가 모자란다. 시작조차 하지 않는다 */
} MkLcdPhase;

typedef struct MkLcd {
    MkLcdIo    io;
    uint8_t   *cmd_buf;
    size_t     cmd_cap;
    uint8_t   *row_buf;
    size_t     row_cap;

    MkLcdPhase phase;
    unsigned   step;        /* 표에서 지금 줄 */
    unsigned   sub;         /* 0 = 명령 바이트, 1 = 파라미터, 2 = 대기 */
    unsigned   row;         /* FILL 에서 지금 행 */
    int64_t    mark_ms;     /* 마지막 전이·전송 시각 */

    /* 🔴 volatile 이다 — mk_lcd_on_tx_done() 은 DMA 완료 인터럽트에서
     *    불리고 mk_lcd_tick() 은 슈퍼루프에서 읽는다. */
    volatile int busy;

    int        row_ready;   /* 행 버퍼를 색으로 채웠는가 */
    int        cs_low;
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

/* 다 그렸는가. */
int  mk_lcd_ready(const MkLcd *l);

/* 8비트 RGB 를 18bpp 3바이트로. p.122 Figure 107 — 상위 6비트만 쓴다. */
void mk_lcd_pixel(uint8_t r, uint8_t g, uint8_t b, uint8_t out[3]);

#endif /* MK_LCD_H */
