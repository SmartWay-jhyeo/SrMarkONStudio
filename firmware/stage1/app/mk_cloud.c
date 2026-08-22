#include "mk_cloud.h"

#include <string.h>

#include "mk_cfgtable.h"     /* 필드 표 — 마스크 비트의 유일한 출처 */
#include "mk_json.h"
/* 🔴 줄 상한은 본선(MK_LINE_MAX=192)과 별개다 [2026-08-22]. 젯슨 파서는
 * json.loads 라 줄 길이 제한이 없고, gnss 레코드(공통+위치+품질+선택
 * 필드)는 200 바이트를 쉽게 넘는다 — 실제로 alt 하나 켜자 198 상한에
 * 걸려 레코드가 통째로 사라졌다(시험이 잡았다). 512 는 모든 필드를 다
 * 켠 gnss(~300 B)에 여유를 둔 값이고 mk_jet 링(4096)의 1/8 이다. */
#define MK_CLOUD_LINE_MAX  512
#include "mk_telem.h"        /* mk_telem_raw_to_ma — 환산식은 한 곳(규격 §7.2.1) */

/* ── ain 채널의 클라우드 타입 표 ─────────────────────────────────────────
 *
 * 🔴 인덱스가 카탈로그의 ain{n}.cloud 열거값이다 (mk_cfgtable.c 의
 *    AIN_CLOUD_LABELS 와 같은 순서). 전선 문자열은 여기에만 있다 —
 *    화면 이름표("도료 분사압")와 계약 문자열(pressure_paint)을 한 곳에
 *    섞으면 규격 §7.3 의 이름표 규칙이 깨진다. */
static const struct {
    const char *type;        /* 계약 §2 의 record type */
    const char *field;       /* 값 필드 이름 (계약의 단위 약어 관례) */
} AIN_CLOUD[] = {
    { NULL,             NULL  },          /* 0 = 없음 → 미발행 (계약 §16.6) */
    { "pressure_paint", "bar" },
    { "pressure_bead",  "bar" },
    { "flow",           "lpm" },
};

/* ── 설정 접근 (mk_telem.c 의 ain_key 와 같은 요령 — app 층은 stdio 금지) ── */

static void ain_key(char *dst, size_t cap, int ch, const char *suffix)
{
    size_t n = 0;
    const char *p;
    for (p = "ain"; *p && n + 1u < cap; p++) dst[n++] = *p;
    if (n + 1u < cap) dst[n++] = (char)('0' + ch);
    for (p = suffix; *p && n + 1u < cap; p++) dst[n++] = *p;
    dst[n] = '\0';
}

static MkCfgItem *ain_item(MkConfig *cfg, int ch, const char *suffix)
{
    char key[MK_CFG_KEY_MAX + 1];
    ain_key(key, sizeof key, ch, suffix);
    return mk_cfg_find(cfg, key);
}

static uint32_t ain_u32(MkConfig *cfg, int ch, const char *suffix, uint32_t def)
{
    MkCfgItem *it = ain_item(cfg, ch, suffix);
    return it ? it->cur.u : def;
}

static float ain_f32(MkConfig *cfg, int ch, const char *suffix, float def)
{
    MkCfgItem *it = ain_item(cfg, ch, suffix);
    return it ? it->cur.f : def;
}

/* i2c 키 — "i2c10.cloud" 처럼 커넥터 번호 두 자리가 들어간다. */
static MkCfgItem *i2c_item(MkConfig *cfg, unsigned port, const char *suffix)
{
    unsigned jack = port + 10u;
    char key[MK_CFG_KEY_MAX + 1];
    size_t n = 0;
    const char *p;
    for (p = "i2c"; *p && n + 1u < sizeof key; p++) key[n++] = *p;
    if (n + 2u < sizeof key) {
        key[n++] = (char)('0' + jack / 10u);
        key[n++] = (char)('0' + jack % 10u);
    }
    for (p = suffix; *p && n + 1u < sizeof key; p++) key[n++] = *p;
    key[n] = '\0';
    return mk_cfg_find(cfg, key);
}

static uint32_t i2c_u32(MkConfig *cfg, unsigned port, const char *suffix,
                        uint32_t def)
{
    MkCfgItem *it = i2c_item(cfg, port, suffix);
    return it ? it->cur.u : def;
}

/* ── i2c 의 quantity → 클라우드 타입 표 (설계 §4.3) ──────────────────────
 *
 * 🔴 종류가 물리량을 이미 확정하므로 선택이 아니라 유도다. 표에 없는
 *    quantity(예: MLX90614 의 주변 온도가 새로 생기면)는 미발행 — 계약에
 *    없는 타입을 지어내지 않는다. */
static const struct {
    const char *quantity;    /* 규격 v3 §7.5.1 의 어휘 */
    const char *type;        /* 계약의 record type */
    const char *field;
} I2C_CLOUD[] = {
    { "temp_object", "temp_road", "degc" },
    { "lux",         "light",     "lux"  },
    { "temp",        "temp_air",  "degc" },   /* 계약 v1.7.0 신설 */
    { "humidity",    "humidity",  "pct"  },   /* 〃 */
};

/* ── 밸브 ────────────────────────────────────────────────────────────────
 *
 * 🔴 밸브 상태 = 디지털 입력들의 OR (사용자 확정 2026-08-22). 좌·우 두
 *    밸브가 어느 입력에서 올지 모르는 현장 구성이라 지정 대신 합성한다 —
 *    어느 쪽이든 열려 있으면 "도색 중(1)". 예비 입력은 미배선이라 항상
 *    0 이므로 셋 전부 OR 해도 무해하다. 미확정·sol 미연결은 0 — "모름"을
 *    표현할 수 없는 계약이라 보수적인 쪽(저장 안 함)으로 간다. */
static int cloud_valve_state(const MkCloud *c)
{
    if (c->sol == NULL) { return 0; }
    for (unsigned ch = 0; ch < MK_SOL_COUNT; ch++) {
        if (c->sol->confirmed_valid[ch] && c->sol->confirmed_state[ch]) {
            return 1;
        }
    }
    return 0;
}

/* ── 필드 마스크 (2026-08-22) ────────────────────────────────────────────
 *
 * 🔴 선택 필드의 켬/끔은 **본선과 같은 마스크**(tx.fields_*)가 정한다 —
 *    사용자 확정: "기존 NDJSON 필드 선택 화면이 곧 이 링크의 구성이다".
 *    비트 번호는 필드 표(mk_cfgtable_fields)에서 이름으로 찾는다 —
 *    mk_telem.c 의 field_on 과 같은 규칙(kind 밖의 비트는 무시). */
static int cloud_field_on(MkConfig *cfg, const char *mask_key,
                          const char *name, uint8_t kind)
{
    MkCfgItem *it = mk_cfg_find(cfg, mask_key);
    uint32_t mask = it ? it->cur.u : 0u;
    size_t n = 0;
    const MkFieldBit *f = mk_cfgtable_fields(&n);
    for (size_t i = 0; i < n; i++) {
        if (strcmp(f[i].name, name) == 0) {
            if ((f[i].kinds & kind) == 0u) { return 0; }
            return (mask & (1u << f[i].bit)) != 0u;
        }
    }
    return 0;
}

/* 마스크의 valve 비트가 켜져 있으면 레코드 끝에 밸브 상태를 붙인다. */
static void maybe_tag_valve(MkCloud *c, MkJson *j,
                            const char *mask_key, uint8_t kind)
{
    if (cloud_field_on(c->cfg, mask_key, "valve", kind)) {
        mk_json_u32(j, "valve", (uint32_t)cloud_valve_state(c));
    }
}

/* ── 공통 필드 (계약 §1) ────────────────────────────────────────────────── */

/* 계약 §1 의 time_source 이름. 규격 v3 의 등급 이름과 다르다 —
 * gnss_pps 를 계약은 "gnss" 라 부른다 (설계 §4.1의 매핑 표). */
static const char *cloud_time_source(const MkCloud *c)
{
    if (c->timeax == NULL) { return "device_clock"; }
    switch (mk_timeax_grade(c->timeax)) {
    case MK_TIMEAX_GNSS_PPS:  return "gnss";
    case MK_TIMEAX_GNSS_NMEA: return "gnss_nmea";
    default:                  return "device_clock";
    }
}

/* 획득 시각(장치 ms)을 시간축으로 — mk_telem 의 acquired_epoch_ms 와 같은
 * 변환. 계약 §1: t 는 UTC epoch ms 이며 "실제 측정 시각"이다 (설계 원칙 2).
 * device_clock 등급이면 값이 부팅 후 ms 그대로인데, 그것도 계약이 정의한
 * 상태다 — time_source 가 신뢰도를 말한다. */
static int64_t cloud_epoch_ms(const MkCloud *c, int64_t acq_ms)
{
    if (c->timeax == NULL) { return acq_ms; }
    return mk_timeax_now_ms_monotonic(c->timeax, (uint64_t)acq_ms * 1000ULL);
}

/* 공통 필드 넷을 연다. 계약 예시와 같은 순서 —
 * schema_ver, device_id, t, type, time_source.
 *
 * 🔴 schema_ver 와 device_id 는 설정이다 (사용자 확정 2026-08-22 — 공통
 *    필드 중 값을 바꿀 수 있는 둘). device_id 는 기존 dev.id 를 그대로
 *    쓴다 — 항목을 둘로 만들면 화면의 장치 ID 와 전선의 device_id 가
 *    갈라진다. t·time_source 는 사실을 말하는 필드라 설정이 아니다. */
static void begin_record(const MkCloud *c, MkJson *j, char *out, size_t cap,
                         const char *type, int64_t acq_ms)
{
    MkCfgItem *ver = mk_cfg_find(c->cfg, "tx.schema_ver");
    MkCfgItem *dev = mk_cfg_find(c->cfg, "dev.id");

    mk_json_begin(j, out, cap);
    mk_json_u32(j, "schema_ver", ver ? ver->cur.u : 1u);
    mk_json_str(j, "device_id",
                (dev && dev->cur.s[0] != '\0') ? dev->cur.s
                : (c->device_id ? c->device_id : ""));
    mk_json_i64(j, "t", cloud_epoch_ms(c, acq_ms));
    mk_json_str(j, "type", type);
    mk_json_str(j, "time_source", cloud_time_source(c));
}

/* 조립이 끝난 줄을 내보낸다. 상한을 넘으면 통째로 버린다 — 반쪽 JSON 을
 * 내보내지 않는 본선(emit_ain_sample)과 같은 계약. 성공 1, 버림 0. */
static int finish_and_emit(char *body, size_t cap, int len,
                           MkCloudEmit emit, void *ctx)
{
    if (len <= 0 || (size_t)len + 2u > cap) { return 0; }
    body[len] = '\n';
    body[len + 1] = '\0';
    emit(ctx, body, (size_t)len + 1u);
    return 1;
}

/* ── ain ────────────────────────────────────────────────────────────────── */

static int build_ain(MkCloud *c, int ch, const MkSample *s,
                     char *out, size_t cap)
{
    uint32_t sel = ain_u32(c->cfg, ch, ".cloud", 0u);
    if (sel == 0u || sel >= sizeof AIN_CLOUD / sizeof *AIN_CLOUD) { return 0; }

    MkJson j;
    begin_record(c, &j, out, cap, AIN_CLOUD[sel].type, s->t_ms);

    /* 계약 §9·§10 — 값은 환산 실수, 소수 1자리 (계약 예시의 자릿수.
     * tx.float_digits 는 본선 방언의 설정이라 여기 적용하지 않는다). */
    float ma = mk_telem_raw_to_ma(s->raw);
    float zero = ain_f32(c->cfg, ch, ".zero", 4.0f);
    float scale = ain_f32(c->cfg, ch, ".scale", 1.0f);
    mk_json_f32(&j, AIN_CLOUD[sel].field, (ma - zero) * scale, 1);

    /* 선택 필드 — 본선과 같은 마스크(tx.fields_ain)가 정한다 (2026-08-22).
     * ma·raw 는 v1.7.1 의 선택 필드로 등재된다. */
    if (cloud_field_on(c->cfg, "tx.fields_ain", "ma", MK_FIELD_AIN)) {
        mk_json_f32(&j, "ma", ma, 3);
    }
    if (cloud_field_on(c->cfg, "tx.fields_ain", "raw", MK_FIELD_AIN)) {
        mk_json_i64(&j, "raw", (int64_t)s->raw);
    }
    maybe_tag_valve(c, &j, "tx.fields_ain", MK_FIELD_AIN);

    return mk_json_end(&j);
}

static int tick_ain(MkCloud *c, MkCloudEmit emit, void *ctx)
{
    int sent = 0;
    for (int ch = 0; ch < MK_ADS_CHANNELS; ch++) {
        MkSample s;
        if (!mk_ads_last(c->ads, ch, &s)) { continue; }
        /* 🔴 같은 표본을 두 번 내지 않는다 — 발행 시점 = 수집 시점
         *    (설계 §4.7). 획득 시각이 그 판정 기준이다. */
        if (c->ain_primed[ch] && c->ain_sent_t[ch] == s.t_ms) { continue; }

        char body[MK_CLOUD_LINE_MAX];
        int len = build_ain(c, ch, &s, body, sizeof body);
        if (len == 0 && ain_u32(c->cfg, ch, ".cloud", 0u) == 0u) {
            /* 없음 = 미발행. 표본은 소비한 것으로 친다 — 안 그러면 켜는
             * 순간 묵은 표본이 소급 발행된다. */
            c->ain_primed[ch] = 1u;
            c->ain_sent_t[ch] = s.t_ms;
            continue;
        }
        if (finish_and_emit(body, sizeof body, len, emit, ctx)) {
            sent++;
            c->ain_primed[ch] = 1u;
            c->ain_sent_t[ch] = s.t_ms;
        }
    }
    return sent;
}

/* ── i2c ────────────────────────────────────────────────────────────────── */

/* 같은 포트의 다른 슬롯에서 quantity 로 값을 찾는다 (주변온도·이슬점용
 * 곁값). 있으면 1. */
static int sibling_value(MkCloud *c, unsigned port, const char *quantity,
                         float *out_v)
{
    for (unsigned k = 0; k < MK_I2C_VALUES_MAX; k++) {
        MkI2cOut o;
        if (!mk_i2c_last(c->i2c, port, k, &o)) { continue; }
        if (o.have_value && o.quantity != NULL &&
            strcmp(o.quantity, quantity) == 0) {
            *out_v = o.value;
            return 1;
        }
    }
    return 0;
}

/* 자연로그 — libm 을 쓰지 않는다. newlib-nano 의 logf 가 __errno 를 끌고
 * 와 nosys 링크가 깨졌다(실측 2026-08-22). x = m·2^e 로 나눠
 * ln x = e·ln2 + 2·atanh((m-1)/(m+1)) — m∈[1,2) 에서 급수 4항이면
 * 오차 < 1e-6 으로 이슬점 0.1°C 에 차고 넘친다. x>0 전제(호출부가 지킨다). */
static float mk_lnf(float x)
{
    union { float f; uint32_t u; } v = { x };
    int e = (int)((v.u >> 23) & 0xFFu) - 127;      /* 지수 */
    v.u = (v.u & 0x007FFFFFu) | 0x3F800000u;       /* 가수만 남겨 [1,2) */
    float m = v.f;
    float t = (m - 1.0f) / (m + 1.0f);
    float t2 = t * t;
    float s = t * (1.0f + t2 * (1.0f / 3.0f
                   + t2 * (1.0f / 5.0f + t2 * (1.0f / 7.0f))));
    return (float)e * 0.69314718f + 2.0f * s;
}

/* 이슬점 (Magnus 공식, °C·%RH -> °C). 근사식 Td≈T-(100-RH)/5 는 RH<50%
 * 에서 1도 넘게 틀려 쓰지 않는다 — "그럴듯하게 틀린 값" 금지. */
static float dewpoint_c(float t, float rh)
{
    const float a = 17.62f, b = 243.12f;
    float g = mk_lnf(rh / 100.0f) + a * t / (b + t);
    return b * g / (a - g);
}

static int build_i2c(MkCloud *c, unsigned port, const MkI2cOut *o,
                     char *out, size_t cap)
{
    if (!o->have_value || o->quantity == NULL) { return 0; }

    for (size_t k = 0; k < sizeof I2C_CLOUD / sizeof *I2C_CLOUD; k++) {
        if (strcmp(I2C_CLOUD[k].quantity, o->quantity) == 0) {
            MkJson j;
            begin_record(c, &j, out, cap, I2C_CLOUD[k].type, o->t_ms);
            mk_json_f32(&j, I2C_CLOUD[k].field, o->value, 1);

            /* 선택 필드 (마스크, 2026-08-22) — 해당 레코드에만 뜻이 있다:
             * 주변온도는 temp_road(적외) 에, 이슬점은 humidity(온습도) 에. */
            float v;
            if (strcmp(I2C_CLOUD[k].type, "temp_road") == 0 &&
                cloud_field_on(c->cfg, "tx.fields_i2c", "temp_ambient",
                               MK_FIELD_I2C) &&
                sibling_value(c, port, "temp_ambient", &v)) {
                mk_json_f32(&j, "ambient_degc", v, 1);
            }
            if (strcmp(I2C_CLOUD[k].type, "humidity") == 0 &&
                cloud_field_on(c->cfg, "tx.fields_i2c", "dewpoint",
                               MK_FIELD_I2C) &&
                sibling_value(c, port, "temp", &v)) {
                /* o->value = 습도 %, v = 같은 센서의 온도 */
                if (o->value > 0.0f && o->value <= 100.0f) {
                    mk_json_f32(&j, "dewpoint_degc",
                                dewpoint_c(v, o->value), 1);
                }
            }
            maybe_tag_valve(c, &j, "tx.fields_i2c", MK_FIELD_I2C);
            return mk_json_end(&j);
        }
    }
    return 0;                        /* 계약에 없는 물리량은 미발행 */
}

static int tick_i2c(MkCloud *c, MkCloudEmit emit, void *ctx)
{
    int sent = 0;
    if (c->i2c == NULL) { return 0; }
    for (unsigned p = 0; p < MK_I2C_COUNT; p++) {
        /* 별도 발행 스위치는 없다(사용자 확정 2026-08-22) — `사용`이 켜져
         * 값이 오고 있으면 나간다. last 는 켜진 포트만 채워진다. */
        for (unsigned k = 0; k < MK_I2C_VALUES_MAX; k++) {
            MkI2cOut o;
            if (!mk_i2c_last(c->i2c, p, k, &o)) { continue; }
            if (c->i2c_primed[p][k] && c->i2c_sent_t[p][k] == o.t_ms) {
                continue;            /* 같은 표본은 두 번 안 낸다 (설계 §4.7) */
            }
            char body[MK_CLOUD_LINE_MAX];
            int len = build_i2c(c, p, &o, body, sizeof body);
            if (len == 0) {
                /* 계약 밖 물리량·값 없음 — 소비만 하고 넘어간다. */
                c->i2c_primed[p][k] = 1u;
                c->i2c_sent_t[p][k] = o.t_ms;
                continue;
            }
            if (finish_and_emit(body, sizeof body, len, emit, ctx)) {
                sent++;
                c->i2c_primed[p][k] = 1u;
                c->i2c_sent_t[p][k] = o.t_ms;
            }
        }
    }
    return sent;
}

/* ── gnss (계약 §3) ──────────────────────────────────────────────────────
 *
 * 🔴 계약의 모든 필드가 필수라, 하나라도 없으면 **레코드 자체를 안 낸다**
 *    (설계 §4.4 — 필드만 빼는 것은 계약 위반이다). fix 유효(have_pos)에
 *    GGA 짝(fix/sat/hdop/cs)까지 갖춰진 fix 만 나간다. */

static int build_gnss(MkCloud *c, const MkGnssFix *f, char *out, size_t cap)
{
    if (!f->have_pos || !f->have_epoch || !f->have_fix || !f->have_sats ||
        !f->have_hdop || !f->have_cs) {
        return 0;
    }

    MkJson j;
    MkCfgItem *ver = mk_cfg_find(c->cfg, "tx.schema_ver");
    MkCfgItem *dev = mk_cfg_find(c->cfg, "dev.id");
    mk_json_begin(&j, out, cap);
    mk_json_u32(&j, "schema_ver", ver ? ver->cur.u : 1u);
    mk_json_str(&j, "device_id",
                (dev && dev->cur.s[0] != '\0') ? dev->cur.s
                : (c->device_id ? c->device_id : ""));
    /* 🔴 t 는 fix 의 측정 시각(RMC 유래 UTC epoch)이다 — cloud_epoch_ms()
     *    변환을 거치지 않는다. 이 값은 시간축 등급과 무관하게 이미 UTC 다
     *    (MkGnssFix.epoch_ms 주석). */
    mk_json_i64(&j, "t", f->epoch_ms);
    mk_json_str(&j, "type", "gnss");
    mk_json_str(&j, "time_source", cloud_time_source(c));
    mk_json_i64(&j, "lat_e8", f->lat_1e8);
    mk_json_i64(&j, "lon_e8", f->lon_1e8);
    mk_json_u32(&j, "fix", f->fix_quality);
    mk_json_u32(&j, "sat", f->sats);
    mk_json_u32(&j, "hdop_x100", f->hdop_1e2);
    mk_json_str(&j, "cs", f->cs);

    /* 선택 필드 — 기존 gnss 마스크 비트 그대로 (alt·speed·course 는
     * 원래 있던 체크박스, diff_age·station_id 는 2026-08-22 신설). */
    if (f->have_alt &&
        cloud_field_on(c->cfg, "tx.fields_gnss", "alt", MK_FIELD_GNSS)) {
        mk_json_fixed(&j, "alt", f->alt_mm, 3);          /* mm -> m, 소수 3 */
    }
    if (f->have_speed &&
        cloud_field_on(c->cfg, "tx.fields_gnss", "speed", MK_FIELD_GNSS)) {
        mk_json_fixed(&j, "speed", f->speed_mm_s, 3);    /* mm/s -> m/s */
    }
    if (f->have_course &&
        cloud_field_on(c->cfg, "tx.fields_gnss", "course", MK_FIELD_GNSS)) {
        mk_json_fixed(&j, "course", f->course_1e2, 2);
    }
    if (f->have_diff_age &&
        cloud_field_on(c->cfg, "tx.fields_gnss", "diff_age", MK_FIELD_GNSS)) {
        mk_json_fixed(&j, "diff_age", f->diff_age_1e1, 1);
    }
    if (f->have_station &&
        cloud_field_on(c->cfg, "tx.fields_gnss", "station_id", MK_FIELD_GNSS)) {
        mk_json_u32(&j, "station_id", f->station_id);
    }

    /* 계약 §3 — gnss 의 valve 태깅은 필수다 (마스크와 무관). */
    mk_json_u32(&j, "valve", (uint32_t)cloud_valve_state(c));
    return mk_json_end(&j);
}

static int tick_gnss(MkCloud *c, MkCloudEmit emit, void *ctx)
{
    if (c->gnss == NULL) { return 0; }
    MkCfgItem *en = mk_cfg_find(c->cfg, "gnss.enabled");
    if (en == NULL || !en->cur.u) { return 0; }

    uint32_t count = mk_gnss_fix_count(c->gnss);
    if (count == c->gnss_sent_count) { return 0; }   /* 새 fix 없음 */

    MkGnssFix f;
    if (!mk_gnss_last_fix(c->gnss, &f)) { return 0; }

    char body[MK_CLOUD_LINE_MAX];
    int len = build_gnss(c, &f, body, sizeof body);
    if (len == 0) {
        /* 불완전한 fix — 소비만 하고 침묵 (설계 §4.4). */
        c->gnss_sent_count = count;
        return 0;
    }
    if (finish_and_emit(body, sizeof body, len, emit, ctx)) {
        c->gnss_sent_count = count;
        return 1;
    }
    return 0;
}

/* ── imu (계약 §7, v1.7.0 — 지자기 없음) ─────────────────────────────────── */

static int tick_imu(MkCloud *c, MkCloudEmit emit, void *ctx)
{
    if (c->imu == NULL) { return 0; }
    MkCfgItem *en = mk_cfg_find(c->cfg, "gnss.imu");
    if (en == NULL || !en->cur.u) { return 0; }

    MkImuSample s;
    if (!mk_imu_last(c->imu, &s)) { return 0; }
    if (s.seq == c->imu_sent_seq) { return 0; }      /* 새 표본 없음 */

    char body[MK_CLOUD_LINE_MAX];
    MkJson j;
    begin_record(c, &j, body, sizeof body, "imu", s.t_ms);
    /* 소수 3자리 — 계약 §7 예시의 자릿수. mx·my·mz 는 싣지 않는다 —
     * UM981 에 자기계가 없고 v1.7.0 이 지자기를 선택으로 완화했다
     * (sentinel 금지 — 없는 값은 생략). */
    mk_json_f32(&j, "ax", s.ax, 3);
    mk_json_f32(&j, "ay", s.ay, 3);
    mk_json_f32(&j, "az", s.az, 3);
    mk_json_f32(&j, "gx", s.gx, 3);
    mk_json_f32(&j, "gy", s.gy, 3);
    mk_json_f32(&j, "gz", s.gz, 3);
    /* IMU 내부 온도 — IMU 필드 카드(tx.fields_imu)의 비트가 게이트한다. */
    if (s.have_temp &&
        cloud_field_on(c->cfg, "tx.fields_imu", "imu_temp", MK_FIELD_IMU)) {
        mk_json_f32(&j, "degc", s.temp_c, 1);
    }
    int len = mk_json_end(&j);

    if (finish_and_emit(body, sizeof body, len, emit, ctx)) {
        c->imu_sent_seq = s.seq;
        return 1;
    }
    return 0;
}

/* ── valve 레코드 (계약 §5) ─────────────────────────────────────────────── */

static int tick_valve(MkCloud *c, int64_t now_ms, MkCloudEmit emit, void *ctx)
{
    if (c->sol == NULL) { return 0; }
    /* 확정된 입력이 하나도 없으면(부팅 직후·sol 미구성) 아직 말하지
     * 않는다 — 모르는 상태를 0 이라 단정해 내보내지 않는다. */
    int any_valid = 0;
    for (unsigned ch = 0; ch < MK_SOL_COUNT; ch++) {
        if (c->sol->confirmed_valid[ch]) { any_valid = 1; break; }
    }
    if (!any_valid) { return 0; }

    int state = cloud_valve_state(c);
    if (c->valve_primed && (int)c->valve_sent_state == state) { return 0; }

    /* 🔴 t 는 "지금"이다 — din 엣지의 정밀 획득 시각은 본선(규격 §7.6)이
     *    싣는다. 계약 §5 의 valve 는 상태 통지라 tick 시각이면 충분하고,
     *    엣지 시각을 여기서도 실으려면 solctl 의 큐를 두 소비자가 나눠
     *    먹는 설계가 필요해진다 — A단계 범위 밖. */
    char body[MK_CLOUD_LINE_MAX];
    MkJson j;
    begin_record(c, &j, body, sizeof body, "valve", now_ms);
    mk_json_u32(&j, "state", (uint32_t)state);
    int len = mk_json_end(&j);

    if (finish_and_emit(body, sizeof body, len, emit, ctx)) {
        c->valve_primed = 1u;
        c->valve_sent_state = (uint8_t)state;
        return 1;
    }
    return 0;
}

/* ── device_capability (계약 §16, 설계 §4.6) ─────────────────────────────
 *
 * "이 장비에 무엇이 있는가"를 설정에서 유도한다 — 계약 §16.6 이 이 배열을
 * 미발행 정책의 ground truth 로 쓰므로, 여기 없는 타입의 레코드는 없어야
 * 하고 그 역도 성립해야 한다. 그래서 발행 조건과 같은 설정을 읽는다. */

/* i2c 종류 → 그 포트가 낼 수 있는 클라우드 타입들.
 * 🔴 mk_cfgtable.c 의 I2C_KIND_LABELS 순서(0 없음, 1 조도, 2 온습도,
 *    3 적외 온도, 4 방수 온도)와 나란하다. I2C_CLOUD(quantity 표)와
 *    어긋나면 capability 가 거짓말을 한다 — test_cloud.c 가 둘을 대조한다. */
static const char *const I2C_KIND_TYPES[][2] = {
    { NULL,        NULL },            /* 0 없음 */
    { "light",     NULL },            /* 1 조도 (lux) */
    { "temp_air",  "humidity" },      /* 2 온습도 */
    { "temp_road", NULL },            /* 3 적외 온도 (대상) */
    { "temp_air",  NULL },            /* 4 방수 온도 — quantity "temp" 경로 */
};

static void add_sensor(const char *list[], size_t cap, size_t *n,
                       const char *type)
{
    if (type == NULL) { return; }
    for (size_t k = 0; k < *n; k++) {
        if (strcmp(list[k], type) == 0) { return; }   /* 중복 제거 */
    }
    if (*n < cap) { list[(*n)++] = type; }
}

/* 관련 설정값의 합성 지문 — 바뀌면 재발행한다. 해시가 아니라 자리 지정
 * 합성이라 충돌하려면 두 설정이 정확히 반대로 함께 움직여야 한다. */
static uint32_t cap_fingerprint(MkCloud *c)
{
    uint32_t fp = 1u;
    for (int ch = 0; ch < MK_ADS_CHANNELS; ch++) {
        fp = fp * 31u + ain_u32(c->cfg, ch, ".cloud", 0u);
    }
    for (unsigned p = 0; p < MK_I2C_COUNT; p++) {
        fp = fp * 31u + i2c_u32(c->cfg, p, ".enabled", 0u);
        fp = fp * 31u + i2c_u32(c->cfg, p, ".kind", 0u);
    }
    MkCfgItem *en = mk_cfg_find(c->cfg, "gnss.enabled");
    fp = fp * 31u + (en && en->cur.u ? 1u : 0u);
    MkCfgItem *imu = mk_cfg_find(c->cfg, "gnss.imu");
    fp = fp * 31u + (imu && imu->cur.u ? 1u : 0u);
    return fp;
}

static int tick_capability(MkCloud *c, int64_t now_ms,
                           MkCloudEmit emit, void *ctx)
{
    uint32_t fp = cap_fingerprint(c);
    if (c->cap_primed && c->cap_sent_fp == fp) { return 0; }

    const char *sensors[12];
    size_t n = 0;
    for (int ch = 0; ch < MK_ADS_CHANNELS; ch++) {
        uint32_t sel = ain_u32(c->cfg, ch, ".cloud", 0u);
        if (sel < sizeof AIN_CLOUD / sizeof *AIN_CLOUD) {
            add_sensor(sensors, 12, &n, AIN_CLOUD[sel].type);
        }
    }
    for (unsigned p = 0; p < MK_I2C_COUNT; p++) {
        if (!i2c_u32(c->cfg, p, ".enabled", 0u)) { continue; }
        uint32_t kind = i2c_u32(c->cfg, p, ".kind", 0u);
        if (kind < sizeof I2C_KIND_TYPES / sizeof *I2C_KIND_TYPES) {
            add_sensor(sensors, 12, &n, I2C_KIND_TYPES[kind][0]);
            add_sensor(sensors, 12, &n, I2C_KIND_TYPES[kind][1]);
        }
    }
    MkCfgItem *en = mk_cfg_find(c->cfg, "gnss.enabled");
    if (en && en->cur.u) { add_sensor(sensors, 12, &n, "gnss"); }
    MkCfgItem *imu = mk_cfg_find(c->cfg, "gnss.imu");
    if (imu && imu->cur.u) { add_sensor(sensors, 12, &n, "imu"); }
    /* 🔴 valve 는 넣지 않는다 — 센서가 아니라 버튼 입력이다 (계약 §16.3). */

    char body[MK_CLOUD_LINE_MAX];
    MkJson j;
    begin_record(c, &j, body, sizeof body, "device_capability", now_ms);
    mk_json_str_array(&j, "sensors", sensors, n);
    mk_json_str(&j, "fw_version", c->fw_version ? c->fw_version : "");
    int len = mk_json_end(&j);

    if (finish_and_emit(body, sizeof body, len, emit, ctx)) {
        c->cap_primed = 1u;
        c->cap_sent_fp = fp;
        return 1;
    }
    return 0;
}

/* ── 진입점 ─────────────────────────────────────────────────────────────── */

void mk_cloud_init(MkCloud *c, MkConfig *cfg, MkAds *ads,
                   const char *device_id, const char *fw_version)
{
    memset(c, 0, sizeof *c);
    c->cfg = cfg;
    c->ads = ads;
    c->device_id = device_id;
    c->fw_version = fw_version;
}

void mk_cloud_attach_i2c(MkCloud *c, MkI2c *i2c)
{
    c->i2c = i2c;
}

void mk_cloud_attach_sol(MkCloud *c, MkSolCtl *sol)
{
    c->sol = sol;
}

void mk_cloud_attach_gnss(MkCloud *c, MkGnss *gnss)
{
    c->gnss = gnss;
}

void mk_cloud_attach_imu(MkCloud *c, MkImu *imu)
{
    c->imu = imu;
}

void mk_cloud_attach_timeax(MkCloud *c, MkTimeAx *timeax)
{
    c->timeax = timeax;
}

int mk_cloud_tick(MkCloud *c, int64_t now_ms, MkCloudEmit emit, void *ctx)
{
    if (c->cfg == NULL || c->ads == NULL || emit == NULL) { return 0; }
    /* 🔴 capability 가 맨 앞이다 — Cloud 가 "이 장비에 뭐가 있나"를 먼저
     *    알아야 뒤따르는 레코드를 해석한다 (계약 §16.1). valve 는 센서
     *    레코드보다 앞 — 같은 tick 의 태깅과 어긋나지 않게. */
    return tick_capability(c, now_ms, emit, ctx)
         + tick_valve(c, now_ms, emit, ctx)
         + tick_ain(c, emit, ctx) + tick_i2c(c, emit, ctx)
         + tick_gnss(c, emit, ctx) + tick_imu(c, emit, ctx);
}
