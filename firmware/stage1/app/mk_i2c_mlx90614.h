/* MLX90614 적외선 온도 센서 — HAL 비의존.
 *
 * 🔴 근거: docs/datasheet/MLX90614.pdf (Doc server Rev 012, Datasheet
 *    Rev 021 - 02-Jun-2026, 58쪽 — pymupdf 로 텍스트 추출해 확인했다).
 *
 *      주소   0x5A (기본, p.20 Figure 6 "default SA = 0x5A")
 *             🔴 웹 요약본에 0x5B 라고 나오는 것이 있는데 틀렸다 — 이
 *             문서 원문(그리고 Table 5, p.8 "Slave address SA ... 5A hex")
 *             을 믿는다.
 *      명령   RAM 0x07 = Tobj1(대상 온도) — p.31 "the result is available
 *             in RAM address 0x07 as TO1"
 *      읽기   명령 1바이트를 쓰고 repeated start 로 3바이트
 *             (LSB, MSB, PEC) 를 읽는다 (p.20 Figure 6)
 *      PEC    CRC-8, 다항식 X8+X2+X1+1(=0x07), 초기값 0, 바이트마다
 *             MSB 먼저 (p.18 "4.1.4.3.1 SMBus Protocol"). 범위는
 *             [addr<<1|W, cmd, addr<<1|R, LSB, MSB] 5바이트 — Figure 6
 *             예제(SA=0x5A, cmd=0x07, result=0x3AD2, PEC=0x30)로 검증했다.
 *      환산   raw / 50 = 켈빈, 켈빈 - 273.15 = 섭씨 (raw × 0.02 − 273.15)
 *             (p.30 "4.1.8.2 Object temperature – To")
 *      오류   bit15(0x8000)가 1이면 오류 플래그(active high) — p.19
 *             "The MSb read from RAM is an error flag", p.30 예제 6
 *             "0x8XXX → xxx.xx˚C (flag error)"
 *
 * 🔴 start() 가 없다. 전원 인가 직후 기본 동작 모드가 이미 SMBus 연속
 *    측정이다(p.10 NOTE 2 "power-up factory default is SMBus") — 모드를
 *    바꾸는 명령이 필요 없다. warmup_ms 는 POR 직후 RAM 값이 유효해지는
 *    시간(Tvalid = 250ms typ, p.8 Table 5 "Output valid (results in RAM)
 *    ... After POR")을 그대로 쓴다 — 이 포트가 켜지는 순간 센서가 막
 *    전원이 들어왔을 수도 있다는 가정으로 안전 여유를 둔다.
 */
#ifndef MK_I2C_MLX90614_H
#define MK_I2C_MLX90614_H

#include "mk_i2c.h"

extern const MkI2cDriver MK_I2C_MLX90614;

/* raw(RAM 0x07 이 돌려준 16bit, 부호 없이 그대로)를 섭씨로. 시험이 이
 * 함수를 통해 환산 상수를 빌려 쓴다. 🔴 오류 플래그(bit15)는 여기서
 * 걸러내지 않는다 — 호출부(read())가 값을 넘기기 전에 먼저 본다. */
float mk_mlx90614_temp_c(uint16_t raw);

/* PEC(CRC-8, 다항식 0x07) 을 계산한다. addr7 은 7비트 슬레이브 주소.
 * 시험이 데이터시트 예제(Figure 6, PEC=0x30)로 이 함수를 직접 검증한다. */
uint8_t mk_mlx90614_pec(uint8_t addr7, uint8_t cmd, uint8_t lsb, uint8_t msb);

#endif /* MK_I2C_MLX90614_H */
