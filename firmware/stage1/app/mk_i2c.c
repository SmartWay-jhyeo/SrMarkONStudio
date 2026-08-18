#include "mk_i2c.h"

/* J10·J11 = I2C3 · J12·J13 = I2C5 · J14·J15 = I2C1 (넷리스트 확인) */
static const uint8_t BUS_OF[MK_I2C_COUNT] = { 3u, 3u, 5u, 5u, 1u, 1u };

uint8_t mk_i2c_bus_of(unsigned port)
{
    return port < MK_I2C_COUNT ? BUS_OF[port] : 0u;
}

unsigned mk_i2c_connector_of(unsigned port)
{
    return port < MK_I2C_COUNT ? 10u + port : 0u;
}
