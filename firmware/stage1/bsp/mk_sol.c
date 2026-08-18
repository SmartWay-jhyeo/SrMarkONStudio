#include "mk_sol.h"

#include "stm32h7xx_hal.h"

/* 🔴 이 세 상수가 이 파일에만 있는 이유는 mk_rails.c 와 같다 — 흩어지면
 *    안전 검사가 못 따라간다.
 *
 *    핀 번호만으로는 판정할 수 없다. GPIO_PIN_4 는 GPIOA 에서는 J18
 *    출력이지만 GPIOE 에서는 아무것도 아니다. 포트가 무엇인지가 전부다. */
#define SOL_PORT    GPIOA
#define PIN_J18     GPIO_PIN_4
#define PIN_J19     GPIO_PIN_5
#define PIN_J20     GPIO_PIN_6

void mk_sol_init(void)
{
    __HAL_RCC_GPIOA_CLK_ENABLE();

    GPIO_InitTypeDef g = {0};
    g.Pin   = PIN_J18 | PIN_J19 | PIN_J20;
    g.Mode  = GPIO_MODE_OUTPUT_PP;
    g.Pull  = GPIO_NOPULL;
    /* 🔴 느린 슬루레이트로 둔다. 이 핀들은 커넥터로 바로 나가 밖으로
     *    선을 끌고 나가므로, 빠른 엣지는 방사만 늘린다. 출력이 바뀌는
     *    빈도는 사람이 스위치를 누르는 정도다. */
    g.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init(SOL_PORT, &g);

    /* 🔴 전부 Low 로 시작한다. 리셋 직후 상태를 그대로 두는 것이고,
     *    올리는 것은 mk_solctl 이 설정을 보고 한다.
     *
     *    🔴 PA7 은 건드리지 않는다. 같은 포트지만 WS2812 데이터선이고
     *       TIM3 의 AF 로 잡혀 있다 — 여기서 함께 초기화하면 그 설정을
     *       덮어써 체인이 조용히 죽는다. */
    HAL_GPIO_WritePin(SOL_PORT, PIN_J18 | PIN_J19 | PIN_J20, GPIO_PIN_RESET);
}

void mk_sol_set(void *ctx, MkSolCh ch, int on)
{
    (void)ctx;

    uint16_t pin;
    switch (ch) {
    case MK_SOL_J18: pin = PIN_J18; break;
    case MK_SOL_J19: pin = PIN_J19; break;
    case MK_SOL_J20: pin = PIN_J20; break;
    default:         return;
    }

    HAL_GPIO_WritePin(SOL_PORT, pin, on ? GPIO_PIN_SET : GPIO_PIN_RESET);
}
