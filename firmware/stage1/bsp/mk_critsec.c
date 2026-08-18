#include "mk_critsec.h"

#include "stm32h7xx_hal.h"

/* enter()/exit() 가 짝을 벗어나 중첩되는 일이 없다(헤더 주석 참고) —
 * enter() 가 __disable_irq() 로 인터럽트를 전부 막으므로, exit() 전에는
 * 어떤 ISR 도 새 enter() 를 부를 수 없다. 그래서 저장할 PRIMASK 는
 * 하나면 된다. */
static volatile uint32_t s_primask;

void mk_critsec_enter(void)
{
    s_primask = __get_PRIMASK();
    __disable_irq();
}

void mk_critsec_exit(void)
{
    /* 🔴 원래 꺼져 있었으면 다시 켜지 않는다. mk_time_ms() 와 같은 이유 —
     *    이 호출이 이미 인터럽트가 막힌 더 큰 임계구역 안에서 걸린 것일
     *    수 있고, 그때 무조건 켜면 바깥 구역의 보호가 새어 나간다. */
    if (!s_primask) {
        __enable_irq();
    }
}
