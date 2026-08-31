/* 젯슨 링크에서 내려오는 RTCM3 보정 스트림의 라우터 — 프레임 경계를 세고
 * CRC-24Q 를 확인한 뒤, 온전한 프레임만 위성 모듈 쪽 송신구로 통째 넘긴다.
 *
 * 🔴 HAL 을 모른다. 바이트는 배선층(main)이 링크 수신 링에서 꺼내 먹이고,
 *    나가는 곳도 배선층이 꽂아 준 sink 콜백 하나뿐이다 — 그래서 보드 없이
 *    시험된다.
 *
 * 🔴 젯슨 링크의 수신 바이트는 이 모듈로만 온다 (사용자 결정 2026-08-28,
 *    좁은 보안 경계). 이 링크에는 인증도 줄 프레이밍도 없으므로 명령을
 *    실으면 안 된다 — 여기는 명령 해석이 아예 없고, 프레임이 아닌 바이트는
 *    조용히 버려진다. host/tests/test_firmware_safety.py 가 이 경계를
 *    문자열 수준에서 상시 강제한다.
 *
 * 원형: LaneControlSystem Q2 host_link.c 의 0xD3 상태기계. 단 그쪽의 전달
 * 결함(수신 인터럽트 안에서 바이트별 송신, 실패 무시 — 사실상 전량 유실이라
 * RTK 가 성립하지 않던 구조)은 버리고, Q1 에서 실증된 "모아서 한 번에"(링
 * 버퍼 + 메인 루프 전달, RTK Fixed ±2.6cm 달성 기록)로 바꿨다. 그쪽이
 * 선언만 하고 쓰지 않던 무음 복귀 시한도 실제로 건다.
 *
 * RTCM3 프레임 (RTCM 10403 계열, Q2 구현과 동일한 해석):
 *     0xD3 · 예약 6비트+길이 10비트(2바이트, 상위 2비트만 길이) · 본문 ≤1023
 *     · CRC-24Q 3바이트(빅엔디언). 전체 = 본문 + 6.
 * CRC-24Q: poly 0x1864CFB, init 0, 반사 없음 — 시험 파일의 파이썬 스니펫이
 * 독립 계산의 출처다.
 */
#ifndef MK_RTCM_H
#define MK_RTCM_H

#include <stddef.h>
#include <stdint.h>

/* 머리 3 + 본문 최대 1023 + CRC 3. 길이 필드가 10비트라 구조상 넘칠 수 없다. */
#define MK_RTCM_MAX_FRAME  1029u

/* 프레임 도중 이 이상 조용하면 조각을 버리고 처음부터 다시 듣는다.
 * 보정은 보통 1초 묶음으로 오므로, 1초 무음 = 그 프레임은 죽은 조각이다. */
#define MK_RTCM_RESYNC_MS  1000

/* 온전한 프레임을 통째로 받는 출구. 1 = 받았다, 0 = 자리 없음(프레임 통째
 * 버려지고 드롭으로 센다 — 반쪽 프레임은 절대 내보내지 않는다).
 * 모양을 MkGnssSend(app/mk_gnss.h)와 같게 두어 배선층이 위성 모듈 송신
 * 함수를 그대로 꽂는다. */
typedef int (*MkRtcmSink)(void *ctx, const char *data, size_t len);

typedef struct MkRtcm {
    uint8_t  buf[MK_RTCM_MAX_FRAME];
    uint16_t have;            /* 지금까지 모은 바이트 */
    uint16_t need;            /* 머리를 읽고 확정된 전체 길이(6..1029) */
    uint8_t  state;
    int64_t  last_byte_ms;    /* 무음 복귀 판정용 — 프레임 도중에만 의미 */
    int64_t  last_ok_ms;      /* 마지막 온전한 프레임 도착 시각. -1 = 아직 없다 */

    uint32_t bytes_in;        /* 먹인 모든 바이트(잡음 포함) — "링크가 사는가" */
    uint32_t frames_ok;       /* CRC 통과 프레임 수(전달 실패분 포함) */
    uint32_t crc_bad;         /* CRC 불일치로 버린 프레임 수 */
    uint32_t resync;          /* 무음 시한으로 버린 조각 수 */
    uint32_t drop;            /* CRC 는 통과했으나 sink 가 거절한 프레임 수 */
    uint32_t link_overruns;   /* 링크 수신 링이 버린 바이트 — 배선층이 미러 */

    MkRtcmSink sink;
    void      *sink_ctx;
} MkRtcm;

void mk_rtcm_init(MkRtcm *r, MkRtcmSink sink, void *sink_ctx);

/* 수신 바이트 하나를 먹인다. now_ms 는 단조 시계(mk_time_ms)다. */
void mk_rtcm_feed(MkRtcm *r, uint8_t b, int64_t now_ms);

/* 마지막 온전한 프레임 이후 경과. -1 = 아직 한 프레임도 없다 — 0 을
 * 지어내지 않는다($STAT 의 pps_age_ms 와 같은 규칙). */
int64_t mk_rtcm_age_ms(const MkRtcm *r, int64_t now_ms);

uint32_t mk_rtcm_bytes(const MkRtcm *r);
uint32_t mk_rtcm_frames(const MkRtcm *r);
uint32_t mk_rtcm_bad(const MkRtcm *r);
uint32_t mk_rtcm_resyncs(const MkRtcm *r);
uint32_t mk_rtcm_drops(const MkRtcm *r);
uint32_t mk_rtcm_link_overruns(const MkRtcm *r);
void     mk_rtcm_set_link_overruns(MkRtcm *r, uint32_t n);

#endif /* MK_RTCM_H */
