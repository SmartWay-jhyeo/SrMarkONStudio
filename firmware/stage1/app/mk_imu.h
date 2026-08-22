/* UM981 RAWIMUX 파서 — '#' 로 시작하는 Unicore ASCII 로그.
 *
 * 근거: docs/datasheet/UM981_Auto_Commands_R1.0.pdf p.20 §2.3.5 —
 *   #RAWIMUXA,<헤더 9필드>;<err>,<imutype>,<week>,<sec>,<status>,
 *   <Z가속>,<-Y가속>,<X가속>,<Z자이로>,<-Y자이로>,<X자이로>*<crc32>
 * 환산 (p.21 Table 2-14, IMU type 64):
 *   가속도 카운트 × 2/32767 → g, 자이로 카운트 × 250/32767 → deg/s.
 *
 * 🔴 축 복원 — 문장의 순서는 Z, −Y, X 다(외함 좌표계). 계약 §7 의
 *    ax·ay·az 로 돌리려면 X↔Z 자리 재배열 + Y 부호 반전이 필요하다.
 *
 * 🔴 NMEA('$')와 조립기를 따로 둔다 — RAWIMUXA 는 ~130자로 NMEA 상한
 *    (MK_GNSS_LINE_MAX 96)을 넘고, CRC32(Unicore 방식)도 XOR 체크섬과
 *    다르다. mk_gnss 는 한 글자도 안 바뀐다. CRC32 검증은 A단계 범위
 *    밖(설계 계획 Task 6) — 필드 수·숫자 파싱 실패만 거른다.
 */
#ifndef MK_IMU_H
#define MK_IMU_H

#include <stddef.h>
#include <stdint.h>

#define MK_IMU_LINE_MAX  192

typedef struct {
    int     valid;
    float   ax, ay, az;      /* g */
    float   gx, gy, gz;      /* deg/s */
    /* IMU 내부(다이) 온도 — status 워드 bit21~31(2의 보수) × 0.125 + 23 °C
     * (Table 2-15). 환경 온도가 아니라 진단값이다. */
    int     have_temp;
    float   temp_c;
    int64_t t_ms;            /* 수신 시각(장치 ms) — 획득 시각의 근사 */
    uint32_t seq;            /* 표본마다 증가 — mk_cloud 의 새 표본 판정 */
} MkImuSample;

typedef struct {
    char    line[MK_IMU_LINE_MAX];
    size_t  used;
    int     in_line;         /* '#' 를 본 뒤 줄바꿈 전까지 1 */
    int     dropping;
    MkImuSample last;
    uint32_t parse_fail_count;
} MkImu;

void mk_imu_init(MkImu *m);

/* 한 바이트. '#'~'\n' 를 조립해 RAWIMUX 면 last 를 갱신한다.
 * now_ms 는 t_ms 로 들어간다. */
void mk_imu_feed(MkImu *m, uint8_t byte, int64_t now_ms);

/* '#' 없이 본문만 파싱한다 (시험용으로도 노출). 성공 1. */
int mk_imu_parse(const char *line, size_t len, MkImuSample *out);

/* 마지막 표본. 없으면 0. */
int mk_imu_last(const MkImu *m, MkImuSample *out);

#endif /* MK_IMU_H */
