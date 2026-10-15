// Stand-ins for Instagram, YouTube and TikTok, for the emulator test only.
//
// The real apps cannot be installed on a test emulator (no store, no login), and
// the service only listens to their exact package names. So each flavour here is
// a one-screen app that carries a real package name and, on its "short" screen,
// a full-height view with the real view id. Nothing here ships to anyone: a real
// phone refuses to install these over the real apps, because the signatures differ.
plugins {
    alias(libs.plugins.android.application)
}

android {
    namespace = "io.github.mendypf.nomoredoom.fakes"
    compileSdk {
        version = release(37)
    }

    defaultConfig {
        minSdk = 26
        targetSdk = 37
        versionCode = 1
        versionName = "test"
    }

    buildFeatures {
        buildConfig = true
    }

    flavorDimensions += "app"
    productFlavors {
        create("instagram") {
            applicationId = "com.instagram.android"
            buildConfigField("String", "SHORT_VIEW_ID", "\"clips_viewer_view_pager\"")
            buildConfigField("int", "SHORT_VIEW_HEIGHT", "60")
        }
        create("youtube") {
            applicationId = "com.google.android.youtube"
            buildConfigField("String", "SHORT_VIEW_ID", "\"reel_recycler\"")
            buildConfigField("int", "SHORT_VIEW_HEIGHT", "-1")
        }
        create("tiktok") {
            applicationId = "com.zhiliaoapp.musically"
            buildConfigField("String", "SHORT_VIEW_ID", "\"\"")
            buildConfigField("int", "SHORT_VIEW_HEIGHT", "0")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    lint {
        // A test prop, not a product.
        abortOnError = false
        checkReleaseBuilds = false
    }
}
