/* I2C1 · I2C3 · I2C5 — 이 핀들을 만지는 유일한 파일.
 *
 *      I2C3  SCL=PA8  SDA=PC9    J10 · J11
 *      I2C5  SCL=PC11 SDA=PC10   J12 · J13
 *      I2C1  SCL=PB8  SDA=PB9    J14 · J15
 *
 * 🔴 PA8 은 GPIOA 다. 같은 포트에 sol(PA4~PA6, 디지털 입력)과 WS2812(PA7)가
 *    있다 — 핀별로만 초기화한다. OR 로 묶어 넣으면 디지털 입력이 못 읽히거나
 *    LED 체인이 죽는다.
 *
 * 🔴 풀업은 온보드 4.7 kΩ 이다(R25~R30). 내부 풀업을 켜지 않는다.
 */
#ifndef MK_I2C_IO_H
#define MK_I2C_IO_H

#include "../app/mk_i2c.h"

void mk_i2c_io_init(void);

/* mk_i2c 에 넘길 콜백. 0 / -1(응답 없음) / -2(버스 오류) */
int mk_i2c_io_xfer(void *ctx, uint8_t bus, uint8_t addr,
                   const uint8_t *tx, size_t ntx, uint8_t *rx, size_t nrx);

/* 🔴 [AM2320] mk_i2c 에 넘길 콜백. MkI2cIo.delay_us 자리에 그대로 꽂는다
 *    — app/mk_i2c_am2320.c 가 깨우기·명령 사이 대기에 쓴다. */
void mk_i2c_io_delay_us(void *ctx, uint32_t us);

#endif /* MK_I2C_IO_H */
