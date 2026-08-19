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
#define MK_ADS_REG_STATUS   0x00u
#define MK_ADS_REG_MUX      0x01u
#define MK_ADS_REG_ADCON    0x02u
#define MK_ADS_REG_DRATE    0x03u

/* STATUS (데이터시트 p.30): bit3 ORDER=0(MSB 먼저) · bit2 ACAL=1 · bit1 BUFEN=0
 *
 * 🔴 ACAL 을 켠다. "self-calibration begins at the completion of the WREG
 *    command that changes the PGA ... DR ... or BUFEN values" — 설정을 바꾼
 *    뒤 손으로 교정 명령을 넣는 것을 잊어도 칩이 알아서 맞춘다.
 *
 * 🔴 BUFEN 은 끈다. 이 보드의 입력원은 120Ω 션트라 버퍼가 필요 없고,
 *    버퍼를 켜면 입력 전압 범위가 AVDD-2V 로 좁아진다. */
#define MK_ADS_STATUS_INIT  0x04u

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
    /* 🔴 SCLK 을 **쉬게** 한다. 보낼 것이 없는데도 필요한 시간이 있다 —
     *    아래 t6·t11 을 볼 것. 바이트를 더 보내는 것으로는 대신할 수 없다.
     *    그것도 SCLK 이기 때문이다.
     *
     *    NULL 이어도 된다. 그때는 쉬지 않는다 — 옛 호출부가 죽지 않게. */
    void (*delay_us)(void *ctx, uint32_t us);
    void *ctx;
} MkAdsIo;

/* ADS1256 데이터시트 (SBAS288K) p.6 "TIMING CHARACTERISTICS FOR FIGURE 1".
 * 이 보드의 크리스털은 7.68 MHz 이므로 tCLKIN = 130.2 ns.
 *
 *   t6  = 50 tCLKIN = 6.51 us
 *         "Delay from last SCLK edge for DIN to first SCLK rising edge for
 *          DOUT: RDATA, RDATAC, RREG Commands"
 *   t11 = 24 tCLKIN = 3.13 us   (SYNC 뒤 다음 명령까지)
 *
 * 🔴 올림해서 쓴다. 모자라면 조용히 틀린 값이 나오고, 남는 몇 마이크로초는
 *    16.8 ms 짜리 변환에서 아무 의미가 없다. */
#define MK_ADS_T6_US        7u
#define MK_ADS_T11_SYNC_US  4u

typedef enum {
    MK_ADS_IDLE = 0,    /* 다음 채널을 시작할 때를 기다린다 */
    MK_ADS_SETUP,       /* WREG MUX + SYNC + WAKEUP 전송 중 */
    MK_ADS_CONVERTING,  /* DRDY 를 기다린다 (여기가 t18) */
    MK_ADS_RDATA_SENT,  /* RDATA 를 보냈다. t6 를 쉰 뒤 데이터를 읽는다 */
    MK_ADS_READING,     /* 데이터 3바이트 수신 중 */
} MkAdsState;

typedef struct {
    uint8_t  enabled;       /* 이 채널을 도는가 */
    uint16_t period_ms;     /* 수집 주기 */
    int64_t  next_due_ms;   /* 다음에 읽을 시각 */
    MkQueue  q;
    /* 🔴 채널별로 센다. 하나가 계속 시간을 넘겨도 다른 채널은 계속 돈다
     *    — 설계 원칙의 채널 장애 격리(CLAUDE.md §3). */
    uint32_t timeouts;

    /* 🔴 마지막 표본 — 수집과 송신을 떼어 놓는 자리다(사용자 설계,
     *    2026-08-19). "변수 a 에 계속 넣고, 송신은 그 a 를 읽는다."
     *
     *    큐(q)와 별개다. 큐는 $STAT 의 깊이·drops 진단용으로 그대로
     *    남는다 — 여기서 지우지 않는다. mk_telem 은 이제 큐를 비우지
     *    않고 이 자리만 읽는다. mk_ads_on_spi_done() 의 READING 완료
     *    지점(큐에 넣는 바로 그 자리)이 여기도 함께 덮어쓴다 — 그래야
     *    두 자리가 어긋나지 않는다.
     *
     *    🔴 mk_ads_configure() 로 채널을 꺼도 has_last/last 는 안 비운다 —
     *    mk_i2c.c 는 비운다. 의도적 비대칭이다(근거는 mk_i2c.c 의 disable
     *    분기 주석). 여기서 안 비워도 되는 이유는 mk_telem.c 가 배출 전에
     *    mk_ads_channel_enabled() 를 따로 확인하기 때문이고, 채널을 다시
     *    켜도 슬롯의 의미(그 채널의 전압)가 바뀌지 않기 때문이다. */
    MkSample last;
    uint8_t  has_last;      /* 한 번이라도 표본을 받았는가 */
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

    /* 칩 레지스터로 나갈 값. 바뀔 때만 쓴다 — mk_ads_set_chip 참조. */
    uint8_t      adcon;
    uint8_t      drate;
    uint8_t      chip_dirty;
} MkAds;

/* 초기화. buf 는 최소 4바이트, DMA 가 닿는 곳이어야 한다. */
void mk_ads_init(MkAds *a, const MkAdsIo *io,
                 uint8_t *tx, uint8_t *rx, size_t buf_cap,
                 int64_t drdy_timeout_ms);

/* 채널 하나의 큐 저장소를 붙인다. 붙이지 않은 채널은 표본을 버리며 센다. */
void mk_ads_attach_queue(MkAds *a, int ch, MkSample *buf, uint16_t cap);

/* 설정을 반영한다. period_ms 가 0 이면 채널을 끈 것으로 본다. */
/* 칩 전체에 걸리는 설정 — 증폭률과 데이터율.
 *
 * 🔴 채널별이 아니라 칩 하나에 하나다. ADS1256 은 PGA·DRATE 를 채널마다
 *    따로 두지 못한다.
 *
 * pga_gain 은 배율(1·2·4·8·16·32·64), drate_sps 는 초당 표본수를 그대로
 * 넘긴다 — 카탈로그에 있는 값 그대로다. 레지스터 부호로 바꾸는 것은 여기서
 * 한다. 모르는 값이 오면 조용히 안전한 쪽(1배 · 60 SPS)으로 간다.
 *
 * 값이 바뀌었을 때만 실제로 칩에 쓴다. 매번 부르면 된다. */
void mk_ads_set_chip(MkAds *a, uint32_t pga_gain, uint32_t drate_sps);

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

/* 채널의 마지막 표본. 한 번이라도 받았으면 1(out 을 채운다), 아니면 0 —
 * mk_telem 이 "아직 값이 없는 채널은 보내지 않는다"를 판단하는 근거다. */
int mk_ads_last(const MkAds *a, int ch, MkSample *out);

#endif /* MK_ADS1256_H */
