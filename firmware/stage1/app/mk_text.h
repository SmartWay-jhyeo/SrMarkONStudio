/* 고정 버퍼 문자열 조립 — libc 없이. HAL 비의존.
 *
 * 🔴 `sprintf` 가 없다. 이 저장소는 손으로 만든다 — 이유는 mk_json.h 상단에
 *    있다(newlib-nano 의 `%f` 가 플래시 10,456 바이트, 그리고 더 중요한 것:
 *    libc 의 출력은 이 개발 호스트에서 검증할 수단이 없다).
 *
 * 🔴 이 파일은 **화면용**이다. mk_json 은 전선으로 나가는 JSON 을 만들고,
 *    여기는 사람이 읽는 칸에 들어갈 글자를 만든다. 둘을 합치지 않은 이유는
 *    넘쳤을 때의 처리가 정반대이기 때문이다:
 *
 *      mk_json  — 넘치면 **줄 전체를 버린다.** 잘린 JSON 을 보내면 호스트가
 *                 레코드를 통째로 버리는데 무엇이 잘렸는지 아무 데도 안 남는다.
 *      mk_text  — 넘치면 **자릿수를 줄여서라도 담고**, 그래도 안 되면 넘쳤다고
 *                 말한다("OVF"). 화면은 칸이 고정이라 잘라 내면 "1234" 가
 *                 "123" 으로 보이고 사람이 그것을 값으로 읽는다.
 *
 * 🔴 다만 **십진수의 모양은 mk_json 과 같아야 한다.** 같은 값이 화면과
 *    호스트에서 다르게 보이면 어느 쪽이 맞는지 정할 방법이 없다. 반올림
 *    규칙(0 에서 먼 쪽), 반올림 결과가 0 이면 부호를 안 붙이는 것까지
 *    맞춘다 — tests/test_screen.c 가 두 출력을 직접 대조한다.
 */
#ifndef MK_TEXT_H
#define MK_TEXT_H

#include <stddef.h>
#include <stdint.h>

typedef struct {
    char  *buf;
    size_t cap;     /* NUL 을 포함한 크기 */
    size_t len;
    int    ok;      /* 0 이 되면 이후 호출은 전부 무시된다 */
} MkText;

/* 🔴 항상 NUL 로 끝난다 — 중간에 넘쳐도 그렇다. 그리기 쪽이 문자열
 *    길이를 세므로, 끝이 없으면 그 자리에서 메모리를 넘어 읽는다. */
void   mk_text_begin(MkText *t, char *buf, size_t cap);
void   mk_text_putc(MkText *t, char c);
void   mk_text_puts(MkText *t, const char *s);
void   mk_text_u32(MkText *t, uint32_t v);
int    mk_text_ok(const MkText *t);
size_t mk_text_len(const MkText *t);

/* 실수를 **칸에 맞게** 담는다.
 *
 *   1. `digits` 자릿수로 시도한다.
 *   2. 안 들어가면 자릿수를 하나씩 줄인다 (소수점까지 없앤다).
 *   3. 정수부만으로도 안 들어가면 `"OVF"` — 🔴 **자르지 않는다.**
 *      자르면 -12345 가 -1234 로 보이고 사람이 그것을 값으로 읽는다.
 *   4. 유한하지 않은 값(NaN·±Inf)은 `"---"`. 계산 실패를 숫자로
 *      위장하지 않는다 (mk_json 이 `null` 을 내는 것과 같은 판단).
 *
 * 반환: 쓴 길이(NUL 제외). `cap` 이 4 보다 작으면 아무것도 못 쓰고 0.
 */
size_t mk_text_f32_fit(char *out, size_t cap, float v, int digits);

#endif /* MK_TEXT_H */
