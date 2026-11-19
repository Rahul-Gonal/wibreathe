/*
 * WiBreathe CSI Transmitter (ESP32 / ESP32-S3 / ESP32-C3)
 * Target: ESP-IDF v5.x
 *
 * Transmits broadcast packets at steady non-drifting cadence using ESP-NOW with
 * an OFDM/HT physical rate (MCS0) required to trigger CSI generation at the receiver.
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>

#include "nvs_flash.h"
#include "esp_mac.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_now.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define TAG "wibreathe_tx"

#ifndef CONFIG_WIBREATHE_TX_CHANNEL
#define CONFIG_WIBREATHE_TX_CHANNEL 6
#endif

#ifndef CONFIG_WIBREATHE_TX_RATE_HZ
#define CONFIG_WIBREATHE_TX_RATE_HZ 50
#endif

#ifndef CONFIG_WIBREATHE_TX_POWER_DBM
#define CONFIG_WIBREATHE_TX_POWER_DBM 20
#endif

static const uint8_t s_broadcast_mac[ESP_NOW_ETH_ALEN] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
static uint32_t s_packets_sent = 0;
static uint32_t s_send_failures = 0;

/* Payload structure with embedded 32-bit sequence number and magic header */
typedef struct __attribute__((packed)) {
    char magic[4];        /* "WIBR" */
    uint32_t sequence_id; /* Incrementing sequence number */
    int64_t timestamp_us; /* Monotonic microsecond clock */
    uint8_t padding[16];  /* Fixed packet size */
} wibreathe_packet_t;

static void wifi_init_sta(void)
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
    ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_WIBREATHE_TX_CHANNEL, WIFI_SECOND_CHAN_NONE));

    /* Scale dBm to quarter-dBm units used by esp_wifi_set_max_tx_power */
    int8_t power_val = (int8_t)(CONFIG_WIBREATHE_TX_POWER_DBM * 4);
    esp_wifi_set_max_tx_power(power_val);

    uint8_t mac[6];
    esp_wifi_get_mac(WIFI_IF_STA, mac);
    ESP_LOGI(TAG, "Wi-Fi STA Initialized. Primary Channel: %d", CONFIG_WIBREATHE_TX_CHANNEL);
    ESP_LOGI(TAG, "TRANSMITTER MAC ADDRESS: " MACSTR " (configure this in receiver)", MAC2STR(mac));
}

static void espnow_init_transmitter(void)
{
    ESP_ERROR_CHECK(esp_now_init());

    esp_now_peer_info_t peer = {
        .channel = CONFIG_WIBREATHE_TX_CHANNEL,
        .ifidx = WIFI_IF_STA,
        .encrypt = false,
    };
    memcpy(peer.peer_addr, s_broadcast_mac, ESP_NOW_ETH_ALEN);
    ESP_ERROR_CHECK(esp_now_add_peer(&peer));

    /*
     * CRITICAL: By default, ESP-NOW sends frames at 1 Mbps DSSS (802.11b).
     * The ESP32 Wi-Fi hardware PHY only generates Channel State Information (CSI)
     * for OFDM packets (802.11g/n). We explicitly force an 802.11n MCS0 rate.
     */
    esp_now_rate_config_t rate_config = {
        .phymode = WIFI_PHY_MODE_HT20,
        .rate = WIFI_PHY_RATE_MCS0_LGI,
        .ersu = false,
        .dcm = false,
    };
    ESP_ERROR_CHECK(esp_now_set_peer_rate_config(peer.peer_addr, &rate_config));
    ESP_LOGI(TAG, "ESP-NOW configured: Broadcast rate set to 802.11n HT20 / MCS0");
}

static void tx_worker_task(void *pvParameters)
{
    const int64_t interval_us = 1000000LL / CONFIG_WIBREATHE_TX_RATE_HZ;
    int64_t next_send_time = esp_timer_get_time();
    int64_t last_heartbeat_time = next_send_time;

    wibreathe_packet_t pkt;
    memcpy(pkt.magic, "WIBR", 4);
    memset(pkt.padding, 0xAA, sizeof(pkt.padding));

    uint32_t seq = 0;
    ESP_LOGI(TAG, "Starting transmission loop at %d Hz cadence...", CONFIG_WIBREATHE_TX_RATE_HZ);

    while (1) {
        pkt.sequence_id = seq++;
        pkt.timestamp_us = esp_timer_get_time();

        esp_err_t ret = esp_now_send(s_broadcast_mac, (const uint8_t *)&pkt, sizeof(pkt));
        if (ret == ESP_OK) {
            s_packets_sent++;
        } else {
            s_send_failures++;
        }

        /* Monotonic timer pacing to eliminate task scheduling drift */
        next_send_time += interval_us;
        int64_t now = esp_timer_get_time();
        int64_t delay_us = next_send_time - now;

        if (delay_us > 1000) {
            vTaskDelay(pdMS_TO_TICKS(delay_us / 1000));
        } else if (delay_us < -50000) {
            /* If fell severely behind (e.g. system interrupt), resynchronize */
            next_send_time = now;
        }

        /* Periodic heartbeat report every 5 seconds */
        if (now - last_heartbeat_time >= 5000000LL) {
            ESP_LOGI(TAG, "Heartbeat: sent=%lu, failed=%lu, free_heap=%lu bytes",
                     (unsigned long)s_packets_sent,
                     (unsigned long)s_send_failures,
                     (unsigned long)esp_get_free_heap_size());
            last_heartbeat_time = now;
        }
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
    ESP_LOGI(TAG, " WiBreathe CSI Transmitter - ESP-IDF v5.x");
    ESP_LOGI(TAG, " Notice: Research prototype - Not a medical device");
    ESP_LOGI(TAG, "===============================================");

    wifi_init_sta();
    espnow_init_transmitter();

    xTaskCreatePinnedToCore(tx_worker_task, "csi_tx_worker", 4096, NULL, 5, NULL, 0);
}
