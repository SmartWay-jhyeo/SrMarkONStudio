/* 종류 → 드라이버. 새 칩은 여기 한 줄과 파일 하나가 전부다. */
#ifndef MK_I2C_DRIVERS_H
#define MK_I2C_DRIVERS_H

#include "mk_i2c.h"

/* 없으면 NULL — 부르는 쪽이 status=3 으로 말한다. */
const MkI2cDriver *mk_i2c_driver_for(uint8_t kind);

#endif /* MK_I2C_DRIVERS_H */
