/**
 * app.js - Lógica de la interfaz web del Animatrónico
 *
 * Comunicación con el servidor Flask mediante fetch() (REST).
 * Actualiza la UI con polling cada 800ms.
 */

// ================================================================
// Estado local
// ================================================================
let currentMode = 'rest';
let manualAngle = 90;
let moveInterval = null;          // para los botones de dirección
let settingsDirty = false;
let calibInProgress = false;

// ================================================================
// Inicialización
// ================================================================
document.addEventListener('DOMContentLoaded', () => {
  loadSettings();
  refreshPorts();
  _loadESP32Config();
  startPolling();
});

// ================================================================
// Polling de estado
// ================================================================
function startPolling() {
  updateStatus();
  setInterval(updateStatus, 800);
  setInterval(updateEvents, 2000);
}

async function updateStatus() {
  try {
    const data = await apiFetch('/api/status');
    if (!data) return;

    // Arduino
    const arduinoDot = document.getElementById('dot-arduino');
    const connInfo   = document.getElementById('connection-info-text');
    if (data.arduino.connected) {
      setDot(arduinoDot, 'ok');
      connInfo.textContent = `Conectado: ${data.arduino.port}`;
    } else {
      setDot(arduinoDot, 'error');
      connInfo.textContent = 'Sin conexión';
    }

    // Cámara
    const camDot = document.getElementById('dot-camera');
    const camBadge = document.getElementById('camera-badge');
    if (data.camera.connected) {
      setDot(camDot, 'ok');
      camBadge.textContent = 'ACTIVA';
      camBadge.className = 'camera-badge';
    } else {
      setDot(camDot, 'error');
      camBadge.textContent = 'OFFLINE';
      camBadge.className = 'camera-badge offline';
    }

    // Botones fuente de cámara — sincronizar con estado del servidor
    const esp32Url = data.camera?.esp32_url ?? '';
    const btnEsp32  = document.getElementById('btn-src-esp32');
    const btnLaptop = document.getElementById('btn-src-laptop');
    if (btnEsp32 && btnLaptop && !btnEsp32.textContent.includes('Conectando')) {
      const isEsp32 = !!esp32Url;
      btnEsp32.classList.toggle('active', isEsp32);
      btnLaptop.classList.toggle('active', !isEsp32);
    }
    // Banner de estado ESP32
    if (esp32Url) {
      const shortUrl = esp32Url.replace(/^https?:\/\//, '');
      if (data.camera.connected) {
        setESP32ConnBanner('ok', '✔ ESP32-CAM Conectado', shortUrl);
      } else {
        setESP32ConnBanner('error', '✘ ESP32-CAM Sin señal', shortUrl + ' — sin respuesta');
      }
    } else if (!btnEsp32?.textContent.includes('Conectando')) {
      setESP32ConnBanner('', 'ESP32-CAM — Sin conexión', 'Ingresa la URL del stream y presiona Conectar');
    }

    // Rostro detectado
    const faceDot = document.getElementById('dot-face');
    const faceDetected = data.gaze?.face_detected;
    setDot(faceDot, faceDetected ? 'ok' : 'warn');

    // Modo
    if (data.mode !== currentMode) {
      currentMode = data.mode;
      updateModeUI(currentMode);
    }

    // Ángulo del servo
    const angle = data.movement?.current_angle ?? 90;
    updateServoDisplay(angle, data.movement);

    // Cuello
    const neckAngle = data.neck?.angle ?? data.movement?.neck_angle ?? 55;
    const neckSync  = data.neck?.sync  ?? data.movement?.neck_sync  ?? true;
    const neckMode  = data.neck?.mode  ?? data.movement?.neck_mode  ?? 'user';
    updateNeckDisplay(neckAngle, neckSync, neckMode);

    // Cámara estado texto
    const camText = document.getElementById('camera-state-text');
    camText.textContent = data.camera.connected
      ? (faceDetected ? '● Rostro detectado' : '○ Buscando rostro...')
      : 'Cámara no disponible';

    // LEDs — sincronizar botón con estado real del servidor
    if (typeof data.led_on === 'boolean' && data.led_on !== ledOn) {
      ledOn = data.led_on;
      updateLEDButton();
    }

    // Calibración
    if (data.gaze?.calibrated) {
      document.getElementById('calib-saved-text').textContent =
        '✓ Calibración guardada';
    }

  } catch (_) { /* silencioso – la conexión puede no estar lista */ }
}

async function updateEvents() {
  try {
    const data = await apiFetch('/api/events');
    if (!data?.events) return;
    const log = document.getElementById('event-log');
    log.innerHTML = data.events.slice(0, 20).map(e => `
      <div class="log-entry log-${e.level}">
        <span class="log-time">${e.time}</span>${e.message}
      </div>`
    ).join('');
  } catch (_) {}
}

// ================================================================
// Modo
// ================================================================
async function setMode(mode) {
  const result = await apiFetch('/api/mode', 'POST', { mode });
  if (result?.success) {
    currentMode = mode;
    updateModeUI(mode);
    showToast(`Modo: ${modeLabel(mode)}`, 'success');

    // Mostrar/ocultar controles manuales
    const manualControls = document.getElementById('manual-controls');
    manualControls.style.display = mode === 'manual' ? 'block' : 'block';
    // (siempre visible pero los botones sólo tienen efecto en modo manual)
  }
}

function updateModeUI(mode) {
  const modes = ['gaze', 'motion', 'manual', 'rest'];
  modes.forEach(m => {
    document.getElementById(`mode-${m}`)?.classList.toggle('active', m === mode);
  });
  const badge = document.getElementById('active-mode-badge');
  badge.textContent = modeLabel(mode).toUpperCase();
}

function modeLabel(mode) {
  return { gaze: 'Seguimiento', motion: 'Movimiento', manual: 'Manual', rest: 'Reposo' }[mode] || mode;
}

// ================================================================
// Control manual
// ================================================================
function startMove(direction) {
  if (moveInterval) return;
  moveInterval = setInterval(() => {
    if (currentMode !== 'manual') return;
    manualAngle += direction === 'left' ? -2 : 2;
    manualAngle = Math.max(
      parseInt(document.getElementById('set-servo-min').value || 50),
      Math.min(parseInt(document.getElementById('set-servo-max').value || 130), manualAngle)
    );
    document.getElementById('manual-slider').value = manualAngle;
    document.getElementById('manual-angle-display').textContent = manualAngle + '°';
    apiFetch('/api/position', 'POST', { angle: manualAngle });
  }, 80);
}

function stopMove() {
  clearInterval(moveInterval);
  moveInterval = null;
}

function onManualSlider(value) {
  manualAngle = parseInt(value);
  document.getElementById('manual-angle-display').textContent = value + '°';
  if (currentMode === 'manual') {
    apiFetch('/api/position', 'POST', { angle: manualAngle });
  }
}

async function centerEyes() {
  await apiFetch('/api/center', 'POST', {});
  manualAngle = 90;
  document.getElementById('manual-slider').value = 90;
  document.getElementById('manual-angle-display').textContent = '90°';
  showToast('Ojos centrados', 'success');
}

async function emergencyStop() {
  await apiFetch('/api/stop', 'POST', {});
  currentMode = 'rest';
  updateModeUI('rest');
  showToast('¡PARADA DE EMERGENCIA!', 'error');
}

// ================================================================
// LEDs de los ojos
// ================================================================
let ledOn = true;

async function toggleLED() {
  ledOn = !ledOn;
  const result = await apiFetch('/api/led', 'POST', { state: ledOn });
  if (result?.success !== undefined) {
    ledOn = result.state;
  }
  updateLEDButton();
  showToast(ledOn ? 'LEDs encendidos' : 'LEDs apagados', ledOn ? 'success' : 'info');
}

function updateLEDButton() {
  const btn  = document.getElementById('btn-led');
  const text = document.getElementById('led-state-text');
  if (!btn || !text) return;
  text.textContent = ledOn ? 'ON' : 'OFF';
  btn.classList.toggle('btn-led-on',  ledOn);
  btn.classList.toggle('btn-led-off', !ledOn);
}

// ================================================================
// Neck controls
// ================================================================
function onNeckSyncToggle(enabled) {
  apiFetch('/api/neck_sync', 'POST', { enabled });
  const wrap = document.getElementById('neck-manual-slider-wrap');
  if (wrap) wrap.style.display = enabled ? 'none' : 'block';
  showToast(enabled ? 'Cuello sincronizado con ojos' : 'Cuello: control independiente', 'info');
}

function onNeckSlider(value) {
  const angle = parseInt(value);
  const el = document.getElementById('neck-manual-angle-display');
  if (el) el.textContent = angle + '°';
  _updateNeckBar(angle);
  apiFetch('/api/neck_position', 'POST', { angle });
}

function setNeckPos(angle) {
  const slider = document.getElementById('neck-manual-slider');
  if (slider) { slider.value = angle; }
  const el = document.getElementById('neck-manual-angle-display');
  if (el) el.textContent = angle + '°';
  _updateNeckBar(angle);
  apiFetch('/api/neck_position', 'POST', { angle });
}

function _updateNeckBar(angle) {
  const pct = (angle / 110) * 100;
  const fill = document.getElementById('neck-bar-fill');
  const dot  = document.getElementById('neck-bar-dot');
  const disp = document.getElementById('neck-angle-display2');
  if (fill) fill.style.width = pct + '%';
  if (dot)  dot.style.left  = pct + '%';
  if (disp) disp.textContent = angle + '°';
}

function updateNeckDisplay(angle, sync, neckMode) {
  _updateNeckBar(angle);
  const angleEl = document.getElementById('neck-angle-display');
  if (angleEl) angleEl.textContent = angle + '°';

  const indicator = document.getElementById('neck-servo-indicator');
  const range     = document.getElementById('neck-servo-range');
  const pct = ((angle - 0) / 110) * 100;
  if (indicator) indicator.style.left  = `${Math.max(0, Math.min(100, pct))}%`;
  if (range)     range.style.width     = `${Math.max(0, Math.min(100, pct))}%`;

  let syncLabel;
  if (neckMode === 'gaze_locked') {
    syncLabel = '🔒 Bloqueado — solo ojos en modo mirada';
  } else if (neckMode === 'motion_delayed') {
    syncLabel = '⏱ Retardo 5s — modo detección';
  } else {
    syncLabel = sync ? '● Sincronizado con ojos' : '○ Control independiente';
  }
  const statusText = document.getElementById('neck-sync-status-text');
  if (statusText) statusText.textContent = syncLabel;
  const ctrlText = document.getElementById('neck-ctrl-sync-text');
  if (ctrlText) ctrlText.textContent = syncLabel;

  // Deshabilitar toggle de sync en modos automáticos
  const toggle = document.getElementById('neck-sync-toggle');
  if (toggle) {
    toggle.disabled = (neckMode === 'gaze_locked' || neckMode === 'motion_delayed');
    if (toggle.checked !== sync) toggle.checked = sync;
    const wrap = document.getElementById('neck-manual-slider-wrap');
    if (wrap) wrap.style.display = sync ? 'none' : 'block';
  }
}

// ================================================================
// Servo display
// ================================================================
function updateServoDisplay(angle, movement) {
  document.getElementById('servo-angle-display').textContent = angle + '°';
  document.getElementById('servo-position-display').textContent = `Servo: ${angle}°`;

  const min    = movement?.servo_min ?? 50;
  const max    = movement?.servo_max ?? 130;
  const center = (movement?.servo_center ?? 90);
  const range  = max - min;

  const pct = range > 0 ? ((angle - min) / range) * 100 : 50;
  const indicatorEl = document.getElementById('servo-indicator');
  if (indicatorEl) indicatorEl.style.left = `${Math.max(0, Math.min(100, pct))}%`;

  const rangeEl = document.querySelector('.servo-range');
  if (rangeEl) rangeEl.style.width = `${Math.max(0, Math.min(100, pct))}%`;
}

// ================================================================
// Puertos seriales
// ================================================================
async function refreshPorts() {
  const btn = document.querySelector('.btn-refresh-ports');
  btn.classList.add('spinning');
  try {
    const data = await apiFetch('/api/ports');
    const select = document.getElementById('port-select');
    select.innerHTML = '<option value="">Seleccionar puerto...</option>';
    (data?.ports || []).forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.port;
      opt.textContent = `${p.port} — ${p.description}`;
      select.appendChild(opt);
    });
    if (!data?.ports?.length) {
      select.innerHTML += '<option disabled>No se encontraron puertos</option>';
    }
  } catch (_) {}
  btn.classList.remove('spinning');
}

async function connectArduino() {
  const port = document.getElementById('port-select').value;
  if (!port) { showToast('Selecciona un puerto', 'warning'); return; }
  const result = await apiFetch('/api/connect_serial', 'POST', { port });
  if (result?.success) {
    showToast(`Conectado a ${port}`, 'success');
  } else {
    showToast(result?.error || 'Error de conexión', 'error');
  }
}

// ================================================================
// Ajustes
// ================================================================
function updateSettingDisplay(input, valueId, formatter) {
  const el = document.getElementById(valueId);
  if (el) el.textContent = formatter(input.value);
  settingsDirty = true;
}

async function loadSettings() {
  try {
    const s = await apiFetch('/api/settings');
    if (!s) return;

    setSlider('set-smoothing',       Math.round(s.smoothing * 100),    'smoothing-val',    v => (v/100).toFixed(2));
    setSlider('set-deadzone',        Math.round(s.dead_zone * 100),     'deadzone-val',     v => (v/100).toFixed(2));
    setSlider('set-return-timeout',  s.return_timeout ?? 3,             'return-val',       v => v+'s');
    setSlider('set-eye-neck-delay',  s.eye_neck_delay_ms ?? 300,        'eye-neck-delay-val', v => v+'ms');
    setSlider('set-gaze-sensitivity',Math.round((s.gaze_sensitivity ?? 1.0)*100), 'gaze-sens-val', v => (v/100).toFixed(1)+'x');
    setSlider('set-motion-area',     s.motion_min_area ?? 1000,         'motion-area-val',  v => v+'px');
    setSlider('set-motion-timeout',  s.motion_return_timeout ?? 2,      'motion-timeout-val', v => v+'s');

    setNumber('set-servo-min',    s.servo_min ?? 50);
    setNumber('set-servo-center', s.servo_center ?? 90);
    setNumber('set-servo-max',    s.servo_max ?? 130);

    // Actualizar slider manual con límites
    const slider = document.getElementById('manual-slider');
    if (slider) {
      slider.min = s.servo_min ?? 50;
      slider.max = s.servo_max ?? 130;
    }

    settingsDirty = false;
  } catch (_) {}
}

async function applySettings() {
  const smoothingRaw = document.getElementById('set-smoothing')?.value ?? 25;
  const deadzoneRaw  = document.getElementById('set-deadzone')?.value ?? 15;
  const settings = {
    smoothing:             parseFloat(smoothingRaw) / 100,
    dead_zone:             parseFloat(deadzoneRaw) / 100,
    return_timeout:        parseFloat(document.getElementById('set-return-timeout')?.value ?? 3),
    gaze_sensitivity:      parseFloat(document.getElementById('set-gaze-sensitivity')?.value ?? 100) / 100,
    eye_neck_delay_ms:     parseInt(document.getElementById('set-eye-neck-delay')?.value ?? 300),
    motion_min_area:       parseInt(document.getElementById('set-motion-area')?.value ?? 1000),
    motion_return_timeout: parseFloat(document.getElementById('set-motion-timeout')?.value ?? 2),
    servo_min:             parseInt(document.getElementById('set-servo-min')?.value ?? 50),
    servo_center:          parseInt(document.getElementById('set-servo-center')?.value ?? 90),
    servo_max:             parseInt(document.getElementById('set-servo-max')?.value ?? 130),
  };

  // Validaciones básicas
  if (settings.servo_min >= settings.servo_center) {
    showToast('El mínimo debe ser menor que el centro', 'warning'); return;
  }
  if (settings.servo_center >= settings.servo_max) {
    showToast('El centro debe ser menor que el máximo', 'warning'); return;
  }

  const result = await apiFetch('/api/settings', 'POST', settings);
  if (result?.success) {
    showToast('Configuración guardada', 'success');
    settingsDirty = false;
  }
}

// ================================================================
// Calibración
// ================================================================
async function startCalibration() {
  const result = await apiFetch('/api/calibrate', 'POST', { action: 'start' });
  if (result?.success) {
    calibInProgress = true;
    setCalibStep(0);
    document.getElementById('btn-calib-start').style.display = 'none';
    document.getElementById('btn-calib-next').style.display  = 'inline-block';
    document.getElementById('btn-calib-cancel').style.display = 'inline-block';
    document.getElementById('calib-message').textContent = result.message;
    showToast('Calibración iniciada', 'success');
  }
}

async function nextCalibStep() {
  const result = await apiFetch('/api/calibrate', 'POST', { action: 'next' });
  document.getElementById('calib-message').textContent = result?.message ?? '';

  if (result?.success) {
    const step = result.step ?? 0;
    markStepDone(step - 1);
    if (step >= 3) {
      // Calibración completa
      calibInProgress = false;
      setCalibStep(-1);
      markStepDone(0); markStepDone(1); markStepDone(2);
      document.getElementById('btn-calib-start').style.display = 'inline-block';
      document.getElementById('btn-calib-next').style.display  = 'none';
      document.getElementById('btn-calib-cancel').style.display = 'none';
      document.getElementById('calib-saved-text').textContent = '✓ Calibración guardada';
      showToast('¡Calibración completada!', 'success');
    } else {
      setCalibStep(step);
    }
  } else {
    showToast(result?.message || 'Error', 'warning');
  }
}

async function cancelCalibration() {
  await apiFetch('/api/calibrate', 'POST', { action: 'cancel' });
  calibInProgress = false;
  resetCalibUI();
  showToast('Calibración cancelada', 'warning');
}

function setCalibStep(step) {
  for (let i = 0; i < 3; i++) {
    const el = document.getElementById(`calib-step-${i}`);
    if (!el) continue;
    el.classList.remove('active-step', 'done-step');
    if (i === step) el.classList.add('active-step');
  }
}

function markStepDone(step) {
  const el = document.getElementById(`calib-step-${step}`);
  if (!el) return;
  el.classList.remove('active-step');
  el.classList.add('done-step');
  const status = document.getElementById(`step-status-${step}`);
  if (status) status.textContent = '✅';
}

function resetCalibUI() {
  for (let i = 0; i < 3; i++) {
    const el = document.getElementById(`calib-step-${i}`);
    if (el) el.classList.remove('active-step', 'done-step');
    const s = document.getElementById(`step-status-${i}`);
    if (s) s.textContent = '⬜';
  }
  document.getElementById('btn-calib-start').style.display = 'inline-block';
  document.getElementById('btn-calib-next').style.display  = 'none';
  document.getElementById('btn-calib-cancel').style.display = 'none';
  document.getElementById('calib-message').textContent =
    'La calibración mejora la precisión del seguimiento. Sigue los pasos.';
}

// ================================================================
// Tabs
// ================================================================
function showTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  document.getElementById(`tab-${name}`)?.classList.add('active');
  document.getElementById(`panel-${name}`)?.classList.add('active');
}

function clearLog() {
  document.getElementById('event-log').innerHTML = '';
}

// ================================================================
// Helpers UI
// ================================================================
function setDot(el, state) {
  if (!el) return;
  el.className = `status-dot ${state}`;
}

function setSlider(id, value, valueId, formatter) {
  const el = document.getElementById(id);
  if (el) {
    el.value = value;
    const vel = document.getElementById(valueId);
    if (vel) vel.textContent = formatter(value);
  }
}

function setNumber(id, value) {
  const el = document.getElementById(id);
  if (el) el.value = value;
}

let toastTimer = null;
function showToast(msg, type = 'info') {
  const toast = document.getElementById('toast');
  toast.textContent = msg;
  toast.className = `toast show toast-${type}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toast.classList.remove('show');
  }, 3000);
}

// ================================================================
// ESP32-CAM
// ================================================================

// Actualiza el banner grande de estado del ESP32
function setESP32ConnBanner(state, main, sub) {
  const banner  = document.getElementById('esp32-conn-banner');
  const mainEl  = document.getElementById('esp32-conn-main');
  const subEl   = document.getElementById('esp32-conn-sub');
  const iconEl  = document.getElementById('esp32-conn-icon');
  if (!banner) return;
  banner.className = 'esp32-status-banner' + (state ? ' conn-' + state : '');
  if (mainEl) { mainEl.textContent = main; mainEl.className = 'esp32-conn-main' + (state ? ' ' + state : ''); }
  if (subEl && sub !== undefined) subEl.textContent = sub;
  if (iconEl) iconEl.textContent = state === 'ok' ? '✅' : state === 'error' ? '❌' : state === 'connecting' ? '🔄' : '⚫';
}

function selectCamSource(source) {
  const panel   = document.getElementById('esp32-detail-panel');
  const btnEsp  = document.getElementById('btn-src-esp32');
  const btnLap  = document.getElementById('btn-src-laptop');
  if (source === 'esp32') {
    if (panel) panel.style.display = 'block';
    btnLap?.classList.remove('active');
    btnEsp?.classList.add('active');
  } else {
    if (panel) panel.style.display = 'none';
    btnEsp?.classList.remove('active');
    btnLap?.classList.add('active');
    restoreLocalCamera();
  }
}

async function applyESP32Url() {
  const url = (document.getElementById('esp32-url-input')?.value || '').trim();
  if (!url) { showToast('Escribe la URL del ESP32', 'warning'); return; }

  const btnEsp = document.getElementById('btn-src-esp32');
  if (btnEsp) btnEsp.textContent = '📡 Conectando...';
  setESP32ConnBanner('connecting', 'Conectando al ESP32...', url);
  showToast('Conectando al ESP32...', 'info');

  const result = await apiFetch('/api/esp32_camera', 'POST', { url });
  if (btnEsp) btnEsp.textContent = '📡 ESP32-CAM';
  // El banner se actualiza al recibir el siguiente /api/status
}

async function restoreLocalCamera() {
  showToast('Restaurando webcam local...', 'info');
  const btnLap = document.getElementById('btn-src-laptop');
  if (btnLap) btnLap.textContent = '💻 Conectando...';
  await apiFetch('/api/esp32_camera', 'POST', { url: '' });
  if (btnLap) btnLap.textContent = '💻 Laptop';
}

async function _loadESP32Config() {
  const data = await apiFetch('/api/esp32_camera');
  if (!data) return;
  const input = document.getElementById('esp32-url-input');
  if (input) input.value = data.url || '';
  // Establecer botón activo y visibilidad del panel
  const isEsp32 = data.source === 'esp32';
  document.getElementById('btn-src-laptop')?.classList.toggle('active', !isEsp32);
  document.getElementById('btn-src-esp32')?.classList.toggle('active', isEsp32);
  const panel = document.getElementById('esp32-detail-panel');
  if (panel) panel.style.display = isEsp32 ? 'block' : 'none';
  if (isEsp32 && data.url) {
    setESP32ConnBanner('connecting', 'Verificando ESP32...', data.url);
  }
}

// ================================================================
// WiFi del ESP32
// ================================================================
let _esp32WifiWaitTimer = null;
let _esp32LastKnownUrl  = '';

async function saveESP32Wifi() {
  const ssid = document.getElementById('esp32-wifi-ssid').value.trim();
  const pass = document.getElementById('esp32-wifi-pass').value;
  if (!ssid) { showToast('Escribe el nombre de la red (SSID)', 'error'); return; }

  showToast('Enviando credenciales al ESP32...', 'info');
  try {
    const r = await apiFetch('/api/esp32_wifi', 'POST', { ssid, password: pass });
    if (r.success) {
      showToast(r.message || 'ESP32 reiniciando con nueva red', 'success');
      document.getElementById('esp32-wifi-ssid').value = '';
      document.getElementById('esp32-wifi-pass').value = '';
      // Guardar URL actual para detectar cambio
      try {
        const st = await apiFetch('/api/status');
        _esp32LastKnownUrl = st?.camera?.esp32_url || '';
      } catch(_) {}
      // Mostrar panel de espera
      const statusDiv = document.getElementById('esp32-wifi-status');
      const statusText = document.getElementById('esp32-wifi-status-text');
      const newUrlRow = document.getElementById('esp32-new-url-row');
      if (statusDiv) {
        statusDiv.style.display = 'block';
        statusText.textContent = 'Esperando anuncio del ESP32... (puede tardar ~30s)';
        newUrlRow.style.display = 'none';
      }
      // Iniciar polling para detectar cuando el ESP32 anuncia la nueva IP
      if (_esp32WifiWaitTimer) clearInterval(_esp32WifiWaitTimer);
      let tries = 0;
      _esp32WifiWaitTimer = setInterval(async () => {
        tries++;
        try {
          const st = await apiFetch('/api/status');
          const newUrl = st?.camera?.esp32_url || '';
          if (newUrl && newUrl !== _esp32LastKnownUrl) {
            clearInterval(_esp32WifiWaitTimer);
            _esp32WifiWaitTimer = null;
            // Mostrar nueva URL
            const linkEl = document.getElementById('esp32-new-url-link');
            if (linkEl) {
              linkEl.href = newUrl;
              linkEl.textContent = newUrl;
            }
            if (statusText) statusText.textContent = 'ESP32 reconectado con nueva red.';
            if (newUrlRow) newUrlRow.style.display = 'block';
            showToast('ESP32 reconectado: ' + newUrl, 'success');
          } else if (tries >= 60) { // 60 * 3s = 3 min
            clearInterval(_esp32WifiWaitTimer);
            _esp32WifiWaitTimer = null;
            if (statusText) statusText.textContent = 'No se recibio anuncio. Ingresa la nueva URL manualmente.';
          }
        } catch(_) {}
      }, 3000);
    } else {
      showToast('Error: ' + (r.error || 'desconocido'), 'error');
    }
  } catch (e) {
    showToast('Sin respuesta del ESP32', 'error');
  }
}

function applyAnnouncedUrl() {
  const linkEl = document.getElementById('esp32-new-url-link');
  if (!linkEl || !linkEl.href || linkEl.href === '#') return;
  const newUrl = linkEl.textContent.trim();
  document.getElementById('esp32-url-input').value = newUrl;
  applyESP32Url();
  document.getElementById('esp32-wifi-status').style.display = 'none';
  showToast('URL actualizada: ' + newUrl, 'success');
}

// ================================================================
// CHUCKY PARLANTE
// ================================================================

let _chuckyPollTimer = null;

function _updateChuckyUI(data) {
  if (!data) return;

  // Banner de estado
  const banner = document.getElementById('chucky-status-banner');
  const icon   = document.getElementById('chucky-status-icon');
  const text   = document.getElementById('chucky-status-text');
  const sub    = document.getElementById('chucky-status-sub');
  const btn    = document.getElementById('chucky-toggle-btn');

  const statusMap = {
    inactivo:    { icon: '⚫', color: 'rgba(100,100,100,.3)',  bg: 'rgba(30,42,69,.4)',          label: 'Inactivo',      sub: 'Activa el modo parlante para comenzar' },
    escuchando:  { icon: '👂', color: 'rgba(0,230,118,.5)',   bg: 'rgba(0,230,118,.07)',         label: 'Escuchando…',   sub: `Di "${data.wake_word || 'andy'}" para activar` },
    procesando:  { icon: '🧠', color: 'rgba(255,171,64,.5)',  bg: 'rgba(255,171,64,.07)',        label: 'Procesando…',   sub: 'Consultando con la IA' },
    hablando:    { icon: '😈', color: 'rgba(229,57,53,.6)',   bg: 'rgba(229,57,53,.1)',          label: '¡Hablando!',    sub: 'Chucky está respondiendo' },
  };
  const st = statusMap[data.status] || statusMap.inactivo;

  if (banner) { banner.style.borderColor = st.color; banner.style.background = st.bg; }
  if (icon)   icon.textContent = st.icon;
  if (text)   { text.textContent = st.label; text.style.color = st.color.includes('229') ? '#ff8a80' : st.color.includes('230') ? 'var(--success)' : st.color.includes('171') ? 'var(--warning)' : 'var(--text-dim)'; }
  if (sub)    sub.textContent  = st.sub;
  if (btn)    { btn.textContent = data.active ? '⏹ Detener' : '▶ Activar'; }

  // Textos
  const heard = document.getElementById('chucky-heard');
  const resp  = document.getElementById('chucky-response');
  if (heard && data.last_heard)    heard.textContent    = `"${data.last_heard}"`;
  if (resp  && data.last_response) resp.textContent     = `"${data.last_response}"`;

  // Mandíbula
  const jawFill  = document.getElementById('chucky-jaw-fill');
  const jawAngle = document.getElementById('chucky-jaw-angle');
  const jawPct   = Math.min(100, (data.jaw_angle / 90) * 100);
  if (jawFill)  jawFill.style.width = jawPct + '%';
  if (jawAngle) jawAngle.textContent = (data.jaw_angle || 0) + '°';

  // Modelo / descarga
  const modelSt    = document.getElementById('chucky-model-status');
  const dlBtn      = document.getElementById('chucky-download-btn');
  if (modelSt) {
    if (data.downloading) {
      modelSt.textContent = `⬇ Descargando modelo… ${data.download_pct || 0}%`;
      modelSt.style.color = 'var(--warning)';
    } else if (data.model_ready) {
      modelSt.textContent = '✅ Modelo de voz listo';
      modelSt.style.color = 'var(--success)';
    } else {
      modelSt.textContent = data.error || '⚠ Modelo de voz no encontrado';
      modelSt.style.color = 'var(--warning)';
    }
  }
  if (dlBtn) dlBtn.style.display = (!data.model_ready && !data.downloading) ? 'block' : 'none';

  // Badge TTS engine
  const badge = document.getElementById('chucky-tts-badge');
  if (badge) {
    if (data.tts_engine === 'elevenlabs') {
      badge.textContent = '🎭 Motor TTS: ElevenLabs' + (data.elevenlabs_voice_set ? ' ✅ voz configurada' : ' ⚠ sin Voice ID');
      badge.style.background = 'rgba(224,64,251,.15)';
      badge.style.color = '#e040fb';
    } else {
      badge.textContent = '🔊 Motor TTS: gTTS (genérico) — configura ElevenLabs para la voz de Chucky';
      badge.style.background = 'rgba(100,100,100,.15)';
      badge.style.color = 'var(--text-dim)';
    }
  }
}

async function toggleChucky() {
  const btn = document.getElementById('chucky-toggle-btn');
  const st  = await apiFetch('/api/chucky/status');
  if (!st) return;
  if (st.active) {
    await apiFetch('/api/chucky/stop', 'POST');
    clearInterval(_chuckyPollTimer);
    _chuckyPollTimer = null;
  } else {
    const res = await apiFetch('/api/chucky/start', 'POST');
    if (res && !res.success) { showToast(res.error || 'No se pudo iniciar', 'error'); return; }
    _startChuckyPoll();
  }
  setTimeout(() => apiFetch('/api/chucky/status').then(_updateChuckyUI), 300);
}

function _startChuckyPoll() {
  if (_chuckyPollTimer) return;
  _chuckyPollTimer = setInterval(async () => {
    const data = await apiFetch('/api/chucky/status');
    _updateChuckyUI(data);
    if (data && !data.active) {
      clearInterval(_chuckyPollTimer);
      _chuckyPollTimer = null;
    }
  }, 500);
}

async function chuckySpeak() {
  const input = document.getElementById('chucky-manual-text');
  const texto = input ? input.value.trim() : '';
  if (!texto) return;
  await apiFetch('/api/chucky/speak', 'POST', { text: texto });
  if (input) input.value = '';
  _startChuckyPoll();
}

async function saveChuckyConfig() {
  const key     = document.getElementById('chucky-api-key')?.value.trim()   || null;
  const wake    = document.getElementById('chucky-wake-word')?.value.trim() || null;
  const endW    = document.getElementById('chucky-end-word')?.value.trim()  || null;
  const elKey   = document.getElementById('chucky-el-key')?.value.trim()    || null;
  const elVoice = document.getElementById('chucky-el-voice')?.value.trim()  || null;
  const payload = {};
  if (key)     payload.groq_api_key        = key;
  if (wake)    payload.wake_word           = wake;
  if (endW)    payload.end_word            = endW;
  if (elKey)   payload.elevenlabs_api_key  = elKey;
  if (elVoice) payload.elevenlabs_voice_id = elVoice;
  const res = await apiFetch('/api/chucky/config', 'POST', payload);
  if (res?.success) showToast('Config de Chucky guardada', 'success');
}

async function downloadChuckyModel() {
  const res = await apiFetch('/api/chucky/download_model', 'POST');
  if (res) showToast('Descarga iniciada (~39 MB)', 'info');
  _startChuckyPoll();
}

// Cargar estado inicial de Chucky al abrir la página
(async function _initChucky() {
  const data = await apiFetch('/api/chucky/status');
  if (data) {
    _updateChuckyUI(data);
    // Pre-llenar palabras clave
    const ww = document.getElementById('chucky-wake-word');
    const ew = document.getElementById('chucky-end-word');
    if (ww && data.wake_word) ww.value = data.wake_word;
    if (ew && data.end_word)  ew.value = data.end_word;
    if (data.active) _startChuckyPoll();
  }
})();

// ================================================================
// FRASES AUTOMÁTICAS
// ================================================================
let _autoActive = false;

async function _loadAutoFrasesUI() {
  const data = await apiFetch('/api/chucky/auto_frases');
  if (!data) return;
  _autoActive = data.auto_active;
  _updateAutoUI(data);
}

function _updateAutoUI(data) {
  const dot = document.getElementById('auto-status-dot');
  const txt = document.getElementById('auto-status-txt');
  const btn = document.getElementById('auto-toggle-btn');
  const cb  = document.getElementById('scary-voice-cb');
  const iv  = document.getElementById('auto-interval');
  const ta  = document.getElementById('auto-phrases-area');

  _autoActive = data.auto_active;

  if (dot) dot.style.background = data.auto_active ? '#ce93d8' : '#555';
  if (txt) txt.textContent      = data.auto_active
    ? `Activo — frase cada ${data.auto_interval}s`
    : 'Inactivo';
  if (btn) {
    btn.textContent = data.auto_active ? '⏹ Detener' : '▶ Iniciar';
    btn.style.background = data.auto_active
      ? 'rgba(123,31,162,.35)'
      : 'rgba(123,31,162,.12)';
  }
  if (cb  && data.scary_voice  !== undefined) cb.checked  = data.scary_voice;
  if (iv  && data.auto_interval !== undefined) iv.value   = data.auto_interval;
  if (ta  && data.auto_phrases  && !ta._userEdited) {
    ta.value = data.auto_phrases.join('\n');
  }
}

async function toggleAutoFrases() {
  const iv     = document.getElementById('auto-interval');
  const cb     = document.getElementById('scary-voice-cb');
  const ta     = document.getElementById('auto-phrases-area');
  const action = _autoActive ? 'stop' : 'start';
  const payload = { action };

  if (!_autoActive) {
    payload.interval    = parseInt(iv?.value || '30');
    payload.scary_voice = cb?.checked ?? true;
    const lines = (ta?.value || '').split('\n').map(l => l.trim()).filter(Boolean);
    if (lines.length > 0) payload.phrases = lines;
  }

  const res = await apiFetch('/api/chucky/auto_frases', 'POST', payload);
  if (res) _updateAutoUI(res);
}

async function saveAutoConfig() {
  const iv = document.getElementById('auto-interval');
  const cb = document.getElementById('scary-voice-cb');
  const ta = document.getElementById('auto-phrases-area');
  const lines = (ta?.value || '').split('\n').map(l => l.trim()).filter(Boolean);

  const res = await apiFetch('/api/chucky/auto_frases', 'POST', {
    action:      'config',
    interval:    parseInt(iv?.value || '30'),
    scary_voice: cb?.checked ?? true,
    phrases:     lines.length > 0 ? lines : undefined,
  });
  if (res?.success) {
    showToast('Config auto-frases guardada', 'success');
    if (ta) ta._userEdited = false;
  }
}

// Marcar que el textarea fue editado manualmente
(function() {
  document.addEventListener('DOMContentLoaded', () => {
    const ta = document.getElementById('auto-phrases-area');
    if (ta) ta.addEventListener('input', () => { ta._userEdited = true; });
  });
})();

// ================================================================
// MICRÓFONO WEB — Hablar con Chucky desde el navegador/celular
// Usa Web Speech API para transcribir en el navegador, luego
// envía el texto al servidor para respuesta Groq + audio ElevenLabs
// ================================================================
(function() {
  let _recognition = null;
  let _listening   = false;

  function _micStatus(msg, color) {
    const el = document.getElementById('web-mic-status');
    if (el) { el.textContent = msg; el.style.color = color || 'var(--text-dim)'; }
  }

  function _micBtn(active) {
    const btn = document.getElementById('web-mic-btn');
    if (!btn) return;
    if (active) {
      btn.textContent = '🔴 ESCUCHANDO… (suelta para enviar)';
      btn.style.background = 'rgba(229,57,53,.35)';
      btn.style.borderColor = '#e53935';
      btn.style.color = '#fff';
    } else {
      btn.textContent = '🎙 MANTÉN PRESIONADO PARA HABLAR';
      btn.style.background = 'rgba(229,57,53,.1)';
      btn.style.borderColor = 'rgba(229,57,53,.5)';
      btn.style.color = '#ff8a80';
    }
  }

  async function _sendToChucky(texto) {
    if (!texto.trim()) return;
    _micStatus('Enviando: "' + texto.slice(0, 40) + '"…', '#00d4ff');
    try {
      const resp = await fetch('/api/voice_chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: texto })
      });
      if (!resp.ok) { _micStatus('Error del servidor', '#ff5252'); return; }
      const blob = await resp.blob();
      const url  = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audio.play();
      _micStatus('✓ Chucky responde…', '#4caf50');
      audio.onended = () => { URL.revokeObjectURL(url); _micStatus(''); };
    } catch(e) {
      _micStatus('Error: ' + e.message, '#ff5252');
    }
  }

  function _initRecognition() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
      _micStatus('Tu navegador no soporta reconocimiento de voz', '#ff5252');
      return null;
    }
    const r = new SR();
    r.lang = 'es-MX';
    r.interimResults = false;
    r.maxAlternatives = 1;
    r.onresult = (e) => {
      const texto = e.results[0][0].transcript;
      _micStatus('"' + texto + '"', '#e0e8f0');
      _sendToChucky(texto);
    };
    r.onerror = (e) => { _micStatus('Error mic: ' + e.error, '#ff5252'); };
    r.onend   = () => { _listening = false; _micBtn(false); };
    return r;
  }

  window.toggleWebMic = function() {
    if (_listening) {
      _recognition && _recognition.stop();
      _listening = false; _micBtn(false);
      return;
    }
    _recognition = _initRecognition();
    if (!_recognition) return;
    try {
      _recognition.start();
      _listening = true; _micBtn(true);
      _micStatus('Habla ahora…', '#ffaa00');
    } catch(e) {
      _micStatus('No se pudo iniciar el mic', '#ff5252');
    }
  };

  // Soporte para mantener presionado (touch y mouse)
  document.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('web-mic-btn');
    if (!btn) return;
    // Click simple también funciona como toggle
    // Sin cambios adicionales necesarios (ya usa onclick)
  });
})();

// Cargar estado al abrir la página
(async function _initAutoFrases() {
  await _loadAutoFrasesUI();
})();

// ================================================================
// API helper
// ================================================================
async function apiFetch(url, method = 'GET', body = null) {
  try {
    const opts = {
      method,
      headers: { 'Content-Type': 'application/json' },
    };
    if (body !== null) opts.body = JSON.stringify(body);
    const res = await fetch(url, opts);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      showToast(err.error || `Error ${res.status}`, 'error');
      return null;
    }
    return await res.json();
  } catch (e) {
    // Sin conectividad (servidor no iniciado)
    return null;
  }
}
