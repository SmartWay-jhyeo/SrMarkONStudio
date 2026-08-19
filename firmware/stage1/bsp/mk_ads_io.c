#include "mk_ads_io.h"

#include "mk_clock.h"
#include "mk_dma_mem.h"
#include "mk_time.h"

#include "stm32h7xx_hal.h"

/* 🔴 SPI4 커널 클럭(APB2, D2CCIP1R.SPI45SEL 리셋값 000)을 여기서 나눈다.
 *
 *    SCLK 은 fCLKIN/4 = 1.92 MHz 를 넘을 수 없다(ADS1256.pdf). 분주비를
 *    HAL 매크로 이름으로만 적어 두면 클럭이 바뀌었을 때 결과 주파수가
 *    조용히 올라가므로, **숫자로 이름을 붙이고 상한을 컴파일 시 본다.**
 *
 *    500 kHz 는 한 바이트에 16 us 다. app/mk_ads1256.c 의 t6 대기 계산이
 *    이 속도를 전제로 한다 — 바꾸면 그쪽도 함께 본다. */
#define MK_ADS_SPI_DIV   128u
#define MK_ADS_SPI_HZ    (MK_SPI4_KERNEL_HZ / MK_ADS_SPI_DIV)
_Static_assert(MK_ADS_SPI_HZ <= 1920000u,
               "SCLK exceeds fCLKIN/4 = 1.92 MHz - ADS1256.pdf");
_Static_assert(MK_SPI_DIV_FROM_MBR(
                   (SPI_BAUDRATEPRESCALER_128 >> SPI_CFG1_MBR_Pos) & 7u)
               == MK_ADS_SPI_DIV,
               "BaudRatePrescaler below does not match MK_ADS_SPI_DIV");

/* 🔴 수집을 다시 시작하는 인터럽트의 주기 (Hz).
 *
 *    한 바퀴가 끝나면 app/mk_ads1256.c 의 finish() 가 **밀린 채널로 곧바로
 *    이어 간다.** 그래도 타이머가 필요한 자리가 하나 남는다 — 일곱 채널을
 *    다 돌고 다음 예정까지 쉬는 구간이다. 거기서 다시 깨우는 것을
 *    슈퍼루프에 맡기면 채널당 10 ms 가 무너진다:
 *
 *      - mk_i2c_tick() 의 HAL 블로킹이 한 바퀴에 최악 60 ms (mk_i2c_io.c)
 *      - mk_telem_tick() 의 HAL_UART_Transmit 도 블로킹 — 163 B 한 줄이
 *        921600 baud 에서 1.77 ms, 한 바퀴에 여러 줄
 *
 *      7채널 × 10 ms 면 채널 하나에 1.43 ms 다. 슈퍼루프가 시작 신호를
 *      쥐고 있으면 그 예산은 지킬 수 없고, 못 뜬 표본은 finish() 의
 *      따라잡기 포기 때문에 큐의 drops 에도 안 잡힌 채 사라진다.
 *
 *    1 kHz 인 이유: 표본 예정(MkAdsChannel.next_due_ms)의 단위가 밀리초라
 *    그보다 성기게 깨우면 예정이 그 간격만큼 반올림된다. 그보다 촘촘히
 *    깨워도 얻는 것이 없다. */
#define MK_ADS_TICK_HZ   1000u

/* 🔴 TIM7 을 고른 이유: 남아 있는 기본 타이머다. TIM3 은 WS2812(PWM+DMA),
 *    TIM8 은 PPS 입력 캡처가 이미 쓴다. 기본 타이머라 출력핀이 없어
 *    이 보드의 배선과 충돌할 여지도 없다. */
#define MK_ADS_TICK_TIM  TIM7

/* APB1 타이머 클럭에서 1 MHz 를 만든 뒤 그 안에서 다시 나눈다 — 숫자를
 * 손으로 적지 않는다(bsp/mk_clock.h 머리말). */
#define MK_ADS_TICK_PSC  MK_TIM_PSC_1US_APB1
#define MK_ADS_TICK_ARR  (1000000u / MK_ADS_TICK_HZ - 1u)
_Static_assert(1000000u % MK_ADS_TICK_HZ == 0u,
               "tick period is not an integer number of microseconds");

/* 🔴 수집에 관여하는 인터럽트는 **전부 같은 우선순위**여야 한다.
 *
 *    DRDY(EXTI15) · SPI4 EOT · DMA1 완료 · 그리고 위의 tick 타이머가 같은
 *    `MkAds` 구조체를 건드린다. Cortex-M 은 **같은 우선순위의 인터럽트가
 *    서로를 선점하지 못한다** — 늦게 온 것은 앞의 핸들러가 끝날 때까지
 *    대기(pending)만 한다. 그래서 넷을 같은 값으로 두면 임계구역 없이
 *    상호배제가 된다.
 *
 *    하나만 값이 다르면 그것이 나머지를 상태 전이 도중에 끊고 들어와
 *    전송이 겹친다 — 재현이 거의 불가능한 결함이 되므로, 값을 하나로
 *    묶고 host/tests/test_firmware_acquisition.py 가 넷이 같은지 본다.
 *
 *    5 가 아니라 6 인 이유는 그대로다 — UART(5)보다 낮게 둔다. 획득 시각이
 *    몇 µs 늦는 것보다 명령 링크가 끊기는 편이 나쁘다. */
#define MK_ADS_IRQ_PRIO  6u

#define ADS_PORT     GPIOE
#define PIN_SYNC     GPIO_PIN_9
#define PIN_RST      GPIO_PIN_10
#define PIN_CS       GPIO_PIN_11
#define PIN_SCK      GPIO_PIN_12
#define PIN_MISO     GPIO_PIN_13
#define PIN_MOSI     GPIO_PIN_14
#define PIN_DRDY     GPIO_PIN_15

static SPI_HandleTypeDef s_spi;
/* 🔴 이름이 `hdma_` 로 시작한다. ST 관례이기도 하고, DMA **버퍼**가 아니라
 *    CPU 가 읽는 **핸들 구조체**라는 표시이기도 하다. DTCM 에 있어도 아무
 *    문제가 없다 — DMA 컨트롤러는 이 구조체를 읽지 않는다.
 *
 *    처음에 `s_dma_rx` 로 두었더니 check_dma_placement.py 가 DMA 버퍼로
 *    오인해 빌드를 세웠다. Codex 감사가 지적한 "이름 규칙 기반이라
 *    불완전하다" 가 양쪽으로 나타난 셈이다 — 놓치기도 하고 헛짚기도 한다. */
static DMA_HandleTypeDef s_hdma_rx;
static DMA_HandleTypeDef s_hdma_tx;
static TIM_HandleTypeDef s_tick;
static MkAds            *s_ads;

/* 🔴 DMA 가 닿는 곳에 둔다. 그냥 static 으로 두면 DTCM(0x2000_0000)에
 *    잡히고, DMA1·DMA2 는 거기 닿지 못한다 — 전송이 조용히 실패한다
 *    (RM0468 p.140 / p.106, bsp/mk_dma_mem.h).
 *
 *    잊어도 아무 일이 일어나지 않으므로, 링크 뒤에 tools/check_dma_placement.py
 *    가 실제 주소를 확인하고 D2 밖이면 빌드를 세운다. */
static uint8_t MK_DMA_BUF s_tx_buf[8];
static uint8_t MK_DMA_BUF s_rx_buf[8];

/* ---- 상태머신이 시키는 일 ------------------------------------------------ */

static void io_cs(void *ctx, int low)
{
    (void)ctx;
    HAL_GPIO_WritePin(ADS_PORT, PIN_CS, low ? GPIO_PIN_RESET : GPIO_PIN_SET);
}

static void io_transfer(void *ctx, const uint8_t *tx, uint8_t *rx, size_t n)
{
    (void)ctx;
    (void)tx;   /* 상태머신이 s_tx_buf 를 직접 채운다 — 같은 버퍼다 */
    (void)rx;

    /* 🔴 즉시 돌아온다. 완료는 DMA 인터럽트가 알려 준다.
     *    여기서 HAL_SPI_TransmitReceive(블로킹)를 부르면 비차단으로 만든
     *    의미가 통째로 사라진다. */
    HAL_SPI_TransmitReceive_DMA(&s_spi, s_tx_buf, s_rx_buf, (uint16_t)n);
}

/* ---- 초기화 -------------------------------------------------------------- */

static void gpio_init(void)
{
    __HAL_RCC_GPIOE_CLK_ENABLE();

    GPIO_InitTypeDef g = {0};

    /* SPI4 신호 3선. AF5 다. */
    g.Pin       = PIN_SCK | PIN_MISO | PIN_MOSI;
    g.Mode      = GPIO_MODE_AF_PP;
    g.Pull      = GPIO_NOPULL;
    g.Speed     = GPIO_SPEED_FREQ_VERY_HIGH;
    g.Alternate = GPIO_AF5_SPI4;
    HAL_GPIO_Init(ADS_PORT, &g);

    /* CS·SYNC·RESET 은 손으로 쥔다. 이유는 헤더에 있다. */
    g.Pin       = PIN_CS | PIN_SYNC | PIN_RST;
    g.Mode      = GPIO_MODE_OUTPUT_PP;
    g.Pull      = GPIO_NOPULL;
    g.Speed     = GPIO_SPEED_FREQ_HIGH;
    g.Alternate = 0;
    HAL_GPIO_Init(ADS_PORT, &g);

    /* 셋 다 High 로 둔다 — CS 는 비선택, SYNC·RESET 은 동작 상태. */
    HAL_GPIO_WritePin(ADS_PORT, PIN_CS | PIN_SYNC | PIN_RST, GPIO_PIN_SET);

    /* 🔴 DRDY 는 **하강 엣지**다. 변환이 끝나면 Low 로 떨어진다
     *    (ADS1256.pdf p.20, "DRDY goes low, indicating that data is
     *    available"). 상승으로 잡으면 이미 읽은 뒤에 깨어난다. */
    g.Pin  = PIN_DRDY;
    g.Mode = GPIO_MODE_IT_FALLING;
    g.Pull = GPIO_NOPULL;
    HAL_GPIO_Init(ADS_PORT, &g);

    /* 🔴 UART(우선순위 5)보다 낮게 둔다. 획득 시각은 이 인터럽트 안에서
     *    찍히지만, 몇 µs 늦는 것보다 명령 링크가 끊기는 편이 나쁘다.
     *    16.84 ms 짜리 변환에서 µs 단위 지터는 무시할 수 있다. */
    HAL_NVIC_SetPriority(EXTI15_10_IRQn, MK_ADS_IRQ_PRIO, 0);
    HAL_NVIC_EnableIRQ(EXTI15_10_IRQn);
}

static void spi_init(void)
{
    __HAL_RCC_SPI4_CLK_ENABLE();
    __HAL_RCC_DMA1_CLK_ENABLE();

    /* 🔴 D2 도메인 SRAM 의 클럭을 켠다.
     *
     *    DMA 버퍼가 0x3000_0000(SRAM1)에 있는데, 리셋 직후 AHB2ENR 의
     *    SRAM1EN·SRAM2EN 은 0 이다. 실기기에서 읽어 확인했다
     *    (AHB2ENR = 0x00000000, 2026-08-14).
     *
     *    DTCM 을 피해 D2 로 옮겨 놓고 클럭을 안 켜면 결국 같은 자리로
     *    돌아온다 — 전송이 조용히 안 되는 것. 링커·매크로·빌드 검사까지
     *    다 갖춰 놓고 이 한 줄이 없어서 안 도는 것이 가능하다. */
    __HAL_RCC_D2SRAM1_CLK_ENABLE();
    __HAL_RCC_D2SRAM2_CLK_ENABLE();

    s_spi.Instance               = SPI4;
    s_spi.Init.Mode              = SPI_MODE_MASTER;
    s_spi.Init.Direction         = SPI_DIRECTION_2LINES;
    s_spi.Init.DataSize          = SPI_DATASIZE_8BIT;
    /* 🔴 SPI 모드 1 (CPOL=0, CPHA=1). ADS1256 은 SCLK 하강에서 DIN 을
     *    래치하고 상승에서 DOUT 을 낸다. 참고 구현의 비트뱅잉도 같은
     *    모드였다 — 모드가 틀리면 한 비트씩 밀린 값이 나온다. */
    s_spi.Init.CLKPolarity       = SPI_POLARITY_LOW;
    s_spi.Init.CLKPhase          = SPI_PHASE_2EDGE;
    s_spi.Init.NSS               = SPI_NSS_SOFT;
    /* 🔴 커널 클럭 / MK_ADS_SPI_DIV = 500 kHz. 상한(1.92 MHz)은 파일 위
     *    _Static_assert 가 본다. 실기기에서 SPI4_CFG1 = 0x60070007 (MBR=6,
     *    ÷128)로 확인했다 [실증 2026-08-19]. */
    s_spi.Init.BaudRatePrescaler = SPI_BAUDRATEPRESCALER_128;
    s_spi.Init.FirstBit          = SPI_FIRSTBIT_MSB;
    s_spi.Init.TIMode            = SPI_TIMODE_DISABLE;
    s_spi.Init.CRCCalculation    = SPI_CRCCALCULATION_DISABLE;
    s_spi.Init.NSSPMode          = SPI_NSS_PULSE_DISABLE;
    HAL_SPI_Init(&s_spi);

    s_hdma_rx.Instance                 = DMA1_Stream0;
    s_hdma_rx.Init.Request             = DMA_REQUEST_SPI4_RX;
    s_hdma_rx.Init.Direction           = DMA_PERIPH_TO_MEMORY;
    s_hdma_rx.Init.PeriphInc           = DMA_PINC_DISABLE;
    s_hdma_rx.Init.MemInc              = DMA_MINC_ENABLE;
    s_hdma_rx.Init.PeriphDataAlignment = DMA_PDATAALIGN_BYTE;
    s_hdma_rx.Init.MemDataAlignment    = DMA_MDATAALIGN_BYTE;
    s_hdma_rx.Init.Mode                = DMA_NORMAL;
    s_hdma_rx.Init.Priority            = DMA_PRIORITY_HIGH;
    s_hdma_rx.Init.FIFOMode            = DMA_FIFOMODE_DISABLE;
    HAL_DMA_Init(&s_hdma_rx);
    __HAL_LINKDMA(&s_spi, hdmarx, s_hdma_rx);

    s_hdma_tx.Instance                 = DMA1_Stream1;
    s_hdma_tx.Init.Request             = DMA_REQUEST_SPI4_TX;
    s_hdma_tx.Init.Direction           = DMA_MEMORY_TO_PERIPH;
    s_hdma_tx.Init.PeriphInc           = DMA_PINC_DISABLE;
    s_hdma_tx.Init.MemInc              = DMA_MINC_ENABLE;
    s_hdma_tx.Init.PeriphDataAlignment = DMA_PDATAALIGN_BYTE;
    s_hdma_tx.Init.MemDataAlignment    = DMA_MDATAALIGN_BYTE;
    s_hdma_tx.Init.Mode                = DMA_NORMAL;
    s_hdma_tx.Init.Priority            = DMA_PRIORITY_HIGH;
    s_hdma_tx.Init.FIFOMode            = DMA_FIFOMODE_DISABLE;
    HAL_DMA_Init(&s_hdma_tx);
    __HAL_LINKDMA(&s_spi, hdmatx, s_hdma_tx);

    HAL_NVIC_SetPriority(DMA1_Stream0_IRQn, MK_ADS_IRQ_PRIO, 0);
    HAL_NVIC_EnableIRQ(DMA1_Stream0_IRQn);
    HAL_NVIC_SetPriority(DMA1_Stream1_IRQn, MK_ADS_IRQ_PRIO, 0);
    HAL_NVIC_EnableIRQ(DMA1_Stream1_IRQn);

    /* 🔴 SPI4 자신의 인터럽트도 켜야 한다. DMA 인터럽트만으로는 부족하다.
     *
     *    STM32H7 의 SPI 는 전송 종료를 **EOT 플래그**로 알린다. HAL 의
     *    DMA 전송은 그 EOT 인터럽트에서 TxRxCpltCallback 을 부르므로,
     *    SPI4_IRQn 을 안 켜면 DMA 는 다 옮겨 놓고도 완료 통보가 오지 않는다.
     *
     *    실기기에서 이 상태를 봤다 (2026-08-14): SPI4 SR=0x101a 로 EOT 가
     *    서 있고, DMA 스트림은 EN=0·NDTR=0 으로 이미 끝나 있는데,
     *    s_spi.State 는 5(BUSY_TX_RX) 그대로였다. 상태머신은 SETUP 에서
     *    영원히 기다리다 채널마다 타임아웃만 쌓았다. */
    HAL_NVIC_SetPriority(SPI4_IRQn, MK_ADS_IRQ_PRIO, 0);
    HAL_NVIC_EnableIRQ(SPI4_IRQn);
}

/* 마이크로초 바쁜 대기.
 *
 * 🔴 SysTick 이나 타이머를 쓰지 않는다. 여기는 SPI 완료 인터럽트 안이고,
 *    쉬어야 하는 시간이 7 us 다 — SPI 한 바이트(500 kHz 에서 16 us)보다
 *    짧다. 타이머로 갔다 오면 그 왕복이 더 길고, 상태머신을 인터럽트
 *    바깥으로 끌어내는 큰 수술이 된다.
 *
 * 🔴 DWT 사이클 카운터를 쓰지 않고 루프로 센다. DWT 는 디버거가 붙어 있지
 *    않으면 켜져 있지 않을 수 있고(TRCENA), 그 경우 조용히 0 을 돌려주며
 *    지연이 통째로 사라진다 — 지금 고치려는 바로 그 증상으로 되돌아간다.
 *
 * 🔴 루프 횟수를 손으로 적지 않는다. 예전에는 `64u / 3u + 1u` 라고 적혀
 *    있었는데, 그 64 는 클럭이지 상수가 아니다 — 클럭을 올리면 대기가
 *    그대로 짧아지고 t6 를 다시 어긴다. bsp/mk_clock.h 에서 파생시킨다. */
static void io_delay_us(void *ctx, uint32_t us)
{
    (void)ctx;
    volatile uint32_t n = us * MK_BUSY_WAIT_LOOPS_PER_US;
    while (n--) {
        __asm volatile ("nop");
    }
}

/* 수집을 다시 시작하는 1 kHz 타이머. 위 MK_ADS_TICK_HZ 주석 참고. */
static void tick_init(void)
{
    __HAL_RCC_TIM7_CLK_ENABLE();

    s_tick.Instance               = MK_ADS_TICK_TIM;
    s_tick.Init.Prescaler         = MK_ADS_TICK_PSC;
    s_tick.Init.CounterMode       = TIM_COUNTERMODE_UP;
    s_tick.Init.Period            = MK_ADS_TICK_ARR;
    s_tick.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
    HAL_TIM_Base_Init(&s_tick);

    HAL_NVIC_SetPriority(TIM7_IRQn, MK_ADS_IRQ_PRIO, 0);
    HAL_NVIC_EnableIRQ(TIM7_IRQn);
    HAL_TIM_Base_Start_IT(&s_tick);
}

void mk_ads_io_init(MkAds *a)
{
    s_ads = a;
    gpio_init();
    spi_init();

    MkAdsIo io = { io_cs, io_transfer, io_delay_us, NULL };
    /* 🔴 DRDY 타임아웃을 500 ms 로 둔다. 가장 느린 DRATE(2.5 SPS)의 정착이
     *    400.18 ms 이므로(ADS1256.pdf p.20 Table 13) 그보다 넉넉해야 한다.
     *    이보다 짧게 잡으면 느린 설정에서 정상 변환을 고장으로 오인한다. */
    mk_ads_init(a, &io, s_tx_buf, s_rx_buf, sizeof s_tx_buf, 500);

    /* 🔴 상태머신을 세운 **뒤에** 깨우기를 켠다. 순서를 뒤집으면 첫
     *    인터럽트가 아직 초기화 안 된 구조체를 밀 수 있다. */
    tick_init();
}

/* ---- 하드웨어 사건 ------------------------------------------------------- */

void mk_ads_io_drdy_isr(void)
{
    if (__HAL_GPIO_EXTI_GET_IT(PIN_DRDY) == RESET) {
        return;
    }
    __HAL_GPIO_EXTI_CLEAR_IT(PIN_DRDY);

    /* 🔴 시각을 **여기서** 읽는다. 설계 원칙 2 (CLAUDE.md §3) — 획득 시각은
     *    STM32 가 확정한다. 슈퍼루프로 미루면 UART·저장·GUI 지연이 전부
     *    타임스탬프에 섞인다. */
    if (s_ads != NULL) {
        mk_ads_on_drdy(s_ads, mk_time_ms());
    }
}

/* 1 kHz 깨우기 (TIM7). 쉬고 있다가 다음 예정이 도착했으면 한 바퀴를
 * 시작한다. 돌고 있는 중이면 DRDY 타임아웃만 본다 — 둘 다 mk_ads_tick()
 * 안에 있다.
 *
 * 🔴 HAL_TIM_IRQHandler 를 쓰지 않고 UIF 를 직접 지운다. HAL 로 가면
 *    HAL_TIM_PeriodElapsedCallback 이라는 **전역 콜백 하나**를 다른 타이머
 *    (WS2812 의 TIM3, PPS 의 TIM8)와 나눠 쓰게 되고, 거기서 인스턴스를
 *    가르는 분기가 하나 더 생긴다. 기본 타이머는 인터럽트 원인이 업데이트
 *    하나뿐이라 그 왕복이 아무것도 사 주지 않는다. */
void mk_ads_io_tick_isr(void)
{
    if ((MK_ADS_TICK_TIM->SR & TIM_SR_UIF) == 0u) {
        return;
    }
    /* 🔴 다른 플래그를 함께 지우지 않도록 쓰기 마스크를 반전해 넣는다
     *    (RM0468 의 rc_w0 필드 — 0 을 쓴 비트만 지워진다). */
    MK_ADS_TICK_TIM->SR = ~(uint32_t)TIM_SR_UIF;

    if (s_ads != NULL) {
        mk_ads_tick(s_ads, mk_time_ms());
    }
}

void mk_ads_io_spi_isr(void)    { HAL_SPI_IRQHandler(&s_spi); }
void mk_ads_io_dma_rx_isr(void) { HAL_DMA_IRQHandler(&s_hdma_rx); }
void mk_ads_io_dma_tx_isr(void) { HAL_DMA_IRQHandler(&s_hdma_tx); }

/* HAL 이 전송 완료 때 부른다.
 *
 * 🔴 TxRxCpltCallback 만 잡는다. TxCplt 은 송신 FIFO 가 빈 시점이라 수신이
 *    아직 안 끝났을 수 있다 — 그때 읽으면 이전 값이 나온다. */
void HAL_SPI_TxRxCpltCallback(SPI_HandleTypeDef *hspi)
{
    if (hspi->Instance == SPI4 && s_ads != NULL) {
        mk_ads_on_spi_done(s_ads, mk_time_ms());
    }
}

void HAL_SPI_ErrorCallback(SPI_HandleTypeDef *hspi)
{
    /* 🔴 오류가 나도 상태머신을 놔두지 않는다. 놔두면 READING 에 갇혀
     *    그 채널뿐 아니라 **모든 채널**이 멈춘다 — 타임아웃이 결국 풀어
     *    주지만 그때까지 다른 채널도 서 있다. 완료로 쳐서 즉시 넘긴다.
     *    값은 쓰레기지만, 그것은 큐의 한 표본일 뿐이다. */
    if (hspi->Instance == SPI4 && s_ads != NULL) {
        mk_ads_on_spi_done(s_ads, mk_time_ms());
    }
}
