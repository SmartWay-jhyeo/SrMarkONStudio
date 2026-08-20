#include "mk_txring.h"

#include <string.h>

void mk_txring_init(MkTxRing *r, uint8_t *buf, uint16_t cap)
{
    memset(r, 0, sizeof *r);
    r->buf = buf;
    r->cap = cap;
}

size_t mk_txring_used(const MkTxRing *r)
{
    if (r->cap == 0u) {
        return 0u;
    }
    /* 🔴 두 색인을 **한 번씩만** 읽어 지역변수에 담는다. 소비자(ISR)가
     *    사이에 tail 을 움직이면 두 번 읽은 값이 서로 안 맞아 결과가
     *    음수(→ 65000 대)로 돈다. */
    uint16_t h = r->head;
    uint16_t t = r->tail;
    return (size_t)((uint16_t)((h + r->cap - t) % r->cap));
}

size_t mk_txring_free(const MkTxRing *r)
{
    if (r->cap == 0u) {
        return 0u;
    }
    return (size_t)(r->cap - 1u) - mk_txring_used(r);
}

int mk_txring_push(MkTxRing *r, const void *data, size_t len, size_t reserve)
{
    /* 넣을 것이 없는 것은 유실이 아니다 — 세지 않는다. */
    if (data == NULL || len == 0u) {
        return 0;
    }
    if (r->buf == NULL || r->cap == 0u) {
        /* 저장소가 없어도 버린 것은 버린 것이다. mk_queue 와 같은 이유 —
         * 세지 않으면 "유실이 없었다" 로 보이고, 안 나가는 이유를 영영
         * 못 찾는다. */
        r->drops++;
        r->dropped_bytes += (uint32_t)len;
        return 0;
    }

    /* 🔴 통째로 들어가야만 넣는다. 조각내면 NDJSON 한 줄이 끊긴다
     *    (헤더의 계약). 예약 몫은 명령 응답이 나갈 자리다. */
    if (len + reserve > mk_txring_free(r)) {
        r->drops++;
        r->dropped_bytes += (uint32_t)len;
        return 0;
    }

    const uint8_t *src = (const uint8_t *)data;
    uint16_t h = r->head;
    size_t first = (size_t)(r->cap - h);
    if (first > len) {
        first = len;
    }
    memcpy(&r->buf[h], src, first);
    if (len > first) {
        memcpy(&r->buf[0], src + first, len - first);
    }

    /* 🔴 바이트를 다 옮긴 **뒤에** head 를 민다. 반대로 하면 소비자가
     *    아직 안 쓴 자리를 보낸다. bsp 쪽은 DMA 를 걸기 직전에 __DSB()
     *    로 이 저장이 메모리에 닿은 것을 보장한다. */
    r->head = (uint16_t)((h + len) % r->cap);

    /* 🔴 최고치는 생산자만 갱신한다. 소비자(ISR)는 used 를 줄이기만 하므로
     *    여기서 읽은 값이 낡아도 실제보다 작게 나올 뿐이다 — 잠금이 필요
     *    없고, 틀리는 방향이 안전하다(과장하지 않는다). */
    uint16_t used_now = (uint16_t)mk_txring_used(r);
    if (used_now > r->peak) {
        r->peak = used_now;
    }
    return 1;
}

size_t mk_txring_chunk(const MkTxRing *r, const uint8_t **out)
{
    if (r->buf == NULL || r->cap == 0u) {
        return 0u;
    }
    size_t used = mk_txring_used(r);
    if (used == 0u) {
        return 0u;
    }
    uint16_t t = r->tail;
    size_t contiguous = (size_t)(r->cap - t);
    size_t n = (used < contiguous) ? used : contiguous;
    if (out != NULL) {
        *out = &r->buf[t];
    }
    return n;
}

void mk_txring_consume(MkTxRing *r, size_t n)
{
    if (r->cap == 0u || n == 0u) {
        return;
    }
    size_t used = mk_txring_used(r);
    if (n > used) {
        n = used;              /* 헤더의 이유 — 넘어가면 링이 통째로 망가진다 */
    }
    r->tail = (uint16_t)((r->tail + n) % r->cap);
}

uint16_t mk_txring_peak(const MkTxRing *r)          { return r->peak; }
uint32_t mk_txring_drops(const MkTxRing *r)         { return r->drops; }
uint32_t mk_txring_dropped_bytes(const MkTxRing *r) { return r->dropped_bytes; }
