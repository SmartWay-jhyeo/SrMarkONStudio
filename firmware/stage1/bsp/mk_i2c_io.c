#include "mk_i2c_io.h"

#include "stm32h7xx_hal.h"

/* 🔴 전송 타임아웃 — 이것이 최악 블로킹 시간이 **아니다**.
 *
 *    이 값은 START 이후, 즉 슬레이브가 클럭을 늘려 잡는 각 바이트 단계
 *    에서만 적용된다. 100 kHz 에서 3바이트가 약 0.4 ms 이므로 5 ms 면
 *    12배 여유다.
 *
 *    하지만 HAL_I2C_Master_Transmit/Receive·HAL_I2C_Mem_Read 는 그
 *    전에 버스 BUSY 를 먼저 기다리는데, 그 대기는 우리가 넘긴
 *    XFER_TIMEOUT_MS 가 아니라 HAL 에 박힌 고정 매크로를 쓴다:
 *
 *      #define I2C_TIMEOUT_BUSY (25U)   // stm32h7xx_hal_i2c.c:344
 *      I2C_WaitOnFlagUntilTimeout(hi2c, I2C_FLAG_BUSY, SET,
 *                                 I2C_TIMEOUT_BUSY, tickstart);
 *      (Master_Transmit 1133행 · Master_Receive 1273행 · Mem_Read 2682행 —
 *       Cube FW_H7 V1.13.0 stm32h7xx_hal_i2c.c)
 *
 *    즉 버스가 눌린 채로(전원 없는 센서가 SDA 를 Low 로 잡고 있다든가,
 *    배선 불량) 호출이 시작되면 최악 블로킹은 5 ms 가 아니라
 *    **25 ms + 5 ms = 30 ms** 다 — xfer **한 번**의 최악이다.
 *
 *    🔴 [검토 지적 I5] BH1750 의 start(app/mk_i2c_bh1750.c)는 Power On·
 *    모드 선택을 STOP 을 사이에 두고 **두 번** 나눠 보낸다. mk_i2c_tick()
 *    은 포트가 START 상태를 나아갈 때 이 두 xfer 를 한 스텝(한 바퀴) 안에
 *    잇달아 부르므로, 버스가 눌린 채로 시작 명령이 오면 최악 블로킹은
 *    30 ms 가 아니라 **60 ms** 다(HANDOFF.md §7.2도 이 값으로 맞춰 뒀다).
 *
 *    지금은 받아들일 만하다 — mk_i2c_tick() 이 한 바퀴에 포트 하나만
 *    진행하므로 60 ms 는 한 바퀴에 최대 한 번뿐이고, ADS1256 수집은
 *    DRDY 인터럽트 + DMA 라 그 60 ms 동안에도 표본을 잃지 않는다.
 *
 *    버스 잠김을 스스로 풀거나(SCL 토글) 선행 BUSY 검사를 넣는 것은
 *    계획서 §14 가 "실제로 겪은 뒤에 넣는다" 로 미뤄 둔 항목이다 —
 *    아직 겪지 않았으므로 여기서는 넣지 않는다. */
#define XFER_TIMEOUT_MS   5u

/* 🔴 여섯 핀 모두 AF4 다 (DS13313 Rev 1, Table 8 — PA8·PC9 p.73·77,
 *    PC10·PC11 p.77, PB8·PB9 p.74). PB8/PB9 는 ST 예제(Cube FW_H7
 *    V1.13.0, NUCLEO-H723ZG I2C 예제)가 GPIO_AF4_I2C1 을 쓰는 것으로도
 *    맞다.
 *
 * 🔴 PA8·PC9 는 AF6 에도 I2C5 가 있다. 잘못 고르면 J10·J11 이 I2C3 이
 *    아니라 I2C5 에 붙고, 코드는 I2C3 핸들을 쓰므로 아무 파형도 안 나간다 —
 *    배선을 의심하게 되는 종류의 실패다. */
#define I2C1_AF              GPIO_AF4_I2C1
#define I2C3_AF              GPIO_AF4_I2C3
#define I2C5_AF              GPIO_AF4_I2C5

/* 커널 클럭 64 MHz(APB1, RCC_D2CCIP2R_I2C1235SEL 리셋값) 에서 100 kHz.
 * ST 자체 유틸리티(i2c_timing_utility.c, Cube FW_H7 V1.13.0) 로 산출했다
 * — SDADEL·SCLDEL·아날로그 필터 지연까지 함께 푸는 계산이라 손으로
 * 짓지 않는다. 클럭을 바꾸면 같은 유틸리티로 다시 뽑는다(400 kHz 는
 * 0x30D00A13 이었다). */
#define I2C_TIMINGR_100K     0x60702729u

static I2C_HandleTypeDef s_i2c1, s_i2c3, s_i2c5;

static I2C_HandleTypeDef *handle_of(uint8_t bus)
{
    switch (bus) {
    case 1u: return &s_i2c1;
    case 3u: return &s_i2c3;
    case 5u: return &s_i2c5;
    default: return NULL;
    }
}

/* 🔴 HAL 의 **블로킹 판**만 쓴다. 이 층은 동기 계약이다 — IT/DMA 판을
 *    쓰면 완료를 기다릴 곳이 없다. 대신 타임아웃을 짧게 잡는다. */
static int map_rc(I2C_HandleTypeDef *h, HAL_StatusTypeDef rc)
{
    if (rc == HAL_OK) {
        return 0;
    }
    /* 🔴 주소 NACK 은 "센서가 없다" 이지 고장이 아니다. 규격 §7.5 의
     *    status=1 로 가야 하고, 버스 고장(status=2)과 갈라야 한다. */
    if ((HAL_I2C_GetError(h) & HAL_I2C_ERROR_AF) != 0u) {
        return -1;
    }
    return -2;
}

/* 🔴 "xfer 한 번 = STOP 한 번" 확인 근거.
 *
 *    BH1750 데이터시트(p.10)가 "insert SP every 1 Opecode" 를 요구하고,
 *    app/mk_i2c_bh1750.c 는 그것을 xfer 를 두 번 나눠 부르는 것으로
 *    표현했다 — 그러려면 이 층에서 호출 한 번이 STOP 으로 끝나야 한다.
 *
 *    HAL 소스로 직접 확인했다(stm32h7xx_hal_i2c.c, Cube FW_H7 V1.13.0):
 *      - HAL_I2C_Master_Transmit  : 1155행 `xfermode = I2C_AUTOEND_MODE`,
 *        1222행 주석 "with AUTOEND mode the stop is automatically
 *        generated", 1224행에서 STOPF 플래그를 기다린 뒤에야 돌아온다.
 *      - HAL_I2C_Master_Receive   : 1336행에서 같은 I2C_AUTOEND_MODE,
 *        1344행에서 STOPF 를 기다린다.
 *      - HAL_I2C_Mem_Read         : 2715·2759행에서 같은 패턴 — 명령
 *        1바이트를 NO_STARTSTOP 로 보낸 뒤(레지스터 주소 자리, repeated
 *        start), 데이터 단계는 AUTOEND 로 열어 STOPF 까지 기다린다.
 *    셋 다 함수가 반환하기 전에 STOP 이 이미 나가 있다. 즉 이 세 함수
 *    중 어느 것을 골라도 xfer 한 번은 STOP 하나로 끝난다. */
int mk_i2c_io_xfer(void *ctx, uint8_t bus, uint8_t addr,
                   const uint8_t *tx, size_t ntx, uint8_t *rx, size_t nrx)
{
    (void)ctx;
    I2C_HandleTypeDef *h = handle_of(bus);
    if (h == NULL) {
        return -2;
    }

    /* HAL 은 8비트 주소를 받는다 — 7비트를 왼쪽으로 한 칸 민다. */
    uint16_t a8 = (uint16_t)((uint16_t)addr << 1);
    HAL_StatusTypeDef rc;

    if (ntx > 0u && nrx > 0u) {
        /* 명령을 쓰고 STOP 없이 읽는다 (repeated start). MLX90614 형태이고,
         * HAL 에서는 Mem_Read 가 그 파형을 낸다 — 명령 1바이트가 "레지스터
         * 주소" 자리에 들어간다. */
        if (ntx != 1u) {
            return -2;           /* 지금 쓰는 칩 중 2바이트 명령은 없다 */
        }
        rc = HAL_I2C_Mem_Read(h, a8, (uint16_t)tx[0], I2C_MEMADD_SIZE_8BIT,
                              rx, (uint16_t)nrx, XFER_TIMEOUT_MS);
    } else if (ntx > 0u) {
        rc = HAL_I2C_Master_Transmit(h, a8, (uint8_t *)tx, (uint16_t)ntx,
                                     XFER_TIMEOUT_MS);
    } else if (nrx > 0u) {
        /* 🔴 명령 없이 그냥 읽는다. BH1750 은 레지스터 주소가 없어서
         *    Mem_Read 를 쓰면 안 된다 — 없는 주소 바이트가 하나 더 나간다. */
        rc = HAL_I2C_Master_Receive(h, a8, rx, (uint16_t)nrx, XFER_TIMEOUT_MS);
    } else {
        return 0;                /* 할 일이 없다 */
    }

    return map_rc(h, rc);
}

static void open_pins(GPIO_TypeDef *port, uint16_t pins, uint8_t af)
{
    GPIO_InitTypeDef g = {0};
    g.Pin = pins;
    /* 🔴 오픈 드레인이다. 푸시풀로 두면 두 포트가 동시에 말할 때
     *    단락된다 — I2C 는 서로 Low 로만 끌어당긴다. */
    g.Mode = GPIO_MODE_AF_OD;
    /* 🔴 내부 풀업을 켜지 않는다. 온보드 4.7 kΩ(R25~R30)이 이미 있고,
     *    그 풀업 전압은 JP1~JP3 가 고른 V_I2Cx 다. 내부 풀업을 켜면
     *    3.3V 를 5V 버스에 얹는 꼴이 된다. */
    g.Pull = GPIO_NOPULL;
    g.Speed = GPIO_SPEED_FREQ_LOW;
    g.Alternate = af;
    HAL_GPIO_Init(port, &g);
}

static void open_bus(I2C_HandleTypeDef *h, I2C_TypeDef *inst)
{
    h->Instance = inst;
    h->Init.Timing = I2C_TIMINGR_100K;
    h->Init.OwnAddress1 = 0;
    h->Init.AddressingMode = I2C_ADDRESSINGMODE_7BIT;
    h->Init.DualAddressMode = I2C_DUALADDRESS_DISABLE;
    h->Init.OwnAddress2 = 0;
    h->Init.OwnAddress2Masks = I2C_OA2_NOMASK;
    h->Init.GeneralCallMode = I2C_GENERALCALL_DISABLE;
    h->Init.NoStretchMode = I2C_NOSTRETCH_DISABLE;
    /* 🔴 [검토 지적 Minor — 그대로 둔다] HAL_I2C_Init 의 반환값을 버린다.
     *    고쳐 보려 했으나 mk_i2c_io_init() 이 void 이고, 이 bsp 층
     *    전체(mk_uart.c 의 HAL_UART_Init, mk_ads_io.c 의 HAL_SPI_Init·
     *    HAL_DMA_Init 등)가 같은 모양이다 — 반환값을 받아도 알릴 통로가
     *    없어 `(void)` 로 다시 버리는 것과 다를 게 없다. 여기만 고치면
     *    다른 bsp 초기화들과 관례가 갈려 오히려 헷갈린다. 정말 고치려면
     *    부팅 오류를 알릴 통로(예: $STAT 에 초기화 실패 비트)부터
     *    설계해야 하고, 그것은 이 라운드 범위 밖이다. */
    (void)HAL_I2C_Init(h);
}

void mk_i2c_io_init(void)
{
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_GPIOB_CLK_ENABLE();
    __HAL_RCC_GPIOC_CLK_ENABLE();

    /* 🔴 핀별로만 연다. GPIOA 에는 sol(PA4~PA6)과 WS2812(PA7)가 있어
     *    포트를 통째로 초기화하면 서로를 덮는다. */
    open_pins(GPIOA, GPIO_PIN_8,  I2C3_AF);              /* I2C3 SCL */
    open_pins(GPIOC, GPIO_PIN_9,  I2C3_AF);              /* I2C3 SDA */
    open_pins(GPIOC, GPIO_PIN_11 | GPIO_PIN_10, I2C5_AF); /* I2C5 SCL·SDA */
    open_pins(GPIOB, GPIO_PIN_8 | GPIO_PIN_9,   I2C1_AF); /* I2C1 SCL·SDA */

    __HAL_RCC_I2C1_CLK_ENABLE();
    __HAL_RCC_I2C3_CLK_ENABLE();
    __HAL_RCC_I2C5_CLK_ENABLE();

    open_bus(&s_i2c1, I2C1);
    open_bus(&s_i2c3, I2C3);
    open_bus(&s_i2c5, I2C5);
}
