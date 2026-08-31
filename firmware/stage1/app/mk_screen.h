/* 화면 내용 — 배치·글자 만들기·"무엇이 바뀌었나" 판정. HAL 비의존.
 *
 * 🔴 이 층이 하는 일은 셋뿐이다:
 *
 *      1. 어디에 무엇을 놓을지 (배치표)
 *      2. 지금 값이 어떤 글자가 되는지 (문자열)
 *      3. 그중 **무엇이 바뀌었는지** (부분 갱신의 근거)
 *
 *    실제 전송은 mk_lcd(상태기계) → bsp/mk_lcd_io(SPI2·DMA) 가 한다.
 *    그래서 화면에 무엇이 나오는지를 보드 없이 호스트에서 시험한다.
 *
 * 🔴 **새 큐를 만들지 않는다** (사용자 설계 2026-08-19). 화면은 마지막
 *    값만 보면 된다 — mk_ads_last() · mk_i2c_last() · mk_solctl_is_on() ·
 *    mk_timeax · mk_railctl 에서 그때그때 읽는다. 텔레메트리의
 *    hold-and-send 와 같은 생각이고, 큐를 하나 더 두면 화면 때문에
 *    표본이 두 벌 생겨 어느 쪽이 진짜인지 흐려진다.
 *
 * 🔴 **바뀐 칸만 그린다.** 전면 갱신은 460,800 바이트 = 약 230 ms 다.
 *    초당 4번이면 SPI2 를 92% 쓰고, 1단계에서 실기기로 확인한 성질
 *    (ain 간격 중앙값 100 ms, 큐 drops 0)을 그대로 잃는다. 한 칸은
 *    258 x 16 = 12,384 바이트(약 6 ms)이고, 그나마 값이 안 바뀌면
 *    **한 바이트도 안 나간다**.
 *
 *    사용자가 못박은 선 (2026-08-19): "뭘 하던지 센서 수집에는 방해가
 *    안된다면 뭐든지 해도 돼" / "센서 값을 늦게 보내도 돼. 무조건 수집을
 *    정상적으로 타임스탬프 찍어서 가지고 있어야해."
 *
 * 🔴 **센서가 없는 자리는 "없음"으로 그린다** (설계 원칙 3). 빈칸으로
 *    두면 "고장인가 안 꽂았나" 를 사람이 못 가린다.
 *
 * ── 화면 배치 (320 x 480, 세로) ──────────────────────────────────────
 *
 *      MARKON STUDIO                       제목
 *      ANALOG 4-20MA (J3-J9)               구역 이름표 (작은 글씨)
 *      J3    -0.33 bar
 *      J4    NONE
 *      ...                                 J9 까지 7줄
 *      I2C SENSORS (J10-J15)
 *      J10   NONE
 *      J12   24.15 C
 *      J13   24.1 C 56.1 %
 *      ...                                 J15 까지 6줄
 *      DIGITAL IN (OPTO)
 *      OPTO  J18 0  J19 0  J20 0
 *      SYSTEM - RAIL SHOWS COMMAND, NOT MEASUREMENT
 *      TIME  DEV CLOCK SAT 3
 *      RAIL  5V ON 15V -- 24V ON
 *
 * 🔴 값이 있는 것만 골라 그리지 않는다. 자리가 고정이면 사람이 **위치로**
 *    읽을 수 있고, 안 꽂힌 커넥터도 "여기에 J11 이 있다"는 것을 알 수
 *    있다. 목록이 값에 따라 늘었다 줄었다 하면 부분 갱신도 성립하지 않는다.
 */
#ifndef MK_SCREEN_H
#define MK_SCREEN_H

#include <stdint.h>

#include "mk_ads1256.h"
#include "mk_config.h"
#include "mk_font.h"
#include "mk_i2c.h"
#include "mk_lcd.h"
#include "mk_railctl.h"
#include "mk_solctl.h"
#include "mk_timeax.h"

/* 한 칸에 들어갈 수 있는 글자 수 + NUL. 가장 긴 칸은 구역 이름표(작은
 * 글씨, 52자)다. */
#define MK_SCREEN_TEXT_MAX  56u

/* ---- 칸 번호 ------------------------------------------------------------- */

enum {
    MK_SF_TITLE = 0,
    MK_SF_AIN_HEAD,
    MK_SF_AIN_NAME0,
    MK_SF_AIN_VALUE0 = MK_SF_AIN_NAME0 + MK_ADS_CHANNELS,
    MK_SF_I2C_HEAD   = MK_SF_AIN_VALUE0 + MK_ADS_CHANNELS,
    MK_SF_I2C_NAME0,
    MK_SF_I2C_VALUE0 = MK_SF_I2C_NAME0 + MK_I2C_COUNT,
    MK_SF_DIN_HEAD   = MK_SF_I2C_VALUE0 + MK_I2C_COUNT,
    MK_SF_DIN_NAME,
    MK_SF_DIN_VALUE,
    MK_SF_SYS_HEAD,
    MK_SF_TIME_NAME,
    MK_SF_TIME_VALUE,
    MK_SF_RAIL_NAME,
    MK_SF_RAIL_VALUE,
    MK_SCREEN_FIELD_COUNT
};

/* ---- 칸 ------------------------------------------------------------------ */

typedef struct {
    uint16_t   x, y, w, h;
    uint8_t    scale;       /* 글꼴 배율 */
    uint8_t    chars;       /* 이 폭에 들어가는 글자 수 (배치가 정한다) */
    MkLcdColor fg;
    char       text[MK_SCREEN_TEXT_MAX];   /* 지금 있어야 할 글자 */
    char       shown[MK_SCREEN_TEXT_MAX];  /* 화면에 실제로 그려진 글자 */
    MkLcdColor shown_fg;
    uint8_t    dirty;
} MkScreenField;

/* ---- 값 스냅샷 -----------------------------------------------------------
 *
 * 🔴 수집기에서 읽은 것을 **한 번에** 담는다. 글자를 만드는 쪽이 수집기
 *    자료구조를 직접 뒤지지 않게 하는 것이 요점이다 — 그래야 화면 내용을
 *    시험할 때 보드도 수집기도 필요 없다(시험이 이 구조체를 손으로 채운다).
 */
typedef struct {
    struct {
        uint8_t     enabled;    /* 사용자가 이 채널을 켜 두었나 */
        uint8_t     have;       /* 표본을 한 번이라도 받았나 */
        float       value;      /* 규격 §7.2.1 — (ma - zero) * scale */
        const char *unit;       /* 설정의 ainN.unit. 비었으면 안 붙인다 */
    } ain[MK_ADS_CHANNELS];

    struct {
        uint8_t used;           /* 종류가 "없음"이 아니다 */
        uint8_t n_slot;
        struct {
            const char *quantity;   /* 규격 §7.5.1 어휘. NULL 이면 빈 슬롯 */
            uint8_t     have;
            float       value;
            uint16_t    status;     /* 0 정상 · 1 무응답 · 2 데이터오류 · 3 미지원 */
        } slot[MK_I2C_VALUES_MAX];
    } i2c[MK_I2C_COUNT];

    uint8_t din[MK_SOL_COUNT];      /* 이미 반전된 값 — 1 = 켜짐 */
    uint8_t rail[MK_RAIL_COUNT];    /* 🔴 **명령** 상태다. 실측이 아니다 */
    uint8_t sats;
    uint8_t time_grade;             /* MkTimeAxGrade */
} MkScreenData;

/* ---- 값의 출처 ------------------------------------------------------------
 *
 * 없는 것은 NULL 로 둔다 — 그 자리는 "없음"으로 그려진다(설계 원칙 3).
 * `cfg` 가 NULL 이면 `mk_screen_tick()` 이 값을 **스스로 읽지 않는다**
 * (시험이 `mk_screen_apply()` 로 직접 먹이는 경로).
 */
typedef struct {
    MkConfig        *cfg;
    const MkAds     *ads;
    const MkI2c     *i2c;
    const MkSolCtl  *sol;
    const MkTimeAx  *timeax;
    const MkRailCtl *rails;
} MkScreenSources;

typedef struct MkScreen {
    const MkFont   *font;
    MkScreenSources src;
    MkScreenField   f[MK_SCREEN_FIELD_COUNT];

    /* 지금 mk_lcd 에 맡긴 칸. 🔴 글자를 따로 복사해 두는 이유: 그리는
     * 도중에 `apply()` 가 그 칸의 text 를 바꾸면 위쪽 절반은 옛 글자,
     * 아래 절반은 새 글자로 그려진다. */
    int        paint_idx;
    char       paint_text[MK_SCREEN_TEXT_MAX];
    MkLcdColor paint_fg;

    unsigned next_scan;         /* 라운드로빈 출발점 — 한 칸이 굶지 않게 */
    int64_t  last_apply_ms;
    int      primed;
    uint32_t lcd_epoch;         /* 패널이 다시 초기화됐는지 (mk_lcd_epoch) */
} MkScreen;

/* 배치를 세우고 고정 글자(제목·구역 이름표·커넥터 이름)를 채운다.
 * `src` 는 NULL 이어도 된다. */
void mk_screen_init(MkScreen *s, const MkScreenSources *src);

/* 수집기에서 지금 값을 읽어 스냅샷으로 담는다. NULL 인 출처는 건너뛴다. */
void mk_screen_collect(const MkScreenSources *src, MkScreenData *out);

/* 스냅샷으로 글자를 다시 만들고, **달라진 칸만** dirty 로 표시한다.
 * 반환: 이번에 새로 달라진 칸 수. */
int mk_screen_apply(MkScreen *s, const MkScreenData *d);

/* 슈퍼루프가 매 바퀴 부른다.
 *
 *   - `lcd.period_ms` 가 지났으면 값을 다시 읽어 `apply()` 한다.
 *   - 화면이 놀고 있으면 dirty 한 칸을 **하나** 맡긴다.
 *
 * 🔴 한 바퀴에 맡기는 것은 하나뿐이고, 맡긴 뒤 완료를 기다리지 않는다.
 *
 * 반환: 이번 바퀴에 값을 다시 읽었으면 1 (갱신 주기 시험이 이것을 센다). */
int mk_screen_tick(MkScreen *s, MkLcd *lcd, int64_t now_ms);

/* 화면에 그린 것을 전부 잊는다 — 다음 기회에 전부 다시 그린다. */
void mk_screen_invalidate(MkScreen *s);

/* ---- 조회 (배치 시험·진단) ------------------------------------------------ */

unsigned mk_screen_field_count(void);
const MkScreenField *mk_screen_field(const MkScreen *s, unsigned i);

unsigned mk_screen_ain_name_field(unsigned ch);
unsigned mk_screen_ain_value_field(unsigned ch);
unsigned mk_screen_i2c_name_field(unsigned port);
unsigned mk_screen_i2c_value_field(unsigned port);
unsigned mk_screen_din_value_field(void);
unsigned mk_screen_time_value_field(void);
unsigned mk_screen_rail_value_field(void);
unsigned mk_screen_system_head_field(void);

#endif /* MK_SCREEN_H */
