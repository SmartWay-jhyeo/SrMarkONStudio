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

/* 필드 앞부분의 부호 있는 십진수를 10^digits 배 정수로 읽는다(규격 §7.8.2).
 *
 * 🔴 부동소수를 쓰지 않는다. `float` 는 유효숫자가 약 7자리라 십진도 경도
 *    (10자리)를 담지 못하고, `double` 은 Cortex-M7 에서도 소프트 부동소수라
 *    비싸다. 어차피 전선에는 십진 문자열로 나가므로 정수만으로 충분하다
 *    (mk_gnss.h 의 MkGnssFix 주석).
 *
 * 반올림은 사사오입이다 — 버림으로 두면 마지막 자리가 늘 한쪽으로 치우친다.
 * digits 자리를 넘는 소수부는 반올림 판단에만 쓰고 버린다.
 *
 * 성공하면 1, 숫자가 하나도 없거나(빈 필드 — GNSS 에서는 정상이다)
 * int32 를 넘으면 0 이고 *out 은 건드리지 않는다. */
static int parse_scaled(Field f, int digits, int32_t *out)
{
    size_t i = 0;
    int neg = 0;
    if (i < f.len && (f.p[i] == '-' || f.p[i] == '+')) {
        neg = (f.p[i] == '-');
        i++;
    }

    int64_t v = 0;
    int seen = 0;
    for (; i < f.len && is_digit(f.p[i]); i++) {
        v = v * 10 + (int64_t)(f.p[i] - '0');
        seen = 1;
        if (v > 100000000LL) {
            return 0;              /* 자릿수가 말이 안 된다 — 지어내지 않는다 */
        }
    }
    if (!seen) {
        return 0;                  /* 빈 필드. GGA/RMC 에서 흔하다 */
    }

    int64_t place = 1;
    for (int d = 0; d < digits; d++) {
        v *= 10;
        place *= 10;
    }

    if (i < f.len && f.p[i] == '.') {
        i++;
        for (int d = 0; i < f.len && is_digit(f.p[i]); i++, d++) {
            if (d < digits) {
                place /= 10;
                v += (int64_t)(f.p[i] - '0') * place;
            } else if (d == digits) {
                if (f.p[i] - '0' >= 5) {
                    v += 1;        /* 사사오입 */
                }
            }
            /* d > digits 자리는 버린다 */
        }
    }

    if (v > 2147483647LL) {
        return 0;
    }
    *out = (int32_t)(neg ? -v : v);
    return 1;
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

/* 분(`mm.dddddddd`) 필드를 10^8 배 정수로 읽는다 — parse_scaled 와 달리
 * int32 로 클램프하지 않는다(도 변환 후 값이 int32 상한을 넘긴다, 헤더
 * 주석). 정수부는 호출 쪽이 이미 정확히 2자리로 잘라 넘기므로(아래
 * mk_gnss_ddmm_to_1e8) 오버플로 걱정이 없다.
 *
 * 🔴 반올림하지 않고 8자리까지만 담고 그 이상은 버린다 — **버릴 일이
 *    없다.** 실기기(UM981, 2026-08-20) 문장이 정확히 `mm.dddddddd`(분
 *    소수 8자리)라 9자리째 입력이 아예 안 온다. parse_scaled 의 반올림은
 *    "덜 정밀한 십진 자릿수 하나를 잘라낼 때" 필요한 것이지, 여기서는
 *    있는 자릿수를 그대로 옮기는 것뿐이다. */
static int parse_minute_1e8(Field f, int64_t *out)
{
    size_t i = 0;
    int64_t v = 0;
    int seen = 0;
    for (; i < f.len && is_digit(f.p[i]); i++) {
        v = v * 10 + (int64_t)(f.p[i] - '0');
        seen = 1;
    }
    if (!seen) {
        return 0;
    }

    int64_t place = 100000000LL;   /* 10^8 */
    v *= place;

    if (i < f.len && f.p[i] == '.') {
        i++;
        for (int d = 0; i < f.len && is_digit(f.p[i]) && d < 8; i++, d++) {
            place /= 10;
            v += (int64_t)(f.p[i] - '0') * place;
        }
        /* 9번째 이후 소수 자리는 버린다 — 실기기에는 오지 않는다(위 주석). */
    }

    *out = v;
    return 1;
}

/* ---- 도-분 -> 십진도 (규격 §7.8.2) ---------------------------------------- */

int mk_gnss_ddmm_to_1e8(const char *text, size_t len, char hemi, int is_lat,
                        int64_t *out)
{
    int neg;
    /* 🔴 축마다 허용 문자가 다르다. 위도 자리에 'E' 가 오면 그것은 필드
     *    순서를 잘못 읽은 것이고, 받아들이면 값은 그럴듯한데 위치가
     *    엉뚱해진다 — 거부해서 null 로 두는 편이 낫다. */
    if (is_lat) {
        if      (hemi == 'N') { neg = 0; }
        else if (hemi == 'S') { neg = 1; }
        else                  { return 0; }
    } else {
        if      (hemi == 'E') { neg = 0; }
        else if (hemi == 'W') { neg = 1; }
        else                  { return 0; }
    }

    /* 정수부는 `dd`(또는 `ddd`) + `mm` 이다 — **뒤 두 자리가 분**이다.
     * 🔴 여기가 이 함수 전체의 요점이다. `3719.1441` 을 그대로 십진도로
     *    읽으면 3719도이고, 소수점만 옮겨 `37.191441` 로 읽으면 실제
     *    위치에서 약 14 km 벗어난다 — 어느 쪽도 자릿수만 보면 이상하지
     *    않다(규격 §7.8.2). */
    size_t dot = len;
    for (size_t i = 0; i < len; i++) {
        if (text[i] == '.') { dot = i; break; }
    }
    if (dot < 3) {
        return 0;                  /* 도 최소 한 자리 + 분 두 자리가 안 된다 */
    }
    for (size_t i = 0; i < dot; i++) {
        if (!is_digit(text[i])) { return 0; }
    }

    size_t deg_len = dot - 2u;
    int32_t deg = 0;
    for (size_t i = 0; i < deg_len; i++) {
        deg = deg * 10 + (text[i] - '0');
    }

    Field mf;
    mf.p = text + deg_len;
    mf.len = len - deg_len;
    int64_t min_1e8;
    if (!parse_minute_1e8(mf, &min_1e8)) {
        return 0;
    }
    if (min_1e8 < 0 || min_1e8 >= 6000000000LL) {
        return 0;                  /* 분은 0~60 이다 */
    }

    /* 10^8 / (60 x 10^8) = 1/60 이라 나눗셈 하나로 끝난다. +30 은 사사오입
     * (60 의 절반). 분을 이미 10^8 스케일로 읽었으므로(parse_minute_1e8) 곱셈
     * 없이 바로 나눌 수 있다 — 예전 1e-7 버전의 "10^7/(60×10^6)=1/6" 과 같은
     * 구조다. */
    int64_t v = deg * 100000000LL + (min_1e8 + 30) / 60;
    int64_t limit = is_lat ? 9000000000LL : 18000000000LL;
    if (v > limit) {
        return 0;
    }
    *out = neg ? -v : v;
    return 1;
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

/* 시각 필드를 그날 자정부터의 ms 로. GGA(날짜 없음)와 RMC(날짜 있음)를
 * 짝지을 수 있는 유일한 공통 값이다(규격 §7.8.1). */
static int32_t tod_of(uint8_t h, uint8_t mi, uint8_t s, uint16_t ms)
{
    return ((int32_t)h * 3600 + (int32_t)mi * 60 + (int32_t)s) * 1000
           + (int32_t)ms;
}

/* 측위 레코드 한 칸을 큐에 넣는다(규격 §7.8). raw 에코 큐와 같은 관례 —
 * 가득 차면 가장 오래된 것을 버린다. RMC 는 1 Hz 이고 슈퍼루프는 그보다
 * 훨씬 자주 도므로 정상 상태에서는 일어나지 않는다. */
static void push_fix(MkGnss *g, const MkGnssFix *fix)
{
    g->fix_q[g->fix_head] = *fix;
    size_t next = (g->fix_head + 1u) % MK_GNSS_FIX_QUEUE_CAP;
    if (next == g->fix_tail) {
        g->fix_tail = (g->fix_tail + 1u) % MK_GNSS_FIX_QUEUE_CAP;
    }
    g->fix_head = next;
}

static void parse_rmc(MkGnss *g, const char *body, size_t body_len)
{
    const char *cursor = body;
    const char *end = body + body_len;

    next_field(&cursor, end);                 /* addr — 이미 확인했다 */
    Field f_time   = next_field(&cursor, end);
    Field f_status = next_field(&cursor, end);
    Field f_lat    = next_field(&cursor, end);
    Field f_ns     = next_field(&cursor, end);
    Field f_lon    = next_field(&cursor, end);
    Field f_ew     = next_field(&cursor, end);
    Field f_speed  = next_field(&cursor, end);
    Field f_course = next_field(&cursor, end);
    Field f_date = next_field(&cursor, end);

    uint8_t h, mi, s;
    uint16_t ms;
    uint16_t year;
    uint8_t month, day;

    int have_time = parse_time_field(f_time, &h, &mi, &s, &ms);
    int have_date = parse_date_field(f_date, &year, &month, &day);
    int valid = (f_status.len >= 1 && f_status.p[0] == 'A') ? 1 : 0;

    /* ---- 시간축 경로 (예전 그대로) ------------------------------------
     * 🔴 시각·날짜가 **둘 다** 있어야 RMC 를 내보낸다. epoch 을 만들 수
     *    없으면 시간축은 이 문장으로 할 수 있는 것이 없다. */
    if (have_time && have_date) {
        MkGnssRmc rmc;
        rmc.valid  = valid;
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
    } else {
        g->parse_fail_count++;
    }

    /* ---- 측위 레코드 경로 (규격 §7.8) ----------------------------------
     * 🔴 시간축과 달리 **날짜가 없어도 낸다.** 이 레코드의 요점은 시각이
     *    아니라 위치이고, 시각은 `fix_t` 라는 자기 자리에서 null 이 될 수
     *    있다(규격 §7.8.3). 날짜를 아직 모르는 수신기에서 위치까지 잃을
     *    이유가 없다. */
    MkGnssFix fix;
    memset(&fix, 0, sizeof fix);

    if (have_time && have_date) {
        fix.epoch_ms = mk_gnss_utc_to_epoch_ms(year, month, day, h, mi, s, ms);
        fix.have_epoch = 1;
    }

    /* 🔴 위·경도는 상태가 'A' 일 때만 싣는다. 'V' 인데 좌표 필드에 옛 값이
     *    남아 오는 수신기가 있고, 그것을 그대로 내보내면 화면에서 차량이
     *    마지막으로 하늘을 본 자리에 서 있게 된다(규격 §7.8.4). */
    if (valid) {
        int64_t lat, lon;
        if (mk_gnss_ddmm_to_1e8(f_lat.p, f_lat.len,
                                f_ns.len ? f_ns.p[0] : '?', 1, &lat) &&
            mk_gnss_ddmm_to_1e8(f_lon.p, f_lon.len,
                                f_ew.len ? f_ew.p[0] : '?', 0, &lon)) {
            fix.have_pos = 1;
            fix.lat_1e8 = lat;
            fix.lon_1e8 = lon;
        }
    }

    /* 🔴 노트가 아니라 m/s 로 낸다(규격 §7.8.5). 1 kn = 1852 m / 3600 s 는
     *    국제 해리의 정의라 정확한 정수비다 — 0.5144 로 어림하지 않는다. */
    int32_t kn_1e3;
    if (parse_scaled(f_speed, 3, &kn_1e3) && kn_1e3 >= 0) {
        fix.speed_mm_s = (int32_t)(((int64_t)kn_1e3 * 1852LL + 1800LL) / 3600LL);
        fix.have_speed = 1;
    }

    int32_t course_1e2;
    if (parse_scaled(f_course, 2, &course_1e2) &&
        course_1e2 >= 0 && course_1e2 <= 36000) {
        fix.course_1e2 = (uint16_t)course_1e2;
        fix.have_course = 1;
    }

    /* 🔴 GGA 에서 오는 값은 **시각이 같을 때만** 붙인다(규격 §7.8.1).
     *    두 문장의 순서는 규격이 정하지 않으므로, 짝을 안 맞추면 1초 전
     *    고도가 지금 위치에 붙는다 — 화면에는 아무 이상이 없어 보인다. */
    if (have_time && g->gga_have_tod && g->gga_tod_ms == tod_of(h, mi, s, ms)) {
        fix.have_fix = 1;
        fix.fix_quality = g->gga.fix_quality;
        fix.have_sats = 1;
        fix.sats = g->gga.sats;
        if (g->gga_have_alt) {
            fix.have_alt = 1;
            fix.alt_mm = g->gga_alt_mm;
        }
        if (g->gga_have_hdop) {
            fix.have_hdop = 1;
            fix.hdop_1e2 = g->gga_hdop_1e2;
        }
    }

    push_fix(g, &fix);
}

static void parse_gga(MkGnss *g, const char *body, size_t body_len)
{
    const char *cursor = body;
    const char *end = body + body_len;

    next_field(&cursor, end);                 /* addr */
    Field f_time = next_field(&cursor, end);
    next_field(&cursor, end);                 /* lat */
    next_field(&cursor, end);                 /* N/S */
    next_field(&cursor, end);                 /* lon */
    next_field(&cursor, end);                 /* E/W */
    Field f_fix  = next_field(&cursor, end);
    Field f_sats = next_field(&cursor, end);
    Field f_hdop = next_field(&cursor, end);
    Field f_alt  = next_field(&cursor, end);

    int32_t fixq = parse_leading_int(f_fix);
    int32_t sats = parse_leading_int(f_sats);

    /* 🔴 빈 필드(파싱 실패)는 0 으로 본다 — "no fix" 문장은 실제로 이
     *    두 필드가 "0","00" 으로 명시되어 오지만, 만에 하나 완전히 비어
     *    오는 수신기가 있어도 값을 지어내지 않고 0(무효/0기)으로 둔다. */
    g->gga.fix_quality = (uint8_t)(fixq < 0 ? 0 : fixq);
    g->gga.sats        = (uint8_t)(sats < 0 ? 0 : sats);
    g->gga_pending = 1;

    /* ---- 측위 레코드용 곁가지 (규격 §7.8) ------------------------------
     * RMC 가 완성될 때 시각이 같으면 실린다. 여기서 바로 레코드를 만들지
     * 않는 이유는 "레코드 하나 = RMC 하나" 이기 때문이다 — 두 문장 모두에서
     * 내면 같은 초의 반쪽짜리 레코드가 둘 나간다. */
    uint8_t h, mi, s;
    uint16_t ms;
    g->gga_have_tod = parse_time_field(f_time, &h, &mi, &s, &ms);
    g->gga_tod_ms = g->gga_have_tod ? tod_of(h, mi, s, ms) : 0;

    int32_t alt_mm;
    g->gga_have_alt = parse_scaled(f_alt, 3, &alt_mm);
    g->gga_alt_mm = g->gga_have_alt ? alt_mm : 0;

    int32_t hdop_1e2;
    /* HDOP 는 조건이 나쁘면 99 를 넘기도 한다. uint16 상한(655.35)을 넘는
     * 값은 어차피 "쓸 수 없다" 는 뜻이라 실을 값이 없다고 본다. */
    if (parse_scaled(f_hdop, 2, &hdop_1e2) &&
        hdop_1e2 >= 0 && hdop_1e2 <= 65535) {
        g->gga_have_hdop = 1;
        g->gga_hdop_1e2 = (uint16_t)hdop_1e2;
    } else {
        g->gga_have_hdop = 0;
        g->gga_hdop_1e2 = 0;
    }
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
        g->sentences_seen_count++;   /* 체크섬 통과 — 모듈이 말은 하고 있다 */
    } else if (addr_is(addr, "GGA")) {
        parse_gga(g, line, star);
        g->sentences_seen_count++;
    }
    /* 그 외 문장은 조용히 버린다(mk_gnss.h — "나머지 문장은 조용히
     * 버린다"). */
}

/* 완성된 줄(체크섬 통과 여부와 무관)을 원시 에코 큐에 담는다(규격 §7.7).
 * '$' 는 여기서 붙인다 — mk_gnss_feed 는 그 글자를 g->line 에 담지 않는다. */
static void push_raw_line(MkGnss *g)
{
    if (g->used == 0u) {
        return;                      /* "$\n" 처럼 빈 줄은 진단 가치가 없다 */
    }

    MkGnssRawLine *r = &g->raw_q[g->raw_head];
    size_t n = 0;
    r->text[n++] = '$';
    size_t copy = g->used;
    if (copy > sizeof r->text - 2u) {
        copy = sizeof r->text - 2u;   /* 이론상 g->line 보다 커서 안 된다 — 방어적 */
    }
    memcpy(r->text + n, g->line, copy);
    n += copy;
    r->text[n] = '\0';
    r->len = n;

    size_t next = (g->raw_head + 1u) % MK_GNSS_RAW_QUEUE_CAP;
    if (next == g->raw_tail) {
        /* 🔴 큐가 찼다 — 가장 오래된 것을 버린다. 진단용이라 최신이 더
         *    값지다(mk_gnss.h 주석). drops 를 세지 않는다 — 에코는 기본
         *    꺼짐이고 켰다면 사람이 화면을 보고 있는 진단 상황이라, 큐가
         *    밀리는 것 자체가 "너무 많이 온다"는 신호로 화면에 이미
         *    드러난다. */
        g->raw_tail = (g->raw_tail + 1u) % MK_GNSS_RAW_QUEUE_CAP;
    }
    g->raw_head = next;
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
            /* 🔴 원문 큐잉이 먼저다. process_line() 은 g->line 을 읽기만
             *    하므로 순서는 뜻이 없지만, "체크섬 실패와 무관하게
             *    담는다"는 계약을 코드로도 분명히 하려고 파싱 성패와
             *    상관없이 둘 다 부른다(규격 §7.7). */
            push_raw_line(g);
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

int mk_gnss_take_raw(MkGnss *g, char *out, size_t cap, size_t *out_len)
{
    if (g->raw_tail == g->raw_head || cap == 0u) {
        return 0;
    }
    const MkGnssRawLine *r = &g->raw_q[g->raw_tail];
    size_t n = r->len < cap - 1u ? r->len : cap - 1u;
    memcpy(out, r->text, n);
    out[n] = '\0';
    if (out_len != NULL) {
        *out_len = n;
    }
    g->raw_tail = (g->raw_tail + 1u) % MK_GNSS_RAW_QUEUE_CAP;
    return 1;
}

int mk_gnss_any_sentence_seen(const MkGnss *g)
{
    return g->sentences_seen_count > 0u ? 1 : 0;
}

int mk_gnss_take_fix(MkGnss *g, MkGnssFix *out)
{
    if (g->fix_tail == g->fix_head) {
        return 0;
    }
    *out = g->fix_q[g->fix_tail];
    g->fix_tail = (g->fix_tail + 1u) % MK_GNSS_FIX_QUEUE_CAP;
    return 1;
}
