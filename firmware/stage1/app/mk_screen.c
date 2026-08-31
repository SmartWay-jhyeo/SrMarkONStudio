#include "mk_screen.h"

#include <string.h>

#include "mk_glyph.h"
#include "mk_ads1256.h"    /* mk_ads_raw_to_ma — 환산식을 한 곳에만 둔다 */
#include "mk_text.h"

/* ── 배치 상수 ────────────────────────────────────────────────────────────
 *
 * 🔴 숫자를 세로로 나열하는 이유: 사람이 **위치로** 읽는다. 한 줄이
 *    24 화소(글자 14 + 여백 10)이고, 3.5" 패널에서 글자 높이가 약 2.1 mm 다
 *    (480 화소 / 73.4 mm = 0.153 mm/화소). 팔 길이에서 읽을 수 있는 최소에
 *    가깝다 — 더 키우면 J3~J9 · J10~J15 를 한 화면에 못 담는다.
 *
 * 🔴 이름 칸을 값 칸과 나눈 이유: 값이 바뀔 때 이름까지 다시 그릴 이유가
 *    없다. 부분 갱신의 단위가 곧 이 칸이다.
 */
#define SCALE_BIG    2u          /* 값·이름·제목 */
#define SCALE_SMALL  1u          /* 구역 이름표 */

#define NAME_X       4u
#define NAME_W       52u         /* 4글자 — "J20" 까지 들어간다 */
#define VAL_X        58u
#define VAL_W        258u        /* 21글자. 58 + 258 = 316 <= 320 */
#define HEAD_X       4u
#define HEAD_W       312u
#define ROW_H        16u         /* 글자 14 + 위아래 1 */
#define ROW_STEP     24u
#define HEAD_H       8u

#define Y_TITLE      4u
#define Y_AIN_HEAD   26u
#define Y_AIN0       38u
#define Y_I2C_HEAD   204u
#define Y_I2C0       216u
#define Y_DIN_HEAD   358u
#define Y_DIN        370u
#define Y_SYS_HEAD   392u
#define Y_TIME       404u
#define Y_RAIL       428u

/* ── 색 ──────────────────────────────────────────────────────────────────
 *
 * 🔴 "값이 있다" 와 "없다" 를 **색으로도** 가른다. 글자만으로 가르면
 *    (NONE / 숫자) 멀리서는 둘 다 그냥 글자로 보인다. */
static const MkLcdColor C_TITLE = { 0xFFu, 0xC0u, 0x40u };
static const MkLcdColor C_HEAD  = { 0x50u, 0xA0u, 0xFFu };
static const MkLcdColor C_NAME  = { 0x90u, 0x90u, 0xA0u };
static const MkLcdColor C_VALUE = { 0xFFu, 0xFFu, 0xFFu };
static const MkLcdColor C_DIM   = { 0x60u, 0x60u, 0x68u };   /* 없음·대기 */
static const MkLcdColor C_ALERT = { 0xFFu, 0x70u, 0x40u };   /* 대답이 없다 */

/* ── 없음·대기 표시 ──────────────────────────────────────────────────────
 *
 * 🔴 설계 원칙 3 — 센서 미연결은 정상 상태다. 빈칸으로 두면 "고장인가
 *    안 꽂았나" 를 사람이 못 가린다.
 *
 *    NONE      쓸 것이 없다. 안 켠 채널, 종류가 "없음"인 포트, 그리고
 *              규격 §7.5 의 status=3(지원하지 않는 종류) — 셋 다 고장이
 *              아니다. 사용자 확정 2026-08-19 로 status=3 도 여기 든다.
 *    WAIT      켜 두었는데 아직 첫 값이 안 왔다. 곧 값이 될 자리다.
 *    NO REPLY  status=1. **꽂아 놨는데 대답이 없다** — 이것만 배선을
 *              볼 이유가 된다. NONE 과 반드시 구분한다.
 *    BAD DATA  status=2. 대답은 하는데 값이 이상하다.
 */
#define T_NONE      "NONE"
#define T_WAIT      "WAIT"
#define T_NO_REPLY  "NO REPLY"
#define T_BAD_DATA  "BAD DATA"

/* ---- 조회 ---------------------------------------------------------------- */

unsigned mk_screen_field_count(void) { return (unsigned)MK_SCREEN_FIELD_COUNT; }

const MkScreenField *mk_screen_field(const MkScreen *s, unsigned i)
{
    return i < (unsigned)MK_SCREEN_FIELD_COUNT ? &s->f[i] : &s->f[0];
}

unsigned mk_screen_ain_name_field(unsigned ch)   { return MK_SF_AIN_NAME0 + ch; }
unsigned mk_screen_ain_value_field(unsigned ch)  { return MK_SF_AIN_VALUE0 + ch; }
unsigned mk_screen_i2c_name_field(unsigned p)    { return MK_SF_I2C_NAME0 + p; }
unsigned mk_screen_i2c_value_field(unsigned p)   { return MK_SF_I2C_VALUE0 + p; }
unsigned mk_screen_din_value_field(void)         { return MK_SF_DIN_VALUE; }
unsigned mk_screen_time_value_field(void)        { return MK_SF_TIME_VALUE; }
unsigned mk_screen_rail_value_field(void)        { return MK_SF_RAIL_VALUE; }
unsigned mk_screen_system_head_field(void)       { return MK_SF_SYS_HEAD; }

/* ---- 배치 세우기 --------------------------------------------------------- */

/* 이 폭에 몇 글자가 들어가나.
 *
 * n 글자의 폭은 (n-1)*advance + width 이므로(마지막 자간은 안 센다)
 * n <= (w/scale + advance - width) / advance 다. */
static uint8_t chars_that_fit(const MkFont *f, unsigned w, unsigned scale)
{
    unsigned per = (unsigned)f->advance * scale;
    unsigned slack = ((unsigned)f->advance - f->width) * scale;
    unsigned n = (w + slack) / per;
    if (n > MK_SCREEN_TEXT_MAX - 1u) {
        n = MK_SCREEN_TEXT_MAX - 1u;
    }
    return (uint8_t)n;
}

static void place(MkScreen *s, unsigned idx, unsigned x, unsigned y,
                  unsigned w, unsigned h, unsigned scale, MkLcdColor fg)
{
    MkScreenField *f = &s->f[idx];
    f->x = (uint16_t)x;
    f->y = (uint16_t)y;
    f->w = (uint16_t)w;
    f->h = (uint16_t)h;
    f->scale = (uint8_t)scale;
    f->chars = chars_that_fit(s->font, w, scale);
    f->fg = fg;
}

/* 칸의 글자를 세운다. 달라졌으면 dirty. 반환 1 = 새로 달라졌다. */
static int set_text(MkScreenField *f, const char *text, MkLcdColor fg)
{
    /* 🔴 칸이 감당하는 글자 수를 넘으면 담지 않는다 — 잘라 담으면
     *    "24.15" 가 "24.1" 로 보이고 사람이 그것을 값으로 읽는다.
     *    여기까지 오는 것은 배치 실수이고, 시험이 배치를 따로 본다. */
    size_t n = strlen(text);
    if (n > f->chars) {
        text = "OVF";
        n = 3u;
    }
    if (n >= MK_SCREEN_TEXT_MAX) {
        n = MK_SCREEN_TEXT_MAX - 1u;
    }

    int same = (strcmp(f->text, text) == 0)
               && f->fg.r == fg.r && f->fg.g == fg.g && f->fg.b == fg.b;
    memcpy(f->text, text, n);
    f->text[n] = '\0';
    f->fg = fg;
    if (same) {
        return 0;
    }
    /* 🔴 이 한 줄이 "안 바뀌면 안 그린다" 의 전부다. 여기서 무조건
     *    dirty 로 두면 초당 4번 전면을 다시 그리게 되고, 1단계에서
     *    실기기로 확인한 "수집이 안 밀린다" 가 무너진다. */
    if (f->dirty) {
        return 0;               /* 이미 그릴 예정이었다 — 새로 생긴 것이 아니다 */
    }
    f->dirty = 1u;
    return 1;
}

void mk_screen_init(MkScreen *s, const MkScreenSources *src)
{
    memset(s, 0, sizeof *s);
    s->font = mk_font_ascii5x7();
    s->paint_idx = -1;
    if (src != NULL) {
        s->src = *src;
    }

    place(s, MK_SF_TITLE, HEAD_X, Y_TITLE, HEAD_W, ROW_H, SCALE_BIG, C_TITLE);
    place(s, MK_SF_AIN_HEAD, HEAD_X, Y_AIN_HEAD, HEAD_W, HEAD_H,
          SCALE_SMALL, C_HEAD);
    for (unsigned c = 0; c < MK_ADS_CHANNELS; c++) {
        unsigned y = Y_AIN0 + ROW_STEP * c;
        place(s, MK_SF_AIN_NAME0 + c, NAME_X, y, NAME_W, ROW_H,
              SCALE_BIG, C_NAME);
        place(s, MK_SF_AIN_VALUE0 + c, VAL_X, y, VAL_W, ROW_H,
              SCALE_BIG, C_VALUE);
    }

    place(s, MK_SF_I2C_HEAD, HEAD_X, Y_I2C_HEAD, HEAD_W, HEAD_H,
          SCALE_SMALL, C_HEAD);
    for (unsigned p = 0; p < MK_I2C_COUNT; p++) {
        unsigned y = Y_I2C0 + ROW_STEP * p;
        place(s, MK_SF_I2C_NAME0 + p, NAME_X, y, NAME_W, ROW_H,
              SCALE_BIG, C_NAME);
        place(s, MK_SF_I2C_VALUE0 + p, VAL_X, y, VAL_W, ROW_H,
              SCALE_BIG, C_VALUE);
    }

    place(s, MK_SF_DIN_HEAD, HEAD_X, Y_DIN_HEAD, HEAD_W, HEAD_H,
          SCALE_SMALL, C_HEAD);
    place(s, MK_SF_DIN_NAME, NAME_X, Y_DIN, NAME_W, ROW_H, SCALE_BIG, C_NAME);
    place(s, MK_SF_DIN_VALUE, VAL_X, Y_DIN, VAL_W, ROW_H, SCALE_BIG, C_VALUE);

    place(s, MK_SF_SYS_HEAD, HEAD_X, Y_SYS_HEAD, HEAD_W, HEAD_H,
          SCALE_SMALL, C_HEAD);
    place(s, MK_SF_TIME_NAME, NAME_X, Y_TIME, NAME_W, ROW_H, SCALE_BIG, C_NAME);
    place(s, MK_SF_TIME_VALUE, VAL_X, Y_TIME, VAL_W, ROW_H, SCALE_BIG, C_VALUE);
    place(s, MK_SF_RAIL_NAME, NAME_X, Y_RAIL, NAME_W, ROW_H, SCALE_BIG, C_NAME);
    place(s, MK_SF_RAIL_VALUE, VAL_X, Y_RAIL, VAL_W, ROW_H, SCALE_BIG, C_VALUE);

    /* ── 안 바뀌는 글자 ───────────────────────────────────────────────
     *
     * 🔴 커넥터 번호를 여기서 만든다. 핀 번호가 아니라 커넥터다
     *    (설계 원칙 1 — 화면은 `PD8` 이 아니라 사람이 보드에서 읽을 수
     *    있는 이름을 쓴다). */
    set_text(&s->f[MK_SF_TITLE], "MARKON STUDIO", C_TITLE);
    set_text(&s->f[MK_SF_AIN_HEAD], "ANALOG 4-20MA (J3-J9)", C_HEAD);
    set_text(&s->f[MK_SF_I2C_HEAD], "I2C SENSORS (J10-J15)", C_HEAD);
    set_text(&s->f[MK_SF_DIN_HEAD], "DIGITAL IN (OPTO)", C_HEAD);
    /* 🔴 설계 원칙 4 — 피드백 회로가 없다. 화면이 "정상 ON" 이라고 말하면
     *    안 된다. 값 칸(21글자)에는 그 단서를 못 넣으므로 구역 이름표가
     *    이고 간다 — 작은 글씨라 52글자가 들어간다. */
    set_text(&s->f[MK_SF_SYS_HEAD],
             "SYSTEM - RAIL SHOWS COMMAND, NOT MEASUREMENT", C_HEAD);
    set_text(&s->f[MK_SF_DIN_NAME], "OPTO", C_NAME);
    set_text(&s->f[MK_SF_TIME_NAME], "TIME", C_NAME);
    set_text(&s->f[MK_SF_RAIL_NAME], "RAIL", C_NAME);

    for (unsigned c = 0; c < MK_ADS_CHANNELS; c++) {
        /* 아날로그 채널 n = J(n+3) (데이터시트 §5.3). */
        char name[8];
        MkText t;
        mk_text_begin(&t, name, sizeof name);
        mk_text_putc(&t, 'J');
        mk_text_u32(&t, c + 3u);
        set_text(&s->f[MK_SF_AIN_NAME0 + c], name, C_NAME);
    }
    for (unsigned p = 0; p < MK_I2C_COUNT; p++) {
        char name[8];
        MkText t;
        mk_text_begin(&t, name, sizeof name);
        mk_text_putc(&t, 'J');
        mk_text_u32(&t, mk_i2c_connector_of(p));
        set_text(&s->f[MK_SF_I2C_NAME0 + p], name, C_NAME);
    }

    /* 값 칸은 아직 아무 말도 못 한다 — 빈칸으로 두지 않는다. */
    for (unsigned c = 0; c < MK_ADS_CHANNELS; c++) {
        set_text(&s->f[MK_SF_AIN_VALUE0 + c], T_NONE, C_DIM);
    }
    for (unsigned p = 0; p < MK_I2C_COUNT; p++) {
        set_text(&s->f[MK_SF_I2C_VALUE0 + p], T_NONE, C_DIM);
    }
    set_text(&s->f[MK_SF_DIN_VALUE], T_WAIT, C_DIM);
    set_text(&s->f[MK_SF_TIME_VALUE], T_WAIT, C_DIM);
    set_text(&s->f[MK_SF_RAIL_VALUE], T_WAIT, C_DIM);
}

void mk_screen_invalidate(MkScreen *s)
{
    for (unsigned i = 0; i < (unsigned)MK_SCREEN_FIELD_COUNT; i++) {
        s->f[i].shown[0] = '\0';
        s->f[i].dirty = 1u;
    }
}

/* ---- 값 읽어 오기 -------------------------------------------------------- */

/* "ain0.unit" 같은 키를 만든다. app/ 이라 snprintf 가 없다 (main.c 와 같은
 * 방식). */
static void ain_key(char *buf, size_t cap, unsigned ch, const char *suffix)
{
    MkText t;
    mk_text_begin(&t, buf, cap);
    mk_text_puts(&t, "ain");
    mk_text_u32(&t, ch);
    mk_text_puts(&t, suffix);
}

void mk_screen_collect(const MkScreenSources *src, MkScreenData *out)
{
    memset(out, 0, sizeof *out);
    if (src == NULL) {
        return;
    }

    /* ── 아날로그 ─────────────────────────────────────────────────── */
    for (unsigned c = 0; c < MK_ADS_CHANNELS; c++) {
        if (src->ads == NULL) {
            continue;
        }
        out->ain[c].enabled = mk_ads_channel_enabled(src->ads, (int)c) ? 1u : 0u;

        MkSample s;
        if (!mk_ads_last(src->ads, (int)c, &s)) {
            continue;
        }
        /* 🔴 규격 §7.2.1 의 환산식을 여기서 다시 쓰지 않는다.
         *    직렬화기(mk_cloud)가 전선으로 내보내는 값과 화면이 다르면
         *    어느 쪽이 맞는지 정할 방법이 없다 — raw -> mA 는 공용 환산을
         *    그대로 부르고, zero·scale 도 같은 설정 항목을 읽는다. */
        float ma = mk_ads_raw_to_ma(s.raw);
        float zero = 4.0f, scale = 1.0f;
        if (src->cfg != NULL) {
            char key[24];
            ain_key(key, sizeof key, c, ".zero");
            MkCfgItem *it = mk_cfg_find(src->cfg, key);
            if (it != NULL) { zero = it->cur.f; }
            ain_key(key, sizeof key, c, ".scale");
            it = mk_cfg_find(src->cfg, key);
            if (it != NULL) { scale = it->cur.f; }
            ain_key(key, sizeof key, c, ".unit");
            it = mk_cfg_find(src->cfg, key);
            if (it != NULL && it->cur.s[0] != '\0') { out->ain[c].unit = it->cur.s; }
        }
        out->ain[c].have = 1u;
        out->ain[c].value = (ma - zero) * scale;
    }

    /* ── I2C ──────────────────────────────────────────────────────── */
    for (unsigned p = 0; p < MK_I2C_COUNT; p++) {
        if (src->i2c == NULL) {
            continue;
        }
        const char *q[MK_I2C_VALUES_MAX] = { NULL, NULL };
        /* 🔴 "지금 무엇이 물려 있나" 는 설정표가 아니라 수집기가 안다.
         *    설정을 바꾼 직후에는 둘이 잠깐 다르고, 화면은 실제로 읽고
         *    있는 쪽을 말해야 한다. */
        uint8_t kind = src->i2c->port[p].kind;
        int n = mk_i2c_kind_quantities(kind, q);
        out->i2c[p].used = (kind != MK_I2C_KIND_NONE) ? 1u : 0u;
        out->i2c[p].n_slot = (uint8_t)n;
        for (int k = 0; k < n && k < MK_I2C_VALUES_MAX; k++) {
            out->i2c[p].slot[k].quantity = q[k];
            MkI2cOut o;
            if (mk_i2c_last(src->i2c, p, (unsigned)k, &o)) {
                out->i2c[p].slot[k].have = o.have_value ? 1u : 0u;
                out->i2c[p].slot[k].value = o.value;
                out->i2c[p].slot[k].status = o.status;
            }
        }
    }

    /* ── 디지털 입력 ──────────────────────────────────────────────── */
    if (src->sol != NULL) {
        for (int c = 0; c < MK_SOL_COUNT; c++) {
            out->din[c] = mk_solctl_is_on(src->sol, (MkSolCh)c) ? 1u : 0u;
        }
    }

    /* ── 시간축 ───────────────────────────────────────────────────── */
    if (src->timeax != NULL) {
        out->time_grade = (uint8_t)mk_timeax_grade(src->timeax);
        out->sats = mk_timeax_sats(src->timeax);
    }

    /* ── 전원 (🔴 명령 상태다 — 설계 원칙 4) ──────────────────────── */
    if (src->rails != NULL) {
        for (int r = 0; r < MK_RAIL_COUNT; r++) {
            out->rail[r] = mk_railctl_is_on(src->rails, (MkRail)r) ? 1u : 0u;
        }
    }
}

/* ---- 글자 만들기 --------------------------------------------------------- */

/* 규격 §7.5.1 의 어휘 -> 화면에 쓸 단위와 자릿수.
 *
 * 🔴 단위는 ASCII 로 적는다. 규격의 표는 `°C`·`%RH` 지만 이 글꼴에 그
 *    글자가 없다 — 전선으로 나가는 것과 화면에 보이는 것이 다른 자리다.
 *    (규격 §7.5 도 같은 이유로 `unit` 을 전선에 안 싣는다: "°C 는 비
 *    ASCII 라 펌웨어의 고정폭 문자열 버퍼와 어긋난다".)
 *
 * 🔴 자릿수는 하드웨어 사실이 아니라 **화면 판단**이다. 온습도는 0.1 이면
 *    충분하고, 적외 온도는 대상 온도라 소수 둘째 자리가 실제로 움직인다. */
static const char *quantity_unit(const char *q)
{
    if (strcmp(q, "lux") == 0)         { return "LX"; }
    if (strcmp(q, "humidity") == 0)    { return "%"; }
    return "C";                        /* temp · temp_object */
}

static int quantity_digits(const char *q)
{
    if (strcmp(q, "lux") == 0)         { return 0; }
    if (strcmp(q, "temp_object") == 0) { return 2; }
    return 1;                          /* temp · humidity */
}

/* 값 + 단위를 한 칸에 담는다. 단위 자리를 **먼저 떼어 놓고** 숫자에 남는
 * 칸을 준다 — 숫자를 먼저 채우면 단위가 밀려 잘린다. */
static void put_value(MkText *t, unsigned chars_left, float v, int digits,
                      const char *unit)
{
    size_t unit_len = (unit != NULL && unit[0] != '\0') ? strlen(unit) + 1u : 0u;
    size_t budget = chars_left > unit_len ? chars_left - unit_len : 0u;

    char num[24];
    if (budget + 1u > sizeof num) {
        budget = sizeof num - 1u;
    }
    mk_text_f32_fit(num, budget + 1u, v, digits);
    mk_text_puts(t, num);
    if (unit_len > 0u) {
        mk_text_putc(t, ' ');
        mk_text_puts(t, unit);
    }
}

static const char *grade_name(uint8_t grade)
{
    switch ((MkTimeAxGrade)grade) {
    case MK_TIMEAX_GNSS_PPS:  return "GNSS PPS";
    case MK_TIMEAX_GNSS_NMEA: return "GNSS NMEA";
    case MK_TIMEAX_DEVICE_CLOCK:
    default:                  return "DEV CLOCK";
    }
}

int mk_screen_apply(MkScreen *s, const MkScreenData *d)
{
    int changed = 0;
    char buf[MK_SCREEN_TEXT_MAX];
    MkText t;

    /* ── 아날로그 ─────────────────────────────────────────────────── */
    for (unsigned c = 0; c < MK_ADS_CHANNELS; c++) {
        MkScreenField *f = &s->f[MK_SF_AIN_VALUE0 + c];
        if (!d->ain[c].enabled) {
            changed += set_text(f, T_NONE, C_DIM);
            continue;
        }
        if (!d->ain[c].have) {
            changed += set_text(f, T_WAIT, C_DIM);
            continue;
        }
        mk_text_begin(&t, buf, sizeof buf);
        /* 🔴 자릿수 2 는 4~20 mA 루프의 분해능에 맞춘 값이다. 0~250 bar
         *    센서라면 16 mA 폭에 250 bar 이므로 0.01 bar 는 이미 잡음
         *    아래다 — 더 찍어도 화면만 흔들린다. */
        put_value(&t, f->chars, d->ain[c].value, 2, d->ain[c].unit);
        changed += set_text(f, buf, C_VALUE);
    }

    /* ── I2C ──────────────────────────────────────────────────────── */
    for (unsigned p = 0; p < MK_I2C_COUNT; p++) {
        MkScreenField *f = &s->f[MK_SF_I2C_VALUE0 + p];
        if (!d->i2c[p].used || d->i2c[p].n_slot == 0u) {
            changed += set_text(f, T_NONE, C_DIM);
            continue;
        }

        /* 🔴 상태는 슬롯마다 따로 오지만 한 포트의 슬롯들은 같은 칩에서
         *    온다 — 첫 슬롯의 상태로 칸 전체를 판단한다. */
        uint16_t st = d->i2c[p].slot[0].status;
        if (st == 3u) {
            /* 규격 §7.5: "status=3 은 고장이 아니다." 사용자 확정
             * 2026-08-19 로 이것도 "없음" 으로 그린다 — 배선을 뜯게
             * 만들면 안 된다. */
            changed += set_text(f, T_NONE, C_DIM);
            continue;
        }
        if (st == 1u) {
            changed += set_text(f, T_NO_REPLY, C_ALERT);
            continue;
        }
        if (st == 2u) {
            changed += set_text(f, T_BAD_DATA, C_ALERT);
            continue;
        }
        if (!d->i2c[p].slot[0].have) {
            changed += set_text(f, T_WAIT, C_DIM);
            continue;
        }

        mk_text_begin(&t, buf, sizeof buf);
        for (unsigned k = 0; k < d->i2c[p].n_slot && k < MK_I2C_VALUES_MAX; k++) {
            const char *q = d->i2c[p].slot[k].quantity;
            if (q == NULL || !d->i2c[p].slot[k].have) {
                continue;
            }
            if (mk_text_len(&t) > 0u) {
                mk_text_putc(&t, ' ');
            }
            put_value(&t, f->chars - (unsigned)mk_text_len(&t),
                      d->i2c[p].slot[k].value, quantity_digits(q),
                      quantity_unit(q));
        }
        changed += set_text(f, buf, C_VALUE);
    }

    /* ── 디지털 입력 ──────────────────────────────────────────────── */
    mk_text_begin(&t, buf, sizeof buf);
    for (int c = 0; c < MK_SOL_COUNT; c++) {
        if (c > 0) { mk_text_putc(&t, ' '); }
        mk_text_putc(&t, 'J');
        mk_text_u32(&t, mk_sol_connector_of((MkSolCh)c));
        mk_text_putc(&t, ' ');
        mk_text_putc(&t, d->din[c] ? '1' : '0');
    }
    changed += set_text(&s->f[MK_SF_DIN_VALUE], buf, C_VALUE);

    /* ── 시간축 ───────────────────────────────────────────────────── */
    mk_text_begin(&t, buf, sizeof buf);
    mk_text_puts(&t, grade_name(d->time_grade));
    mk_text_puts(&t, " SAT ");
    mk_text_u32(&t, d->sats);
    changed += set_text(&s->f[MK_SF_TIME_VALUE], buf,
                        d->time_grade == (uint8_t)MK_TIMEAX_DEVICE_CLOCK
                        ? C_DIM : C_VALUE);

    /* ── 전원 ─────────────────────────────────────────────────────── */
    {
        static const char *const RAIL_NAME[MK_RAIL_COUNT] = { "5V", "15V", "24V" };
        mk_text_begin(&t, buf, sizeof buf);
        for (int r = 0; r < MK_RAIL_COUNT; r++) {
            if (r > 0) { mk_text_putc(&t, ' '); }
            mk_text_puts(&t, RAIL_NAME[r]);
            mk_text_putc(&t, ' ');
            /* 🔴 "ON" 과 "--" 다. "OFF" 라고 쓰지 않는 이유는 이것이 명령
             *    상태이기 때문이다 — 안 켰다는 뜻이지 꺼져 있음을 확인한
             *    것이 아니다 (설계 원칙 4, 구역 이름표가 그것을 말한다). */
            mk_text_puts(&t, d->rail[r] ? "ON" : "--");
        }
        changed += set_text(&s->f[MK_SF_RAIL_VALUE], buf, C_VALUE);
    }

    return changed;
}

/* ---- 그리기 -------------------------------------------------------------- */

/* mk_lcd 가 행 하나를 채워 달라고 부른다.
 *
 * 🔴 즉시 돌아온다. 한 행이 258 화소이고 화소마다 나눗셈 둘이다 —
 *    Cortex-M7 에서 수 마이크로초다. 이 콜백에서 오래 머물면 그만큼
 *    슈퍼루프가 서고, 그것이 곧 수집 지연이다. */
static void screen_row(void *ctx, unsigned y_rel, unsigned w, uint8_t *out)
{
    MkScreen *s = (MkScreen *)ctx;
    if (s->paint_idx < 0) {
        return;
    }
    const MkScreenField *f = &s->f[s->paint_idx];

    unsigned gh = mk_glyph_text_height(s->font, f->scale);
    unsigned pad_y = f->h > gh ? (f->h - gh) / 2u : 0u;

    for (unsigned x = 0; x < w; x++) {
        int on = 0;
        if (y_rel >= pad_y) {
            on = mk_glyph_text_pixel(s->font, s->paint_text,
                                     x, y_rel - pad_y, f->scale);
        }
        const MkLcdColor *c = on ? &s->paint_fg : NULL;
        if (c != NULL) {
            mk_lcd_pixel(c->r, c->g, c->b, &out[x * MK_LCD_BYTES_PER_PIXEL]);
        } else {
            mk_lcd_pixel(MK_LCD_BG_R, MK_LCD_BG_G, MK_LCD_BG_B,
                         &out[x * MK_LCD_BYTES_PER_PIXEL]);
        }
    }
}

/* dirty 한 칸 하나를 맡긴다. 맡겼으면 1.
 *
 * 🔴 라운드로빈이다. 늘 0번부터 찾으면 위쪽 칸이 자주 바뀔 때 아래쪽
 *    칸이 영영 안 그려진다(mk_i2c_tick 의 next_port 와 같은 이유). */
static int submit_one(MkScreen *s, MkLcd *lcd)
{
    for (unsigned k = 0; k < (unsigned)MK_SCREEN_FIELD_COUNT; k++) {
        unsigned i = (s->next_scan + k) % (unsigned)MK_SCREEN_FIELD_COUNT;
        MkScreenField *f = &s->f[i];
        if (!f->dirty) {
            continue;
        }

        /* 🔴 그리는 동안 글자가 바뀌어도 흔들리지 않게 복사해 둔다. */
        size_t n = strlen(f->text);
        memcpy(s->paint_text, f->text, n + 1u);
        s->paint_fg = f->fg;
        s->paint_idx = (int)i;

        if (!mk_lcd_paint(lcd, f->x, f->y, f->w, f->h, screen_row, s)) {
            /* 받아 주지 않았다 — 다음 바퀴에 다시 온다. dirty 를 그대로
             * 둔다(안 그러면 그 칸이 영영 안 그려진다). */
            s->paint_idx = -1;
            return 0;
        }
        memcpy(f->shown, f->text, n + 1u);
        f->shown_fg = f->fg;
        f->dirty = 0u;
        s->next_scan = (i + 1u) % (unsigned)MK_SCREEN_FIELD_COUNT;
        return 1;
    }
    return 0;
}

int mk_screen_tick(MkScreen *s, MkLcd *lcd, int64_t now_ms)
{
    /* 🔴 패널이 다시 초기화됐으면 그린 것을 전부 잊는다. GRAM 이 우리가
     *    아는 내용이라는 보장이 없다 (mk_lcd_epoch 주석). */
    uint32_t epoch = mk_lcd_epoch(lcd);
    if (epoch != s->lcd_epoch) {
        s->lcd_epoch = epoch;
        mk_screen_invalidate(s);
    }

    int applied = 0;

    /* 🔴 갱신 주기. 사람이 읽는 화면이라 초당 2~4번이면 넘친다 —
     *    텔레메트리 주기(100 ms)를 따라갈 이유가 없고, 따라가면 SPI2 와
     *    슈퍼루프를 그만큼 더 쓴다. */
    if (s->src.cfg != NULL) {
        MkCfgItem *it = mk_cfg_find(s->src.cfg, "lcd.period_ms");
        int64_t period = it != NULL ? (int64_t)it->cur.u : 250;
        if (period < 1) { period = 1; }

        if (!s->primed || now_ms - s->last_apply_ms >= period) {
            MkScreenData d;
            mk_screen_collect(&s->src, &d);
            (void)mk_screen_apply(s, &d);
            s->last_apply_ms = now_ms;
            s->primed = 1;
            applied = 1;
        }
    }

    /* 🔴 화면이 놀고 있을 때만, 한 바퀴에 **한 칸**만 맡긴다. 여러 칸을
     *    한꺼번에 밀 방법도 없고(전송이 하나뿐이다) 밀 이유도 없다 —
     *    사람 눈에는 같은 순간이다. */
    if (mk_lcd_idle(lcd)) {
        (void)submit_one(s, lcd);
    }
    return applied;
}
