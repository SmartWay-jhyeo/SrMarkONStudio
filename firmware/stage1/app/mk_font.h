/* 글꼴 표의 **모양**만 정한다 — 표 자체도, 그리는 코드도 여기 없다.
 *
 * 🔴 왜 이 파일이 따로 있나 (사용자 확정 2026-08-19)
 *
 *    2차는 ASCII 비트맵으로 간다. 한글은 글리프 표가 커서 이번 몫이
 *    아니다. 그런데 나중에 한글 부분집합을 얹을 때 **그리기 코드를 뜯지
 *    않아야 한다** — 그래서 표와 그리기 사이에 이 규약을 세운다.
 *
 *      mk_font.h      규약        (이 파일)
 *      mk_font5x7.c   표          ASCII 0x20~0x7E
 *      mk_glyph.c     그리기      표를 모른다. MkFont 만 본다
 *
 *    한글을 얹을 때 새로 생기는 것은 표 파일 하나(`mk_font_kr16.c`)와
 *    `MkFont` 하나뿐이고, `mk_glyph.c` 와 `mk_screen.c` 는 한 줄도 안
 *    바뀐다. tests/test_screen.c 가 가짜 글꼴(3x5)을 같은 그리기 함수에
 *    넣어 그것을 실제로 확인한다 — 말이 아니라 코드로 보이는 유일한 방법.
 *
 * ── 글리프의 바이트 배치 ──────────────────────────────────────────────
 *
 * **열 우선(column-major)** 이다. 열 하나가 `bytes_per_col` 바이트이고,
 * 그 안에서 **비트 0 이 맨 윗줄**이다.
 *
 *      5x7 ASCII :  bytes_per_col = 1,  글리프 = 5바이트
 *      16x16 한글:  bytes_per_col = 2,  글리프 = 32바이트
 *
 * 열 우선을 고른 이유는 이것이 5x7 계열 표의 통례라 옮겨 오기 쉽고
 * (LCD 컨트롤러의 페이지 주소지정도 같은 배치다), 폭이 다른 글꼴을
 * 섞어도 열 인덱스 계산이 그대로이기 때문이다.
 */
#ifndef MK_FONT_H
#define MK_FONT_H

#include <stdint.h>

typedef struct MkFont {
    uint8_t width;          /* 글리프의 그려지는 폭(화소) */
    uint8_t height;         /* 글리프의 높이(화소) */
    /* 🔴 다음 글자가 시작하는 거리. width 보다 커야 자간이 생긴다.
     *    width 와 같게 두면 글자가 서로 붙어 읽기 어렵다. */
    uint8_t advance;
    uint8_t bytes_per_col;  /* (height + 7) / 8 */

    /* 코드포인트 하나의 글리프. 없으면 NULL — 그리기는 그것을 빈칸으로
     * 본다. 🔴 uint32_t 인 이유는 나중에 한글(U+AC00~)이 들어오기
     * 때문이다. ASCII 표는 0x20~0x7E 밖을 전부 NULL 로 답한다. */
    const uint8_t *(*glyph)(const struct MkFont *f, uint32_t cp);
} MkFont;

/* ASCII 5x7. 표는 mk_font5x7.c 에 있다. */
const MkFont *mk_font_ascii5x7(void);

#endif /* MK_FONT_H */
