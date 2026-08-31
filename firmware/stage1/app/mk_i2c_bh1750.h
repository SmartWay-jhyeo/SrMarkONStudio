/* BH1750FVI 조도 센서 — HAL 비의존.
 *
 * 🔴 근거: BH1750 데이터시트(확보본, docs/datasheet/BH1750FVI.pdf,
 *    ROHM Technical Note Rev.C, 2010.04). 참고 구현은
 *    LaneControlSystemQ2/STM32Code/App/Drivers/bh1750.{c,h} 에 있으나
 *    **두 군데가 다르다** — 전원을 먼저 켜야 하고(p.4), 첫 측정 대기는
 *    120ms(typ) 이 아니라 180ms(max) 다(p.7). 참고 구현을 따르지 않는다.
 *
 *      주소   0x23 (ADDR 핀 Low) · 0x5C (High)          (p.10)
 *      0x10   연속 고해상도 모드, 1 lx, 변환 typ 120ms   (p.5)
 *      읽기   2바이트 MSB 먼저, lux = raw / 1.2          (p.10)
 *
 * 🔴 레지스터 주소가 없다. 명령은 1바이트를 그냥 쓰고, 읽기는 명령 없이
 *    2바이트를 받는다 — MLX90614 처럼 repeated start 를 쓰지 않는다.
 */
#ifndef MK_I2C_BH1750_H
#define MK_I2C_BH1750_H

#include "mk_i2c.h"

extern const MkI2cDriver MK_I2C_BH1750;

/* 원시값을 lx 로. 시험이 이 함수를 통해 상수를 빌려 쓴다. */
float mk_bh1750_lux(uint16_t raw);

#endif /* MK_I2C_BH1750_H */
