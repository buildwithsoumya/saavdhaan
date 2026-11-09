package com.saavdhaan.app;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Insets;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowManager;
import android.window.OnBackInvokedDispatcher;
import android.webkit.PermissionRequest;
import android.webkit.SslErrorHandler;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.security.MessageDigest;
import java.security.cert.X509Certificate;
import java.util.Arrays;
import java.util.Locale;

/**
 * Saavdhaan listener app: a full-screen WebView on the backend, either a hosted server or
 * a laptop running `python -m app.serve`. The API keys stay on the backend; this app only adds what a
 * browser tab lacks: an installed icon, mic permission without prompts on every visit,
 * the screen kept on, and trust in the backend's self-signed certificate.
 *
 * Certificate trust is pinned per laptop on first use: the app shows the certificate's
 * SHA-256 fingerprint, which app.serve also prints, and only proceeds for that exact
 * certificate. Everything is limited to the exact origin (scheme, host, port) pasted in.
 */
public class MainActivity extends Activity {
    private static final String PREFS = "saavdhaan", KEY_URL = "url", KEY_PIN = "pin:";
    private static final int ASK_MIC = 1;
    private static final int INK = Color.rgb(0x15, 0x23, 0x3f), PAPER = Color.rgb(0xd9, 0xe7, 0xef);

    private SharedPreferences prefs;
    private FrameLayout root;
    private WebView web;
    private Uri origin;                 // the backend: scheme + host + port of the pasted link
    private PermissionRequest pendingMic;
    private AlertDialog sslDialog;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON); // the mic stops if the screen sleeps
        root = new FrameLayout(this);
        root.setBackgroundColor(INK);
        root.setOnApplyWindowInsetsListener(this::padForSystemBars); // targetSdk 35+ draws edge to edge
        setContentView(root);
        if (Build.VERSION.SDK_INT >= 33) { // predictive back: onBackPressed is not called on targetSdk 36
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(
                    OnBackInvokedDispatcher.PRIORITY_DEFAULT, this::handleBack);
        }
        String url = prefs.getString(KEY_URL, null);
        if (url == null) showSetup(null);
        else openApp(url);
        handleLink(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        handleLink(intent);
    }

    /** The App button on the Saavdhaan page opens saavdhaan://open?u=<link>, so nothing has to be pasted.
     *  Any site can open such a link, so the app asks before switching to a new server. */
    private void handleLink(Intent intent) {
        Uri data = intent == null ? null : intent.getData();
        if (data == null || !"saavdhaan".equalsIgnoreCase(data.getScheme())) return;
        String url = data.getQueryParameter("u");
        if (!validLink(url) || url.equals(prefs.getString(KEY_URL, null))) return;
        new AlertDialog.Builder(this)
                .setTitle("इस सर्वर से जुड़ें? · Connect to this server?")
                .setMessage(Uri.parse(url).getHost())
                .setPositiveButton("जोड़ें · Connect", (d, w) -> {
                    prefs.edit().putString(KEY_URL, url).apply();
                    openApp(url);
                })
                .setNegativeButton("नहीं · Cancel", null)
                .show();
    }

    private static boolean validLink(String url) {
        if (url == null) return false;
        Uri u = Uri.parse(url.trim());
        return "https".equalsIgnoreCase(u.getScheme()) && u.getHost() != null && u.getQueryParameter("t") != null;
    }

    @SuppressWarnings("deprecation")
    private WindowInsets padForSystemBars(View v, WindowInsets insets) {
        if (Build.VERSION.SDK_INT >= 30) {
            Insets b = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.ime());
            v.setPadding(b.left, b.top, b.right, b.bottom);
            return WindowInsets.CONSUMED;
        }
        v.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
        return insets.consumeSystemWindowInsets();
    }

    // ---- setup: paste the link printed by `python -m app.serve` -------------------------
    private void showSetup(String problem) {
        destroyWeb();
        root.removeAllViews();
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setBackgroundColor(PAPER);
        int pad = dp(24);
        box.setPadding(pad, pad, pad, pad);

        TextView title = text("सावधान · Saavdhaan", 30, true);
        TextView help = text("आसान तरीका: फ़ोन के ब्राउज़र में Saavdhaan पेज खोलें और ऊपर “ऐप” दबाएँ।\n"
                + "Easiest: open the Saavdhaan page in the phone's browser and tap “App” at the top.\n\n"
                + "या अपना लिंक (?t=… वाला) यहाँ डालें · Or paste your link (the one with ?t=…): "
                + "your server's address, or the link python -m app.serve prints on the laptop.", 17, false);
        EditText link = new EditText(this);
        link.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        link.setHint("https://saavdhaan.onrender.com/?t=…");
        link.setText(prefs.getString(KEY_URL, ""));
        link.setTextSize(18);
        Button go = new Button(this);
        go.setText("जोड़ें · Connect");
        go.setTextSize(20);
        TextView err = text(problem == null ? "" : problem, 16, false);
        err.setTextColor(Color.rgb(0x8d, 0x0c, 0x0c));

        go.setOnClickListener(v -> {
            String url = link.getText().toString().trim();
            if (!validLink(url)) {
                err.setText("लिंक https://…/?t=… जैसा होना चाहिए · The link must look like https://…/?t=…");
                return;
            }
            prefs.edit().putString(KEY_URL, url).apply();
            openApp(url);
        });

        for (View v : new View[]{title, help, link, go, err}) {
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            lp.topMargin = dp(14);
            box.addView(v, lp);
        }
        root.addView(box, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
    }

    /** Leave the WebView's own callbacks before tearing it down. */
    private void showSetupLater(String problem) {
        root.post(() -> showSetup(problem));
    }

    // ---- the guardian page ------------------------------------------------------------------
    private void openApp(String url) {
        destroyWeb();
        root.removeAllViews();
        origin = Uri.parse(url);
        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);                   // token, voice and opt-ins live in localStorage
        s.setMediaPlaybackRequiresUserGesture(false);   // spoken alerts arrive without a tap

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(PermissionRequest req) {
                runOnUiThread(() -> {
                    boolean mic = Arrays.asList(req.getResources()).contains(PermissionRequest.RESOURCE_AUDIO_CAPTURE);
                    if (!sameOrigin(req.getOrigin()) || !mic) { req.deny(); return; }
                    if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
                        req.grant(new String[]{PermissionRequest.RESOURCE_AUDIO_CAPTURE});
                        return;
                    }
                    if (pendingMic != null) pendingMic.deny(); // never leave an older request hanging
                    pendingMic = req;
                    requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, ASK_MIC);
                });
            }

            @Override
            public void onPermissionRequestCanceled(PermissionRequest req) {
                if (req == pendingMic) pendingMic = null;
            }
        });

        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
                X509Certificate cert = error.getCertificate().getX509Certificate();
                String fp = cert == null ? null : fingerprint(cert);
                if (fp == null || !sameOrigin(Uri.parse(error.getUrl()))) {
                    handler.cancel();
                    return;
                }
                String pinKey = KEY_PIN + originKey(origin);
                if (fp.equals(prefs.getString(pinKey, ""))) {
                    handler.proceed();
                    return;
                }
                if (sslDialog != null && sslDialog.isShowing()) { // one question at a time
                    handler.cancel();
                    return;
                }
                boolean renewed = prefs.contains(pinKey);
                sslDialog = new AlertDialog.Builder(MainActivity.this)
                        .setTitle(renewed ? "सर्वर का सर्टिफ़िकेट बदला है · Server certificate changed"
                                          : "इस लैपटॉप पर भरोसा करें? · Trust this laptop?")
                        .setMessage("Compare with the fingerprint printed by python -m app.serve:\n\n"
                                + origin.getHost() + "\n\nSHA-256\n" + fp
                                + (renewed ? "\n\napp.serve renews its certificate every 25 days. If you did not restart it, do not trust this." : ""))
                        .setCancelable(false)
                        .setPositiveButton("भरोसा करें · Trust", (d, w) -> {
                            prefs.edit().putString(pinKey, fp).apply();
                            handler.proceed();
                        })
                        .setNegativeButton("रद्द करें · Cancel", (d, w) -> {
                            handler.cancel();
                            showSetupLater("सर्टिफ़िकेट पर भरोसा नहीं किया · Certificate not trusted");
                        })
                        .show();
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                Uri u = req.getUrl();
                if (sameOrigin(u)) return false; // the app and its reports stay inside
                String scheme = u.getScheme() == null ? "" : u.getScheme().toLowerCase(Locale.ROOT);
                if (scheme.equals("http") || scheme.equals("https")) { // cybercrime.gov.in and the like
                    try {
                        startActivity(new Intent(Intent.ACTION_VIEW, u));
                    } catch (ActivityNotFoundException ignored) {
                    }
                }
                return true;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError e) {
                // a refused certificate is handled by the trust dialog, not reported as "cannot reach"
                if (req.isForMainFrame() && e.getErrorCode() != WebViewClient.ERROR_FAILED_SSL_HANDSHAKE) {
                    showSetupLater("Saavdhaan से नहीं जुड़ पाया · Cannot reach Saavdhaan: " + e.getDescription()
                            + "\nIs the internet on? A sleeping free server can take a minute to wake: try again."
                            + "\nOn a laptop: same Wi-Fi? Is python -m app.serve running? Did its IP change?");
                }
            }
        });

        root.addView(web, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        web.loadUrl(url);
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] results) {
        if (code != ASK_MIC || pendingMic == null) return;
        PermissionRequest req = pendingMic;
        pendingMic = null;
        try {
            if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) {
                req.grant(new String[]{PermissionRequest.RESOURCE_AUDIO_CAPTURE});
            } else {
                req.deny();
            }
        } catch (IllegalStateException ignored) { // the page went away while Android was asking
        }
    }

    @Override
    @SuppressWarnings("deprecation")
    @android.annotation.SuppressLint("GestureBackNavigation")
    public void onBackPressed() { // Android 12 and older; 13+ uses the callback registered in onCreate
        handleBack();
    }

    private void handleBack() {
        if (web == null) { finish(); return; }
        if (web.canGoBack()) { web.goBack(); return; }
        new AlertDialog.Builder(this)
                .setItems(new String[]{"लिंक बदलें · Change link", "बंद करें · Close app"}, (d, which) -> {
                    if (which == 0) showSetup(null);
                    else finish();
                })
                .show();
    }

    @Override
    protected void onDestroy() {
        destroyWeb();
        super.onDestroy();
    }

    // ---- helpers ----------------------------------------------------------------------------
    private void destroyWeb() {
        if (sslDialog != null) {
            sslDialog.dismiss();
            sslDialog = null;
        }
        if (pendingMic != null) {
            try { pendingMic.deny(); } catch (IllegalStateException ignored) { }
            pendingMic = null;
        }
        if (web == null) return;
        root.removeView(web);
        web.destroy(); // releases the mic and closes the WebSocket
        web = null;
    }

    private static int port(Uri u) {
        if (u.getPort() != -1) return u.getPort();
        return "http".equalsIgnoreCase(u.getScheme()) ? 80 : 443;
    }

    private static String originKey(Uri u) {
        return (u.getScheme() + "://" + u.getHost() + ":" + port(u)).toLowerCase(Locale.ROOT);
    }

    private boolean sameOrigin(Uri u) {
        return origin != null && u != null && u.getHost() != null && originKey(u).equals(originKey(origin));
    }

    static String fingerprint(X509Certificate cert) {
        try {
            byte[] d = MessageDigest.getInstance("SHA-256").digest(cert.getEncoded());
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < d.length; i++) sb.append(i == 0 ? "" : ":").append(String.format("%02X", d[i]));
            return sb.toString();
        } catch (Exception e) {
            return null;
        }
    }

    private TextView text(String s, int sp, boolean bold) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(sp);
        t.setTextColor(INK);
        t.setGravity(Gravity.START);
        if (bold) t.setTypeface(t.getTypeface(), android.graphics.Typeface.BOLD);
        return t;
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
