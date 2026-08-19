#include "mk_timeax.h"

#include <string.h>

void mk_timeax_init(MkTimeAx *tx)
{
    memset(tx, 0, sizeof *tx);
    tx->grade = MK_TIMEAX_DEVICE_CLOCK;
}

/* 정수부만 남기고 이 초의 시작(.000)으로 내린다. */
static int64_t floor_to_second(int64_t epoch_ms)
{
    int64_t rem = epoch_ms % 1000;
    if (rem < 0) {
        rem += 1000;             /* epoch_ms 는 UTC 이후라 실제로는 안 오지만 방어적으로 둔다 */
    }
    return epoch_ms - rem;
}

void mk_timeax_on_pps(MkTimeAx *tx, uint64_t dev_us)
{
    tx->pps_pending = 1;
    tx->pps_pending_dev_us = dev_us;
}

void mk_timeax_on_rmc(MkTimeAx *tx, const MkGnssRmc *rmc, uint64_t dev_us)
{
    if (!rmc->valid) {
        /* 🔴 무효 fix 는 신선도를 갱신하지 않는다(헤더 주석 — 설계 원칙
         *    3·4). 응답이 왔다고 믿을 만하다는 뜻은 아니다. */
        return;
    }

    tx->has_nmea = 1;
    tx->last_nmea_dev_us = dev_us;

    if (tx->pps_pending &&
        dev_us - tx->pps_pending_dev_us <= MK_TIMEAX_PAIR_WINDOW_US) {
        /* 🔴 짝짓기 성립 — 헤더 주석의 규칙대로 RMC 자신의 소수부는
         *    버리고 PPS 에지를 그 초의 진짜 .000 으로 삼는다. */
        tx->anchor_valid = 1;
        tx->anchor_epoch_ms = floor_to_second(rmc->epoch_ms);
        tx->anchor_dev_us = tx->pps_pending_dev_us;
        tx->pps_pending = 0;

        tx->has_pps = 1;
        tx->last_pps_dev_us = tx->pps_pending_dev_us;

        tx->grade = MK_TIMEAX_GNSS_PPS;
        return;
    }

    /* PPS 짝이 없다(아직 없거나 창을 넘겼다) — NMEA 단독으로도 gnss_pps
     * 보다는 낮은 등급으로 올릴 수 있다. 이미 gnss_pps 라면 여기서
     * 낮추지 않는다 — 이번 문장에 짝이 없었을 뿐 방금 전 PPS 정밀도가
     * 사라진 것은 아니다(신선도 하락은 mk_timeax_tick() 의 몫이다). */
    if (tx->grade < MK_TIMEAX_GNSS_NMEA) {
        tx->grade = MK_TIMEAX_GNSS_NMEA;
    }
    if (tx->grade == MK_TIMEAX_GNSS_NMEA) {
        tx->anchor_valid = 1;
        tx->anchor_epoch_ms = rmc->epoch_ms;   /* NMEA 단독 — 소수부까지 그대로 믿는다 */
        tx->anchor_dev_us = dev_us;
    }
}

void mk_timeax_on_gga(MkTimeAx *tx, const MkGnssGga *gga)
{
    tx->sats = gga->sats;
}

void mk_timeax_tick(MkTimeAx *tx, uint64_t dev_us)
{
    tx->last_seen_dev_us = dev_us;

    if (tx->grade == MK_TIMEAX_GNSS_PPS) {
        if (!tx->has_pps || dev_us - tx->last_pps_dev_us > MK_TIMEAX_PPS_STALE_US) {
            /* PPS 가 끊겼다 — RMC 가 아직 신선하면 gnss_nmea, 아니면
             * 곧장 device_clock. */
            tx->grade = (tx->has_nmea &&
                        dev_us - tx->last_nmea_dev_us <= MK_TIMEAX_NMEA_STALE_US)
                       ? MK_TIMEAX_GNSS_NMEA : MK_TIMEAX_DEVICE_CLOCK;
        }
    }
    if (tx->grade == MK_TIMEAX_GNSS_NMEA) {
        if (!tx->has_nmea || dev_us - tx->last_nmea_dev_us > MK_TIMEAX_NMEA_STALE_US) {
            tx->grade = MK_TIMEAX_DEVICE_CLOCK;
        }
    }
}

MkTimeAxGrade mk_timeax_grade(const MkTimeAx *tx)
{
    return tx->grade;
}

const char *mk_timeax_grade_name(MkTimeAxGrade grade)
{
    switch (grade) {
    case MK_TIMEAX_GNSS_PPS:  return "gnss_pps";
    case MK_TIMEAX_GNSS_NMEA: return "gnss_nmea";
    default:                  return "device_clock";
    }
}

uint8_t mk_timeax_time_quality(const MkTimeAx *tx)
{
    return (uint8_t)tx->grade;
}

uint8_t mk_timeax_sats(const MkTimeAx *tx)
{
    return tx->sats;
}

int64_t mk_timeax_now_ms(const MkTimeAx *tx, uint64_t dev_us)
{
    if (tx->grade != MK_TIMEAX_DEVICE_CLOCK && tx->anchor_valid) {
        int64_t delta_us = (int64_t)(dev_us - tx->anchor_dev_us);
        return tx->anchor_epoch_ms + delta_us / 1000;
    }
    return (int64_t)(dev_us / 1000u);
}

int64_t mk_timeax_pps_age_us(const MkTimeAx *tx, uint64_t dev_us)
{
    if (!tx->has_pps) {
        return -1;
    }
    return (int64_t)(dev_us - tx->last_pps_dev_us);
}

int64_t mk_timeax_pps_age_us_now(const MkTimeAx *tx)
{
    return mk_timeax_pps_age_us(tx, tx->last_seen_dev_us);
}

uint64_t mk_timeax_extend_ticks(uint32_t wrap_count, uint16_t raw_ccr,
                                uint32_t period, int update_pending)
{
    uint64_t base = (uint64_t)wrap_count * (uint64_t)period;
    if (update_pending) {
        if (raw_ccr >= period / 2u) {
            /* 뒤쪽 절반 — 롤오버 직전에 캡처됐는데 Update ISR 이 먼저
             * wrap_count 를 올렸을 수 있다. 하나 빼서 보정한다. */
            base -= period;
        }
        /* 앞쪽 절반이면 롤오버 이후 캡처 — wrap_count 를 그대로 쓴다. */
    }
    return base + raw_ccr;
}
