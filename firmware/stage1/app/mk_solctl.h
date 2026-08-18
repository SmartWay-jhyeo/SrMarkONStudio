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
 * 🔴 ISR 은 판단하지 않는다. bsp/mk_sol.c 가 핀 상태(raw)와 시각만 잡아
 *    `mk_solctl_on_edge()` 로 큐에 넣고 즉시 나온다. 디바운스·극성 반전은
 *    전부 `mk_solctl_tick()` 이 슈퍼루프에서 한다 — 이 저장소가 ADS1256
 *    에서 지킨 규칙과 같다(수집은 인터럽트, 판단은 슈퍼루프).
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
    int64_t  t_ms;           /* 엣지를 잡은 시각 */
} MkSolOut;

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

    /* 디바운스 중인 후보값. */
    uint8_t  has_candidate[MK_SOL_COUNT];
    uint8_t  candidate[MK_SOL_COUNT];        /* raw(0/1), 반전 전 */
    int64_t  candidate_t_ms[MK_SOL_COUNT];   /* 후보가 이 값으로 굳기 시작한 시각 */

    /* 확정된(반전 후) 상태. $STAT 이 이것을 읽는다. */
    uint8_t  confirmed_valid[MK_SOL_COUNT];
    uint8_t  confirmed_state[MK_SOL_COUNT];

    /* 이번 tick 에서 나갈 레코드. 채널마다 한 바퀴에 최대 하나다. */
    MkSolOut out[MK_SOL_COUNT];
    int      n_out;
    int      out_head;
    uint32_t out_dropped;   /* out 버퍼가 넘친 횟수 — 정상 경로로는 안 온다 */
} MkSolCtl;

void mk_solctl_init(MkSolCtl *sc);

/* 채널 -> 커넥터 번호(18~20). 범위 밖이면 0. */
unsigned mk_sol_connector_of(MkSolCh ch);

/* 🔴 ISR 이 부른다. 판단하지 않는다 — raw 핀 상태와 시각을 큐에 넣기만
 *    한다. 큐가 가득 차 있으면 mk_queue_push() 의 규칙대로 가장 오래된
 *    것을 버리고 drops 를 센다(mk_queue_drops 로 조회). */
void mk_solctl_on_edge(MkSolCtl *sc, MkSolCh ch, int raw_level, int64_t t_ms);

/* 🔴 부팅 때 한 번, bsp 가 실제 핀을 동기적으로 읽어서 부른다. ISR 을
 *    거치지 않고 즉시 확정 상태를 세운다 — 디바운스를 기다리지 않는다.
 *    이 호출은 **레코드를 내지 않는다**. 규격 §7.6 이 "막 연결한 호스트는
 *    지금 상태를 모른다, `$STAT` 이 그 공백을 채운다"고 못박은 대로,
 *    초기값은 텔레메트리가 아니라 `$STAT` 으로만 전해진다. */
void mk_solctl_prime(MkSolCtl *sc, MkSolCh ch, int raw_level, int64_t t_ms);

/* 큐를 비우고 디바운스를 진행한다. `sol.debounce_ms` 설정값(없으면 5)
 * 만큼 후보가 안정되면 상태를 확정하고, 이전과 다르면 레코드를 하나
 * 쌓는다. 매 바퀴 불러도 된다. */
void mk_solctl_tick(MkSolCtl *sc, MkConfig *cfg, int64_t now_ms);

/* 나갈 레코드를 하나 꺼낸다. 있으면 1, 없으면 0. */
int mk_solctl_take(MkSolCtl *sc, MkSolOut *out);

/* 지금 확정된 상태(반전 후, 1 = 켜짐). 아직 확정된 적이 없으면 0.
 * `$STAT` 이 이것을 싣는다(규격 §7.4). */
int mk_solctl_is_on(const MkSolCtl *sc, MkSolCh ch);

#endif /* MK_SOLCTL_H */
