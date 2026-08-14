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
