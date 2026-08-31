#include "mk_cfgtable.h"

#include <string.h>

#include "mk_ws2812.h"      /* MK_LED_COUNT — 정의는 저쪽이 들고 있다 */
#include "mk_i2c.h"         /* MK_I2C_COUNT · 버스 표 — 정의는 저쪽이 들고 있다 */
#include "mk_i2c_drivers.h" /* 종류의 수집 주기 하한 — 데이터시트 값은 드라이버가 */
#include "mk_linkbaud.h"    /* 고를 수 있는 링크 속도 — 목록은 저쪽에만 있다 */

/* 이 보드가 가진 것들 (데이터시트 §5).
 *   J18~J20  디지털 입력 3 — 카탈로그에는 sol.debounce_ms 하나만 남는다
 *            (사용자 확정 2026-08-18. "켜라/꺼라" 가 성립하지 않는 입력
 *            이라, 남는 설정은 디바운스 시간뿐이다 — 아래 add_sol() 참고)
 *   J21~J24  WS2812 체인 — 개수·밝기 + 램프마다 R·G·B
 *   J10~J15  I2C 6포트 — 버스·사용·종류·주소·주기
 *
 * 🔴 MK_LED_COUNT 도 MK_I2C_COUNT 도 여기 다시 적지 않는다. 저쪽이 핀을
 *    내는 층이라, 두 곳에 적으면 카탈로그가 말하는 개수와 실제로 움직이는
 *    핀 수가 갈린다. sol 은 이제 채널 수와 무관하게 항목이 하나뿐이라
 *    MK_SOL_COUNT 를 끌어오지 않는다(app/mk_solctl.h 가 그 상수를 들고
 *    있다). */

/* I2C 포트에 꽂을 수 있는 센서 **종류**.
 *
 * 🔴 칩 모델이 아니라 종류다 (사용자 확정 2026-08-17). 조도계가 무엇이든
 *    호스트가 할 일은 같고, 펌웨어만 드라이버를 고르면 된다. 모델로 두면
 *    새 칩을 살 때마다 카탈로그를 고쳐야 한다.
 *
 * 🔴 값이 둘인 종류는 온습도뿐이다. 나머지는 전부 하나다.
 *    호스트의 화면 표(host/gui/screen.py 의 I2C_KIND_QUANTITIES)와 같아야
 *    한다. 🔴 [2026-08-20] 자동 대조는 없다 — 시뮬레이터와 함께 지웠다. */
/* LCD SPI 클럭으로 고를 수 있는 값(kHz).
 *
 * 🔴 분주비로 실제로 낼 수 있는 값만 넣는다. SPI2 커널 클럭이 64 MHz 이고
 *    (per_ck = hsi_ker_ck — bsp/mk_lcd_io.c spi_init 의 사연) HAL 의
 *    분주비는 2 의 거듭제곱이다: 64/4·64/8·64/16·64/32. 64/2 = 32 MHz 는
 *    ILI9488 의 쓰기 상한 20 MHz(twc MIN 50 ns, p.332 §17.4.3)를 넘으므로
 *    목록에 없다.
 *
 * 🔴 [2026-08-20] 이 목록의 짝이던 시뮬레이터 상수는 지웠다. 화면은 이제
 *    카탈로그가 실어 보내는 choices 만 보므로 맞출 상대가 없다. */
static const uint32_t LCD_SPI_KHZ_CHOICES[] = { 2000u, 4000u, 8000u, 16000u };

/* 호스트 링크 속도 (규격 §4.2.6).
 *
 * 🔴 값을 여기 손으로 적지 않는다. `MK_LINKBAUD_CHOICE_LIST` 한 곳에만
 *    있고, main.c 가 **같은 목록으로** 컴파일 때 오차를 검사한다
 *    (_Static_assert). 두 곳에 적으면 카탈로그에는 있는데 실제로는 못 내는
 *    속도가 생기고, 그것은 사용자가 고르는 순간에만 드러난다 — 즉 링크가
 *    끊긴 뒤에 드러난다. */
#define LINK_BAUD_ITEM(v)  v,
static const uint32_t LINK_BAUD_CHOICES[] = {
    MK_LINKBAUD_CHOICE_LIST(LINK_BAUD_ITEM)
};
#undef LINK_BAUD_ITEM
#define LINK_BAUD_CHOICE_COUNT \
    ((uint8_t)(sizeof LINK_BAUD_CHOICES / sizeof LINK_BAUD_CHOICES[0]))

static const uint32_t I2C_KIND_CHOICES[] = { 0u, 1u, 2u, 3u, 4u };
static const char *const I2C_KIND_LABELS[] = {
    "없음", "조도", "온습도", "적외 온도", "방수 온도"
};

/* (클라우드 타입은 2026-08-26 에 열거형에서 자유 문자열로 바뀌었다 —
 * 유량계 두 대가 똑같이 "flow" 로 나가면 젯슨에서 구분할 수 없었다.
 * 사용자가 친 문자열이 그대로 record type 이 된다. mk_cloud.c 참고.) */

/* (밸브 입력 선택은 2026-08-22 에 걷어냈다 — 좌·우 밸브가 어느 입력에서
 * 올지 모르는 현장 구성이라, 밸브 상태는 **디지털 입력들의 OR** 로
 * 합성한다(사용자 확정). mk_cloud.c 의 cloud_valve_state 참고.) */

/* dev 1 + tx 9(마스크 5 + 주기 + 자릿수 + 스키마 버전 + 순번 seq)
 * + pwr 4 + adc 2 + ain 5×7
 * + sol 1(디바운스) + din 3×2(이름·타입) + led 3 + i2c 5×7(송신 주기 포함)
 * + gnss 3(사용·통신속도·원시 문장 에코)
 * + link 1(호스트 링크 속도)
 * + lcd 5(사용·갱신 주기·SPI 클럭·되읽기 대조 주기·전면 갱신 주기)
 *
 * (led{21..24}.r/g/b 수동 색 12개는 2026-08-22 에 걷어냈다 — LED 가
 *  상태 표시 전용이 됐다(app/mk_statled.h, 사용자 설계). 색을 사용자가
 *  정하는 항목이 남아 있으면 상태 색과 싸운다.) */
#define ITEM_COUNT   (1 + 9 + 4 + 2 + MK_AIN_COUNT * 7 \
                      + 1 \
                      + 6 \
                      + MK_I2C_COUNT * 7 \
                      + 4 + 3 \
                      + 1 \
                      + 5)

/* 이름을 만들어 써야 하는 항목 수 (ain·i2c). sol 은 고정 문자열이다. */
#define GEN_COUNT    (MK_AIN_COUNT * 7 + MK_I2C_COUNT * 7 + 6)

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

/* GNSS UART 통신속도. 업계에서 흔히 쓰는 NMEA 보율 상수다 — UM981
 * 자체의 공장 기본값은 데이터시트를 확보하지 못해 **확인 필요**다(§5).
 * 115200 을 기본으로 둔 것은 이 보드의 다른 UART(USART3, 호스트 VCP)가
 * 이미 115200 이상을 쓰고 있어 자릿수가 맞는다는 것뿐이고, 실기기로
 * 검증되지 않았다. */
static const uint32_t GNSS_BAUD_CHOICES[] = {
    4800, 9600, 19200, 38400, 57600, 115200, 230400
};

/* 만들어 쓰는 키·라벨을 담아 둘 자리. 포인터로 참조되므로 살아 있어야 한다. */
static char s_keys[GEN_COUNT][MK_CFG_KEY_MAX + 1];
static char s_labels[GEN_COUNT][24];
static size_t s_gen;              /* 다음에 쓸 자리 */

/* 🔴 [판단, 2026-08-19] `time_source` 는 여기 없다 — 일부러 뺐다.
 *
 *    레코드의 `t` 는 등급에 따라 뜻이 다르다(규격 §7.1.2): device_clock
 *    이면 부팅 후 경과 ms, gnss_* 면 UTC epoch_ms. `time_source` 가 바로
 *    그 구분을 말해 주는 필드인데, 마스크로 끌 수 있으면 호스트가
 *    device_clock 의 `t` 를 UTC 로 오해해 저장할 길이 열린다 — 이 시간축을
 *    만든 이유(카메라 프레임 정렬) 자체가 무너지는 사고다.
 *
 *    그래서 `i2c` 의 `quantity`·`value`, `din` 의 `connector_id`·`state`
 *    와 같은 자리(mk_telem.c 의 build_record 계열)로 옮겼다 — 마스크
 *    비트가 아예 없고 항상 실린다. bit=1 자리는 다른 필드가 새로 쓰지
 *    않는다(예전에 이 값을 저장했던 보드의 `tx.fields` 에 그 비트가 서
 *    있어도 이제는 조용히 무시될 뿐이다). */
/* 🔴 [개정, 2026-08-19] `records` 열(kinds)이 늘었다 — 이 비트가 실제로
 *    실리는 레코드 종류다(규격 §7.2 "해당 레코드"). `tx.fields` 하나를
 *    ain·i2c·din 세 마스크로 나누면서, "비트 번호는 표 하나를 공유하되
 *    어느 레코드에 해당하는지는 비트마다 다르다"는 결정이 났다(사용자
 *    확정 2026-08-18) — 번호를 셋이 따로 쓰면 사람이 못 읽는다.
 *
 *    add_tx() 가 여기서 `tx.fields_ain`·`tx.fields_i2c`·`tx.fields_din`
 *    각각의 최댓값·기본값을 끌어내고, mk_cfgwire.c 가 여기서 `cfg_field`
 *    의 `records` 배열을 만들고, mk_telem.c 의 field_on() 이 여기서
 *    "해당 없는 비트는 마스크에 서 있어도 무시" 를 판정한다. */
/* 🔴 [신설, 2026-08-20] 비트 10~15 는 GNSS 측위 레코드(규격 §7.8)의 것이다.
 *
 *    기본값을 이렇게 고른 근거(규격 §7.8.5): 켜 둔 셋(alt·sats·fix)은
 *    "어디에 있고 그 값을 믿어도 되는가" 에 답한다. 꺼 둔 셋은 이차적이다 —
 *    hdop 은 sats·fix 가 이미 말한 신뢰도를 한 번 더 말하는 수이고,
 *    speed·course 는 위치가 아니라 운동이라 쓰는 쪽이 정해져 있지 않다.
 *    이 레코드는 1 Hz 라 셋을 다 켜도 30 B/s 남짓이므로, 필요하면 망설일
 *    이유 없이 켜면 된다.
 *
 *    🔴 lat·lon·fix_t 는 여기 없다 — 마스크 비트가 아예 없고 항상 실린다.
 *    위치가 빠진 GNSS 레코드는 아무 말도 안 한다(i2c 의 quantity·value,
 *    din 의 connector_id·state 와 같은 자리, 규격 §7.8.5). */
static const MkFieldBit FIELDS[] = {
    { 0, "device_id",       0, "보드 식별자",
      MK_FIELD_AIN | MK_FIELD_I2C | MK_FIELD_DIN | MK_FIELD_GNSS },
    { 2, "time_quality",    0, "시간 품질",
      MK_FIELD_AIN | MK_FIELD_I2C | MK_FIELD_DIN | MK_FIELD_GNSS },
    { 3, "raw",             1, "ADS1256 원시 카운트",  MK_FIELD_AIN },
    { 4, "ma",              1, "전류 (mA)",            MK_FIELD_AIN },
    { 5, "value",           1, "물리량 환산",          MK_FIELD_AIN },
    { 6, "unit",            0, "단위",                 MK_FIELD_AIN },
    { 7, "status",          1, "채널 상태",  MK_FIELD_AIN | MK_FIELD_I2C },
    { 8, "capture_counter", 0, "획득 카운터",          MK_FIELD_AIN },
    /* 🔴 비트 9 는 connector_id 였으나 2026-08-21 잠금으로 회수됐다 —
     *    이제 마스크와 무관하게 항상 실린다(규격 §7.2·§7.5, mk_telem.c).
     *    저장된 옛 마스크에 서 있는 비트 9 는 무해하게 무시된다.
     *    **다른 필드에 재사용하지 않는다** — 옛 마스크가 그 필드를
     *    멋대로 켠다. */
    { 10, "alt",            1, "고도 (m)",             MK_FIELD_GNSS },
    { 11, "sats",           1, "위성 수",              MK_FIELD_GNSS },
    { 12, "fix",            1, "측위 품질",            MK_FIELD_GNSS },
    { 13, "hdop",           0, "HDOP",                 MK_FIELD_GNSS },
    { 14, "speed",          0, "대지 속도 (m/s)",      MK_FIELD_GNSS },
    { 15, "course",         0, "대지 방위 (도)",       MK_FIELD_GNSS },
    /* 🔴 [2026-08-22] 젯슨 링크 필드 확장 (사용자 지시 — "기존 NDJSON 필드
     *    선택 화면에서 원하는 필드를 조절"). 체크박스는 이 표에서 저절로
     *    생긴다. valve 는 좌·우 밸브 입력의 OR 태깅, 주변 온도는 적외
     *    (MLX90614) 전용, 이슬점은 온습도(AM2320) 전용 — 해당 없는 포트의
     *    레코드에는 켜도 안 실린다. 자세한 반영처는 mk_cloud.c. */
    { 16, "valve",          0, "밸브",                 MK_FIELD_AIN | MK_FIELD_I2C },
    { 17, "diff_age",       0, "보정 나이 (s)",        MK_FIELD_GNSS },
    { 18, "station_id",     0, "기준국",               MK_FIELD_GNSS },
    { 19, "temp_ambient",   0, "주변 온도 (적외)",     MK_FIELD_I2C },
    { 20, "dewpoint",       0, "이슬점 (온습도)",      MK_FIELD_I2C },
    /* imu 레코드의 선택 필드 (전송 화면의 IMU 카드 — 사용자 확정
     * 2026-08-22 "IMU 도 GNSS 와 분리"). 값은 RAWIMUX status bit21~31
     * × 0.125 + 23 °C. ax~gz 는 마스크 밖(항상 실림)이다. */
    { 21, "imu_temp",       0, "IMU 온도",             MK_FIELD_IMU },
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

/* FIELDS 를 훑어 kind 에 해당하는 비트만으로 마스크의 최댓값·기본값을
 * 만든다. mk_cfgtable_pack/unpack 이 값만 저장하듯, 여기도 "표에서 끌어낸
 * 것" 과 "손으로 적은 것" 이 갈리면 안 되는 자리다(add_tx() 원래 주석과
 * 같은 근거, 2026-08-19 셋으로 나누며 함수로 뽑았다). */
static void field_mask_bounds(uint8_t kind, uint32_t *out_max, uint32_t *out_def)
{
    uint32_t all = 0u;
    uint32_t def = 0u;
    for (size_t f = 0; f < sizeof FIELDS / sizeof *FIELDS; f++) {
        if ((FIELDS[f].kinds & kind) == 0u) {
            continue;                    /* 이 레코드에는 해당 없는 비트 */
        }
        all |= (1u << FIELDS[f].bit);
        if (FIELDS[f].def) {
            def |= (1u << FIELDS[f].bit);
        }
    }
    *out_max = all;
    *out_def = def;
}

/* 전송.
 *
 * 🔴 [개정, 2026-08-19] `tx.fields` 하나가 ain·i2c·din 을 전부 묶던 것을
 *    셋으로 나눴다(규격 §7.2·§7.5·§7.6) — `ain` 의 `raw` 를 꺼도 `i2c` 와는
 *    무관해야 하는데, 마스크가 하나면 그럴 수 없었다. 비트 번호는
 *    FIELDS 표 하나를 공유하고, 마스크마다 자기 kind 에 해당하는 비트만
 *    켤 수 있다(field_mask_bounds()). */
static size_t add_tx(size_t i)
{
    uint32_t max_u, def_u;

    field_mask_bounds(MK_FIELD_AIN, &max_u, &def_u);
    s_items[i] = (MkCfgItem){ .key = "tx.fields_ain", .group = "tx",
                              .vtype = MK_VT_U32, .min = 0,
                              .has_min = 1, .has_max = 1,
                              .label = "NDJSON 필드 마스크 (아날로그)" };
    s_items[i].def.u = def_u;
    s_items[i].max = (float)max_u;
    i++;

    field_mask_bounds(MK_FIELD_I2C, &max_u, &def_u);
    s_items[i] = (MkCfgItem){ .key = "tx.fields_i2c", .group = "tx",
                              .vtype = MK_VT_U32, .min = 0,
                              .has_min = 1, .has_max = 1,
                              .label = "NDJSON 필드 마스크 (I2C)" };
    s_items[i].def.u = def_u;
    s_items[i].max = (float)max_u;
    i++;

    field_mask_bounds(MK_FIELD_DIN, &max_u, &def_u);
    s_items[i] = (MkCfgItem){ .key = "tx.fields_din", .group = "tx",
                              .vtype = MK_VT_U32, .min = 0,
                              .has_min = 1, .has_max = 1,
                              .label = "NDJSON 필드 마스크 (디지털 입력)" };
    s_items[i].def.u = def_u;
    s_items[i].max = (float)max_u;
    i++;

    /* 🔴 [신설, 2026-08-20] 네 번째 마스크. GNSS 측위 레코드(규격 §7.8)는
     *    주기가 다르고(모듈이 정하는 1 Hz) 성격도 다르다 — 아날로그를
     *    가볍게 하려고 비트를 끄다가 위성 수가 같이 사라지면 안 된다. */
    field_mask_bounds(MK_FIELD_GNSS, &max_u, &def_u);
    s_items[i] = (MkCfgItem){ .key = "tx.fields_gnss", .group = "tx",
                              .vtype = MK_VT_U32, .min = 0,
                              .has_min = 1, .has_max = 1,
                              .label = "NDJSON 필드 마스크 (GNSS)" };
    s_items[i].def.u = def_u;
    s_items[i].max = (float)max_u;
    i++;

    /* 🔴 [신설, 2026-08-22] 다섯 번째 마스크 — imu 레코드(젯슨 링크,
     *    UM981 RAWIMUX). GNSS 와 장치는 같지만 레코드가 다르다(사용자
     *    확정 — "IMU 전송도 GNSS 와 분리"). ax~gz 는 마스크 밖이다. */
    field_mask_bounds(MK_FIELD_IMU, &max_u, &def_u);
    s_items[i] = (MkCfgItem){ .key = "tx.fields_imu", .group = "tx",
                              .vtype = MK_VT_U32, .min = 0,
                              .has_min = 1, .has_max = 1,
                              .label = "NDJSON 필드 마스크 (IMU)" };
    s_items[i].def.u = def_u;
    s_items[i].max = (float)max_u;
    i++;

    s_items[i] = (MkCfgItem){ .key = "tx.period_ms", .group = "tx",
                              .vtype = MK_VT_U16, .min = 10, .max = 10000,
                              .has_min = 1, .has_max = 1,
                              .unit = "ms", .label = "전송 주기" };
    s_items[i].def.u = 100;
    i++;

    /* 🔴 seq — 줄 순번(유실 검출 근거, HANDOFF_0831 결정 2). 기본 켜짐.
     *    체크박스로 뺀 것은 사용자 결정(2026-08-31)이고, 끄면 어느 쪽
     *    수신자도 링크 유실을 셀 수 없다는 것을 note 가 말한다. */
    s_items[i] = (MkCfgItem){ .key = "tx.seq", .group = "tx",
                              .vtype = MK_VT_BOOL,
                              .label = "순번(seq)",
                              .note = "끄면 GUI 도 젯슨도 링크 유실을 세지 못한다" };
    s_items[i].def.u = 1;
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
    /* 🔴 젯슨(J29) NDJSON 의 schema_ver (사용자 요청 2026-08-22 — 공통
     *    필드 중 값을 바꿀 수 있는 것은 스키마 버전·장치 식별자다.
     *    장치 식별자는 기존 dev.id 를 그대로 쓴다). 본선(규격 v3)의
     *    schema_ver 3 은 규격이 정하는 값이라 이 항목과 무관하다. */
    s_items[i] = (MkCfgItem){ .key = "tx.schema_ver", .group = "tx",
                              .vtype = MK_VT_U8, .min = 1, .max = 9,
                              .has_min = 1, .has_max = 1,
                              .label = "스키마 버전",
                              .note = "젯슨 링크 레코드의 schema_ver" };
    s_items[i].def.u = 1;
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
                              .note = "끄면 24V·14.9V·팬·수집·WS2812 가 함께 멈춘다" };
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
    /* 🔴 [개정 2026-08-23] "데이터율" 이라는 이름표가 아날로그 탭의
     *    "수집 주기(ms)" 와 헷갈렸다 — 사용자가 60 을 보고 "얼마를 넣어야
     *    몇 ms 인지 모르겠다" 고 했다. 단위가 다른 값이다: 이것은 변환기
     *    자체의 속도(초당 표본 수)이고, 채널을 언제 읽을지는 채널별 수집
     *    주기가 정한다. 이름표와 안내문이 그 관계를 말하게 한다.
     *    🔴 안내문은 짧아야 한다 — choices 14개가 같은 줄에 실려
     *    MK_CFGWIRE_LIST_LINE_MAX(384B)가 빠듯하다. 길면 그 줄이 통째로
     *    빠져 카탈로그가 거기서 끊긴다(재동결에서 실제로 그랬다). */
    s_items[i] = (MkCfgItem){ .key = "adc.drate", .group = "adc",
                              .vtype = MK_VT_ENUM, .unit = "SPS",
                              .choices = DRATE_CHOICES, .n_choices = 14,
                              .label = "ADC 변환 속도",
                              .note = "초당 표본 수 — 60이면 약 17 ms에 한 번. "
                                      "채널별 수집 주기(ms)와는 다른 값이다" };
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

        /* 🔴 단위는 젯슨 레코드의 **값 필드 이름**으로도 쓰인다
         *    ("lpm": 5.0). 그래서 영문·숫자·밑줄만 받는다 (2026-08-26). */
        k = gen("ain", (unsigned)ch, ".unit", jack, "단위");
        s_items[i] = (MkCfgItem){ .key = s_keys[k], .group = "ain",
                                  .vtype = MK_VT_STR, .max = 7, .has_max = 1,
                                  .ascii_ident = 1,
                                  .label = s_labels[k],
                                  .note = "젯슨 레코드의 값 이름으로도 쓰인다"
                                          " — 영문·숫자·_ 만" };
        i++;

        /* 🔴 사용자가 붙이는 이름 — "J3" 대신 "유압" (사용자 요청
         *    2026-08-20). 보드에 저장해야 하는 이유: 이름은 이 커넥터에
         *    물리적으로 무엇이 물렸는가의 이름이라, 호스트를 바꿔도(PC →
         *    Jetson) 따라가야 한다. 비면 화면이 J 번호로 돌아간다.
         *    최대 23바이트 = MK_ARG_MAX — UTF-8 한글 7자다. */
        k = gen("ain", (unsigned)ch, ".name", jack, "이름");
        s_items[i] = (MkCfgItem){ .key = s_keys[k], .group = "ain",
                                  .vtype = MK_VT_STR, .max = 23, .has_max = 1,
                                  .label = s_labels[k],
                                  .note = "비우면 커넥터 번호로 보인다" };
        i++;

        /* 🔴 타입 — "이 채널의 센서가 NDJSON 에서 뭐라 불리는가"를
         *    사용자가 **직접 친다** (사용자 확정 2026-08-26 — 열거형이던
         *    것을 자유 문자열로. 유량계 두 대가 같은 "flow" 로 나가면
         *    젯슨에서 구분이 안 됐다. flow_front 처럼 가른다).
         *    빈 값 = 젯슨 링크 미발행 (계약 §16.6). 이름(.name)과 다르다 —
         *    이름은 화면 표시용(한글 OK), 이것은 전선의 type 문자열. */
        k = gen("ain", (unsigned)ch, ".cloud", jack, "타입");
        s_items[i] = (MkCfgItem){ .key = s_keys[k], .group = "ain",
                                  .vtype = MK_VT_STR, .max = 15, .has_max = 1,
                                  .ascii_ident = 1,
                                  .label = s_labels[k],
                                  .note = "젯슨 레코드의 type — 비우면 안"
                                          " 내보낸다. 영문·숫자·_ 만" };
        i++;
    }
    return i;
}

/* 디지털 입력 J18~J20. */
static size_t add_sol(size_t i)
{
    /* ── 디지털 입력 J18~J20 (데이터시트 §5.7, 사용자 확정 2026-08-18) ──
     *
     * 🔴 출력이 아니라 입력이다. 넷리스트 확인 결과 커넥터 pin1 이 MCU
     *    핀에 직결이고 사이에 부품이 없다 — 옵토커플러가 커넥터 반대편
     *    (외부)에 붙고, 보드는 그 신호를 읽는다. "켜라/꺼라" 가 성립하지
     *    않으므로 sol.j18~sol.j20 은 카탈로그에 없다. 지금 상태는 규격
     *    §7.6 의 `din` 텔레메트리와 `$STAT` 의 `din` 배열로 온다.
     *
     * 🔴 남는 것은 디바운스 시간뿐이다. 옵토 접점이 흔들리는 정도는
     *    커넥터마다 물리는 외부 장치에 따라 달라질 수 있어, 펌웨어
     *    상수로 고정하지 않고 현장에서 조정할 수 있게 카탈로그에
     *    남긴다. `out` 이 아니다 — 켜고 끄는 출력이 아니라 필터 값이라
     *    TEST 이탈 때 되돌릴 "출력" 이 없다.
     *
     * 🔴 [2026-08-20] 짝이던 시뮬레이터 항목은 지웠다. 이 항목의 값·라벨·
     *    note·범위는 이제 여기가 유일한 출처다. */
    s_items[i] = (MkCfgItem){
        .key = "sol.debounce_ms", .group = "sol", .vtype = MK_VT_U16,
        .min = 0, .max = 1000, .has_min = 1, .has_max = 1, .unit = "ms",
        .label = "디바운스",
        .note = "이보다 짧게 흔들리는 신호는 상태 변화로 보지 않는다" };
    s_items[i].def.u = 5;
    i++;

    /* 사용자가 붙이는 이름 — ain 의 .name 과 같다(그쪽 주석 참고).
     * 키의 숫자가 커넥터 번호 그대로다(J18~J20). */
    for (unsigned jack = 18; jack <= 20; jack++) {
        size_t k = gen("din", jack, ".name", jack, "이름");
        s_items[i] = (MkCfgItem){
            .key = s_keys[k], .group = "sol", .vtype = MK_VT_STR,
            .max = 23, .has_max = 1, .label = s_labels[k],
            .note = "비우면 커넥터 번호로 보인다" };
        i++;

        /* 🔴 타입 — ain 의 .cloud 와 같은 원칙 (사용자 확정 2026-08-31,
         *    HANDOFF_0831 검토 5). 사용자가 친 문자열이 그대로 상태 변화
         *    레코드의 type 이 된다(J20 에 "valve" 면 기존 젯슨 수신분과
         *    동일). 빈 값 = 미발행. gnss 태깅용 밸브 상태(입력들의 OR)는
         *    이것과 무관하게 그대로다(mk_cloud.c cloud_valve_state). */
        k = gen("din", jack, ".cloud", jack, "타입");
        s_items[i] = (MkCfgItem){ .key = s_keys[k], .group = "sol",
                                  .vtype = MK_VT_STR, .max = 15, .has_max = 1,
                                  .ascii_ident = 1,
                                  .label = s_labels[k],
                                  .note = "젯슨 레코드의 type — 비우면 안"
                                          " 내보낸다. 밸브면 valve" };
        i++;
    }
    return i;
}

/* GNSS/PPS 시간축(J16, 데이터시트 §5.5, Phase 3). */
static size_t add_gnss(size_t i)
{
    /* 🔴 [2026-08-20] 짝이던 시뮬레이터 항목은 지웠다 — 이제 여기가
     *    유일한 출처다(sol.debounce_ms 와 같은 사정). */
    s_items[i] = (MkCfgItem){
        .key = "gnss.enabled", .group = "gnss", .vtype = MK_VT_BOOL,
        .label = "GNSS 사용",
        .note = "J16 에 GNSS 모듈이 꽂혀 있을 때만 켠다 — 없으면 NMEA 가 "
                "안 와서 시간 등급이 device_clock 에 머문다" };
    i++;
    /* 🔴 note 를 길게 두면 이 줄이 mk_cfgwire_list 의 320바이트 버퍼를
     *    넘어 **조용히 카탈로그에서 빠진다** — I2C 종류 항목에서 실제로
     *    겪은 문제다(위 mk_cfgwire.c 주석, 2026-08-17). choices 배열까지
     *    있는 enum 항목이라 여유가 적어 note 를 짧게 둔다. */
    s_items[i] = (MkCfgItem){
        .key = "gnss.baud", .group = "gnss", .vtype = MK_VT_ENUM,
        .choices = GNSS_BAUD_CHOICES, .n_choices = 7, .unit = "bps",
        .label = "GNSS 통신속도",
        .note = "UM981 기본값 확인 필요" };
    s_items[i].def.u = 115200;
    i++;
    /* 🔴 기본이 꺼짐이다(규격 §7.7). RTK 모듈은 초당 수십 줄을 낼 수
     *    있고 그것을 전부 텔레메트리에 실으면 ain·i2c 가 쓸 대역을
     *    잠식한다 — 진단할 때만 켠다(mk_telem.c 의 build_gnss_raw_record
     *    가 gnss.echo 를 보고 큐를 비울지 정한다). */
    s_items[i] = (MkCfgItem){
        .key = "gnss.echo", .group = "gnss", .vtype = MK_VT_BOOL,
        .label = "GNSS 원시 문장 에코",
        .note = "받은 줄을 그대로 텔레메트리로 올린다 — 진단용, 대역을 먹는다" };
    i++;
    /* 🔴 UM981 내장 IMU (설계 2026-08-21 §4.8). 켜면 초기화 명령에
     *    RAWIMUXA 가 추가되고 수신분이 클라우드 imu 레코드로 나간다.
     *    자기계는 없다 — 계약 v1.7.0 이 지자기 3필드를 선택으로 완화했다. */
    s_items[i] = (MkCfgItem){
        .key = "gnss.imu", .group = "gnss", .vtype = MK_VT_BOOL,
        .label = "IMU (UM981)",
        .note = "가속도·자이로를 젯슨으로 내보낸다" };
    i++;
    /* (IMU 온도는 설정 항목이 아니라 **필드 비트**다 — FIELDS 표의
     * "imu_temp". 사용자 확정 2026-08-22: "필드는 NDJSON 설정 탭에". ) */
    return i;
}

/* 호스트 링크 (USART3 → F103 → USB VCP). 규격 §4.2. */
static size_t add_link(size_t i)
{
    /* 🔴 이 항목만 다른 절차를 탄다 — 바꾸는 순간 이 대화가 끊긴다.
     *    보드는 응답을 옛 속도로 먼저 내보내고, 그 뒤 10초 안에 호스트가
     *    새 속도로 확인($BAUD,CONFIRM)을 보내지 않으면 스스로 되돌아간다.
     *    상태기계는 app/mk_linkbaud.c 에 있다.
     *
     * 🔴 `out` 이 아니다. TEST 를 벗어날 때 기본값으로 되돌리면 링크 속도가
     *    사용자 몰래 바뀌어 대화가 끊긴다 — `out` 의 뜻(모드 이탈 시 안전
     *    상태로 복귀)이 여기서는 정반대로 작용한다. 이 항목의 안전장치는
     *    제어 모드가 아니라 §4.2 의 확인 시한이다.
     *
     * 🔴 note 는 짧게 둔다. choices 가 여섯이라 mk_cfgwire_list 의
     *    320바이트 줄 상한에 여유가 적다(gnss.baud 와 같은 사정).
     *
     * 🔴 [2026-08-20] 짝이던 시뮬레이터 항목은 지웠다 — 이제 여기가
     *    유일한 출처다. */
    s_items[i] = (MkCfgItem){
        .key = "link.baud", .group = "link", .vtype = MK_VT_ENUM,
        .choices = LINK_BAUD_CHOICES, .n_choices = LINK_BAUD_CHOICE_COUNT,
        .unit = "bps", .label = "호스트 링크 속도",
        .note = "바꾸면 링크가 끊긴다 — 10초 안에 확인 못 하면 되돌아간다" };
    s_items[i].def.u = MK_LINKBAUD_DEFAULT;
    i++;
    return i;
}

/* LCD 화면 (J25, 데이터시트 §5.9). */
static size_t add_lcd(size_t i)
{
    /* 🔴 기본이 **꺼짐**이다. 화면 한 장이 320 x 480 x 3 = 460,800 바이트라
     *    SPI2 16 MHz 에서 약 230 ms 를 쓴다. 패널이 안 물린 보드에서 그것을
     *    매번 밀면 ADS1256 표본과 텔레메트리가 그만큼 뒤로 밀린다 —
     *    gnss.echo 를 기본으로 끈 것과 같은 이유다.
     *
     * 🔴 [2026-08-20] 짝이던 시뮬레이터 항목은 지웠다 — 이제 여기가
     *    유일한 출처다.
     *
     * 🔴 out 이 아니다. TEST 모드를 빠져나올 때 되돌릴 "출력" 이 아니라
     *    화면을 쓸 것인가라는 설정이다 — led.grb 와 같은 결. */
    s_items[i] = (MkCfgItem){
        .key = "lcd.enabled", .group = "lcd", .vtype = MK_VT_BOOL,
        .label = "화면 사용",
        .note = "J25 에 LCD 가 꽂혀 있을 때만 켠다 — 한 장 그리는 데 "
                "230 ms 를 쓴다" };
    i++;

    /* 🔴 갱신 주기. 사람이 읽는 화면이라 초당 2~4번이면 넘친다 —
     *    텔레메트리 주기(tx.period_ms, 기본 100 ms)를 따라갈 이유가 없다.
     *    250 ms 면 초당 4번이고, 값이 안 바뀌면 그때도 한 바이트도 안
     *    나간다(부분 갱신).
     *
     * 🔴 하한을 50 ms 로 둔다. 그보다 짧게 두면 값을 다시 읽는 일 자체가
     *    잦아져 슈퍼루프를 갉아먹는데, 사람 눈에는 아무 차이가 없다.
     *    상한 10초는 "거의 안 바뀌는 설비를 지켜볼 때" 를 위한 것이다. */
    s_items[i] = (MkCfgItem){
        .key = "lcd.period_ms", .group = "lcd", .vtype = MK_VT_U16,
        .min = 50, .max = 10000, .has_min = 1, .has_max = 1, .unit = "ms",
        .label = "화면 갱신 주기",
        .note = "값이 바뀐 칸만 다시 그린다 — 짧게 잡아도 화면이 조용하면 "
                "전송이 없다" };
    s_items[i].def.u = 250;
    i++;

    /* ── 회복 (2026-08-19, 실기기 증상) ──────────────────────────────
     *
     * 🔴 사용자: "LCD는 가끔 리셋을 해줘야겠다. 노이즈 타면 픽셀이 다
     *    깨지는데?" — 깨진 뒤 저절로 안 돌아온다. 부분 갱신은 값이 바뀐
     *    칸만 다시 그리므로 어긋난 그림이 그대로 남는다.
     *
     *    증상은 **무작위**다(사용자 확인). 24V 스위칭과 무관하고 케이블을
     *    만질 때도 아니다. 그래서 아래 셋을 전부 설정 항목으로 뺀다 —
     *    원인을 모르는 동안에는 사용자가 현장에서 돌려 볼 수 있어야 한다. */

    /* 🔴 SPI 클럭. **기본을 8 MHz 로 둔다** (사용자 결정 2026-08-19:
     *    "8mhz로 낮춰서 해보자").
     *
     *    남은 유력 후보가 "핀 헤더 + 점퍼선에 16 MHz 가 빠른 것" 이라,
     *    낮춰서 증상이 사라지면 그것 자체가 신호 무결성 문제라는 진단이
     *    된다. 지금은 갱신 속도보다 안정성이 값지다.
     *
     *    분주비로 낼 수 있는 값만 고를 수 있게 enum 이다. SPI2 커널 클럭은
     *    per_ck = hsi_ker_ck = **64 MHz** 이고(bsp/mk_lcd_io.c 의 spi_init
     *    이 직접 고른다 — sys_ck 가 아니라서 시스템 클럭을 HSE 로 옮겨도
     *    이 표는 안 바뀐다), HAL 의 분주비는 2 의 거듭제곱뿐이다:
     *
     *        64 / 4  = 16 MHz    쓰기 상한 20 MHz 안 (twc MIN 50 ns,
     *                            ILI9488.pdf p.332 §17.4.3)
     *        64 / 8  =  8 MHz    ← 기본
     *        64 / 16 =  4 MHz
     *        64 / 32 =  2 MHz
     *
     *    64 / 2 = 32 MHz 는 상한을 넘으므로 목록에 없다.
     *
     *    갱신 시간이 그대로 두 배가 된다: 전면 460,800 바이트가 16 MHz 에서
     *    약 230 ms, 8 MHz 에서 약 461 ms. 🔴 그래도 **한 바퀴에 한 행**이라
     *    수집에는 영향이 없다. 부분 갱신 한 칸(258 x 16 x 3 = 12,384 B)은
     *    8 MHz 에서 약 12 ms 라 사람 눈에는 그대로다. */
    s_items[i] = (MkCfgItem){
        .key = "lcd.spi_khz", .group = "lcd", .vtype = MK_VT_ENUM,
        .choices = LCD_SPI_KHZ_CHOICES,
        .n_choices = (uint8_t)(sizeof LCD_SPI_KHZ_CHOICES
                               / sizeof LCD_SPI_KHZ_CHOICES[0]),
        .unit = "kHz", .label = "화면 SPI 클럭",
        .note = "픽셀이 무작위로 깨지면 낮춘다 — 사라지면 신호 무결성 문제다" };
    s_items[i].def.u = 8000;
    i++;

    /* 🔴 되읽기 대조 주기. 패널에서 MADCTL·COLMOD 를 되읽어 우리가 넣은
     *    값과 맞춰 본다 (0Bh p.157 §5.2.7 · 0Ch p.159 §5.2.8). 다르면
     *    명령이 깨진 것이라 초기화부터 다시 하고, 같으면 GRAM 동기만
     *    어긋난 것이다 — 이 구분이 원인을 가리는 유일한 창구다.
     *
     *    0 = 안 함. MISO 가 안 물린 판에서도 첫 대조가 스스로 그것을
     *    알아채 검사를 끄지만(app/mk_lcd.c finish_verify), 사용자가 손으로
     *    끌 수 있어야 한다. */
    s_items[i] = (MkCfgItem){
        .key = "lcd.verify_ms", .group = "lcd", .vtype = MK_VT_U16,
        .min = 0, .max = 60000, .has_min = 1, .has_max = 1, .unit = "ms",
        .label = "화면 레지스터 대조 주기",
        .note = "0 이면 안 한다 — 값이 다르면 화면을 초기화부터 다시 세운다" };
    s_items[i].def.u = 5000;
    i++;

    /* 🔴 주기적 전면 다시 그리기. 되읽기가 못 잡는 종류의 어긋남(GRAM
     *    쓰기 포인터가 밀린 경우)을 덮는 마지막 그물이다. 바탕까지 다시
     *    칠하므로 칸 바깥에 밀려 찍힌 화소도 지워진다.
     *
     *    u32 다. 60초로는 모자랄 수 있고(증상이 드물면 길게), u16 은
     *    65.5초에서 끝난다. 기본 60초는 8 MHz 에서 약 461 ms 를 쓰므로
     *    듀티가 0.8% 다 — 한 바퀴에 한 행이라 수집에는 영향이 없다.
     *
     *    0 = 안 함. */
    s_items[i] = (MkCfgItem){
        .key = "lcd.redraw_ms", .group = "lcd", .vtype = MK_VT_U32,
        .min = 0, .max = 3600000, .has_min = 1, .has_max = 1, .unit = "ms",
        .label = "화면 전면 갱신 주기",
        .note = "값이 안 바뀌어도 통째로 다시 그린다 — 0 이면 안 한다" };
    s_items[i].def.u = 60000;
    i++;
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

    /* (수동 색 항목 led{21..24}.r/g/b 는 걷어냈다 — 파일 위 ITEM_COUNT
     *    주석. 색은 mk_statled 가 상태에서 계산한다.) */
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

        /* 🔴 송신 주기 — 수집과 분리 (HANDOFF_0831 결정 1, 사용자 결정
         *    2026-08-31 포트별 두 손잡이). 수집은 위 `주기`(센서 하한의
         *    지배를 받는다), 송신은 캐시 최신값을 이 주기마다 반복. */
        k = gen("i2c", jack, ".tx_period_ms", jack, "송신 주기");
        s_items[i] = (MkCfgItem){
            .key = s_keys[k], .group = "i2c", .vtype = MK_VT_U16,
            .min = 10, .max = 60000, .has_min = 1, .has_max = 1,
            .unit = "ms", .label = s_labels[k],
            .note = "짧게 잡으면 젯슨 링크가 포화할 수 있다 — 전송 탭 사용량 확인" };
        s_items[i].def.u = 200;
        i++;

        /* ain 의 .name 과 같다 — 사용자가 붙이는 이름. */
        k = gen("i2c", jack, ".name", jack, "이름");
        s_items[i] = (MkCfgItem){
            .key = s_keys[k], .group = "i2c", .vtype = MK_VT_STR,
            .max = 23, .has_max = 1, .label = s_labels[k],
            .note = "비우면 커넥터 번호로 보인다" };
        i++;

        /* (i2c 의 젯슨 발행 여부는 별도 스위치가 없다 — `사용`이 켜져 있고
         * 종류가 정해져 있으면 나간다. 타입은 종류가 정한다: 조도→light,
         * 적외→temp_road, 온습도→temp_air+humidity. 2026-08-22 사용자
         * 확정 — 클라우드 전용 항목을 두지 않는다.) */
    }
    return i;
}

/* 🔴 조용한 클램프 폐지 (HANDOFF_0831 검토 8, 사용자 합의 2026-08-31).
 *
 *    i2c 수집 주기의 하한은 펌웨어의 취향이 아니라 **센서(종류)를 따라다니는
 *    데이터시트 값**이다(AM2320 2000ms / MLX90614 250ms / BH1750 180ms —
 *    각 드라이버의 warmup_ms). 예전에는 mk_i2c.c 가 실효 주기 =
 *    max(설정, 하한) 으로 말없이 덮어서 화면의 설정값이 거짓말을 했다
 *    (설계 원칙 4 위반). 이제 입구가 말한다:
 *
 *      - `i2cN.period_ms` SET 이 현재 종류의 하한 미만이면 RANGE 로 거절
 *      - `i2cN.kind` SET 은 그 종류의 하한으로 주기를 **끌어올린다**
 *        (기본 200ms 인 채 온습도를 고르는 흔한 경우를 거절로 막으면
 *        사용자가 순서를 맞춰야 한다 — 정렬이 맞다. 다음 LIST 에 보인다)
 *
 *    mk_i2c.c 의 런타임 max() 는 플래시에 남은 옛 설정(입구를 안 거친 값)을
 *    위한 최후 방어로 남는다. */
static MkCfgResult table_policy(MkConfig *cfg, MkCfgItem *item,
                                const MkValue *v)
{
    const char *key = item->key;
    if (!(key[0] == 'i' && key[1] == '2' && key[2] == 'c')) {
        return MK_CFG_OK;
    }
    const char *dot = key + 3;
    while (*dot != '\0' && *dot != '.') { dot++; }
    if (*dot != '.') {
        return MK_CFG_OK;
    }
    size_t stem = (size_t)(dot - key);
    char sibling[MK_CFG_KEY_MAX + 1];
    if (stem + sizeof ".period_ms" > sizeof sibling) {
        return MK_CFG_OK;
    }
    memcpy(sibling, key, stem);

    if (strcmp(dot, ".period_ms") == 0) {
        memcpy(sibling + stem, ".kind", sizeof ".kind");
        MkCfgItem *kind = mk_cfg_find(cfg, sibling);
        uint32_t floor_ms =
            mk_i2c_min_period_ms(kind != NULL ? (uint8_t)kind->cur.u : 0u);
        if (v->u < floor_ms) {
            return MK_CFG_RANGE;
        }
    } else if (strcmp(dot, ".kind") == 0) {
        memcpy(sibling + stem, ".period_ms", sizeof ".period_ms");
        MkCfgItem *per = mk_cfg_find(cfg, sibling);
        uint32_t floor_ms = mk_i2c_min_period_ms((uint8_t)v->u);
        if (per != NULL && per->cur.u < floor_ms) {
            per->cur.u = floor_ms;
            cfg->dirty = 1u;
        }
    }
    return MK_CFG_OK;
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
    i = add_gnss(i);
    i = add_link(i);
    i = add_lcd(i);
    i = add_led(i);
    i = add_i2c(i);

    for (size_t n = 0; n < i; n++) {
        s_items[n].cur = s_items[n].def;
    }

    cfg->items = s_items;
    cfg->count = i;
    cfg->dirty = 0;
    cfg->policy = table_policy;
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
