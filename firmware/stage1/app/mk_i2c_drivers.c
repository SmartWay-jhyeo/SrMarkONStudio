#include "mk_i2c_drivers.h"
#include "mk_i2c_bh1750.h"

/* 🔴 종류 하나에 드라이버 하나. 새 칩은 여기 한 줄과 파일 하나다. */
static const MkI2cDriver *const TABLE[] = {
    &MK_I2C_BH1750,
};

const MkI2cDriver *mk_i2c_driver_for(uint8_t kind)
{
    for (size_t k = 0; k < sizeof TABLE / sizeof TABLE[0]; k++) {
        if (TABLE[k]->kind == kind) {
            return TABLE[k];
        }
    }
    return NULL;                 /* 부르는 쪽이 status=3 으로 말한다 */
}
