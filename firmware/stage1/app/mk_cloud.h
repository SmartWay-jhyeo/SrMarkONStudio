/* Cloud 스키마 직렬화 — J29(젯슨) 링크가 말하는 방언.
 *
 * 🔴 이 모듈이 만드는 줄은 규격 v3(protocol/specification.md)이 아니라
 *    **전작 Cloud 계약**(docs/데이터 스키마 명세서 v1.7.0)을 따른다.
 *    젯슨의 기존 소프트웨어(_lanemark_serial.py → MQTT → Cloud)가 이
 *    형식을 무수정으로 먹는다 — 그것이 이 모듈의 존재 이유다
 *    (설계: docs/superpowers/specs/2026-08-21-cloud-schema-jet-link-design.md,
 *     사용자 결정 2026-08-21).
 *
 * 🔴 [개정 2026-08-31, HANDOFF_0831 결정 2] **유일 직렬화기**다 — 같은
 *    줄이 젯슨(USART2)과 본선(USART3) 두 링크로 나간다(main.c 의
 *    emit_records: 젯슨 항상, USB 는 침묵 게이트 뒤). mk_telem(규격 v3
 *    본선 직렬화기)은 은퇴했고 이 모듈이 그 소비 골격(큐 드레인·주기)을
 *    상속했다.
 *
 * 🔴 "이 채널의 센서가 클라우드에서 뭐라 불리는가"는 설정이 정한다
 *    (ain{n}.cloud 열거). 센서를 딴 커넥터로 옮기면 설정만 옮기면 된다 —
 *    타입 이름이 채널을 따라간다. 전선의 계약 문자열(pressure_paint 등)은
 *    이 모듈 안의 표 하나가 정하고, 화면 이름표는 카탈로그(choice_labels)가
 *    따로 정한다.
 *
 * 🔴 발행 시점 = 수집 시점 (사용자 확정 2026-08-21 — "보드는 그냥 잘
 *    수집해서 잘 보내기만 하면 돼"). 채널의 last 표본이 갱신됐을 때만
 *    한 줄 낸다. 클라우드 쪽 페이싱·집계는 젯슨/Cloud 의 몫이다.
 */
#ifndef MK_CLOUD_H
#define MK_CLOUD_H

#include <stddef.h>
#include <stdint.h>

#include "mk_ads1256.h"
#include "mk_config.h"
#include "mk_gnss.h"
#include "mk_i2c.h"
#include "mk_imu.h"
#include "mk_solctl.h"
#include "mk_timeax.h"

typedef void (*MkCloudEmit)(void *ctx, const char *line, size_t len);

typedef struct MkCloud {
    MkConfig *cfg;
    MkAds    *ads;
    MkI2c    *i2c;                    /* NULL 이면 i2c 유래 타입 없음 */
    MkSolCtl *sol;                    /* NULL 이면 valve 없음(항상 0) */
    MkGnss   *gnss;                   /* NULL 이면 gnss 레코드 없음 */
    MkImu    *imu;                    /* NULL 이면 imu 레코드 없음 */
    MkTimeAx *timeax;                 /* NULL 이면 device_clock 고정 */
    const char *device_id;
    const char *fw_version;
    /* 줄 순번 — 유실 검출의 근거 (HANDOFF_0831 결정 2). 발행에 성공한
     * 줄마다 1 오른다. tx.seq(체크박스, 기본 켜짐)를 꺼도 계속 올라,
     * 다시 켰을 때 번호가 이어진다 — 끔 구간이 유실로 보이지 않게 하는
     * 것이 아니라, 켬 구간끼리의 연속성을 지키는 것이 목적이다. */
    uint32_t seq;
    /* ain 송신 골격 — 본선 mk_telem 에서 상속 (HANDOFF_0831 검토 1).
     * last_ms 는 tx.period_ms 게이트의 기준, ain_rr 은 큐 드레인
     * 라운드로빈의 시작 채널(기아 방지, 688ce00). */
    int64_t  last_ms;
    int      ain_rr;
    /* 마지막으로 발행한 표본의 획득 시각 — i2c 의 "새 표본" 판정. */
    int64_t  i2c_sent_t[MK_I2C_COUNT][MK_I2C_VALUES_MAX];
    uint8_t  i2c_primed[MK_I2C_COUNT][MK_I2C_VALUES_MAX];
    /* 포트별 마지막 발행 시각 — i2cN.tx_period_ms(수집·송신 분리, HANDOFF_0831
     * 결정 1)의 기준점. 슬롯이 아니라 포트 단위다: 온도·습도는 같은 수집에서
     * 나오므로 함께 반복된다. */
    int64_t  i2c_tx_last_ms[MK_I2C_COUNT];
    /* 마지막으로 발행한 din 확정 상태 — 채널별 **변화**에만 레코드를 낸다
     * (dinN.cloud 사용자 문자열이 type, HANDOFF_0831 검토 5). */
    uint8_t  din_sent_state[MK_SOL_COUNT];
    uint8_t  din_primed[MK_SOL_COUNT];
    uint32_t gnss_sent_count;         /* 마지막으로 발행한 fix_count */
    uint32_t imu_sent_seq;            /* 마지막으로 발행한 imu 표본 seq */
    /* device_capability — 부팅 후 1회 + 관련 설정이 바뀐 tick 에 재발행
     * (계약 §16, 설계 §4.6). 지문은 관련 설정값의 합성이다. */
    uint32_t cap_sent_fp;
    uint8_t  cap_primed;
} MkCloud;

void mk_cloud_init(MkCloud *c, MkConfig *cfg, MkAds *ads,
                   const char *device_id, const char *fw_version);

void mk_cloud_attach_i2c(MkCloud *c, MkI2c *i2c);
void mk_cloud_attach_sol(MkCloud *c, MkSolCtl *sol);
void mk_cloud_attach_gnss(MkCloud *c, MkGnss *gnss);
void mk_cloud_attach_imu(MkCloud *c, MkImu *imu);
void mk_cloud_attach_timeax(MkCloud *c, MkTimeAx *timeax);

/* 한 바퀴. 새로 발행한 줄 수를 돌려준다. emit 은 mk_telem 과 같은 계약 —
 * 줄(개행 포함)을 받아 링에 넣고 즉시 돌아온다. */
int mk_cloud_tick(MkCloud *c, int64_t now_ms, MkCloudEmit emit, void *ctx);

#endif /* MK_CLOUD_H */
