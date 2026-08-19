# 호스트 네이티브 빌드. 크로스 툴체인도 보드도 필요 없다.
CC ?= gcc
CFLAGS = -std=c11 -Wall -Wextra -Werror -O1 -g

TESTS = test_framing test_json test_hostlink test_config test_cfgwire test_crc test_sol test_cfgtable test_i2c test_lcd

all: $(TESTS)
	./test_framing
	./test_json
	./test_hostlink
	./test_config
	./test_cfgwire
	./test_crc
	./test_sol
	./test_cfgtable
	./test_i2c
	./test_lcd

test_framing: test_framing.c ../app/mk_framing.c
	$(CC) $(CFLAGS) -o $@ $^

test_json: test_json.c ../app/mk_json.c
	$(CC) $(CFLAGS) -o $@ $^

test_hostlink: test_hostlink.c ../app/mk_hostlink.c ../app/mk_framing.c ../app/mk_json.c ../app/mk_config.c ../app/mk_cfgwire.c
	$(CC) $(CFLAGS) -o $@ $^

test_config: test_config.c ../app/mk_config.c
	$(CC) $(CFLAGS) -o $@ $^

test_cfgwire: test_cfgwire.c ../app/mk_cfgwire.c ../app/mk_config.c ../app/mk_json.c
	$(CC) $(CFLAGS) -o $@ $^

test_crc: test_crc.c ../app/mk_crc.c
	$(CC) $(CFLAGS) -o $@ $^

test_sol: test_sol.c ../app/mk_solctl.c ../app/mk_cfgtable.c ../app/mk_cfgwire.c ../app/mk_config.c ../app/mk_json.c
	$(CC) $(CFLAGS) -o $@ $^

test_cfgtable: test_cfgtable.c ../app/mk_cfgtable.c ../app/mk_cfgwire.c ../app/mk_config.c ../app/mk_json.c ../app/mk_i2c.c ../app/mk_i2c_drivers.c ../app/mk_i2c_bh1750.c ../app/mk_i2c_am2320.c ../app/mk_i2c_mlx90614.c
	$(CC) $(CFLAGS) -o $@ $^

test_i2c: test_i2c.c ../app/mk_i2c.c ../app/mk_i2c_drivers.c ../app/mk_i2c_bh1750.c ../app/mk_i2c_am2320.c ../app/mk_i2c_mlx90614.c ../app/mk_cfgtable.c ../app/mk_cfgwire.c ../app/mk_config.c ../app/mk_json.c ../app/mk_ws2812.c ../app/mk_solctl.c
	$(CC) $(CFLAGS) -o $@ $^

test_lcd: test_lcd.c ../app/mk_lcd.c ../app/mk_cfgtable.c ../app/mk_cfgwire.c ../app/mk_config.c ../app/mk_json.c ../app/mk_i2c.c ../app/mk_i2c_drivers.c ../app/mk_i2c_bh1750.c ../app/mk_i2c_am2320.c ../app/mk_i2c_mlx90614.c ../app/mk_ws2812.c ../app/mk_solctl.c ../app/mk_queue.c
	$(CC) $(CFLAGS) -o $@ $^

clean:
	rm -f $(TESTS) $(addsuffix .exe,$(TESTS))
.PHONY: all clean
