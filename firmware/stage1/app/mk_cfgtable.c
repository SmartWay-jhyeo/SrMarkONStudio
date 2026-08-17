#include "mk_cfgtable.h"

#include <string.h>

#include "mk_ws2812.h"      /* MK_LED_COUNT — 정의는 저쪽이 들고 있다 */

/* 이 보드가 가진 것들 (데이터시트 §5).
 *   J18~J20  디지털 출력 3
 *   J21~J24  WS2812 체인 — 개수·밝기 + 램프마다 R·G·B
 *   J10~J15  I2C 6포트 — 버스·사용·종류·주소·주기
 *
 * 🔴 MK_LED_COUNT 를 여기 다시 적지 않는다. 송신 버퍼 크기가 같은 수에
 *    묶여 있어, 두 곳에 적으면 카탈로그와 실제로 켜지는 램프 수가 갈린다. */
#define MK_SOL_COUNT   3
#define MK_I2C_COUNT   6

/* I2C 포트에 꽂을 수 있는 센서 **종류**.
 *
 * 🔴 칩 모델이 아니라 종류다 (사용자 확정 2026-08-17). 조도계가 무엇이든
 *    호스트가 할 일은 같고, 펌웨어만 드라이버를 고르면 된다. 모델로 두면
 *    새 칩을 살 때마다 카탈로그를 고쳐야 한다.
 *
 * 🔴 값이 둘인 종류는 온습도뿐이다. 나머지는 전부 하나다.
 *    시뮬레이터(tools/simulator/config_store.py)의 I2C_KINDS 와 같아야
 *    하고, crosscheck_cfgtable.py 가 그것을 대조한다. */
static const uint32_t I2C_KIND_CHOICES[] = { 0u, 1u, 2u, 3u, 4u };
static const char *const I2C_KIND_LABELS[] = {
    "없음", "조도", "온습도", "적외 온도", "방수 온도"
};

/* dev 1 + tx 3 + pwr 4 + adc 2 + ain 5×7 + sol 3 + led 3+3×4 + i2c 5×6 */
#define ITEM_COUNT   (1 + 3 + 4 + 2 + MK_AIN_COUNT * 5 \
                      + MK_SOL_COUNT \
                      + 3 + MK_LED_COUNT * 3 \
                      + MK_I2C_COUNT * 5)

/* 이름을 만들어 써야 하는 항목 수 (ain·led·i2c). sol 은 고정 문자열이다. */
#define GEN_COUNT    (MK_AIN_COUNT * 5 + MK_LED_COUNT * 3 + MK_I2C_COUNT * 5)

static MkCfgItem s_items[ITEM_COUNT];

/* 🔴 저장 덩어리가 Flash 의 staging 버퍼에 들어가는지 **컴파일 때** 본다.
 *
 *    처음에 staging 을 512 로 어림잡았는데 실제 덩어리는 1,080 바이트였다.
 *    실기기에서 $CFG,SAVE 가 ERR,BUSY 로 떨어지고 나서야 알았다. 항목을
 *    늘릴 때마다 같은 실수를 할 수 있으므로 여기서 막는다. */
_Static_assert(ITEM_COUNT <= MK_CFG_MAX_ITEMS,
               "설정 항목이 MK_CFG_MAX_ITEMS 를 넘었다");
_Static_assert(sizeof(MkValue) * ITEM_COUNT + 32u <= MK_CFG_BLOB_MAX,
               "저장 덩어리가 mk_flash.c 의 staging 버퍼를 넘는다 "
               "— 둘을 함께 키워야 한다");
_Static_assert(GEN_COUNT <= ITEM_COUNT,
               "이름 자리가 항목 수보다 많다 — 세는 식이 어긋났다");

/* ADS1256 이 지원하는 DRATE (데이터시트). */
static const uint32_t DRATE_CHOICES[] = {
    2, 5, 10, 15, 25, 30, 50, 60, 100, 500, 1000, 2000, 3750, 7500
};
static const uint32_t PGA_CHOICES[] = { 1, 2, 4, 8, 16, 32, 64 };

/* 만들어 쓰는 키·라벨을 담아 둘 자리. 포인터로 참조되므로 살아 있어야 한다. */
static char s_keys[GEN_COUNT][MK_CFG_KEY_MAX + 1];
static char s_labels[GEN_COUNT][24];
static size_t s_gen;              /* 다음에 쓸 자리 */

static const MkFieldBit FIELDS[] = {
    { 0, "device_id",       0, "보드 식별자" },
    { 1, "time_source",     1, "시간 소스" },
    { 2, "time_quality",    0, "시간 품질" },
    { 3, "raw",             1, "ADS1256 원시 카운트" },
    { 4, "ma",              1, "전류 (mA)" },
    { 5, "value",           1, "물리량 환산" },
    { 6, "unit",            0, "단위" },
    { 7, "status",          1, "채널 상태" },
    { 8, "capture_counter", 0, "획득 카운터" },
    { 9, "connector_id",    1, "커넥터 번호" },
};

const MkFieldBit *mk_cfgtable_fields(size_t *count)
{
    *count = sizeof FIELDS / sizeof *FIELDS;
    return FIELDS;
}

/* 문자열을 고정 버퍼에 담는다. */
static void put(char *dst, size_t cap, const char *src)
{
    size_t n = strlen(src);
    if (n + 1u > cap) {
        n = cap - 1u;
    }
    memcpy(dst, src, n);
    dst[n] = '\0';
}

/* 문자열을 이어 붙인다. 남은 자리가 없으면 그대로 둔다. */
static size_t cat(char *dst, size_t cap, size_t n, const char *src)
{
    for (; *src && n + 1u < cap; src++) dst[n++] = *src;
    return n;
}

/* 열 자리까지의 정수를 이어 붙인다. libc 의 sprintf 를 쓰지 않는다.
 *
 * 🔴 커넥터 번호가 두 자리(J10~J24)로 늘면서 필요해졌다. 예전에는
 *    `'0' + ch` 로 한 자리만 찍었는데, 그대로 두면 J10 이 ':' 이 된다. */
static size_t cat_uint(char *dst, size_t cap, size_t n, unsigned v)
{
    char tmp[11];
    size_t t = 0;
    do { tmp[t++] = (char)('0' + (v % 10u)); v /= 10u; } while (v && t < sizeof tmp);
    while (t-- > 0 && n + 1u < cap) dst[n++] = tmp[t];
    return n;
}

/* "ain0.zero" · "i2c10.addr" 같은 키를 만든다. */
static void make_key(char *dst, size_t cap, const char *prefix,
                     unsigned num, const char *suffix)
{
    size_t n = cat(dst, cap, 0, prefix);
    n = cat_uint(dst, cap, n, num);
    n = cat(dst, cap, n, suffix);
    dst[n] = '\0';
}

/* "J3 영점" 같은 라벨. 커넥터 번호를 그대로 받는다. */
static void make_label(char *dst, size_t cap, unsigned connector,
                       const char *what)
{
    size_t n = cat(dst, cap, 0, "J");
    n = cat_uint(dst, cap, n, connector);
    n = cat(dst, cap, n, " ");
    n = cat(dst, cap, n, what);
    dst[n] = '\0';
}

/* 다음 자리를 하나 잡아 키·라벨을 채우고 그 자리 번호를 돌려준다. */
static size_t gen(const char *prefix, unsigned num, const char *suffix,
                  unsigned connector, const char *what)
{
    size_t k = s_gen++;
    make_key(s_keys[k], sizeof s_keys[k], prefix, num, suffix);
    make_label(s_labels[k], sizeof s_labels[k], connector, what);
    return k;
}

/* 장치. */
static size_t add_dev(size_t i)
{
    s_items[i] = (MkCfgItem){ .key = "dev.id", .group = "dev",
                              .vtype = MK_VT_STR, .max = 15, .has_max = 1,
                              .label = "장치 ID" };
    put(s_items[i].def.s, sizeof s_items[i].def.s, "1");
    i++;
    return i;
}

/* 전송. */
static size_t add_tx(size_t i)
{
    s_items[i] = (MkCfgItem){ .key = "tx.fields", .group = "tx",
                              .vtype = MK_VT_U32, .min = 0,
                              .has_min = 1, .has_max = 1,
                              .label = "NDJSON 필드 마스크" };
    /* 기본 마스크 = FIELDS 의 def 가 1 인 비트들.
     * 최대 = FIELDS 에 있는 **모든** 비트.
     *
     * 🔴 둘 다 손으로 적지 않는다. 최대를 1023 으로 박아 두었더니 비트를
     *    하나 더 붙이는 순간 새 비트를 켤 수 없게 되는데, 그 사실이
     *    코드 어디에도 드러나지 않는다. 표에서 끌어내면 못 어긋난다. */
    {
        uint32_t all = 0u;
        for (size_t f = 0; f < sizeof FIELDS / sizeof *FIELDS; f++) {
            all |= (1u << FIELDS[f].bit);
            if (FIELDS[f].def) {
                s_items[i].def.u |= (1u << FIELDS[f].bit);
            }
        }
        s_items[i].max = (float)all;
    }
    i++;

    s_items[i] = (MkCfgItem){ .key = "tx.period_ms", .group = "tx",
                              .vtype = MK_VT_U16, .min = 10, .max = 10000,
                              .has_min = 1, .has_max = 1,
                              .unit = "ms", .label = "전송 주기" };
    s_items[i].def.u = 100;
    i++;

    /* 🔴 최소가 2 다. 0 을 허용하면 4~20 mA 값이 정수로 잘려 분해능이
     *    1 mA 가 된다 — 24비트 ADC 를 쓰는 이유가 사라진다. 대역폭을
     *    아끼려면 자릿수보다 필드 마스크나 주기를 줄이는 편이 낫다. */
    s_items[i] = (MkCfgItem){ .key = "tx.float_digits", .group = "tx",
                              .vtype = MK_VT_U8, .min = 2, .max = 6,
                              .has_min = 1, .has_max = 1,
                              .label = "실수 자릿수" };
    s_items[i].def.u = 4;
    i++;
    return i;
}

/* 전원 레일. */
static size_t add_pwr(size_t i)
{
    /* 🔴 전원 레일. 라벨에 핀 번호를 쓰지 않는다 (설계 원칙 1). */
    s_items[i] = (MkCfgItem){ .key = "pwr.24v", .group = "pwr",
                              .vtype = MK_VT_BOOL, .out = 1,
                              .label = "24V 전원" };
    i++;
    s_items[i] = (MkCfgItem){ .key = "pwr.14v9", .group = "pwr",
                              .vtype = MK_VT_BOOL, .out = 1,
                              .label = "14.9V 전원" };
    i++;
    /* 🔴 5V 는 끌 수 있다 (사용자 확정 2026-08-14). 다만 무엇이 함께
     *    멈추는지는 알려 준다 — 막지는 않되 모르고 끄는 일은 없게 한다.
     *
     *    note 가 화면에 그대로 뜬다(규격 §7.3). 이것이 인터록을 푼 대신
     *    남겨 둔 안전장치다. */
    s_items[i] = (MkCfgItem){ .key = "pwr.5v", .group = "pwr",
                              .vtype = MK_VT_BOOL, .out = 1,
                              .label = "5V 전원",
                              .note = "끄면 쿨링 팬·아날로그 수집·WS2812 가 함께 멈춘다" };
    s_items[i].def.u = 1;
    i++;
    s_items[i] = (MkCfgItem){ .key = "pwr.seq_delay_ms", .group = "pwr",
                              .vtype = MK_VT_U16, .min = 0, .max = 5000,
                              .has_min = 1, .has_max = 1,
                              .unit = "ms", .label = "레일 기동 간격" };
    s_items[i].def.u = 500;
    i++;
    return i;
}

/* ADS1256. */
static size_t add_adc(size_t i)
{
    s_items[i] = (MkCfgItem){ .key = "adc.pga", .group = "adc",
                              .vtype = MK_VT_ENUM,
                              .choices = PGA_CHOICES, .n_choices = 7,
                              .label = "PGA" };
    s_items[i].def.u = 1;
    i++;
    s_items[i] = (MkCfgItem){ .key = "adc.drate", .group = "adc",
                              .vtype = MK_VT_ENUM, .unit = "SPS",
                              .choices = DRATE_CHOICES, .n_choices = 14,
                              .label = "데이터율" };
    s_items[i].def.u = 60;
    i++;
    return i;
}

/* 아날로그 입력 J3~J9. */
static size_t add_ain(size_t i)
{
    for (int ch = 0; ch < MK_AIN_COUNT; ch++) {
        /* 커넥터 번호는 채널 + 3 (데이터시트 §5.3). */
        unsigned jack = (unsigned)ch + 3u;
        size_t k = gen("ain", (unsigned)ch, ".enabled", jack, "사용");
        /* 🔴 기본으로 켜지는 것은 J3 하나뿐이다.
         *
         *    처음에는 일곱 개를 전부 켜 두었는데, 그 조합이 보드 자신의
         *    용량 검사에 걸린다 — 100 ms × 7채널 = 70 SPS 인데 DRATE 60 에
         *    정착시간을 반영한 가용은 45.3 SPS 다. $CFG,RESET 을 누르면
         *    보드가 스스로 거부하는 설정으로 돌아가는 셈이었다.
         *
         *    설계 원칙 3 과도 맞다 — 센서 미연결은 정상 상태이므로, 빈
         *    보드가 일곱 개의 끊긴 루프를 빨갛게 띄우게 두지 않는다.
         *    쓸 채널은 사용자가 GUI 에서 켠다. */
        s_items[i] = (MkCfgItem){ .key = s_keys[k], .group = "ain",
                                  .vtype = MK_VT_BOOL, .label = s_labels[k] };
        s_items[i].def.u = (ch == 0) ? 1u : 0u;
        i++;

        k = gen("ain", (unsigned)ch, ".period_ms", jack, "수집 주기");
        /* 🔴 최대가 60초다. tx.period_ms(전송 주기)의 10초를 따라 적었던
         *    것인데 둘은 다른 것이다. 느린 센서를 1분에 한 번만 읽는 것은
         *    정상이고, 오히려 링크 부하를 줄인다. */
        s_items[i] = (MkCfgItem){ .key = s_keys[k], .group = "ain",
                                  .vtype = MK_VT_U16, .min = 10, .max = 60000,
                                  .has_min = 1, .has_max = 1, .unit = "ms",
                                  .label = s_labels[k] };
        s_items[i].def.u = 100;
        i++;

        k = gen("ain", (unsigned)ch, ".zero", jack, "영점");
        s_items[i] = (MkCfgItem){ .key = s_keys[k], .group = "ain",
                                  .vtype = MK_VT_F32, .min = 0.0f, .max = 25.0f,
                                  .has_min = 1, .has_max = 1, .unit = "mA",
                                  .label = s_labels[k] };
        s_items[i].def.f = 4.0f;
        i++;

        k = gen("ain", (unsigned)ch, ".scale", jack, "스케일");
        s_items[i] = (MkCfgItem){ .key = s_keys[k], .group = "ain",
                                  .vtype = MK_VT_F32, .label = s_labels[k] };
        s_items[i].def.f = 1.0f;
        i++;

        k = gen("ain", (unsigned)ch, ".unit", jack, "단위");
        s_items[i] = (MkCfgItem){ .key = s_keys[k], .group = "ain",
                                  .vtype = MK_VT_STR, .max = 7, .has_max = 1,
                                  .label = s_labels[k] };
        i++;
    }
    return i;
}

/* 디지털 출력 J18~J20. */
static size_t add_sol(size_t i)
{
    /* ── 디지털 출력 J18~J20 (데이터시트 §5.7) ───────────────────────
     *
     * 🔴 MCU GPIO 가 커넥터에 직결이다 — 버퍼도 직렬저항도 클램프도 없다.
     *    핀당 20 mA 가 상한이라 밸브를 직접 못 구동하고 외부 옵토커플러/
     *    드라이버 모듈이 반드시 붙는다. 그 사실을 note 로 실어 화면이
     *    조작 옆에 띄우게 한다.
     *
     * 🔴 보드는 3채널이지만 하우징 설계는 2채널로 확정됐다. 남는 한 채널을
     *    감추지 않는다 — 보드에 있는 것은 보드가 말한다. */
    static const char *const SOL_KEYS[MK_SOL_COUNT] = {
        "sol.j18", "sol.j19", "sol.j20"
    };
    static const char *const SOL_LABELS[MK_SOL_COUNT] = {
        "J18 출력", "J19 출력", "J20 출력"
    };
    for (int s = 0; s < MK_SOL_COUNT; s++) {
        s_items[i] = (MkCfgItem){
            .key = SOL_KEYS[s], .group = "sol", .vtype = MK_VT_BOOL,
            .out = 1, .label = SOL_LABELS[s],
            .note = "외부 옵토·드라이버 필요 — 핀당 20 mA 가 상한이다" };
        i++;
    }
    return i;
}

/* WS2812 체인 J21~J24. */
static size_t add_led(size_t i)
{
    /* ── WS2812 체인 J21~J24 (데이터시트 §5.8) ──────────────────────── */
    s_items[i] = (MkCfgItem){
        .key = "led.count", .group = "led", .vtype = MK_VT_U8,
        .min = 0, .max = MK_LED_COUNT, .has_min = 1, .has_max = 1,
        .out = 1, .label = "체인 LED 수",
        .note = "J21 부터 순서대로 채운다 — 중간이 비면 뒤쪽이 동작하지 않는다" };
    i++;
    s_items[i] = (MkCfgItem){
        .key = "led.brightness", .group = "led", .vtype = MK_VT_U8,
        .min = 0, .max = 255, .has_min = 1, .has_max = 1,
        .out = 1, .label = "밝기",
        .note = "5V 레일이 꺼져 있으면 LED 가 켜지지 않는다" };
    s_items[i].def.u = 64;
    i++;
    /* 🔴 out 이 아니다. 이것은 켜고 끄는 출력이 아니라 **물린 스트립의
     *    성질**이다. out 으로 두면 TEST 를 빠져나올 때 기본값으로 돌아가
     *    색이 저 혼자 뒤집힌다.
     *
     *    안내문에 증상을 적는다. 사용자가 겪는 것은 "GRB" 라는 낱말이 아니라
     *    빨강을 넣었는데 초록이 켜지는 일이다. */
    s_items[i] = (MkCfgItem){
        .key = "led.grb", .group = "led", .vtype = MK_VT_BOOL,
        .label = "색 순서 GRB",
        .note = "빨강과 초록이 바뀌어 보이면 이 값을 뒤집는다 — 칩마다 다르다" };
    i++;

    /* 🔴 색을 0xRRGGBB 한 덩이로 두지 않는다. 그러면 화면에 생짜 숫자가
     *    떠서 손으로 계산해야 한다 — 필드 마스크가 `698` 로 보이던 것과
     *    같은 문제다. R·G·B 로 나누면 각각 0~255 이고, 반복 구조라 화면이
     *    표로 접는다. */
    static const char *const RGB_SUFFIX[3] = { ".r", ".g", ".b" };
    static const char *const RGB_LABEL[3]  = { "빨강", "초록", "파랑" };
    for (unsigned lamp = 21; lamp < 21u + MK_LED_COUNT; lamp++) {
        for (int c = 0; c < 3; c++) {
            size_t k = gen("led", lamp, RGB_SUFFIX[c], lamp, RGB_LABEL[c]);
            s_items[i] = (MkCfgItem){
                .key = s_keys[k], .group = "led", .vtype = MK_VT_U8,
                .min = 0, .max = 255, .has_min = 1, .has_max = 1,
                .out = 1, .label = s_labels[k] };
            i++;
        }
    }
    return i;
}

/* I2C 센서 포트 J10~J15. */
static size_t add_i2c(size_t i)
{
    /* ── I2C 센서 포트 J10~J15 (데이터시트 §5.4) ─────────────────────
     *
     * 🔴 짝 커넥터는 같은 버스다. J10·J11 은 물리적으로 같은 I2C3 에 병렬로
     *    붙어 있어 같은 주소를 함께 쓰면 충돌한다. 버스를 **열로** 주어
     *    어느 포트가 한 버스인지 훑으면 보이게 한다 — 포트마다 문장으로
     *    풀어 쓰면 표에 같은 말이 여섯 번 반복돼 정작 값이 안 읽힌다. */
    static const char *const I2C_BUS[MK_I2C_COUNT] = {
        "I2C3", "I2C3", "I2C5", "I2C5", "I2C1", "I2C1"
    };
    for (unsigned p = 0; p < MK_I2C_COUNT; p++) {
        unsigned jack = 10u + p;
        size_t k = gen("i2c", jack, ".bus", jack, "버스");
        s_items[i] = (MkCfgItem){
            .key = s_keys[k], .group = "i2c", .vtype = MK_VT_STR,
            .max = 7, .has_max = 1, .readonly = 1, .label = s_labels[k],
            .note = "같은 버스에 물린 두 포트는 주소가 겹치면 안 된다" };
        put(s_items[i].def.s, sizeof s_items[i].def.s, I2C_BUS[p]);
        i++;

        k = gen("i2c", jack, ".enabled", jack, "사용");
        s_items[i] = (MkCfgItem){
            .key = s_keys[k], .group = "i2c", .vtype = MK_VT_BOOL,
            .label = s_labels[k] };
        i++;

        /* 🔴 0 은 "미지정" 이다. 7비트 주소에서 실제로 쓸 수 있는 구간은
         *    0x08~0x77 이고 양 끝은 예약이지만, 하한을 0x08 로 두면
         *    기본값 0 이 범위 밖이 되어 $CFG,RESET 이 스스로 어긋난
         *    값으로 되돌린다. */
        k = gen("i2c", jack, ".addr", jack, "주소");
        s_items[i] = (MkCfgItem){
            .key = s_keys[k], .group = "i2c", .vtype = MK_VT_U8,
            .min = 0, .max = 0x77, .has_min = 1, .has_max = 1,
            .label = s_labels[k],
            .note = "0 = 미지정. 쓸 수 있는 7비트 주소는 0x08~0x77" };
        i++;

        k = gen("i2c", jack, ".kind", jack, "종류");
        s_items[i] = (MkCfgItem){
            .key = s_keys[k], .group = "i2c", .vtype = MK_VT_ENUM,
            .choices = I2C_KIND_CHOICES, .n_choices = 5,
            .choice_labels = I2C_KIND_LABELS,
            .label = s_labels[k],
            .note = "꽂은 센서 종류" };
        i++;

        k = gen("i2c", jack, ".period_ms", jack, "주기");
        s_items[i] = (MkCfgItem){
            .key = s_keys[k], .group = "i2c", .vtype = MK_VT_U16,
            .min = 10, .max = 60000, .has_min = 1, .has_max = 1,
            .unit = "ms", .label = s_labels[k] };
        s_items[i].def.u = 200;
        i++;
    }
    return i;
}

void mk_cfgtable_init(MkConfig *cfg)
{
    /* 🔴 그룹마다 함수 하나. 예전에는 254 줄짜리 한 덩어리였고, 항목을
     *    더할 때마다 `i++` 를 손으로 세며 남의 그룹 한가운데에 끼워
     *    넣어야 했다. 무엇이 어느 그룹인지도 주석에만 있었다. */
    size_t i = 0;
    memset(s_items, 0, sizeof s_items);
    s_gen = 0;                       /* 이름 자리를 처음부터 다시 잡는다 */

    i = add_dev(i);
    i = add_tx(i);
    i = add_pwr(i);
    i = add_adc(i);
    i = add_ain(i);
    i = add_sol(i);
    i = add_led(i);
    i = add_i2c(i);

    for (size_t n = 0; n < i; n++) {
        s_items[n].cur = s_items[n].def;
    }

    cfg->items = s_items;
    cfg->count = i;
    cfg->dirty = 0;
}

/* ---- 저장 형식 ---------------------------------------------------------- */

size_t mk_cfgtable_blob_size(void)
{
    return sizeof(MkValue) * ITEM_COUNT;
}

void mk_cfgtable_pack(const MkConfig *cfg, void *out)
{
    MkValue *dst = (MkValue *)out;
    for (size_t n = 0; n < cfg->count; n++) {
        dst[n] = cfg->items[n].cur;
    }
}

void mk_cfgtable_unpack(MkConfig *cfg, const void *in)
{
    const MkValue *src = (const MkValue *)in;
    for (size_t n = 0; n < cfg->count; n++) {
        cfg->items[n].cur = src[n];
    }
}
