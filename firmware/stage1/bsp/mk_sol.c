#include "mk_sol.h"

#include "stm32h7xx_hal.h"

#include "mk_time.h"

/* 🔴 이 세 상수가 이 파일에만 있는 이유는 mk_rails.c 와 같다 — 흩어지면
 *    안전 검사가 못 따라간다.
 *
 *    핀 번호만으로는 판정할 수 없다. GPIO_PIN_4 는 GPIOA 에서는 J18
 *    입력이지만 GPIOE 에서는 아무것도 아니다. 포트가 무엇인지가 전부다. */
#define SOL_PORT    GPIOA
#define PIN_J18     GPIO_PIN_4
#define PIN_J19     GPIO_PIN_5
#define PIN_J20     GPIO_PIN_6

static MkSolCtl *s_sol;

/* raw 핀 레벨을 읽는다. HAL 이 GPIO_PIN_SET/RESET 이라는 enum 을
 * 돌려주므로 0/1 로 좁힌다 — app 층은 이 값에 HAL 흔적이 남는 것을
 * 모른다(HAL 비의존 경계). */
static int read_raw(uint16_t pin)
{
    return HAL_GPIO_ReadPin(SOL_PORT, pin) == GPIO_PIN_SET ? 1 : 0;
}

void mk_sol_init(MkSolCtl *sc)
{
    s_sol = sc;

    __HAL_RCC_GPIOA_CLK_ENABLE();

    /* 🔴 핀마다 따로 연다(mk_sol.h 상단 주석). Pull 은 내부 풀업 — 신호가
     *    없을 때 핀을 HIGH 로 고정해 뜬 상태(플로팅)로 흔들리지 않게
     *    한다. 옵토가 도통하면 그 풀업을 이기고 LOW 로 끌어내린다.
     *
     *    Mode 는 양쪽 엣지(RISING_FALLING) — 켜짐도 꺼짐도 놓치면 안
     *    된다. 상태는 레벨이지 한쪽 엣지만으로는 못 잡는다(ADS1256
     *    DRDY 처럼 한쪽만 보는 신호와 다르다). */
    GPIO_InitTypeDef g = {0};
    g.Mode = GPIO_MODE_IT_RISING_FALLING;
    g.Pull = GPIO_PULLUP;

    g.Pin = PIN_J18;
    HAL_GPIO_Init(SOL_PORT, &g);

    g.Pin = PIN_J19;
    HAL_GPIO_Init(SOL_PORT, &g);

    g.Pin = PIN_J20;
    HAL_GPIO_Init(SOL_PORT, &g);

    /* 🔴 부팅 시각의 실제 상태를 동기적으로 읽어 세운다. 텔레메트리는
     *    상태 변화에만 나가므로(규격 §7.6), 첫 엣지가 언제 올지(혹은
     *    영영 안 올지) 모르는 채로 두면 `$STAT` 이 "아직 모름"을 답하게
     *    된다 — mk_solctl_prime() 은 레코드 없이 조용히 기준을 세운다. */
    int64_t now = mk_time_ms();
    mk_solctl_prime(sc, MK_SOL_J18, read_raw(PIN_J18), now);
    mk_solctl_prime(sc, MK_SOL_J19, read_raw(PIN_J19), now);
    mk_solctl_prime(sc, MK_SOL_J20, read_raw(PIN_J20), now);

    /* 🔴 PA4 는 EXTI4 를 혼자 쓰고, PA5·PA6 은 EXTI9_5 를 나눠 쓴다
     *    (RM0468 — EXTI 라인 번호는 핀 번호를 그대로 따라간다. GPIO
     *    포트와 무관하게 "4번 핀"은 항상 EXTI4). ADS1256 DRDY(EXTI15,
     *    우선순위 6)보다 급하지 않다 — 사람이 스위치를 젖히는 신호라
     *    수 ms 지연은 무해하다. 값을 더 크게(우선순위를 낮게) 둔다. */
    HAL_NVIC_SetPriority(EXTI4_IRQn, 7, 0);
    HAL_NVIC_EnableIRQ(EXTI4_IRQn);
    HAL_NVIC_SetPriority(EXTI9_5_IRQn, 7, 0);
    HAL_NVIC_EnableIRQ(EXTI9_5_IRQn);
}

int mk_sol_read(void *ctx, MkSolCh ch)
{
    (void)ctx;
    /* 🔴 이 함수가 상태의 근거다 — `mk_solctl_tick()` 이 매 바퀴 여기를
     *    거쳐 세 핀을 직접 읽는다. ISR(mk_sol_exti4_isr·mk_sol_exti9_5_isr)
     *    은 "언제 바뀌었나"의 정밀 시각만 대는 보조 자료로 남는다. */
    switch (ch) {
    case MK_SOL_J18: return read_raw(PIN_J18);
    case MK_SOL_J19: return read_raw(PIN_J19);
    case MK_SOL_J20: return read_raw(PIN_J20);
    default:         return 0;
    }
}

void mk_sol_exti4_isr(void)
{
    if (__HAL_GPIO_EXTI_GET_IT(PIN_J18) == RESET) {
        return;
    }
    __HAL_GPIO_EXTI_CLEAR_IT(PIN_J18);

    /* 🔴 시각을 여기서 읽는다(설계 원칙 2) — 슈퍼루프로 미루면 UART·
     *    tick 지연이 타임스탬프에 섞인다. 판단(디바운스·반전)은 안 한다,
     *    큐에 넣기만 한다. */
    if (s_sol != NULL) {
        mk_solctl_on_edge(s_sol, MK_SOL_J18, read_raw(PIN_J18), mk_time_ms());
    }
}

void mk_sol_exti9_5_isr(void)
{
    /* 🔴 공유 벡터라 두 핀을 각각 확인해야 한다. 하나만 보면 같은
     *    바퀴에 J19·J20 이 함께 흔들렸을 때 한쪽 엣지를 놓친다. */
    if (__HAL_GPIO_EXTI_GET_IT(PIN_J19) != RESET) {
        __HAL_GPIO_EXTI_CLEAR_IT(PIN_J19);
        if (s_sol != NULL) {
            mk_solctl_on_edge(s_sol, MK_SOL_J19, read_raw(PIN_J19),
                              mk_time_ms());
        }
    }
    if (__HAL_GPIO_EXTI_GET_IT(PIN_J20) != RESET) {
        __HAL_GPIO_EXTI_CLEAR_IT(PIN_J20);
        if (s_sol != NULL) {
            mk_solctl_on_edge(s_sol, MK_SOL_J20, read_raw(PIN_J20),
                              mk_time_ms());
        }
    }
}
