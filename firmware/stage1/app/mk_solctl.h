/* 디지털 입력 J18~J20 — HAL 비의존.
 *
 * 🔴 방향이 뒤집혔다(사용자 확정 2026-08-18). 넷리스트를 다시 보면 커넥터
 *    pin1 이 MCU 핀에 직결이고 사이에 부품이 없다 — 옵토커플러가 커넥터
 *    반대편(외부)에 붙고, **보드는 그 신호를 읽는다.** 출력으로 잡으면
 *    반대편 포토트랜지스터와 맞선다.
 *
 * 🔴 극성 (참고 구현 LaneControlSystemQ2 의 markonsync_mgr — PC817 배선):
 *
 *      옵토 LED 켜짐(신호 있음) -> 포토트랜지스터 도통 -> 핀을 GND 로
 *      끌어내림  =>  raw LOW = 신호 있음
 *      신호 없음 -> 내부 풀업이 핀을 HIGH 로 잡음     =>  raw HIGH = 꺼짐
 *
 *    전기적으로는 로우 액티브지만, 규격 §7.6 의 `state` 는 "1 = 켜짐" 이
 *    전선에 나가는 유일한 뜻이다. 옵토가 로우 액티브라는 하드웨어 사실이
 *    호스트까지 새어 나오면 안 된다(설계 원칙 1과 같은 결).
 *
 * 🔴 상태의 근거는 **핀 레벨**이지 엣지가 아니다(사용자 확정 2026-08-18 —
 *    "엣지만 보는게 아니라 그 상태를 읽어야 하는거야 엣지는 딱 감지하려고
 *    하는거고"). `mk_solctl_tick()` 이 매 바퀴 `read()` 로 세 핀을 직접
 *    읽어 디바운스와 확정을 그 값에 건다. 엣지 큐(`mk_solctl_on_edge()`)는
 *    남아 있지만 역할이 다르다 — 상태를 바꾸는 근거가 아니라 **그 상태가
 *    "언제" 바뀌었는지**를 정밀하게 대는 자료다. 폴링만으로는 두 tick
 *    사이 어느 순간에 바뀌었는지 알 수 없다(폴링 주기 안에서 뭉개진다).
 *
 *    그래서 매 tick 은 두 근거를 합친다: 큐에 쌓인 엣지가 있으면 그
 *    시각을 쓰고(정밀), 없는데도 폴링된 레벨이 지금 후보와 다르면(엣지를
 *    놓쳤다는 뜻) 그 tick 의 시각으로 새 후보를 연다(폴링 시각, 정밀도는
 *    떨어지지만 상태는 맞다). ISR 은 여전히 판단하지 않는다 —
 *    `mk_solctl_on_edge()` 는 raw 값과 시각을 큐에 넣기만 한다.
 *
 * 🔴 극성 반전은 `flip_polarity()`(mk_solctl.c) **한 곳**에서만 한다.
 *    두 곳에서 뒤집으면 원래대로 돌아오고, 화면이 모든 것을 반대로
 *    말하게 된다 — 그래서 부팅 시 초기값을 세우는 `mk_solctl_prime()`
 *    도 이 함수를 통해서만 반전한다.
 */
#ifndef MK_SOLCTL_H
#define MK_SOLCTL_H

#include <stdint.h>

#include "mk_config.h"
#include "mk_queue.h"

typedef enum {
    MK_SOL_J18 = 0,
    MK_SOL_J19,
    MK_SOL_J20,
    MK_SOL_COUNT
} MkSolCh;

/* 엣지 큐 한 칸. ISR 이 한 바퀴에 여러 번 흔들려도(잡음) 잃지 않을
 * 만큼 넉넉히 잡는다. */
#define MK_SOL_QUEUE_CAP  16u

/* 상태가 바뀔 때 나갈 레코드 하나 (규격 §7.6). */
typedef struct {
    unsigned connector_id;   /* 18·19·20 */
    uint8_t  state;          /* 이미 반전된 값 — 1 = 켜짐 */
    int64_t  t_ms;           /* 엣지를 잡은 시각. 엣지를 놓쳤으면 대신
                               * 폴링 시각이다 — mk_solctl_tick() 주석. */
} MkSolOut;

/* 핀 하나의 raw 레벨을 즉시 돌려준다(0/1). bsp 가 HAL_GPIO_ReadPin 으로
 * 구현한다 — mk_railctl.h 의 MkRailSet 과 같은 자리다(출력은 콜백으로
 * 내보내고, 입력은 콜백으로 읽어 들인다). 매 tick 마다 불리므로 즉시
 * 돌아와야 한다. */
typedef int (*MkSolRead)(void *ctx, MkSolCh ch);

/* 🔴 태그를 둔다(익명 struct 가 아니다). mk_hostlink.h·mk_telem.h 가
 *    `struct MkSolCtl *` 로 전방 선언해 HAL 처럼 무거운 include 없이
 *    포인터만 받는다 — mk_railctl.h 의 MkRailCtl·mk_ads1256.h 의 MkAds
 *    와 같은 관례다. 익명으로 두면 전방 선언의 태그와 이 타입이 서로
 *    다른 타입이 되어, MSVC 가 "형식이 호환되지 않습니다"(C4133/WX) 로
 *    빌드를 세운다. */
typedef struct MkSolCtl {
    /* ISR -> tick 사이의 원시 표본 큐. raw(0/1)를 MkSample.raw 에 담는다 —
     * 24비트 ADC 표본과 값의 뜻은 다르지만 큐 자료구조는 그대로 쓸 수
     * 있다(채널 격리·최고치·drops 를 다시 구현하지 않는다). */
    MkQueue  q[MK_SOL_COUNT];
    MkSample q_buf[MK_SOL_COUNT][MK_SOL_QUEUE_CAP];

    /* 매 tick 세 핀을 읽는 콜백. NULL 이면(주로 시험) 레벨 폴링을 건너뛰고
     * 예전처럼 엣지 큐만 본다 — "폴링을 빼면 자가회복이 깨진다"는 되돌림
     * 시험이 바로 이 경로를 쓴다(tests/test_sol.c). */
    MkSolRead read;
    void     *ctx;

    /* 디바운스 중인 후보값. */
    uint8_t  has_candidate[MK_SOL_COUNT];
    uint8_t  candidate[MK_SOL_COUNT];        /* raw(0/1), 반전 전 */
    int64_t  candidate_t_ms[MK_SOL_COUNT];   /* 후보가 이 값으로 굳기 시작한 시각.
                                               * 엣지에서 왔으면 정밀, 폴링에서
                                               * 왔으면(엣지를 놓쳤으면) 그
                                               * tick 의 now_ms 다. */

    /* 확정된(반전 후) 상태. $STAT 이 이것을 읽는다. */
    uint8_t  confirmed_valid[MK_SOL_COUNT];
    uint8_t  confirmed_state[MK_SOL_COUNT];

    /* 이번 tick 에서 나갈 레코드. 채널마다 한 바퀴에 최대 하나다. */
    MkSolOut out[MK_SOL_COUNT];
    int      n_out;
    int      out_head;
    uint32_t out_dropped;   /* out 버퍼가 넘친 횟수 — 정상 경로로는 안 온다 */
} MkSolCtl;

/* `read`(bsp 의 핀 읽기)를 등록한다. `ctx` 는 그대로 `read` 에 돌려준다.
 * `read` 를 NULL 로 주면(호스트 시험 대부분) 레벨 폴링 없이 예전처럼
 * 엣지 큐만으로 디바운스한다 — mk_solctl_tick() 의 되돌림 시험이 이 경로를
 * 확인한다. */
void mk_solctl_init(MkSolCtl *sc, MkSolRead read, void *ctx);

/* 채널 -> 커넥터 번호(18~20). 범위 밖이면 0. */
unsigned mk_sol_connector_of(MkSolCh ch);

/* 🔴 ISR 이 부른다. 판단하지 않는다 — raw 핀 상태와 시각을 큐에 넣기만
 *    한다. 큐가 가득 차 있으면 mk_queue_push() 의 규칙대로 가장 오래된
 *    것을 버리고 drops 를 센다(mk_queue_drops 로 조회). 이제 상태를
 *    바꾸는 근거는 아니다 — mk_solctl_tick() 이 그날 그 채널의 폴링된
 *    레벨과 맞는지만 이 큐로 확인해 "언제" 를 정밀하게 잡는 데 쓴다. */
void mk_solctl_on_edge(MkSolCtl *sc, MkSolCh ch, int raw_level, int64_t t_ms);

/* 🔴 부팅 때 한 번, bsp 가 실제 핀을 동기적으로 읽어서 부른다. 매 tick
 *    레벨을 폴링하는 지금도 이 함수가 남아 있는 이유는 하나다 — 첫
 *    `mk_solctl_tick()` 이 돌 때까지 기다리면 그 사이(부팅 직후 첫
 *    디바운스 구간)에는 `$STAT` 이 "아직 모름"을 답하게 된다. `prime()`
 *    은 디바운스 없이 즉시 확정해 그 공백을 없앤다. 이 호출은 **레코드를
 *    내지 않는다** — 규격 §7.6 이 "막 연결한 호스트는 지금 상태를 모른다,
 *    `$STAT` 이 그 공백을 채운다"고 못박은 대로, 초기값은 텔레메트리가
 *    아니라 `$STAT` 으로만 전해진다. */
void mk_solctl_prime(MkSolCtl *sc, MkSolCh ch, int raw_level, int64_t t_ms);

/* 큐를 비우고(정밀 시각 확보) `read` 로 세 핀의 지금 레벨을 폴링해
 * (등록돼 있으면) 디바운스를 진행한다. `sol.debounce_ms` 설정값(없으면
 * 5) 만큼 후보가 안정되면 상태를 확정하고, 이전과 다르면 레코드를 하나
 * 쌓는다. 매 바퀴 불러도 된다.
 *
 * 🔴 폴링된 레벨이 지금 후보와 다르면(엣지를 놓쳤다는 뜻) 이 tick 의
 *    시각으로 새 후보를 연다 — 정밀도는 폴링 주기만큼 떨어지지만, 엣지를
 *    영영 하나 잃어도 다음 안정 구간에서 반드시 회복한다. */
void mk_solctl_tick(MkSolCtl *sc, MkConfig *cfg, int64_t now_ms);

/* 나갈 레코드를 하나 꺼낸다. 있으면 1, 없으면 0. */
int mk_solctl_take(MkSolCtl *sc, MkSolOut *out);

/* 지금 확정된 상태(반전 후, 1 = 켜짐). 아직 확정된 적이 없으면 0.
 * `$STAT` 이 이것을 싣는다(규격 §7.4). */
int mk_solctl_is_on(const MkSolCtl *sc, MkSolCh ch);

#endif /* MK_SOLCTL_H */
