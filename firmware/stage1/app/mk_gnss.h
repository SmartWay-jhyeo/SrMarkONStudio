/* GNSS NMEA0183 파서 — HAL 비의존.
 *
 * 대상 모듈: UM981(ArduSimple AS-RTK3B-UM981-L125-NH), J16(데이터시트 §5.5).
 *
 * 🔴 UM982 가 아니라 UM981 이다(CLAUDE.md §1.2). LaneControlSystem Q1/Q2 의
 *    `um982.c` 참고 구현은 다른 모델이라 초기화 명령·독자 NMEA 확장을 그대로
 *    믿을 수 없다. UM981 데이터시트를 확보하지 못했으므로(§5, 근거 기반
 *    작업 — 확인 불가면 "확인 필요"라고 멈춘다) 이 파서는 **표준
 *    NMEA0183 RMC/GGA만** 다루고, 모델 고유 문장은 아예 건드리지 않는다.
 *    표준 문장의 필드 배치는 NMEA0183 규격 자체이지 제조사 문서가 아니라
 *    가정해도 안전하다 — RMC/GGA 는 30년 넘게 형식이 고정된 업계 표준이다.
 *
 * 🔴 바이트 단위로 먹인다(`mk_gnss_feed`). UART 가 인터럽트로 오든 DMA 로
 *    오든 이 모듈은 몰라도 된다 — bsp/mk_gnss_io.c 가 그 차이를 흡수한다.
 *
 * 🔴 체크섬이 틀린 문장은 버린다. NMEA 체크섬은 '$' 와 '*' 사이 전체 바이트의
 *    XOR 이다. 시각이 한 글자만 틀려도 시간축 전체가 튄다 — 체크섬 없이는
 *    그 한 글자를 잡을 방법이 없다.
 *
 * 🔴 `mktime()`(libc) 을 쓰지 않는다. 지역시간대·전역 상태(`tzset`, errno)에
 *    기대는 함수라 펌웨어에 맞지 않고, `app/` 은애초에 stdio 를 포함해 libc
 *    시간 함수를 부르지 않는 경계다. `mk_gnss_utc_to_epoch_ms()` 는 순수
 *    정수 산술로 윤년을 포함한 UTC -> epoch_ms 변환을 직접 구현한다
 *    (Howard Hinnant 의 `days_from_civil` — 그레고리력 400 규칙까지 다루는
 *    공개된 정수 알고리즘. 하드웨어 사실이 아니라 달력 수학이므로 데이터시트
 *    인용 대상이 아니다).
 */
#ifndef MK_GNSS_H
#define MK_GNSS_H

#include <stddef.h>
#include <stdint.h>

/* 🔴 NMEA0183 문장은 '$' 포함 82자가 상한이다(업계 관행). 헤더·체크섬·CRLF
 *    여유를 더해 넉넉히 잡는다 — mk_uart.h 의 MK_RX_LINE_MAX(192)보다는
 *    작아도 된다(이 줄은 GNSS 전용 UART 에서만 온다). */
#define MK_GNSS_LINE_MAX  96

/* $..RMC — 위치·시각·측위 유효성. */
typedef struct {
    int      valid;      /* 상태 필드 'A' = 1(유효) · 'V' 또는 그 외 = 0 */
    uint16_t year;        /* 4자리(2000+yy 가정 — RMC 의 ddmmyy 는 2자리뿐) */
    uint8_t  month;        /* 1~12 */
    uint8_t  day;          /* 1~31 */
    uint8_t  hour;          /* 0~23 UTC */
    uint8_t  minute;
    uint8_t  sec;
    uint16_t msec;         /* hhmmss.sss 의 소수부, 0~999 */
    int64_t  epoch_ms;      /* year..msec 로 계산된 UTC epoch. valid=0 이어도
                              * 필드 자체가 파싱 가능했으면 채워진다 — 호출
                              * 쪽이 valid 를 보고 신뢰 여부를 정한다. */
} MkGnssRmc;

/* $..GGA — 측위 품질·위성 수. */
typedef struct {
    uint8_t fix_quality;   /* 0=무효, 1=GPS fix, 2=DGPS, 4=RTK fixed, 5=RTK float 등 */
    uint8_t sats;           /* 사용 중인 위성 수 */
} MkGnssGga;

/* ---- 측위 레코드(규격 §7.8) -----------------------------------------------
 *
 * 🔴 위·경도를 float 로 담지 않는다. `float` 는 가수 24비트라 유효숫자가 약
 *    7자리인데, 십진도 경도는 `127.3405907` 로 10자리다 — float 에 넣으면
 *    약 1 m 가 조용히 사라진다. RTK 수신기를 단 이유가 그 자리에서 없어지고,
 *    값은 여전히 그럴듯해 보여서 아무도 눈치채지 못한다. `double` 은
 *    Cortex-M7 에서도 소프트 부동소수라 비싸고, 애초에 이 값들은 십진
 *    문자열로 나가므로 부동소수를 거칠 이유가 없다.
 *
 *    그래서 **1e-7 도 단위 정수**로 담는다. 1e-7 도 = 적도에서 약 1.11 cm 라
 *    RTK 의 정밀도와 자릿수가 맞고, 경도 최댓값 1,800,000,000 이 int32
 *    (2,147,483,647) 안에 들어간다. 도-분 -> 십진도 변환도 정수 산술만으로
 *    한다(mk_gnss.c 의 parse_latlon).
 *
 * 🔴 `have_*` 가 0 인 자리는 규격의 `null` 이다. 0 을 지어내지 않는다
 *    (설계 원칙 3·4, 규격 §7.8.4).
 */
typedef struct {
    /* 이 위치가 측정된 순간(규격 §7.8.3 의 `fix_t`). RMC 의 시각+날짜에서
     * 온다. **언제나 UTC 이며 time_source 와 무관하다.** */
    int64_t epoch_ms;
    int     have_epoch;

    /* RMC 상태가 'A' 이고 위·경도를 읽었을 때만 1. 남위·서경은 음수. */
    int     have_pos;
    int32_t lat_1e7;
    int32_t lon_1e7;

    /* GGA 에서 온다 — **시각 필드가 같은** GGA 를 봤을 때만 채워진다
     * (규격 §7.8.1). 두 문장의 순서가 규격으로 정해져 있지 않아, 짝을
     * 안 맞추면 1초 전 고도가 지금 위치에 붙는다. */
    int     have_alt;
    int32_t alt_mm;            /* 해발 고도, mm */
    int     have_sats;
    uint8_t sats;
    int     have_fix;
    uint8_t fix_quality;
    int      have_hdop;
    uint16_t hdop_1e2;         /* 수평 정밀도 저하율 × 100 */

    /* RMC 에서 온다. */
    int     have_speed;
    int32_t speed_mm_s;        /* 대지 속도, mm/s (노트에서 변환됨) */
    int      have_course;
    uint16_t course_1e2;       /* 대지 방위(진북 °) × 100, 0~36000 */
} MkGnssFix;

/* 측위 레코드 큐(규격 §7.8). RMC 는 1 Hz 이고 슈퍼루프는 그보다 훨씬 자주
 * 도므로 두 칸이면 넉넉하다 — raw 에코 큐(4칸)보다 작은 이유는 이쪽이
 * 문장마다가 아니라 **RMC 마다** 한 칸을 쓰기 때문이다. */
#define MK_GNSS_FIX_QUEUE_CAP  2u

/* 🔴 모듈로 명령 한 줄을 내보내는 계약(규격 §4.1). 줄바꿈은 구현이
 *    붙인다 — 여기 오는 text 에는 CR/LF 가 없다. 성공하면 1, 실패(버퍼
 *    부족 등)면 0.
 *
 *    $GNSS 명령 전달(mk_hostlink)과 켤 때 초기화 명령(mk_gnssctl)이 이
 *    계약 하나를 공유한다 — bsp 구현은 하나(mk_gnss_io_write_line)뿐이다.
 *    여기 두는 이유는 파싱(수신)과 명령(송신)이 "GNSS 모듈과 말한다"는
 *    같은 개념의 양면이기 때문이다. */
typedef int (*MkGnssSend)(void *ctx, const char *text, size_t len);

/* 원시 문장 에코 큐 한 칸(규격 §7.7) — '$' 포함, CR/LF 제외 그대로 담는다.
 * 체크섬 통과 여부와 무관하게 담는다: "왜 파싱이 안 되는지" 를 보려고
 * 만든 채널이라 실패한 줄도 값이 있다. */
#define MK_GNSS_RAW_QUEUE_CAP  4u

typedef struct {
    char   text[MK_GNSS_LINE_MAX + 2];   /* '$' + line[0..used) + NUL */
    size_t len;
} MkGnssRawLine;

/* 파서 상태. 필드가 전부 내부용이라 헤더에 두는 이유는 스택에 두고 쓰기
 * 위해서다(mk_solctl.h 의 MkSolCtl 과 같은 관례). */
typedef struct MkGnss {
    /* 문장 조립 버퍼. '$' 다음부터 '\r'/'\n' 전까지 담는다('$' 자체와
     * 줄바꿈은 담지 않는다). */
    char   line[MK_GNSS_LINE_MAX];
    size_t used;
    int    in_sentence;    /* '$' 를 본 뒤 줄바꿈 전까지 1 */
    int    dropping;        /* 버퍼 상한을 넘겨 이 줄을 통째로 버리는 중 */

    MkGnssRmc rmc;
    int       rmc_pending;   /* take 되지 않은 새 RMC 가 있다 */
    MkGnssGga gga;
    int       gga_pending;

    uint32_t checksum_fail_count;   /* 체크섬이 틀려 버린 문장 수(진단용) */
    uint32_t parse_fail_count;      /* 체크섬은 맞았지만 필드를 못 읽은 수 */

    /* 원시 문장 에코 큐(규격 §7.7). 링. head 는 다음에 쓸 자리,
     * tail 은 다음에 꺼낼 자리 — mk_gnss_io.c 의 UART 링과 같은 관례다. */
    MkGnssRawLine raw_q[MK_GNSS_RAW_QUEUE_CAP];
    size_t        raw_head;
    size_t        raw_tail;

    /* 🔴 체크섬이 통과한 RMC·GGA 를 한 번이라도 받았는가 — 부팅 이후
     *    누적이며 절대 내려가지 않는다(mk_gnss_init 에서만 0). "모듈이
     *    말은 하고 있다" 는 사실 자체를 보는 값이라 RMC 의 상태 필드
     *    (A/V)와는 무관하다. mk_gnssctl 이 초기화 재시도를 멈출 근거로
     *    쓰고, $STAT 의 gnss.sentence_seen 이 그대로 싣는다(규격 §7.4). */
    uint32_t sentences_seen_count;

    /* ---- 측위 레코드(규격 §7.8) --------------------------------------- */

    /* 마지막 GGA 에서 뽑아 둔 값들. RMC 가 완성될 때 **시각이 같으면**
     * 그 레코드에 실린다. `gga_tod_ms` 는 그날 자정부터의 ms 다 — 날짜가
     * 없는 GGA 와 날짜가 있는 RMC 를 비교할 수 있는 유일한 공통 값이다. */
    int32_t  gga_tod_ms;
    int      gga_have_tod;
    int      gga_have_alt;
    int32_t  gga_alt_mm;
    int      gga_have_hdop;
    uint16_t gga_hdop_1e2;

    /* 측위 레코드 링. raw_q 와 같은 관례(head = 다음에 쓸 자리). */
    MkGnssFix fix_q[MK_GNSS_FIX_QUEUE_CAP];
    size_t    fix_head;
    size_t    fix_tail;
} MkGnss;

void mk_gnss_init(MkGnss *g);

/* 바이트 하나를 먹인다. 줄이 완성되면(체크섬까지 확인해) 내부에 결과를
 * 쌓는다 — 호출 스택에서 즉시 결과를 돌려주지 않는다(mk_solctl 의
 * take 패턴과 같다), 슈퍼루프가 원하는 때에 take 로 꺼내 쓴다. */
void mk_gnss_feed(MkGnss *g, uint8_t byte);

/* 새 RMC 가 있으면 out 에 담고 1, 없으면 0(out 은 건드리지 않는다). */
int mk_gnss_take_rmc(MkGnss *g, MkGnssRmc *out);

/* 새 GGA 가 있으면 out 에 담고 1, 없으면 0. */
int mk_gnss_take_gga(MkGnss *g, MkGnssGga *out);

uint32_t mk_gnss_checksum_fail_count(const MkGnss *g);
uint32_t mk_gnss_parse_fail_count(const MkGnss *g);

/* 새 원시 줄이 있으면 out 에 담고(NUL 종료) 1, 없으면 0(규격 §7.7).
 * out_len 이 NULL 이 아니면 길이(NUL 제외)를 채운다. cap 이 모자라면
 * 잘라 담는다 — 그래도 NUL 로 끝난 문자열이어야 mk_json_str 이 안전하다. */
int mk_gnss_take_raw(MkGnss *g, char *out, size_t cap, size_t *out_len);

/* 체크섬이 통과한 RMC·GGA 를 한 번이라도 받았으면 1, 아직이면 0. */
int mk_gnss_any_sentence_seen(const MkGnss *g);

/* 새 측위 레코드가 있으면 out 에 담고 1, 없으면 0 (규격 §7.8).
 *
 * 🔴 `mk_gnss_take_rmc` 와 **별개의 큐**다. 저쪽은 시간축(mk_timeax)이
 *    비우고 이쪽은 텔레메트리(mk_telem)가 비운다 — 하나를 둘이 나눠 가지면
 *    먼저 꺼낸 쪽만 값을 보고 다른 쪽은 영영 못 본다. */
int mk_gnss_take_fix(MkGnss *g, MkGnssFix *out);

/* 도-분(`ddmm.mmmmmmmm`) 문자열을 1e-7 도 정수로. 부동소수를 쓰지 않는다.
 *
 * `hemi` 는 'N'/'S'/'E'/'W' 중 하나이며 'S'·'W' 면 음수가 된다. 성공하면
 * 1 이고 `*out` 을 채운다. 자릿수가 안 맞거나 범위(위도 ±90, 경도 ±180)를
 * 벗어나면 0 이고 `*out` 은 건드리지 않는다.
 *
 * 🔴 헤더에 올린 이유는 시험이 이 변환 하나만 겨눠야 하기 때문이다.
 *    도-분을 그대로 십진도로 쓰면 위치가 수십 km 어긋나는데 값은
 *    그럴듯해 보인다(규격 §7.8.2) — 문장 파싱 전체로만 시험하면 그 자리가
 *    가려진다. */
int mk_gnss_ddmm_to_1e7(const char *text, size_t len, char hemi, int is_lat,
                        int32_t *out);

/* UTC 달력 시각 -> epoch_ms. 윤년(그레고리력 400 규칙 포함)을 직접 계산한다.
 * 범위를 벗어난 입력(month 0/13, day 0/32 등)은 검증하지 않는다 — 호출
 * 쪽(mk_gnss.c 안)이 이미 NMEA 필드 길이를 확인한 뒤에만 부른다. */
int64_t mk_gnss_utc_to_epoch_ms(int32_t year, int32_t month, int32_t day,
                                int32_t hour, int32_t minute, int32_t sec,
                                int32_t msec);

#endif /* MK_GNSS_H */
