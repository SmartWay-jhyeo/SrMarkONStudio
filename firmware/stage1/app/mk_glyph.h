/* 글자 그리기 — **글꼴 표를 모른다.** MkFont 규약만 본다.
 *
 * 🔴 이 파일이 `mk_font5x7.c` 를 include 하지 않는 것이 요점이다
 *    (사용자 확정 2026-08-19: "글꼴 표와 그리기 코드를 분리해서, 나중에
 *    한글 부분집합을 얹을 때 구조를 뜯지 않아도 되게 해라").
 *
 * 🔴 프레임버퍼가 없다. 물어보는 것은 **"이 화소가 글자 획 안인가"** 하나다.
 *
 *    이유는 메모리다 — 320 x 480 x 3 = 460,800 바이트짜리 프레임버퍼를
 *    들 수 없다. 대신 mk_lcd 가 행 하나(최대 960바이트)를 채워 달라고
 *    부를 때마다 이 함수로 화소를 판정한다. 화면 전체가 아니라 **바뀐
 *    칸의 직사각형**만 이렇게 지나가므로 비용도 작다.
 *
 *    비용: 화소마다 나눗셈 둘(배율, 글자 위치). Cortex-M7 은 하드웨어
 *    나눗셈이 있고, 한 칸(258 x 16)이 4,128 화소다.
 */
#ifndef MK_GLYPH_H
#define MK_GLYPH_H

#include "mk_font.h"

/* 문자열의 화소 폭. 🔴 마지막 글자 뒤의 자간은 세지 않는다 — 세면
 * 오른쪽 정렬이 자간만큼 밀린다. 빈 문자열은 0. */
unsigned mk_glyph_text_width(const MkFont *f, const char *s, unsigned scale);

/* 글자 한 줄의 화소 높이. */
unsigned mk_glyph_text_height(const MkFont *f, unsigned scale);

/* (x, y) 가 글자 획 안인가. 문자열 상자의 왼쪽 위가 (0, 0) 이다.
 *
 * 문자열 밖·글리프 높이 밖·자간 자리·표에 없는 글자는 전부 0 이다 —
 * 즉 "그리지 않는다". */
int mk_glyph_text_pixel(const MkFont *f, const char *s,
                        unsigned x, unsigned y, unsigned scale);

#endif /* MK_GLYPH_H */
