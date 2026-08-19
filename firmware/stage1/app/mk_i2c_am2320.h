/* AM2320 온습도 센서 — HAL 비의존.
 *
 * 🔴 근거: docs/datasheet/AM2320.pdf (27쪽 — pymupdf 로 텍스트 추출해
 *    확인했다).
 *
 *      주소     0xB8(8bit 쓰기 주소) = 0x5C(7bit) — p.10 §8.2
 *               "AM2320 sensor I2C address ... is 0xB8"
 *      🔴 평소 잠들어 있다 — 통신마다 깨워야 하고, 통신이 끝나면 다시
 *         잔다(p.10 §8.2.1). 최소 읽기 간격 2초("minimum interval of
 *         two reads 2s") · 전체 통신 3초 제한("host communication from
 *         start to finish, for a maximum of 3s") — 같은 절.
 *      깨우기   START + (addr+W) + wait(>=800us, 최대 3ms) + STOP
 *               (p.17 §8.2.4 "Step one: Wake Sensor", Figure 15). NACK
 *               이 정상이다 — "the sensor does not respond to ACK, but
 *               the host must send back an ACK ... verify that the
 *               ninth SCL clock signal".
 *      읽기     START+(addr+W)+0x03(함수코드)+시작주소+레지스터수+STOP,
 *               그 뒤 host 는 최소 1.5ms 를 기다린 뒤
 *               START+(addr+R)+응답+STOP (p.17 §8.2.4 Step two·three,
 *               p.13 함수코드 0x03 형식)
 *      응답     0x03 + 개수(4) + 습도High·습도Low·온도High·온도Low + CRC
 *               (p.13~14 Table 8: 0x00=습도High·0x01=습도Low·
 *               0x02=온도High·0x03=온도Low)
 *      CRC      CRC16(모드버스와 같은 식), 초기 0xFFFF, 다항식 0xA001,
 *               저바이트 먼저 전송(p.15~16, C 예제 포함). 데이터시트
 *               예제(습도=0x01F4, 온도=0x00FA → CRC=0xA531, 전송
 *               0x31,0xA5)로 검증했다.
 *      환산     레지스터값 / 10 (p.13~14)
 *      🔴 음수 온도는 2의 보수가 아니라 부호+크기다 — p.13 "temperature
 *         highest bit(Bit15) is equal to 1 indicates a negative
 *         temperature ... temperature in addition to the most
 *         significant bit (Bit14~Bit0) indicates the temperature ...
 *         value". 습도는 부호 비트가 없다(0~100%RH, 항상 양수).
 *
 * 🔴 read() 안에서 깨운다 — start() 가 아니다. 통신이 끝나면 다시 자므로
 *    한 번만 깨워서는 안 되고 읽을 때마다 깨워야 한다(위 §8.2.1). 깨우기
 *    호출의 반환값은 무시하고 삼킨다 — NACK 이 정상 동작이기 때문이다.
 *    mk_i2c.h/mk_i2c_io.c 가 재정의한 "ntx==0&&nrx==0 = 주소만 두드린다"
 *    를 그대로 쓴다. 본 프레임(명령·응답)이 실패했을 때만 오류를
 *    돌려준다 — 그래야 배선이 멀쩡한 센서를 "없다" 고 잘못 보고하지
 *    않는다.
 */
#ifndef MK_I2C_AM2320_H
#define MK_I2C_AM2320_H

#include "mk_i2c.h"

extern const MkI2cDriver MK_I2C_AM2320;

/* CRC16(모드버스식). 시험이 데이터시트 예제(p.14)로 이 함수를 직접
 * 검증한다 — crc16([0x03,0x04,0x01,0xF4,0x00,0xFA]) == 0xA531. */
uint16_t mk_am2320_crc16(const uint8_t *data, size_t n);

/* 습도 레지스터값(0x00·0x01) → %RH. 부호 없이 그대로 / 10. */
float mk_am2320_humidity(uint16_t raw);

/* 온도 레지스터값(0x02·0x03) → 섭씨. bit15 가 부호, bit14~0 이 크기다
 * (2의 보수가 아니다 — p.13). */
float mk_am2320_temp_c(uint16_t raw);

#endif /* MK_I2C_AM2320_H */
