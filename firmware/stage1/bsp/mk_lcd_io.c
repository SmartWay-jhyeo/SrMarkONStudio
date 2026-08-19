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
     *    HAL_SPI_Transmit(블로킹)을 부르면 한 행에 1 ms, 한 장에 461 ms
     *    (8 MHz 기준) 동안 슈퍼루프가 서고 ADS1256 표본과 텔레메트리가
     *    통째로 밀린다. */
    return HAL_SPI_Transmit_DMA(&s_spi, (uint8_t *)(uintptr_t)buf,
                                (uint16_t)n) == HAL_OK;
}

/* ── SPI 클럭 ─────────────────────────────────────────────────────────────
 *
 * 🔴 SPI2 의 커널 클럭은 **per_ck = hsi_ker_ck = 64 MHz** 다. 이 펌웨어는
 *    PLL 을 안 켜므로 기본 소스(pll1_q_ck)를 쓸 수 없어 아래 spi_init() 이
 *    직접 골라 준다. 아래 계산이 전부 그 64 MHz 를 전제로 한다.
 *
 *        64 / 4  = 16 MHz   쓰기 상한 20 MHz 안 (twc MIN 50 ns,
 *                           ILI9488.pdf p.332 §17.4.3)
 *        64 / 8  =  8 MHz   ← 기본. 사용자 결정 2026-08-19
 *        64 / 16 =  4 MHz   되읽기는 항상 이 값으로 내려간다 (아래)
 *        64 / 32 =  2 MHz
 *
 *    64 / 2 = 32 MHz 는 상한을 넘으므로 안 쓴다.
 *
 * 🔴 **되읽기 상한은 6.6 MHz 다** — 같은 표의 trc(Serial clock cycle,
 *    Read) MIN 150 ns. 쓰기 상한(20 MHz)과 다르므로, 되읽기 동안만
 *    4 MHz 로 내렸다가 되돌린다. 이것을 안 지키면 되읽은 값이 조용히
 *    틀리고, 그러면 회복 장치가 멀쩡한 패널을 몇 초마다 다시 켠다.
 */
#define LCD_READ_PRESCALER   SPI_BAUDRATEPRESCALER_16   /* 64/16 = 4 MHz */

static uint32_t s_write_presc = SPI_BAUDRATEPRESCALER_8; /* 64/8 = 8 MHz */

/* 방향·분주비를 다시 세운다.
 *
 * 🔴 H7 의 HAL_SPI_Init() 은 안에서 주변장치를 껐다가 레지스터를 다시
 *    쓴다. DMA 연결(__HAL_LINKDMA)은 핸들에 남으므로 다시 안 걸어도 된다.
 *    전송이 떠 있을 때 부르면 안 된다 — 부르는 쪽이 busy 를 보고 있다. */
static void spi_apply(uint32_t direction, uint32_t presc)
{
    s_spi.Init.Direction = direction;
    s_spi.Init.BaudRatePrescaler = presc;
    HAL_SPI_Init(&s_spi);
}

static void io_set_clock(void *ctx, uint32_t khz)
{
    (void)ctx;
    /* 🔴 모르는 값이면 8 MHz 로 본다. 설정표와 여기가 갈리는 것은 실수이고,
     *    실수했을 때는 느린 쪽이 안전하다. */
    uint32_t presc = SPI_BAUDRATEPRESCALER_8;
    if (khz >= 16000u)     { presc = SPI_BAUDRATEPRESCALER_4;  }
    else if (khz >= 8000u) { presc = SPI_BAUDRATEPRESCALER_8;  }
    else if (khz >= 4000u) { presc = SPI_BAUDRATEPRESCALER_16; }
    else                   { presc = SPI_BAUDRATEPRESCALER_32; }

    s_write_presc = presc;
    spi_apply(SPI_DIRECTION_2LINES_TXONLY, presc);
}

/* 되읽기 한 번 (app/mk_lcd.c 의 되읽기 상태기계가 부른다).
 *
 * 🔴 여기만 **동기**다. 최대 2바이트이고 4 MHz 라 4 us 다 — DMA 를 걸고
 *    인터럽트를 기다리는 비용이 전송 자체보다 크고, 그러자고 SPI2 를
 *    전이중 DMA 로 다시 짜면 그림 그리는 쪽(지금 실기기에서 도는 경로)까지
 *    함께 흔들린다. 대신 방향과 분주비를 이 함수 안에서만 바꾸고 즉시
 *    되돌린다.
 *
 * 🔴 완료를 이 안에서 알린다. `send` 와 같은 규약을 지키되 동기로 끝나는
 *    것뿐이라, 상태기계 쪽은 다음 바퀴에 다음 걸음을 낸다. */
static int io_xfer(void *ctx, const uint8_t *tx, uint8_t *rx, size_t n)
{
    (void)ctx;
    spi_apply(SPI_DIRECTION_2LINES, LCD_READ_PRESCALER);
    HAL_StatusTypeDef st = HAL_SPI_TransmitReceive(
        &s_spi, (uint8_t *)(uintptr_t)tx, rx, (uint16_t)n, 5u);
    spi_apply(SPI_DIRECTION_2LINES_TXONLY, s_write_presc);

    if (s_lcd != NULL) {
        mk_lcd_on_tx_done(s_lcd);
    }
    return st == HAL_OK;
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

    /* 🔴 터치 CS 는 **능동으로 몰아야 한다.** 위 WritePin 만 있고 이 줄의
     *    핀 목록에서 빠지면 그 쓰기는 아무 데도 안 간다 — 핀은 리셋 기본
     *    상태(아날로그/입력)로 남고, High 를 지키는 것은 R89 10k 풀업뿐이
     *    된다. 풀업은 "아직 아무도 안 몬 상태" 일 뿐이라, 노이즈가 그 선을
     *    끌어내리면 XPT2046 이 LCD 로 가는 클럭에 반응해 MISO 를 물고
     *    늘어진다. 그것이 "무작위로 픽셀이 깨진다" 의 유력한 후보다
     *    (사용자 관측 2026-08-19).
     *
     *    host/tests/test_firmware_safety.py 가 이 줄과 위 WritePin 을 함께
     *    확인한다. */
    g.Pin   = PIN_RESX | PIN_DC | PIN_TOUCH_CS;
    HAL_GPIO_Init(LCD_CTL_PORT, &g);

    /* 🔴 PIN_TOUCH_IRQ(PD12)는 열지 않는다 — EXTI12 는 터치가 붙을 때
     *    쓴다(CLAUDE.md §4: EXTI15 는 ADS1256 DRDY 가 점유한다). */
    (void)PIN_TOUCH_IRQ;

    /* 🔴 PB14(MISO)를 **연다.** 2차까지는 쓰기만 해서 닫아 두었는데,
     *    화면이 깨진 뒤 저절로 안 돌아오는 문제(2026-08-19)를 가리려면
     *    패널에게 되물어야 한다 — MADCTL·COLMOD 를 되읽어 우리가 넣은
     *    값과 대조하는 것이 "명령이 깨졌나 GRAM 이 어긋났나" 를 가르는
     *    유일한 방법이다 (app/mk_lcd.h).
     *
     *    터치 칩과 공유하는 선이지만 터치 CS 는 늘 High 라(위) 이 선을
     *    모는 것은 LCD 뿐이다. */
    g.Pin       = PIN_SCK | PIN_MOSI | PIN_MISO;
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
    /* 🔴 평상시(그림 그리기)는 송신 전용이다. MISO 는 열려 있지만, 화소를
     *    460,800 바이트 미는 동안 받을 것이 없는데 전이중으로 두면 RX
     *    FIFO 가 채워지고 오버런 처리를 이유 없이 안고 간다.
     *
     *    되읽기 때만 io_xfer() 가 2LINES 로 바꿨다가 즉시 되돌린다. */
    s_spi.Init.Direction     = SPI_DIRECTION_2LINES_TXONLY;
    s_spi.Init.DataSize      = SPI_DATASIZE_8BIT;
    /* 🔴 모드 0. ILI9488.pdf p.44 §4.2.1 — 상승 엣지에서 SDA 를 읽는다. */
    s_spi.Init.CLKPolarity   = SPI_POLARITY_LOW;
    s_spi.Init.CLKPhase      = SPI_PHASE_1EDGE;
    s_spi.Init.NSS           = SPI_NSS_SOFT;
    /* 🔴 64 MHz / 8 = 8 MHz 로 시작한다. `lcd.spi_khz` 가 첫 tick 에
     *    io_set_clock() 을 부르므로 여기 값은 그때까지의 잠깐뿐이지만,
     *    그 잠깐도 낮은 쪽에서 시작하는 편이 낫다 (사용자 결정
     *    2026-08-19: "8mhz로 낮춰서 해보자"). 계산은 io_set_clock() 위
     *    주석에 있다. */
    s_spi.Init.BaudRatePrescaler = SPI_BAUDRATEPRESCALER_8;
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

    MkLcdIo io = { io_cs, io_dc, io_reset, io_backlight, io_send,
                   io_xfer, io_set_clock, NULL };
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
