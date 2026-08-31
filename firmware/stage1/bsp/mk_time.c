#include "mk_time.h"

#include "stm32h7xx_hal.h"

/* 마지막으로 본 32비트 틱과, 그것이 몇 바퀴 돌았는지. */
static volatile uint32_t s_last;
static volatile uint32_t s_wraps;

int64_t mk_time_ms(void)
{
    /* 🔴 임계구역이 필요하다. 이 함수는 슈퍼루프와 인터럽트(DRDY, SPI DMA)
     *    양쪽에서 불린다. 감싸지 않으면 두 호출이 겹칠 때 한쪽이 s_wraps 를
     *    올리는 중간을 다른 쪽이 읽어, 49.7일마다 한 번씩 4,294,967,296 ms
     *    (약 49.7일)를 통째로 잘못 세는 순간이 생긴다.
     *
     *    비활성 구간은 몇 사이클이다. UART RXNE(우선순위 5)를 이 정도로
     *    막는 것은 921600 baud 에서도 문제가 되지 않는다 — 한 바이트가
     *    약 10.8 µs 이고 여기는 그보다 두 자릿수 짧다. */
    uint32_t primask = __get_PRIMASK();
    __disable_irq();

    uint32_t now = HAL_GetTick();
    if (now < s_last) {
        /* 되감겼다. 이 함수를 49.7일에 한 번보다는 자주 부른다는 전제다 —
         * 슈퍼루프가 매 바퀴 부르므로 성립한다. */
        s_wraps++;
    }
    s_last = now;
    uint32_t wraps = s_wraps;

    if (!primask) {
        __enable_irq();
    }

    return ((int64_t)wraps << 32) | (int64_t)now;
}
