#include "mk_cfgtable.h"

#include <string.h>

/* dev 1 + tx 3 + pwr 4 + adc 2 + ain 5×7 */
#define ITEM_COUNT   (1 + 3 + 4 + 2 + MK_AIN_COUNT * 5)

static MkCfgItem s_items[ITEM_COUNT];

/* 🔴 저장 덩어리가 Flash 의 staging 버퍼에 들어가는지 **컴파일 때** 본다.
 *
 *    처음에 staging 을 512 로 어림잡았는데 실제 덩어리는 1,080 바이트였다.
 *    실기기에서 $CFG,SAVE 가 ERR,BUSY 로 떨어지고 나서야 알았다. 항목을
 *    늘릴 때마다 같은 실수를 할 수 있으므로 여기서 막는다. */
_Static_assert(ITEM_COUNT <= MK_CFG_MAX_ITEMS,
               "설정 항목이 MK_CFG_MAX_ITEMS 를 넘었다");
_Static_assert(sizeof(MkValue) * ITEM_COUNT + 32u <= 2048u,
               "저장 덩어리가 mk_flash.c 의 staging 버퍼를 넘는다 "
               "— 둘을 함께 키워야 한다");

/* ADS1256 이 지원하는 DRATE (데이터시트). */
static const uint32_t DRATE_CHOICES[] = {
    2, 5, 10, 15, 25, 30, 50, 60, 100, 500, 1000, 2000, 3750, 7500
};
static const uint32_t PGA_CHOICES[] = { 1, 2, 4, 8, 16, 32, 64 };

/* 채널별 키·라벨을 담아 둘 자리. 포인터로 참조되므로 살아 있어야 한다. */
static char s_keys[MK_AIN_COUNT * 5][MK_CFG_KEY_MAX + 1];
static char s_labels[MK_AIN_COUNT * 5][24];

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

/* "ain0.zero" 같은 키를 만든다. libc 의 sprintf 를 쓰지 않는다. */
static void make_key(char *dst, size_t cap, int ch, const char *suffix)
{
    size_t n = 0;
    const char *p;
    for (p = "ain"; *p && n + 1u < cap; p++) dst[n++] = *p;
    if (n + 1u < cap) dst[n++] = (char)('0' + ch);
    for (p = suffix; *p && n + 1u < cap; p++) dst[n++] = *p;
    dst[n] = '\0';
}

/* "J3 영점" 같은 라벨. 커넥터 번호는 채널 + 3 (데이터시트 §5.3). */
static void make_label(char *dst, size_t cap, int ch, const char *what)
{
    size_t n = 0;
    const char *p;
    if (n + 1u < cap) dst[n++] = 'J';
    if (n + 1u < cap) dst[n++] = (char)('0' + ch + 3);
    if (n + 1u < cap) dst[n++] = ' ';
    for (p = what; *p && n + 1u < cap; p++) dst[n++] = *p;
    dst[n] = '\0';
}

void mk_cfgtable_init(MkConfig *cfg)
{
    size_t i = 0;
    memset(s_items, 0, sizeof s_items);

    s_items[i] = (MkCfgItem){ .key = "dev.id", .group = "dev",
                              .vtype = MK_VT_STR, .max = 15, .has_max = 1,
                              .label = "장치 ID" };
    put(s_items[i].def.s, sizeof s_items[i].def.s, "1");
    i++;

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

    /* 🔴 전원 레일. 라벨에 핀 번호를 쓰지 않는다 (설계 원칙 1). */
    s_items[i] = (MkCfgItem){ .key = "pwr.24v", .group = "pwr",
                              .vtype = MK_VT_BOOL, .label = "24V 전원" };
    i++;
    s_items[i] = (MkCfgItem){ .key = "pwr.14v9", .group = "pwr",
                              .vtype = MK_VT_BOOL, .label = "14.9V 전원" };
    i++;
    /* 🔴 5V 는 끌 수 없다. 쿨링 팬(J34)이 직결이고 상시 동작이 요구사항이다
     *    (데이터시트 §4). 인터록으로 두어 사유가 사용자에게 전달되게 한다 —
     *    읽기 전용으로만 두면 왜 안 되는지가 사라진다(규격 §5.2). */
    s_items[i] = (MkCfgItem){ .key = "pwr.5v", .group = "pwr",
                              .vtype = MK_VT_BOOL, .interlocked = 1,
                              .label = "5V 전원",
                              .note = "쿨링 팬이 5V 전원에 직결이라 끌 수 없다" };
    s_items[i].def.u = 1;
    i++;
    s_items[i] = (MkCfgItem){ .key = "pwr.seq_delay_ms", .group = "pwr",
                              .vtype = MK_VT_U16, .min = 0, .max = 5000,
                              .has_min = 1, .has_max = 1,
                              .unit = "ms", .label = "레일 기동 간격" };
    s_items[i].def.u = 500;
    i++;

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

    for (int ch = 0; ch < MK_AIN_COUNT; ch++) {
        int k = ch * 5;

        make_key(s_keys[k], sizeof s_keys[k], ch, ".enabled");
        make_label(s_labels[k], sizeof s_labels[k], ch, "사용");
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

        make_key(s_keys[k + 1], sizeof s_keys[k + 1], ch, ".period_ms");
        make_label(s_labels[k + 1], sizeof s_labels[k + 1], ch, "수집 주기");
        /* 🔴 최대가 60초다. tx.period_ms(전송 주기)의 10초를 따라 적었던
         *    것인데 둘은 다른 것이다. 느린 센서를 1분에 한 번만 읽는 것은
         *    정상이고, 오히려 링크 부하를 줄인다. */
        s_items[i] = (MkCfgItem){ .key = s_keys[k + 1], .group = "ain",
                                  .vtype = MK_VT_U16, .min = 10, .max = 60000,
                                  .has_min = 1, .has_max = 1, .unit = "ms",
                                  .label = s_labels[k + 1] };
        s_items[i].def.u = 100;
        i++;

        make_key(s_keys[k + 2], sizeof s_keys[k + 2], ch, ".zero");
        make_label(s_labels[k + 2], sizeof s_labels[k + 2], ch, "영점");
        s_items[i] = (MkCfgItem){ .key = s_keys[k + 2], .group = "ain",
                                  .vtype = MK_VT_F32, .min = 0.0f, .max = 25.0f,
                                  .has_min = 1, .has_max = 1, .unit = "mA",
                                  .label = s_labels[k + 2] };
        s_items[i].def.f = 4.0f;
        i++;

        make_key(s_keys[k + 3], sizeof s_keys[k + 3], ch, ".scale");
        make_label(s_labels[k + 3], sizeof s_labels[k + 3], ch, "스케일");
        s_items[i] = (MkCfgItem){ .key = s_keys[k + 3], .group = "ain",
                                  .vtype = MK_VT_F32, .label = s_labels[k + 3] };
        s_items[i].def.f = 1.0f;
        i++;

        make_key(s_keys[k + 4], sizeof s_keys[k + 4], ch, ".unit");
        make_label(s_labels[k + 4], sizeof s_labels[k + 4], ch, "단위");
        s_items[i] = (MkCfgItem){ .key = s_keys[k + 4], .group = "ain",
                                  .vtype = MK_VT_STR, .max = 7, .has_max = 1,
                                  .label = s_labels[k + 4] };
        i++;
    }

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
