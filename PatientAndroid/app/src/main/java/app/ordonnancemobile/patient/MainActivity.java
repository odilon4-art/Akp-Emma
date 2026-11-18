package app.ordonnancemobile.patient;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.net.Uri;
import android.os.Bundle;
import android.webkit.GeolocationPermissions;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import java.net.HttpURLConnection;
import java.net.URL;

/**
 * Ordonnance Mobile Patient (Android) : fenetre WebView vers le logiciel Patient lance sur le PC
 * (python main.py --reseau, meme Wi-Fi). L'adresse du PC est saisie une seule fois.
 */
public class MainActivity extends Activity {
    private static final String SETUP = "file:///android_asset/setup.html";
    private static final int DEFAULT_PORT = 8769;
    private static final int REQ_LOC = 42;

    private WebView web;
    private SharedPreferences prefs;
    private GeolocationPermissions.Callback geoCallback;
    private String geoOrigin;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences("om", MODE_PRIVATE);
        web = new WebView(this);
        web.setBackgroundColor(0xFF0A1220);
        setContentView(web);

        WebSettings st = web.getSettings();
        st.setJavaScriptEnabled(true);
        st.setDomStorageEnabled(true);
        st.setGeolocationEnabled(true);
        st.setAllowFileAccess(true);
        st.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);

        web.addJavascriptInterface(new Bridge(), "Android");
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
                Uri u = r.getUrl();
                String s = u.toString();
                if (s.startsWith("file:///android_asset/") || s.startsWith(serverBase())) return false;
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, u)); // tel:, cartes, liens externes
                } catch (Exception ignored) { }
                return true;
            }

            @Override
            public void onReceivedError(WebView v, WebResourceRequest r, WebResourceError e) {
                if (r.isForMainFrame() && !r.getUrl().toString().startsWith("file:")) {
                    showSetup("Impossible de joindre le PC. Verifiez que le logiciel Patient est lance avec Lancer_RESEAU.bat et que le telephone est sur le meme Wi-Fi.");
                }
            }

            @Override
            public void onPageStarted(WebView v, String url, Bitmap f) { }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onGeolocationPermissionsShowPrompt(String origin, GeolocationPermissions.Callback cb) {
                if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED) {
                    cb.invoke(origin, true, false);
                } else {
                    geoCallback = cb;
                    geoOrigin = origin;
                    requestPermissions(new String[]{Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION}, REQ_LOC);
                }
            }
        });

        String url = prefs.getString("url", null);
        if (url == null) web.loadUrl(SETUP);
        else web.loadUrl(url);
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] res) {
        if (code == REQ_LOC && geoCallback != null) {
            boolean ok = res.length > 0 && res[0] == PackageManager.PERMISSION_GRANTED;
            geoCallback.invoke(geoOrigin, ok, false);
            geoCallback = null;
        }
    }

    private String serverBase() {
        String u = prefs.getString("url", "");
        int i = u.indexOf("/index.html");
        return i > 0 ? u.substring(0, i) : "http://invalide.local";
    }

    private void showSetup(String message) {
        String a = prefs.getString("addr", "");
        String q = "?a=" + Uri.encode(a) + (message == null ? "" : "&e=" + Uri.encode(message));
        web.loadUrl(SETUP + q);
    }

    @Override
    public void onBackPressed() {
        String cur = web.getUrl();
        if (web.canGoBack() && cur != null && !cur.startsWith("file:")) web.goBack();
        else super.onBackPressed();
    }

    /** Appele depuis setup.html et depuis l'application Patient (bouton "Changer l'adresse du PC"). */
    private class Bridge {
        @JavascriptInterface
        public void reset() {
            runOnUiThread(() -> showSetup(null));
        }

        @JavascriptInterface
        public void save(String raw) {
            final String addr = clean(raw);
            if (addr == null) {
                reply(false, "Adresse invalide. Exemple : 192.168.1.20");
                return;
            }
            new Thread(() -> {
                String url = "http://" + addr + "/index.html";
                try {
                    HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
                    c.setConnectTimeout(4000);
                    c.setReadTimeout(4000);
                    int code = c.getResponseCode();
                    c.disconnect();
                    if (code != 200) {
                        reply(false, "Le PC repond mais ce n'est pas le logiciel Patient (code " + code + "). Lancez Lancer_RESEAU.bat.");
                        return;
                    }
                } catch (Exception e) {
                    reply(false, "Connexion impossible a " + addr + ". Meme Wi-Fi ? Logiciel lance avec --reseau ? Pare-feu Windows autorise ?");
                    return;
                }
                prefs.edit().putString("addr", addr).putString("url", url).apply();
                runOnUiThread(() -> web.loadUrl(url));
            }).start();
        }

        private void reply(boolean ok, String msg) {
            final String js = "window.resultat(" + ok + "," + org.json.JSONObject.quote(msg) + ")";
            runOnUiThread(() -> web.evaluateJavascript(js, null));
        }
    }

    /** "192.168.1.20", "http://192.168.1.20:8769/" -> "192.168.1.20:8769" ; null si invalide. */
    static String clean(String raw) {
        if (raw == null) return null;
        String s = raw.trim().replaceAll("^https?://", "");
        int slash = s.indexOf('/');
        if (slash >= 0) s = s.substring(0, slash);
        if (!s.matches("[A-Za-z0-9.\\-]+(:\\d{2,5})?")) return null;
        if (!s.contains(":")) s = s + ":" + DEFAULT_PORT;
        return s;
    }
}
