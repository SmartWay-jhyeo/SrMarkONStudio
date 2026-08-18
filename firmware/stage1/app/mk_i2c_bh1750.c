#include "mk_i2c_bh1750.h"

/* 🔴 근거는 전부 docs/datasheet/BH1750FVI.pdf (ROHM Rev.C, 2010.04) 다. */
#define BH1750_ADDR_DEFAULT   0x23u   /* p.10 ADDR='L' → 0100011 */
#define BH1750_OP_POWER_ON    0x01u   /* p.5 "Waiting for measurement command" */
#define BH1750_OP_CONT_HRES   0x10u   /* p.5 연속 고해상도, typ 120ms */
/* 🔴 120 이 아니라 180 이다. 120 은 typ 이고, p.7 ex1) 는 첫 측정을
 *    "max. 180ms." 기다리라고 한다. 120 에 읽으면 변환이 안 끝난 값을
 *    읽는데 값은 나오므로 눈으로 못 가린다. */
#define BH1750_WARMUP_MS      180u
#define BH1750_LUX_DIVISOR    1.2f

float mk_bh1750_lux(uint16_t raw)
{
    return (float)raw / BH1750_LUX_DIVISOR;
}

static int bh1750_start(const MkI2cIo *io, uint8_t bus, uint8_t addr)
{
    /* 🔴 전원부터 켠다. "Initial state is Power Down mode after VCC and DVI
     *    supply"(p.4) 라 모드 명령만 보내면 칩이 받지 않을 수 있다.
     *
     * 🔴 두 번 나눠 보내는 것이 요건이다 — "not able to accept plural
     *    command without stop condition. Please insert SP every 1
     *    Opecode."(p.10) 우리 xfer 는 한 번 부를 때마다 STOP 을 낸다. */
    uint8_t on = BH1750_OP_POWER_ON;
    int rc = io->xfer(io->ctx, bus, addr, &on, 1u, NULL, 0u);
    if (rc != 0) {
        return rc;
    }
    uint8_t mode = BH1750_OP_CONT_HRES;
    return io->xfer(io->ctx, bus, addr, &mode, 1u, NULL, 0u);
}

static int bh1750_read(const MkI2cIo *io, uint8_t bus, uint8_t addr,
                       MkI2cValue out[MK_I2C_VALUES_MAX], int *n_out)
{
    uint8_t rx[2] = {0u, 0u};
    int rc = io->xfer(io->ctx, bus, addr, NULL, 0u, rx, sizeof rx);
    if (rc != 0) {
        *n_out = 0;
        return rc;
    }
    /* 🔴 MSB 먼저다. 뒤집어도 값은 나오므로 눈으로는 못 가린다. */
    uint16_t raw = (uint16_t)(((uint16_t)rx[0] << 8) | (uint16_t)rx[1]);
    out[0].quantity = "lux";     /* 규격 §7.5.1 어휘 */
    out[0].value = mk_bh1750_lux(raw);
    *n_out = 1;
    return 0;
}

const MkI2cDriver MK_I2C_BH1750 = {
    .kind = (uint8_t)MK_I2C_KIND_LUX,
    .default_addr = BH1750_ADDR_DEFAULT,
    .warmup_ms = BH1750_WARMUP_MS,
    .start = bh1750_start,
    .read = bh1750_read,
};
