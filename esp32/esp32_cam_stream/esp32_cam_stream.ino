/*
 * esp32_cam_stream.ino
 * ─────────────────────────────────────────────────────────────
 * Stream MJPEG para AnimatronicOS  —  ESP32-S3 N16R8 + OV2640
 *
 * Funciones:
 *   http://<IP>:81/stream   <- stream MJPEG (usa esta en Flask)
 *   http://<IP>:81/capture  <- foto única JPEG
 *   http://<IP>:81/wifi     <- GET info | POST ssid=..&pass=..&flask=..
 *   http://animatronico.local:81/stream  <- URL fija (mDNS)
 *
 * Al conectar a WiFi nuevo el ESP32 avisa automáticamente a Flask
 * con su nueva IP y Flask actualiza la URL del stream.
 *
 * ── Config Arduino IDE ───────────────────────────────────────
 *   Placa:            ESP32S3 Dev Module
 *   Partition Scheme: Huge APP (3MB No OTA / 1MB SPIFFS)
 *   PSRAM:            OPI PSRAM   <- MUY IMPORTANTE
 *   USB CDC On Boot:  Enabled
 * ─────────────────────────────────────────────────────────────
 */

#include "esp_camera.h"
#include "esp_http_server.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <ESPmDNS.h>
#include <Preferences.h>

// ── Credenciales WiFi ─────────────────────────────────────────
// Estas se usan si NVS está vacío o si FORCE_RESET_CREDENTIALS = true
#define DEFAULT_SSID     "mayumi"
#define DEFAULT_PASSWORD "janetteamo"
#define DEFAULT_FLASK    "10.71.77.231:5000"  // IP del laptop en red mayumi (Wi-Fi)

// Descomenta la siguiente línea para forzar uso de DEFAULT_SSID/PASSWORD
// (borra credenciales guardadas en NVS y usa las de arriba)
#define FORCE_RESET_CREDENTIALS

#define STREAM_PORT 81
#define MDNS_NAME   "animatronico"

// ── Pines OV2640 — ESP32-S3 N16R8 ────────────────────────────
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

static httpd_handle_t stream_server = NULL;
static Preferences prefs;
static char wifi_ssid[64];
static char wifi_pass[64];
static char flask_host[64];
static bool should_restart = false;

// ── Cargar credenciales desde NVS ─────────────────────────────
void loadConfig() {
#ifdef FORCE_RESET_CREDENTIALS
  prefs.begin("cfg", false);
  prefs.putString("ssid",  DEFAULT_SSID);
  prefs.putString("pass",  DEFAULT_PASSWORD);
  prefs.end();
  Serial.println("Credenciales reseteadas a DEFAULT.");
#endif
  prefs.begin("cfg", true);
  String s = prefs.getString("ssid",  DEFAULT_SSID);
  String p = prefs.getString("pass",  DEFAULT_PASSWORD);
  String f = prefs.getString("flask", DEFAULT_FLASK);
  prefs.end();
  strncpy(wifi_ssid,  s.c_str(), 63);
  strncpy(wifi_pass,  p.c_str(), 63);
  strncpy(flask_host, f.c_str(), 63);
}

// ── URL-decode ─────────────────────────────────────────────────
void urlDecode(char *dst, const char *src, int maxLen) {
  int i = 0, j = 0;
  while (src[i] && j < maxLen - 1) {
    if (src[i] == '%' && src[i+1] && src[i+2]) {
      char hex[3] = { src[i+1], src[i+2], 0 };
      dst[j++] = (char)strtol(hex, NULL, 16);
      i += 3;
    } else if (src[i] == '+') {
      dst[j++] = ' '; i++;
    } else {
      dst[j++] = src[i++];
    }
  }
  dst[j] = '\0';
}

// ── Avisar a Flask con la IP actual ───────────────────────────
void announceToFlask() {
  if (strlen(flask_host) == 0) return;
  String ip  = WiFi.localIP().toString();
  String url = "http://" + String(flask_host) + "/api/esp32_announce";
  String body = "{\"ip\":\"" + ip + "\",\"port\":" + String(STREAM_PORT) + "}";
  HTTPClient http;
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  int code = http.POST(body);
  http.end();
  Serial.printf("Anuncio a Flask %s -> HTTP %d\n", flask_host, code);
}

// ── Handler: stream MJPEG optimizado ─────────────────────────
// Target: ~25-30 fps fluidos, frame siempre reciente
static esp_err_t stream_handler(httpd_req_t *req) {
  camera_fb_t *fb  = NULL;
  esp_err_t   res  = ESP_OK;
  uint8_t    *jpg  = NULL;
  size_t      jpgLen = 0;
  char        hdr[64];
  TickType_t  last_frame = 0;
  const int   FRAME_MS   = 33; // ~30 fps cap — evita saturar la red WiFi

  httpd_resp_set_type(req, "multipart/x-mixed-replace;boundary=frame");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  httpd_resp_set_hdr(req, "Cache-Control", "no-cache");
  httpd_resp_set_hdr(req, "Pragma",        "no-cache");
  httpd_resp_set_hdr(req, "Connection",    "close");

  while (true) {
    // Esperar para respetar el cap de FPS
    TickType_t now     = xTaskGetTickCount();
    int        elapsed = (int)((now - last_frame) * portTICK_PERIOD_MS);
    if (elapsed < FRAME_MS && last_frame > 0)
      vTaskDelay(pdMS_TO_TICKS(FRAME_MS - elapsed));
    last_frame = xTaskGetTickCount();

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

// ── Handler: captura única ─────────────────────────────────────
static esp_err_t capture_handler(httpd_req_t *req) {
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) return ESP_FAIL;
  httpd_resp_set_type(req, "image/jpeg");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  esp_err_t res = httpd_resp_send(req, (const char*)fb->buf, fb->len);
  esp_camera_fb_return(fb);
  return res;
}

// ── Handler: configuración WiFi (GET info / POST cambiar) ─────
static esp_err_t wifi_handler(httpd_req_t *req) {
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");

  if (req->method == HTTP_GET) {
    char buf[256];
    snprintf(buf, sizeof(buf),
      "{\"ssid\":\"%s\",\"ip\":\"%s\",\"mdns\":\"" MDNS_NAME ".local\"}",
      wifi_ssid, WiFi.localIP().toString().c_str());
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_send(req, buf, strlen(buf));
  }

  char body[300] = {0};
  int recv = httpd_req_recv(req, body, sizeof(body) - 1);
  if (recv <= 0) { httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "No body"); return ESP_FAIL; }
  body[recv] = '\0';

  auto parseField = [&](const char *key, char *out, int maxLen) {
    char enc[128] = {0};
    char *p = strstr(body, key);
    if (!p) return;
    p += strlen(key);
    char *end = strchr(p, '&');
    int len = end ? (int)(end - p) : (int)strlen(p);
    if (len > 127) len = 127;
    strncpy(enc, p, len);
    urlDecode(out, enc, maxLen);
  };

  char new_ssid[64]={0}, new_pass[64]={0}, new_flask[64]={0};
  parseField("ssid=",  new_ssid,  64);
  parseField("pass=",  new_pass,  64);
  parseField("flask=", new_flask, 64);

  if (strlen(new_ssid) == 0) {
    httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "ssid requerido");
    return ESP_FAIL;
  }

  prefs.begin("cfg", false);
  prefs.putString("ssid",  new_ssid);
  prefs.putString("pass",  new_pass);
  if (strlen(new_flask) > 0) prefs.putString("flask", new_flask);
  prefs.end();

  Serial.printf("WiFi actualizado: %s — reiniciando...\n", new_ssid);
  const char *resp = "{\"ok\":true,\"msg\":\"Reiniciando con nueva red\"}";
  httpd_resp_set_type(req, "application/json");
  httpd_resp_send(req, resp, strlen(resp));
  should_restart = true;
  return ESP_OK;
}

// ── Handler: página de estado ──────────────────────────────────
static esp_err_t index_handler(httpd_req_t *req) {
  String ip = WiFi.localIP().toString();
  char html[800];
  snprintf(html, sizeof(html),
    "<!DOCTYPE html><html><head><meta charset='UTF-8'>"
    "<meta name='viewport' content='width=device-width'></head>"
    "<body style='font-family:monospace;background:#0a0c12;color:#00d4ff;padding:24px'>"
    "<h3>AnimatronicOS &mdash; ESP32-S3</h3>"
    "<p><b>Red:</b> %s</p>"
    "<p><b>IP:</b> %s</p>"
    "<p><b>Stream URL:</b></p>"
    "<p style='color:#ffaa00'>http://%s:%d/stream</p>"
    "<p style='color:#aaa'>o por nombre fijo:</p>"
    "<p style='color:#ffaa00'>" MDNS_NAME ".local:%d/stream</p>"
    "<p><a href='/capture' style='color:#00e676'>Ver captura</a></p>"
    "</body></html>",
    wifi_ssid, ip.c_str(), ip.c_str(), STREAM_PORT, STREAM_PORT);
  httpd_resp_set_type(req, "text/html");
  return httpd_resp_send(req, html, strlen(html));
}

// ── Iniciar servidor HTTP ──────────────────────────────────────
void startStreamServer() {
  httpd_config_t cfg      = HTTPD_DEFAULT_CONFIG();
  cfg.server_port         = STREAM_PORT;
  cfg.ctrl_port           = STREAM_PORT + 1000;
  cfg.max_uri_handlers    = 8;
  cfg.task_priority       = tskIDLE_PRIORITY + 5; // prioridad alta para el server
  cfg.stack_size          = 8192;                  // más stack para el handler de stream
  if (httpd_start(&stream_server, &cfg) != ESP_OK) return;

  httpd_uri_t uris[] = {
    { "/stream",  HTTP_GET,  stream_handler,  NULL },
    { "/capture", HTTP_GET,  capture_handler, NULL },
    { "/wifi",    HTTP_GET,  wifi_handler,    NULL },
    { "/wifi",    HTTP_POST, wifi_handler,    NULL },
    { "/",        HTTP_GET,  index_handler,   NULL },
  };
  for (int i = 0; i < 5; i++)
    httpd_register_uri_handler(stream_server, &uris[i]);
}

// ── Setup ──────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  Serial.println("\n=== AnimatronicOS ESP32-S3 ===");

  loadConfig();

  // ── Configurar cámara OV2640 ──────────────────────────────
  camera_config_t cam;
  cam.ledc_channel = LEDC_CHANNEL_0;
  cam.ledc_timer   = LEDC_TIMER_0;
  cam.pin_d0=Y2_GPIO_NUM; cam.pin_d1=Y3_GPIO_NUM; cam.pin_d2=Y4_GPIO_NUM;
  cam.pin_d3=Y5_GPIO_NUM; cam.pin_d4=Y6_GPIO_NUM; cam.pin_d5=Y7_GPIO_NUM;
  cam.pin_d6=Y8_GPIO_NUM; cam.pin_d7=Y9_GPIO_NUM;
  cam.pin_xclk=XCLK_GPIO_NUM; cam.pin_pclk=PCLK_GPIO_NUM;
  cam.pin_vsync=VSYNC_GPIO_NUM; cam.pin_href=HREF_GPIO_NUM;
  cam.pin_sscb_sda=SIOD_GPIO_NUM; cam.pin_sscb_scl=SIOC_GPIO_NUM;
  cam.pin_pwdn=PWDN_GPIO_NUM; cam.pin_reset=RESET_GPIO_NUM;

  // 24 MHz: 20% más throughput que 20 MHz → más FPS reales
  cam.xclk_freq_hz = 24000000;
  cam.pixel_format = PIXFORMAT_JPEG;

  if (psramFound()) {
    // PSRAM disponible: máxima calidad y buffers
    cam.frame_size   = FRAMESIZE_VGA;   // 640×480 — ideal para detección facial
    cam.jpeg_quality = 8;               // 1=máx calidad, 63=mín; 8 es excelente
    cam.fb_count     = 3;               // 3 buffers = streaming muy fluido
    cam.grab_mode    = CAMERA_GRAB_LATEST; // siempre el frame más reciente
    cam.fb_location  = CAMERA_FB_IN_PSRAM;
    Serial.println("PSRAM detectada — config alta calidad (VGA, q=8, 3 buffers)");
  } else {
    cam.frame_size   = FRAMESIZE_QVGA;  // 320×240 sin PSRAM
    cam.jpeg_quality = 12;
    cam.fb_count     = 1;
    cam.grab_mode    = CAMERA_GRAB_WHEN_EMPTY;
    cam.fb_location  = CAMERA_FB_IN_DRAM;
    Serial.println("Sin PSRAM — config reducida (QVGA, q=12, 1 buffer)");
  }

  if (esp_camera_init(&cam) != ESP_OK) {
    Serial.println("ERROR FATAL: cámara no iniciada");
    while (true) delay(1000);
  }

  // ── Ajustes finos del sensor OV2640 ──────────────────────
  // Mejoran la imagen para detección facial y reducen ruido
  sensor_t *s = esp_camera_sensor_get();
  if (s) {
    s->set_brightness(s,  1);   // +1 brillo  (-2..2)
    s->set_contrast(s,    1);   // +1 contraste  (-2..2) — mejor detección de bordes
    s->set_saturation(s,  0);   // neutro  (-2..2)
    s->set_sharpness(s,   1);   // +1 nitidez  (-2..2)
    s->set_denoise(s,     1);   // reducción de ruido ON
    s->set_ae_level(s,    0);   // exposición automática centrada  (-2..2)
    s->set_awb_gain(s,    1);   // ganancia de balance de blancos ON
    s->set_wb_mode(s,     0);   // balance de blancos auto (0=auto,1=sunny,2=cloudy,3=office,4=home)
    s->set_gainceiling(s, (gainceiling_t)2); // techo de ganancia 4x
    s->set_lenc(s,        1);   // corrección de lente ON
    s->set_hmirror(s,     0);   // sin espejo horizontal (cambiar a 1 si la imagen está invertida)
    s->set_vflip(s,       0);   // sin volteo vertical (cambiar a 1 si está al revés)
    Serial.println("Sensor OV2640 configurado (brillo/contraste/nitidez optimizados)");
  }

  // ── WiFi — deshabilitar sleep para latencia mínima ────────
  Serial.printf("Conectando a WiFi: %s\n", wifi_ssid);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);                   // sin sleep = latencia ~1ms (vs ~20ms con sleep)
  WiFi.setAutoReconnect(true);
  WiFi.setTxPower(WIFI_POWER_19_5dBm);   // máxima potencia de transmisión
  WiFi.begin(wifi_ssid, strlen(wifi_pass) ? wifi_pass : NULL);

  int t = 0;
  while (WiFi.status() != WL_CONNECTED) {
    delay(500); Serial.print(".");
    if (++t % 20 == 0) {
      Serial.printf("\nReintentando WiFi (%s)...\n", wifi_ssid);
      WiFi.disconnect();
      delay(500);
      WiFi.begin(wifi_ssid, strlen(wifi_pass) ? wifi_pass : NULL);
    }
    if (t > 120) {
      Serial.println("\nATENCION: sin WiFi. Verifica SSID y contraseña.");
      t = 0;
    }
  }
  Serial.println();

  // ── mDNS: accesible como animatronico.local ───────────────
  if (MDNS.begin(MDNS_NAME)) {
    MDNS.addService("http", "tcp", STREAM_PORT);
    Serial.println("mDNS: " MDNS_NAME ".local");
  }

  String ip = WiFi.localIP().toString();
  Serial.println("======================================================");
  Serial.printf("  Red:        %s\n",                  wifi_ssid);
  Serial.printf("  IP:         %s\n",                  ip.c_str());
  Serial.printf("  Stream:     http://%s:%d/stream\n", ip.c_str(), STREAM_PORT);
  Serial.println("  URL fija:   http://" MDNS_NAME ".local:" + String(STREAM_PORT) + "/stream");
  Serial.printf("  Calidad:    VGA 640x480 | JPEG q=8 | 3 buf | 24MHz\n");
  Serial.println("======================================================");

  startStreamServer();

  delay(500);
  announceToFlask();

  Serial.println("Listo — stream activo.");
}

void loop() {
  if (should_restart) {
    delay(500);
    ESP.restart();
  }
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi perdido, reconectando...");
    WiFi.reconnect();
    delay(5000);
  }
  delay(10000);
}
