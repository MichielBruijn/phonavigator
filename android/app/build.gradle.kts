plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "nu.bruijn.phonavigator"
    compileSdk = 35

    defaultConfig {
        applicationId = "nu.bruijn.phonavigator"
        minSdk = 26
        targetSdk = 35
        versionCode = 4
        versionName = "0.2.0"
    }

    // Release key lives outside the repo; without it the release build is unsigned.
    val keyDir = File(System.getProperty("user.home"), ".android-keystores")
    val keyFile = File(keyDir, "phonavigator-release.jks")
    if (keyFile.exists()) {
        val pw = File(keyDir, "phonavigator-release.password").readText().trim()
        signingConfigs.create("release") {
            storeFile = keyFile
            storePassword = pw
            keyAlias = "phonavigator"
            keyPassword = pw
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.findByName("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}

// No AndroidX on purpose: platform APIs suffice and the APK stays small.
