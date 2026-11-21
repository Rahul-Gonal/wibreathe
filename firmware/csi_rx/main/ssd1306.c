/*
 * Self-contained SSD1306 / SH1106 128x64 I2C OLED Driver for ESP-IDF v5.x
 *
 * UNVERIFIED on physical hardware: verified against SSD1306 and SH1106 datasheet specs.
 * Supports SSD1306 (standard 0-column offset) and SH1106 (2-column offset).
 * If the display is disconnected or absent, all operations return immediately without blocking.
 */

#include "ssd1306.h"
#include <string.h>
#include "esp_log.h"

#define TAG "ssd1306"

#if defined(CONFIG_WIBREATHE_OLED_SH1106)
#define OLED_COL_OFFSET 2
#define OLED_IS_SH1106 1
#else
#define OLED_COL_OFFSET 0
#define OLED_IS_SH1106 0
#endif

static i2c_port_t s_i2c_port = I2C_NUM_0;
static uint8_t s_i2c_addr = 0x3C;
static uint8_t s_buffer[SSD1306_WIDTH * SSD1306_HEIGHT / 8];
static bool s_is_present = false;

/* Standard 5x7 ASCII font table (characters 32 to 126) */
static const uint8_t font5x7[][5] = {
    {0x00, 0x00, 0x00, 0x00, 0x00}, /* Space */
    {0x00, 0x00, 0x5F, 0x00, 0x00}, /* ! */
    {0x00, 0x07, 0x00, 0x07, 0x00}, /* " */
    {0x14, 0x7F, 0x14, 0x7F, 0x14}, /* # */
    {0x24, 0x2A, 0x7F, 0x2A, 0x12}, /* $ */
    {0x23, 0x13, 0x08, 0x64, 0x62}, /* % */
    {0x36, 0x49, 0x55, 0x22, 0x50}, /* & */
    {0x00, 0x05, 0x03, 0x00, 0x00}, /* ' */
    {0x00, 0x1C, 0x22, 0x41, 0x00}, /* ( */
    {0x00, 0x41, 0x22, 0x1C, 0x00}, /* ) */
    {0x14, 0x08, 0x3E, 0x08, 0x14}, /* * */
    {0x08, 0x08, 0x3E, 0x08, 0x08}, /* + */
    {0x00, 0x50, 0x30, 0x00, 0x00}, /* , */
    {0x08, 0x08, 0x08, 0x08, 0x08}, /* - */
    {0x00, 0x60, 0x60, 0x00, 0x00}, /* . */
    {0x20, 0x10, 0x08, 0x04, 0x02}, /* / */
    {0x3E, 0x51, 0x49, 0x45, 0x3E}, /* 0 */
    {0x00, 0x42, 0x7F, 0x40, 0x00}, /* 1 */
    {0x42, 0x61, 0x51, 0x49, 0x46}, /* 2 */
    {0x21, 0x41, 0x45, 0x4B, 0x31}, /* 3 */
    {0x18, 0x14, 0x12, 0x7F, 0x10}, /* 4 */
    {0x27, 0x45, 0x45, 0x45, 0x39}, /* 5 */
    {0x3C, 0x4A, 0x49, 0x49, 0x30}, /* 6 */
    {0x01, 0x71, 0x09, 0x05, 0x03}, /* 7 */
    {0x36, 0x49, 0x49, 0x49, 0x36}, /* 8 */
    {0x06, 0x49, 0x49, 0x29, 0x1E}, /* 9 */
    {0x00, 0x36, 0x36, 0x00, 0x00}, /* : */
    {0x00, 0x56, 0x36, 0x00, 0x00}, /* ; */
    {0x08, 0x14, 0x22, 0x41, 0x00}, /* < */
    {0x14, 0x14, 0x14, 0x14, 0x14}, /* = */
    {0x00, 0x41, 0x22, 0x14, 0x08}, /* > */
    {0x02, 0x01, 0x51, 0x09, 0x06}, /* ? */
    {0x32, 0x49, 0x79, 0x41, 0x3E}, /* @ */
    {0x7E, 0x11, 0x11, 0x11, 0x7E}, /* A */
    {0x7F, 0x49, 0x49, 0x49, 0x36}, /* B */
    {0x3E, 0x41, 0x41, 0x41, 0x22}, /* C */
    {0x7F, 0x41, 0x41, 0x22, 0x1C}, /* D */
    {0x7F, 0x49, 0x49, 0x49, 0x41}, /* E */
    {0x7F, 0x09, 0x09, 0x09, 0x01}, /* F */
    {0x3E, 0x41, 0x49, 0x49, 0x7A}, /* G */
    {0x7F, 0x08, 0x08, 0x08, 0x7F}, /* H */
    {0x00, 0x41, 0x7F, 0x41, 0x00}, /* I */
    {0x20, 0x40, 0x41, 0x3F, 0x01}, /* J */
    {0x7F, 0x08, 0x14, 0x22, 0x41}, /* K */
    {0x7F, 0x40, 0x40, 0x40, 0x40}, /* L */
    {0x7F, 0x02, 0x0C, 0x02, 0x7F}, /* M */
    {0x7F, 0x04, 0x08, 0x10, 0x7F}, /* N */
    {0x3E, 0x41, 0x41, 0x41, 0x3E}, /* O */
    {0x7F, 0x09, 0x09, 0x09, 0x06}, /* P */
    {0x3E, 0x41, 0x51, 0x21, 0x5E}, /* Q */
    {0x7F, 0x09, 0x19, 0x29, 0x46}, /* R */
    {0x46, 0x49, 0x49, 0x49, 0x31}, /* S */
    {0x01, 0x01, 0x7F, 0x01, 0x01}, /* T */
    {0x3F, 0x40, 0x40, 0x40, 0x3F}, /* U */
    {0x1F, 0x20, 0x40, 0x20, 0x1F}, /* V */
    {0x3F, 0x40, 0x38, 0x40, 0x3F}, /* W */
    {0x63, 0x14, 0x08, 0x14, 0x63}, /* X */
    {0x07, 0x08, 0x70, 0x08, 0x07}, /* Y */
    {0x61, 0x51, 0x49, 0x45, 0x43}, /* Z */
};

static esp_err_t write_cmd(uint8_t cmd)
{
    if (!s_is_present) return ESP_FAIL;
    i2c_cmd_handle_t link = i2c_cmd_link_create();
    i2c_master_start(link);
    i2c_master_write_byte(link, (s_i2c_addr << 1) | I2C_MASTER_WRITE, true);
    i2c_master_write_byte(link, 0x00, true); /* Command stream */
    i2c_master_write_byte(link, cmd, true);
    i2c_master_stop(link);
    esp_err_t ret = i2c_master_cmd_begin(s_i2c_port, link, pdMS_TO_TICKS(10));
    i2c_cmd_link_delete(link);
    if (ret != ESP_OK) {
        s_is_present = false; /* Mark disconnected on bus failure */
    }
    return ret;
}

#ifndef CONFIG_WIBREATHE_OLED_I2C_FREQ_HZ
#define CONFIG_WIBREATHE_OLED_I2C_FREQ_HZ 100000
#endif

esp_err_t ssd1306_init(i2c_port_t port, int sda_pin, int scl_pin, uint8_t i2c_addr)
{
    s_i2c_port = port;
    s_i2c_addr = i2c_addr;

    i2c_config_t conf = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = sda_pin,
        .scl_io_num = scl_pin,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = CONFIG_WIBREATHE_OLED_I2C_FREQ_HZ,
    };
    esp_err_t ret = i2c_param_config(s_i2c_port, &conf);
    if (ret != ESP_OK) {
        s_is_present = false;
        return ret;
    }
    ret = i2c_driver_install(s_i2c_port, conf.mode, 0, 0, 0);
    if (ret != ESP_OK) {
        s_is_present = false;
        return ret;
    }

    /* Ping device address */
    i2c_cmd_handle_t cmd = i2c_cmd_link_create();
    i2c_master_start(cmd);
    i2c_master_write_byte(cmd, (s_i2c_addr << 1) | I2C_MASTER_WRITE, true);
    i2c_master_stop(cmd);
    ret = i2c_master_cmd_begin(s_i2c_port, cmd, pdMS_TO_TICKS(50));
    i2c_cmd_link_delete(cmd);

    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "OLED not detected at I2C address 0x%02X (SDA=%d, SCL=%d). Continuing without display.",
                 s_i2c_addr, sda_pin, scl_pin);
        s_is_present = false;
        return ESP_ERR_NOT_FOUND;
    }

    s_is_present = true;
    ESP_LOGI(TAG, "OLED detected successfully at 0x%02X. Controller: %s (col offset=%d)",
             s_i2c_addr, OLED_IS_SH1106 ? "SH1106" : "SSD1306", OLED_COL_OFFSET);

    /* Initialization command sequence */
    write_cmd(0xAE); /* Display off */
    write_cmd(0xD5); write_cmd(0x80); /* Set clock divide */
    write_cmd(0xA8); write_cmd(0x3F); /* Multiplex 64 */
    write_cmd(0xD3); write_cmd(0x00); /* Display offset 0 */
    write_cmd(0x40); /* Start line 0 */
    write_cmd(0x8D); write_cmd(0x14); /* Charge pump on */
    write_cmd(0xA1); /* Segment remap */
    write_cmd(0xC8); /* COM output scan reversed */
    write_cmd(0xDA); write_cmd(0x12); /* COM pins config */
    write_cmd(0x81); write_cmd(0xCF); /* Contrast */
    write_cmd(0xD9); write_cmd(0xF1); /* Precharge */
    write_cmd(0xDB); write_cmd(0x40); /* VCOM detect */
    write_cmd(0xA4); /* Output follows RAM */
    write_cmd(0xA6); /* Normal display (non-inverted) */
    write_cmd(0xAF); /* Display ON */

    ssd1306_clear();
    ssd1306_update();
    return ESP_OK;
}

void ssd1306_clear(void)
{
    memset(s_buffer, 0x00, sizeof(s_buffer));
}

void ssd1306_update(void)
{
    if (!s_is_present) return;

    /*
     * Page-by-page update with column offset.
     * Compatible with both SSD1306 and SH1106 (which has 2-column RAM offset).
     */
    for (uint8_t page = 0; page < 8; page++) {
        uint8_t col_lo = (OLED_COL_OFFSET & 0x0F);
        uint8_t col_hi = 0x10 | ((OLED_COL_OFFSET >> 4) & 0x0F);

        write_cmd(0xB0 | page); /* Set page address */
        write_cmd(col_lo);      /* Set lower column address */
        write_cmd(col_hi);      /* Set higher column address */

        i2c_cmd_handle_t link = i2c_cmd_link_create();
        i2c_master_start(link);
        i2c_master_write_byte(link, (s_i2c_addr << 1) | I2C_MASTER_WRITE, true);
        i2c_master_write_byte(link, 0x40, true); /* Data stream */
        i2c_master_write(link, &s_buffer[page * SSD1306_WIDTH], SSD1306_WIDTH, true);
        i2c_master_stop(link);
        esp_err_t ret = i2c_master_cmd_begin(s_i2c_port, link, pdMS_TO_TICKS(20));
        i2c_cmd_link_delete(link);

        if (ret != ESP_OK) {
            s_is_present = false;
            break;
        }
    }
}

void ssd1306_draw_pixel(int x, int y, bool on)
{
    if (x < 0 || x >= SSD1306_WIDTH || y < 0 || y >= SSD1306_HEIGHT) return;
    int idx = x + (y / 8) * SSD1306_WIDTH;
    if (on) {
        s_buffer[idx] |= (1 << (y % 8));
    } else {
        s_buffer[idx] &= ~(1 << (y % 8));
    }
}

void ssd1306_draw_char(int x, int y, char c)
{
    if (c < 32 || c > 126) c = ' ';
    const uint8_t *glyph = font5x7[c - 32];
    for (int col = 0; col < 5; col++) {
        uint8_t line = glyph[col];
        for (int row = 0; row < 8; row++) {
            ssd1306_draw_pixel(x + col, y + row, (line >> row) & 0x01);
        }
    }
}

void ssd1306_draw_string(int x, int y, const char *str)
{
    if (!str) return;
    int cursor_x = x;
    while (*str) {
        ssd1306_draw_char(cursor_x, y, *str);
        cursor_x += 6;
        str++;
    }
}

void ssd1306_draw_large_digit_string(int x, int y, const char *str)
{
    if (!str) return;
    int cursor_x = x;
    while (*str) {
        char c = *str;
        if (c >= 32 && c <= 126) {
            const uint8_t *glyph = font5x7[c - 32];
            for (int col = 0; col < 5; col++) {
                uint8_t line = glyph[col];
                for (int row = 0; row < 8; row++) {
                    bool on = (line >> row) & 0x01;
                    ssd1306_draw_pixel(cursor_x + col * 2, y + row * 2, on);
                    ssd1306_draw_pixel(cursor_x + col * 2 + 1, y + row * 2, on);
                    ssd1306_draw_pixel(cursor_x + col * 2, y + row * 2 + 1, on);
                    ssd1306_draw_pixel(cursor_x + col * 2 + 1, y + row * 2 + 1, on);
                }
            }
        }
        cursor_x += 12;
        str++;
    }
}

void ssd1306_draw_rect(int x, int y, int w, int h, bool fill)
{
    for (int i = 0; i < w; i++) {
        for (int j = 0; j < h; j++) {
            if (fill || i == 0 || i == w - 1 || j == 0 || j == h - 1) {
                ssd1306_draw_pixel(x + i, y + j, true);
            }
        }
    }
}

void ssd1306_draw_progress_bar(int x, int y, int w, int h, int percent)
{
    ssd1306_draw_rect(x, y, w, h, false);
    int fill_w = ((w - 4) * percent) / 100;
    if (fill_w > 0) {
        ssd1306_draw_rect(x + 2, y + 2, fill_w, h - 4, true);
    }
}

void ssd1306_show_splash(void)
{
    if (!s_is_present) return;
    ssd1306_clear();
    ssd1306_draw_rect(0, 0, 128, 64, false);
    ssd1306_draw_string(24, 8, "WiBreathe");
    ssd1306_draw_string(14, 24, "Wi-Fi CSI Monitor");
    ssd1306_draw_string(8, 44, "Research Prototype");
    ssd1306_draw_string(12, 54, "Not Medical Device");
    ssd1306_update();
}

void ssd1306_show_no_link(void)
{
    if (!s_is_present) return;
    ssd1306_clear();
    ssd1306_draw_rect(0, 0, 128, 64, false);
    ssd1306_draw_string(40, 16, "WiBreathe");
    ssd1306_draw_string(32, 34, "[ NO LINK ]");
    ssd1306_draw_string(14, 48, "Check USB or App");
    ssd1306_update();
}

void ssd1306_show_telemetry(const char *state, const char *bpm_str, int confidence)
{
    if (!s_is_present) return;
    ssd1306_clear();

    /* State Header Banner */
    ssd1306_draw_rect(0, 0, 128, 14, true);
    if (state) {
        int len = strlen(state);
        int sx = (128 - len * 6) / 2;
        if (sx < 2) sx = 2;
        for (int i = 0; i < len; i++) {
            char c = state[i];
            if (c >= 32 && c <= 126) {
                const uint8_t *glyph = font5x7[c - 32];
                for (int col = 0; col < 5; col++) {
                    uint8_t line = glyph[col];
                    for (int row = 0; row < 7; row++) {
                        bool on = !((line >> row) & 0x01);
                        ssd1306_draw_pixel(sx + i * 6 + col, 3 + row, on);
                    }
                }
            }
        }
    }

    /* Large Rate Digits */
    ssd1306_draw_large_digit_string(16, 22, bpm_str ? bpm_str : "--");
    ssd1306_draw_string(80, 28, "BPM");

    /* Confidence Quality Bar */
    ssd1306_draw_string(8, 48, "QUAL");
    ssd1306_draw_progress_bar(36, 48, 84, 9, confidence);

    ssd1306_update();
}
