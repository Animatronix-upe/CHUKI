package com.chucky.controller

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.WindowManager
import android.webkit.JavascriptInterface
import android.webkit.PermissionRequest
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {

    companion object {
        const val DEFAULT_SERVER_URL = "http://173.17.24.231:5000"
        const val PREFS_NAME = "ChuckyPrefs"
        const val KEY_SERVER_URL = "server_url"
    }

    private lateinit var webView: WebView
    private val handler = Handler(Looper.getMainLooper())
    private var pageLoaded = false

    private fun getSavedUrl(): String =
        getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .getString(KEY_SERVER_URL, DEFAULT_SERVER_URL) ?: DEFAULT_SERVER_URL

    private fun saveUrl(url: String) =
        getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE).edit()
            .putString(KEY_SERVER_URL, url).apply()

    inner class ChuckyInterface {
        @JavascriptInterface
        fun getServerUrl(): String = getSavedUrl()

        @JavascriptInterface
        fun setServerUrl(url: String) {
            saveUrl(url)
            runOnUiThread { webView.loadUrl(url) }
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContentView(R.layout.activity_main)

        webView = findViewById(R.id.webView)
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            loadWithOverviewMode = true
            useWideViewPort = true
            builtInZoomControls = false
            displayZoomControls = false
            setSupportZoom(false)
            mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
            mediaPlaybackRequiresUserGesture = false
        }

        webView.addJavascriptInterface(ChuckyInterface(), "Android")

        webView.webChromeClient = object : WebChromeClient() {
            override fun onPermissionRequest(request: PermissionRequest) {
                request.grant(request.resources)
            }
        }

        webView.setBackgroundColor(Color.parseColor("#0a0c12"))

        webView.webViewClient = object : WebViewClient() {
            override fun onPageStarted(view: WebView?, url: String?, favicon: android.graphics.Bitmap?) {
                if (url != null && !url.startsWith("data:")) {
                    pageLoaded = false
                    handler.removeCallbacksAndMessages(null)
                    handler.postDelayed({
                        if (!pageLoaded) {
                            view?.loadData(offlinePage(getSavedUrl()), "text/html; charset=utf-8", "UTF-8")
                        }
                    }, 5000)
                }
            }
            override fun onPageFinished(view: WebView?, url: String?) {
                pageLoaded = true
                handler.removeCallbacksAndMessages(null)
            }
            override fun onReceivedError(view: WebView?, request: WebResourceRequest?, error: WebResourceError?) {
                if (request?.isForMainFrame == true) {
                    pageLoaded = true
                    handler.removeCallbacksAndMessages(null)
                    view?.loadData(offlinePage(getSavedUrl()), "text/html; charset=utf-8", "UTF-8")
                }
            }
        }

        // Muestra pantalla de carga inmediatamente
        webView.loadData(loadingPage(getSavedUrl()), "text/html; charset=utf-8", "UTF-8")
        // Luego intenta conectar al servidor
        handler.postDelayed({ webView.loadUrl(getSavedUrl()) }, 800)
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (webView.canGoBack()) webView.goBack() else super.onBackPressed()
    }

    private fun loadingPage(url: String) = """
        <!DOCTYPE html><html><head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width,initial-scale=1.0">
        <style>
          body{background:#0a0c12;color:#e0e8f0;font-family:monospace;
               display:flex;align-items:center;justify-content:center;
               min-height:100vh;margin:0;flex-direction:column;gap:20px;text-align:center;padding:28px}
          h2{color:#00d4ff;margin:0;font-size:1.3rem;letter-spacing:2px}
          p{color:#6a7a90;font-size:.8rem;margin:0}
          .spinner{width:60px;height:60px;border:4px solid rgba(0,212,255,.2);
                   border-top:4px solid #00d4ff;border-radius:50%;
                   animation:spin 1s linear infinite}
          @keyframes spin{to{transform:rotate(360deg)}}
          .url{color:#00d4ff;font-size:.75rem;opacity:.7;word-break:break-all;max-width:280px}
        </style></head><body>
        <div class="spinner"></div>
        <h2>CONECTANDO...</h2>
        <p>Buscando el servidor de Chucky</p>
        <div class="url">$url</div>
        </body></html>
    """.trimIndent()

    private fun offlinePage(url: String) = """
        <!DOCTYPE html><html><head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width,initial-scale=1.0">
        <style>
          *{box-sizing:border-box}
          body{background:#0a0c12;color:#e0e8f0;font-family:monospace;
               display:flex;align-items:center;justify-content:center;
               min-height:100vh;margin:0;flex-direction:column;gap:18px;
               text-align:center;padding:28px}
          h2{color:#ff5252;margin:0;font-size:1.4rem}
          p{color:#6a7a90;line-height:1.7;margin:0;font-size:.85rem}
          .box{width:100%;max-width:340px;display:flex;flex-direction:column;gap:10px}
          input{background:rgba(0,212,255,.08);border:1px solid rgba(0,212,255,.3);
                color:#e0e8f0;padding:13px 16px;border-radius:8px;font-size:.9rem;
                font-family:monospace;width:100%;text-align:center}
          input:focus{outline:none;border-color:#00d4ff}
          button{padding:14px;border-radius:8px;font-size:.95rem;cursor:pointer;
                 font-family:monospace;letter-spacing:1px;width:100%;border:1px solid}
          .btn-retry{background:rgba(0,212,255,.12);border-color:#00d4ff;color:#00d4ff}
          .btn-save{background:rgba(255,170,0,.1);border-color:#ffaa00;color:#ffaa00}
          .icon{font-size:3.2rem}
        </style></head><body>
        <div class="icon">&#x1F441;</div>
        <h2>SIN CONEXI&#xD3;N</h2>
        <p>Aseg&#xFA;rate de estar en la misma red WiFi<br>y que el servidor est&#xE9; corriendo en la PC.</p>
        <div class="box">
          <input id="ip" type="url" value="$url" placeholder="http://192.168.x.x:5000">
          <button class="btn-retry" onclick="retry()">&#x27F3;&nbsp; REINTENTAR</button>
          <button class="btn-save" onclick="saveAndGo()">&#x270E;&nbsp; GUARDAR NUEVA IP</button>
        </div>
        <script>
          function retry(){var u=document.getElementById('ip').value.trim();if(u)location.href=u;}
          function saveAndGo(){
            var u=document.getElementById('ip').value.trim();
            if(!u)return;
            if(typeof Android!=='undefined')Android.setServerUrl(u);
            else location.href=u;
          }
        </script>
        </body></html>
    """.trimIndent()
}
