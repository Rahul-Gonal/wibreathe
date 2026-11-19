#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "driver/i2c.h"

#ifdef __cplusplus
extern "C" {
#endif

#define SSD1306_WIDTH  128
#define SSD1306_HEIGHT 64

esp_err_t ssd1306_init(i2c_port_t port, int sda_pin, int scl_pin, uint8_t i2c_addr);
void ssd1306_clear(void);
void ssd1306_update(void);
void ssd1306_draw_pixel(int x, int y, bool on);
void ssd1306_draw_string(int x, int y, const char *str);
void ssd1306_draw_large_digit_string(int x, int y, const char *str);
void ssd1306_draw_rect(int x, int y, int w, int h, bool fill);
void ssd1306_draw_progress_bar(int x, int y, int w, int h, int percent);

void ssd1306_show_splash(void);
void ssd1306_show_no_link(void);
void ssd1306_show_telemetry(const char *state, const char *bpm_str, int confidence);

#ifdef __cplusplus
}
#endif
