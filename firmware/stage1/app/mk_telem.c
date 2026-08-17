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
        /* 🔴 1단계에는 GNSS 도 PPS 도 없다. `t` 는 부팅 후 경과 ms 이고
         *    UTC 가 아니다 — 호스트가 이것을 시각으로 저장하면 안 된다. */
        mk_json_str(&j, "time_source", "device_clock");
    }
    if (field_on(t, mask, "time_quality")) {
        mk_json_u32(&j, "time_quality", 0u);
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

/* ---- 주기 처리 ---------------------------------------------------------- */

int mk_telem_tick(MkTelem *t, int64_t now_ms, MkTelemEmit emit, void *ctx)
{
    if (t->cfg == NULL || t->ads == NULL || emit == NULL) {
        return 0;
    }

    uint32_t period = cfg_u32(t->cfg, "tx.period_ms", 100u);
    if (period == 0u) {
        period = 1u;
    }
    if (now_ms - t->last_ms < (int64_t)period) {
        return 0;
    }
    t->last_ms = now_ms;

    int sent = 0;
    int progress = 1;

    /* 🔴 한 바퀴에 **채널당 하나씩** 꺼낸다.
     *
     *    처음에는 한 채널의 큐를 다 비우고 다음으로 갔다. 그러면 앞 두
     *    채널이 상한을 다 먹고 세 번째가 굶는다 — 시험이 그것을 잡았다
     *    (`test_channels_take_turns`). 큐가 채널마다 따로인 이유가 격리인데
     *    송신이 그 격리를 깨면 안 된다.
     *
     *    출발점도 매번 옮긴다. 그래야 상한에 걸려 잘릴 때 잘리는 쪽이
     *    돌아가며 바뀐다. */
    while (progress && sent < MK_TELEM_MAX_LINES) {
        progress = 0;
        for (int n = 0; n < MK_ADS_CHANNELS && sent < MK_TELEM_MAX_LINES; n++) {
            int ch = (t->next_ch + n) % MK_ADS_CHANNELS;
            if (!mk_ads_channel_enabled(t->ads, ch)) {
                continue;
            }
            MkSample s;
            if (!mk_queue_pop(mk_ads_queue(t->ads, ch), &s)) {
                continue;
            }
            progress = 1;

            char body[MK_LINE_MAX + 8];
            t->seq++;
            int len = build_record(t, ch, &s, body, sizeof body);
            /* 🔴 잘린 JSON 을 내보내지 않는다. 반쪽짜리 줄은 호스트에서
             *    파싱 오류가 되고, 그 오류는 링크 문제로 오인된다. */
            if (len <= 0 || (size_t)len + 2u > sizeof body) {
                continue;
            }
            body[len] = '\n';
            body[len + 1] = '\0';
            emit(ctx, body, (size_t)len + 1u);
            sent++;
        }
    }
    /* 다음에는 그다음 채널부터 본다. */
    t->next_ch = (t->next_ch + 1) % MK_ADS_CHANNELS;
    return sent;
}
