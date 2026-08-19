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

/* UTC 달력 시각 -> epoch_ms. 윤년(그레고리력 400 규칙 포함)을 직접 계산한다.
 * 범위를 벗어난 입력(month 0/13, day 0/32 등)은 검증하지 않는다 — 호출
 * 쪽(mk_gnss.c 안)이 이미 NMEA 필드 길이를 확인한 뒤에만 부른다. */
int64_t mk_gnss_utc_to_epoch_ms(int32_t year, int32_t month, int32_t day,
                                int32_t hour, int32_t minute, int32_t sec,
                                int32_t msec);

#endif /* MK_GNSS_H */
