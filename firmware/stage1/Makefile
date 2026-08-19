# MarkON Studio 펌웨어 1단계 — STM32H723ZGT6 빌드
#
# 사용: make CUBE=<STM32Cube_FW_H7 경로> GCC_BIN=<arm gcc bin 경로>
#   기본값은 이 PC 의 CubeIDE 번들 경로. 산출물: build/markon_stage1.{elf,bin,hex}
#
# 🔴 CubeMX 도 .ioc 도 쓰지 않는다. HAL 소스를 직접 나열해 컴파일한다.
#    참고 펌웨어(h723_sensor_read)가 그렇게 하고 있고, 그것이 이 보드에서
#    실증된 유일한 빌드 구조다.
#
# 🔴 -u _printf_float 를 쓰지 않는다. app/ 이 libc 의 printf 를 부르지
#    않기 때문이다. 실측으로 플래시 10,456 바이트를 아낀다
#    (docs/measurements/2026-08-13_newlib_nano_printf.md).

TARGET   = markon_stage1

# ── 경로 (환경에 맞게 override 가능) ──────────────────────────
CUBE    ?= C:/Users/xovud/STM32Cube/Repository/STM32Cube_FW_H7_V1.13.0
GCC_BIN ?= C:/ST/STM32CubeIDE_2.1.1/STM32CubeIDE/plugins/com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.14.3.rel1.win32_1.0.100.202602081740/tools/bin

CC  = $(GCC_BIN)/arm-none-eabi-gcc
CP  = $(GCC_BIN)/arm-none-eabi-objcopy
SZ  = $(GCC_BIN)/arm-none-eabi-size

HAL   = $(CUBE)/Drivers/STM32H7xx_HAL_Driver
CMSIS = $(CUBE)/Drivers/CMSIS
DEV   = $(CMSIS)/Device/ST/STM32H7xx

# ── 소스 ─────────────────────────────────────────────────────
# app/ 은 HAL 을 include 하지 않는다. 호스트에서 그대로 컴파일해 시험한
# 바로 그 파일들이다 — 보드용으로 따로 고친 판이 없다.
C_SRC = \
  main.c \
  app/mk_framing.c \
  app/mk_json.c \
  app/mk_hostlink.c \
  app/mk_config.c \
  app/mk_cfgwire.c \
  app/mk_crc.c \
  app/mk_cfgtable.c \
  app/mk_queue.c \
  app/mk_ads1256.c \
  app/mk_railctl.c \
  app/mk_telem.c \
  app/mk_ws2812.c \
  app/mk_solctl.c \
  app/mk_i2c.c \
  app/mk_i2c_drivers.c \
  app/mk_i2c_bh1750.c \
  app/mk_i2c_am2320.c \
  app/mk_i2c_mlx90614.c \
  app/mk_gnss.c \
  app/mk_gnssctl.c \
  app/mk_timeax.c \
  app/mk_lcd.c \
  app/mk_font5x7.c \
  app/mk_glyph.c \
  app/mk_text.c \
  app/mk_screen.c \
  bsp/mk_lcd_io.c \
  bsp/mk_gnss_io.c \
  bsp/mk_time.c \
  bsp/mk_critsec.c \
  bsp/mk_rails.c \
  bsp/mk_ws2812_io.c \
  bsp/mk_sol.c \
  bsp/mk_ads_io.c \
  bsp/mk_flash.c \
  bsp/mk_uart.c \
  bsp/mk_i2c_io.c \
  bsp/stm32h7xx_it.c \
  $(DEV)/Source/Templates/system_stm32h7xx.c \
  $(HAL)/Src/stm32h7xx_hal.c \
  $(HAL)/Src/stm32h7xx_hal_cortex.c \
  $(HAL)/Src/stm32h7xx_hal_rcc.c \
  $(HAL)/Src/stm32h7xx_hal_rcc_ex.c \
  $(HAL)/Src/stm32h7xx_hal_gpio.c \
  $(HAL)/Src/stm32h7xx_hal_pwr.c \
  $(HAL)/Src/stm32h7xx_hal_pwr_ex.c \
  $(HAL)/Src/stm32h7xx_hal_uart.c \
  $(HAL)/Src/stm32h7xx_hal_uart_ex.c \
  $(HAL)/Src/stm32h7xx_hal_i2c.c \
  $(HAL)/Src/stm32h7xx_hal_dma.c \
  $(HAL)/Src/stm32h7xx_hal_dma_ex.c \
  $(HAL)/Src/stm32h7xx_hal_spi.c \
  $(HAL)/Src/stm32h7xx_hal_tim.c \
  $(HAL)/Src/stm32h7xx_hal_tim_ex.c \
  $(HAL)/Src/stm32h7xx_hal_flash.c \
  $(HAL)/Src/stm32h7xx_hal_flash_ex.c

ASM_SRC  = $(DEV)/Source/Templates/gcc/startup_stm32h723xx.s

# ST 원본을 로컬 복사 후 전 섹션 32B 정렬.
# H7 플래시 워드가 256 bit 이고 같은 워드를 두 번 프로그래밍할 수 없다.
LDSCRIPT = STM32H723ZGTX_FLASH.ld

INC = \
  -I. -Iapp -Ibsp \
  -I$(CMSIS)/Include \
  -I$(DEV)/Include \
  -I$(HAL)/Inc

DEFS = -DSTM32H723xx -DUSE_HAL_DRIVER

# ── 플래그 ───────────────────────────────────────────────────
CPU = -mcpu=cortex-m7 -mthumb -mfpu=fpv5-d16 -mfloat-abi=hard

# -MMD -MP = 헤더 의존성 자동 생성. 없으면 .h 만 고쳤을 때 재컴파일이 안 돼
# 옛 오브젝트가 그대로 링크된다 (참고 펌웨어에서 2026-07-28 실제로 물린 함정).
CFLAGS  = $(CPU) $(DEFS) $(INC) -O2 -g3 -Wall -Wextra \
          -ffunction-sections -fdata-sections -std=gnu11 -MMD -MP
LDFLAGS = $(CPU) -T$(LDSCRIPT) --specs=nano.specs --specs=nosys.specs \
          -Wl,--gc-sections -Wl,-Map=build/$(TARGET).map

BUILD = build
OBJ   = $(addprefix $(BUILD)/,$(notdir $(C_SRC:.c=.o))) $(BUILD)/startup.o
VPATH = $(sort $(dir $(C_SRC)))

# ── 규칙 ─────────────────────────────────────────────────────
all: $(BUILD)/$(TARGET).elf $(BUILD)/$(TARGET).bin $(BUILD)/$(TARGET).hex
	@"$(SZ)" $(BUILD)/$(TARGET).elf
	@$(MAKE) --no-print-directory check-dma

# 🔴 DMA 버퍼가 DTCM 에 잡혔는지 링크 후에 본다.
#
#    DTCM 에 잡혀도 컴파일·링크가 전부 통과하고 경고가 없다. 실기기에서
#    전송만 조용히 안 된다(RM0468 p.140/p.106 — DMA1·DMA2 는 DTCM 에 닿지
#    못한다). 그래서 `all` 에 매달아 둔다 — 따로 부르게 두면 아무도 안 부른다.
check-dma: $(BUILD)/$(TARGET).map
	@python tools/check_dma_placement.py $(BUILD)/$(TARGET).map

.PHONY: all check-dma

$(BUILD)/%.o: %.c | $(BUILD)
	@"$(CC)" $(CFLAGS) -c $< -o $@

$(BUILD)/startup.o: $(ASM_SRC) | $(BUILD)
	@"$(CC)" $(CPU) -c $< -o $@

$(BUILD)/$(TARGET).elf: $(OBJ)
	@"$(CC)" $(OBJ) $(LDFLAGS) -o $@

$(BUILD)/$(TARGET).bin: $(BUILD)/$(TARGET).elf
	@"$(CP)" -O binary $< $@

$(BUILD)/$(TARGET).hex: $(BUILD)/$(TARGET).elf
	@"$(CP)" -O ihex $< $@

$(BUILD):
	@mkdir -p $(BUILD)

clean:
	@rm -rf $(BUILD)

-include $(OBJ:.o=.d)

.PHONY: all clean
