#include "mk_queue.h"

#include <string.h>

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
    return dropped ? 0 : 1;
}

int mk_queue_pop(MkQueue *q, MkSample *out)
{
    if (q->count == 0u) {
        return 0;
    }
    /* 가장 오래된 자리 = head 에서 count 만큼 뒤로. */
    uint16_t tail = (uint16_t)((q->head + q->cap - q->count) % q->cap);
    if (out != NULL) {
        *out = q->buf[tail];
    }
    q->count--;
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
