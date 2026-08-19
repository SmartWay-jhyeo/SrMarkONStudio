/* I2C 센서 포트 J10~J15 — HAL 비의존.
 *
 * 🔴 짝 커넥터는 같은 버스다 (넷리스트 확인 2026-08-18):
 *
 *      I2C3  SCL=PA8(100)  SDA=PC9(99)    J10 · J11
 *      I2C5  SCL=PC11(112) SDA=PC10(111)  J12 · J13
 *      I2C1  SCL=PB8(139)  SDA=PB9(140)   J14 · J15
 *
 *    같은 주소를 짝에 함께 꽂으면 충돌한다. 펌웨어가 막을 수 없다 —
 *    주소는 사용자가 설정으로 넣는다.
 *
 * 🔴 개수와 버스 표를 여기 두는 이유는 mk_cfgtable 이 이것을 쓰기
 *    때문이다. 두 곳에 적으면 카탈로그가 말하는 버스와 실제로 두드리는
 *    버스가 갈린다 (MK_LED_COUNT·MK_SOL_COUNT 와 같은 규칙).
 */
#ifndef MK_I2C_H
#define MK_I2C_H

#include <stddef.h>
#include <stdint.h>

#include "mk_config.h"     /* MkConfig · mk_cfg_find — mk_i2c_tick 이 설정을 읽는다 */

#define MK_I2C_COUNT   6

/* 🔴 카탈로그의 choices 와 같은 값이어야 한다 (mk_cfgtable 의
 *    I2C_KIND_CHOICES, 시뮬레이터의 I2C_KINDS). 종류는 칩 모델이 아니라
 *    무엇을 재는가다 — 사용자 확정 2026-08-17. */
typedef enum {
    MK_I2C_KIND_NONE       = 0,
    MK_I2C_KIND_LUX        = 1,
    MK_I2C_KIND_HUMID      = 2,
    MK_I2C_KIND_IR_TEMP    = 3,
    MK_I2C_KIND_WATER_TEMP = 4
} MkI2cKind;

/* 포트 0~5 → 버스 번호 (1 · 3 · 5). 범위 밖이면 0. */
uint8_t  mk_i2c_bus_of(unsigned port);

/* 포트 0~5 → 커넥터 번호 (10~15). 범위 밖이면 0. */
unsigned mk_i2c_connector_of(unsigned port);

/* ---- 버스 ---------------------------------------------------------------- */

/* 한 번의 전송. ntx>0 && nrx>0 이면 repeated start 로 잇는다.
 *
 * 🔴 반환값이 곧 규격 §7.5 의 status 다:
 *      0 = OK · -1 = 응답 없음(NACK/타임아웃) · -2 = 버스 오류
 *    상태 판정을 이 한 곳으로 모아, 드라이버마다 다르게 세지 않는다. */
typedef int (*MkI2cXfer)(void *ctx, uint8_t bus, uint8_t addr,
                         const uint8_t *tx, size_t ntx,
                         uint8_t *rx, size_t nrx);

/* 🔴 [AM2320] `ntx==0 && nrx==0` 은 이제 "주소만 두드린다" 다 — HAL 의
 *    HAL_I2C_IsDeviceReady() 가 정확히 그것이다(시도 1회, 짧은 타임아웃).
 *    예전에는 "할 일이 없다"며 버스를 안 건드리고 조용히 0 을 돌려줬는데,
 *    그래서는 AM2320 의 깨우기(주소만 보내고 데이터는 안 보내는 전송,
 *    NACK 이 정상)를 표현할 수 없었다. 반환값 규칙(0/-1/-2)은 그대로다. */
typedef struct {
    MkI2cXfer xfer;
    void     *ctx;
    /* 🔴 [AM2320] 마이크로초 바쁜 대기. AM2320 은 깨우기 뒤(>=800us,
     *    데이터시트 p.17)와 명령 전송 뒤(>=1.5ms, 같은 쪽)에 실제로
     *    기다려야 하는 칩이다 — mk_ads1256.c 의 t6·t11 대기와 같은
     *    이유로, 이 층이 HAL 을 몰라도 되게 콜백으로 뺀다
     *    (MkAdsIo.delay_us 와 같은 모양).
     *
     *    필드를 맨 끝에 둔 이유: 기존 위치(positional) 초기화
     *    `{ xfer, ctx }` 가 이 필드를 안 채워도(C 가 나머지를 0 으로
     *    채운다) 그대로 컴파일된다 — BH1750·MLX90614 처럼 딜레이가
     *    필요 없는 드라이버의 호출부를 손대지 않아도 된다.
     *    NULL 이어도 된다 — 그러면 그 드라이버는 못 쓴다(호출하면
     *    죽는다). 실제 보드용은 bsp/mk_i2c_io.c 가 항상 채운다. */
    void (*delay_us)(void *ctx, uint32_t us);
} MkI2cIo;

/* ---- 드라이버 ------------------------------------------------------------ */

typedef struct {
    const char *quantity;        /* 규격 §7.5.1 어휘 */
    float       value;
} MkI2cValue;

/* 🔴 값이 둘인 종류는 온습도뿐이다 (규격 §7.5.1). 그래서 2 다. */
#define MK_I2C_VALUES_MAX  2

/* 🔴 종류 → 양 목록 (규격 §7.5.1 표). 드라이버 유무와 무관하게 항상 있다 —
 *    양은 종류가 정하지 드라이버가 정하지 않는다. 실패(status 1·2)·
 *    미지원(status 3) 레코드도 quantity 를 실어야 한다 — 없으면
 *    host/gui/screen.py 가 그 레코드를 통째로 버린다(`if not quantity:
 *    continue`), 화면은 마지막 값을 붙든 채로 멈추고 "지원 안 함" 도
 *    영영 안 뜬다 (검토 지적 C1, 2026-08-18).
 *
 *    out[MK_I2C_VALUES_MAX] 를 채우고 채운 개수를 돌려준다. 모르는
 *    종류(카탈로그에 없는 값 포함 MK_I2C_KIND_NONE)는 0.
 *
 *    시뮬레이터(tools/simulator/config_store.py 의 I2C_QUANTITIES)와 같은
 *    표여야 한다 — crosscheck_i2c_quantities.py 가 대조한다. */
int mk_i2c_kind_quantities(uint8_t kind, const char *out[MK_I2C_VALUES_MAX]);

typedef struct {
    uint8_t  kind;               /* MkI2cKind */
    uint8_t  default_addr;       /* 설정의 addr 이 0(미지정)일 때 */
    uint16_t warmup_ms;          /* 시작 명령 후 첫 값까지 */
    int (*start)(const MkI2cIo *io, uint8_t bus, uint8_t addr);
    int (*read )(const MkI2cIo *io, uint8_t bus, uint8_t addr,
                 MkI2cValue out[MK_I2C_VALUES_MAX], int *n_out);
} MkI2cDriver;

/* ---- 상태기계 ------------------------------------------------------------ */

typedef enum {
    MK_I2C_OFF = 0,
    MK_I2C_START,
    MK_I2C_WARMUP,
    MK_I2C_READY,
    /* 지원 안 하는 종류(드라이버 없음 — 절대 회복 안 됨, 종류를 바꿔야
     * 벗어난다) · 시작 실패(드라이버는 있는데 응답이 없음 — period_ms
     * 마다 재시도한다, C2). 둘 다 같은 상태를 쓰는 이유는 "버스에
     * 아무것도 없다" 는 사실이 같고 화면에 보이는 status(3 vs 1/2)만
     * 다르기 때문이다. */
    MK_I2C_FAULT
} MkI2cState;

typedef struct {
    unsigned    connector_id;
    const char *quantity;
    float       value;
    int         have_value;      /* 0 이면 JSON 에 null 을 넣는다 */
    uint16_t    status;
    int64_t     t_ms;
} MkI2cOut;

/* 🔴 내보낼 것을 담아 두는 자리 — 2 로는 모자란다 (검토 지적 I1).
 *
 *    READY 에서 실제로 버스를 두드리는 포트는 한 바퀴에 하나뿐이지만,
 *    FAULT(드라이버 없음 · status=3)는 버스를 안 건드려 순회를 안 멈춘다
 *    — 한 바퀴 안에서 **포트 여섯 전부**가 gate 를 동시에 통과할 수
 *    있다(부팅 직후 last_read_ms 가 다 0이라 그렇다). 종류마다 최대
 *    MK_I2C_VALUES_MAX(2, 온습도)줄을 내므로 최악은
 *    MK_I2C_COUNT × MK_I2C_VALUES_MAX 다.
 *
 *    mk_telem.c 의 MK_TELEM_MAX_LINES(16)보다 작아야 배출이 한 바퀴 안에
 *    끝난다는 그쪽 가정이 깨지지 않는다 — 지금 6×2=12 < 16 이라 괜찮지만,
 *    MK_I2C_COUNT 나 MK_I2C_VALUES_MAX 가 늘면 그 가정도 다시 본다. */
#define MK_I2C_OUT_MAX  (MK_I2C_COUNT * MK_I2C_VALUES_MAX)

typedef struct {
    MkI2cState state;
    uint8_t    kind;             /* 지금 물려 있는 종류 (바뀌면 되돌린다) */
    uint8_t    addr;             /* 지금 쓰는 주소 */
    int64_t    step_ms;          /* 마지막 상태 전이 시각 */
    int64_t    last_read_ms;
} MkI2cPort;

typedef struct MkI2c {
    MkI2cIo   io;
    MkI2cPort port[MK_I2C_COUNT];
    unsigned  next_port;         /* 라운드로빈 출발점 */

    MkI2cOut  out[MK_I2C_OUT_MAX];
    int       n_out;
    int       out_head;
    /* 🔴 mk_i2c_take() 로 못 비운 사이 push_out 이 자리가 없어 버린 개수
     *    (mk_queue.c 의 drops 와 같은 관례). 버려도 값이 섞이거나 다음
     *    레코드가 이전 것에 덮어써지지는 않는다 — 그냥 통째로 사라진다.
     *    올라간다고 해서 손을 쓰는 코드는 아직 없다(YAGNI) — $STAT 노출은
     *    이 Task 범위 밖이다. 세지 않으면 유실이 없었던 것으로 보인다. */
    uint32_t  dropped;
} MkI2c;

void mk_i2c_init(MkI2c *i, const MkI2cIo *io);

/* 한 바퀴에 포트 **하나**만 나아간다. 매 루프 불러도 된다.
 *
 * 🔴 out 버퍼는 MK_I2C_OUT_MAX 칸이다(위 정의 참고). 정상 경로(READY 에서
 *    값을 읽음)는 한 바퀴에 버스를 건드리는 포트가 하나뿐이고 그 포트가
 *    내는 값도 최대 2개(온습도)라 절대 안 넘친다. 하지만 **지원 안 하는
 *    종류(드라이버 없음)는 버스를 건드리지 않으므로 한 바퀴 안에서
 *    포트 여섯 전부가 동시에 status=3 을 낼 수 있다** — 그래서 이 함수를
 *    부른 직후에는 mk_i2c_take() 로 **반드시 완전히** 비워야 한다. 안
 *    비우고 다음 바퀴를 돌리면 새 레코드가 자리가 없어 조용히 버려지고
 *    dropped 만 올라간다. */
void mk_i2c_tick(MkI2c *i, MkConfig *cfg, int64_t now_ms);

/* 내보낼 것이 있으면 1 을 돌려주고 out 을 채운다. 없으면 0.
 *
 * 🔴 mk_i2c_tick() 을 부른 **매 바퀴 뒤** 0 이 나올 때까지 while 로
 *    반복해서 불러야 하는 계약이다. 다 비우지 않은 채 다음 tick 을 부르면
 *    이전 바퀴가 남겨 둔 자리 때문에 이번 바퀴의 레코드가 밀려 버려질 수
 *    있다 — dropped 로 셀 수 있다. */
int mk_i2c_take(MkI2c *i, MkI2cOut *out);

#endif /* MK_I2C_H */
