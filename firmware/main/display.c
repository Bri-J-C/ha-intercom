/**
 * Display Module Implementation
 *
 * SSD1306 OLED driver and room selection UI.
 */

#include "display.h"

#if FEATURE_DISPLAY

#include <string.h>
#include "driver/i2c_master.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "display";

// I2C handle
static i2c_master_bus_handle_t i2c_bus = NULL;
static i2c_master_dev_handle_t i2c_dev = NULL;
static bool display_available = false;

// Room list
static room_target_t rooms[MAX_ROOMS];
static int room_count = 0;
static int selected_index = 0;

// Display state
static display_state_t current_state = DISPLAY_STATE_IDLE;
static char remote_name[MAX_ROOM_NAME_LEN] = "";

// Cycle button state
static bool cycle_button_pressed = false;
static int64_t last_cycle_time = 0;
#define CYCLE_DEBOUNCE_MS 200

// Temporary message
static char temp_message[32] = "";
static int64_t temp_message_until = 0;

// Horizontal scroll for long names
static int scroll_offset = 0;
static int64_t last_scroll_time = 0;
static int last_selected_index = -1;
#define SCROLL_DELAY_MS 150  // Speed of scrolling
#define MAX_VISIBLE_CHARS 16  // Characters that fit in selection box

// Frame buffer (128x64 = 1024 bytes, organized as 8 pages of 128 bytes)
static uint8_t framebuffer[DISPLAY_WIDTH * DISPLAY_HEIGHT / 8];

// Simple 5x7 font (ASCII 32-127)
// Each character is 5 bytes wide, each byte is a column with LSB at top
static const uint8_t font_5x7[] = {
    0x00, 0x00, 0x00, 0x00, 0x00, // Space
    0x00, 0x00, 0x5F, 0x00, 0x00, // !
    0x00, 0x07, 0x00, 0x07, 0x00, // "
    0x14, 0x7F, 0x14, 0x7F, 0x14, // #
    0x24, 0x2A, 0x7F, 0x2A, 0x12, // $
    0x23, 0x13, 0x08, 0x64, 0x62, // %
    0x36, 0x49, 0x55, 0x22, 0x50, // &
    0x00, 0x05, 0x03, 0x00, 0x00, // '
    0x00, 0x1C, 0x22, 0x41, 0x00, // (
    0x00, 0x41, 0x22, 0x1C, 0x00, // )
    0x08, 0x2A, 0x1C, 0x2A, 0x08, // *
    0x08, 0x08, 0x3E, 0x08, 0x08, // +
    0x00, 0x50, 0x30, 0x00, 0x00, // ,
    0x08, 0x08, 0x08, 0x08, 0x08, // -
    0x00, 0x60, 0x60, 0x00, 0x00, // .
    0x20, 0x10, 0x08, 0x04, 0x02, // /
    0x3E, 0x51, 0x49, 0x45, 0x3E, // 0
    0x00, 0x42, 0x7F, 0x40, 0x00, // 1
    0x42, 0x61, 0x51, 0x49, 0x46, // 2
    0x21, 0x41, 0x45, 0x4B, 0x31, // 3
    0x18, 0x14, 0x12, 0x7F, 0x10, // 4
    0x27, 0x45, 0x45, 0x45, 0x39, // 5
    0x3C, 0x4A, 0x49, 0x49, 0x30, // 6
    0x01, 0x71, 0x09, 0x05, 0x03, // 7
    0x36, 0x49, 0x49, 0x49, 0x36, // 8
    0x06, 0x49, 0x49, 0x29, 0x1E, // 9
    0x00, 0x36, 0x36, 0x00, 0x00, // :
    0x00, 0x56, 0x36, 0x00, 0x00, // ;
    0x00, 0x08, 0x14, 0x22, 0x41, // <
    0x14, 0x14, 0x14, 0x14, 0x14, // =
    0x41, 0x22, 0x14, 0x08, 0x00, // >
    0x02, 0x01, 0x51, 0x09, 0x06, // ?
    0x32, 0x49, 0x79, 0x41, 0x3E, // @
    0x7E, 0x11, 0x11, 0x11, 0x7E, // A
    0x7F, 0x49, 0x49, 0x49, 0x36, // B
    0x3E, 0x41, 0x41, 0x41, 0x22, // C
    0x7F, 0x41, 0x41, 0x22, 0x1C, // D
    0x7F, 0x49, 0x49, 0x49, 0x41, // E
    0x7F, 0x09, 0x09, 0x01, 0x01, // F
    0x3E, 0x41, 0x41, 0x51, 0x32, // G
    0x7F, 0x08, 0x08, 0x08, 0x7F, // H
    0x00, 0x41, 0x7F, 0x41, 0x00, // I
    0x20, 0x40, 0x41, 0x3F, 0x01, // J
    0x7F, 0x08, 0x14, 0x22, 0x41, // K
    0x7F, 0x40, 0x40, 0x40, 0x40, // L
    0x7F, 0x02, 0x04, 0x02, 0x7F, // M
    0x7F, 0x04, 0x08, 0x10, 0x7F, // N
    0x3E, 0x41, 0x41, 0x41, 0x3E, // O
    0x7F, 0x09, 0x09, 0x09, 0x06, // P
    0x3E, 0x41, 0x51, 0x21, 0x5E, // Q
    0x7F, 0x09, 0x19, 0x29, 0x46, // R
    0x46, 0x49, 0x49, 0x49, 0x31, // S
    0x01, 0x01, 0x7F, 0x01, 0x01, // T
    0x3F, 0x40, 0x40, 0x40, 0x3F, // U
    0x1F, 0x20, 0x40, 0x20, 0x1F, // V
    0x7F, 0x20, 0x18, 0x20, 0x7F, // W
    0x63, 0x14, 0x08, 0x14, 0x63, // X
    0x03, 0x04, 0x78, 0x04, 0x03, // Y
    0x61, 0x51, 0x49, 0x45, 0x43, // Z
    0x00, 0x00, 0x7F, 0x41, 0x41, // [
    0x02, 0x04, 0x08, 0x10, 0x20, // backslash
    0x41, 0x41, 0x7F, 0x00, 0x00, // ]
    0x04, 0x02, 0x01, 0x02, 0x04, // ^
    0x40, 0x40, 0x40, 0x40, 0x40, // _
    0x00, 0x01, 0x02, 0x04, 0x00, // `
    0x20, 0x54, 0x54, 0x54, 0x78, // a
    0x7F, 0x48, 0x44, 0x44, 0x38, // b
    0x38, 0x44, 0x44, 0x44, 0x20, // c
    0x38, 0x44, 0x44, 0x48, 0x7F, // d
    0x38, 0x54, 0x54, 0x54, 0x18, // e
    0x08, 0x7E, 0x09, 0x01, 0x02, // f
    0x08, 0x14, 0x54, 0x54, 0x3C, // g
    0x7F, 0x08, 0x04, 0x04, 0x78, // h
    0x00, 0x44, 0x7D, 0x40, 0x00, // i
    0x20, 0x40, 0x44, 0x3D, 0x00, // j
    0x00, 0x7F, 0x10, 0x28, 0x44, // k
    0x00, 0x41, 0x7F, 0x40, 0x00, // l
    0x7C, 0x04, 0x18, 0x04, 0x78, // m
    0x7C, 0x08, 0x04, 0x04, 0x78, // n
    0x38, 0x44, 0x44, 0x44, 0x38, // o
    0x7C, 0x14, 0x14, 0x14, 0x08, // p
    0x08, 0x14, 0x14, 0x18, 0x7C, // q
    0x7C, 0x08, 0x04, 0x04, 0x08, // r
    0x48, 0x54, 0x54, 0x54, 0x20, // s
    0x04, 0x3F, 0x44, 0x40, 0x20, // t
    0x3C, 0x40, 0x40, 0x20, 0x7C, // u
    0x1C, 0x20, 0x40, 0x20, 0x1C, // v
    0x3C, 0x40, 0x30, 0x40, 0x3C, // w
    0x44, 0x28, 0x10, 0x28, 0x44, // x
    0x0C, 0x50, 0x50, 0x50, 0x3C, // y
    0x44, 0x64, 0x54, 0x4C, 0x44, // z
    0x00, 0x08, 0x36, 0x41, 0x00, // {
    0x00, 0x00, 0x7F, 0x00, 0x00, // |
    0x00, 0x41, 0x36, 0x08, 0x00, // }
    0x08, 0x08, 0x2A, 0x1C, 0x08, // -> (right arrow, replacing ~)
};

// SSD1306 commands
#define SSD1306_CMD_DISPLAY_OFF         0xAE
#define SSD1306_CMD_DISPLAY_ON          0xAF
#define SSD1306_CMD_SET_CONTRAST        0x81
#define SSD1306_CMD_NORMAL_DISPLAY      0xA6
#define SSD1306_CMD_INVERT_DISPLAY      0xA7
#define SSD1306_CMD_SET_MUX_RATIO       0xA8
#define SSD1306_CMD_SET_DISPLAY_OFFSET  0xD3
#define SSD1306_CMD_SET_START_LINE      0x40
#define SSD1306_CMD_SET_SEG_REMAP       0xA0
#define SSD1306_CMD_SET_COM_SCAN_DIR    0xC0
#define SSD1306_CMD_SET_COM_PINS        0xDA
#define SSD1306_CMD_SET_CLOCK_DIV       0xD5
#define SSD1306_CMD_SET_PRECHARGE       0xD9
#define SSD1306_CMD_SET_VCOM_DESELECT   0xDB
#define SSD1306_CMD_CHARGE_PUMP         0x8D
#define SSD1306_CMD_MEMORY_MODE         0x20
#define SSD1306_CMD_SET_COL_ADDR        0x21
#define SSD1306_CMD_SET_PAGE_ADDR       0x22

// Send command to SSD1306
static esp_err_t ssd1306_cmd(uint8_t cmd)
{
    uint8_t data[2] = {0x00, cmd};  // Co=0, D/C#=0 (command)
    return i2c_master_transmit(i2c_dev, data, 2, 100);
}

// Send data to SSD1306
static esp_err_t ssd1306_data(const uint8_t *data, size_t len)
{
    // Prepend control byte (Co=0, D/C#=1 for data)
    uint8_t *buf = malloc(len + 1);
    if (!buf) return ESP_ERR_NO_MEM;

    buf[0] = 0x40;  // Data mode
    memcpy(buf + 1, data, len);

    esp_err_t ret = i2c_master_transmit(i2c_dev, buf, len + 1, 100);
    free(buf);
    return ret;
}

// Initialize SSD1306
static esp_err_t ssd1306_init(void)
{
    esp_err_t ret;

    // Init sequence for 128x64 OLED
    const uint8_t init_cmds[] = {
        SSD1306_CMD_DISPLAY_OFF,
        SSD1306_CMD_SET_CLOCK_DIV, 0x80,
        SSD1306_CMD_SET_MUX_RATIO, 0x3F,  // 64 lines
        SSD1306_CMD_SET_DISPLAY_OFFSET, 0x00,
        SSD1306_CMD_SET_START_LINE | 0x00,
        SSD1306_CMD_CHARGE_PUMP, 0x14,  // Enable charge pump
        SSD1306_CMD_MEMORY_MODE, 0x00,  // Horizontal addressing
        SSD1306_CMD_SET_SEG_REMAP | 0x01,  // Flip horizontally
        SSD1306_CMD_SET_COM_SCAN_DIR | 0x08,  // Flip vertically
        SSD1306_CMD_SET_COM_PINS, 0x12,
        SSD1306_CMD_SET_CONTRAST, 0xCF,
        SSD1306_CMD_SET_PRECHARGE, 0xF1,
        SSD1306_CMD_SET_VCOM_DESELECT, 0x40,
        SSD1306_CMD_NORMAL_DISPLAY,
        SSD1306_CMD_DISPLAY_ON,
    };

    for (size_t i = 0; i < sizeof(init_cmds); i++) {
        ret = ssd1306_cmd(init_cmds[i]);
        if (ret != ESP_OK) {
            ESP_LOGE(TAG, "SSD1306 init cmd %d failed: %s", i, esp_err_to_name(ret));
            return ret;
        }
    }

    return ESP_OK;
}

// Clear framebuffer
static void fb_clear(void)
{
    memset(framebuffer, 0, sizeof(framebuffer));
}

// Set pixel in framebuffer
static void fb_set_pixel(int x, int y, bool on)
{
    if (x < 0 || x >= DISPLAY_WIDTH || y < 0 || y >= DISPLAY_HEIGHT) return;

    int page = y / 8;
    int bit = y % 8;
    int idx = page * DISPLAY_WIDTH + x;

    if (on) {
        framebuffer[idx] |= (1 << bit);
    } else {
        framebuffer[idx] &= ~(1 << bit);
    }
}

// Draw character at position
static void fb_draw_char(int x, int y, char c, bool inverted)
{
    if (c < 32 || c > 127) c = '?';
    int idx = (c - 32) * 5;

    for (int col = 0; col < 5; col++) {
        uint8_t line = font_5x7[idx + col];
        for (int row = 0; row < 7; row++) {
            bool on = (line >> row) & 1;
            if (inverted) on = !on;
            fb_set_pixel(x + col, y + row, on);
        }
    }

    // Space between characters
    for (int row = 0; row < 7; row++) {
        fb_set_pixel(x + 5, y + row, inverted);
    }
}

// Draw string at position
static void fb_draw_string(int x, int y, const char *str, bool inverted)
{
    while (*str) {
        fb_draw_char(x, y, *str, inverted);
        x += 6;  // 5 pixels + 1 space
        str++;
    }
}

// Draw string centered horizontally
static void fb_draw_string_centered(int y, const char *str, bool inverted)
{
    int len = strlen(str);
    int x = (DISPLAY_WIDTH - len * 6) / 2;
    fb_draw_string(x, y, str, inverted);
}

// Draw large text (2x scale)
static void fb_draw_string_large(int x, int y, const char *str, bool inverted)
{
    while (*str) {
        if (*str < 32 || *str > 127) { str++; continue; }
        int idx = (*str - 32) * 5;

        for (int col = 0; col < 5; col++) {
            uint8_t line = font_5x7[idx + col];
            for (int row = 0; row < 7; row++) {
                bool on = (line >> row) & 1;
                if (inverted) on = !on;
                // Draw 2x2 block for each pixel
                fb_set_pixel(x + col*2, y + row*2, on);
                fb_set_pixel(x + col*2 + 1, y + row*2, on);
                fb_set_pixel(x + col*2, y + row*2 + 1, on);
                fb_set_pixel(x + col*2 + 1, y + row*2 + 1, on);
            }
        }
        x += 12;  // 10 pixels + 2 space
        str++;
    }
}

// Draw horizontal line
static void fb_draw_hline(int x, int y, int w)
{
    for (int i = 0; i < w; i++) {
        fb_set_pixel(x + i, y, true);
    }
}

// Draw filled rectangle
static void fb_fill_rect(int x, int y, int w, int h, bool on)
{
    for (int dy = 0; dy < h; dy++) {
        for (int dx = 0; dx < w; dx++) {
            fb_set_pixel(x + dx, y + dy, on);
        }
    }
}

// Intercom icon bitmap (24x32 pixels)
// Rounded rectangle with speaker grille lines and button
static const uint8_t intercom_icon[] = {
    // Each byte is 8 vertical pixels, LSB at top
    // 24 columns, 4 rows of bytes (32 pixels tall)
    // Row 0 (y=0-7)
    0x00, 0xE0, 0xF8, 0xFC, 0xFE, 0xFE, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFE, 0xFE, 0xFC, 0xF8, 0xE0, 0x00, 0x00, 0x00,
    // Row 1 (y=8-15)
    0x00, 0xFF, 0xFF, 0xFF, 0x00, 0x00, 0xFF, 0xFF,
    0x00, 0x00, 0xFF, 0xFF, 0x00, 0x00, 0xFF, 0xFF,
    0x00, 0x00, 0xFF, 0xFF, 0xFF, 0x00, 0x00, 0x00,
    // Row 2 (y=16-23)
    0x00, 0xFF, 0xFF, 0xFF, 0x00, 0x00, 0xFF, 0xFF,
    0x00, 0x00, 0xFF, 0xFF, 0x00, 0x00, 0xFF, 0xFF,
    0x00, 0x00, 0xFF, 0xFF, 0xFF, 0x00, 0x00, 0x00,
    // Row 3 (y=24-31)
    0x00, 0x07, 0x1F, 0x3F, 0x7F, 0x7F, 0x7F, 0x7F,
    0x7F, 0x9F, 0xCF, 0xCF, 0xCF, 0x9F, 0x7F, 0x7F,
    0x7F, 0x7F, 0x3F, 0x1F, 0x07, 0x00, 0x00, 0x00,
};

// Draw intercom icon at position
static void fb_draw_icon(int x, int y)
{
    for (int row = 0; row < 4; row++) {
        for (int col = 0; col < 24; col++) {
            uint8_t byte = intercom_icon[row * 24 + col];
            for (int bit = 0; bit < 8; bit++) {
                if (byte & (1 << bit)) {
                    fb_set_pixel(x + col, y + row * 8 + bit, true);
                }
            }
        }
    }
}

// Send framebuffer to display
static esp_err_t fb_flush(void)
{
    esp_err_t ret;

    // Set column address (0-127)
    ret = ssd1306_cmd(SSD1306_CMD_SET_COL_ADDR);
    if (ret != ESP_OK) return ret;
    ret = ssd1306_cmd(0);
    if (ret != ESP_OK) return ret;
    ret = ssd1306_cmd(127);
    if (ret != ESP_OK) return ret;

    // Set page address (0-7)
    ret = ssd1306_cmd(SSD1306_CMD_SET_PAGE_ADDR);
    if (ret != ESP_OK) return ret;
    ret = ssd1306_cmd(0);
    if (ret != ESP_OK) return ret;
    ret = ssd1306_cmd(7);
    if (ret != ESP_OK) return ret;

    // Send framebuffer
    return ssd1306_data(framebuffer, sizeof(framebuffer));
}

// Cycle button task
static TaskHandle_t cycle_task_handle = NULL;
static bool cycle_task_running = false;

static void cycle_button_task(void *arg)
{
    bool last_state = true;  // Pull-up, active low
    int64_t last_refresh = 0;

    while (cycle_task_running) {
        bool current = gpio_get_level(CYCLE_BUTTON_PIN);
        int64_t now = esp_timer_get_time() / 1000;

        // Detect press (falling edge) with debounce
        if (last_state && !current) {
            if (now - last_cycle_time > CYCLE_DEBOUNCE_MS) {
                last_cycle_time = now;
                display_cycle_next();
                ESP_LOGI(TAG, "Cycle button pressed, selected: %d", selected_index);
            }
        }

        // Periodic display refresh for scroll animation (every 150ms)
        if (current_state == DISPLAY_STATE_IDLE &&
            now - last_refresh > SCROLL_DELAY_MS) {
            last_refresh = now;
            // Check if selected room name needs scrolling
            if (room_count > 0 && selected_index < room_count) {
                int name_len = strlen(rooms[selected_index].name);
                if (name_len > MAX_VISIBLE_CHARS) {
                    display_update();
                }
            }
        }

        last_state = current;
        vTaskDelay(pdMS_TO_TICKS(20));
    }

    vTaskDelete(NULL);
}

// Public API implementation

esp_err_t display_init(void)
{
    esp_err_t ret;

    ESP_LOGI(TAG, "Initializing display (SDA=%d, SCL=%d)", DISPLAY_SDA_PIN, DISPLAY_SCL_PIN);

    // Initialize I2C master bus
    i2c_master_bus_config_t bus_config = {
        .i2c_port = I2C_NUM_0,
        .sda_io_num = DISPLAY_SDA_PIN,
        .scl_io_num = DISPLAY_SCL_PIN,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };

    ret = i2c_new_master_bus(&bus_config, &i2c_bus);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to create I2C bus: %s", esp_err_to_name(ret));
        return ret;
    }

    // Add SSD1306 device
    i2c_device_config_t dev_config = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = DISPLAY_I2C_ADDR,
        .scl_speed_hz = 400000,  // 400kHz
    };

    ret = i2c_master_bus_add_device(i2c_bus, &dev_config, &i2c_dev);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to add I2C device: %s", esp_err_to_name(ret));
        i2c_del_master_bus(i2c_bus);
        return ret;
    }

    // Probe for display
    ret = i2c_master_probe(i2c_bus, DISPLAY_I2C_ADDR, 100);
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "No display found at 0x%02X", DISPLAY_I2C_ADDR);
        i2c_master_bus_rm_device(i2c_dev);
        i2c_del_master_bus(i2c_bus);
        i2c_dev = NULL;
        i2c_bus = NULL;
        return ESP_ERR_NOT_FOUND;
    }

    // Initialize SSD1306
    ret = ssd1306_init();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "SSD1306 init failed: %s", esp_err_to_name(ret));
        i2c_master_bus_rm_device(i2c_dev);
        i2c_del_master_bus(i2c_bus);
        i2c_dev = NULL;
        i2c_bus = NULL;
        return ret;
    }

    display_available = true;
    ESP_LOGI(TAG, "Display initialized");

    // Show boot screen with underlined title
    fb_clear();
    fb_draw_string_centered(24, "HA Intercom", false);
    int title_width = 11 * 6;  // 11 chars * 6 pixels
    int title_x = (DISPLAY_WIDTH - title_width) / 2;
    fb_draw_hline(title_x, 32, title_width);  // Underline
    fb_flush();

    // Initialize cycle button
    gpio_config_t btn_conf = {
        .pin_bit_mask = (1ULL << CYCLE_BUTTON_PIN),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&btn_conf);

    // Start cycle button task (4096 stack for I2C operations)
    cycle_task_running = true;
    xTaskCreate(cycle_button_task, "cycle_btn", 4096, NULL, 5, &cycle_task_handle);

    ESP_LOGI(TAG, "Cycle button initialized (GPIO%d)", CYCLE_BUTTON_PIN);

    // Set default room
    rooms[0] = (room_target_t){
        .name = "All Rooms",
        .ip = "224.0.0.100",
        .is_multicast = true,
    };
    room_count = 1;
    selected_index = 0;

    return ESP_OK;
}

bool display_is_available(void)
{
    return display_available;
}

void display_set_rooms(const room_target_t *new_rooms, int count)
{
    if (count > MAX_ROOMS) count = MAX_ROOMS;

    room_count = count;
    memcpy(rooms, new_rooms, count * sizeof(room_target_t));

    // Reset selection if out of bounds
    if (selected_index >= room_count) {
        selected_index = 0;
    }

    ESP_LOGI(TAG, "Room list updated: %d rooms", room_count);
    display_update();
}

int display_get_room_count(void)
{
    return room_count;
}

int display_get_selected_index(void)
{
    return selected_index;
}

const room_target_t *display_get_selected_room(void)
{
    if (room_count == 0) return NULL;
    return &rooms[selected_index];
}

void display_cycle_next(void)
{
    if (room_count == 0) return;

    selected_index = (selected_index + 1) % room_count;
    display_update();
}

void display_set_state(display_state_t state)
{
    current_state = state;
    display_update();
}

void display_set_remote_name(const char *name)
{
    if (name) {
        strncpy(remote_name, name, sizeof(remote_name) - 1);
        remote_name[sizeof(remote_name) - 1] = '\0';
    } else {
        remote_name[0] = '\0';
    }
}

void display_update(void)
{
    if (!display_available) return;

    int64_t now = esp_timer_get_time() / 1000;

    fb_clear();

    // Check for temporary message
    if (temp_message[0] && (temp_message_until == 0 || now < temp_message_until)) {
        fb_draw_string_centered(28, temp_message, false);
        fb_flush();
        return;
    }
    temp_message[0] = '\0';

    switch (current_state) {
        case DISPLAY_STATE_TRANSMITTING: {
            // Show TX status with large text
            fb_draw_string_centered(8, "TRANSMITTING", false);
            fb_draw_hline(0, 20, DISPLAY_WIDTH);

            // Show target
            char buf[40];
            snprintf(buf, sizeof(buf), "~ %s", rooms[selected_index].name);
            fb_draw_string_centered(32, buf, false);
            break;
        }

        case DISPLAY_STATE_RECEIVING: {
            // Show RX status
            fb_draw_string_centered(8, "RECEIVING", false);
            fb_draw_hline(0, 20, DISPLAY_WIDTH);

            // Show source if known
            if (remote_name[0]) {
                char buf[40];
                snprintf(buf, sizeof(buf), "< %s", remote_name);
                fb_draw_string_centered(32, buf, false);
            }
            break;
        }

        case DISPLAY_STATE_ERROR: {
            fb_draw_string_centered(20, "ERROR", true);  // Inverted
            fb_draw_string_centered(36, "Check connection", false);
            break;
        }

        case DISPLAY_STATE_IDLE:
        case DISPLAY_STATE_SELECTING:
        default: {
            // Show room list with selection
            fb_draw_string_centered(0, "Target:", false);
            fb_draw_hline(0, 10, DISPLAY_WIDTH);

            // Calculate visible range (show up to 4 rooms)
            int visible_count = (room_count < 4) ? room_count : 4;
            int start_idx = 0;

            // Scroll to keep selection visible
            if (selected_index >= visible_count) {
                start_idx = selected_index - visible_count + 1;
            }

            // Handle scroll reset when selection changes
            if (selected_index != last_selected_index) {
                scroll_offset = 0;
                last_scroll_time = now;
                last_selected_index = selected_index;
            }

            for (int i = 0; i < visible_count && (start_idx + i) < room_count; i++) {
                int idx = start_idx + i;
                int y = 14 + i * 13;  // Slightly more spacing

                bool is_selected = (idx == selected_index);

                if (is_selected) {
                    // Draw larger selection box with 2px padding
                    fb_fill_rect(2, y - 2, 110, 11, true);
                    // Draw arrow
                    fb_draw_string(4, y, "~", true);  // Arrow inverted

                    // Handle scrolling for long names
                    const char *name = rooms[idx].name;
                    int name_len = strlen(name);

                    if (name_len > MAX_VISIBLE_CHARS) {
                        // Update scroll offset periodically
                        if (now - last_scroll_time > SCROLL_DELAY_MS) {
                            last_scroll_time = now;
                            scroll_offset++;
                            // Reset scroll after reaching end (with pause)
                            if (scroll_offset > name_len - MAX_VISIBLE_CHARS + 3) {
                                scroll_offset = -2;  // Pause at start
                            }
                        }

                        // Draw scrolled portion of name
                        int offset = (scroll_offset < 0) ? 0 : scroll_offset;
                        if (offset > name_len - MAX_VISIBLE_CHARS) {
                            offset = name_len - MAX_VISIBLE_CHARS;
                        }
                        fb_draw_string(16, y, name + offset, true);
                    } else {
                        fb_draw_string(16, y, name, true);
                    }
                } else {
                    // Draw room name normal (truncated if too long)
                    fb_draw_string(16, y, rooms[idx].name, false);
                }
            }

            // Scroll indicators if needed
            if (start_idx > 0) {
                fb_draw_string(DISPLAY_WIDTH - 8, 14, "^", false);
            }
            if (start_idx + visible_count < room_count) {
                fb_draw_string(DISPLAY_WIDTH - 8, 14 + 36, "v", false);
            }
            break;
        }
    }

    fb_flush();
}

void display_show_message(const char *message, uint32_t duration_ms)
{
    if (!message) return;

    strncpy(temp_message, message, sizeof(temp_message) - 1);
    temp_message[sizeof(temp_message) - 1] = '\0';

    if (duration_ms > 0) {
        temp_message_until = esp_timer_get_time() / 1000 + duration_ms;
    } else {
        temp_message_until = 0;
    }

    display_update();
}

void display_deinit(void)
{
    if (cycle_task_handle) {
        cycle_task_running = false;
        vTaskDelay(pdMS_TO_TICKS(50));
        cycle_task_handle = NULL;
    }

    if (display_available) {
        fb_clear();
        fb_flush();
        ssd1306_cmd(SSD1306_CMD_DISPLAY_OFF);
    }

    if (i2c_dev) {
        i2c_master_bus_rm_device(i2c_dev);
        i2c_dev = NULL;
    }

    if (i2c_bus) {
        i2c_del_master_bus(i2c_bus);
        i2c_bus = NULL;
    }

    display_available = false;
    ESP_LOGI(TAG, "Display deinitialized");
}

#endif // FEATURE_DISPLAY
