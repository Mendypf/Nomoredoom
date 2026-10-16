package io.github.mendypf.nomoredoom

import android.accessibilityservice.AccessibilityService
import android.annotation.SuppressLint
import android.graphics.PixelFormat
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.os.Build
import android.provider.Settings
import android.util.Log
import android.view.ContextThemeWrapper
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.WindowManager
import android.widget.FrameLayout
import io.github.mendypf.nomoredoom.core.Box
import io.github.mendypf.nomoredoom.core.Gags
import io.github.mendypf.nomoredoom.core.Ui

/**
 * Draws a [Ui] on top of whatever app is in front, and nothing else. It decides
 * nothing: the engine says whether the memes belong on screen, this makes the screen match.
 *
 * The window is an accessibility overlay, the one kind an accessibility service
 * may add without the "draw over other apps" permission.
 */
internal class Stage(
    private val service: AccessibilityService,
    private val art: Art,
) {
    /** The memes: which GIFs land on the video. */
    var gags: Gags = Gags.NONE

    private val windows = service.getSystemService(WindowManager::class.java)
    private val audio = service.getSystemService(AudioManager::class.java)
    private val themed = ContextThemeWrapper(service, R.style.Theme_Nomoredoom_Overlay)
    private val inflater = LayoutInflater.from(themed)

    private var shown = Ui.NONE
    private var area = Box(0, 0, 0, 0)
    private var cover: View? = null
    private var chaos: Chaos? = null
    private var quiet = false

    private val focusRequest = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
        .setAudioAttributes(
            AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_ASSISTANCE_SONIFICATION)
                .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                .build(),
        )
        .setOnAudioFocusChangeListener { }
        .build()

    /** @param safe the part of the screen free of system bars and banners; everything is drawn inside it */
    fun show(ui: Ui, safe: Box) {
        if (ui == shown && safe == area) return
        try {
            if (safe != area) {
                area = safe
                cover?.let { windows.updateViewLayout(it, params()) }
            }
            if (ui.cover && cover == null) showCover()
            if (!ui.cover) cover = remove(cover)
            showQuiet(ui.cover)
            shown = ui
        } catch (e: WindowManager.BadTokenException) {
            // Android took the service's window token away (the service is being switched off).
            Log.w(TAG, "could not draw, the service is going away", e)
            forget()
        }
    }

    fun clear() {
        show(Ui.NONE, area)
        forget()
    }

    /** What is drawn right now: `memes` or `-`. */
    fun onScreen(): String = if (cover != null) "memes" else "-"

    /**
     * The memes as a test on a computer needs them: the line, the last GIF asked for (`name:playing`,
     * `still`, `loading`, `missing` or `broken`), how many have landed, where each one on screen sits
     * (`middle x,middle y,tilt`, separated by `;`), and where the line is.
     */
    fun report(): Map<String, String> {
        val view = cover ?: return emptyMap()
        val pile = chaos ?: return emptyMap()
        val out = linkedMapOf(
            "line" to themed.getString(R.string.card_agent),
            "gif" to pile.state,
            "popped" to "${pile.popped}",
            "spots" to pile.spots().joinToString(";"),
        )
        boxOf(view.findViewById(R.id.chaos_line))?.let { out["line_box"] = it.joinToString(",") }
        return out
    }

    private fun boxOf(view: View): IntArray? {
        if (view.visibility != View.VISIBLE || view.width == 0) return null
        val spot = IntArray(2)
        view.getLocationOnScreen(spot)
        return intArrayOf(spot[0], spot[1], spot[0] + view.width, spot[1] + view.height)
    }

    /** Every GIF on the phone, shuffled anew for each visit, slammed onto the screen. There is no X. */
    private fun showCover() {
        val view = inflater.inflate(R.layout.overlay_memes, FrameLayout(themed), false)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) view.accessibilityPaneTitle = themed.getString(R.string.card_agent)
        windows.addView(view, params())
        cover = view
        val reel = gags.media.filter { art.file(it) != null }.shuffled()
        chaos = Chaos(view, art, reel, { area }, motionAllowed()).also { it.start() }
    }

    private fun motionAllowed(): Boolean =
        Settings.Global.getFloat(service.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) > 0f

    /** Ask for the sound while the memes are up; video apps pause when another app takes it. */
    private fun showQuiet(on: Boolean) {
        if (on == quiet) return
        quiet = on
        if (on) audio.requestAudioFocus(focusRequest) else audio.abandonAudioFocusRequest(focusRequest)
    }

    private fun remove(view: View?): View? {
        if (view != null) {
            chaos?.stop()
            chaos = null
            runCatching { windows.removeViewImmediate(view) }
                .onFailure { Log.w(TAG, "window was already gone", it) }
        }
        return null
    }

    private fun forget() {
        cover = remove(cover)
        if (quiet) {
            quiet = false
            audio.abandonAudioFocusRequest(focusRequest)
        }
        shown = Ui.NONE
    }

    /**
     * The one window: it covers the video and takes touches, so it cannot be swiped.
     *
     * Android stacks these overlays above its own status bar, button bar and call
     * banner, and hands them no insets to avoid those. So the window is placed by
     * hand inside [area], the part of the screen the system is not using; a window
     * lying over the Home gesture would lock the phone.
     *
     * It is not focusable, so the keyboard, the back gesture and the volume keys keep
     * going to the app underneath. The memes can be annoying; they must never be a trap.
     */
    @SuppressLint("RtlHardcoded") // x is counted from the left edge of the screen whatever the language
    private fun params() = WindowManager.LayoutParams(
        area.width,
        area.height,
        area.left,
        area.top,
        WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
            WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
            WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
        PixelFormat.TRANSLUCENT,
    ).apply {
        gravity = Gravity.TOP or Gravity.LEFT
        title = "Nomoredoom"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
            fitInsetsTypes = 0
        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
        }
    }

    private companion object {
        const val TAG = "Nomoredoom"
    }
}
