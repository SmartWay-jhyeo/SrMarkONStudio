/* mk_rtcm 단위 시험 — 젯슨 링크 RTCM3 라우터의 프레임 경계·CRC·전달, HAL 비의존.
 *
 * 대상: firmware/stage1/app/mk_rtcm.c
 *
 * 벡터 출처 — 작은 프레임의 CRC 는 구현과 독립인 파이썬으로 계산해 박았다
 * (시험과 구현이 같은 코드를 쓰면 같은 버그를 공유한다). 스니펫:
 *
 *     def crc24q(data):
 *         crc = 0
 *         for b in data:
 *             crc ^= b << 16
 *             for _ in range(8):
 *                 crc <<= 1
 *                 if crc & 0x1000000: crc ^= 0x1864CFB
 *         return crc & 0xFFFFFF
 *
 *     crc24q(b"123456789") == 0xCDE703   ← 아래 ref_crc24q 교차 검증 상수
 *
 * 최대 길이(1023) 프레임만은 시험 안의 ref_crc24q 로 만든다 — 이 경우 CRC 값
 * 자체가 아니라 "1029바이트를 끝까지 세는가"(프레임 경계)를 보는 것이고,
 * ref_crc24q 의 정확성은 위 파이썬 상수와의 대조가 담보한다.
 */
#include <stdio.h>
#include <string.h>
#include "../app/mk_rtcm.h"

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
    if (!(cond)) { printf("  FAIL %s\n", msg); failures++; }    \
    else         { printf("  ok   %s\n", msg); }                \
} while (0)

/* ---- 파이썬 계산 벡터 ---------------------------------------------------- */

/* 길이 0 프레임 — 머리 셋 + CRC 셋. 규격상 유효하다. */
static const uint8_t F_EMPTY[6] = { 0xd3, 0x00, 0x00, 0x47, 0xea, 0x4b };

/* 본문 4바이트 프레임(1005형 머리 모양) — CRC 는 파이썬 값. */
static const uint8_t F_SMALL[10] =
    { 0xd3, 0x00, 0x04, 0x3e, 0xd0, 0xaa, 0x55, 0x58, 0x2c, 0x06 };

/* F_SMALL 의 본문 한 바이트(0xd0→0x2f)를 뒤집은 것 — CRC 가 어긋난다. */
static const uint8_t F_BAD[10] =
    { 0xd3, 0x00, 0x04, 0x3e, 0x2f, 0xaa, 0x55, 0x58, 0x2c, 0x06 };

/* ---- 시험용 독립 CRC (최대 길이 프레임 생성에만 쓴다) --------------------- */

static uint32_t ref_crc24q(const uint8_t *p, size_t n)
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

/* ---- 전달된 프레임을 받는 통 --------------------------------------------- */

static uint8_t CAPTURED[MK_RTCM_MAX_FRAME];
static size_t  CAP_LEN;
static int     CALLS;
static int     ACCEPT;

static void sink_reset(void) { CAP_LEN = 0; CALLS = 0; ACCEPT = 1; }

static int fake_sink(void *ctx, const char *data, size_t len)
{
    (void)ctx;
    CALLS++;
    if (!ACCEPT) { return 0; }
    if (len <= sizeof CAPTURED) {
        memcpy(CAPTURED, data, len);
        CAP_LEN = len;
    }
    return 1;
}

static void feed_all(MkRtcm *r, const uint8_t *p, size_t n, int64_t t)
{
    for (size_t i = 0; i < n; i++) {
        mk_rtcm_feed(r, p[i], t);
    }
}

/* ---- 기본 상태 ----------------------------------------------------------- */

static void test_starts_empty(void)
{
    MkRtcm r;
    mk_rtcm_init(&r, fake_sink, NULL);
    CHECK(mk_rtcm_age_ms(&r, 99999) == -1,
          "한 프레임도 없으면 나이는 -1 이다 — 0 을 지어내지 않는다");
    CHECK(mk_rtcm_bytes(&r) == 0 && mk_rtcm_frames(&r) == 0 &&
          mk_rtcm_bad(&r) == 0 && mk_rtcm_resyncs(&r) == 0 &&
          mk_rtcm_drops(&r) == 0 && mk_rtcm_link_overruns(&r) == 0,
          "계수기는 전부 0 에서 시작한다");
}

static void test_reference_crc_matches_python(void)
{
    /* 시험 안의 ref_crc24q 가 파이썬과 같은 답을 내는지 먼저 못박는다 —
     * 최대 길이 시험이 이 함수로 프레임을 만들기 때문이다. */
    CHECK(ref_crc24q((const uint8_t *)"123456789", 9) == 0xCDE703u,
          "ref_crc24q('123456789') == 0xCDE703 (파이썬 대조)");
}

/* ---- 온전한 프레임 -------------------------------------------------------- */

static void test_whole_frame_in_one_burst(void)
{
    MkRtcm r;
    mk_rtcm_init(&r, fake_sink, NULL);
    sink_reset();
    feed_all(&r, F_SMALL, sizeof F_SMALL, 1000);
    CHECK(CALLS == 1, "프레임 하나 = sink 호출 한 번 (바이트별 호출 금지)");
    CHECK(CAP_LEN == sizeof F_SMALL, "프리앰블·머리·CRC 까지 통째로 나간다");
    CHECK(memcmp(CAPTURED, F_SMALL, sizeof F_SMALL) == 0, "바이트가 그대로다");
    CHECK(mk_rtcm_frames(&r) == 1, "frames_ok 가 센다");
    CHECK(mk_rtcm_bytes(&r) == sizeof F_SMALL, "bytes_in 은 먹인 전량");
    CHECK(mk_rtcm_age_ms(&r, 1500) == 500, "나이 = now - 마지막 온전 프레임");
}

static void test_byte_by_byte_is_identical(void)
{
    /* 링크가 어떻게 조각내 주든 결과가 같아야 한다 — 수신 링에서 한 바이트씩
     * 꺼내 먹이는 것이 실제 배선이다. */
    MkRtcm r;
    mk_rtcm_init(&r, fake_sink, NULL);
    sink_reset();
    for (size_t i = 0; i < sizeof F_SMALL; i++) {
        mk_rtcm_feed(&r, F_SMALL[i], 1000 + (int64_t)i);
    }
    CHECK(CALLS == 1 && CAP_LEN == sizeof F_SMALL &&
          memcmp(CAPTURED, F_SMALL, sizeof F_SMALL) == 0,
          "1바이트씩 먹여도 한 번에 먹인 것과 같다");
}

static void test_zero_length_frame_is_valid(void)
{
    MkRtcm r;
    mk_rtcm_init(&r, fake_sink, NULL);
    sink_reset();
    feed_all(&r, F_EMPTY, sizeof F_EMPTY, 0);
    CHECK(CALLS == 1 && CAP_LEN == 6, "본문 0 프레임도 유효하다 (Q2 와 동일)");
    CHECK(mk_rtcm_frames(&r) == 1, "frames_ok 1");
}

static void test_max_length_frame(void)
{
    static uint8_t big[MK_RTCM_MAX_FRAME];
    big[0] = 0xd3; big[1] = 0x03; big[2] = 0xff;          /* 길이 1023 */
    for (size_t i = 0; i < 1023u; i++) {
        big[3 + i] = (uint8_t)i;
    }
    uint32_t c = ref_crc24q(big, 1026);
    big[1026] = (uint8_t)(c >> 16);
    big[1027] = (uint8_t)(c >> 8);
    big[1028] = (uint8_t)c;

    MkRtcm r;
    mk_rtcm_init(&r, fake_sink, NULL);
    sink_reset();
    feed_all(&r, big, sizeof big, 0);
    CHECK(CALLS == 1 && CAP_LEN == MK_RTCM_MAX_FRAME,
          "최대 길이(본문 1023, 전체 1029)를 끝까지 센다");
    CHECK(memcmp(CAPTURED, big, sizeof big) == 0, "내용도 그대로");
}

/* ---- 오염·잡음 ------------------------------------------------------------ */

static void test_corrupted_frame_counts_and_recovers(void)
{
    MkRtcm r;
    mk_rtcm_init(&r, fake_sink, NULL);
    sink_reset();
    feed_all(&r, F_BAD, sizeof F_BAD, 0);
    CHECK(CALLS == 0, "CRC 가 어긋난 프레임은 내보내지 않는다");
    CHECK(mk_rtcm_bad(&r) == 1, "crc_bad 가 센다 — 전선 오염의 가시화");
    CHECK(mk_rtcm_age_ms(&r, 100) == -1, "오염 프레임은 나이를 갱신하지 않는다");

    feed_all(&r, F_SMALL, sizeof F_SMALL, 200);
    CHECK(CALLS == 1 && mk_rtcm_frames(&r) == 1, "다음 프레임부터 정상 복귀");
}

static void test_noise_before_preamble_is_ignored(void)
{
    MkRtcm r;
    mk_rtcm_init(&r, fake_sink, NULL);
    sink_reset();
    static const uint8_t noise[3] = { 'a', 'b', 'c' };
    feed_all(&r, noise, sizeof noise, 0);
    CHECK(CALLS == 0 && mk_rtcm_bad(&r) == 0,
          "프리앰블 밖 잡음은 조용히 버린다 — 오류도 아니다");
    feed_all(&r, F_SMALL, sizeof F_SMALL, 10);
    CHECK(CALLS == 1, "잡음 뒤 프레임은 정상 수신");
    CHECK(mk_rtcm_bytes(&r) == 3u + sizeof F_SMALL,
          "bytes_in 은 잡음까지 센다 — '링크가 사는가'의 답이므로");
}

static void test_d3_inside_body_does_not_restart(void)
{
    /* 본문에 0xD3 이 들어 있는 프레임 — 길이로 세므로 재시작하면 안 된다.
     * (Q1 은 상태기계가 없어 이 문제를 못 풀었고, Q2 가 길이 계수로 풀었다.) */
    uint8_t f[10];
    f[0] = 0xd3; f[1] = 0x00; f[2] = 0x04;
    f[3] = 0xd3; f[4] = 0x00; f[5] = 0x00; f[6] = 0x01;   /* 본문에 D3 00 00 */
    uint32_t c = ref_crc24q(f, 7);
    f[7] = (uint8_t)(c >> 16); f[8] = (uint8_t)(c >> 8); f[9] = (uint8_t)c;

    MkRtcm r;
    mk_rtcm_init(&r, fake_sink, NULL);
    sink_reset();
    feed_all(&r, f, sizeof f, 0);
    CHECK(CALLS == 1 && CAP_LEN == 10 && memcmp(CAPTURED, f, 10) == 0,
          "본문 속 0xD3 은 프레임을 재시작하지 않는다");
}

/* ---- 무음 복귀 ------------------------------------------------------------ */

static void test_silence_mid_frame_resyncs(void)
{
    MkRtcm r;
    mk_rtcm_init(&r, fake_sink, NULL);
    sink_reset();
    feed_all(&r, F_SMALL, 5, 0);                  /* 프레임 앞 5바이트만 */
    /* 무음 뒤 새 프레임 — 첫 바이트를 먹이는 순간 죽은 조각을 버리고
     * 그 바이트부터 처음처럼 들어야 한다. */
    for (size_t i = 0; i < sizeof F_SMALL; i++) {
        mk_rtcm_feed(&r, F_SMALL[i], MK_RTCM_RESYNC_MS + 100 + (int64_t)i);
    }
    CHECK(mk_rtcm_resyncs(&r) == 1, "무음 시한이 죽은 조각을 버린다 (Q2 미구현의 고침)");
    CHECK(CALLS == 1 && mk_rtcm_frames(&r) == 1, "새 프레임은 온전히 나간다");
    CHECK(mk_rtcm_bad(&r) == 0, "조각은 CRC 실패가 아니라 resync 로 센다");
}

static void test_truncation_without_silence_costs_one_frame(void)
{
    /* 잘린 프레임 직후 무음 없이 다음 프레임이 오면, 죽은 조각이 다음
     * 프레임의 머리를 삼켜 CRC 실패 한 번을 낳고 그다음부터 복구된다.
     * 이 비용(프레임 1~2개)을 문서화하는 시험이다 — 보정은 초마다 다시
     * 오므로 감내한다. */
    MkRtcm r;
    mk_rtcm_init(&r, fake_sink, NULL);
    sink_reset();
    feed_all(&r, F_SMALL, 5, 0);                  /* 잘린 조각 */
    feed_all(&r, F_SMALL, sizeof F_SMALL, 1);     /* 머리가 삼켜진다 */
    feed_all(&r, F_SMALL, sizeof F_SMALL, 2);     /* 이것부터 복구 */
    CHECK(mk_rtcm_bad(&r) == 1, "삼켜진 자리에서 CRC 실패 한 번");
    CHECK(CALLS == 1 && mk_rtcm_frames(&r) == 1, "세 번째 프레임은 정상");
}

/* ---- 출구 거절(송신 링 만재) ---------------------------------------------- */

static void test_sink_rejection_counts_drop(void)
{
    MkRtcm r;
    mk_rtcm_init(&r, fake_sink, NULL);
    sink_reset();
    ACCEPT = 0;
    feed_all(&r, F_SMALL, sizeof F_SMALL, 700);
    CHECK(CALLS == 1, "거절이어도 시도는 한 번뿐 — 재시도 없음(보정은 곧 또 온다)");
    CHECK(mk_rtcm_drops(&r) == 1, "drop 이 센다 — 송신 링 만재의 가시화");
    CHECK(mk_rtcm_frames(&r) == 1, "CRC 는 통과했으므로 frames_ok 도 센다");
    CHECK(mk_rtcm_age_ms(&r, 1000) == 300,
          "나이는 '온전한 프레임이 왔는가'다 — 전달 실패는 drop 이 말한다");

    ACCEPT = 1;
    feed_all(&r, F_SMALL, sizeof F_SMALL, 800);
    CHECK(mk_rtcm_drops(&r) == 1 && mk_rtcm_frames(&r) == 2,
          "출구가 뚫리면 drop 은 멈추고 frames_ok 만 는다");
}

/* ---- 링크 계층 미러 -------------------------------------------------------- */

static void test_link_overrun_mirror(void)
{
    MkRtcm r;
    mk_rtcm_init(&r, fake_sink, NULL);
    mk_rtcm_set_link_overruns(&r, 7);
    CHECK(mk_rtcm_link_overruns(&r) == 7,
          "수신 링 overrun 은 배선층이 밀어넣고 $STAT 이 읽는다");
}

int main(void)
{
    printf("mk_rtcm\n");
    test_starts_empty();
    test_reference_crc_matches_python();
    test_whole_frame_in_one_burst();
    test_byte_by_byte_is_identical();
    test_zero_length_frame_is_valid();
    test_max_length_frame();
    test_corrupted_frame_counts_and_recovers();
    test_noise_before_preamble_is_ignored();
    test_d3_inside_body_does_not_restart();
    test_silence_mid_frame_resyncs();
    test_truncation_without_silence_costs_one_frame();
    test_sink_rejection_counts_drop();
    test_link_overrun_mirror();
    printf(failures ? "\nFAILED (%d)\n" : "\nPASSED\n", failures);
    return failures ? 1 : 0;
}
