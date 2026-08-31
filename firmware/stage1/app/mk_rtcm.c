#include "mk_rtcm.h"

#include <string.h>

/* PREFIX 에서 0xD3 만 기다리고, 머리 2바이트로 길이를 확정한 뒤 본문+CRC 를
 * 끝까지 센다. 본문 속 0xD3 은 길이 계수 덕에 프레임을 재시작하지 못한다 —
 * Q1 이 상태기계 없이 못 풀던 문제를 Q2 가 이 방식으로 풀었다. */
enum {
    ST_PREFIX = 0,
    ST_HDR,
    ST_BODY
};

/* CRC-24Q (poly 0x1864CFB, init 0, 반사 없음). 비트단위 — 표 없이도 최악
 * 1KB 프레임에 ~0.33ms(64MHz)라 슈퍼루프에서 싸다. 값의 출처와 교차 검증은
 * tests/test_rtcm.c 머리말의 파이썬 스니펫이다. */
static uint32_t crc24q(const uint8_t *p, size_t n)
{
    uint32_t crc = 0;
    for (size_t i = 0; i < n; i++) {
        crc ^= (uint32_t)p[i] << 16;
        for (int bit = 0; bit < 8; bit++) {
            crc <<= 1;
            if (crc & 0x1000000u) {
                crc ^= 0x1864CFBu;
            }
        }
    }
    return crc & 0xFFFFFFu;
}

void mk_rtcm_init(MkRtcm *r, MkRtcmSink sink, void *sink_ctx)
{
    memset(r, 0, sizeof *r);
    r->state = ST_PREFIX;
    r->last_ok_ms = -1;
    r->sink = sink;
    r->sink_ctx = sink_ctx;
}

static void finish_frame(MkRtcm *r, int64_t now_ms)
{
    /* CRC 는 끝 3바이트(빅엔디언), 계산 범위는 프리앰블부터 본문 끝까지. */
    uint32_t want = ((uint32_t)r->buf[r->need - 3u] << 16) |
                    ((uint32_t)r->buf[r->need - 2u] << 8) |
                    (uint32_t)r->buf[r->need - 1u];
    r->state = ST_PREFIX;

    if (crc24q(r->buf, (size_t)r->need - 3u) != want) {
        r->crc_bad++;               /* 전선 오염의 가시화 — $STAT 로 올라간다 */
        return;
    }

    r->frames_ok++;
    r->last_ok_ms = now_ms;         /* 나이 = "온전한 보정이 오는가" — 전달
                                     * 실패는 drop 이 따로 말한다 */

    /* 🔴 반쪽 프레임은 절대 내보내지 않는다 — 통째로 넘기고, 거절이면 통째로
     *    버린다. 재시도도 없다: 보정은 초마다 다시 오고, 출구(송신 링)가
     *    막힌 동안의 재시도는 막힘을 연장할 뿐이다. */
    if (r->sink == NULL ||
        !r->sink(r->sink_ctx, (const char *)r->buf, (size_t)r->need)) {
        r->drop++;
    }
}

void mk_rtcm_feed(MkRtcm *r, uint8_t b, int64_t now_ms)
{
    r->bytes_in++;

    /* 프레임 도중의 긴 무음 = 죽은 조각. 버리고 이 바이트부터 처음처럼
     * 듣는다 — Q2 가 시한 상수를 선언만 하고 걸지 않던 결함의 고침. */
    if (r->state != ST_PREFIX &&
        now_ms - r->last_byte_ms > MK_RTCM_RESYNC_MS) {
        r->resync++;
        r->state = ST_PREFIX;
    }
    r->last_byte_ms = now_ms;

    switch (r->state) {
    case ST_PREFIX:
        if (b == 0xD3u) {
            r->buf[0] = b;
            r->have = 1;
            r->state = ST_HDR;
        }
        /* 그 외는 잡음 — 조용히 버린다. 이 링크에 명령은 없다(헤더 주석).
         * bytes_in 에는 이미 셌다: "링크가 사는가"는 잡음도 증거다. */
        break;

    case ST_HDR:
        r->buf[r->have++] = b;
        if (r->have == 3u) {
            /* 길이 10비트 — 둘째 바이트의 하위 2비트만 상위다. 구조상
             * 1023 을 넘을 수 없어 buf(1029)도 넘칠 수 없다. */
            uint32_t payload = (((uint32_t)r->buf[1] & 0x03u) << 8) |
                               (uint32_t)r->buf[2];
            r->need = (uint16_t)(payload + 6u);
            r->state = ST_BODY;
        }
        break;

    case ST_BODY:
    default:
        r->buf[r->have++] = b;
        if (r->have == r->need) {
            finish_frame(r, now_ms);
        }
        break;
    }
}

int64_t mk_rtcm_age_ms(const MkRtcm *r, int64_t now_ms)
{
    return r->last_ok_ms < 0 ? -1 : now_ms - r->last_ok_ms;
}

uint32_t mk_rtcm_bytes(const MkRtcm *r)         { return r->bytes_in; }
uint32_t mk_rtcm_frames(const MkRtcm *r)        { return r->frames_ok; }
uint32_t mk_rtcm_bad(const MkRtcm *r)           { return r->crc_bad; }
uint32_t mk_rtcm_resyncs(const MkRtcm *r)       { return r->resync; }
uint32_t mk_rtcm_drops(const MkRtcm *r)         { return r->drop; }
uint32_t mk_rtcm_link_overruns(const MkRtcm *r) { return r->link_overruns; }

void mk_rtcm_set_link_overruns(MkRtcm *r, uint32_t n)
{
    r->link_overruns = n;
}
