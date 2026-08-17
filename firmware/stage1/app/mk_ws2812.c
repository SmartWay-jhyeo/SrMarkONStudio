#include "mk_ws2812.h"

/* 밝기를 비례로 적용한다.
 *
 * 🔴 나누기를 256 이 아니라 255 로 한다. 256 으로 나누면 밝기 255 에서
 *    254 가 나와, "최대 밝기인데 최댓값이 아닌" 상태가 된다. 그 1 차이는
 *    눈에 안 보이지만 시험과 계산이 어긋나 원인을 찾는 데 시간이 든다. */
static uint8_t scale(uint8_t v, uint8_t brightness)
{
    return (uint8_t)(((unsigned)v * (unsigned)brightness) / 255u);
}

/* 한 바이트를 8슬롯으로 편다. 상위 비트가 먼저다. */
static size_t put_byte(uint16_t *out, size_t i, uint8_t v)
{
    for (int bit = 7; bit >= 0; bit--) {
        out[i++] = (v & (1u << bit)) ? (uint16_t)MK_WS2812_T1H
                                     : (uint16_t)MK_WS2812_T0H;
    }
    return i;
}

size_t mk_ws2812_encode(const MkRgb *lamps, size_t n, uint8_t brightness,
                        MkWs2812Order order, uint16_t *out, size_t cap)
{
    if (out == NULL) {
        return 0;
    }
    /* 🔴 자리를 먼저 확인하고 한 칸도 쓰지 않는다. 쓰다가 멈추면 체인에
     *    반쪽 프레임이 나가고, 그러면 남은 램프가 이전 색으로 굳는다. */
    size_t need = MK_WS2812_SLOTS(n);
    if (cap < need) {
        return 0;
    }
    if (n > 0u && lamps == NULL) {
        return 0;
    }

    size_t i = 0;
    for (size_t k = 0; k < n; k++) {
        /* 🔴 앞 두 바이트만 자리를 바꾼다. 파랑은 두 순서 모두 셋째다 —
         *    그래서 파랑만 켜 보고는 순서가 맞는지 알 수 없다. 확인은
         *    빨강이나 초록으로 해야 한다. */
        uint8_t first  = order == MK_WS2812_GRB ? lamps[k].g : lamps[k].r;
        uint8_t second = order == MK_WS2812_GRB ? lamps[k].r : lamps[k].g;
        i = put_byte(out, i, scale(first, brightness));
        i = put_byte(out, i, scale(second, brightness));
        i = put_byte(out, i, scale(lamps[k].b, brightness));
    }

    /* 프레임 끝의 Low 구간. 듀티 0 이면 핀이 계속 Low 다. */
    for (size_t r = 0; r < MK_WS2812_RESET_SLOTS; r++) {
        out[i++] = 0u;
    }
    return i;
}
