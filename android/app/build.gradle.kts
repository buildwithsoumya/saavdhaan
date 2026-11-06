plugins {
    id("com.android.application")
}

android {
    namespace = "com.saavdhaan.app"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.saavdhaan.app"
        minSdk = 29 // SslCertificate.getX509Certificate() for certificate pinning
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
