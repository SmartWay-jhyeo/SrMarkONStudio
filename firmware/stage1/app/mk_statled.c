#include "mk_statled.h"

#include <string.h>

#include "mk_queue.h"
#include "mk_ads1256.h"  /* mk_ads_raw_to_ma — mA 환산의 유일 출처 */

/* ── 색상 ────────────────────────────────────────────────────────────────
 * 🔴 노랑과 주황을 확실히 가른다. WS2812 에서 (255,255,0)과 (255,80,0)은
 *    눈으로도 다르다 — 초록 성분이 어중간하면 거리가 조금만 멀어져도
 *    같은 색으로 보인다. */
static const MkRgb OFF    = {   0,   0,   0 };
static const MkRgb GREEN  = {   0, 255,   0 };
static const MkRgb YELLOW = { 255, 255,   0 };
static const MkRgb ORANGE = { 255,  60,   0 };
static const MkRgb RED    = { 255,   0,   0 };

/* 문제 = 점멸 (400ms 주기). 켜짐 구간이 더 길다 — 꺼짐이 길면 "죽었나"
 * 와 헷갈린다. */
static int blink_on(int64_t now_ms)
{
    return (now_ms % 800) < 500;
}

/* 진행 = 숨쉬기 (2초 삼각파, 15%~100%). 바닥을 0 으로 안 두는 이유:
 * 완전히 꺼지는 순간이 있으면 "깜빡임(문제)"과 구분이 안 된다. */
static MkRgb breathe(MkRgb c, int64_t now_ms)
{
    int64_t t = now_ms % 2000;
    int64_t tri = t < 1000 ? t : 2000 - t;          /* 0..1000..0 */
    uint32_t lvl = (uint32_t)(150u + (850u * (uint32_t)tri) / 1000u);
    MkRgb out;
    out.r = (uint8_t)((c.r * lvl) / 1000u);
    out.g = (uint8_t)((c.g * lvl) / 1000u);
    out.b = (uint8_t)((c.b * lvl) / 1000u);
    return out;
}

static uint32_t cfg_flag(MkConfig *cfg, const char *key)
{
    MkCfgItem *it = mk_cfg_find(cfg, key);
    return it != NULL ? it->cur.u : 0u;
}

/* "i2c12.enabled" 를 손으로 만든다 — app/ 에는 snprintf 가 없다. */
static uint32_t i2c_flag(MkConfig *cfg, unsigned jack, const char *suffix)
{
    char key[20];
    int n = 0;
    key[n++] = 'i'; key[n++] = '2'; key[n++] = 'c';
    key[n++] = (char)('0' + jack / 10u);
    key[n++] = (char)('0' + jack % 10u);
    for (const char *p = suffix; *p; p++) { key[n++] = *p; }
    key[n] = '\0';
    return cfg_flag(cfg, key);
}

void mk_statled_init(MkStatLed *s, MkConfig *cfg, MkAds *ads,
                     int woke_from_iwdg)
{
    memset(s, 0, sizeof *s);
    s->cfg = cfg;
    s->ads = ads;
    s->woke_from_iwdg = (woke_from_iwdg != 0);
}

void mk_statled_attach_i2c(MkStatLed *s, MkI2c *i2c)  { s->i2c = i2c; }
void mk_statled_attach_gnss(MkStatLed *s, MkGnss *g)  { s->gnss = g; }

/* ── LED1: 시스템 ─────────────────────────────────────────────────────── */

static MkRgb led_system(MkStatLed *s, int64_t now_ms)
{
    /* 워치독 부활 표시 — 부팅 후 5초. RAM 설정이 날아간 채 도는 중이라는
     * 신호이기도 하다(CLAUDE.md §4 IWDG 항목). */
    if (s->woke_from_iwdg && now_ms < 5000) {
        return ORANGE;
    }

    uint32_t drops = 0;
    for (int ch = 0; ch < MK_ADS_CHANNELS; ch++) {
        MkQueue *q = mk_ads_queue(s->ads, ch);
        if (q != NULL) {
            drops += mk_queue_drops(q);
        }
    }
    if (drops != s->last_drops) {
        s->last_drops = drops;
        s->drops_bad_until = now_ms + 5000;
    }
    if (now_ms < s->drops_bad_until) {
        return blink_on(now_ms) ? RED : OFF;
    }

    return breathe(GREEN, now_ms);
}

/* ── LED2: 측위 품질 ──────────────────────────────────────────────────── */

static MkRgb led_fix(MkStatLed *s, int64_t now_ms)
{
    if (s->gnss == NULL || !cfg_flag(s->cfg, "gnss.enabled")) {
        return OFF;
    }

    uint32_t count = mk_gnss_fix_count(s->gnss);
    if (count != s->last_fix_count) {
        s->last_fix_count = count;
        s->last_fix_ms = now_ms;
        s->fix_seen = 1;
    }
    if (!s->fix_seen ||
        now_ms - s->last_fix_ms > MK_STATLED_FIX_STALE_MS) {
        return blink_on(now_ms) ? RED : OFF;        /* 수신 자체가 없다 */
    }

    MkGnssFix f;
    if (!mk_gnss_last_fix(s->gnss, &f)) {
        return blink_on(now_ms) ? RED : OFF;
    }
    switch (f.fix_quality) {
    case 4:  return GREEN;                          /* RTK fixed */
    case 5:  return YELLOW;                         /* RTK float */
    case 1:
    case 2:  return ORANGE;                         /* 단독·DGPS */
    default: return blink_on(now_ms) ? RED : OFF;   /* 0 = 무효 */
    }
}

/* ── LED3: 센서 건강 ──────────────────────────────────────────────────── */

static MkRgb led_sensors(MkStatLed *s, int64_t now_ms)
{
    int any_enabled = 0;
    int fault = 0;
    int warn = 0;

    for (int ch = 0; ch < MK_ADS_CHANNELS; ch++) {
        if (!mk_ads_channel_enabled(s->ads, ch)) {
            continue;
        }
        any_enabled = 1;
        MkSample last;
        if (!mk_ads_last(s->ads, ch, &last) ||
            now_ms - last.t_ms > MK_STATLED_STALE_MS) {
            fault = 1;                              /* 값 두절 */
            continue;
        }
        if (mk_ads_raw_to_ma(last.raw) < MK_STATLED_BREAK_MA) {
            warn = 1;                               /* 단선 의심 */
        }
    }

    if (s->i2c != NULL) {
        for (unsigned jack = 10; jack <= 15; jack++) {
            if (!i2c_flag(s->cfg, jack, ".enabled")) {
                continue;
            }
            uint8_t kind = (uint8_t)i2c_flag(s->cfg, jack, ".kind");
            if (kind == 0u) {
                continue;                           /* 종류 미지정 = 안 돈다 */
            }
            any_enabled = 1;
            const char *names[MK_I2C_VALUES_MAX];
            int slots = mk_i2c_kind_quantities(kind, names);
            for (int slot = 0; slot < slots; slot++) {
                MkI2cOut o;
                if (!mk_i2c_last(s->i2c, jack - 10u, (unsigned)slot, &o)) {
                    continue;      /* 아직 값 없음 — WARMUP 중, 오탐 금지 */
                }
                /* status 1·2 = 실패 (규격 §7.5). 3(미지원)은 정상 취급 —
                 * MLX 의 주변온도처럼 없는 것이 정상인 슬롯이 있다. */
                if (o.status == 1u || o.status == 2u) {
                    fault = 1;
                }
            }
        }
    }

    if (!any_enabled) {
        return OFF;
    }
    if (fault) {
        return blink_on(now_ms) ? RED : OFF;
    }
    if (warn) {
        return YELLOW;
    }
    return GREEN;
}

void mk_statled_colors(MkStatLed *s, int64_t now_ms,
                       MkRgb out[MK_STATLED_COUNT])
{
    out[0] = led_system(s, now_ms);
    out[1] = led_fix(s, now_ms);
    out[2] = led_sensors(s, now_ms);
}
