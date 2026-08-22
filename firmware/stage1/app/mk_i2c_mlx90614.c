#include "mk_i2c_mlx90614.h"

/* 🔴 근거는 전부 docs/datasheet/MLX90614.pdf 다 (파일 위쪽 주석 참고). */
#define MLX_ADDR_DEFAULT   0x5Au   /* p.20 Figure 6 "default SA = 0x5A" */
#define MLX_CMD_TOBJ1      0x07u  /* p.31 "RAM address 0x07 as TO1" */
#define MLX_CMD_TA         0x06u  /* p.31 "RAM address 0x06 as TA" — 주변 온도 */
/* 🔴 250 이다. Tvalid(POR 뒤 RAM 값이 유효해지는 시간) typ 250ms — p.8
 *    Table 5 "Output valid (results in RAM) ... After POR". 우리가 전원
 *    시퀀싱을 통제하지 않으므로 포트가 켜지는 순간이 POR 직후일 수도
 *    있다는 가정으로 안전하게 기다린다. */
#define MLX_WARMUP_MS      250u
/* 🔴 CRC-8 다항식(p.18 "X8+X2+X1+1") = x^8+x^2+x+1 = 0x07. */
#define MLX_PEC_POLY       0x07u

float mk_mlx90614_temp_c(uint16_t raw)
{
    /* p.30 "4.1.8.2": 십진값 / 50 = 켈빈, 켈빈 - 273.15 = 섭씨.
     * 1/50 = 0.02 다. */
    return (float)raw * 0.02f - 273.15f;
}

uint8_t mk_mlx90614_pec(uint8_t addr7, uint8_t cmd, uint8_t lsb, uint8_t msb)
{
    /* p.18: "The PEC calculation includes all bits except the START,
     * REPEATED START, STOP, ACK, and NACK bits. The PEC is a CRC-8 with
     * polynomial X8+X2+X1+1. The Most Significant Bit of every Byte is
     * transferred first." 범위는 5바이트: SA+W, Command, SA+R, LSB, MSB
     * (Figure 6 — SA=0x5A, cmd=0x07, result=0x3AD2, PEC=0x30 으로 검증). */
    uint8_t data[5];
    data[0] = (uint8_t)(addr7 << 1);            /* SA+W (R/W bit = 0) */
    data[1] = cmd;
    data[2] = (uint8_t)((addr7 << 1) | 1u);     /* SA+R (R/W bit = 1) */
    data[3] = lsb;
    data[4] = msb;

    uint8_t crc = 0u;
    for (int i = 0; i < 5; i++) {
        crc = (uint8_t)(crc ^ data[i]);
        for (int b = 0; b < 8; b++) {
            crc = (uint8_t)((crc & 0x80u) != 0u
                            ? (uint8_t)((uint8_t)(crc << 1) ^ MLX_PEC_POLY)
                            : (uint8_t)(crc << 1));
        }
    }
    return crc;
}

/* RAM 워드 하나(온도)를 PEC 검증까지 해서 섭씨로. 성공 0. */
static int mlx_read_temp(const MkI2cIo *io, uint8_t bus, uint8_t addr,
                         uint8_t cmd, float *out_c)
{
    uint8_t rx[3] = {0u, 0u, 0u};   /* LSB, MSB, PEC (p.20 Figure 6) */
    int rc = io->xfer(io->ctx, bus, addr, &cmd, 1u, rx, sizeof rx);
    if (rc != 0) {
        return rc;
    }

    /* 🔴 PEC 를 반드시 본다 — 이 프로젝트가 "그럴듯하게 틀린 값" 을 여러
     *    번 밟았고(ADS1256 t6, 전류 환산 등), PEC 는 그것을 잡는 유일한
     *    장치다. 어긋나면 값을 절대 내보내지 않는다. */
    uint8_t want_pec = mk_mlx90614_pec(addr, cmd, rx[0], rx[1]);
    if (rx[2] != want_pec) {
        return -2;                  /* 데이터 오류 */
    }

    /* 🔴 LSB 먼저다 (BH1750 은 MSB 먼저) — 순서가 뒤집혀도 값은 나오므로
     *    눈으로는 못 가린다. */
    uint16_t raw = (uint16_t)(((uint16_t)rx[1] << 8) | (uint16_t)rx[0]);
    /* 🔴 bit15 는 오류 플래그(active high) — p.19, p.30 예제 6
     *    "0x8XXX → xxx.xx˚C (flag error)". */
    if ((raw & 0x8000u) != 0u) {
        return -2;
    }

    *out_c = mk_mlx90614_temp_c(raw);
    return 0;
}

static int mlx_read(const MkI2cIo *io, uint8_t bus, uint8_t addr,
                    MkI2cValue out[MK_I2C_VALUES_MAX], int *n_out)
{
    float tobj;
    int rc = mlx_read_temp(io, bus, addr, MLX_CMD_TOBJ1, &tobj);
    if (rc != 0) {
        *n_out = 0;
        return rc;
    }

    out[0].quantity = "temp_object";      /* 규격 §7.5.1 */
    out[0].value = tobj;
    *n_out = 1;

    /* 🔴 주변 온도(Ta)도 함께 (젯슨 링크 선택 필드 temp_ambient,
     *    2026-08-22). 대상 온도가 성공했는데 Ta 만 실패하면 대상 온도는
     *    그대로 내보낸다 — 부가 정보 실패가 본 값을 죽이면 안 된다.
     *    xfer 가 한 번 늘어 이 드라이버의 최악 블로킹이 AM2320 과 같은
     *    2회(60 ms)가 된다 — bsp/mk_i2c_io.c 머리말의 셈과 같은 부류. */
    float ta;
    if (mlx_read_temp(io, bus, addr, MLX_CMD_TA, &ta) == 0) {
        out[1].quantity = "temp_ambient";
        out[1].value = ta;
        *n_out = 2;
    }
    return 0;
}

const MkI2cDriver MK_I2C_MLX90614 = {
    .kind = (uint8_t)MK_I2C_KIND_IR_TEMP,
    .default_addr = MLX_ADDR_DEFAULT,
    .warmup_ms = MLX_WARMUP_MS,
    .start = NULL,        /* 전원 인가 직후 기본이 이미 연속 측정(p.10) */
    .read = mlx_read,
};
