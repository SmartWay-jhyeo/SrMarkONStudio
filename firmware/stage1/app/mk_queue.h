/* 채널별 표본 큐 — HAL 비의존.
 *
 * 🔴 채널마다 **독립된** 큐를 둔다. 하나로 합치면 센서 하나가 폭주할 때
 *    다른 채널의 표본까지 밀려나간다. 설계 원칙(CLAUDE.md §3)의 채널 장애
 *    격리가 여기서 시작된다 — J5 가 죽어도 J3 는 계속 나가야 한다.
 *
 * 🔴 가득 차면 **가장 오래된 것을 버린다.** 새것을 버리지 않는다.
 *
 *    호스트가 잠깐 느려졌을 때 사용자가 보고 싶은 것은 지금 값이지 3초 전
 *    값이 아니다. 반대로 하면 링크가 막힌 동안 큐가 과거에 고정되고,
 *    풀린 뒤에도 한참 동안 낡은 값을 흘려보낸다.
 *
 * 🔴 버린 개수를 센다. Q2 에서 UART 유실이 ~2% 났는데 어디서 나는지 몰라
 *    8개 가설이 전부 기각된 채로 미해결이다(CLAUDE.md §1.2). 그 일을
 *    되풀이하지 않으려면 버린 자리에서 세어야 한다. `$STAT` 이 이 수를
 *    채널별로 보고한다(규격 §7.4).
 */
#ifndef MK_QUEUE_H
#define MK_QUEUE_H

#include <stddef.h>
#include <stdint.h>

/* 표본 하나.
 *
 * 🔴 `t_ms` 는 **획득 시각**이다. 호스트가 받은 시각이 아니라 보드가
 *    DRDY 를 본 시각이며, 설계 원칙 2 (CLAUDE.md §3) 그 자체다.
 *    이 값을 나중에 덮어쓰는 코드가 있으면 설계 위반이다.
 *
 * 🔴 `raw` 는 24비트 2의 보수를 부호확장한 값이라 부호가 있다.
 *    부호 없는 타입에 담으면 음수 입력이 800만 근처의 큰 수로 보인다. */
typedef struct {
    int64_t t_ms;
    int32_t raw;
} MkSample;

typedef struct {
    MkSample *buf;
    uint16_t  cap;      /* buf 의 칸 수 */
    uint16_t  head;     /* 다음에 쓸 자리 */
    uint16_t  count;    /* 지금 들어 있는 수 */
    uint16_t  peak;     /* 여태 최고 count — 여유가 얼마나 빠듯했는지 */
    uint32_t  drops;    /* 넘쳐서 버린 수 */
} MkQueue;

/* 저장소를 붙인다. cap 은 buf 의 칸 수다. */
void mk_queue_init(MkQueue *q, MkSample *buf, uint16_t cap);

/* 🔴 [검토 지적 I3] `push`(ISR)와 `pop`(슈퍼루프)가 같은 `q->count` 를
 *    함께 건드린다. 둘 다 읽고-고치고-쓰는 세 단계(LDRH/SUB/STRH 류)라,
 *    pop 의 로드와 스토어 사이에 push 의 ISR 이 끼면 push 의 증가분이
 *    그대로 사라진다 — 큐에 든 항목 하나가 영구히 고아가 된다.
 *
 *    ADS1256 표본이면 그래프에 점 하나가 비지만, 디지털 입력(J18~J20)
 *    엣지가 사라지면 확정 상태가 틀린 채로 다음 진짜 변화까지 고정된다
 *    — `$STAT` 도 화면도 같이 틀린 말을 한다.
 *
 *    고치는 방법은 PRIMASK 임계구역인데, `app/` 은 `__disable_irq()` 조차
 *    모른다(CMSIS 라 HAL 경계 밖이 아니라 이 저장소가 그은 경계 밖이다).
 *    그래서 진입/이탈을 함수 포인터로 **주입**한다 — bsp 가 실제 PRIMASK
 *    구현(`bsp/mk_critsec.c`)을 이 자리에 꽂는다.
 *
 *    기본은 아무것도 안 하는 구현이다. 호스트 시험은 단일 스레드라 그걸로
 *    충분하고, 등록하지 않은 채로 링크되는 test_queue.exe 등이 깨지지
 *    않는 이유이기도 하다. */
typedef void (*MkQueueCritFn)(void);

/* count 를 건드리는 구간을 감쌀 진입/이탈 함수를 등록한다. 어느 쪽이든
 * NULL 이면 그 함수는 아무것도 안 하는 기본값으로 되돌아간다(둘 다 NULL
 * 이면 mk_queue_init() 이전 상태 = 무보호로 복귀 — 시험이 다음 시험에
 * 영향을 남기지 않으려 이렇게 되돌릴 수 있어야 한다). */
void mk_queue_set_critical_section(MkQueueCritFn enter, MkQueueCritFn exit_fn);

/* 표본 하나를 넣는다. 가득 차 있으면 가장 오래된 것을 버리고 넣는다.
 * 버렸으면 0, 그냥 들어갔으면 1 을 돌려준다. */
int mk_queue_push(MkQueue *q, int64_t t_ms, int32_t raw);

/* 가장 오래된 표본을 꺼낸다. 비었으면 0, 꺼냈으면 1. */
int mk_queue_pop(MkQueue *q, MkSample *out);

/* 지금 들어 있는 수 / 여태 최고치 / 버린 누계. */
uint16_t mk_queue_count(const MkQueue *q);
uint16_t mk_queue_peak(const MkQueue *q);
uint32_t mk_queue_drops(const MkQueue *q);

/* 내용을 비운다. **peak 과 drops 는 지우지 않는다.**
 *
 * 🔴 그 둘은 진단 기록이다. 큐를 비우는 것은 흔한 일이고(재연결, 설정
 *    변경), 그때마다 유실 이력이 사라지면 "아까 유실이 있었나" 를 영영
 *    알 수 없다. 지우려면 mk_queue_reset_stats 를 따로 부른다. */
void mk_queue_clear(MkQueue *q);
void mk_queue_reset_stats(MkQueue *q);

#endif /* MK_QUEUE_H */
