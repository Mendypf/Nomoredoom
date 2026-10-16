package io.github.mendypf.nomoredoom

import android.content.Context
import android.graphics.ImageDecoder
import android.graphics.drawable.AnimatedImageDrawable
import android.graphics.drawable.Drawable
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log
import java.io.File
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/**
 * The GIFs for the popups.
 *
 * The app never fetches them. They are files the install tool copies over adb into the app's own folder on the phone's shared storage
 * (`Android/data/<this app>/files/gags`), which needs no permission to read.
 *
 * Only on Android 11 and newer. Before that, any app holding the storage
 * permission could drop a file into this folder, and a GIF is not worth letting
 * a stranger's file be opened inside an accessibility service.
 */
internal class Art(context: Context) {
    private val main = Handler(Looper.getMainLooper())
    private var worker: ExecutorService? = null

    /** Asking for the folder is what creates it, owned by this app. Null on Android 10 and older. */
    val folder: File? =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) context.applicationContext.getExternalFilesDir(FOLDER) else null

    fun count(): Int = folder?.listFiles()?.count { it.isFile && it.extension.lowercase() in ENDINGS } ?: 0

    /** The file for a GIF by name, or null when it is not on the phone (or is too big to be a GIF). */
    fun file(name: String): File? {
        val dir = folder ?: return null
        return ENDINGS.map { File(dir, "$name.$it") }.firstOrNull { it.isFile && it.length() in 1..MAX_BYTES }
    }

    /**
     * Opens the file on a side thread and hands the picture to [onReady] on the main thread; null if it would not open.
     * A picture bigger than [maxSide] on its longest side is opened at that size.
     */
    fun load(file: File, maxSide: Int = MAX_SIDE, onReady: (Drawable?) -> Unit) {
        val thread = worker ?: Executors.newSingleThreadExecutor { Thread(it, "nomoredoom-art") }.also { worker = it }
        thread.execute {
            val drawable = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                runCatching {
                    ImageDecoder.decodeDrawable(ImageDecoder.createSource(file)) { decoder, info, _ ->
                        val longest = maxOf(info.size.width, info.size.height)
                        if (longest > maxSide) {
                            val scale = maxSide.toFloat() / longest
                            decoder.setTargetSize(
                                (info.size.width * scale).toInt().coerceAtLeast(1),
                                (info.size.height * scale).toInt().coerceAtLeast(1),
                            )
                        }
                    }.also { if (it is AnimatedImageDrawable) it.repeatCount = AnimatedImageDrawable.REPEAT_INFINITE }
                }.onFailure { Log.w(TAG, "could not open ${file.name}", it) }.getOrNull()
            } else {
                null
            }
            main.post { onReady(drawable) }
        }
    }

    fun close() {
        worker?.shutdownNow()
        worker = null
    }

    companion object {
        const val FOLDER = "gags"
        private const val TAG = "Nomoredoom"
        private val ENDINGS = listOf("webp", "gif")
        private const val MAX_BYTES = 12L * 1024 * 1024
        private const val MAX_SIDE = 1_080
    }
}
