#include "mk_telem.h"

#include <string.h>

#include "mk_json.h"

/* 데이터시트 §5.3. 시뮬레이터(tools/simulator/telemetry.py)와 같은 값이어야
 * 한다 — 두 구현이 같은 원시 코드에 다른 mA 를 내면, 화면은 시뮬레이터에서
 * 멀쩡하다가 보드에서만 틀어진다. */
#define ADS_FULL_SCALE   8388607.0f      /* 2^23 - 1 */
#define VREF_V           2.5f
/* 🔴 만재 입력은 VREF 가 아니라 **2·VREF** 다 (PGA=1).
 *
 *      ADS1256.pdf p.11: "full-scale input range is ±2VREF (for PGA = 1)"
 *      ADS1256.pdf p.23: LSB = 2VREF/(PGA(2^23 − 1))
 *
 *    여기를 VREF 로 두면 모든 값이 정확히 절반으로 나온다. 실기기에서 4 mA
 *    신호가 1.99 mA 로 보였다 [실증 2026-08-17] — 배수가 딱 2 라 눈치채기
 *    어렵고, 센서나 배선을 먼저 의심하게 된다. */
#define ADS_FULL_SCALE_V (2.0f * VREF_V)
#define SHUNT_OHMS       120.0f

/* 커넥터 번호 = 채널 + 3 (데이터시트 §5.3). */
#define CONNECTOR_OFFSET 3

float mk_telem_raw_to_ma(int32_t raw)
{
    float volts = ((float)raw / ADS_FULL_SCALE) * ADS_FULL_SCALE_V;
    return volts / SHUNT_OHMS * 1000.0f;
}

void mk_telem_init(MkTelem *t, MkConfig *cfg, MkAds *ads,
                   const MkFieldBit *fields, size_t n_fields,
                   const char *device_id)
{
    memset(t, 0, sizeof *t);
    t->cfg = cfg;
    t->ads = ads;
    t->fields = fields;
    t->n_fields = n_fields;
    t->device_id = device_id;
}

/* ---- 설정 읽기 ---------------------------------------------------------- */

static uint32_t cfg_u32(MkConfig *cfg, const char *key, uint32_t fallback)
{
    MkCfgItem *it = mk_cfg_find(cfg, key);
    return it ? it->cur.u : fallback;
}

/* "ain0.zero" 같은 키를 만든다. libc 를 쓰지 않는다 (app/ 규칙). */
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

/* 이 필드가 마스크에서 켜져 있는가.
 *
 * 🔴 비트 번호를 여기 적지 않는다. 필드 표(mk_cfgtable 의 FIELDS)가 유일한
 *    출처이고, 그것을 카탈로그로도 보낸다 — 두 곳에 적으면 화면이 켠 것과
 *    보드가 싣는 것이 갈린다. */
static int field_on(const MkTelem *t, uint32_t mask, const char *name)
{
    for (size_t i = 0; i < t->n_fields; i++) {
        if (strcmp(t->fields[i].name, name) == 0) {
            return (mask & (1u << t->fields[i].bit)) != 0u;
        }
    }
    return 0;                    /* 모르는 필드는 싣지 않는다 */
}

/* time_source 문자열. timeax 가 안 붙어 있으면(1단계 빌드) "device_clock"
 * 고정 — 예전 동작 그대로다. */
static const char *time_source_of(const MkTelem *t)
{
    return t->timeax ? mk_timeax_grade_name(mk_timeax_grade(t->timeax))
                     : "device_clock";
}

static uint32_t time_quality_of(const MkTelem *t)
{
    return t->timeax ? mk_timeax_time_quality(t->timeax) : 0u;
}

/* ---- 레코드 ------------------------------------------------------------- */

static int build_record(MkTelem *t, int ch, const MkSample *s,
                        char *out, size_t cap)
{
    MkConfig *cfg = t->cfg;
    uint32_t mask = cfg_u32(cfg, "tx.fields", 0u);
    uint32_t digits = cfg_u32(cfg, "tx.float_digits", 4u);

    MkJson j;
    mk_json_begin(&j, out, cap);

    /* 규격 §7.1 — 이 넷은 마스크와 무관하게 항상 들어간다. */
    mk_json_u32(&j, "schema_ver", 3u);
    mk_json_u32(&j, "seq", t->seq);
    mk_json_i64(&j, "t", s->t_ms);
    mk_json_str(&j, "type", "ain");

    /* 순서는 규격 §7.2 의 표 순서 = 시뮬레이터가 담는 순서다. 두 카탈로그를
     * 나란히 놓고 볼 일이 많으므로 맞춰 둔다. */
    if (field_on(t, mask, "connector_id")) {
        mk_json_u32(&j, "connector_id", (uint32_t)(ch + CONNECTOR_OFFSET));
    }
    if (field_on(t, mask, "raw")) {
        /* 🔴 원본이다. ma·value 는 반올림된 파생값이므로, 정밀도가 필요한
         *    분석은 이것을 쓴다 (규격 §7.2). */
        mk_json_i64(&j, "raw", (int64_t)s->raw);
    }

    float ma = mk_telem_raw_to_ma(s->raw);
    if (field_on(t, mask, "ma")) {
        mk_json_f32(&j, "ma", ma, (int)digits);
    }
    if (field_on(t, mask, "value")) {
        /* 규격 §7.2.1 — value = (ma - zero) * scale */
        MkCfgItem *z = ain_item(cfg, ch, ".zero");
        MkCfgItem *sc = ain_item(cfg, ch, ".scale");
        float zero = z ? z->cur.f : 4.0f;
        float scale = sc ? sc->cur.f : 1.0f;
        mk_json_f32(&j, "value", (ma - zero) * scale, (int)digits);
    }
    if (field_on(t, mask, "unit")) {
        MkCfgItem *u = ain_item(cfg, ch, ".unit");
        mk_json_str(&j, "unit", u ? u->cur.s : "");
    }
    if (field_on(t, mask, "status")) {
        /* 🔴 0 = 정상. 지금은 채널 상태를 따로 세지 않는다 — 타임아웃은
         *    mk_ads_timeouts 가 들고 있고 $STAT 이 보고한다. 여기에 0 이
         *    아닌 값을 지어 넣지 않는다. */
        mk_json_u32(&j, "status", 0u);
    }
    if (field_on(t, mask, "device_id")) {
        mk_json_str(&j, "device_id", t->device_id ? t->device_id : "");
    }
    if (field_on(t, mask, "time_source")) {
        /* 🔴 Phase 3 — timeax 가 없으면(1단계 빌드) "device_clock" 고정,
         *    붙어 있으면 실제 등급(gnss_pps/gnss_nmea/device_clock)이다.
         *    device_clock 일 때 `t` 는 부팅 후 경과 ms 이지 UTC 가
         *    아니다 — 호스트가 그 상태에서 이것을 시각으로 저장하면 안 된다. */
        mk_json_str(&j, "time_source", time_source_of(t));
    }
    if (field_on(t, mask, "time_quality")) {
        mk_json_u32(&j, "time_quality", time_quality_of(t));
    }
    if (field_on(t, mask, "capture_counter")) {
        /* 🔴 지금은 획득 시각이 곧 이것이다. 고분해능 타이머 캡처는 Phase 3
         *    (PPS)에서 들어온다 — 그전까지 여기에 ×1000 같은 것을 곱해
         *    마이크로초처럼 보이게 하지 않는다. 없는 정밀도를 지어내면
         *    호스트가 그것을 믿는다. */
        mk_json_i64(&j, "capture_counter", s->t_ms);
    }

    return mk_json_end(&j);
}

/* i2c 레코드 조립. **필드 순서는 시뮬레이터 build_i2c_record 와 같게** 둔다
 * (`schema_ver` `seq` `t` `type` `connector_id` `quantity` `value` `status`
 *  `device_id` `time_source` `time_quality`). */
static int build_i2c_record(MkTelem *t, const MkI2cOut *o,
                            char *out, size_t cap)
{
    uint32_t mask = cfg_u32(t->cfg, "tx.fields", 0u);
    uint32_t digits = cfg_u32(t->cfg, "tx.float_digits", 4u);

    MkJson j;
    mk_json_begin(&j, out, cap);
    mk_json_u32(&j, "schema_ver", 3u);
    mk_json_u32(&j, "seq", t->seq);
    mk_json_i64(&j, "t", o->t_ms);
    mk_json_str(&j, "type", "i2c");

    if (field_on(t, mask, "connector_id")) {
        mk_json_u32(&j, "connector_id", (uint32_t)o->connector_id);
    }
    /* 🔴 quantity·value 는 마스크로 끌 수 없다 (규격 §7.5). 둘이 빠지면
     *    레코드가 아무 말도 안 한다. */
    mk_json_str(&j, "quantity", o->quantity ? o->quantity : "");
    if (o->have_value) {
        mk_json_f32(&j, "value", o->value, (int)digits);
    } else {
        mk_json_null(&j, "value");
    }
    /* 🔴 unit 을 싣지 않는다 — quantity 가 단위를 이미 정한다 (규격 §7.5). */
    if (field_on(t, mask, "status")) {
        mk_json_u32(&j, "status", o->status);
    }
    if (field_on(t, mask, "device_id")) {
        mk_json_str(&j, "device_id", t->device_id ? t->device_id : "");
    }
    if (field_on(t, mask, "time_source")) {
        mk_json_str(&j, "time_source", time_source_of(t));
    }
    if (field_on(t, mask, "time_quality")) {
        mk_json_u32(&j, "time_quality", time_quality_of(t));
    }
    return mk_json_end(&j);
}

/* din 레코드 조립 (규격 §7.6). **필드 순서는 시뮬레이터 build_din_record 와
 * 같게** 둔다 (`schema_ver` `seq` `t` `type` `connector_id` `state`
 * `device_id` `time_source` `time_quality`). */
static int build_din_record(MkTelem *t, const MkSolOut *o,
                            char *out, size_t cap)
{
    uint32_t mask = cfg_u32(t->cfg, "tx.fields", 0u);

    MkJson j;
    mk_json_begin(&j, out, cap);
    mk_json_u32(&j, "schema_ver", 3u);
    mk_json_u32(&j, "seq", t->seq);
    mk_json_i64(&j, "t", o->t_ms);
    mk_json_str(&j, "type", "din");

    /* 🔴 connector_id·state 는 마스크로 끌 수 없다 (규격 §7.6) — 둘이
     *    빠지면 레코드가 아무 말도 안 한다(i2c 의 quantity·value 와
     *    같은 이유). */
    mk_json_u32(&j, "connector_id", (uint32_t)o->connector_id);
    mk_json_u32(&j, "state", o->state);

    if (field_on(t, mask, "device_id")) {
        mk_json_str(&j, "device_id", t->device_id ? t->device_id : "");
    }
    if (field_on(t, mask, "time_source")) {
        mk_json_str(&j, "time_source", time_source_of(t));
    }
    if (field_on(t, mask, "time_quality")) {
        mk_json_u32(&j, "time_quality", time_quality_of(t));
    }
    return mk_json_end(&j);
}

/* 표본 하나를 ain 레코드로 만들어 내보낸다. 큐에서 막 꺼낸 것이든
 * mk_ads_last() 로 얻은 마지막 값이든 이 한 곳으로 모은다 — 두 경로가
 * 레코드 조립·자르기 처리를 따로 하면 어느 한쪽만 고치는 실수가 난다.
 * 성공하면 1(sent 에 더할 값), 잘려서 버렸으면 0. */
static int emit_ain_sample(MkTelem *t, int ch, const MkSample *s,
                           MkTelemEmit emit, void *ctx)
{
    char body[MK_LINE_MAX + 8];
    t->seq++;
    int len = build_record(t, ch, s, body, sizeof body);
    /* 🔴 잘린 JSON 을 내보내지 않는다. 반쪽짜리 줄은 호스트에서
     *    파싱 오류가 되고, 그 오류는 링크 문제로 오인된다. */
    if (len <= 0 || (size_t)len + 2u > sizeof body) {
        return 0;
    }
    body[len] = '\n';
    body[len + 1] = '\0';
    emit(ctx, body, (size_t)len + 1u);
    return 1;
}

/* ---- 주기 처리 ---------------------------------------------------------- */

void mk_telem_attach_i2c(MkTelem *t, MkI2c *i2c)
{
    t->i2c = i2c;
}

void mk_telem_attach_sol(MkTelem *t, MkSolCtl *sol)
{
    t->sol = sol;
}

void mk_telem_attach_timeax(MkTelem *t, MkTimeAx *timeax)
{
    t->timeax = timeax;
}

int mk_telem_tick(MkTelem *t, int64_t now_ms, MkTelemEmit emit, void *ctx)
{
    if (t->cfg == NULL || t->ads == NULL || emit == NULL) {
        return 0;
    }

    /* 🔴 din 은 이 함수의 다른 무엇과도 다르다 — 엣지 그 자체가 이벤트라
     *    `tx.period_ms` 를 기다리지 않는다(규격 §7.6). 수집·송신을 떼어
     *    놓는 이번 변경(사용자 설계 2026-08-19)은 **ain·i2c** 에만 해당한다
     *    — 상태 변화의 순간이 핵심인 din 을 마지막 값 반복 방식에 섞으면
     *    "언제 들어왔나"가 다음 주기까지 묻힌다. 그대로 둔다.
     *
     *    out 이 채널마다 한 바퀴에 최대 하나(MK_SOL_COUNT=3)라
     *    MK_TELEM_MAX_LINES(16)를 절대 넘지 않는다. */
    int sent_din = 0;
    if (t->sol != NULL) {
        MkSolOut o;
        while (sent_din < MK_TELEM_MAX_LINES && mk_solctl_take(t->sol, &o)) {
            char body[MK_LINE_MAX + 8];
            t->seq++;
            int len = build_din_record(t, &o, body, sizeof body);
            if (len <= 0 || (size_t)len + 2u > sizeof body) { continue; }
            body[len] = '\n';
            body[len + 1] = '\0';
            emit(ctx, body, (size_t)len + 1u);
            sent_din++;
        }
    }

    /* 🔴 수집과 송신을 떼어 놓는다 (사용자 설계, 2026-08-19):
     *
     *      "수집은 수집대로 하고 송신은 내가 원하는 간격에 맞춰서 하는거지.
     *       변수 a 라는 곳에 수집된 값을 계속 넣고, 송신 할 때는 변수 a 를
     *       사용하면 어쨋든 그 안에 있던 값이 날라갈거 아니야"
     *
     *    그래서 여기서는 `tx.period_ms` 를 **기다린다.** 주기 전에는 아무
     *    것도 하지 않는다 — 안 그러면 이 값이 뜻을 잃는다. */
    uint32_t period = cfg_u32(t->cfg, "tx.period_ms", 100u);
    if (period == 0u) {
        period = 1u;
    }
    if (now_ms - t->last_ms < (int64_t)period) {
        return sent_din;
    }
    t->last_ms = now_ms;

    int sent = 0;

    /* 🔴 [정정, 2026-08-19] 큐는 "수집 우선, 송신 지연 흡수" 장치다 — 사용자가
     *    명시적으로 정정했다: "큐를 사용하라는 목적은 타임스탬프를 찍어서
     *    큐에 넣고 보낼 때는 좀 늦게 보내도 괜찮으니까 센서 수집을 우선으로
     *    하라는 거였어." 표본을 버리는 자리가 아니다.
     *
     *    그래서 여기는 근거가 **둘**이고, 역할이 다르다 — 하나로 줄이지
     *    않는다(mk_ads1256.h 의 MkAdsChannel.last 주석과 같은 결론):
     *
     *      1) 큐(mk_ads_queue) — 진짜 표본들. 있으면 **전부** 나간다.
     *         각자 자기 획득 시각(DRDY 순간)을 달고 한 줄씩이다 — 늦게
     *         나가도 시각은 정확해야 한다는 것이 큐가 존재하는 이유다.
     *      2) 마지막 값(mk_ads_last) — 큐가 **비어 있을 때만** 쓰는
     *         대역이다. 수집이 송신보다 느린 흔한 경우(기본값 60 SPS
     *         수집 대 tx.period_ms=100 ms 송신)에 주기를 채우는 유일한
     *         근거다. 이때도 `t` 는 그 값의 획득 시각 그대로 반복된다 —
     *         송신 시각으로 덮어쓰지 않는다.
     *
     *    이 판 이전엔 큐를 아예 안 비웠다 — 실기기에서 depth=32/drops=218
     *    로 계속 넘쳤다(마지막 값만 읽고 큐는 방치). 그다음엔 반대로
     *    갔다 — 큐를 비우되 최신 하나만 쓰고 나머지를 버렸는데, 그러면
     *    "수집 우선" 이라는 원래 취지와 정반대가 된다. 지금은 큐를
     *    비우되 **꺼낸 것 전부**를 내보낸다 — 그래야 정상 상태(수집 <
     *    송신)에서 큐가 항상 비어 `drops` 가 0 을 유지하다가, 슈퍼루프가
     *    한 틱 안에 못 따라잡을 때만(큐 자체가 찰 만큼 밀릴 때) 오르는
     *    진짜 신호가 된다.
     *
     *    한 채널이 한 틱에 낼 수 있는 줄 수는 MK_TELEM_MAX_LINES 가
     *    막는다(전체 ain 합산). 상한에 걸려 못 낸 나머지는 큐에 그대로
     *    남는다 — pop 을 안 부르면 표본이 지워지지 않으므로 다음 틱에
     *    이어 나간다. 버리는 것이 아니라 미루는 것이다. */
    for (int ch = 0; ch < MK_ADS_CHANNELS && sent < MK_TELEM_MAX_LINES; ch++) {
        if (!mk_ads_channel_enabled(t->ads, ch)) {
            continue;
        }

        MkQueue *q = mk_ads_queue(t->ads, ch);
        MkSample s;
        int drained = 0;
        while (sent < MK_TELEM_MAX_LINES && q != NULL && mk_queue_pop(q, &s)) {
            sent += emit_ain_sample(t, ch, &s, emit, ctx);
            drained = 1;
        }
        if (drained) {
            continue;               /* 큐에 있던 것은 이미 다 냈다 */
        }

        /* 큐가 비어 있었다 — has_last 가 거짓이면(한 번도 표본을 못
         * 받았으면) 아무것도 안 낸다. 0 을 지어내지 않는다(설계 원칙
         * 3·4). */
        if (!mk_ads_last(t->ads, ch, &s)) {
            continue;
        }
        sent += emit_ain_sample(t, ch, &s, emit, ctx);
    }

    /* i2c 는 포트·슬롯마다 마지막 값(last[][])만 있고 ain 같은 표본 큐가
     * 없다(2026-08-19 걷어냄, 커밋 ab11ee0). ain 의 "큐 우선, last 는
     * 큐가 빌 때만" 원칙과 어긋나 보이지만, i2c 구조 자체가 큐가 필요
     * 없게 돼 있다 — mk_i2c_tick() 은 한 바퀴에 포트를 **하나만** 나아가게
     * 하고, 그 포트가 낸 값은 즉시 last[][] 의 제 슬롯에 store_last() 로
     * 쌓인다. 같은 포트·슬롯이 다음 값을 만들려면 그 포트의 상태기계가
     * WARMUP·eff_period 를 다시 거쳐야 하므로(BH1750·AM2320·MLX90614 모두
     * 180ms~2s), 같은 슬롯에 "아직 안 내보낸 표본 두 개"가 동시에 쌓일
     * 여지가 사실상 없다 — last 하나가 항상 "큐에 표본이 정확히 0개 또는
     * 1개"인 상태와 같다. 그래서 last[][] 를 그대로 읽는 지금 방식이
     * ain 의 "큐 우선" 원칙의 퇴화한(값이 하나뿐인) 형태다. 아직 한 번도
     * 값을 못 받은 슬롯(last_valid 거짓)은 안 낸다. */
    int sent_i2c = 0;
    if (t->i2c != NULL) {
        for (unsigned p = 0; p < MK_I2C_COUNT && sent_i2c < MK_TELEM_MAX_LINES; p++) {
            for (unsigned k = 0; k < MK_I2C_VALUES_MAX && sent_i2c < MK_TELEM_MAX_LINES; k++) {
                MkI2cOut o;
                if (!mk_i2c_last(t->i2c, p, k, &o)) {
                    continue;
                }
                char body[MK_LINE_MAX + 8];
                t->seq++;
                int len = build_i2c_record(t, &o, body, sizeof body);
                if (len <= 0 || (size_t)len + 2u > sizeof body) { continue; }
                body[len] = '\n';
                body[len + 1] = '\0';
                emit(ctx, body, (size_t)len + 1u);
                sent_i2c++;
            }
        }
    }

    return sent + sent_i2c + sent_din;
}
