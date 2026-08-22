#include "mk_imu.h"

#include <string.h>

/* Table 2-14 (IMU type 64): 가속도 2g/32767, 자이로 250dps/32767. */
#define MK_IMU_ACCEL_LSB  (2.0f / 32767.0f)
#define MK_IMU_GYRO_LSB   (250.0f / 32767.0f)

void mk_imu_init(MkImu *m)
{
    memset(m, 0, sizeof *m);
}

/* ';' 뒤 본문에서 쉼표 필드 하나를 정수로. '*' 나 끝에서 멈춘다. */
static int next_long(const char **p, const char *end, long *out)
{
    const char *q = *p;
    int neg = 0;
    long v = 0;
    int any = 0;
    if (q < end && *q == '-') { neg = 1; q++; }
    while (q < end && *q >= '0' && *q <= '9') {
        v = v * 10 + (*q - '0');
        any = 1;
        q++;
    }
    if (!any) { return 0; }
    /* 소수부는 버린다 — 카운트 필드는 정수다. seconds 필드(소수)는 이
     * 파서로 읽되 정수부만 취해도 버릴 필드라 무해하다. */
    if (q < end && *q == '.') {
        q++;
        while (q < end && *q >= '0' && *q <= '9') { q++; }
    }
    if (q < end && (*q == ',' || *q == '*')) { q++; }
    *p = q;
    *out = neg ? -v : v;
    return 1;
}

int mk_imu_parse(const char *line, size_t len, MkImuSample *out)
{
    /* "#RAWIMUXA," 로 시작해야 한다 ('#' 는 조립기가 떼고 줄 수도 있어
     * 둘 다 받는다). */
    const char *p = line;
    const char *end = line + len;
    if (p < end && *p == '#') { p++; }
    if ((size_t)(end - p) < 9u || strncmp(p, "RAWIMUXA,", 9) != 0) {
        return 0;
    }

    /* 헤더는 ';' 까지 — 필드 수가 고정이 아니라 통째로 건너뛴다. */
    const char *semi = memchr(p, ';', (size_t)(end - p));
    if (semi == NULL) { return 0; }
    p = semi + 1;

    /* 본문: err, imutype, week, seconds, status, Z가속, -Y가속, X가속,
     *       Z자이로, -Y자이로, X자이로 */
    long v[11];
    uint32_t status = 0u;
    int have_status = 0;
    for (int k = 0; k < 5; k++) {          /* err..status — 앞 넷은 버린다 */
        if (k == 4) {
            /* status 는 16진("0ac00000")이다. bit21~31 이 온도라(Table
             * 2-15) 버리지 않고 읽는다 — 16진이 아니면 온도만 포기한다. */
            have_status = 1;
            while (p < end && *p != ',' && *p != '*') {
                int d;
                if (*p >= '0' && *p <= '9') { d = *p - '0'; }
                else if (*p >= 'a' && *p <= 'f') { d = *p - 'a' + 10; }
                else if (*p >= 'A' && *p <= 'F') { d = *p - 'A' + 10; }
                else { have_status = 0; d = 0; }
                status = (status << 4) | (uint32_t)d;
                p++;
            }
            if (p < end && *p == ',') { p++; }
        } else if (!next_long(&p, end, &v[k])) {
            return 0;
        }
    }
    long zacc, nyacc, xacc, zgyro, nygyro, xgyro;
    if (!next_long(&p, end, &zacc) || !next_long(&p, end, &nyacc) ||
        !next_long(&p, end, &xacc) || !next_long(&p, end, &zgyro) ||
        !next_long(&p, end, &nygyro) || !next_long(&p, end, &xgyro)) {
        return 0;
    }

    /* 🔴 축 복원 (헤더 주석): 문장은 Z, −Y, X 순서다. 전송된 값이 −Y 이니
     *    ay = −(전송값) 이다. */
    out->az = (float)zacc  * MK_IMU_ACCEL_LSB;
    out->ay = (float)-nyacc * MK_IMU_ACCEL_LSB;
    out->ax = (float)xacc  * MK_IMU_ACCEL_LSB;
    out->gz = (float)zgyro  * MK_IMU_GYRO_LSB;
    out->gy = (float)-nygyro * MK_IMU_GYRO_LSB;
    out->gx = (float)xgyro  * MK_IMU_GYRO_LSB;

    /* IMU 내부 온도 — Table 2-15: bit21~31 의 11비트 2의 보수,
     * 온도 = 값 × 0.125 + 23 (°C). 산술 시프트로 부호를 살린다. */
    if (have_status) {
        int32_t stemp = (int32_t)status >> 21;
        out->temp_c = (float)stemp * 0.125f + 23.0f;
        out->have_temp = 1;
    }

    out->valid = 1;
    return 1;
}

void mk_imu_feed(MkImu *m, uint8_t byte, int64_t now_ms)
{
    char c = (char)byte;

    if (c == '#') {                        /* 새 로그 시작 — mk_gnss 의 '$' 관례 */
        m->used = 0;
        m->in_line = 1;
        m->dropping = 0;
        m->line[m->used++] = '#';
        return;
    }
    if (!m->in_line) { return; }
    if (c == '\r') { return; }

    if (c == '\n') {
        if (!m->dropping) {
            MkImuSample s;
            memset(&s, 0, sizeof s);
            if (mk_imu_parse(m->line, m->used, &s)) {
                s.t_ms = now_ms;
                s.seq = m->last.seq + 1u;
                m->last = s;
            } else {
                m->parse_fail_count++;
            }
        }
        m->used = 0;
        m->in_line = 0;
        m->dropping = 0;
        return;
    }

    if (m->dropping) { return; }
    if (m->used + 1u >= sizeof m->line) {
        m->dropping = 1;                   /* 잘라 담지 않는다 — mk_gnss 관례 */
        return;
    }
    m->line[m->used++] = c;
}

int mk_imu_last(const MkImu *m, MkImuSample *out)
{
    if (!m->last.valid) { return 0; }
    *out = m->last;
    return 1;
}
