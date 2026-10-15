import java.util.Properties
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.android.application)
}

// The release key lives outside the repo: in ~/.nomoredoom, or wherever
// -Pnomoredoom.keystore or NOMOREDOOM_KEYSTORE points, with its passwords in a
// keystore.properties file next to it. Without it (a fresh clone, a pull request,
// CI) the release build is signed with the debug key so it still installs.
val keystoreFile = providers.gradleProperty("nomoredoom.keystore")
    .orElse(providers.environmentVariable("NOMOREDOOM_KEYSTORE"))
    .orElse(providers.systemProperty("user.home").map { "$it/.nomoredoom/nomoredoom-release.jks" })
    .map { file(it) }
    .orNull
    ?.takeIf { it.isFile }
val keystoreProps = Properties().apply {
    keystoreFile?.resolveSibling("keystore.properties")?.takeIf { it.isFile }?.inputStream()?.use(::load)
}

fun secret(name: String): String? =
    keystoreProps.getProperty(name) ?: providers.environmentVariable("NOMOREDOOM_${name.uppercase()}").orNull

android {
    namespace = "io.github.mendypf.nomoredoom"
    compileSdk {
        version = release(37)
    }

    defaultConfig {
        applicationId = "io.github.mendypf.nomoredoom"
        minSdk = 26
        targetSdk = 37
        versionCode = 1
        versionName = "0.1.0"
    }

    signingConfigs {
        if (keystoreFile != null) {
            create("release") {
                storeFile = keystoreFile
                storePassword = secret("storePassword")
                keyAlias = secret("keyAlias")
                keyPassword = secret("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        buildConfig = true
    }

    lint {
        warningsAsErrors = true
        abortOnError = true
        checkDependencies = true
    }

    // No dependency list encrypted for Google Play inside the APK: this app is
    // never on Play, and without the blob two builds of one commit are identical.
    dependenciesInfo {
        includeInApk = false
        includeInBundle = false
    }

    packaging {
        resources.excludes += setOf("META-INF/*.version", "META-INF/*.kotlin_module", "kotlin/**", "DebugProbesKt.bin")
    }

    testOptions {
        unitTests.isIncludeAndroidResources = false
    }
}

kotlin {
    compilerOptions {
        jvmTarget = JvmTarget.JVM_17
        allWarningsAsErrors = true
    }
}

dependencies {
    implementation(project(":core"))

    testImplementation(libs.kotlin.test.junit5)
    testImplementation(platform(libs.junit.bom))
    testImplementation(libs.junit.jupiter)
    testRuntimeOnly(libs.junit.launcher)
}

tasks.withType<Test>().configureEach {
    useJUnitPlatform()
}
