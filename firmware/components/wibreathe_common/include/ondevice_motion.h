#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Minimal rolling-variance motion detection stub running directly on ESP32.
 */
typedef struct {
    int32_t history[16];
    uint8_t head;
    uint8_t count;
    int32_t variance_threshold;
    bool motion_flag;
} ondevice_motion_t;

void ondevice_motion_init(ondevice_motion_t *detector, int32_t threshold);
bool ondevice_motion_update(ondevice_motion_t *detector, int8_t *raw_subcarriers, uint16_t len);

#ifdef __cplusplus
}
#endif
