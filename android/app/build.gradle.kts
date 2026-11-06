plugins {
    id("com.android.application")
}

// Release signing comes from the environment (GitHub Actions secrets), never from the repo.
// The same key must sign every release, or phones refuse to install the update.
val keystoreFile = System.getenv("SAAVDHAAN_KEYSTORE")?.let { file(it) }?.takeIf { it.exists() }
// CI run number: each build has a higher versionCode, which Android requires for updates
val buildNumber = System.getenv("GITHUB_RUN_NUMBER")?.toIntOrNull() ?: 1

android {
    namespace = "com.saavdhaan.app"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.saavdhaan.app"
        minSdk = 29 // SslCertificate.getX509Certificate() for certificate pinning
        targetSdk = 36
        versionCode = buildNumber
        versionName = "0.1.$buildNumber"
    }

    signingConfigs {
        if (keystoreFile != null) {
            create("release") {
                storeFile = keystoreFile
                storePassword = System.getenv("SAAVDHAAN_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("SAAVDHAAN_KEY_ALIAS")
                keyPassword = System.getenv("SAAVDHAAN_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.findByName("release") // unsigned without the key
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
