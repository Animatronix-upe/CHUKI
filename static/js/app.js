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
    const neckAngle = data.neck?.angle ?? data.movement?.neck_angle ?? 90;
    const neckSync  = data.neck?.sync  ?? data.movement?.neck_sync  ?? true;
    updateNeckDisplay(neckAngle, neckSync);

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
  apiFetch('/api/neck_position', 'POST', { angle });
}

function updateNeckDisplay(angle, sync) {
  const angleEl = document.getElementById('neck-angle-display');
  if (angleEl) angleEl.textContent = angle + '°';

  const indicator = document.getElementById('neck-servo-indicator');
  const range     = document.getElementById('neck-servo-range');
  const pct = ((angle - 0) / 180) * 100;
  if (indicator) indicator.style.left  = `${Math.max(0, Math.min(100, pct))}%`;
  if (range)     range.style.width     = `${Math.max(0, Math.min(100, pct))}%`;

  const statusText = document.getElementById('neck-sync-status-text');
  if (statusText) statusText.textContent = sync ? '● Sincronizado con ojos' : '○ Control independiente';

  // Sincronizar toggle con estado del servidor
  const toggle = document.getElementById('neck-sync-toggle');
  if (toggle && toggle.checked !== sync) {
    toggle.checked = sync;
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
function toggleESP32Panel() {
  const p = document.getElementById('esp32-panel');
  if (p) p.style.display = p.style.display === 'none' ? 'flex' : 'none';
}

async function applyESP32Url() {
  const url = (document.getElementById('esp32-url-input')?.value || '').trim();
  if (!url) { showToast('Escribe la URL del ESP32', 'warning'); return; }
  showToast('Conectando al ESP32...', 'info');
  const result = await apiFetch('/api/esp32_camera', 'POST', { url });
  if (result?.success) {
    _updateESP32Badge('esp32');
    showToast('ESP32-CAM conectada ✓', 'success');
    document.getElementById('esp32-panel').style.display = 'none';
  }
}

async function restoreLocalCamera() {
  showToast('Restaurando webcam local...', 'info');
  const result = await apiFetch('/api/esp32_camera', 'POST', { url: '' });
  if (result?.success) {
    _updateESP32Badge('local');
    showToast('Webcam PC activa', 'success');
    document.getElementById('esp32-panel').style.display = 'none';
  }
}

async function _loadESP32Config() {
  const data = await apiFetch('/api/esp32_camera');
  if (!data) return;
  const input = document.getElementById('esp32-url-input');
  if (input) input.value = data.url || '';
  _updateESP32Badge(data.source);
}

function _updateESP32Badge(source) {
  const badge = document.getElementById('esp32-source-badge');
  if (!badge) return;
  if (source === 'esp32') {
    badge.textContent = 'ESP32';
    badge.classList.add('active');
  } else {
    badge.textContent = 'LOCAL';
    badge.classList.remove('active');
  }
}

// ================================================================
// Cámara del teléfono
// ================================================================
let _phoneStream   = null;
let _phoneInterval = null;
let _phoneActive   = false;

async function togglePhoneCamera() {
  if (_phoneActive) {
    _stopPhoneCamera();
  } else {
    await _startPhoneCamera();
  }
}

async function _startPhoneCamera() {
  if (!navigator.mediaDevices?.getUserMedia) {
    showToast('getUserMedia no disponible en este navegador', 'error');
    return;
  }
  try {
    _phoneStream = await navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: { ideal: 'environment' },
        width: { ideal: 320 },
        height: { ideal: 240 }
      }
    });
    const vid = document.getElementById('phone-video');
    vid.srcObject = _phoneStream;

    document.getElementById('camera-feed').style.display  = 'none';
    vid.style.display = 'block';

    _phoneInterval = setInterval(_sendPhoneFrame, 150);
    _phoneActive   = true;

    const btn = document.getElementById('btn-phone-cam');
    if (btn) { btn.textContent = '🖥 USAR CÁMARA PC'; btn.classList.add('active'); }
    showToast('Cámara del teléfono activa', 'success');
  } catch (e) {
    showToast('Sin acceso a la cámara: ' + e.message, 'error');
  }
}

function _stopPhoneCamera() {
  if (_phoneStream) { _phoneStream.getTracks().forEach(t => t.stop()); _phoneStream = null; }
  clearInterval(_phoneInterval); _phoneInterval = null;
  _phoneActive = false;

  document.getElementById('camera-feed').style.display  = 'block';
  const vid = document.getElementById('phone-video');
  if (vid) { vid.srcObject = null; vid.style.display = 'none'; }

  const btn = document.getElementById('btn-phone-cam');
  if (btn) { btn.textContent = '📱 USAR CÁMARA DEL TELÉFONO'; btn.classList.remove('active'); }
  showToast('Cámara PC restaurada');
}

async function _sendPhoneFrame() {
  const vid = document.getElementById('phone-video');
  const cvs = document.getElementById('phone-canvas');
  if (!vid || vid.readyState < 2 || !cvs) return;

  cvs.width  = vid.videoWidth  || 320;
  cvs.height = vid.videoHeight || 240;
  cvs.getContext('2d').drawImage(vid, 0, 0, cvs.width, cvs.height);

  try {
    await fetch('/api/phone_frame', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ frame: cvs.toDataURL('image/jpeg', 0.5) })
    });
  } catch (_) {}
}

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
