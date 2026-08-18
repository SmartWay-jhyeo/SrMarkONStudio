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

typedef struct {
    MkI2cXfer xfer;
    void     *ctx;
} MkI2cIo;

/* ---- 드라이버 ------------------------------------------------------------ */

typedef struct {
    const char *quantity;        /* 규격 §7.5.1 어휘 */
    float       value;
} MkI2cValue;

/* 🔴 값이 둘인 종류는 온습도뿐이다 (규격 §7.5.1). 그래서 2 다. */
#define MK_I2C_VALUES_MAX  2

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
    MK_I2C_FAULT                 /* 지원 안 하는 종류 · 시작 실패 */
} MkI2cState;

typedef struct {
    unsigned    connector_id;
    const char *quantity;
    float       value;
    int         have_value;      /* 0 이면 JSON 에 null 을 넣는다 */
    uint16_t    status;
    int64_t     t_ms;
} MkI2cOut;

/* 내보낼 것을 담아 두는 자리. 한 바퀴에 포트 하나만 읽으므로 2 면 넉넉하다. */
#define MK_I2C_OUT_MAX  MK_I2C_VALUES_MAX

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
} MkI2c;

void mk_i2c_init(MkI2c *i, const MkI2cIo *io);

/* 한 바퀴에 포트 **하나**만 나아간다. 매 루프 불러도 된다. */
void mk_i2c_tick(MkI2c *i, MkConfig *cfg, int64_t now_ms);

/* 내보낼 것이 있으면 1 을 돌려주고 out 을 채운다. 없으면 0. */
int mk_i2c_take(MkI2c *i, MkI2cOut *out);

#endif /* MK_I2C_H */
