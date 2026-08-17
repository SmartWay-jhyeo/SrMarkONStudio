/* WS2812 체인(J21~J24) 인코딩 — 설정값을 타이머 듀티 배열로 바꾼다.
 *
 * 🔴 HAL 을 모른다. 여기는 "무엇을 보낼 것인가" 만 정하고, 실제로 핀을
 *    흔드는 것은 bsp/mk_ws2812_io 다. 그래야 색·순서·밝기가 보드 없이
 *    시험된다 — WS2812 는 실물에서 원인을 가리기 어려운 부품이라 이 경계가
 *    특히 값지다.
 *
 * 배선 (데이터시트 §5.8, 넷리스트 확인):
 *     PA7 ── R84(390Ω) ── J21 ── J22 ── J23 ── J24
 *     전원은 5V 레일이다. pwr.5v 가 꺼져 있으면 아무것도 켜지지 않는다.
 *
 * 신호 (WS2812B 데이터시트):
 *     한 비트 1.25us. 1 = High 0.70us, 0 = High 0.35us.
 *     램프마다 24비트, **GRB** 순서, 상위 비트 먼저.
 *     프레임 끝에 50us 이상 Low 를 두어야 다음 프레임으로 인식한다.
 *
 * 타이머 값 (TIM3_CH2, AF2 — DS13313 Rev 1 p.72 Table 8):
 *     64MHz(HSI, 분주 1) 기준. ARR = 79 → (79+1)/64MHz = 1.25us.
 */
#ifndef MK_WS2812_H
#define MK_WS2812_H

#include <stddef.h>
#include <stdint.h>

/* 체인 램프 수 = J21~J24.
 *
 * 🔴 여기가 유일한 정의다. 카탈로그(mk_cfgtable)와 송신 버퍼(bsp)가 같은 수를
 *    봐야 한다 — 갈리면 GUI 에 램프 넷이 뜨는데 셋만 켜지는 식이 된다. */
#define MK_LED_COUNT  4u

/* 한 슬롯 1.25us. 64MHz 에서 80틱. */
#define MK_WS2812_ARR   79u
#define MK_WS2812_T0H   22u      /* 0.344us */
#define MK_WS2812_T1H   45u      /* 0.703us */

/* 리셋 구간. 48 × 1.25us = 60us ≥ 50us. */
#define MK_WS2812_RESET_SLOTS  48u

#define MK_WS2812_BITS_PER_LAMP  24u

/* 램프 n 개를 보내는 데 필요한 슬롯 수. */
#define MK_WS2812_SLOTS(n) \
    ((size_t)(n) * MK_WS2812_BITS_PER_LAMP + MK_WS2812_RESET_SLOTS)

typedef struct {
    uint8_t r, g, b;
} MkRgb;

/* 램프 배열을 듀티 배열로 바꾼다.
 *
 * brightness 는 0~255 로 전체를 비례 축소한다. 0 이면 꺼진다.
 * 자리가 모자라면 **아무것도 쓰지 않고** 0 을 돌려준다 — 반쪽 프레임은
 * 체인을 엉뚱한 색으로 굳힌다.
 *
 * 돌려주는 값은 out 에 쓴 슬롯 수다. */
size_t mk_ws2812_encode(const MkRgb *lamps, size_t n, uint8_t brightness,
                        uint16_t *out, size_t cap);

#endif /* MK_WS2812_H */
