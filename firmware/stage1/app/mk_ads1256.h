/* ADS1256 수집 상태머신 — HAL 비의존.
 *
 * 🔴 참고 구현(h723_sensor_read/ads1256.c)을 이식하지 않는다. 그쪽은 채널
 *    하나를 읽는 동안 CPU 를 통째로 잡는다:
 *
 *      - SPI 를 GPIO 로 비트뱅잉한다. `sclk_half()` 가 `delay_us(2)` 라
 *        비트 하나에 4 µs 씩 CPU 가 논다. 채널당 ~304 µs.
 *      - `wait_drdy()` 가 DRDY 핀을 `while` 로 돈다. 이것이 채널당
 *        16.84 ms 다 (ADS1256.pdf p.20 Table 13, DRATE 60 SPS).
 *
 *    7채널 한 바퀴에 약 118 ms 를 CPU 가 서 있게 된다. 480 MHz Cortex-M7
 *    한테 5,600만 사이클이고, 그동안 UART 도 GNSS NMEA 도 큐 배출도 멈춘다.
 *
 *    그런데 넷리스트를 보면 이 핀들은 하드웨어 SPI4 에 배선돼 있다
 *    (`SPI4_SCK` = PE12, `SPI4_MISO` = PE13, `SPI4_MOSI` = PE14).
 *    DRDY(PE15)는 EXTI 로 받을 수 있다.
 *
 * 그래서 이 층은 **기다리지 않는다.** 사건이 오면 다음 한 걸음만 딛고 돌아온다:
 *
 *      mk_ads_tick(now)        다음 채널을 시작할 때가 됐는지 본다
 *      mk_ads_on_spi_done()    SPI 전송이 끝났다 (DMA 완료 인터럽트)
 *      mk_ads_on_drdy(now)     변환이 끝났다 (DRDY 하강 엣지 EXTI)
 *
 *    한 걸음의 결과로 필요한 동작은 MkAdsIo 의 함수 포인터로 나간다.
 *    HAL 은 그 뒤에만 있다 — 그래서 이 파일이 보드 없이 시험된다.
 *
 * 🔴 118 ms 라는 **주기 자체는 이 층이 줄이지 못한다.** 그것은 ΔΣ 필터의
 *    정착시간이고 데이터시트 값이다. 이 층이 하는 일은 그 시간 동안 CPU 를
 *    풀어 주는 것이다. 주기를 줄이려면 DRATE 를 올려야 하고, 그 계산은
 *    호스트의 용량 검사(tools/simulator/capacity.py)가 한다.
 */
#ifndef MK_ADS1256_H
#define MK_ADS1256_H

#include <stddef.h>
#include <stdint.h>

#include "mk_queue.h"

#define MK_ADS_CHANNELS   7

/* ADS1256 명령 (데이터시트 Table 24). */
#define MK_ADS_CMD_WAKEUP   0x00u
#define MK_ADS_CMD_RDATA    0x01u
#define MK_ADS_CMD_SYNC     0xFCu
#define MK_ADS_CMD_WREG     0x50u
#define MK_ADS_REG_MUX      0x01u

/* 이 층이 바깥에 시키는 일. 전부 **즉시 돌아와야 한다** — 여기서 기다리면
 * 비차단으로 만든 의미가 없다.
 *
 *   cs        칩 선택. low != 0 이면 내린다(선택).
 *   transfer  n 바이트를 주고받기 시작한다. 끝나면 mk_ads_on_spi_done 을
 *             부르는 것은 **부른 쪽의 책임**이다(보통 DMA 완료 인터럽트).
 *             rx 가 NULL 이면 받은 것을 버려도 된다.
 *
 * 🔴 tx·rx 버퍼는 DMA 가 닿는 곳에 있어야 한다. DTCM 에 두면 전송이
 *    조용히 실패한다 — bsp/mk_dma_mem.h 의 MK_DMA_BUF 를 쓴다. */
typedef struct {
    void (*cs)(void *ctx, int low);
    void (*transfer)(void *ctx, const uint8_t *tx, uint8_t *rx, size_t n);
    void *ctx;
} MkAdsIo;

typedef enum {
    MK_ADS_IDLE = 0,    /* 다음 채널을 시작할 때를 기다린다 */
    MK_ADS_SETUP,       /* WREG MUX + SYNC + WAKEUP 전송 중 */
    MK_ADS_CONVERTING,  /* DRDY 를 기다린다 (여기가 t18) */
    MK_ADS_READING,     /* RDATA + 3바이트 수신 중 */
} MkAdsState;

typedef struct {
    uint8_t  enabled;       /* 이 채널을 도는가 */
    uint16_t period_ms;     /* 수집 주기 */
    int64_t  next_due_ms;   /* 다음에 읽을 시각 */
    MkQueue  q;
    /* 🔴 채널별로 센다. 하나가 계속 시간을 넘겨도 다른 채널은 계속 돈다
     *    — 설계 원칙의 채널 장애 격리(CLAUDE.md §3). */
    uint32_t timeouts;
} MkAdsChannel;

/* 🔴 이름 있는 구조체다. mk_hostlink 가 `struct MkAds *` 로 전방 선언해서
 *    받기 때문이다 — 익명 typedef 로 두면 그쪽의 `struct MkAds` 가 전혀
 *    다른 타입이 되고, 컴파일러가 포인터 형식 불일치를 경고한다. */
typedef struct MkAds {
    MkAdsIo      io;
    MkAdsState   state;
    int          cur;               /* 지금 읽고 있는 채널, 없으면 -1 */
    int64_t      started_ms;        /* 지금 단계를 시작한 시각 */
    int64_t      drdy_timeout_ms;   /* 이만큼 지나도 DRDY 가 없으면 포기 */
    MkAdsChannel ch[MK_ADS_CHANNELS];

    /* 전송 버퍼. 바깥에서 DMA 가 닿는 곳에 잡아 넘긴다. */
    uint8_t     *tx;
    uint8_t     *rx;
    size_t       buf_cap;

    /* 🔴 SETUP 은 명령 세 개를 잇달아 보낸다. 몇 번째를 보냈는지 들고
     *    있어야 완료 인터럽트가 어디로 이어질지 안다. */
    uint8_t      setup_step;
} MkAds;

/* 초기화. buf 는 최소 4바이트, DMA 가 닿는 곳이어야 한다. */
void mk_ads_init(MkAds *a, const MkAdsIo *io,
                 uint8_t *tx, uint8_t *rx, size_t buf_cap,
                 int64_t drdy_timeout_ms);

/* 채널 하나의 큐 저장소를 붙인다. 붙이지 않은 채널은 표본을 버리며 센다. */
void mk_ads_attach_queue(MkAds *a, int ch, MkSample *buf, uint16_t cap);

/* 설정을 반영한다. period_ms 가 0 이면 채널을 끈 것으로 본다. */
void mk_ads_configure(MkAds *a, int ch, int enabled, uint16_t period_ms,
                      int64_t now_ms);

/* 주기 처리. 쉬고 있고 다음 채널이 때가 됐으면 한 바퀴를 시작한다.
 * CONVERTING 중이면 타임아웃도 여기서 본다. */
void mk_ads_tick(MkAds *a, int64_t now_ms);

/* SPI 전송 하나가 끝났다. */
void mk_ads_on_spi_done(MkAds *a, int64_t now_ms);

/* DRDY 하강 엣지.
 *
 * 🔴 `now_ms` 가 **획득 시각**이 된다(설계 원칙 2). 인터럽트 안에서 읽은
 *    시각을 그대로 넘겨야 하고, 나중에 큐에서 꺼낼 때의 시각으로 덮어쓰면
 *    안 된다. 그것이 이 층이 존재하는 이유의 절반이다. */
void mk_ads_on_drdy(MkAds *a, int64_t now_ms);

/* 진단. */
MkAdsState mk_ads_state(const MkAds *a);
int        mk_ads_current_channel(const MkAds *a);
MkQueue   *mk_ads_queue(MkAds *a, int ch);
int        mk_ads_channel_enabled(const MkAds *a, int ch);
uint32_t   mk_ads_timeouts(const MkAds *a, int ch);

#endif /* MK_ADS1256_H */
