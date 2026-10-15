package io.github.mendypf.nomoredoom

import android.content.ComponentName
import android.content.Context
import android.view.accessibility.AccessibilityManager
import java.lang.ref.WeakReference

/** How the app's own screen and the adb receiver reach the running service. Same process, so no IPC. */
internal object Live {
    private var ref: WeakReference<WatchService>? = null

    /** Called on the main thread whenever something the screen shows may have changed. */
    var onChange: (() -> Unit)? = null

    val service: WatchService? get() = ref?.get()

    fun attach(service: WatchService) {
        ref = WeakReference(service)
        changed()
    }

    fun detach(service: WatchService) {
        if (ref?.get() === service) ref = null
        changed()
    }

    fun changed() {
        onChange?.invoke()
    }
}

internal fun serviceComponent(context: Context) = ComponentName(context, WatchService::class.java)

/** The apps this build may hear from, read back from Android rather than repeated in code. */
internal fun watchedPackages(context: Context): Set<String> =
    context.getSystemService(AccessibilityManager::class.java)
        .installedAccessibilityServiceList
        .firstOrNull { it.resolveInfo.serviceInfo.packageName == context.packageName }
        ?.packageNames?.toSet().orEmpty()
