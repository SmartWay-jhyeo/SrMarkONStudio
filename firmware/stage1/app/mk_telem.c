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
 *    보드가 싣는 것이 갈린다.
 *
 * 🔴 [개정, 2026-08-19] `kind` 를 받아 **이 비트가 이 레코드에 해당하는지도
 *    함께 본다.** `tx.fields` 를 셋으로 나누며 마스크만 갈아 끼우고 이름
 *    비교만 남겨 두면, 해당 없는 비트가 마스크에 서 있어도(예: 손상된
 *    저장값, 또는 나중에 실수로 상한 계산이 어긋난 경우) 조용히 무시되는
 *    대신 엉뚱하게 실릴 위험이 남는다 — 화면에서 켠 적 없는 필드가
 *    나오거나, 반대로 켠 필드가 안 나오는 상태다. 각 마스크의 상한이
 *    field_mask_bounds() 로 이미 kind 안에서만 계산되므로 정상 경로에서는
 *    걸릴 일이 없지만, 방어선을 이름 비교 하나에만 맡기지 않는다. */
/* 🔴 static 이 아니다 — 헤더에는 안 올리지만(내부 구현이라 다른 .c 는
 *    안 쓴다) test_telem.c 가 이 함수의 kind 판정 계약을 직접 겨눠 시험한다
 *    (extern 선언). 레코드 조립 경로만으로는 이 분기가 드러나지 않는다 —
 *    지금 build_i2c_record 등은 애초에 자기 kind 밖의 이름을 묻지 않으므로,
 *    함수를 직접 부르지 않으면 이 계약이 무엇에도 걸리지 않는 죽은 코드로
 *    보인다. */
int field_on(const MkTelem *t, uint32_t mask, const char *name,
             uint8_t kind)
{
    for (size_t i = 0; i < t->n_fields; i++) {
        if (strcmp(t->fields[i].name, name) == 0) {
            if ((t->fields[i].kinds & kind) == 0u) {
                return 0;         /* 이름은 맞지만 이 레코드에는 해당 없다 */
            }
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

/* 획득 시각(장치 카운터, ms — 지금까지 `mk_time_ms()` 가 채워 온 값)을
 * 시간축으로 변환한다. timeax 가 안 붙어 있으면(1단계 빌드) 변환 없이
 * 그대로 — 예전 동작 그대로다.
 *
 * 🔴 [판단, 2026-08-19] `mk_time_ms()`(SysTick, 1ms)와 `mk_timeax` 의
 *    `dev_us`(TIM8, 1µs)는 서로 다른 카운터다 — mk_timeax.h 상단 주석이
 *    "두 시계를 섞으면 안 된다"고 명시한다. 이 저장소에서는 둘 다 같은
 *    64MHz HCLK 에서 분주 없이 나온다(mk_gnss_io.h 의 "APB2 도 64MHz다"
 *    주석 — SysTick 도 HCLK, TIM8 도 프리스케일 63 으로 1MHz). 같은
 *    클럭이라 **상대 드리프트는 없고**, 남는 오차는 두 타이머가 부팅 후
 *    각각 켜지는 시점 차이뿐이다 — SysTick 은 `HAL_Init()` 직후, TIM8 은
 *    `main()` 뒷부분의 `mk_gnss_io_init()` 호출 시점이다. 그 사이는
 *    레지스터 설정뿐이고 블로킹 대기가 없어 수십~수백 µs 수준이다.
 *
 *    카메라 프레임 정렬(≤33ms)이 목적인 이 시간축에는 무시할 수 있는
 *    크기라 판단해 **단위만(×1000) 맞춰 그대로 넘긴다** — ADS1256·I2C·
 *    디지털 입력 각각의 획득 타임스탬프 자체를 TIM8 dev_us 로 다시 찍는
 *    것은 bsp 세 곳(mk_ads_io·mk_i2c_io·mk_sol)을 다시 여는 훨씬 큰
 *    변경이라 이번 범위 밖으로 둔다. 더 정밀한 정합이 필요해지면(예:
 *    실기기에서 두 카운터의 시작 오프셋을 재 보니 무시할 크기가 아니면)
 *    그쪽을 먼저 본다.
 *
 *    device_clock 일 때는 `mk_timeax_now_ms_monotonic()` 이 `dev_us/1000`
 *    을 그대로 돌려주므로(mk_timeax.c) 이 변환은 **정확히 원래 값과
 *    같다** — 위 오차는 gnss_pps/gnss_nmea 로 등급이 오른 뒤에만 남는다. */
static int64_t acquired_epoch_ms(MkTelem *t, int64_t acq_ms)
{
    if (t->timeax == NULL) {
        return acq_ms;
    }
    return mk_timeax_now_ms_monotonic(t->timeax, (uint64_t)acq_ms * 1000ULL);
}

/* ---- 레코드 ------------------------------------------------------------- */

static int build_record(MkTelem *t, int ch, const MkSample *s,
                        char *out, size_t cap)
{
    MkConfig *cfg = t->cfg;
    /* 🔴 [개정, 2026-08-19] ain 전용 마스크다 — tx.fields_i2c·tx.fields_din
     *    과 독립이다(규격 §7.2). */
    uint32_t mask = cfg_u32(cfg, "tx.fields_ain", 0u);
    uint32_t digits = cfg_u32(cfg, "tx.float_digits", 4u);
    const uint8_t kind = MK_FIELD_AIN;

    MkJson j;
    mk_json_begin(&j, out, cap);

    /* 규격 §7.1 — 이 넷은 마스크와 무관하게 항상 들어간다.
     *
     * 🔴 `t` 는 **획득 시각**(s->t_ms)을 시간축으로 변환한 값이다 —
     *    지금(now_ms)이 아니다. acquired_epoch_ms() 주석 참고. */
    mk_json_u32(&j, "schema_ver", 3u);
    mk_json_u32(&j, "seq", t->seq);
    mk_json_i64(&j, "t", acquired_epoch_ms(t, s->t_ms));
    mk_json_str(&j, "type", "ain");

    /* 순서는 규격 §7.2 의 표 순서 = 시뮬레이터가 담는 순서다. 두 카탈로그를
     * 나란히 놓고 볼 일이 많으므로 맞춰 둔다. */
    if (field_on(t, mask, "connector_id", kind)) {
        mk_json_u32(&j, "connector_id", (uint32_t)(ch + CONNECTOR_OFFSET));
    }
    if (field_on(t, mask, "raw", kind)) {
        /* 🔴 원본이다. ma·value 는 반올림된 파생값이므로, 정밀도가 필요한
         *    분석은 이것을 쓴다 (규격 §7.2). */
        mk_json_i64(&j, "raw", (int64_t)s->raw);
    }

    float ma = mk_telem_raw_to_ma(s->raw);
    if (field_on(t, mask, "ma", kind)) {
        mk_json_f32(&j, "ma", ma, (int)digits);
    }
    if (field_on(t, mask, "value", kind)) {
        /* 규격 §7.2.1 — value = (ma - zero) * scale */
        MkCfgItem *z = ain_item(cfg, ch, ".zero");
        MkCfgItem *sc = ain_item(cfg, ch, ".scale");
        float zero = z ? z->cur.f : 4.0f;
        float scale = sc ? sc->cur.f : 1.0f;
        mk_json_f32(&j, "value", (ma - zero) * scale, (int)digits);
    }
    if (field_on(t, mask, "unit", kind)) {
        MkCfgItem *u = ain_item(cfg, ch, ".unit");
        mk_json_str(&j, "unit", u ? u->cur.s : "");
    }
    if (field_on(t, mask, "status", kind)) {
        /* 🔴 0 = 정상. 지금은 채널 상태를 따로 세지 않는다 — 타임아웃은
         *    mk_ads_timeouts 가 들고 있고 $STAT 이 보고한다. 여기에 0 이
         *    아닌 값을 지어 넣지 않는다. */
        mk_json_u32(&j, "status", 0u);
    }
    if (field_on(t, mask, "device_id", kind)) {
        mk_json_str(&j, "device_id", t->device_id ? t->device_id : "");
    }
    /* 🔴 [판단, 2026-08-19] time_source 는 마스크로 못 끈다 — `t` 가
     *    UTC epoch_ms 인지 부팅 후 경과 ms 인지는 이 필드만이 말해 준다.
     *    끌 수 있으면 device_clock 의 `t` 를 호스트가 UTC 로 오해해
     *    저장하는 사고가 되돌아온다(mk_cfgtable.c FIELDS 주석과 같은
     *    근거). i2c 의 quantity·value, din 의 connector_id·state 와
     *    같은 자리다 — timeax 가 없으면(1단계 빌드) "device_clock" 고정,
     *    붙어 있으면 실제 등급(gnss_pps/gnss_nmea/device_clock)이다. */
    mk_json_str(&j, "time_source", time_source_of(t));
    if (field_on(t, mask, "time_quality", kind)) {
        mk_json_u32(&j, "time_quality", time_quality_of(t));
    }
    if (field_on(t, mask, "capture_counter", kind)) {
        /* 🔴 `t` 와 달리 이것은 **변환하지 않은** 원시 획득 카운터다 —
         *    `t` 가 등급에 따라 UTC 로 바뀌는 것과 별개로, capture_counter
         *    는 항상 장치 카운터(지금은 s->t_ms, 곧 mk_time_ms() 값) 그대로
         *    싣는다. 고분해능 타이머 캡처는 Phase 3(PPS)에서 들어온다 —
         *    그전까지 여기에 ×1000 같은 것을 곱해 마이크로초처럼 보이게
         *    하지 않는다. 없는 정밀도를 지어내면 호스트가 그것을 믿는다. */
        mk_json_i64(&j, "capture_counter", s->t_ms);
    }

    return mk_json_end(&j);
}

/* gnss_raw 레코드 조립(규격 §7.7). **필드 순서는 시뮬레이터
 * build_gnss_raw_record 와 같게** 둔다 (`schema_ver` `seq` `t` `type` `line`
 * `device_id` `time_source` `time_quality`). */
static int build_gnss_raw_record(MkTelem *t, const char *line, int64_t now_ms,
                                 char *out, size_t cap)
{
    /* 🔴 [판단, 2026-08-19] 전용 마스크를 새로 만들지 않고 tx.fields_ain 을
     *    빌려 쓴다 — 이 레코드가 쓰는 device_id·time_quality 비트는 ain
     *    에도 있고(규격 §7.2 표), gnss_raw 는 진단용이라 항목을 늘릴
     *    만큼 자주 쓰지 않는다(규격 §7.7). */
    uint32_t mask = cfg_u32(t->cfg, "tx.fields_ain", 0u);
    const uint8_t kind = MK_FIELD_AIN;

    MkJson j;
    mk_json_begin(&j, out, cap);
    mk_json_u32(&j, "schema_ver", 3u);
    mk_json_u32(&j, "seq", t->seq);
    /* 🔴 `t` 는 이 줄이 완성된(파서가 CR/LF 를 본) 시점을 시간축으로 바꾼
     *    값이다. mk_gnss.c 는 바이트만 먹고 시각을 모르므로, 슈퍼루프가
     *    GNSS UART 를 비운 직후 같은 바퀴에서 이 함수를 부르는 시각
     *    (now_ms)을 쓴다 — 오차는 한 바퀴(수백 us~수 ms) 이내다. ain 의
     *    DRDY 인터럽트 시각처럼 하드웨어가 확정한 정밀 시각이 아니라
     *    진단용 근사치다(규격 §7.7 — 이 레코드의 목적이 통신 여부 확인이지
     *    정밀 계측이 아니다). */
    mk_json_i64(&j, "t", acquired_epoch_ms(t, now_ms));
    mk_json_str(&j, "type", "gnss_raw");
    /* 🔴 line 은 마스크로 끌 수 없다 — i2c 의 quantity·value 와 같은 이유,
     *    빠지면 레코드가 아무 말도 안 한다(규격 §7.7). mk_json_str 이
     *    `"` `\` 와 제어문자를 이스케이프하므로 원문에 그런 바이트가
     *    섞여도 줄이 깨지지 않는다. */
    mk_json_str(&j, "line", line);
    if (field_on(t, mask, "device_id", kind)) {
        mk_json_str(&j, "device_id", t->device_id ? t->device_id : "");
    }
    /* 🔴 time_source 는 마스크로 못 끈다 — ain 의 build_record 와 같은
     *    근거. */
    mk_json_str(&j, "time_source", time_source_of(t));
    if (field_on(t, mask, "time_quality", kind)) {
        mk_json_u32(&j, "time_quality", time_quality_of(t));
    }
    return mk_json_end(&j);
}

/* i2c 레코드 조립. **필드 순서는 시뮬레이터 build_i2c_record 와 같게** 둔다
 * (`schema_ver` `seq` `t` `type` `connector_id` `quantity` `value` `status`
 *  `device_id` `time_source` `time_quality`). */
static int build_i2c_record(MkTelem *t, const MkI2cOut *o,
                            char *out, size_t cap)
{
    /* 🔴 [개정, 2026-08-19] i2c 전용 마스크 — tx.fields_ain·tx.fields_din 과
     *    독립이다(규격 §7.5). */
    uint32_t mask = cfg_u32(t->cfg, "tx.fields_i2c", 0u);
    uint32_t digits = cfg_u32(t->cfg, "tx.float_digits", 4u);
    const uint8_t kind = MK_FIELD_I2C;

    MkJson j;
    mk_json_begin(&j, out, cap);
    mk_json_u32(&j, "schema_ver", 3u);
    mk_json_u32(&j, "seq", t->seq);
    /* 🔴 `t` 는 획득 시각(o->t_ms)을 시간축으로 변환한 값이다 — ain 과
     *    같은 규칙(acquired_epoch_ms() 주석 참고). */
    mk_json_i64(&j, "t", acquired_epoch_ms(t, o->t_ms));
    mk_json_str(&j, "type", "i2c");

    if (field_on(t, mask, "connector_id", kind)) {
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
    if (field_on(t, mask, "status", kind)) {
        mk_json_u32(&j, "status", o->status);
    }
    if (field_on(t, mask, "device_id", kind)) {
        mk_json_str(&j, "device_id", t->device_id ? t->device_id : "");
    }
    /* 🔴 time_source 는 마스크로 못 끈다 — ain 의 build_record 와 같은
     *    근거(위 주석 참고). */
    mk_json_str(&j, "time_source", time_source_of(t));
    if (field_on(t, mask, "time_quality", kind)) {
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
    /* 🔴 [개정, 2026-08-19] din 전용 마스크 — tx.fields_ain·tx.fields_i2c 와
     *    독립이다(규격 §7.6). */
    uint32_t mask = cfg_u32(t->cfg, "tx.fields_din", 0u);
    const uint8_t kind = MK_FIELD_DIN;

    MkJson j;
    mk_json_begin(&j, out, cap);
    mk_json_u32(&j, "schema_ver", 3u);
    mk_json_u32(&j, "seq", t->seq);
    /* 🔴 `t` 는 엣지를 잡은 시각(o->t_ms)을 시간축으로 변환한 값이다 —
     *    ain 과 같은 규칙(acquired_epoch_ms() 주석 참고). */
    mk_json_i64(&j, "t", acquired_epoch_ms(t, o->t_ms));
    mk_json_str(&j, "type", "din");

    /* 🔴 connector_id·state 는 마스크로 끌 수 없다 (규격 §7.6) — 둘이
     *    빠지면 레코드가 아무 말도 안 한다(i2c 의 quantity·value 와
     *    같은 이유). */
    mk_json_u32(&j, "connector_id", (uint32_t)o->connector_id);
    mk_json_u32(&j, "state", o->state);

    if (field_on(t, mask, "device_id", kind)) {
        mk_json_str(&j, "device_id", t->device_id ? t->device_id : "");
    }
    /* 🔴 time_source 는 마스크로 못 끈다 — ain 의 build_record 와 같은
     *    근거(위 주석 참고). */
    mk_json_str(&j, "time_source", time_source_of(t));
    if (field_on(t, mask, "time_quality", kind)) {
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

void mk_telem_attach_gnss(MkTelem *t, MkGnss *gnss)
{
    t->gnss = gnss;
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

    /* 🔴 gnss_raw 도 din 과 같은 성격이다 — 사건(줄 완성)이 이벤트라
     *    `tx.period_ms` 를 기다리지 않는다(규격 §7.7). `gnss.echo` 가
     *    꺼져 있으면(기본값) 큐를 비우지 않는다 — mk_gnss.c 의 원시
     *    큐는 자체 용량(MK_GNSS_RAW_QUEUE_CAP)이 차면 가장 오래된 것을
     *    스스로 버리므로, 안 비워도 무한히 쌓이지 않는다. */
    int sent_gnss = 0;
    if (t->gnss != NULL && cfg_u32(t->cfg, "gnss.echo", 0u)) {
        char raw[MK_GNSS_LINE_MAX + 2];
        while (sent_gnss < MK_TELEM_MAX_LINES &&
               mk_gnss_take_raw(t->gnss, raw, sizeof raw, NULL)) {
            char body[MK_LINE_MAX + 8];
            t->seq++;
            int len = build_gnss_raw_record(t, raw, now_ms, body, sizeof body);
            if (len <= 0 || (size_t)len + 2u > sizeof body) { continue; }
            body[len] = '\n';
            body[len + 1] = '\0';
            emit(ctx, body, (size_t)len + 1u);
            sent_gnss++;
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
        return sent_din + sent_gnss;
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
     *    이어 나간다.
     *
     *    🔴 [정정, 2026-08-20] "미루는 것이지 버리는 것이 아니다" 는
     *    이 주석이 예전에 여기서 했던 말이다. 실기기에서 그 말이 거짓임이
     *    드러났다 — 채널당 10ms 로 7채널을 켜고 tx.period_ms=10 으로
     *    돌리자(2026-08-20 실측), 뒤 채널(J8·J9)이 30초 내내 단 한 줄도
     *    못 냈다: 앞 채널부터 늘 ch=0 에서 순회를 시작했고, 한 채널이 자기
     *    큐를 통째로 비울 때까지 다음 채널로 안 넘어갔다. "다음 틱으로
     *    미룬다"는 앞 채널이 매번 다시 그 몫을 먼저 가져가 버려 뒤 채널은
     *    영영 차례가 안 왔다는 뜻이었다 — 미루기가 영원하면 그것은 버리는
     *    것이다. 큐 칸수(64)가 다 찬 채 drops 만 계속 오른 것이 그 증거다.
     *
     *    고친 방식 — 라운드로빈, 한 바퀴에 채널마다 한 표본:
     *      1) `t->ain_rr` 에서부터 켜진 채널을 한 바퀴 돈다. 채널마다
     *         큐에서 **딱 하나만**(가장 오래된 것 — mk_queue_pop 은 FIFO)
     *         꺼내 낸다. 그래서 한 채널이 한 틱의 예산을 혼자 다 먹을 수
     *         없다 — 다른 채널도 같은 바퀴 안에서 자기 몫을 받는다.
     *      2) 예산이 남고 이번 바퀴에 뭔가 나갔으면 다음 바퀴를 돈다 —
     *         밀린 채널은 여러 바퀴에 걸쳐 여러 줄을 낼 수 있지만, 매
     *         바퀴 다른 켜진 채널에게도 최소 한 줄씩 기회를 준다.
     *      3) 다음 틱의 시작 채널은 이번 틱이 멈춘 자리의 다음으로 돌린다
     *         (`t->ain_rr` 갱신, 함수 끝) — 그래야 여러 틱에 걸쳐서도
     *         특정 채널이 항상 먼저(=유리하게) 서지 않는다. 꺼진 채널은
     *         순회에서 그냥 건너뛰므로 회전이 깨지지 않는다.
     *      4) 이번 바퀴 내내 큐가 비어 있던 채널만 last 값 경로를 탄다 —
     *         정상 부하(수집 < 송신)에서는 지금까지와 같은 동작이다. */
    int drained[MK_ADS_CHANNELS];
    for (int k = 0; k < MK_ADS_CHANNELS; k++) { drained[k] = 0; }
    int start = t->ain_rr;
    if (start < 0 || start >= MK_ADS_CHANNELS) { start = 0; }
    int last_ch = start;
    int progressed = 1;
    while (sent < MK_TELEM_MAX_LINES && progressed) {
        progressed = 0;
        for (int i = 0; i < MK_ADS_CHANNELS && sent < MK_TELEM_MAX_LINES; i++) {
            int ch = (start + i) % MK_ADS_CHANNELS;
            if (!mk_ads_channel_enabled(t->ads, ch)) {
                continue;
            }
            MkQueue *q = mk_ads_queue(t->ads, ch);
            MkSample s;
            if (q != NULL && mk_queue_pop(q, &s)) {
                sent += emit_ain_sample(t, ch, &s, emit, ctx);
                drained[ch] = 1;
                progressed = 1;
                last_ch = ch;
            }
        }
    }

    /* 큐가 이번 틱 내내 비어 있던 채널만 last 값을 반복한다 — has_last 가
     * 거짓이면(한 번도 표본을 못 받았으면) 아무것도 안 낸다. 0 을 지어내지
     * 않는다(설계 원칙 3·4). */
    for (int i = 0; i < MK_ADS_CHANNELS && sent < MK_TELEM_MAX_LINES; i++) {
        int ch = (start + i) % MK_ADS_CHANNELS;
        if (!mk_ads_channel_enabled(t->ads, ch) || drained[ch]) {
            continue;
        }
        MkSample s;
        if (!mk_ads_last(t->ads, ch, &s)) {
            continue;
        }
        sent += emit_ain_sample(t, ch, &s, emit, ctx);
        last_ch = ch;
    }
    t->ain_rr = (last_ch + 1) % MK_ADS_CHANNELS;

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

    return sent + sent_i2c + sent_din + sent_gnss;
}
