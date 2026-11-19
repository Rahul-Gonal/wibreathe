/*
 * WiBreathe CSI Receiver (ESP32 / ESP32-S3 / ESP32-C3)
 * Target: ESP-IDF v5.x
 *
 * Acquires 802.11n Channel State Information (CSI), streams formatted ASCII frames
 * to USB-UART in ground-truth format, provides status heartbeats, and drives an
 * SSD1306 OLED display with live respiration telemetry sent back from the host laptop.
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>

#include "nvs_flash.h"
#include "esp_mac.h"
#include "rom/ets_sys.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_now.h"
#include "esp_timer.h"
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

#ifndef CONFIG_WIBREATHE_OLED_SDA_PIN
#define CONFIG_WIBREATHE_OLED_SDA_PIN 8
#endif

#ifndef CONFIG_WIBREATHE_OLED_SCL_PIN
#define CONFIG_WIBREATHE_OLED_SCL_PIN 9
#endif

#ifndef CONFIG_WIBREATHE_OLED_I2C_ADDR
#define CONFIG_WIBREATHE_OLED_I2C_ADDR 0x3C
#endif

#ifndef CONFIG_WIBREATHE_NO_LINK_TIMEOUT_SEC
#define CONFIG_WIBREATHE_NO_LINK_TIMEOUT_SEC 5
#endif

#define CSI_MAX_BUF_LEN 128
#define CSI_QUEUE_DEPTH 64

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
            ESP_LOGI(TAG, "Filtering CSI from MAC: " MACSTR, MAC2STR(s_filter_mac));
            return;
        }
    }
#endif
    s_has_mac_filter = false;
    ESP_LOGI(TAG, "No MAC filter configured: accepting CSI from all transmitters");
}

/* Fast ISR callback: strictly copies packet info and buffers into FreeRTOS queue */
static void wifi_csi_rx_cb(void *ctx, wifi_csi_info_t *info)
{
    if (!info || !info->buf || info->len == 0) {
        return;
    }

    if (s_has_mac_filter && memcmp(info->mac, s_filter_mac, 6) != 0) {
        return;
    }

    csi_item_t item;
    const wifi_pkt_rx_ctrl_t *rx_ctrl = &info->rx_ctrl;

    /* Extract sequence ID if embedded in payload, else use counter */
    if (info->payload && info->len >= 19) {
        item.rx_id = *(uint32_t *)(info->payload + 4);
    } else {
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
    item.rx_state = rx_ctrl->sig_mode;
    item.first_word_invalid = info->first_word_invalid;

    uint16_t copy_len = info->len < CSI_MAX_BUF_LEN ? info->len : CSI_MAX_BUF_LEN;
    item.len = copy_len;
    memcpy(item.buf, info->buf, copy_len);

    BaseType_t xHigherPriorityTaskWoken = pdFALSE;
    if (xQueueSendFromISR(s_csi_queue, &item, &xHigherPriorityTaskWoken) == pdTRUE) {
        s_csi_frames_total++;
    } else {
        s_csi_frames_dropped++;
    }

    if (xHigherPriorityTaskWoken) {
        portYIELD_FROM_ISR();
    }
}

static void csi_print_task(void *pvParameters)
{
    csi_item_t item;
    static bool s_header_printed = false;

    if (!s_header_printed) {
        ets_printf("type,id,mac,rssi,rate,sig_mode,mcs,bandwidth,smoothing,not_sounding,aggregation,"
                   "stbc,fec_coding,sgi,noise_floor,ampdu_cnt,channel,secondary_channel,local_timestamp,"
                   "ant,sig_len,rx_state,len,first_word,data\n");
        s_header_printed = true;
    }

    int64_t last_status_us = esp_timer_get_time();

    while (1) {
        if (xQueueReceive(s_csi_queue, &item, pdMS_TO_TICKS(100)) == pdTRUE) {
            /* Format exact CSI_DATA line matching Espressif parser column order */
            ets_printf("CSI_DATA,%u," MACSTR ",%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%u,%d,%d,%d,%d,%d,\"[",
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

            if (item.len > 0) {
                ets_printf("%d", item.buf[0]);
                for (int i = 1; i < item.len; i++) {
                    ets_printf(",%d", item.buf[i]);
                }
            }
            ets_printf("]\"\n");
        }

        /* Periodic status heartbeat line every 5 seconds */
        int64_t now = esp_timer_get_time();
        if (now - last_status_us >= 5000000LL) {
            uint32_t uptime_ms = (uint32_t)(now / 1000LL);
            ets_printf("STATUS,%lu,%lu,%lu,%d,%s\n",
                       (unsigned long)uptime_ms,
                       (unsigned long)s_csi_frames_total,
                       (unsigned long)s_csi_frames_dropped,
                       CONFIG_WIBREATHE_RX_CHANNEL,
                       s_has_mac_filter ? CONFIG_WIBREATHE_FILTER_MAC : "none");
            last_status_us = now;
        }
    }
}

/* UART downstream listener reading DISP telemetry packets from laptop */
static void uart_rx_task(void *pvParameters)
{
    uint8_t line_buf[128];
    int line_pos = 0;

    while (1) {
        uint8_t ch;
        int len = uart_read_bytes(UART_NUM_0, &ch, 1, pdMS_TO_TICKS(100));
        if (len > 0) {
            if (ch == '\n' || ch == '\r') {
                if (line_pos > 0) {
                    line_buf[line_pos] = '\0';
                    /* Parse: DISP,<state>,<bpm>,<confidence>,<seq> */
                    if (strncmp((char *)line_buf, "DISP,", 5) == 0) {
                        char state_str[24] = {0};
                        char bpm_str[16] = {0};
                        int conf = 0;
                        int seq = 0;
                        if (4 == sscanf((char *)line_buf + 5, "%23[^,],%15[^,],%d,%d",
                                        state_str, bpm_str, &conf, &seq)) {
                            s_last_disp_time_us = esp_timer_get_time();
                            ssd1306_show_telemetry(state_str, bpm_str, conf);
                        }
                    }
                    line_pos = 0;
                }
            } else if (line_pos < sizeof(line_buf) - 1) {
                line_buf[line_pos++] = ch;
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
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous(true));

    /* Initialize ESP-NOW so promiscuous reception is active */
    ESP_ERROR_CHECK(esp_now_init());

    /* CSI Configuration matching ESP-IDF v5.x ground truth */
#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61
    wifi_csi_config_t csi_config = {
        .enable = true,
        .acquire_csi_ht20 = true,
        .acquire_csi_ht40 = true,
    };
#elif CONFIG_IDF_TARGET_ESP32C6
    wifi_csi_config_t csi_config = {
        .enable = true,
        .acquire_csi_ht20 = true,
        .acquire_csi_ht40 = true,
    };
#else
    wifi_csi_config_t csi_config = {
        .lltf_en           = true,
        .htltf_en          = true,
        .stbc_htltf2_en    = true,
        .ltf_merge_en      = true,
        .channel_filter_en = true,
        .manu_scale        = false,
        .shift             = false,
    };
#endif
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi_config));
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(wifi_csi_rx_cb, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));

    ESP_LOGI(TAG, "Wi-Fi CSI reception configured on Channel %d", CONFIG_WIBREATHE_RX_CHANNEL);
}

static void check_bandwidth_budget(void)
{
    /* Estimate throughput: ~450 bytes per frame at 50 Hz = ~22,500 bytes/sec */
    const uint32_t approx_bytes_per_frame = 450;
    const uint32_t rate_hz = 50;
    uint32_t est_bytes_sec = approx_bytes_per_frame * rate_hz;
    uint32_t baud = CONFIG_WIBREATHE_SERIAL_BAUD;
    uint32_t max_serial_capacity = baud / 10; /* ~10 bits per byte with framing */
    uint32_t usage_pct = (est_bytes_sec * 100) / max_serial_capacity;

    ESP_LOGI(TAG, "UART Baud: %lu | Est. CSI Ingress: %lu B/s | Link Budget Usage: %lu%%",
             (unsigned long)baud, (unsigned long)est_bytes_sec, (unsigned long)usage_pct);

    if (usage_pct > 80) {
        ESP_LOGW(TAG, "WARNING: Estimated CSI bandwidth exceeds 80%% of serial link capacity! "
                      "Consider reducing packet rate or disabling LTF subcarriers.");
    }
}

void app_main(void)
{
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    ESP_LOGI(TAG, "===============================================");
    ESP_LOGI(TAG, " WiBreathe CSI Receiver - ESP-IDF v5.x");
    ESP_LOGI(TAG, " Notice: Research prototype - Not a medical device");
    ESP_LOGI(TAG, "===============================================");

    parse_mac_filter();
    check_bandwidth_budget();

    /* Initialize FreeRTOS queue for CSI frames */
    s_csi_queue = xQueueCreate(CSI_QUEUE_DEPTH, sizeof(csi_item_t));

    /* Initialize OLED display */
    ret = ssd1306_init(I2C_NUM_0,
                       CONFIG_WIBREATHE_OLED_SDA_PIN,
                       CONFIG_WIBREATHE_OLED_SCL_PIN,
                       CONFIG_WIBREATHE_OLED_I2C_ADDR);
    if (ret == ESP_OK) {
        ssd1306_show_splash();
    }

    wifi_init_csi();

    /* Dedicated tasks: Core 1 handles CSI printing, Core 0 handles UART RX and display */
    xTaskCreatePinnedToCore(csi_print_task, "csi_printer", 4096, NULL, 5, NULL, 1);
    xTaskCreatePinnedToCore(uart_rx_task, "uart_rx_worker", 4096, NULL, 4, NULL, 0);
}
