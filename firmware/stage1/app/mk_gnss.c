#include "mk_gnss.h"

#include <string.h>

/* ---- UTC -> epoch_ms ------------------------------------------------------
 *
 * Howard Hinnant 의 `days_from_civil` (공개된 정수 달력 알고리즘, chrono
 * "Date Algorithms"). 프로타입 그레고리력에서 임의의 연도에 대해 윤년을
 * 포함해 정확하다 — 400 규칙(2000 은 윤년, 2100 은 아니다)까지 나눗셈
 * 없이 정수 연산만으로 처리한다. */
static int64_t days_from_civil(int64_t y, int32_t m, int32_t d)
{
    y -= (m <= 2) ? 1 : 0;
    int64_t era = (y >= 0 ? y : y - 399) / 400;
    int32_t yoe = (int32_t)(y - era * 400);              /* [0, 399] */
    int32_t doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1; /* [0, 365] */
    int32_t doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;   /* [0, 146096] */
    return era * 146097 + (int64_t)doe - 719468;           /* 1970-01-01 원점 */
}

int64_t mk_gnss_utc_to_epoch_ms(int32_t year, int32_t month, int32_t day,
                                int32_t hour, int32_t minute, int32_t sec,
                                int32_t msec)
{
    int64_t days = days_from_civil(year, month, day);
    int64_t secs = days * 86400LL + (int64_t)hour * 3600LL
                  + (int64_t)minute * 60LL + (int64_t)sec;
    return secs * 1000LL + (int64_t)msec;
}

/* ---- 필드 토크나이저 ------------------------------------------------------- */

typedef struct {
    const char *p;
    size_t      len;
} Field;

/* [*cursor, end) 구간에서 다음 콤마까지를 잘라내고 커서를 콤마 다음으로
 * 옮긴다. 콤마가 없으면 끝까지가 마지막 필드다. */
static Field next_field(const char **cursor, const char *end)
{
    const char *p = *cursor;
    const char *start = p;
    while (p < end && *p != ',') {
        p++;
    }
    Field f;
    f.p = start;
    f.len = (size_t)(p - start);
    if (p < end) {
        p++;                /* 콤마를 건너뛴다 */
    }
    *cursor = p;
    return f;
}

static int is_digit(char c)
{
    return c >= '0' && c <= '9';
}

/* 필드 앞부분의 연속된 십진 숫자를 정수로 읽는다. 숫자가 하나도 없으면
 * -1(실패)을 돌려준다 — GGA 의 fix_quality·sats 처럼 빈 필드가 정상인
 * 자리에 쓴다. */
static int32_t parse_leading_int(Field f)
{
    if (f.len == 0 || !is_digit(f.p[0])) {
        return -1;
    }
    int32_t v = 0;
    for (size_t i = 0; i < f.len && is_digit(f.p[i]); i++) {
        v = v * 10 + (f.p[i] - '0');
    }
    return v;
}

/* hhmmss.sss (또는 .ss, 소수부 없음도 허용) 를 시/분/초/밀리초로 나눈다.
 * 길이가 6 미만이면 실패(0). */
static int parse_time_field(Field f, uint8_t *h, uint8_t *mi, uint8_t *s,
                            uint16_t *ms)
{
    if (f.len < 6) {
        return 0;
    }
    for (int i = 0; i < 6; i++) {
        if (!is_digit(f.p[i])) {
            return 0;
        }
    }
    *h  = (uint8_t)((f.p[0] - '0') * 10 + (f.p[1] - '0'));
    *mi = (uint8_t)((f.p[2] - '0') * 10 + (f.p[3] - '0'));
    *s  = (uint8_t)((f.p[4] - '0') * 10 + (f.p[5] - '0'));

    uint16_t frac = 0;
    if (f.len > 7 && f.p[6] == '.') {
        /* 소수 자리 최대 3자리만 쓴다(밀리초). 그 이상은 버림. */
        int digits = 0;
        uint16_t mul[3] = { 100, 10, 1 };
        for (size_t i = 7; i < f.len && digits < 3; i++, digits++) {
            if (!is_digit(f.p[i])) {
                break;
            }
            frac = (uint16_t)(frac + (uint16_t)(f.p[i] - '0') * mul[digits]);
        }
    }
    *ms = frac;
    return (*h < 24 && *mi < 60 && *s < 60) ? 1 : 0;
}

/* ddmmyy 를 연/월/일로 나눈다. 연도는 2000+yy 로 가정한다 — RMC 는 2자리
 * 연도뿐이라 세기 정보가 없다(위 헤더 주석 참고). */
static int parse_date_field(Field f, uint16_t *year, uint8_t *month, uint8_t *day)
{
    if (f.len != 6) {
        return 0;
    }
    for (int i = 0; i < 6; i++) {
        if (!is_digit(f.p[i])) {
            return 0;
        }
    }
    *day   = (uint8_t)((f.p[0] - '0') * 10 + (f.p[1] - '0'));
    *month = (uint8_t)((f.p[2] - '0') * 10 + (f.p[3] - '0'));
    *year  = (uint16_t)(2000 + (f.p[4] - '0') * 10 + (f.p[5] - '0'));
    return (*month >= 1 && *month <= 12 && *day >= 1 && *day <= 31) ? 1 : 0;
}

/* ---- 문장 종류 판정 --------------------------------------------------------
 *
 * 표준 NMEA 는 talker(2자) + type(3자) 를 주소 필드로 쓴다("GNRMC" 등).
 * talker 는 수신기·위성군에 따라 GP/GL/GA/GB/GN 등으로 갈리므로, 뒤쪽
 * 3글자(RMC/GGA)만 비교한다 — 어떤 talker 든 같은 필드 배치를 쓴다는 것이
 * NMEA0183 규격 자체다. */
static int addr_is(Field addr, const char *suffix3)
{
    return addr.len >= 3 &&
           memcmp(addr.p + addr.len - 3, suffix3, 3) == 0;
}

/* ---- 체크섬 ---------------------------------------------------------------- */

static int hex_nibble(char c)
{
    if (c >= '0' && c <= '9') { return c - '0'; }
    if (c >= 'A' && c <= 'F') { return c - 'A' + 10; }
    if (c >= 'a' && c <= 'f') { return c - 'a' + 10; }
    return -1;
}

/* ---- RMC/GGA 필드 채우기 --------------------------------------------------- */

static void parse_rmc(MkGnss *g, const char *body, size_t body_len)
{
    const char *cursor = body;
    const char *end = body + body_len;

    next_field(&cursor, end);                 /* addr — 이미 확인했다 */
    Field f_time   = next_field(&cursor, end);
    Field f_status = next_field(&cursor, end);
    next_field(&cursor, end);                 /* lat */
    next_field(&cursor, end);                 /* N/S */
    next_field(&cursor, end);                 /* lon */
    next_field(&cursor, end);                 /* E/W */
    next_field(&cursor, end);                 /* speed */
    next_field(&cursor, end);                 /* course */
    Field f_date = next_field(&cursor, end);

    uint8_t h, mi, s;
    uint16_t ms;
    uint16_t year;
    uint8_t month, day;

    if (!parse_time_field(f_time, &h, &mi, &s, &ms) ||
        !parse_date_field(f_date, &year, &month, &day)) {
        g->parse_fail_count++;
        return;                                /* 시각/날짜를 못 읽으면 못 믿는다 */
    }

    MkGnssRmc rmc;
    rmc.valid  = (f_status.len >= 1 && f_status.p[0] == 'A') ? 1 : 0;
    rmc.year   = year;
    rmc.month  = month;
    rmc.day    = day;
    rmc.hour   = h;
    rmc.minute = mi;
    rmc.sec    = s;
    rmc.msec   = ms;
    rmc.epoch_ms = mk_gnss_utc_to_epoch_ms(year, month, day, h, mi, s, ms);

    g->rmc = rmc;
    g->rmc_pending = 1;
}

static void parse_gga(MkGnss *g, const char *body, size_t body_len)
{
    const char *cursor = body;
    const char *end = body + body_len;

    next_field(&cursor, end);                 /* addr */
    next_field(&cursor, end);                 /* time */
    next_field(&cursor, end);                 /* lat */
    next_field(&cursor, end);                 /* N/S */
    next_field(&cursor, end);                 /* lon */
    next_field(&cursor, end);                 /* E/W */
    Field f_fix  = next_field(&cursor, end);
    Field f_sats = next_field(&cursor, end);

    int32_t fixq = parse_leading_int(f_fix);
    int32_t sats = parse_leading_int(f_sats);

    /* 🔴 빈 필드(파싱 실패)는 0 으로 본다 — "no fix" 문장은 실제로 이
     *    두 필드가 "0","00" 으로 명시되어 오지만, 만에 하나 완전히 비어
     *    오는 수신기가 있어도 값을 지어내지 않고 0(무효/0기)으로 둔다. */
    g->gga.fix_quality = (uint8_t)(fixq < 0 ? 0 : fixq);
    g->gga.sats        = (uint8_t)(sats < 0 ? 0 : sats);
    g->gga_pending = 1;
}

/* 완성된 한 줄(g->line[0..used)) 을 처리한다. '$' 와 '\r'/'\n' 은 이미
 * 빠진 상태다. */
static void process_line(MkGnss *g)
{
    const char *line = g->line;
    size_t len = g->used;

    /* 🔴 체크섬이 틀린 문장은 버린다 — 시각이 한 글자만 틀려도 시간축
     *    전체가 튄다(mk_gnss.h 헤더 주석). '*' 를 뒤에서부터 찾는다:
     *    필드 값 안에는 '*' 가 오지 않으므로 대개 마지막 3바이트가
     *    "*XX" 다. */
    if (len < 3) {
        g->checksum_fail_count++;
        return;
    }
    size_t star = len;                          /* '*' 의 위치, 없으면 len */
    for (size_t i = len; i-- > 0; ) {
        if (line[i] == '*') {
            star = i;
            break;
        }
    }
    if (star == len || len - star != 3) {
        g->checksum_fail_count++;                /* '*' 가 없거나 자리가 안 맞다 */
        return;
    }
    int hi = hex_nibble(line[star + 1]);
    int lo = hex_nibble(line[star + 2]);
    if (hi < 0 || lo < 0) {
        g->checksum_fail_count++;
        return;
    }
    uint8_t want = (uint8_t)((hi << 4) | lo);

    uint8_t got = 0;
    for (size_t i = 0; i < star; i++) {
        got = (uint8_t)(got ^ (uint8_t)line[i]);
    }
    if (got != want) {
        g->checksum_fail_count++;
        return;
    }

    /* 본문(주소 필드부터 '*' 전까지)에서 문장 종류를 가른다. */
    const char *cursor = line;
    const char *body_end = line + star;
    Field addr = next_field(&cursor, body_end);

    if (addr_is(addr, "RMC")) {
        parse_rmc(g, line, star);
    } else if (addr_is(addr, "GGA")) {
        parse_gga(g, line, star);
    }
    /* 그 외 문장은 조용히 버린다(mk_gnss.h — "나머지 문장은 조용히
     * 버린다"). */
}

/* ---- 공개 API --------------------------------------------------------------- */

void mk_gnss_init(MkGnss *g)
{
    memset(g, 0, sizeof *g);
}

void mk_gnss_feed(MkGnss *g, uint8_t byte)
{
    char c = (char)byte;

    /* '$' 는 언제나 새 문장의 시작이다 — 이미 문장을 조립하던 중이었어도
     * (잡음으로 끊긴 문장 뒤에 새 문장이 바로 붙는 경우) 그 자리에서
     * 다시 시작한다. 잘린 첫 조각은 그냥 버려진다. */
    if (c == '$') {
        g->used = 0;
        g->in_sentence = 1;
        g->dropping = 0;
        return;
    }

    if (!g->in_sentence) {
        return;                    /* '$' 을 아직 못 봤다 — 잡음, 버린다 */
    }

    if (c == '\r') {
        return;                    /* CR 은 담지 않는다(규격 §3.2 와 같은 관례) */
    }

    if (c == '\n') {
        if (!g->dropping) {
            process_line(g);
        }
        g->used = 0;
        g->in_sentence = 0;
        g->dropping = 0;
        return;
    }

    if (g->dropping) {
        return;                    /* 이미 너무 길어져 버리기로 한 줄 */
    }

    if (g->used + 1 >= sizeof g->line) {
        /* 🔴 잘라 담지 않는다(mk_uart.c 의 MK_RX_LINE_MAX 와 같은 이유) —
         *    앞부분만으로 체크섬이 우연히 맞아떨어지면 잘못된 시각을
         *    진짜처럼 받아들이게 된다. 줄바꿈까지 통째로 버린다. */
        g->dropping = 1;
        return;
    }
    g->line[g->used++] = c;
}

int mk_gnss_take_rmc(MkGnss *g, MkGnssRmc *out)
{
    if (!g->rmc_pending) {
        return 0;
    }
    *out = g->rmc;
    g->rmc_pending = 0;
    return 1;
}

int mk_gnss_take_gga(MkGnss *g, MkGnssGga *out)
{
    if (!g->gga_pending) {
        return 0;
    }
    *out = g->gga;
    g->gga_pending = 0;
    return 1;
}

uint32_t mk_gnss_checksum_fail_count(const MkGnss *g)
{
    return g->checksum_fail_count;
}

uint32_t mk_gnss_parse_fail_count(const MkGnss *g)
{
    return g->parse_fail_count;
}
