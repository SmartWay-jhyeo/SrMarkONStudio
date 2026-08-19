/* mk_screen · mk_glyph · mk_text 단위 시험 — 보드도 패널도 필요 없다.
 *
 * 🔴 여기서 지키는 것은 **"안 바뀌면 안 그린다"** 하나다.
 *
 *    전면 갱신이 320 x 480 x 3 = 460,800 바이트이고 SPI2 16 MHz 에서 약
 *    230 ms 다. 1단계에서 실기기로 확인한 것은 "그 230 ms 동안에도 수집이
 *    안 밀린다"(ain 간격 중앙값 100 ms, 큐 drops 0) 였다. 2단계가 값 하나
 *    바뀔 때마다 전면을 다시 그리면 그 성질을 그대로 잃는다 — 초당 4번이면
 *    SPI2 가 92% 를 차지한다.
 *
 *    그래서 시험이 **바이트를 센다**. "안 바뀐 주기에는 한 바이트도 안
 *    나간다", "한 칸이 바뀌면 그 칸의 직사각형만 나간다".
 *
 * 🔴 그리고 **글꼴 표와 그리기의 분리**를 본다. 한글 부분집합을 나중에
 *    얹을 때 그리기 코드를 뜯지 않으려면, 그리기가 표를 직접 읽으면 안
 *    된다. 시험이 가짜 글꼴(3x5, 전부 채움)을 넣어 같은 그리기 코드가
 *    그대로 도는 것을 확인한다 — 표를 갈아도 그리기가 안 바뀐다는 것을
 *    말이 아니라 코드로 보이는 유일한 방법이다.
 */
#include <stdio.h>
#include <string.h>

#include "../app/mk_screen.h"
#include "../app/mk_glyph.h"
#include "../app/mk_text.h"
#include "../app/mk_font.h"
#include "../app/mk_cfgtable.h"
#include "../app/mk_queue.h"
#include "../app/mk_json.h"

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
    if (!(cond)) { printf("  FAIL %s\n", msg); failures++; }    \
    else         { printf("  ok   %s\n", msg); }                \
} while (0)

/* ---- 가짜 SPI 버스 -------------------------------------------------------
 *
 * test_lcd.c 의 것과 같은 모양이되, 여기서는 **주소창 인자와 화소 행의
 * 크기**를 본다 — "어느 직사각형을 그렸나" 가 이 시험의 전부이기 때문이다.
 */
#define MAX_EV 4096

typedef struct {
    int      dc[MAX_EV];        /* 0 = 명령, 1 = 데이터 */
    size_t   len[MAX_EV];
    uint8_t  head[MAX_EV][4];   /* 앞 4바이트 — 명령 번호와 주소창 인자 */
    int      n;

    int      cur_dc;
    int64_t  now;
    size_t   bytes;             /* 여태 나간 총 바이트 */
} Bus;

static Bus      BUS;
static MkLcd    LCD;
static MkScreen SCR;
static MkConfig CFG;

static uint8_t CMDBUF[MK_LCD_CMD_BUF_BYTES];
static uint8_t ROWBUF[MK_LCD_ROW_BYTES];

static void fake_cs(void *ctx, int low)       { (void)ctx; (void)low; }
static void fake_reset(void *ctx, int low)    { (void)ctx; (void)low; }
static void fake_backlight(void *ctx, int on) { (void)ctx; (void)on; }

static void fake_dc(void *ctx, int data)
{
    Bus *b = (Bus *)ctx;
    b->cur_dc = data;
}

static int fake_send(void *ctx, const uint8_t *buf, size_t n)
{
    Bus *b = (Bus *)ctx;
    if (b->n < MAX_EV) {
        b->dc[b->n] = b->cur_dc;
        b->len[b->n] = n;
        for (size_t k = 0; k < 4u; k++) {
            b->head[b->n][k] = k < n ? buf[k] : 0u;
        }
        b->n++;
    }
    b->bytes += n;
    return 1;
}

/* 전송마다 완료를 알리며 시간을 흘린다 (test_lcd.c 와 같은 관례 —
 * 가짜 전송은 저절로 끝나지 않으므로, 상태기계가 완료를 기다리며 돌면
 * 시험이 그 자리에서 멈춘다). */
static void run_ms(int64_t ms)
{
    for (int64_t t = 0; t < ms; t++) {
        BUS.now += 1;
        mk_lcd_tick(&LCD, &CFG, BUS.now);
        mk_screen_tick(&SCR, &LCD, BUS.now);
        mk_lcd_on_tx_done(&LCD);
    }
}

static void set_u32(const char *key, uint32_t v)
{
    MkCfgItem *it = mk_cfg_find(&CFG, key);
    if (it) { it->cur.u = v; }
}

/* 화면을 켜고 첫 그림(전면 지우기 + 모든 칸)이 끝날 때까지 돌린다. */
static void boot_screen(void)
{
    memset(&BUS, 0, sizeof BUS);
    mk_cfgtable_init(&CFG);
    set_u32("lcd.enabled", 1u);

    MkLcdIo io = { fake_cs, fake_dc, fake_reset, fake_backlight,
                   fake_send, &BUS };
    mk_lcd_init(&LCD, &io, CMDBUF, sizeof CMDBUF, ROWBUF, sizeof ROWBUF);
    mk_screen_init(&SCR, NULL);

    /* 전면 지우기 480행 + 칸마다 h 행. 넉넉히 돌린다. */
    run_ms(4000);
}

/* 마지막 주소창(2Ah·2Bh)이 가리키는 직사각형과, 그 뒤 화소 행 수. */
typedef struct { unsigned x0, x1, y0, y1, rows; size_t row_bytes; } Rect;

static int last_rect(Rect *r)
{
    int found = 0;
    memset(r, 0, sizeof *r);
    for (int e = 0; e < BUS.n; e++) {
        if (BUS.dc[e] != 0) { continue; }
        if (BUS.head[e][0] == 0x2Au && e + 1 < BUS.n && BUS.dc[e + 1] == 1) {
            r->x0 = ((unsigned)BUS.head[e + 1][0] << 8) | BUS.head[e + 1][1];
            r->x1 = ((unsigned)BUS.head[e + 1][2] << 8) | BUS.head[e + 1][3];
        } else if (BUS.head[e][0] == 0x2Bu && e + 1 < BUS.n && BUS.dc[e + 1] == 1) {
            r->y0 = ((unsigned)BUS.head[e + 1][0] << 8) | BUS.head[e + 1][1];
            r->y1 = ((unsigned)BUS.head[e + 1][2] << 8) | BUS.head[e + 1][3];
        } else if (BUS.head[e][0] == 0x2Cu) {
            /* RAMWR 뒤로 이어지는 화소 행을 센다. */
            r->rows = 0;
            r->row_bytes = 0;
            for (int k = e + 1; k < BUS.n && BUS.dc[k] == 1; k++) {
                r->rows++;
                r->row_bytes = BUS.len[k];
            }
            found = 1;
        }
    }
    return found;
}

/* ---- 1. 글꼴 표와 그리기의 분리 ------------------------------------------
 *
 * 🔴 가짜 글꼴을 넣어도 **같은 그리기 코드**가 돈다. 이것이 성립해야
 *    나중에 한글 부분집합을 다른 MkFont 로 얹을 수 있다.
 */
static const uint8_t FAKE_GLYPH[3] = { 0x1Fu, 0x1Fu, 0x1Fu };   /* 3열 x 5행 꽉 참 */

static const uint8_t *fake_glyph(const MkFont *f, uint32_t cp)
{
    (void)f;
    return cp == (uint32_t)'X' ? FAKE_GLYPH : NULL;
}

static const MkFont FAKE_FONT = { 3u, 5u, 4u, 1u, fake_glyph };

static void test_the_drawing_code_does_not_know_the_font_table(void)
{
    /* 진짜 글꼴 */
    const MkFont *ascii = mk_font_ascii5x7();
    CHECK(ascii != NULL && ascii->width == 5u && ascii->height == 7u,
          "기본 글꼴은 5x7 이다");
    CHECK(ascii->advance == 6u, "자간을 포함한 진행폭이 6 이다");
    CHECK(mk_glyph_text_width(ascii, "AB", 1u) == 11u,
          "\"AB\" 는 6 + 5 = 11 화소다 (마지막 자간은 안 센다)");
    CHECK(mk_glyph_text_width(ascii, "", 1u) == 0u, "빈 문자열은 0 화소다");
    CHECK(mk_glyph_text_height(ascii, 2u) == 14u, "배율 2 면 높이가 14 다");

    /* 같은 함수에 가짜 글꼴을 넣는다 — 그리기 코드는 한 줄도 안 바뀐다 */
    CHECK(mk_glyph_text_width(&FAKE_FONT, "XX", 1u) == 7u,
          "가짜 글꼴 \"XX\" 는 4 + 3 = 7 화소다");
    CHECK(mk_glyph_text_pixel(&FAKE_FONT, "X", 0u, 0u, 1u) == 1,
          "가짜 글리프는 (0,0)이 켜져 있다");
    CHECK(mk_glyph_text_pixel(&FAKE_FONT, "X", 3u, 0u, 1u) == 0,
          "자간 자리(열 3)는 꺼져 있다");
    CHECK(mk_glyph_text_pixel(&FAKE_FONT, "X", 0u, 5u, 1u) == 0,
          "글리프 높이 밖은 꺼져 있다");
    CHECK(mk_glyph_text_pixel(&FAKE_FONT, "Z", 0u, 0u, 1u) == 0,
          "표에 없는 글자는 아무것도 안 그린다");

    /* 배율은 그리기 쪽 성질이다 — 표를 안 건드린다 */
    CHECK(mk_glyph_text_pixel(&FAKE_FONT, "X", 5u, 9u, 2u) == 1,
          "배율 2 에서 (5,9)는 글리프 (2,4) 라 켜져 있다");
    CHECK(mk_glyph_text_pixel(&FAKE_FONT, "X", 6u, 0u, 2u) == 0,
          "배율 2 에서 (6,0)은 자간 자리다");
}

/* 🔴 화면에 나오는 글자는 전부 표에 있어야 한다. 하나라도 빠지면 그 자리가
 *    조용히 빈칸이 되고, 사람은 "값이 안 나온다" 로 읽는다. */
static void test_the_ascii_table_covers_everything_the_screen_prints(void)
{
    const MkFont *f = mk_font_ascii5x7();
    int missing = 0;
    for (uint32_t c = 0x20u; c <= 0x7Eu; c++) {
        if (f->glyph(f, c) == NULL) { missing++; }
    }
    CHECK(missing == 0, "0x20~0x7E 가 표에 전부 있다");
    CHECK(f->glyph(f, 0x1Fu) == NULL, "제어문자는 표에 없다");
    CHECK(f->glyph(f, 0xAC00u) == NULL, "한글은 아직 이 표에 없다");

    /* 공백은 있되 비어 있어야 한다 — 없으면(NULL) 그리기가 같은 결과를
     * 내지만, 표가 "그 글자를 모른다" 와 "빈 글자다" 를 구분하지 못한다. */
    const uint8_t *sp = f->glyph(f, (uint32_t)' ');
    CHECK(sp != NULL && sp[0] == 0u && sp[4] == 0u, "공백은 빈 글리프다");
}

/* ---- 2. 숫자 형식 --------------------------------------------------------
 *
 * 🔴 libc 의 snprintf 가 없다(app/ 규칙). 직접 짠 것이므로 음수·반올림·
 *    자리 넘침을 여기서 못박는다.
 */
static void test_number_formatting(void)
{
    char b[16];

    mk_text_f32_fit(b, sizeof b, -0.33f, 2);
    CHECK(strcmp(b, "-0.33") == 0, "음수와 0 앞자리를 함께 낸다");

    mk_text_f32_fit(b, sizeof b, 24.15f, 2);
    CHECK(strcmp(b, "24.15") == 0, "24.15 를 두 자리로");

    mk_text_f32_fit(b, sizeof b, 24.1f, 1);
    CHECK(strcmp(b, "24.1") == 0, "24.1 을 한 자리로");

    mk_text_f32_fit(b, sizeof b, 0.0f, 1);
    CHECK(strcmp(b, "0.0") == 0, "0 도 자릿수를 지킨다");

    /* 🔴 반올림 결과가 0 이면 부호를 안 붙인다 — mk_json_f32 와 같은
     *    규칙이다. 같은 값이 화면과 호스트에서 다르게 보이면 어느 쪽이
     *    맞는지 정할 방법이 없다. 아래 대조 시험이 그것을 못박는다. */
    mk_text_f32_fit(b, sizeof b, -0.004f, 2);
    CHECK(strcmp(b, "0.00") == 0, "반올림해서 0 이면 부호를 안 붙인다");

    mk_text_f32_fit(b, sizeof b, 1.005f, 2);
    CHECK(b[0] == '1' && b[1] == '.', "1.005 도 소수점을 낸다");

    mk_text_f32_fit(b, sizeof b, 123.0f, 0);
    CHECK(strcmp(b, "123") == 0, "자릿수 0 이면 소수점을 안 찍는다");

    mk_text_f32_fit(b, sizeof b, 12.5f, 4);
    CHECK(strcmp(b, "12.5000") == 0, "빈 소수 자리를 0 으로 채운다");

    /* 🔴 유한하지 않은 값. 계산 실패를 숫자로 위장하지 않는다. */
    float inf = 1.0f;
    float zero = 0.0f;
    /* 컴파일러 최적화가 상수 0 나눗셈을 접지 않게 변수로 만든다 */
    inf = inf / zero;
    mk_text_f32_fit(b, sizeof b, inf, 2);
    CHECK(strcmp(b, "---") == 0, "무한대는 값이 아니라 '---' 다");
}

/* 🔴 화면의 십진수와 전선으로 나가는 십진수가 **같은 모양**인지.
 *
 *    같은 값이 화면에서는 24.1, 호스트에서는 24.2 로 보이면 어느 쪽이
 *    맞는지 정할 방법이 없다. 두 곳에 같은 규칙을 손으로 적어 두었으므로
 *    (mk_json_f32 · mk_text_f32_fit) 여기서 실제로 대조한다 — 한쪽만
 *    고쳐 놓는 것을 이 시험이 막는다. */
static void test_the_screen_and_the_wire_round_numbers_the_same_way(void)
{
    static const float VALUES[] = {
        -0.33f, 0.0f, 24.15f, -0.004f, 1.005f, 123.456f, -99.999f, 0.5f
    };
    int bad = 0;
    for (unsigned k = 0; k < sizeof VALUES / sizeof VALUES[0]; k++) {
        for (int digits = 0; digits <= 3; digits++) {
            char jbuf[64];
            MkJson j;
            mk_json_begin(&j, jbuf, sizeof jbuf);
            mk_json_f32(&j, "v", VALUES[k], digits);
            mk_json_end(&j);

            const char *p = strstr(jbuf, "\"v\":");
            char wire[32];
            size_t n = 0;
            for (p += 4; *p != '\0' && *p != '}' && n + 1u < sizeof wire; p++) {
                wire[n++] = *p;
            }
            wire[n] = '\0';

            char screen[32];
            mk_text_f32_fit(screen, sizeof screen, VALUES[k], digits);
            if (strcmp(wire, screen) != 0) {
                printf("       %s != %s (digits=%d)\n", wire, screen, digits);
                bad++;
            }
        }
    }
    CHECK(bad == 0, "화면과 전선이 같은 십진수를 낸다");
}

/* 🔴 값이 길어져도 옆 칸을 침범하지 않는다. 자릿수를 줄여서라도 칸 안에
 *    들어가고, 정수부만으로도 안 되면 넘쳤다고 말한다 — 잘라 내면
 *    "1234" 가 "123" 으로 보이고 사람이 그것을 값으로 읽는다. */
static void test_a_long_number_never_spills_into_the_next_cell(void)
{
    char b[8];

    /* 여덟 칸(NUL 포함)에 -12345.678 을 두 자리로 → 자릿수를 줄인다 */
    mk_text_f32_fit(b, sizeof b, -12345.678f, 2);
    CHECK(strlen(b) < sizeof b, "칸을 절대 넘지 않는다");
    CHECK(strcmp(b, "-12345.") != 0, "소수점만 남기고 자르지 않는다");
    CHECK(strcmp(b, "-12345.7") == 0 || strcmp(b, "-12346") == 0
          || strcmp(b, "-12345") == 0,
          "자릿수를 줄여서 담는다");

    char small[5];
    mk_text_f32_fit(small, sizeof small, -123456.0f, 2);
    CHECK(strcmp(small, "OVF") == 0,
          "정수부만으로도 안 들어가면 잘라 내지 않고 넘쳤다고 말한다");
    CHECK(strlen(small) < sizeof small, "넘침 표시도 칸 안이다");
}

/* ---- 3. 배치 ------------------------------------------------------------- */

/* 🔴 칸이 겹치면 부분 갱신이 서로를 지운다 — 한 칸을 그릴 때마다 옆 칸의
 *    글자가 반쯤 잘려 나가고, 증상은 "가끔 글자가 깨진다" 뿐이다. */
static void test_the_layout_fits_the_panel_and_never_overlaps(void)
{
    MkScreen s;
    mk_screen_init(&s, NULL);

    unsigned n = mk_screen_field_count();
    CHECK(n > 10u, "칸이 여럿 있다");

    int bad_bounds = 0, overlap = 0, too_wide = 0;
    for (unsigned i = 0; i < n; i++) {
        const MkScreenField *a = mk_screen_field(&s, i);
        if (a->x + a->w > MK_LCD_WIDTH || a->y + a->h > MK_LCD_HEIGHT) {
            bad_bounds++;
        }
        /* 🔴 한 행이 행 버퍼를 넘으면 DMA 가 남의 메모리를 읽는다. */
        if ((size_t)a->w * MK_LCD_BYTES_PER_PIXEL > MK_LCD_ROW_BYTES) {
            too_wide++;
        }
        for (unsigned k = i + 1u; k < n; k++) {
            const MkScreenField *b = mk_screen_field(&s, k);
            if (a->x < b->x + b->w && b->x < a->x + a->w
                && a->y < b->y + b->h && b->y < a->y + a->h) {
                overlap++;
            }
        }
    }
    CHECK(bad_bounds == 0, "모든 칸이 320x480 안에 있다");
    CHECK(overlap == 0, "칸끼리 겹치지 않는다");
    CHECK(too_wide == 0, "칸의 한 행이 행 버퍼를 안 넘는다");
}

/* ---- 4. 부분 갱신 (이 작업의 요지) --------------------------------------- */

static void fill_data(MkScreenData *d)
{
    memset(d, 0, sizeof *d);
    d->ain[0].enabled = 1;
    d->ain[0].have = 1;
    d->ain[0].value = -0.33f;
    d->ain[0].unit = "bar";
    d->i2c[2].used = 1;                       /* J12 = 적외 온도 */
    d->i2c[2].n_slot = 1;
    d->i2c[2].slot[0].quantity = "temp_object";
    d->i2c[2].slot[0].have = 1;
    d->i2c[2].slot[0].value = 24.15f;
    d->time_grade = 0;
    d->sats = 3;
}

/* 🔴 이 시험이 이 작업 전체의 이유다. 값이 그대로면 SPI 로 **한 바이트도**
 *    안 나가야 한다. 여기가 깨지면 1단계에서 확인한 "수집이 안 밀린다" 가
 *    같이 무너진다. */
static void test_nothing_is_drawn_when_nothing_changed(void)
{
    MkScreenData d;
    fill_data(&d);

    boot_screen();
    mk_screen_apply(&SCR, &d);
    run_ms(2000);                      /* 첫 그림을 다 끝낸다 */

    size_t before = BUS.bytes;
    for (int k = 0; k < 20; k++) {
        mk_screen_apply(&SCR, &d);     /* 같은 값을 스무 번 */
        run_ms(100);
    }
    CHECK(BUS.bytes == before, "값이 그대로면 한 바이트도 안 나간다");
}

/* 🔴 값 하나가 바뀌면 **그 칸의 직사각형만** 나간다. 전면 갱신이면
 *    460,800 바이트이고 이 시험이 곧바로 잡는다. */
static void test_one_changed_value_repaints_only_its_own_cell(void)
{
    MkScreenData d;
    fill_data(&d);

    boot_screen();
    mk_screen_apply(&SCR, &d);
    run_ms(2000);

    size_t before = BUS.bytes;
    int ev_before = BUS.n;

    d.ain[0].value = -0.34f;
    mk_screen_apply(&SCR, &d);
    run_ms(500);

    CHECK(BUS.bytes > before, "값이 바뀌면 무언가는 나간다");

    /* 🔴 한 칸은 258 x 16 x 3 = 12,384 바이트 = 전면(460,800)의 2.7% 다.
     *    5% 를 넘으면 두 칸 이상을 그렸거나 전면을 그린 것이다. */
    size_t sent = BUS.bytes - before;
    size_t full = (size_t)MK_LCD_WIDTH * MK_LCD_HEIGHT * MK_LCD_BYTES_PER_PIXEL;
    CHECK(sent < full / 20u,
          "전면 갱신의 5% 미만이다 (한 칸만 그렸다)");

    /* 그 칸이 실제로 ain0 값 칸인지 주소창으로 확인한다. */
    Rect r;
    CHECK(last_rect(&r), "주소창과 화소 행이 나갔다");
    const MkScreenField *f = mk_screen_field(&SCR, mk_screen_ain_value_field(0));
    CHECK(r.x0 == f->x && r.x1 == (unsigned)(f->x + f->w - 1u),
          "가로 주소창이 그 칸의 가로 범위다");
    CHECK(r.y0 == f->y && r.y1 == (unsigned)(f->y + f->h - 1u),
          "세로 주소창이 그 칸의 세로 범위다");
    CHECK(r.rows == f->h, "그 칸의 높이만큼만 행을 보냈다");
    CHECK(r.row_bytes == (size_t)f->w * MK_LCD_BYTES_PER_PIXEL,
          "한 행이 그 칸의 폭이다");
    /* 주소창 3명령 + 인자 2개 = 5, 그리고 행 16개. 전면 갱신이면 485 다. */
    CHECK(BUS.n - ev_before == 5 + (int)f->h,
          "주소창 5번 + 그 칸의 행 수뿐이다 (전면 갱신이 아니다)");
}

/* 🔴 갱신 주기를 지킨다. 사람이 읽는 화면이라 초당 2~4번이면 넘친다 —
 *    텔레메트리 주기(100 ms)를 따라갈 이유가 없다. */
static void test_the_refresh_period_is_honoured(void)
{
    boot_screen();
    set_u32("lcd.period_ms", 500u);

    /* 🔴 출처를 붙여야 mk_screen_tick() 이 스스로 값을 읽는다. 안 붙이면
     *    (다른 시험들처럼) apply() 로만 값이 들어오고 주기가 뜻을 잃는다. */
    MkScreenSources src;
    memset(&src, 0, sizeof src);
    src.cfg = &CFG;
    mk_screen_init(&SCR, &src);
    run_ms(4000);

    /* 이제부터 값을 매 ms 바꾸면서 1초를 돈다. 주기가 500 ms 이므로
     * 다시 읽는 것은 두 번뿐이어야 한다. */
    int applied = 0;
    for (int k = 0; k < 1000; k++) {
        BUS.now += 1;
        mk_lcd_tick(&LCD, &CFG, BUS.now);
        applied += mk_screen_tick(&SCR, &LCD, BUS.now) ? 1 : 0;
        mk_lcd_on_tx_done(&LCD);
    }
    CHECK(applied <= 3 && applied >= 1,
          "1초에 500 ms 주기면 두 번 안팎만 값을 다시 읽는다");
}

/* ---- 5. 센서 미연결 ------------------------------------------------------
 *
 * 🔴 설계 원칙 3 — 미연결은 정상이다. 빈칸으로 두면 "고장인가 안 꽂았나"
 *    를 사람이 못 가린다.
 */
static void test_missing_sensors_are_drawn_as_none(void)
{
    MkScreen s;
    MkScreenData d;
    mk_screen_init(&s, NULL);
    memset(&d, 0, sizeof d);

    /* 아무것도 안 꽂힌 판 */
    mk_screen_apply(&s, &d);

    int empty = 0;
    for (unsigned i = 0; i < mk_screen_field_count(); i++) {
        if (mk_screen_field(&s, i)->text[0] == '\0') { empty++; }
    }
    CHECK(empty == 0, "빈 채로 남는 칸이 하나도 없다");

    const MkScreenField *a0 = mk_screen_field(&s, mk_screen_ain_value_field(0));
    CHECK(strcmp(a0->text, "NONE") == 0, "안 쓰는 아날로그 칸은 NONE 이다");

    const MkScreenField *p2 = mk_screen_field(&s, mk_screen_i2c_value_field(2));
    CHECK(strcmp(p2->text, "NONE") == 0, "종류가 '없음' 인 포트는 NONE 이다");

    /* 🔴 status = 3 (지원하지 않는 종류, 규격 §7.5)도 "없음" 이다.
     *    고장이 아니므로 사용자가 배선을 뜯게 만들면 안 된다. */
    d.i2c[3].used = 1;
    d.i2c[3].n_slot = 1;
    d.i2c[3].slot[0].quantity = "lux";
    d.i2c[3].slot[0].have = 1;
    d.i2c[3].slot[0].status = 3u;
    mk_screen_apply(&s, &d);
    const MkScreenField *p3 = mk_screen_field(&s, mk_screen_i2c_value_field(3));
    CHECK(strcmp(p3->text, "NONE") == 0, "status=3 (지원 안 함)도 NONE 이다");

    /* 응답이 없는 것은 다르다 — 꽂아 놨는데 대답이 없다는 사실이다. */
    d.i2c[3].slot[0].status = 1u;
    mk_screen_apply(&s, &d);
    CHECK(strcmp(p3->text, "NONE") != 0 && p3->text[0] != '\0',
          "status=1 (응답 없음)은 NONE 과 구분해서 말한다");

    /* 켜 두었는데 아직 값이 안 온 것도 다르다. */
    memset(&d, 0, sizeof d);
    d.ain[1].enabled = 1;
    d.ain[1].have = 0;
    mk_screen_apply(&s, &d);
    const MkScreenField *a1 = mk_screen_field(&s, mk_screen_ain_value_field(1));
    CHECK(strcmp(a1->text, "NONE") != 0 && a1->text[0] != '\0',
          "켜 두었는데 값이 아직 없는 것은 NONE 과 구분한다");
}

/* ---- 6. 만들어지는 글자 --------------------------------------------------- */

static void test_the_texts_say_what_the_user_asked_for(void)
{
    MkScreen s;
    MkScreenData d;
    mk_screen_init(&s, NULL);
    fill_data(&d);
    d.i2c[3].used = 1;                     /* J13 = 온습도 */
    d.i2c[3].n_slot = 2;
    d.i2c[3].slot[0].quantity = "temp";
    d.i2c[3].slot[0].have = 1;
    d.i2c[3].slot[0].value = 24.1f;
    d.i2c[3].slot[1].quantity = "humidity";
    d.i2c[3].slot[1].have = 1;
    d.i2c[3].slot[1].value = 56.1f;
    d.din[0] = 0; d.din[1] = 1; d.din[2] = 0;
    d.rail[MK_RAIL_24V] = 1;
    mk_screen_apply(&s, &d);

    const MkScreenField *f;

    f = mk_screen_field(&s, mk_screen_ain_name_field(0));
    CHECK(strcmp(f->text, "J3") == 0, "아날로그 0 번은 J3 이다");

    f = mk_screen_field(&s, mk_screen_ain_value_field(0));
    CHECK(strcmp(f->text, "-0.33 bar") == 0, "J3 유압이 -0.33 bar 로 나온다");

    f = mk_screen_field(&s, mk_screen_i2c_name_field(2));
    CHECK(strcmp(f->text, "J12") == 0, "I2C 포트 2 는 J12 다");

    f = mk_screen_field(&s, mk_screen_i2c_value_field(2));
    CHECK(strcmp(f->text, "24.15 C") == 0, "J12 적외온도가 24.15 C 다");

    f = mk_screen_field(&s, mk_screen_i2c_value_field(3));
    CHECK(strcmp(f->text, "24.1 C 56.1 %") == 0,
          "J13 온습도는 두 값을 한 칸에 낸다");

    f = mk_screen_field(&s, mk_screen_din_value_field());
    CHECK(strstr(f->text, "J18") != NULL && strstr(f->text, "J20") != NULL,
          "옵토 입력 칸이 J18~J20 을 모두 말한다");

    f = mk_screen_field(&s, mk_screen_time_value_field());
    CHECK(strstr(f->text, "SAT 3") != NULL, "위성 수가 나온다");
    CHECK(strstr(f->text, "DEV") != NULL, "시간축 등급이 나온다");

    f = mk_screen_field(&s, mk_screen_rail_value_field());
    CHECK(strstr(f->text, "24V") != NULL, "전원 칸이 24V 를 말한다");

    /* 🔴 설계 원칙 4 — 피드백 회로가 없다. 화면이 '정상 ON' 이라고
     *    말하면 안 된다. 좁은 칸에 다 못 적으므로 구역 이름표가 그것을
     *    이고 있어야 한다. */
    f = mk_screen_field(&s, mk_screen_system_head_field());
    CHECK(strstr(f->text, "COMMAND") != NULL,
          "전원 표시가 명령 상태라는 것을 화면이 말한다");
}

/* 🔴 만들어진 글자가 그 칸의 글자 수를 넘지 않는지. 넘으면 옆 칸을
 *    침범하지는 않지만(직사각형이 고정이다) 뒤가 잘려 값이 달라 보인다. */
static void test_every_text_fits_its_own_cell(void)
{
    MkScreen s;
    MkScreenData d;
    mk_screen_init(&s, NULL);

    /* 가장 긴 글자가 나올 만한 값들 */
    memset(&d, 0, sizeof d);
    for (int c = 0; c < MK_ADS_CHANNELS; c++) {
        d.ain[c].enabled = 1;
        d.ain[c].have = 1;
        d.ain[c].value = -12345.678f;
        d.ain[c].unit = "MPa";
    }
    for (int p = 0; p < MK_I2C_COUNT; p++) {
        d.i2c[p].used = 1;
        d.i2c[p].n_slot = 2;
        d.i2c[p].slot[0].quantity = "temp";
        d.i2c[p].slot[0].have = 1;
        d.i2c[p].slot[0].value = -999.9f;
        d.i2c[p].slot[1].quantity = "humidity";
        d.i2c[p].slot[1].have = 1;
        d.i2c[p].slot[1].value = 100.0f;
    }
    d.sats = 99;
    d.time_grade = 2;
    for (int r = 0; r < MK_RAIL_COUNT; r++) { d.rail[r] = 1; }
    mk_screen_apply(&s, &d);

    const MkFont *f = mk_font_ascii5x7();
    int over = 0;
    for (unsigned i = 0; i < mk_screen_field_count(); i++) {
        const MkScreenField *fd = mk_screen_field(&s, i);
        if (mk_glyph_text_width(f, fd->text, fd->scale) > fd->w) { over++; }
    }
    CHECK(over == 0, "어떤 값이 와도 글자가 칸 폭을 안 넘는다");
}

/* ---- 7. 화면이 꺼져 있을 때 ----------------------------------------------- */

/* 🔴 화면을 껐다 켜면 전부 다시 그려야 한다. 껐을 때 패널의 GRAM 이
 *    유지된다는 보장이 없고(전원은 그대로지만 다시 초기화한다), "이미
 *    그렸다" 고 기억하고 있으면 절반만 그려진 화면이 남는다. */
static void test_turning_the_panel_off_and_on_repaints_everything(void)
{
    MkScreenData d;
    fill_data(&d);

    boot_screen();
    mk_screen_apply(&SCR, &d);
    run_ms(3000);
    size_t first = BUS.bytes;
    CHECK(first > 0u, "처음에는 그렸다");

    set_u32("lcd.enabled", 0u);
    run_ms(200);
    size_t after_off = BUS.bytes;

    set_u32("lcd.enabled", 1u);
    run_ms(4000);
    CHECK(BUS.bytes - after_off > first / 2u,
          "다시 켜면 전면 지우기와 모든 칸을 다시 그린다");
}

/* 🔴 화면이 꺼져 있으면 mk_screen 도 조용해야 한다 — lcd.enabled 가 꺼진
 *    판에서 SPI 로 한 바이트도 안 나가는 것이 1단계의 계약이다. */
static void test_a_disabled_panel_stays_silent(void)
{
    memset(&BUS, 0, sizeof BUS);
    mk_cfgtable_init(&CFG);

    MkLcdIo io = { fake_cs, fake_dc, fake_reset, fake_backlight,
                   fake_send, &BUS };
    mk_lcd_init(&LCD, &io, CMDBUF, sizeof CMDBUF, ROWBUF, sizeof ROWBUF);
    mk_screen_init(&SCR, NULL);

    MkScreenData d;
    fill_data(&d);
    for (int k = 0; k < 2000; k++) {
        BUS.now += 1;
        mk_screen_apply(&SCR, &d);
        mk_lcd_tick(&LCD, &CFG, BUS.now);
        mk_screen_tick(&SCR, &LCD, BUS.now);
        mk_lcd_on_tx_done(&LCD);
    }
    CHECK(BUS.bytes == 0u, "화면이 꺼져 있으면 한 바이트도 안 나간다");
}

/* ---- 8. 수집을 방해하지 않는다 (사용자 확정 2026-08-19) -------------------
 *
 * 🔴 사용자가 못박은 선이다: **"뭘 하던지 센서 수집에는 방해가 안된다면
 *    뭐든지 해도 돼"**, 그리고 **"센서 값을 늦게 보내도 돼. 무조건 수집을
 *    정상적으로 타임스탬프 찍어서 가지고 있어야해."**
 *
 *    늦어도 되는 것 = 호스트로 나가는 시각·화면 갱신·저장.
 *    절대 안 되는 것 = 표본을 못 뜨는 것 · 타임스탬프가 틀리는 것 ·
 *                      **표본을 버리는 것**.
 *
 *    그래서 판정 기준은 "그리는 데 몇 ms 걸리나" 가 아니라 **큐의 drops
 *    가 0 인가** 다. 아래 두 시험이 그것을 각각 구조와 시간으로 본다.
 */

/* 🔴 구조 — 한 바퀴에 전송을 **시작만** 하고 완료를 기다리지 않는다.
 *
 *    완료를 한 번도 안 알려 주고 tick 을 천 번 부른다. 어딘가에서
 *    완료를 기다리며 돌면(바쁜 대기·블로킹 전송) 이 시험은 그 자리에서
 *    멈추거나, 전송이 두 번 이상 나가면서 실패한다.
 */
static void test_a_tick_starts_at_most_one_transfer_and_never_waits(void)
{
    memset(&BUS, 0, sizeof BUS);
    mk_cfgtable_init(&CFG);
    set_u32("lcd.enabled", 1u);

    MkLcdIo io = { fake_cs, fake_dc, fake_reset, fake_backlight,
                   fake_send, &BUS };
    mk_lcd_init(&LCD, &io, CMDBUF, sizeof CMDBUF, ROWBUF, sizeof ROWBUF);
    mk_screen_init(&SCR, NULL);

    /* 리셋 취소(120 ms)를 지나 첫 전송이 나갈 때까지만 시간을 흘린다. */
    for (int k = 0; k < 200 && BUS.n == 0; k++) {
        BUS.now += 1;
        mk_lcd_tick(&LCD, &CFG, BUS.now);
        mk_screen_tick(&SCR, &LCD, BUS.now);
    }
    CHECK(BUS.n == 1, "첫 전송이 하나 나갔다");

    /* 완료를 **안** 알려 준 채로 계속 민다. */
    for (int k = 0; k < 1000; k++) {
        BUS.now += 1;
        mk_lcd_tick(&LCD, &CFG, BUS.now);
        mk_screen_tick(&SCR, &LCD, BUS.now);
    }
    CHECK(BUS.n == 1,
          "완료를 안 알리면 다음 전송이 안 나간다 (완료를 안 기다린다)");
}

/* 🔴 시간 — 실제 SPI 소요시간을 흉내 낸 슈퍼루프에서 표본이 하나도 안
 *    버려지는지.
 *
 *    가짜 전송이 즉시 끝나지 않고 **바이트 수만큼 시간을 먹는다**
 *    (SPI2 16 MHz = 2 MB/s). 그동안에도 슈퍼루프는 계속 돌아야 하고,
 *    DRDY 를 흉내 낸 표본이 100 ms 마다 큐에 들어가며, 텔레메트리를
 *    흉내 낸 배출이 100 ms 마다 비운다.
 *
 *    화면은 내내 값이 바뀌어 쉬지 않고 다시 그린다 — 가장 나쁜 경우다.
 */
#define SIM_STEP_US    100u        /* 슈퍼루프 한 바퀴 (넉넉히 잡은 값) */
#define SIM_SPI_BPS    2000000u    /* 16 MHz / 8 = 초당 2 MB */

static uint64_t BUSY_UNTIL_US;
static uint64_t SIM_NOW_US;
static int      OVERLAP_VIOLATIONS;

static int timed_send(void *ctx, const uint8_t *buf, size_t n)
{
    /* 🔴 이미 전송 중인데 또 시작하면 DMA 가 겹친다 — 실기기에서는
     *    HAL 이 BUSY 를 돌려주고 화면이 조용히 멈춘다. */
    if (SIM_NOW_US < BUSY_UNTIL_US) { OVERLAP_VIOLATIONS++; }
    BUSY_UNTIL_US = SIM_NOW_US + ((uint64_t)n * 1000000u) / SIM_SPI_BPS + 1u;
    return fake_send(ctx, buf, n);
}

static void test_no_sample_is_lost_while_the_screen_redraws(void)
{
    memset(&BUS, 0, sizeof BUS);
    mk_cfgtable_init(&CFG);
    set_u32("lcd.enabled", 1u);
    set_u32("lcd.period_ms", 250u);

    MkLcdIo io = { fake_cs, fake_dc, fake_reset, fake_backlight,
                   timed_send, &BUS };
    mk_lcd_init(&LCD, &io, CMDBUF, sizeof CMDBUF, ROWBUF, sizeof ROWBUF);
    mk_screen_init(&SCR, NULL);

    /* 채널 하나의 큐 — main.c 와 같은 32칸. */
    MkSample store[32];
    MkQueue q;
    mk_queue_init(&q, store, 32u);

    MkScreenData d;
    fill_data(&d);

    BUSY_UNTIL_US = 0;
    SIM_NOW_US = 0;
    OVERLAP_VIOLATIONS = 0;

    uint32_t pushed = 0, popped = 0, bad_stamp = 0;
    uint64_t next_sample_us = 0, next_drain_us = 50000u;

    /* 10초. 화면 전체를 그리고도(전면 지우기 480행) 한참 남는다. */
    for (SIM_NOW_US = 0; SIM_NOW_US < 10000000u; SIM_NOW_US += SIM_STEP_US) {
        int64_t now_ms = (int64_t)(SIM_NOW_US / 1000u);

        /* DRDY 흉내 — 획득 시각은 이 순간이다 (설계 원칙 2). */
        if (SIM_NOW_US >= next_sample_us) {
            mk_queue_push(&q, now_ms, (int32_t)pushed);
            pushed++;
            next_sample_us += 100000u;
        }

        /* SPI 가 실제로 끝났을 때만 완료를 알린다. */
        if (BUSY_UNTIL_US != 0u && SIM_NOW_US >= BUSY_UNTIL_US) {
            BUSY_UNTIL_US = 0;
            mk_lcd_on_tx_done(&LCD);
        }

        /* 🔴 값을 계속 바꾼다 — 화면이 쉬지 않고 다시 그리는 최악의 경우. */
        d.ain[0].value = (float)(SIM_NOW_US / 1000u) * 0.01f;
        mk_screen_apply(&SCR, &d);

        mk_lcd_tick(&LCD, &CFG, now_ms);
        mk_screen_tick(&SCR, &LCD, now_ms);

        /* 텔레메트리 흉내 — 100 ms 마다 큐를 비운다. */
        if (SIM_NOW_US >= next_drain_us) {
            MkSample got;
            while (mk_queue_pop(&q, &got)) {
                if (got.raw != (int32_t)popped) { bad_stamp++; }
                popped++;
            }
            next_drain_us += 100000u;
        }
    }

    CHECK(BUS.bytes > 0u, "그동안 화면은 실제로 그렸다");
    CHECK(OVERLAP_VIOLATIONS == 0,
          "전송이 끝나기 전에 다음 전송을 시작하지 않는다");
    CHECK(mk_queue_drops(&q) == 0u,
          "🔴 화면이 도는 내내 표본을 하나도 안 버렸다 (drops = 0)");
    CHECK(popped + mk_queue_count(&q) == pushed,
          "넣은 표본 수와 꺼낸 표본 수가 맞는다");
    CHECK(bad_stamp == 0u, "표본의 순서가 뒤섞이지 않았다");
    CHECK(mk_queue_peak(&q) <= 4u,
          "큐가 깊게 차지 않는다 (배출이 밀리지 않았다)");
}

/* ---- 9. 설정 항목 --------------------------------------------------------- */

static void test_the_catalog_has_the_refresh_period(void)
{
    MkConfig cfg;
    mk_cfgtable_init(&cfg);
    MkCfgItem *it = mk_cfg_find(&cfg, "lcd.period_ms");
    CHECK(it != NULL, "카탈로그에 lcd.period_ms 가 있다");
    if (it != NULL) {
        CHECK(it->vtype == MK_VT_U16, "u16 이다");
        CHECK(it->has_min && it->has_max, "범위가 있다");
        CHECK(it->min >= 50.0 && it->max <= 10000.0,
              "사람이 읽는 화면다운 범위다");
        CHECK(it->def.u == 250u, "기본값이 250 ms (초당 4번) 다");
        CHECK(it->unit != NULL && strcmp(it->unit, "ms") == 0, "단위가 ms 다");
        CHECK(it->label != NULL && it->note != NULL, "라벨·안내문이 있다");
    }
}

int main(void)
{
    printf("mk_screen\n");
    test_the_drawing_code_does_not_know_the_font_table();
    test_the_ascii_table_covers_everything_the_screen_prints();
    test_number_formatting();
    test_the_screen_and_the_wire_round_numbers_the_same_way();
    test_a_long_number_never_spills_into_the_next_cell();
    test_the_layout_fits_the_panel_and_never_overlaps();
    test_nothing_is_drawn_when_nothing_changed();
    test_one_changed_value_repaints_only_its_own_cell();
    test_the_refresh_period_is_honoured();
    test_missing_sensors_are_drawn_as_none();
    test_the_texts_say_what_the_user_asked_for();
    test_every_text_fits_its_own_cell();
    test_turning_the_panel_off_and_on_repaints_everything();
    test_a_disabled_panel_stays_silent();
    test_a_tick_starts_at_most_one_transfer_and_never_waits();
    test_no_sample_is_lost_while_the_screen_redraws();
    test_the_catalog_has_the_refresh_period();
    printf(failures ? "FAILED %d\n" : "all passed\n", failures);
    return failures ? 1 : 0;
}
