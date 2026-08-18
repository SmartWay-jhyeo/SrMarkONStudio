#include "mk_queue.h"

#include <string.h>

/* 기본값 — 아무것도 안 한다. mk_queue_set_critical_section() 이 아직
 * 안 불렸거나(호스트 시험) NULL 로 되돌렸을 때 여기로 떨어진다. */
static void mk_queue_crit_noop(void) {}

static MkQueueCritFn s_crit_enter = mk_queue_crit_noop;
static MkQueueCritFn s_crit_exit  = mk_queue_crit_noop;

void mk_queue_set_critical_section(MkQueueCritFn enter, MkQueueCritFn exit_fn)
{
    s_crit_enter = (enter != NULL) ? enter : mk_queue_crit_noop;
    s_crit_exit  = (exit_fn != NULL) ? exit_fn : mk_queue_crit_noop;
}

void mk_queue_init(MkQueue *q, MkSample *buf, uint16_t cap)
{
    memset(q, 0, sizeof *q);
    q->buf = buf;
    q->cap = cap;
}

int mk_queue_push(MkQueue *q, int64_t t_ms, int32_t raw)
{
    if (q->buf == NULL || q->cap == 0u) {
        /* 저장소가 없으면 넣을 곳이 없다. 그래도 버린 것은 버린 것이다 —
         * 세지 않으면 "유실이 없었다" 로 보인다. */
        q->drops++;
        return 0;
    }

    /* 🔴 [I3] count 를 읽고-고치고-쓰는 전체를 감싼다. 이 함수는 ISR 안에서
     *    불리므로 슈퍼루프(mk_queue_pop)가 같은 count 를 만지는 도중에
     *    끼어들 수 있다 — 임계구역이 없으면 pop 의 로드 뒤에 이 증가가
     *    끼었다가 pop 의 스토어가 그것을 덮어써 사라진다. */
    s_crit_enter();

    int dropped = 0;
    if (q->count == q->cap) {
        /* 가장 오래된 것을 버린다. head 가 곧 가장 오래된 자리이므로
         * 덮어쓰고 head 만 밀면 된다. */
        q->drops++;
        q->count--;
        dropped = 1;
    }

    q->buf[q->head].t_ms = t_ms;
    q->buf[q->head].raw = raw;
    q->head = (uint16_t)((q->head + 1u) % q->cap);
    q->count++;

    if (q->count > q->peak) {
        q->peak = q->count;
    }

    s_crit_exit();
    return dropped ? 0 : 1;
}

int mk_queue_pop(MkQueue *q, MkSample *out)
{
    /* 🔴 [I3] 여기가 실제 사고가 나는 자리다 — 슈퍼루프에서 돌아 우선순위가
     *    가장 낮으므로, count 를 읽은 뒤(LDRH) 다시 쓰기 전(STRH) 사이에
     *    push 의 ISR 이 끼어들 여지가 가장 크다. 진입/이탈로 그 창을
     *    닫는다. 빈 큐라도 이탈을 반드시 부른다 — 이른 반환에서 건너뛰면
     *    다음 진입 때 저장된 PRIMASK 가 어긋난다(bsp/mk_critsec.c 참고). */
    s_crit_enter();
    if (q->count == 0u) {
        s_crit_exit();
        return 0;
    }
    /* 가장 오래된 자리 = head 에서 count 만큼 뒤로. */
    uint16_t tail = (uint16_t)((q->head + q->cap - q->count) % q->cap);
    if (out != NULL) {
        *out = q->buf[tail];
    }
    q->count--;
    s_crit_exit();
    return 1;
}

uint16_t mk_queue_count(const MkQueue *q) { return q->count; }
uint16_t mk_queue_peak(const MkQueue *q)  { return q->peak; }
uint32_t mk_queue_drops(const MkQueue *q) { return q->drops; }

void mk_queue_clear(MkQueue *q)
{
    q->head = 0u;
    q->count = 0u;
    /* peak·drops 는 그대로 둔다 — 헤더의 이유 참조. */
}

void mk_queue_reset_stats(MkQueue *q)
{
    q->peak = q->count;
    q->drops = 0u;
}
