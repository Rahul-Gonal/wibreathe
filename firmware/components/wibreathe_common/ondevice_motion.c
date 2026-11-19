#include "ondevice_motion.h"
#include <string.h>

void ondevice_motion_init(ondevice_motion_t *detector, int32_t threshold)
{
    if (!detector) return;
    memset(detector->history, 0, sizeof(detector->history));
    detector->head = 0;
    detector->count = 0;
    detector->variance_threshold = threshold;
    detector->motion_flag = false;
}

bool ondevice_motion_update(ondevice_motion_t *detector, int8_t *raw_subcarriers, uint16_t len)
{
    if (!detector || !raw_subcarriers || len < 4) {
        return false;
    }

    /* Compute approximate energy over first 8 subcarrier pairs */
    int32_t energy = 0;
    uint16_t sub_count = (len / 2) < 8 ? (len / 2) : 8;
    for (uint16_t i = 0; i < sub_count; i++) {
        int32_t q = raw_subcarriers[i * 2];
        int32_t real_part = raw_subcarriers[i * 2 + 1];
        energy += (q * q + real_part * real_part);
    }

    /* Store energy in circular buffer */
    detector->history[detector->head] = energy;
    detector->head = (detector->head + 1) & 0x0F;
    if (detector->count < 16) {
        detector->count++;
        return false;
    }

    /* Calculate rolling mean and variance */
    int64_t sum = 0;
    for (int i = 0; i < 16; i++) {
        sum += detector->history[i];
    }
    int32_t mean = (int32_t)(sum / 16);

    int64_t var_sum = 0;
    for (int i = 0; i < 16; i++) {
        int32_t diff = detector->history[i] - mean;
        var_sum += (int64_t)diff * diff;
    }
    int32_t variance = (int32_t)(var_sum / 16);

    detector->motion_flag = (variance > detector->variance_threshold);
    return detector->motion_flag;
}
