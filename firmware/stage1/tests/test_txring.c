/* mk_txring 단위 시험 — 송신 링버퍼.
 *
 * 🔴 왜 이 시험이 먼저 생겼나 (2026-08-20 실측)
 *
 *    7채널 × 10 ms, adc.drate=2000, tx.period_ms=10, 링크 1.5 Mbps 로 30초:
 *
 *        J3~J9  고유 2123~2124개씩   (3000 이어야 한다)
 *        간격   중앙 10 ms / 최대 30 ms
 *        큐     일곱 채널 모두 depth 64 peak 64 drops 853~860
 *        총     14,863줄 / 30초 = 초당 495줄   (700 이어야 한다)
 *
 *    495 ÷ 16(MK_TELEM_MAX_LINES) = 초당 31 틱 = 한 틱에 32 ms 다. 링크는
 *    67 KB/s 로 1.5 Mbps(187 KB/s)의 36 % 밖에 안 썼다 — 막고 있던 것은
 *    링크가 아니라 `HAL_UART_Transmit`(블로킹)이었다. 16줄 × 135 B 를
 *    1.5 Mbps 로 밀어내는 14 ms 동안 슈퍼루프가 통째로 서 있었다.
 *
 *    이 모듈은 그 자리를 링버퍼로 바꾼다: 쓰는 쪽은 바이트를 링에 넣고
 *    즉시 돌아오고, DMA 가 링에서 꺼내 보낸다. 여기 있는 시험은 그
 *    계약(즉시 반환·줄이 안 잘림·감김·예약 몫)을 DMA 도 보드도 없이
 *    확인한다.
 */
#include <stdio.h>
#include <string.h>
#include "../app/mk_txring.h"

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
    if (!(cond)) { printf("  FAIL %s\n", msg); failures++; }    \
    else         { printf("  ok   %s\n", msg); }                \
} while (0)

#define CAP  16u

static uint8_t BUF[CAP];
static MkTxRing R;

static void setup(void) { mk_txring_init(&R, BUF, (uint16_t)CAP); }

/* 가짜 DMA — 링에서 한 조각을 꺼내 sink 에 붙이고 소비한다. 실제
 * 완료 인터럽트가 하는 일과 같다. 돌려주는 값은 옮긴 바이트 수. */
static char SINK[256];
static size_t SINK_N;

static size_t fake_dma_step(void)
{
    const uint8_t *p = NULL;
    size_t n = mk_txring_chunk(&R, &p);
    if (n == 0u) {
        return 0u;
    }
    for (size_t i = 0; i < n && SINK_N + 1u < sizeof SINK; i++) {
        SINK[SINK_N++] = (char)p[i];
    }
    SINK[SINK_N] = '\0';
    mk_txring_consume(&R, n);
    return n;
}

static void drain(void)
{
    while (fake_dma_step() > 0u) { }
}

/* ---- 즉시 반환 ---------------------------------------------------------- */

static void test_push_succeeds_without_any_consumer(void)
{
    /* 🔴 이것이 이 모듈의 존재 이유다. 가짜 DMA 를 한 번도 안 돌려도
     *    — 즉 아무도 완료를 알려 주지 않아도 — 넣는 쪽은 성공하고
     *    돌아온다. 블로킹 판은 여기서 영원히 기다렸다. */
    setup();
    SINK_N = 0;
    CHECK(mk_txring_push(&R, "hello\n", 6u, 0u) == 1,
          "소비자가 한 번도 안 돌아도 넣는 쪽은 성공한다");
    CHECK(mk_txring_used(&R) == 6u, "넣은 만큼 들어 있다");
    CHECK(SINK_N == 0u, "아직 한 바이트도 안 나갔다 — 넣기는 보내기가 아니다");
}

static void test_free_and_used_agree(void)
{
    setup();
    /* 🔴 쓸 수 있는 칸은 cap-1 이다. head==tail 을 '빔' 으로 쓰기로
     *    했으므로 한 칸은 빈 상태와 꽉 찬 상태를 가르는 데 쓴다 —
     *    이 한 칸이 없으면 SPSC 에서 잠금 없이 구별할 수 없다. */
    CHECK(mk_txring_free(&R) == CAP - 1u, "빈 링의 여유는 cap-1");
    mk_txring_push(&R, "abc", 3u, 0u);
    CHECK(mk_txring_used(&R) == 3u && mk_txring_free(&R) == CAP - 1u - 3u,
          "used + free 가 항상 cap-1");
}

/* ---- 줄이 잘리지 않는다 -------------------------------------------------- */

static void test_a_line_that_does_not_fit_is_rejected_whole(void)
{
    /* 🔴 이 시험이 이 파일에서 가장 중요하다.
     *
     *    들어가는 만큼만 넣으면 NDJSON 한 줄이 가운데서 끊긴다. 호스트는
     *    깨진 JSON 을 받고, 그 오류는 링크 문제로 오인된다 — Q2 에서
     *    8개 가설이 전부 기각된 채 미해결로 남은 그 증상과 구별이 안
     *    된다(CLAUDE.md §1.2). 그래서 안 들어가면 **한 바이트도** 안
     *    넣는다. */
    setup();
    SINK_N = 0;
    CHECK(mk_txring_push(&R, "0123456789", 10u, 0u) == 1, "10바이트는 들어간다");
    CHECK(mk_txring_push(&R, "ABCDEFGH", 8u, 0u) == 0,
          "남은 5칸에 8바이트 줄은 거절된다");
    CHECK(mk_txring_used(&R) == 10u,
          "거절된 줄은 한 바이트도 안 들어갔다 — 앞줄이 그대로다");
    drain();
    CHECK(strcmp(SINK, "0123456789") == 0,
          "나간 바이트열에 잘린 줄의 조각이 섞이지 않았다");
}

static void test_rejected_lines_are_counted(void)
{
    /* 🔴 버린 것을 세지 않으면 "유실이 없었다" 로 보인다. mk_queue 가
     *    같은 이유로 drops 를 센다(app/mk_queue.h). 링이 차는 것은
     *    설정이 링크 용량을 넘었다는 신호이므로 밖에서 보여야 한다. */
    setup();
    CHECK(mk_txring_drops(&R) == 0u, "처음엔 버린 줄이 없다");
    mk_txring_push(&R, "0123456789", 10u, 0u);
    mk_txring_push(&R, "ABCDEFGH", 8u, 0u);
    mk_txring_push(&R, "IJKLMNOP", 8u, 0u);
    CHECK(mk_txring_drops(&R) == 2u, "거절된 줄 수를 센다");
    CHECK(mk_txring_dropped_bytes(&R) == 16u, "버린 바이트 수도 센다");
}

static void test_a_line_longer_than_the_ring_is_rejected_not_split(void)
{
    /* 링보다 긴 줄은 링이 아무리 비어도 못 들어간다. 조각내서 넣으면
     * 그 자체로 잘린 줄이 된다. */
    setup();
    uint8_t big[CAP + 4];
    memset(big, 'x', sizeof big);
    CHECK(mk_txring_push(&R, big, sizeof big, 0u) == 0, "링보다 긴 줄은 거절");
    CHECK(mk_txring_used(&R) == 0u, "한 바이트도 안 들어갔다");
}

/* ---- 예약 몫 ------------------------------------------------------------ */

static void test_reserve_keeps_room_for_the_control_path(void)
{
    /* 🔴 텔레메트리가 링을 다 먹으면 $SACK·$ERR 이 나갈 자리가 없어진다.
     *    그러면 사용자는 링크를 포화시킨 그 설정을 되돌릴 수단을 잃는다 —
     *    mk_linkbaud 의 자동 되돌림과 같은 정신이다(사람이 아무것도 못
     *    하는 상태를 만들지 않는다). 텔레메트리는 예약 몫을 남기고,
     *    명령 응답은 예약 몫까지 쓴다. */
    setup();
    /* 쓸 수 있는 칸 15 에서 예약 6 을 빼면 텔레메트리 몫은 9 다. */
    CHECK(mk_txring_push(&R, "0123456789", 10u, 6u) == 0,
          "예약 6칸을 남기면 10바이트 텔레메트리 줄은 거절된다");
    CHECK(mk_txring_push(&R, "0123456789", 10u, 0u) == 1,
          "예약을 안 요구하는 쪽(명령 응답)은 같은 줄이 들어간다");
    CHECK(mk_txring_push(&R, "abcd", 4u, 0u) == 1,
          "남은 5칸에 4바이트 응답이 들어간다");
}

/* ---- 감김 -------------------------------------------------------------- */

static void test_the_chunk_stops_at_the_wrap(void)
{
    /* DMA 는 연속된 메모리만 옮긴다. 링이 감기는 자리에서 조각을 끊어
     * 줘야 하고, 완료 인터럽트가 나머지를 이어 건다. */
    setup();
    SINK_N = 0;
    mk_txring_push(&R, "0123456789", 10u, 0u);   /* head=10 */
    drain();                                      /* tail=10, 링은 빈다 */
    SINK_N = 0;
    mk_txring_push(&R, "ABCDEFGH", 8u, 0u);      /* 10..15 + 0..1 로 감긴다 */

    const uint8_t *p = NULL;
    size_t n = mk_txring_chunk(&R, &p);
    CHECK(n == 6u, "첫 조각은 링 끝까지만 (6바이트)");
    CHECK(n > 0u && memcmp(p, "ABCDEF", 6u) == 0, "첫 조각의 내용이 맞다");

    size_t moved = fake_dma_step();
    CHECK(moved == 6u, "첫 조각을 소비했다");
    moved = fake_dma_step();
    CHECK(moved == 2u, "완료 인터럽트가 감긴 나머지를 이어 건다");
    CHECK(strcmp(SINK, "ABCDEFGH") == 0, "감겨도 바이트열이 온전하다");
    CHECK(mk_txring_used(&R) == 0u, "다 나가면 빈다");
}

static void test_bytes_survive_many_wraps(void)
{
    /* 넣기와 내보내기를 섞어 head 와 tail 이 서로를 여러 번 넘어가게
     * 만든다. 순서가 한 번이라도 어긋나면 NDJSON 이 깨진다. */
    setup();
    SINK_N = 0;
    int ok = 1;
    for (int round = 0; round < 40; round++) {
        char line[8];
        line[0] = (char)('a' + (round % 26));
        line[1] = (char)('0' + (round % 10));
        line[2] = '\n';
        if (!mk_txring_push(&R, line, 3u, 0u)) { ok = 0; }
        drain();
        if (SINK_N != (size_t)(round + 1) * 3u) { ok = 0; }
        if (SINK[SINK_N - 1] != '\n') { ok = 0; }
    }
    CHECK(ok, "40번을 감아도 넣은 순서대로 온전히 나간다");
}

/* ---- 부분 소비 ---------------------------------------------------------- */

static void test_partial_consume_leaves_the_rest(void)
{
    /* 실기기에서 DMA 를 중간에 멈추는 경우(baud 전환의 시한 만료)가
     * 있다. 소비한 만큼만 비워지고 나머지는 그대로 남아야 한다. */
    setup();
    mk_txring_push(&R, "0123456789", 10u, 0u);
    mk_txring_consume(&R, 4u);
    CHECK(mk_txring_used(&R) == 6u, "소비한 만큼만 빠진다");
    const uint8_t *p = NULL;
    size_t n = mk_txring_chunk(&R, &p);
    CHECK(n == 6u && memcmp(p, "456789", 6u) == 0, "남은 것이 다음 조각이다");
}

static void test_consume_more_than_used_is_clamped(void)
{
    /* 방어. 여기서 tail 이 head 를 넘어가면 used 가 60000 대로 튀어
     * 링이 통째로 망가진다 — 링버퍼가 죽는 가장 흔한 방식이다. */
    setup();
    mk_txring_push(&R, "abc", 3u, 0u);
    /* 🔴 딱 하나 더 부른다. 크게 부르면(예: 99) cap 으로 나눈 나머지가
     *    우연히 head 와 같아져 방어가 없어도 통과해 버린다 — 되돌림
     *    검사에서 실제로 그렇게 새어 나갔다. */
    mk_txring_consume(&R, 4u);
    CHECK(mk_txring_used(&R) == 0u, "있는 것보다 많이 소비해도 0 에서 멈춘다");
    CHECK(mk_txring_chunk(&R, NULL) == 0u, "빈 링의 조각은 0");
}

static void test_zero_length_and_null_are_ignored(void)
{
    setup();
    CHECK(mk_txring_push(&R, NULL, 5u, 0u) == 0, "NULL 은 안 넣는다");
    CHECK(mk_txring_push(&R, "x", 0u, 0u) == 0, "길이 0 은 안 넣는다");
    CHECK(mk_txring_drops(&R) == 0u, "그것들은 유실이 아니다 — 세지 않는다");
    CHECK(mk_txring_used(&R) == 0u, "링은 그대로다");
}

static void test_no_storage_still_counts_drops(void)
{
    /* mk_queue 와 같은 이유 — 저장소를 안 붙였는데 조용히 성공하면
     * 안 나가는 이유를 영영 못 찾는다. */
    MkTxRing r;
    mk_txring_init(&r, NULL, 0u);
    CHECK(mk_txring_push(&r, "abc", 3u, 0u) == 0, "저장소가 없으면 실패");
    CHECK(mk_txring_drops(&r) == 1u, "그래도 버린 것은 센다");
}

int main(void)
{
    printf("mk_txring\n");
    test_push_succeeds_without_any_consumer();
    test_free_and_used_agree();
    test_a_line_that_does_not_fit_is_rejected_whole();
    test_rejected_lines_are_counted();
    test_a_line_longer_than_the_ring_is_rejected_not_split();
    test_reserve_keeps_room_for_the_control_path();
    test_the_chunk_stops_at_the_wrap();
    test_bytes_survive_many_wraps();
    test_partial_consume_leaves_the_rest();
    test_consume_more_than_used_is_clamped();
    test_zero_length_and_null_are_ignored();
    test_no_storage_still_counts_drops();
    printf(failures ? "FAILED (%d)\n" : "PASSED\n", failures);
    return failures ? 1 : 0;
}
