/* MarkON NDJSON 조립 — HAL 비의존, libc 의 printf 비의존.
 *
 * 🔴 이 파일은 stm32h7xx_hal.h 를 include 하지 않는다. 호스트 컴파일러로
 *    그대로 컴파일해 Python 의 json.dumps 와 바이트 단위로 대조한다.
 *
 * 🔴 정수·실수 출력을 직접 짠다. snprintf 에 맡기지 않는 이유는 두 가지다.
 *
 *    1. newlib-nano 에서 %f 를 쓰려면 -u _printf_float 가 필요하고, 이
 *       툴체인에서 실측한 비용이 플래시 10,456 바이트다
 *       (docs/measurements/2026-08-13_newlib_nano_printf.md).
 *
 *    2. 더 중요한 것 — 시험할 수 있다. nano 의 %lld 가 올바른 값을 내는지는
 *       이 개발 호스트에서 확인할 방법이 없다(ARM 코드를 돌릴 수단이 없다).
 *       libc 에 맡기면 검증하지 못한 출력을 보드에 실어 보내게 된다.
 *       직접 짠 코드는 호스트에서 Python 과 대조된다.
 *
 *    규격 §7.1 의 `t` 는 int64 epoch_ms 라 32비트로 담기지 않는다.
 *    %lld 회피는 선택이 아니라 필수다.
 *
 * 규격: protocol/specification.md §7
 */
#ifndef MK_JSON_H
#define MK_JSON_H

#include <stddef.h>
#include <stdint.h>

typedef struct {
    char  *buf;
    size_t cap;
    size_t len;
    int    ok;      /* 0 이 되면 이후 호출은 전부 무시된다 */
    int    nfield;  /* 이미 쓴 필드 수 — 쉼표를 넣을지 판단한다 */
} MkJson;

/* 🔴 넘치면 잘라 담지 않는다. 한 번 넘치면 ok 가 0 이 되고 그 뒤 모든
 *    호출이 무시되며 mk_json_end 가 음수를 돌려준다.
 *
 *    잘린 JSON 을 보내면 호스트의 json.loads 가 실패해 레코드가 통째로
 *    버려지는데, 무엇이 잘렸는지는 아무 데도 남지 않는다. 보내지 않는
 *    편이 낫다 — 최소한 seq 가 건너뛴 것으로 유실이 드러난다. */
void mk_json_begin(MkJson *j, char *buf, size_t cap);

/* 문자열 값. `"` `\` 와 제어문자를 이스케이프한다.
 *
 * 🔴 이스케이프는 선택이 아니다. dev.id 같은 설정값에 `"` 와 `\` 가 실제로
 *    들어올 수 있다 — 호스트의 설정 검증(_ALLOWED_STR_CHARS)이 프로토콜
 *    구분자 `$,*` 만 막고 이 둘은 통과시킨다. 이스케이프하지 않으면 그
 *    값 하나가 NDJSON 한 줄을 깨뜨린다. */
void mk_json_str(MkJson *j, const char *key, const char *val);

void mk_json_i64(MkJson *j, const char *key, int64_t val);
void mk_json_u64(MkJson *j, const char *key, uint64_t val);
void mk_json_i32(MkJson *j, const char *key, int32_t val);
void mk_json_u32(MkJson *j, const char *key, uint32_t val);
void mk_json_bool(MkJson *j, const char *key, int val);

/* 정수 배열. 규격 §7.3 의 `choices` 가 이 형태다.
 *
 * 🔴 배열이 없으면 enum 항목을 화면에 그릴 수 없다. 호스트는 설정 항목을
 *    하드코딩하지 않으므로 허용값 목록도 보드가 알려 줘야 한다. */
void mk_json_u32_array(MkJson *j, const char *key,
                       const uint32_t *values, size_t count);

/* 고정 소수점 자릿수로 반올림한 십진수. digits 는 0~6.
 *
 * 🔴 유한하지 않거나(NaN/Inf) 자릿수를 곱했을 때 int64 를 넘는 값은
 *    `null` 로 낸다. JSON 에는 NaN 이 없다.
 *
 *    레코드 전체를 실패시키지 않는 이유는 채널 장애 격리다 — 센서 하나가
 *    이상한 값을 내도 나머지 필드는 살아야 한다. 호스트 쪽 loop_gauge 가
 *    이미 유한하지 않은 값을 "값 없음" 으로 표시하도록 되어 있다. */
void mk_json_f32(MkJson *j, const char *key, float val, int digits);

/* 중첩 객체와 배열.
 *
 * 규격 §7.4 의 `stat` 레코드가 이 둘을 쓴다.
 *
 *   "rails":{"v24":false,...}          객체
 *   "queues":[{"ch":0,...},...]        객체 배열
 *
 * 🔴 여는 함수와 닫는 함수가 짝이다. 짝이 맞지 않으면 JSON 이 깨지는데,
 *    `put` 이 경계만 보고 괄호는 세지 않으므로 **여기서는 못 잡는다.**
 *    호출 쪽이 지켜야 하고, 시험이 그것을 확인한다.
 */
void mk_json_object_begin(MkJson *j, const char *key);
void mk_json_object_end(MkJson *j);

void mk_json_array_begin(MkJson *j, const char *key);
/* 배열 안의 객체 하나를 연다. 키가 없다. */
void mk_json_array_object_begin(MkJson *j);
void mk_json_array_object_end(MkJson *j);
void mk_json_array_end(MkJson *j);

/* `}` 를 닫는다. 줄끝(`\n`)은 붙이지 않는다 — 전송 계층이 붙인다.
 * 반환: NUL 을 뺀 길이. 넘쳤으면 음수. */
int mk_json_end(MkJson *j);

#endif /* MK_JSON_H */
