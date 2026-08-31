/* 종류 → 드라이버. 새 칩은 여기 한 줄과 파일 하나가 전부다. */
#ifndef MK_I2C_DRIVERS_H
#define MK_I2C_DRIVERS_H

#include "mk_i2c.h"

/* 없으면 NULL — 부르는 쪽이 status=3 으로 말한다. */
const MkI2cDriver *mk_i2c_driver_for(uint8_t kind);

/* 종류의 수집 주기 하한(ms) — 드라이버 warmup_ms(데이터시트 값)를 그대로
 * 돌려준다. 종류 없음·미지는 0 (하한 없음). 설정 입구의 하한 검사가 쓴다
 * (mk_cfgtable.c 의 policy, HANDOFF_0831 검토 8). */
uint32_t mk_i2c_min_period_ms(uint8_t kind);

#endif /* MK_I2C_DRIVERS_H */
