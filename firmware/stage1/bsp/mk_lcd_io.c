#include "mk_lcd_io.h"

#include "mk_dma_mem.h"

#include "stm32h7xx_hal.h"

/* ── 핀 ───────────────────────────────────────────────────────────────────
 * 근거는 헤더에 있다. 여기서는 포트가 둘로 갈린다는 것만 다시 적는다 —
 * 안전 검사(host/tests/test_firmware_safety.py)가 "포트 + 핀 번호" 로
 * 소유권을 보기 때문에, 같은 번호가 다른 포트에 또 있다는 사실이 중요하다.
 * (PB14 = MISO 와 PD14 = 터치 CS 가 실제로 그렇다.) */
#define LCD_BUS_PORT   GPIOB
#define PIN_BL         GPIO_PIN_6    /* PB6  백라이트 */
#define PIN_CS         GPIO_PIN_12   /* PB12 LCD CS */
#define PIN_SCK        GPIO_PIN_13   /* PB13 SPI2_SCK  AF5 */
#define PIN_MISO       GPIO_PIN_14   /* PB14 SPI2_MISO AF5 — 아래 참고 */
#define PIN_MOSI       GPIO_PIN_15   /* PB15 SPI2_MOSI AF5 */

#define LCD_CTL_PORT   GPIOD
#define PIN_TOUCH_IRQ  GPIO_PIN_12   /* PD12 터치 IRQ — 1단계는 안 쓴다 */
#define PIN_RESX       GPIO_PIN_13   /* PD13 RESX */
#define PIN_TOUCH_CS   GPIO_PIN_14   /* PD14 터치 CS — High 고정 */
#define PIN_DC         GPIO_PIN_15   /* PD15 D/CX */

static SPI_HandleTypeDef s_spi;
static DMA_HandleTypeDef s_hdma_tx;
static MkLcd            *s_lcd;

/* 🔴 DMA 가 닿는 곳에 둔다. 그냥 static 이면 DTCM 에 잡히고 DMA1 은 거기
 *    닿지 못한다 — 전송이 조용히 실패한다 (bsp/mk_dma_mem.h). 링크 뒤
 *    tools/check_dma_placement.py 가 실제 주소를 확인한다.
 *
 * 🔴 행 버퍼가 960바이트다. 프레임버퍼(460,800)를 들지 않는 이유는
 *    app/mk_lcd.h 상단에 있다. */
static uint8_t MK_DMA_BUF s_cmd_buf[MK_LCD_CMD_BUF_BYTES];
static uint8_t MK_DMA_BUF s_row_buf[MK_LCD_ROW_BYTES];

/* ---- 상태기계가 시키는 일 ------------------------------------------------ */

static void io_cs(void *ctx, int low)
{
    (void)ctx;
    HAL_GPIO_WritePin(LCD_BUS_PORT, PIN_CS, low ? GPIO_PIN_RESET : GPIO_PIN_SET);
}

static void io_dc(void *ctx, int data)
{
    (void)ctx;
    /* D/CX = 1 이면 데이터, 0 이면 명령 (ILI9488.pdf p.44 Figure 6). */
    HAL_GPIO_WritePin(LCD_CTL_PORT, PIN_DC, data ? GPIO_PIN_SET : GPIO_PIN_RESET);
}

static void io_reset(void *ctx, int low)
{
    (void)ctx;
    HAL_GPIO_WritePin(LCD_CTL_PORT, PIN_RESX, low ? GPIO_PIN_RESET : GPIO_PIN_SET);
}

static void io_backlight(void *ctx, int on)
{
    (void)ctx;
    /* 🔴 [미확인] High = 켜짐으로 **가정**한다. J25.8 은 MCU 핀이 커넥터로
     *    바로 나가고(넷리스트), 그 너머 모듈 안에서 무엇이 받는지는 모듈
     *    제조사 자료가 있어야 안다 — 흔한 3.5" ILI9488 모듈은 트랜지스터
     *    베이스를 받아 High = 켜짐이지만, 이 판이 그렇다는 근거는 아직
     *    없다(CLAUDE.md §5). 실기기에서 화면이 검게만 나오면 여기부터
     *    뒤집어 본다. */
    HAL_GPIO_WritePin(LCD_BUS_PORT, PIN_BL, on ? GPIO_PIN_SET : GPIO_PIN_RESET);
}

static int io_send(void *ctx, const uint8_t *buf, size_t n)
{
    (void)ctx;
    /* 🔴 즉시 돌아온다. 완료는 DMA·EOT 인터럽트가 알려 준다. 여기서
     *    HAL_SPI_Transmit(블로킹)을 부르면 한 행에 0.5 ms, 한 장에 230 ms
     *    동안 슈퍼루프가 서고 ADS1256 표본과 텔레메트리가 통째로 밀린다. */
    return HAL_SPI_Transmit_DMA(&s_spi, (uint8_t *)(uintptr_t)buf,
                                (uint16_t)n) == HAL_OK;
}

/* ---- 초기화 -------------------------------------------------------------- */

static void gpio_init(void)
{
    __HAL_RCC_GPIOB_CLK_ENABLE();
    __HAL_RCC_GPIOD_CLK_ENABLE();

    GPIO_InitTypeDef g = {0};

    /* 🔴 값을 먼저 정해 놓고 방향을 연다. 순서가 반대면 핀이 한순간
     *    0 으로 나갔다가 올라간다 — RESX 가 그 순간 리셋을 먹고, 터치 CS 는
     *    그 순간 선택된다. */
    HAL_GPIO_WritePin(LCD_BUS_PORT, PIN_CS, GPIO_PIN_SET);      /* 비선택 */
    HAL_GPIO_WritePin(LCD_BUS_PORT, PIN_BL, GPIO_PIN_RESET);    /* 꺼짐 */
    HAL_GPIO_WritePin(LCD_CTL_PORT, PIN_RESX, GPIO_PIN_SET);    /* 리셋 해제 */
    HAL_GPIO_WritePin(LCD_CTL_PORT, PIN_DC, GPIO_PIN_RESET);
    /* 🔴 터치 CS 를 여기서 High 로 못박고 그 뒤로 다시 건드리지 않는다.
     *    LCD 와 같은 버스라, 떠 있으면 XPT2046 이 LCD 클럭에 반응해 MISO 를
     *    물고 늘어진다 (헤더 주석). */
    HAL_GPIO_WritePin(LCD_CTL_PORT, PIN_TOUCH_CS, GPIO_PIN_SET);

    g.Pin   = PIN_CS | PIN_BL;
    g.Mode  = GPIO_MODE_OUTPUT_PP;
    g.Pull  = GPIO_NOPULL;
    g.Speed = GPIO_SPEED_FREQ_HIGH;
    HAL_GPIO_Init(LCD_BUS_PORT, &g);

    g.Pin   = PIN_RESX | PIN_DC | PIN_TOUCH_CS;
    HAL_GPIO_Init(LCD_CTL_PORT, &g);

    /* 🔴 PIN_MISO(PB14)를 열지 않는다. 1단계는 쓰기만 하고, 이 선은 터치
     *    칩과 공유라 우리가 AF 로 잡아 두면 나중에 터치를 붙일 때 누가
     *    소유하는지가 흐려진다. 그래서 SPI2 도 TXONLY 로 연다.
     *
     * 🔴 PIN_TOUCH_IRQ(PD12)도 열지 않는다 — EXTI12 는 터치가 붙을 때
     *    쓴다(CLAUDE.md §4: EXTI15 는 ADS1256 DRDY 가 점유한다). */
    (void)PIN_MISO;
    (void)PIN_TOUCH_IRQ;

    g.Pin       = PIN_SCK | PIN_MOSI;
    g.Mode      = GPIO_MODE_AF_PP;
    g.Pull      = GPIO_NOPULL;
    /* 직렬 22Ω(R91·R92)이 링잉을 잡아 주지만, 16 MHz 에서 엣지가 무뎌지면
     * 셋업 시간(tds MIN 10 ns, p.332)을 못 지킨다. */
    g.Speed     = GPIO_SPEED_FREQ_VERY_HIGH;
    g.Alternate = GPIO_AF5_SPI2;      /* DS13313 p.75 Table 8 */
    HAL_GPIO_Init(LCD_BUS_PORT, &g);
}

static void spi_init(void)
{
    /* 🔴 SPI1/2/3 의 커널 클럭을 per_ck 로 돌린다. 기본값 pll1_q_ck 는 이
     *    펌웨어에서 존재하지 않는다(PLL 을 안 켠다) — 그대로 두면 SCK 가
     *    한 번도 안 움직이고, 증상은 "화면이 검다" 하나뿐이라 배선 불량과
     *    구분되지 않는다. per_ck 의 기본 소스는 hsi_ker_ck = 64 MHz 다.
     *    (헤더 주석 참고) */
    RCC_PeriphCLKInitTypeDef pk = {0};
    pk.PeriphClockSelection = RCC_PERIPHCLK_SPI123 | RCC_PERIPHCLK_CKPER;
    pk.CkperClockSelection  = RCC_CLKPSOURCE_HSI;
    pk.Spi123ClockSelection = RCC_SPI123CLKSOURCE_CLKP;
    HAL_RCCEx_PeriphCLKConfig(&pk);

    __HAL_RCC_SPI2_CLK_ENABLE();
    __HAL_RCC_DMA1_CLK_ENABLE();

    /* 🔴 D2 도메인 SRAM 의 클럭. 리셋 직후 꺼져 있다 — DMA 버퍼를 D2 로
     *    옮겨 놓고 이 줄이 없으면 결국 전송이 조용히 안 된다
     *    (bsp/mk_ads_io.c 에 실기기 확인 기록이 있다). 두 번 켜도 무해하다. */
    __HAL_RCC_D2SRAM1_CLK_ENABLE();
    __HAL_RCC_D2SRAM2_CLK_ENABLE();

    s_spi.Instance           = SPI2;
    s_spi.Init.Mode          = SPI_MODE_MASTER;
    /* 🔴 송신 전용. MISO(PB14)를 안 열었으므로 2LINES 로 두면 HAL 이 없는
     *    선을 기다린다. 터치를 붙일 때 이 값을 2LINES 로 바꾼다. */
    s_spi.Init.Direction     = SPI_DIRECTION_2LINES_TXONLY;
    s_spi.Init.DataSize      = SPI_DATASIZE_8BIT;
    /* 🔴 모드 0. ILI9488.pdf p.44 §4.2.1 — 상승 엣지에서 SDA 를 읽는다. */
    s_spi.Init.CLKPolarity   = SPI_POLARITY_LOW;
    s_spi.Init.CLKPhase      = SPI_PHASE_1EDGE;
    s_spi.Init.NSS           = SPI_NSS_SOFT;
    /* 🔴 64 MHz / 4 = 16 MHz. 상한은 20 MHz 다 (twc MIN 50 ns, p.332
     *    §17.4.3). /2 = 32 MHz 는 상한을 넘는다. */
    s_spi.Init.BaudRatePrescaler = SPI_BAUDRATEPRESCALER_4;
    s_spi.Init.FirstBit      = SPI_FIRSTBIT_MSB;
    s_spi.Init.TIMode        = SPI_TIMODE_DISABLE;
    s_spi.Init.CRCCalculation = SPI_CRCCALCULATION_DISABLE;
    s_spi.Init.NSSPMode      = SPI_NSS_PULSE_DISABLE;
    HAL_SPI_Init(&s_spi);

    /* 🔴 Stream0·1 = SPI4(ADS1256), Stream2 = TIM3(WS2812). Stream3 이
     *    비어 있다 (넷리스트·소스 확인 2026-08-19). 겹치면 둘 다 망가진다. */
    s_hdma_tx.Instance                 = DMA1_Stream3;
    s_hdma_tx.Init.Request             = DMA_REQUEST_SPI2_TX;
    s_hdma_tx.Init.Direction           = DMA_MEMORY_TO_PERIPH;
    s_hdma_tx.Init.PeriphInc           = DMA_PINC_DISABLE;
    s_hdma_tx.Init.MemInc              = DMA_MINC_ENABLE;
    s_hdma_tx.Init.PeriphDataAlignment = DMA_PDATAALIGN_BYTE;
    s_hdma_tx.Init.MemDataAlignment    = DMA_MDATAALIGN_BYTE;
    s_hdma_tx.Init.Mode                = DMA_NORMAL;
    /* 🔴 수집(SPI4, HIGH)보다 낮춘다. 화면이 한 바퀴 늦는 것은 안 보이지만
     *    ADS1256 표본이 밀리면 타임스탬프가 오염된다 (설계 원칙 2). */
    s_hdma_tx.Init.Priority            = DMA_PRIORITY_LOW;
    s_hdma_tx.Init.FIFOMode            = DMA_FIFOMODE_DISABLE;
    HAL_DMA_Init(&s_hdma_tx);
    __HAL_LINKDMA(&s_spi, hdmatx, s_hdma_tx);

    HAL_NVIC_SetPriority(DMA1_Stream3_IRQn, 7, 0);
    HAL_NVIC_EnableIRQ(DMA1_Stream3_IRQn);

    /* 🔴 SPI2 자신의 인터럽트도 켜야 한다. H7 의 SPI 는 전송 종료를 EOT
     *    플래그로 알리고 HAL 은 그 인터럽트에서 완료 콜백을 부른다 —
     *    안 켜면 DMA 는 다 옮겨 놓고도 통보가 오지 않아 상태기계가 첫
     *    전송에서 영영 선다. SPI4 에서 실기기로 겪은 것과 같은 함정
     *    (bsp/mk_ads_io.c). */
    HAL_NVIC_SetPriority(SPI2_IRQn, 7, 0);
    HAL_NVIC_EnableIRQ(SPI2_IRQn);
}

void mk_lcd_io_init(MkLcd *l)
{
    s_lcd = l;
    gpio_init();
    spi_init();

    MkLcdIo io = { io_cs, io_dc, io_reset, io_backlight, io_send, NULL };
    mk_lcd_init(l, &io, s_cmd_buf, sizeof s_cmd_buf,
                s_row_buf, sizeof s_row_buf);
}

/* ---- 하드웨어 사건 ------------------------------------------------------- */

void mk_lcd_io_dma_tx_isr(void) { HAL_DMA_IRQHandler(&s_hdma_tx); }

void mk_lcd_io_spi_isr(void)
{
    HAL_SPI_IRQHandler(&s_spi);

    /* 🔴 오류로 끝나면 완료 콜백이 오지 않는다. 그런데 이 파일은
     *    HAL_SPI_ErrorCallback 을 정의할 수 없다 — bsp/mk_ads_io.c 가
     *    이미 그 약한 심볼을 차지했고, 둘을 함께 정의하면 링크가 깨진다.
     *
     *    그래서 여기서 본다: HAL 이 상태를 READY 로 되돌렸다면 이 전송은
     *    어떤 식으로든 끝난 것이다. 정상 종료에도 걸리지만 busy 를 두 번
     *    내리는 것뿐이라 무해하고, 대신 **오류로 상태기계가 영원히 서는**
     *    경우가 없어진다 — 그 고장은 "화면이 안 나온다" 로만 보여서 원인을
     *    찾기 가장 어렵다. */
    if (s_lcd != NULL && HAL_SPI_GetState(&s_spi) == HAL_SPI_STATE_READY) {
        mk_lcd_on_tx_done(s_lcd);
    }
}

/* HAL 이 송신 완료 때 부른다.
 *
 * 🔴 TxCplt 이다 — bsp/mk_ads_io.c 는 TxRxCplt 을 쓴다(전이중). 서로 다른
 *    콜백이라 겹치지 않는다. */
void HAL_SPI_TxCpltCallback(SPI_HandleTypeDef *hspi)
{
    if (hspi->Instance == SPI2 && s_lcd != NULL) {
        mk_lcd_on_tx_done(s_lcd);
    }
}
