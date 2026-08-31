# newlib-nano 의 printf 비용 측정 (2026-08-13)

**왜 쟀나.** 계획서가 "newlib-nano 는 `%f`·`%lld` 를 지원하지 않는다"고
단정하고 있었다. 근거 없는 단정은 §5 위반이므로 실제로 링크해 확인했다.

## 환경

```
arm-none-eabi-gcc 14.3.1
  C:\ST\STM32CubeIDE_2.1.1\STM32CubeIDE\plugins\
  com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.14.3.rel1.win32_
  1.0.100.202602081740\tools\bin\

-mcpu=cortex-m7 -mthumb -mfpu=fpv5-d16 -mfloat-abi=hard -O2
--specs=nano.specs --specs=nosys.specs
```

시험 코드는 `snprintf` 로 `%lld`, `%llu`, `%.4f` 를 각각 한 번씩 부른다.

## 결과

| 빌드 | text | data | bss | 합계 |
|---|---:|---:|---:|---:|
| `nano.specs` 만 | 5,068 | 104 | 496 | 5,668 |
| `nano.specs` + `-u _printf_float` | 15,524 | 468 | 500 | 16,492 |

**`-u _printf_float` 는 플래시 10,456 바이트를 더 쓴다.** [실증]

참고로 상위 저장소의 참고 펌웨어(`h723_sensor_read`)는 이미
`--specs=nano.specs ... -u _printf_float` 를 쓰고 있다.

## 확인하지 못한 것

**`%lld` 가 nano 에서 올바른 값을 내는지 확인하지 못했다.** [미확인]

두 빌드 모두 **링크는 성공한다.** 그러나 링크 성공은 출력이 옳다는 증거가
아니다 — newlib-nano 의 `vfprintf` 는 `_WANT_IO_LONG_LONG` 이 꺼져 있으면
`ll` 길이 지정자를 처리하지 않고 엉뚱한 값을 낸다. 이 호스트에는 ARM 코드를
실행할 수단(보드 외 QEMU 등)이 없어 실제 출력을 볼 수 없다.

보드에 굽고 확인하는 방법은 있으나, 그러자고 굽는 것은 지금 단계에서
과하다.

## 이 측정이 뒷받침하는 판단

`mk_json` 은 정수·실수 출력을 **직접 짠다.** 근거는 두 가지다.

1. **10 KB 를 아낀다.** 위 실측치다.
2. **더 중요한 것 — 시험할 수 있다.** 직접 짠 코드는 호스트에서
   `host/core/framing.py` 와 같은 방식으로 대조할 수 있다. libc 의 `%lld`
   에 맡기면, 이 호스트에서 검증할 수 없는 출력을 보드에 실어 보내게 된다.
   위 "확인하지 못한 것"이 정확히 그 상황이다.

`t` 필드가 `int64_t` epoch_ms 라 `%lld` 회피는 선택이 아니라 필수다
(규격 §7.1). 32비트로는 담기지 않는다.
