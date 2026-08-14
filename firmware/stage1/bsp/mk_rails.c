#include "mk_rails.h"

#include "stm32h7xx_hal.h"

/* 🔴 이 세 상수가 이 파일에 있는 이유는, 여기가 GPIOD 를 만지는 유일한
 *    곳이기 때문이다. 흩어지면 안전 검사가 못 따라간다.
 *
 *    핀 번호만으로는 판정할 수 없다 — GPIO_PIN_10 은 GPIOD 에서는 5V
 *    레일이지만 GPIOB 에서는 USART3 TX 다. 포트가 무엇인지가 전부다. */
#define RAIL_PORT   GPIOD
#define PIN_24V     GPIO_PIN_8
#define PIN_14V9    GPIO_PIN_9
#define PIN_5V      GPIO_PIN_10
#define PIN_LED     GPIO_PIN_11

void mk_rails_init(void)
{
    __HAL_RCC_GPIOD_CLK_ENABLE();

    GPIO_InitTypeDef g = {0};
    g.Pin   = PIN_24V | PIN_14V9 | PIN_5V | PIN_LED;
    g.Mode  = GPIO_MODE_OUTPUT_PP;
    g.Pull  = GPIO_NOPULL;
    g.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init(RAIL_PORT, &g);

    /* 🔴 전부 Low 로 시작한다. 리셋 직후의 상태를 그대로 유지하는 것이고,
     *    올리는 것은 mk_railctl 이 설정을 보고 순서대로 한다.
     *
     *    여기서 5V 를 미리 올리고 싶은 유혹이 있다(ADS1256 도 WS2812 도
     *    필요하니까). 그러면 순서 로직을 우회하게 되고, 그 로직이 지키는
     *    간격이 사라진다. 한 곳에서만 올린다. */
    HAL_GPIO_WritePin(RAIL_PORT, PIN_24V | PIN_14V9 | PIN_5V | PIN_LED,
                      GPIO_PIN_RESET);
}

void mk_rails_set(void *ctx, MkRail rail, int on)
{
    (void)ctx;

    uint16_t pin;
    switch (rail) {
    case MK_RAIL_5V:   pin = PIN_5V;   break;
    case MK_RAIL_14V9: pin = PIN_14V9; break;
    case MK_RAIL_24V:  pin = PIN_24V;  break;
    default:           return;
    }


    HAL_GPIO_WritePin(RAIL_PORT, pin, on ? GPIO_PIN_SET : GPIO_PIN_RESET);
}

void mk_rails_led(int on)
{
    HAL_GPIO_WritePin(RAIL_PORT, PIN_LED, on ? GPIO_PIN_SET : GPIO_PIN_RESET);
}
