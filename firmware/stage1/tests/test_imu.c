/* mk_imu — UM981 RAWIMUX 파서 시험. 예시 문장은 매뉴얼 원문이다
 * (docs/datasheet/UM981_Auto_Commands_R1.0.pdf p.20 §2.3.5). */
#include <stdio.h>
#include <string.h>
#include "../app/mk_imu.h"

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
    if (!(cond)) { printf("  FAIL %s\n", msg); failures++; }    \
    else         { printf("  ok   %s\n", msg); }                \
} while (0)

#define NEAR(a, b) ((a) > (b) - 0.001f && (a) < (b) + 0.001f)

#define MANUAL_LINE \
    "#RAWIMUXA,COM1,0,60.0,FINE,2261,366772.050,0,0,0;" \
    "00,64,2261,366772.050,0ac00000,16278,-70,172,-1044,-90,-200*bffb7522"

static void test_manual_example_parses(void)
{
    MkImuSample s;
    memset(&s, 0, sizeof s);
    CHECK(mk_imu_parse(MANUAL_LINE, sizeof MANUAL_LINE - 1u, &s) == 1,
          "매뉴얼 예시 문장이 파싱된다");
    /* zacc=16278 -> az = 16278*2/32767 = 0.9936 g — 정지 상태의 중력 */
    CHECK(NEAR(s.az, 0.9936f), "Z 가속도 = 카운트 x 2/32767 (거의 1 g)");
    /* 문장의 둘째 값은 -Y = -70 -> ay = +70*2/32767 = +0.00427 */
    CHECK(s.ay > 0.0f && NEAR(s.ay, 0.00427f), "-Y 필드의 부호를 복원한다");
    CHECK(NEAR(s.ax, 0.0105f), "X 가속도");
    /* zgyro=-1044 -> gz = -1044*250/32767 = -7.965 dps */
    CHECK(NEAR(s.gz, -7.965f), "Z 자이로 = 카운트 x 250/32767");
    CHECK(NEAR(s.gy, 0.6867f), "-Y 자이로도 부호 복원");
    CHECK(NEAR(s.gx, -1.5259f), "X 자이로");
}

static void test_feed_assembles_and_counts(void)
{
    MkImu m;
    mk_imu_init(&m);
    const char *wire = MANUAL_LINE "\r\n";
    for (const char *p = wire; *p; p++) {
        mk_imu_feed(&m, (uint8_t)*p, 1234);
    }
    MkImuSample s;
    CHECK(mk_imu_last(&m, &s) == 1, "조립기를 거쳐 표본이 나온다");
    CHECK(s.t_ms == 1234, "t 는 수신 시각");
    CHECK(s.seq == 1u, "표본마다 seq 가 오른다");

    /* NMEA('$') 줄이 섞여 들어와도 무시된다 — 같은 바이트 흐름을 mk_gnss
     * 와 나눠 먹는 구조라, 남의 문장에 흔들리면 안 된다. */
    const char *nmea = "$GNGGA,120000.00,,,,,0,00,,,M,,M,,*55\r\n";
    for (const char *p = nmea; *p; p++) {
        mk_imu_feed(&m, (uint8_t)*p, 2000);
    }
    CHECK(mk_imu_last(&m, &s) == 1 && s.seq == 1u, "NMEA 는 표본을 안 만든다");
}

static void test_garbage_is_counted_not_crashed(void)
{
    MkImu m;
    mk_imu_init(&m);
    const char *bad = "#RAWIMUXA,COM1;00,64,broken*00\r\n";
    for (const char *p = bad; *p; p++) {
        mk_imu_feed(&m, (uint8_t)*p, 1);
    }
    MkImuSample s;
    CHECK(mk_imu_last(&m, &s) == 0, "깨진 줄은 표본이 안 된다");
    CHECK(m.parse_fail_count == 1u, "깨진 줄을 센다");
}

int main(void)
{
    test_manual_example_parses();
    test_feed_assembles_and_counts();
    test_garbage_is_counted_not_crashed();

    if (failures) { printf("%d failure(s)\n", failures); return 1; }
    printf("all ok\n");
    return 0;
}
