#include "mk_i2c_am2320.h"

/* 🔴 근거는 전부 docs/datasheet/AM2320.pdf 다 (파일 위쪽 주석 참고). */
#define AM_ADDR_DEFAULT     0x5Cu   /* p.10 0xB8(8bit 쓰기) = 0x5C(7bit) */
#define AM_FUNC_READ        0x03u   /* p.13 "Reading Register Data" */
#define AM_REG_START        0x00u   /* p.12 Table 8: 0x00 = 습도 High */
#define AM_REG_COUNT        0x04u   /* 습도 2바이트 + 온도 2바이트 */
/* 🔴 최소 읽기 간격 2초(p.10 §8.2.1 "minimum interval of two reads 2s").
 *    mk_i2c.c 는 실효 주기 = max(period_ms, warmup_ms) 로 이것을 강제한다
 *    — READY 상태의 규칙 그대로(다른 칩과 다른 매커니즘을 새로 안
 *    만든다). */
#define AM_WARMUP_MS         2000u

/* 🔴 깨우기 뒤 대기 — p.17 §8.2.4 Figure 15 "wait for sometime (waiting
 *    time of at least 800 μs, the maximum 3ms)". 800us 위, 3ms 아래로
 *    여유 있게 1000us 를 쓴다 — 전체 통신 예산 3초(같은 절)에 비하면
 *    무시할 만한 크기다. */
#define AM_WAKE_WAIT_US      1000u
/* 🔴 명령 전송 뒤 대기 — p.17 "the host is required to wait at least
 *    1.5ms, and then sends a read timing". */
#define AM_CMD_WAIT_US       1500u

float mk_am2320_humidity(uint16_t raw)
{
    /* p.13~14 예제: 0x01F4 = 500 → 50.0%RH (500/10). 부호 비트가 없다. */
    return (float)raw / 10.0f;
}

float mk_am2320_temp_c(uint16_t raw)
{
    /* 🔴 2의 보수가 아니다 — p.13 "temperature highest bit(Bit15) is
     *    equal to 1 indicates a negative temperature ... Bit14~Bit0
     *    indicates the temperature ... value". 부호+크기다. */
    float magnitude = (float)(raw & 0x7FFFu) / 10.0f;
    return (raw & 0x8000u) != 0u ? -magnitude : magnitude;
}

uint16_t mk_am2320_crc16(const uint8_t *data, size_t n)
{
    /* p.15~16 "CRC 코드 계산 방법" — 초기 0xFFFF, 다항식 0xA001(반사),
     * 데이터시트가 실은 C 예제(crc16())를 그대로 옮긴다. */
    uint16_t crc = 0xFFFFu;
    for (size_t i = 0; i < n; i++) {
        crc = (uint16_t)(crc ^ data[i]);
        for (int b = 0; b < 8; b++) {
            if ((crc & 0x0001u) != 0u) {
                crc = (uint16_t)((crc >> 1) ^ 0xA001u);
            } else {
                crc = (uint16_t)(crc >> 1);
            }
        }
    }
    return crc;
}

static int am_read(const MkI2cIo *io, uint8_t bus, uint8_t addr,
                   MkI2cValue out[MK_I2C_VALUES_MAX], int *n_out)
{
    *n_out = 0;

    /* 🔴 [1] 깨운다. NACK 이 정상이다(p.10 §8.2.1, p.17 §8.2.4) — 반환값을
     *    일부러 버린다. io->xfer 의 ntx==0&&nrx==0 은 "주소만 두드린다"
     *    는 계약이다(mk_i2c.h, bsp/mk_i2c_io.c). 여기서 반환값을 검사해
     *    실패로 돌리면 멀쩡한 센서를 매번 "없다" 고 잘못 보고하게 된다
     *    — 반드시 삼킨다. 본 프레임(아래)이 실패했을 때만 진짜 오류다. */
    (void)io->xfer(io->ctx, bus, addr, NULL, 0u, NULL, 0u);

    if (io->delay_us != NULL) {
        io->delay_us(io->ctx, AM_WAKE_WAIT_US);
    }

    /* 🔴 [2] 읽기 명령. START+(addr+W)+0x03+시작주소+개수+STOP
     *    (p.13, p.17). 이 전송은 깨우기와 달리 진짜 실패일 수 있다 —
     *    센서가 정말 응답하지 않는다는 뜻이다. */
    uint8_t cmd[3] = { (uint8_t)AM_FUNC_READ, (uint8_t)AM_REG_START,
                       (uint8_t)AM_REG_COUNT };
    int rc = io->xfer(io->ctx, bus, addr, cmd, sizeof cmd, NULL, 0u);
    if (rc != 0) {
        return rc;
    }

    if (io->delay_us != NULL) {
        io->delay_us(io->ctx, AM_CMD_WAIT_US);
    }

    /* 🔴 [3] 응답 8바이트: 함수코드, 개수, 습도High·Low, 온도High·Low,
     *    CRC저·CRC고 (p.13~14). */
    uint8_t rx[8] = {0u, 0u, 0u, 0u, 0u, 0u, 0u, 0u};
    rc = io->xfer(io->ctx, bus, addr, NULL, 0u, rx, sizeof rx);
    if (rc != 0) {
        return rc;
    }

    /* 🔴 응답이 우리가 보낸 명령과 맞물리는지 본다 — 함수코드가 요청한
     *    0x03 이 아니거나 개수가 요청한 4가 아니면 신뢰할 수 없다. */
    if (rx[0] != (uint8_t)AM_FUNC_READ || rx[1] != (uint8_t)AM_REG_COUNT) {
        return -2;
    }

    /* 🔴 CRC 검사를 반드시 넣는다 — MLX90614 의 PEC 와 같은 이유다.
     *    저바이트가 먼저 전송된다(p.15 "CRC ... low byte first, high
     *    byte ... after") — rx[6]=저, rx[7]=고. */
    uint16_t want_crc = mk_am2320_crc16(rx, 6u);
    uint16_t got_crc = (uint16_t)((uint16_t)rx[6] | ((uint16_t)rx[7] << 8));
    if (want_crc != got_crc) {
        return -2;                  /* 데이터 오류 */
    }

    uint16_t humidity_raw = (uint16_t)(((uint16_t)rx[2] << 8) | (uint16_t)rx[3]);
    uint16_t temp_raw     = (uint16_t)(((uint16_t)rx[4] << 8) | (uint16_t)rx[5]);

    /* 🔴 순서는 mk_i2c_kind_quantities(MK_I2C_KIND_HUMID) 표와 같다 —
     *    temp 먼저, humidity 다음(규격 §7.5.1, mk_i2c.c). 어긋나면
     *    host/gui/screen.py 가 값을 못 찾는다(검토 지적 C1과 같은 결). */
    out[0].quantity = "temp";
    out[0].value = mk_am2320_temp_c(temp_raw);
    out[1].quantity = "humidity";
    out[1].value = mk_am2320_humidity(humidity_raw);
    *n_out = 2;
    return 0;
}

const MkI2cDriver MK_I2C_AM2320 = {
    .kind = (uint8_t)MK_I2C_KIND_HUMID,
    .default_addr = AM_ADDR_DEFAULT,
    .warmup_ms = AM_WARMUP_MS,
    /* 🔴 start 가 없다 — 매번 read() 안에서 깨운다. 한 번만 깨우고 다시
     *    안 깨우면, 통신이 끝난 뒤 자동으로 잠든(p.10) 센서에 다음 주기
     *    명령이 그냥 씹힌다. */
    .start = NULL,
    .read = am_read,
};
