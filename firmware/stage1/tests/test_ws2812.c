/* mk_ws2812 단위 시험.
 *
 * 🔴 WS2812 는 틀려도 조용하지 않다 — 색이 엉키거나 체인 전체가 안 켜진다.
 *    그런데 "안 켜진다" 는 전원·배선·타이밍 어느 쪽이든 같은 모습이라,
 *    보드에서 원인을 가리기가 어렵다. 인코딩이 맞는지는 여기서 못 박아 두고
 *    실물에서는 타이밍과 배선만 의심하도록 한다.
 *
 * 규격(WS2812B 데이터시트): 24비트 GRB, 상위 비트 먼저, 리셋은 50us 이상 Low.
 */
#include <stdio.h>
#include <string.h>
#include "../app/mk_ws2812.h"

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
    if (!(cond)) { printf("  FAIL %s\n", msg); failures++; }    \
    else         { printf("  ok   %s\n", msg); }                \
} while (0)

#define CAP 512
static uint16_t OUT[CAP];

static size_t encode(const MkRgb *lamps, size_t n, uint8_t bright)
{
    memset(OUT, 0xAA, sizeof OUT);   /* 안 쓴 자리를 눈에 띄게 */
    return mk_ws2812_encode(lamps, n, bright, OUT, CAP);
}

/* out[i] 가 1 비트인가 0 비트인가. */
static int is_one(size_t i)  { return OUT[i] == MK_WS2812_T1H; }
static int is_zero(size_t i) { return OUT[i] == MK_WS2812_T0H; }

/* ---- 슬롯 수 ------------------------------------------------------------ */

static void test_slot_count(void)
{
    printf("-- 슬롯 수 --\n");
    MkRgb l[2] = { {1, 2, 3}, {4, 5, 6} };

    CHECK(encode(l, 2, 255) == 2u * 24u + MK_WS2812_RESET_SLOTS,
          "램프 2개 = 48비트 + 리셋");
    CHECK(encode(l, 0, 255) == MK_WS2812_RESET_SLOTS,
          "램프 0개면 리셋만 — 체인을 끄는 것도 보내야 한다");
}

/* ---- GRB 순서 ----------------------------------------------------------- */

static void test_grb_order(void)
{
    printf("-- GRB 순서 --\n");

    /* 🔴 RGB 가 아니라 GRB 다. 이것을 뒤집으면 빨강이 초록으로 나오는데,
     *    보드에서 보면 "색이 이상하다" 로만 보여 원인을 짚기 어렵다. */
    MkRgb red = { 255, 0, 0 };
    encode(&red, 1, 255);
    /* 0~7 = G(0), 8~15 = R(255), 16~23 = B(0) */
    int g_all_zero = 1, r_all_one = 1, b_all_zero = 1;
    for (size_t i = 0; i < 8; i++)  if (!is_zero(i))      g_all_zero = 0;
    for (size_t i = 8; i < 16; i++) if (!is_one(i))       r_all_one = 0;
    for (size_t i = 16; i < 24; i++) if (!is_zero(i))     b_all_zero = 0;
    CHECK(g_all_zero, "빨강만 켜면 첫 8비트(G)는 0");
    CHECK(r_all_one,  "빨강만 켜면 다음 8비트(R)가 1");
    CHECK(b_all_zero, "빨강만 켜면 마지막 8비트(B)는 0");

    MkRgb green = { 0, 255, 0 };
    encode(&green, 1, 255);
    int g_one = 1;
    for (size_t i = 0; i < 8; i++) if (!is_one(i)) g_one = 0;
    CHECK(g_one, "초록만 켜면 첫 8비트(G)가 1");
}

/* ---- 비트 순서 ---------------------------------------------------------- */

static void test_msb_first(void)
{
    printf("-- 비트 순서 --\n");

    MkRgb l = { 0, 0x80, 0 };        /* G = 0b10000000 */
    encode(&l, 1, 255);
    CHECK(is_one(0), "0x80 이면 첫 슬롯이 1 — 상위 비트가 먼저다");
    CHECK(is_zero(7), "0x80 이면 여덟째 슬롯은 0");

    MkRgb l2 = { 0, 0x01, 0 };       /* G = 0b00000001 */
    encode(&l2, 1, 255);
    CHECK(is_zero(0), "0x01 이면 첫 슬롯이 0");
    CHECK(is_one(7),  "0x01 이면 여덟째 슬롯이 1");
}

/* ---- 밝기 --------------------------------------------------------------- */

static void test_brightness(void)
{
    printf("-- 밝기 --\n");

    /* 🔴 밝기 0 은 "끔" 이다. 체인을 끄려고 밝기를 0 으로 두는 것이 가장
     *    자연스러운 조작인데, 그때 색이 남아 있으면 안 꺼진다. */
    MkRgb white = { 255, 255, 255 };
    encode(&white, 1, 0);
    int all_zero = 1;
    for (size_t i = 0; i < 24; i++) if (!is_zero(i)) all_zero = 0;
    CHECK(all_zero, "밝기 0 이면 24비트가 전부 0 — 꺼진다");

    encode(&white, 1, 255);
    int all_one = 1;
    for (size_t i = 0; i < 24; i++) if (!is_one(i)) all_one = 0;
    CHECK(all_one, "밝기 255 면 그대로 나간다");

    /* 128/255 ≈ 0.502 → 255 * 128 / 255 = 128 = 0b10000000 */
    encode(&white, 1, 128);
    CHECK(is_one(0) && is_zero(1), "밝기 128 이면 255 가 128(0x80)로 줄어든다");
}

/* ---- 리셋 --------------------------------------------------------------- */

static void test_reset_gap(void)
{
    printf("-- 리셋 구간 --\n");

    MkRgb l = { 255, 255, 255 };
    size_t n = encode(&l, 1, 255);
    int gap_zero = 1;
    for (size_t i = 24; i < n; i++) if (OUT[i] != 0u) gap_zero = 0;
    CHECK(gap_zero, "데이터 뒤 리셋 구간은 듀티 0 이다");

    /* 🔴 50us 이상이어야 다음 프레임을 새 프레임으로 인식한다. 모자라면
     *    색이 한 칸씩 밀려 들어간다.
     *
     * 상수를 그대로 CHECK 에 넣으면 /WX 가 C4127 로 막는다. 지역 변수를
     * 거친다 — 값을 확인하는 것이 목적이지 상수 접기가 목적이 아니다. */
    unsigned reset_us = MK_WS2812_RESET_SLOTS * 1250u / 1000u;
    CHECK(reset_us >= 50u, "리셋이 50us 이상이다");
}

/* ---- 자리가 모자랄 때 --------------------------------------------------- */

static void test_capacity(void)
{
    printf("-- 자리 부족 --\n");

    MkRgb l[4] = { {1,1,1}, {2,2,2}, {3,3,3}, {4,4,4} };
    uint16_t small[10];
    memset(small, 0x5A, sizeof small);

    CHECK(mk_ws2812_encode(l, 4, 255, small, 10) == 0u,
          "자리가 모자라면 0 을 돌려준다");

    /* 🔴 반쪽 프레임을 보내면 체인이 엉뚱한 색으로 굳는다. 자리가 모자라면
     *    아무것도 쓰지 않아야 한다 — 부분 성공이 가장 나쁘다. */
    int untouched = 1;
    for (size_t i = 0; i < 10; i++) {
        if (((unsigned char *)small)[i * 2] != 0x5A) untouched = 0;
    }
    CHECK(untouched, "모자라면 버퍼를 건드리지 않는다");
}

/* ---- 타이밍 상수 -------------------------------------------------------- */

static void test_timing(void)
{
    printf("-- 타이밍 --\n");

    /* 타이머 64MHz(HSI, 분주 1), ARR+1 = 80 → 한 슬롯 1.25us = 800kHz.
     * WS2812B 규격: T0H 0.35±0.15us, T1H 0.70±0.15us */
    unsigned period = MK_WS2812_ARR + 1u;
    CHECK(period == 80u, "ARR+1 = 80 → 64MHz 에서 1.25us");

    unsigned t0h_ns = (MK_WS2812_T0H * 1000u) / 64u;   /* 틱 → ns */
    unsigned t1h_ns = (MK_WS2812_T1H * 1000u) / 64u;
    CHECK(t0h_ns >= 200u && t0h_ns <= 500u, "T0H 가 0.35us±0.15 안에 든다");
    CHECK(t1h_ns >= 550u && t1h_ns <= 850u, "T1H 가 0.70us±0.15 안에 든다");
}

int main(void)
{
    printf("test_ws2812\n");
    test_slot_count();
    test_grb_order();
    test_msb_first();
    test_brightness();
    test_reset_gap();
    test_capacity();
    test_timing();
    printf(failures ? "\nFAILED %d\n" : "\nOK\n", failures);
    return failures ? 1 : 0;
}
