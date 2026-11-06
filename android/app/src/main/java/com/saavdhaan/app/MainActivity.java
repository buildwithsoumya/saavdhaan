package com.saavdhaan.app;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Insets;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowManager;
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

/**
 * Saavdhaan listener app: a full-screen WebView on the laptop backend started with
 * `python -m app.serve`. The API keys stay on the laptop; this app only adds what a
 * browser tab lacks: an installed icon, mic permission without prompts on every visit,
 * the screen kept on, and trust in the backend's self-signed certificate.
 *
 * Certificate trust is pinned on first use: the app shows the certificate's SHA-256
 * fingerprint, which app.serve also prints, and only proceeds for that exact certificate.
 */
public class MainActivity extends Activity {
    private static final String PREFS = "saavdhaan", KEY_URL = "url", KEY_PIN = "pin";
    private static final int ASK_MIC = 1;
    private static final int INK = Color.rgb(0x15, 0x23, 0x3f), PAPER = Color.rgb(0xd9, 0xe7, 0xef);

    private SharedPreferences prefs;
    private FrameLayout root;
    private WebView web;
    private PermissionRequest pendingMic;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON); // the mic stops if the screen sleeps
        root = new FrameLayout(this);
        root.setBackgroundColor(INK);
        root.setOnApplyWindowInsetsListener((v, insets) -> { // targetSdk 35+ draws edge to edge
            Insets bars = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.ime());
            v.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            return WindowInsets.CONSUMED;
        });
        setContentView(root);
        String url = prefs.getString(KEY_URL, null);
        if (url == null) showSetup(null);
        else openApp(url);
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
        TextView help = text("लैपटॉप पर python -m app.serve चलाएँ और जो लिंक दिखे उसे यहाँ डालें।\n"
                + "Run python -m app.serve on the laptop and paste the link it prints:\n"
                + "https://<laptop-ip>:8443/?t=<token>", 17, false);
        EditText link = new EditText(this);
        link.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        link.setHint("https://192.168.1.5:8443/?t=…");
        link.setText(prefs.getString(KEY_URL, ""));
        link.setTextSize(18);
        Button go = new Button(this);
        go.setText("जोड़ें · Connect");
        go.setTextSize(20);
        TextView err = text(problem == null ? "" : problem, 16, false);
        err.setTextColor(Color.rgb(0x8d, 0x0c, 0x0c));

        go.setOnClickListener(v -> {
            String url = link.getText().toString().trim();
            Uri u = Uri.parse(url);
            if (!"https".equals(u.getScheme()) || u.getHost() == null || u.getQueryParameter("t") == null) {
                err.setText("लिंक https://…:8443/?t=… जैसा होना चाहिए · The link must look like https://…:8443/?t=…");
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

    // ---- the guardian page ------------------------------------------------------------------
    private void openApp(String url) {
        destroyWeb();
        root.removeAllViews();
        String host = Uri.parse(url).getHost();
        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);                   // token, voice and opt-ins live in localStorage
        s.setMediaPlaybackRequiresUserGesture(false);   // spoken alerts arrive without a tap

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(PermissionRequest req) {
                runOnUiThread(() -> {
                    boolean ours = host.equals(req.getOrigin().getHost());
                    boolean mic = java.util.Arrays.asList(req.getResources())
                            .contains(PermissionRequest.RESOURCE_AUDIO_CAPTURE);
                    if (!ours || !mic) { req.deny(); return; }
                    if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
                        req.grant(new String[]{PermissionRequest.RESOURCE_AUDIO_CAPTURE});
                    } else {
                        pendingMic = req;
                        requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, ASK_MIC);
                    }
                });
            }
        });

        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
                X509Certificate cert = error.getCertificate().getX509Certificate();
                String fp = cert == null ? null : fingerprint(cert);
                if (fp == null || !host.equals(Uri.parse(error.getUrl()).getHost())) {
                    handler.cancel();
                    return;
                }
                if (fp.equals(prefs.getString(KEY_PIN, ""))) {
                    handler.proceed();
                    return;
                }
                boolean renewed = prefs.contains(KEY_PIN);
                new AlertDialog.Builder(MainActivity.this)
                        .setTitle(renewed ? "सर्वर का सर्टिफ़िकेट बदला है · Server certificate changed"
                                          : "इस लैपटॉप पर भरोसा करें? · Trust this laptop?")
                        .setMessage("Compare with the fingerprint printed by python -m app.serve:\n\n"
                                + host + "\n\nSHA-256\n" + fp
                                + (renewed ? "\n\napp.serve renews its certificate every 25 days. If you did not restart it, do not trust this." : ""))
                        .setCancelable(false)
                        .setPositiveButton("भरोसा करें · Trust", (d, w) -> {
                            prefs.edit().putString(KEY_PIN, fp).apply();
                            handler.proceed();
                        })
                        .setNegativeButton("रद्द करें · Cancel", (d, w) -> {
                            handler.cancel();
                            showSetup("सर्टिफ़िकेट पर भरोसा नहीं किया · Certificate not trusted");
                        })
                        .show();
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                Uri u = req.getUrl();
                if (host.equals(u.getHost())) return false; // the app and its reports stay inside
                startActivity(new Intent(Intent.ACTION_VIEW, u)); // cybercrime.gov.in and the like
                return true;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError e) {
                if (req.isForMainFrame()) {
                    showSetup("लैपटॉप से नहीं जुड़ पाया · Cannot reach the laptop: " + e.getDescription()
                            + "\nSame Wi-Fi? Is python -m app.serve running? Did the laptop's IP change?");
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
        if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) {
            pendingMic.grant(new String[]{PermissionRequest.RESOURCE_AUDIO_CAPTURE});
        } else {
            pendingMic.deny();
        }
        pendingMic = null;
    }

    @Override
    @SuppressWarnings("deprecation")
    public void onBackPressed() {
        if (web == null) { super.onBackPressed(); return; }
        if (web.canGoBack()) { web.goBack(); return; }
        new AlertDialog.Builder(this)
                .setItems(new String[]{"लैपटॉप बदलें · Change laptop link", "बंद करें · Close app"}, (d, which) -> {
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
        if (web == null) return;
        root.removeView(web);
        web.destroy(); // releases the mic and closes the WebSocket
        web = null;
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
