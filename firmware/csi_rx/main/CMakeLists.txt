idf_component_register(
    SRCS "main.c" "ssd1306.c"
    INCLUDE_DIRS "."
    REQUIRES nvs_flash esp_wifi esp_netif esp_timer driver esp_hw_support wibreathe_common
)
