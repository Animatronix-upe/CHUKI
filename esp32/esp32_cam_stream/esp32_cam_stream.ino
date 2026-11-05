/*
 * esp32_cam_stream.ino
 * ─────────────────────────────────────────────────────────────
 * Stream MJPEG para AnimatrónicoOS
 * Compatible con: ESP32-S3 N16R8 + cámara OV2640
 * (Freenove ESP32-S3-WROOM CAM / ESP32-S3-EYE / similares)
 *
 * Rutas disponibles:
 *   http://<IP>:81/stream   ← stream MJPEG continuo (usa esta en Flask)
 *   http://<IP>:81/capture  ← foto única JPEG
 *   http://<IP>/            ← página de estado con la URL
 *
 * ── Configuración en Arduino IDE ────────────────────────────
 *   Placa:              ESP32S3 Dev Module
 *   Flash Size:         16MB (128Mb)
 *   Partition Scheme:   Huge APP (3MB No OTA / 1MB SPIFFS)
 *   PSRAM:              OPI PSRAM          ← MUY IMPORTANTE
 *   USB CDC On Boot:    Enabled
 *   Upload Speed:       921600
 *
 * ── Cómo flashear ───────────────────────────────────────────
 *   1. Mantén presionado el botón BOOT
 *   2. Conecta el cable USB-C a la PC
 *   3. Suelta BOOT
 *   4. Sube el sketch desde Arduino IDE
 *   5. Cuando termine, presiona RESET
 * ─────────────────────────────────────────────────────────────
 */

#include "esp_camera.h"
#include "esp_http_server.h"
#include <WiFi.h>

// ============================================================
//  ★  CAMBIA SOLO ESTAS DOS LÍNEAS  ★
// ============================================================
#define WIFI_SSID     "TU_RED_WIFI"
#define WIFI_PASSWORD "TU_CONTRASEÑA"
// ============================================================

#define STREAM_PORT 81

// ── Pines cámara OV2640 — ESP32-S3 N16R8 ─────────────────────
// (Freenove ESP32-S3-WROOM CAM / ESP32-S3-EYE)
#define PWDN_GPIO_NUM   -1
#define RESET_GPIO_NUM  -1
#define XCLK_GPIO_NUM   15
#define SIOD_GPIO_NUM    4
#define SIOC_GPIO_NUM    5
#define Y9_GPIO_NUM     16
#define Y8_GPIO_NUM     17
#define Y7_GPIO_NUM     18
#define Y6_GPIO_NUM     12
#define Y5_GPIO_NUM     10
#define Y4_GPIO_NUM      8
#define Y3_GPIO_NUM      9
#define Y2_GPIO_NUM     11
#define VSYNC_GPIO_NUM   6
#define HREF_GPIO_NUM    7
#define PCLK_GPIO_NUM   13
// ─────────────────────────────────────────────────────────────

static httpd_handle_t stream_server = NULL;

// ── Handler: stream MJPEG continuo ───────────────────────────
static esp_err_t stream_handler(httpd_req_t *req) {
  camera_fb_t *fb     = NULL;
  esp_err_t    res    = ESP_OK;
  uint8_t     *jpg    = NULL;
  size_t       jpgLen = 0;
  char         hdr[64];

  httpd_resp_set_type(req, "multipart/x-mixed-replace;boundary=frame");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  httpd_resp_set_hdr(req, "Cache-Control", "no-cache");

  while (true) {
    fb = esp_camera_fb_get();
    if (!fb) { res = ESP_FAIL; break; }

    if (fb->format != PIXFORMAT_JPEG) {
      bool ok = frame2jpg(fb, 80, &jpg, &jpgLen);
      esp_camera_fb_return(fb); fb = NULL;
      if (!ok) { res = ESP_FAIL; break; }
    } else {
      jpg    = fb->buf;
      jpgLen = fb->len;
    }

    size_t hLen = snprintf(hdr, sizeof(hdr),
      "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n",
      (unsigned)jpgLen);

    res = httpd_resp_send_chunk(req, hdr, hLen);
    if (res == ESP_OK) res = httpd_resp_send_chunk(req, (const char*)jpg, jpgLen);
    if (res == ESP_OK) res = httpd_resp_send_chunk(req, "\r\n", 2);

    if (fb) { esp_camera_fb_return(fb); fb = NULL; }
    else    { free(jpg); jpg = NULL; }

    if (res != ESP_OK) break;
  }
  return res;
}

// ── Handler: captura única ────────────────────────────────────
static esp_err_t capture_handler(httpd_req_t *req) {
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) return ESP_FAIL;
  httpd_resp_set_type(req, "image/jpeg");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  esp_err_t res = httpd_resp_send(req, (const char*)fb->buf, fb->len);
  esp_camera_fb_return(fb);
  return res;
}

// ── Handler: página de estado ─────────────────────────────────
static esp_err_t index_handler(httpd_req_t *req) {
  char ip[24];
  strcpy(ip, WiFi.localIP().toString().c_str());
  char html[600];
  snprintf(html, sizeof(html),
    "<!DOCTYPE html><html><head><meta charset='UTF-8'></head>"
    "<body style='font-family:monospace;background:#0a0c12;color:#00d4ff;padding:24px'>"
    "<h3>ESP32-S3 N16R8 &mdash; AnimatrónicoOS</h3>"
    "<p><b>PSRAM:</b> %s</p>"
    "<p><b>Stream URL</b> (copia en la app):</p>"
    "<p style='color:#ffaa00;font-size:1.1em'>http://%s:%d/stream</p>"
    "<p><a href='/capture' style='color:#00e676'>📷 Ver captura</a></p>"
    "</body></html>",
    psramFound() ? "OK (8MB OPI)" : "NO detectado",
    ip, STREAM_PORT, ip, STREAM_PORT);
  httpd_resp_set_type(req, "text/html");
  return httpd_resp_send(req, html, strlen(html));
}

// ── Iniciar servidor ──────────────────────────────────────────
void startStreamServer() {
  httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
  cfg.server_port = STREAM_PORT;
  cfg.ctrl_port   = STREAM_PORT + 1000;
  cfg.max_uri_handlers = 4;

  httpd_uri_t uris[] = {
    { "/stream",  HTTP_GET, stream_handler,  NULL },
    { "/capture", HTTP_GET, capture_handler, NULL },
    { "/",        HTTP_GET, index_handler,   NULL },
  };

  if (httpd_start(&stream_server, &cfg) == ESP_OK)
    for (int i = 0; i < 3; i++)
      httpd_register_uri_handler(stream_server, &uris[i]);
}

// ── Setup ─────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  Serial.println("\n━━━ ESP32-S3 N16R8 AnimatrónicoOS ━━━");

  camera_config_t cam;
  cam.ledc_channel = LEDC_CHANNEL_0;
  cam.ledc_timer   = LEDC_TIMER_0;
  cam.pin_d0       = Y2_GPIO_NUM;
  cam.pin_d1       = Y3_GPIO_NUM;
  cam.pin_d2       = Y4_GPIO_NUM;
  cam.pin_d3       = Y5_GPIO_NUM;
  cam.pin_d4       = Y6_GPIO_NUM;
  cam.pin_d5       = Y7_GPIO_NUM;
  cam.pin_d6       = Y8_GPIO_NUM;
  cam.pin_d7       = Y9_GPIO_NUM;
  cam.pin_xclk     = XCLK_GPIO_NUM;
  cam.pin_pclk     = PCLK_GPIO_NUM;
  cam.pin_vsync    = VSYNC_GPIO_NUM;
  cam.pin_href     = HREF_GPIO_NUM;
  cam.pin_sscb_sda = SIOD_GPIO_NUM;
  cam.pin_sscb_scl = SIOC_GPIO_NUM;
  cam.pin_pwdn     = PWDN_GPIO_NUM;
  cam.pin_reset    = RESET_GPIO_NUM;
  cam.xclk_freq_hz = 20000000;
  cam.pixel_format = PIXFORMAT_JPEG;

  // Con 8MB PSRAM podemos usar resolución más alta y más buffers
  if (psramFound()) {
    Serial.println("PSRAM detectado — modo alta calidad");
    cam.frame_size   = FRAMESIZE_VGA;   // 640×480 — ideal para detección
    cam.jpeg_quality = 10;              // mejor calidad (0-63)
    cam.fb_count     = 2;
    cam.grab_mode    = CAMERA_GRAB_LATEST;
    cam.fb_location  = CAMERA_FB_IN_PSRAM;
  } else {
    Serial.println("Sin PSRAM — modo básico");
    cam.frame_size   = FRAMESIZE_QVGA;  // 320×240
    cam.jpeg_quality = 12;
    cam.fb_count     = 1;
    cam.grab_mode    = CAMERA_GRAB_WHEN_EMPTY;
    cam.fb_location  = CAMERA_FB_IN_DRAM;
  }

  esp_err_t err = esp_camera_init(&cam);
  if (err != ESP_OK) {
    Serial.printf("ERROR cámara: 0x%x\n", err);
    Serial.println("Verifica que el cable ribbon está bien conectado.");
    while (true) delay(1000);
  }
  Serial.println("Cámara OV2640 OK");

  // Ajustes opcionales de imagen — descomenta para tunear:
  // sensor_t *s = esp_camera_sensor_get();
  // s->set_hmirror(s, 1);    // espejo horizontal
  // s->set_vflip(s, 1);      // voltear vertical
  // s->set_brightness(s, 1); // brillo (-2 a 2)
  // s->set_contrast(s, 1);   // contraste (-2 a 2)

  // Conectar WiFi (red abierta: deja WIFI_PASSWORD como "")
  if (strlen(WIFI_PASSWORD) == 0)
    WiFi.begin(WIFI_SSID);
  else
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("WiFi");
  int t = 0;
  while (WiFi.status() != WL_CONNECTED && t++ < 40) {
    delay(500); Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("ERROR: No se conectó al WiFi. Verifica SSID y contraseña.");
    while (true) delay(1000);
  }

  Serial.println("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
  Serial.printf("  IP:         %s\n",    WiFi.localIP().toString().c_str());
  Serial.printf("  Stream URL: http://%s:%d/stream\n",
                WiFi.localIP().toString().c_str(), STREAM_PORT);
  Serial.println("  ↑ Pega esta URL en la app → 📡 ESP32-CAM → Conectar");
  Serial.println("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");

  startStreamServer();
  Serial.println("Servidor iniciado. Listo.");
}

void loop() {
  // Reconectar WiFi si se pierde
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi perdido, reconectando...");
    WiFi.reconnect();
    delay(5000);
  }
  delay(10000);
}
