/*
 * WiBreathe CSI Receiver
 * Primary Target: Classic ESP32 DevKit V1 (ESP-32D / ESP-WROOM-32, 4 MB flash)
 * Secondary Guards: ESP32-S3, ESP32-C3
 *
 * UNVERIFIED ON PHYSICAL HARDWARE:
 *  - CSI callback context (assumed Wi-Fi task, non-ISR xQueueSend with 0 timeout).
 *  - Promiscuous requirement for ESP-NOW CSI (station mode with promiscuous filter).
 *  - wifi_csi_config_t struct members on classic ESP32 (lltf_en, htltf_en, etc.).
 *  - I/Q subcarrier column order in output lines.
 *
 * All serial data (CSI_DATA and STATUS) is routed exclusively through a single
 * writer task (csi_print_task) using atomic uart_write_bytes to prevent line interleaving.
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>

#include "nvs_flash.h"
#include "esp_idf_version.h"
#include "esp_mac.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_now.h"
#include "esp_timer.h"
#include "esp_system.h"
#include "driver/uart.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"

#include "ssd1306.h"

#define TAG "wibreathe_rx"

#ifndef CONFIG_WIBREATHE_RX_CHANNEL
#define CONFIG_WIBREATHE_RX_CHANNEL 6
#endif

#ifndef CONFIG_WIBREATHE_SERIAL_BAUD
#define CONFIG_WIBREATHE_SERIAL_BAUD 921600
#endif

/* Classic ESP32 DevKit V1 defaults: SDA=21, SCL=22. (ESP32-S3 uses SDA=8, SCL=9) */
#ifndef CONFIG_WIBREATHE_OLED_SDA_PIN
#define CONFIG_WIBREATHE_OLED_SDA_PIN 21
#endif

#ifndef CONFIG_WIBREATHE_OLED_SCL_PIN
#define CONFIG_WIBREATHE_OLED_SCL_PIN 22
#endif

#ifndef CONFIG_WIBREATHE_OLED_I2C_ADDR
#define CONFIG_WIBREATHE_OLED_I2C_ADDR 0x3C
#endif

#ifndef CONFIG_WIBREATHE_NO_LINK_TIMEOUT_SEC
#define CONFIG_WIBREATHE_NO_LINK_TIMEOUT_SEC 5
#endif

#ifndef CONFIG_WIBREATHE_EXPECTED_RATE_HZ
#define CONFIG_WIBREATHE_EXPECTED_RATE_HZ 50
#endif

#if defined(CONFIG_WIBREATHE_CSI_LLTF_HTLTF)
#define CSI_MAX_BUF_LEN 384
#else
#define CSI_MAX_BUF_LEN 128
#endif

#define CSI_QUEUE_DEPTH 32
#define UART_LINE_BUF_SIZE 2048

/* Buffered CSI frame for asynchronous serial output */
typedef struct {
    uint32_t rx_id;
    uint8_t mac[6];
    int8_t rssi;
    uint8_t rate;
    uint8_t sig_mode;
    uint8_t mcs;
    uint8_t cwb;
    uint8_t smoothing;
    uint8_t not_sounding;
    uint8_t aggregation;
    uint8_t stbc;
    uint8_t fec_coding;
    uint8_t sgi;
    int8_t noise_floor;
    uint8_t ampdu_cnt;
    uint8_t channel;
    uint8_t secondary_channel;
    uint32_t timestamp;
    uint8_t ant;
    uint16_t sig_len;
    uint8_t rx_state;
    uint16_t len;
    uint8_t first_word_invalid;
    int8_t buf[CSI_MAX_BUF_LEN];
} csi_item_t;

static QueueHandle_t s_csi_queue = NULL;
static uint32_t s_csi_frames_total = 0;
static uint32_t s_csi_frames_dropped = 0;
static uint8_t s_filter_mac[6] = {0};
static bool s_has_mac_filter = false;
static int64_t s_last_disp_time_us = 0;

static const char *get_reset_reason_str(esp_reset_reason_t r)
{
    switch (r) {
        case ESP_RST_POWERON:   return "Power-on event";
        case ESP_RST_EXT:       return "External pin reset";
        case ESP_RST_SW:        return "Software reset";
        case ESP_RST_PANIC:     return "Software exception / panic";
        case ESP_RST_INT_WDT:   return "Interrupt watchdog reset";
        case ESP_RST_TASK_WDT:  return "Task watchdog reset";
        case ESP_RST_WDT:       return "Other watchdog reset";
        case ESP_RST_DEEPSLEEP: return "Deep-sleep wake reset";
        case ESP_RST_BROWNOUT:  return "Brownout reset (VDD voltage dip)";
        case ESP_RST_SDIO:      return "SDIO reset";
        default:                return "Unknown reset reason";
    }
}

static void parse_mac_filter(void)
{
#ifdef CONFIG_WIBREATHE_FILTER_MAC
    const char *mac_str = CONFIG_WIBREATHE_FILTER_MAC;
    if (strlen(mac_str) == 17) {
        int values[6];
        if (6 == sscanf(mac_str, "%x:%x:%x:%x:%x:%x",
                        &values[0], &values[1], &values[2],
                        &values[3], &values[4], &values[5])) {
            for (int i = 0; i < 6; i++) {
                s_filter_mac[i] = (uint8_t)values[i];
            }
            s_has_mac_filter = true;
            ESP_LOGW(TAG, "Filtering CSI from MAC: " MACSTR, MAC2STR(s_filter_mac));
            return;
        }
    }
#endif
    s_has_mac_filter = false;
    /* UNVERIFIED: Loud boot warning when FILTER_MAC is empty */
    ESP_LOGW(TAG, "****************************************************************");
    ESP_LOGW(TAG, "* WARNING: No transmitter MAC filter set (FILTER_MAC is empty)!*");
    ESP_LOGW(TAG, "* Receiver will capture CSI from ALL nearby 802.11n transmitters*");
    ESP_LOGW(TAG, "* Set CONFIG_WIBREATHE_FILTER_MAC to lock to your csi_tx node! *");
    ESP_LOGW(TAG, "****************************************************************");
}

/*
 * CSI Callback: Executed in Wi-Fi task context (not an ISR in ESP-IDF v5.x).
 * Strictly zero dynamic allocation (no malloc/free), no printf, no heavy processing.
 * Dispatches frame to queue with timeout 0. Dropped frames increment counter.
 */
static void wifi_csi_rx_cb(void *ctx, wifi_csi_info_t *info)
{
    if (!info || !info->buf || info->len == 0) {
        return;
    }

    if (s_has_mac_filter && memcmp(info->mac, s_filter_mac, 6) != 0) {
        return;
    }

    const wifi_pkt_rx_ctrl_t *rx_ctrl = &info->rx_ctrl;

    /* UNVERIFIED: Optional drop of non-HT frames (sig_mode != 1) */
#if defined(CONFIG_WIBREATHE_ONLY_HT_FRAMES)
    if (rx_ctrl->sig_mode != 1) {
        return;
    }
#endif

    csi_item_t item;

    /* UNVERIFIED: Scan info->payload for 4-byte magic "WIBR", read following uint32 sequence */
    bool seq_found = false;
#if defined(ESP_IDF_VERSION) && defined(ESP_IDF_VERSION_VAL)
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 1, 0)
    if (info->payload && info->payload_len >= 8) {
        for (int i = 0; i <= (int)info->payload_len - 8; i++) {
            if (memcmp(info->payload + i, "WIBR", 4) == 0) {
                memcpy(&item.rx_id, info->payload + i + 4, sizeof(uint32_t));
                seq_found = true;
                break;
            }
        }
    }
#endif
#endif
    if (!seq_found) {
        item.rx_id = s_csi_frames_total;
    }

    memcpy(item.mac, info->mac, 6);
    item.rssi = rx_ctrl->rssi;
    item.rate = rx_ctrl->rate;
    item.sig_mode = rx_ctrl->sig_mode;
    item.mcs = rx_ctrl->mcs;
    item.cwb = rx_ctrl->cwb;
    item.smoothing = rx_ctrl->smoothing;
    item.not_sounding = rx_ctrl->not_sounding;
    item.aggregation = rx_ctrl->aggregation;
    item.stbc = rx_ctrl->stbc;
    item.fec_coding = rx_ctrl->fec_coding;
    item.sgi = rx_ctrl->sgi;
    item.noise_floor = rx_ctrl->noise_floor;
    item.ampdu_cnt = rx_ctrl->ampdu_cnt;
    item.channel = rx_ctrl->channel;
    item.secondary_channel = rx_ctrl->secondary_channel;
    item.timestamp = rx_ctrl->timestamp;
    item.ant = rx_ctrl->ant;
    item.sig_len = rx_ctrl->sig_len;
    /* UNVERIFIED: Set rx_state from rx_ctrl->rx_state, not sig_mode */
    item.rx_state = rx_ctrl->rx_state;
    item.first_word_invalid = info->first_word_invalid;

    uint16_t copy_len = info->len < CSI_MAX_BUF_LEN ? info->len : CSI_MAX_BUF_LEN;
    item.len = copy_len;
    memcpy(item.buf, info->buf, copy_len);

    /* Queue send with zero timeout from Wi-Fi task context */
    if (xQueueSend(s_csi_queue, &item, 0) == pdTRUE) {
        s_csi_frames_total++;
    } else {
        s_csi_frames_dropped++;
    }
}

/*
 * Single Writer Task: Exclusively controls UART0 TX for CSI_DATA and STATUS lines.
 * Formats full line into buffer and sends atomically via uart_write_bytes to eliminate interleaving.
 */
static void csi_print_task(void *pvParameters)
{
    csi_item_t item;
    static char line_buf[UART_LINE_BUF_SIZE];
    static bool s_header_printed = false;

    if (!s_header_printed) {
        const char *hdr = "type,id,mac,rssi,rate,sig_mode,mcs,bandwidth,smoothing,not_sounding,aggregation,"
                          "stbc,fec_coding,sgi,noise_floor,ampdu_cnt,channel,secondary_channel,local_timestamp,"
                          "ant,sig_len,rx_state,len,first_word,data\n";
        uart_write_bytes(UART_NUM_0, hdr, strlen(hdr));
        s_header_printed = true;
    }

    int64_t last_status_us = esp_timer_get_time();

    while (1) {
        if (xQueueReceive(s_csi_queue, &item, pdMS_TO_TICKS(50)) == pdTRUE) {
            /* Format header part */
            int pos = snprintf(line_buf, sizeof(line_buf),
                               "CSI_DATA,%u," MACSTR ",%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%u,%d,%d,%d,%d,%d,\"[",
                               (unsigned int)item.rx_id,
                               MAC2STR(item.mac),
                               item.rssi,
                               item.rate,
                               item.sig_mode,
                               item.mcs,
                               item.cwb,
                               item.smoothing,
                               item.not_sounding,
                               item.aggregation,
                               item.stbc,
                               item.fec_coding,
                               item.sgi,
                               item.noise_floor,
                               item.ampdu_cnt,
                               item.channel,
                               item.secondary_channel,
                               (unsigned int)item.timestamp,
                               item.ant,
                               item.sig_len,
                               item.rx_state,
                               item.len,
                               item.first_word_invalid);

            /* Append subcarrier array: UNVERIFIED I/Q order on classic ESP32 */
            for (int i = 0; i < item.len && pos < (int)(sizeof(line_buf) - 10); i++) {
                if (i == 0) {
                    pos += snprintf(line_buf + pos, sizeof(line_buf) - pos, "%d", item.buf[i]);
                } else {
                    pos += snprintf(line_buf + pos, sizeof(line_buf) - pos, ",%d", item.buf[i]);
                }
            }
            if (pos < (int)(sizeof(line_buf) - 3)) {
                pos += snprintf(line_buf + pos, sizeof(line_buf) - pos, "]\"\n");
            } else {
                line_buf[sizeof(line_buf) - 2] = '\n';
                line_buf[sizeof(line_buf) - 1] = '\0';
                pos = sizeof(line_buf) - 1;
            }

            /* Atomic write to UART0 */
            uart_write_bytes(UART_NUM_0, line_buf, pos);
        }

        /* Periodic status heartbeat line every 5 seconds */
        int64_t now = esp_timer_get_time();
        if (now - last_status_us >= 5000000LL) {
            uint32_t uptime_ms = (uint32_t)(now / 1000LL);
            int len = snprintf(line_buf, sizeof(line_buf),
                               "STATUS,%lu,%lu,%lu,%d,%s\n",
                               (unsigned long)uptime_ms,
                               (unsigned long)s_csi_frames_total,
                               (unsigned long)s_csi_frames_dropped,
                               CONFIG_WIBREATHE_RX_CHANNEL,
                               s_has_mac_filter ? CONFIG_WIBREATHE_FILTER_MAC : "none");
            if (len > 0) {
                uart_write_bytes(UART_NUM_0, line_buf, len);
            }
            last_status_us = now;
        }
    }
}

/*
 * UART RX Task: Reads downstream DISP telemetry lines from host laptop.
 * Protocol spec in docs/PROTOCOL.md: DISP,<state>,<bpm or -->,<confidence 0-100>,<seq>\n
 * Uses bounded line buffer; ignores overlong lines; never blocks CSI writer.
 */
static void uart_rx_task(void *pvParameters)
{
    uint8_t line_buf[64];
    int line_pos = 0;

    while (1) {
        uint8_t ch;
        int len = uart_read_bytes(UART_NUM_0, &ch, 1, pdMS_TO_TICKS(50));
        if (len > 0) {
            if (ch == '\n' || ch == '\r') {
                if (line_pos > 0) {
                    line_buf[line_pos] = '\0';
                    /* Parse canonical format: DISP,<state>,<bpm>,<conf>,<seq> */
                    if (strncmp((char *)line_buf, "DISP,", 5) == 0) {
                        char state_str[20] = {0};
                        char bpm_str[12] = {0};
                        int conf = 0;
                        int seq = 0;
                        if (4 == sscanf((char *)line_buf + 5, "%19[^,],%11[^,],%d,%d",
                                        state_str, bpm_str, &conf, &seq)) {
                            s_last_disp_time_us = esp_timer_get_time();
                            ssd1306_show_telemetry(state_str, bpm_str, conf);
                        }
                    }
                    line_pos = 0;
                }
            } else if (line_pos < (int)(sizeof(line_buf) - 1)) {
                line_buf[line_pos++] = ch;
            } else {
                /* Line overflow: discard to prevent unbounded accumulation */
                line_pos = 0;
            }
        }

        /* Check for timeout: show No Link screen if no DISP packet arrived */
        int64_t now = esp_timer_get_time();
        if (s_last_disp_time_us > 0 &&
            (now - s_last_disp_time_us) > (CONFIG_WIBREATHE_NO_LINK_TIMEOUT_SEC * 1000000LL)) {
            ssd1306_show_no_link();
            s_last_disp_time_us = 0;
        }
    }
}

static void wifi_init_csi(void)
{
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));

    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_bandwidth(WIFI_IF_STA, WIFI_BW_HT20));
    ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_WIBREATHE_RX_CHANNEL, WIFI_SECOND_CHAN_NONE));

    /*
     * UNVERIFIED: Check whether promiscuous mode is required on classic ESP32 for ESP-NOW CSI.
     * Setting promiscuous filter allows PHY to pass all frames to CSI callback.
     */
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous(true));
    ESP_ERROR_CHECK(esp_now_init());

    /*
     * Classic ESP32 CSI Configuration.
     * UNVERIFIED: Struct field definitions checked against esp-csi reference csi_recv.
     */
#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61 || CONFIG_IDF_TARGET_ESP32C6
    wifi_csi_config_t csi_config = {
        .enable = true,
        .acquire_csi_ht20 = true,
        .acquire_csi_ht40 = true,
    };
#else
    /* Classic ESP32 (and ESP32-S3/C3) */
#if defined(CONFIG_WIBREATHE_CSI_LLTF_HTLTF)
    wifi_csi_config_t csi_config = {
        .lltf_en           = true,
        .htltf_en          = true,
        .stbc_htltf2_en    = false,
        .ltf_merge_en      = false,
        .channel_filter_en = true,
        .manu_scale        = false,
        .shift             = false,
    };
#else
    /* Default: LLTF only (len=128, 64 subcarriers) to preserve UART headroom */
    wifi_csi_config_t csi_config = {
        .lltf_en           = true,
        .htltf_en          = false,
        .stbc_htltf2_en    = false,
        .ltf_merge_en      = false,
        .channel_filter_en = true,
        .manu_scale        = false,
        .shift             = false,
    };
#endif
#endif
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi_config));
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(wifi_csi_rx_cb, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));

    /*
     * UNVERIFIED / NOT AVAILABLE on classic ESP32: Forced hardware RX gain (phy_force_rx_gain)
     * is not part of standard public ESP-IDF v5.x Wi-Fi APIs for classic ESP32.
     * Amplitude stability relies on host-side normalization (PCA / baseline profiling).
     */

    ESP_LOGW(TAG, "Wi-Fi CSI reception configured on Channel %d (LLTF len=%d)",
             CONFIG_WIBREATHE_RX_CHANNEL, CSI_MAX_BUF_LEN);
}

static void check_bandwidth_budget(void)
{
    /*
     * Actual formatted line length:
     * Header prefix: ~90 bytes.
     * Subcarrier values in CSV format: average 3.5 bytes per value (signed int8: e.g. -128 to 127).
     * For len=128: 90 + 128*3.5 = ~538 bytes/line.
     * For len=384: 90 + 384*3.5 = ~1434 bytes/line.
     */
    uint32_t bytes_per_frame = 90 + (CSI_MAX_BUF_LEN * 7) / 2;
    uint32_t rate_hz = CONFIG_WIBREATHE_EXPECTED_RATE_HZ;
    uint32_t est_bytes_sec = bytes_per_frame * rate_hz;
    uint32_t baud = CONFIG_WIBREATHE_SERIAL_BAUD;
    uint32_t max_serial_capacity = baud / 10; /* 8N1 framing = 10 bits per byte */
    uint32_t usage_pct = (est_bytes_sec * 100) / max_serial_capacity;

    ESP_LOGW(TAG, "Serial Budget: len=%d, rate=%lu Hz, est line=%lu B -> %lu B/s (%lu%% of %lu baud)",
             CSI_MAX_BUF_LEN, (unsigned long)rate_hz, (unsigned long)bytes_per_frame,
             (unsigned long)est_bytes_sec, (unsigned long)usage_pct, (unsigned long)baud);

    if (usage_pct > 80) {
        ESP_LOGE(TAG, "DANGER: Serial bandwidth usage %lu%% exceeds 80%% threshold! Dropped frames likely.",
                 (unsigned long)usage_pct);
    }
}

void app_main(void)
{
    /* 1. Log reset reason and check brownout */
    esp_reset_reason_t reason = esp_reset_reason();
    ESP_LOGW(TAG, "Board Reset Reason: %d (%s)", reason, get_reset_reason_str(reason));
    if (reason == ESP_RST_BROWNOUT) {
        ESP_LOGE(TAG, "CRITICAL: Brownout reset detected! VDD supply dipped below brownout threshold. "
                      "Check USB cable resistance, host USB port current capacity, or add a decoupling capacitor.");
    }

    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    ESP_LOGW(TAG, "=========================================================");
    ESP_LOGW(TAG, " WiBreathe CSI Receiver (Classic ESP32 DevKit V1)");
    ESP_LOGW(TAG, " Notice: Research prototype - Not a medical device");
    ESP_LOGW(TAG, "=========================================================");

    parse_mac_filter();
    check_bandwidth_budget();

    /* 2. Configure UART0 driver with appropriate FIFO buffers */
    uart_config_t uart_config = {
        .baud_rate = CONFIG_WIBREATHE_SERIAL_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity    = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    ESP_ERROR_CHECK(uart_param_config(UART_NUM_0, &uart_config));
    ESP_ERROR_CHECK(uart_driver_install(UART_NUM_0, 1024, 2048, 0, NULL, 0));

    /* 3. Initialize FreeRTOS queue for CSI frames */
    s_csi_queue = xQueueCreate(CSI_QUEUE_DEPTH, sizeof(csi_item_t));

    /* 4. Initialize OLED display (returns immediately if not connected) */
    ret = ssd1306_init(I2C_NUM_0,
                       CONFIG_WIBREATHE_OLED_SDA_PIN,
                       CONFIG_WIBREATHE_OLED_SCL_PIN,
                       CONFIG_WIBREATHE_OLED_I2C_ADDR);
    if (ret == ESP_OK) {
        ssd1306_show_splash();
    }

    /* 5. Initialize Wi-Fi and CSI */
    wifi_init_csi();

    /* 6. Launch FreeRTOS tasks */
    xTaskCreatePinnedToCore(csi_print_task, "csi_printer", 4096, NULL, 5, NULL, 1);
    xTaskCreatePinnedToCore(uart_rx_task, "uart_rx_worker", 3072, NULL, 4, NULL, 0);
}
