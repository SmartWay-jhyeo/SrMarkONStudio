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

#endif /* MK_I2C_H */
