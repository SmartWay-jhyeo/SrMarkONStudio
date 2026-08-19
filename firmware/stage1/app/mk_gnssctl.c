#include "mk_gnssctl.h"

#include <string.h>

void mk_gnssctl_init(MkGnssCtl *c)
{
    memset(c, 0, sizeof *c);
}

void mk_gnssctl_tick(MkGnssCtl *c, int enabled, int sentence_seen,
                     int64_t now_ms, MkGnssSend send, void *ctx)
{
    /* 🔴 enabled 여부와 무관하게 매 tick 최신값을 반영한다. mk_gnss 의
     *    sentences_seen_count 는 부팅 이후 누적이라 gnss.enabled 를 꺼도
     *    내려가지 않는다 — $STAT 이 "받은 적 있는가"를 물으면 꺼진
     *    동안에도 그 사실을 잃으면 안 된다. */
    c->sentence_seen_cached = sentence_seen;

    if (!enabled) {
        /* 다음에 켜질 때 처음부터 다시 센다. */
        c->attempts = 0u;
        c->last_attempt_ms = 0;
        c->done = 0;
        return;
    }

    if (sentence_seen) {
        /* 🔴 재시도 간격을 기다리지 않고 즉시 멈춘다 — 이미 말하고 있는
         *    모듈에 계속 명령을 보낼 이유가 없다. */
        c->done = 1;
        return;
    }
    if (c->done) {
        return;                     /* 방어적 — sentence_seen 이 다시
                                      * 거짓으로 흔들려도 재개하지 않는다 */
    }

    if (c->attempts >= MK_GNSSCTL_MAX_ATTEMPTS) {
        return;                     /* 재시도 상한 — mk_gnssctl_exhausted() */
    }
    if (c->attempts > 0u &&
        now_ms - c->last_attempt_ms < (int64_t)MK_GNSSCTL_RETRY_INTERVAL_MS) {
        return;                     /* 아직 재시도할 때가 아니다 */
    }
    if (send == NULL) {
        return;                     /* 보낼 길이 없다(1단계 빌드 등) */
    }

    /* 🔴 시각에 필요한 최소 문장 둘만 보낸다(규격 §4.1.1) — SAVECONFIG 는
     *    여기서 절대 보내지 않는다(이 파일 상단 주석, CLAUDE.md 규칙).
     *
     *    🔴 줄 끝 CR/LF 는 여기(app/, HAL 비의존)가 붙인다 — send 콜백
     *    (bsp/mk_gnss_io.c 의 mk_gnss_io_write)은 받은 바이트를 그대로
     *    내보내기만 하는 얇은 관이다. 그래야 이 붙이는 로직이 호스트에서
     *    시험된다 — bsp 는 HAL 이 있어야 링크되므로 이 저장소에서
     *    돌릴 수 없다. `$GNSS` 명령 전달(mk_hostlink.c 의 on_gnss)도 같은
     *    규칙을 따른다. 길이는 리터럴이라 sizeof-1 로 안전하게 구한다. */
    send(ctx, "LOG GPRMC ONTIME 1\r\n", sizeof "LOG GPRMC ONTIME 1\r\n" - 1u);
    send(ctx, "LOG GPGGA ONTIME 1\r\n", sizeof "LOG GPGGA ONTIME 1\r\n" - 1u);

    c->attempts++;
    c->last_attempt_ms = now_ms;
}

int mk_gnssctl_sent(const MkGnssCtl *c)
{
    return c->attempts > 0u ? 1 : 0;
}

int mk_gnssctl_exhausted(const MkGnssCtl *c)
{
    return (!c->done && c->attempts >= MK_GNSSCTL_MAX_ATTEMPTS) ? 1 : 0;
}

int mk_gnssctl_sentence_seen(const MkGnssCtl *c)
{
    return c->sentence_seen_cached ? 1 : 0;
}
