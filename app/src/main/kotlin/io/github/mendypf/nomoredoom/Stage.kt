package io.github.mendypf.nomoredoom

import android.accessibilityservice.AccessibilityService
import android.annotation.SuppressLint
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.drawable.Animatable
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.Log
import android.util.TypedValue
import android.view.ContextThemeWrapper
import android.view.Gravity
import android.view.HapticFeedbackConstants
import android.view.LayoutInflater
import android.view.View
import android.view.View.MeasureSpec
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.view.WindowManager
import android.view.animation.OvershootInterpolator
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.TextView
import io.github.mendypf.nomoredoom.core.Box
import io.github.mendypf.nomoredoom.core.Card
import io.github.mendypf.nomoredoom.core.Fit
import io.github.mendypf.nomoredoom.core.Gags
import io.github.mendypf.nomoredoom.core.Size
import io.github.mendypf.nomoredoom.core.Stamp
import io.github.mendypf.nomoredoom.core.Ui

/**
 * Draws a [Ui] on top of whatever app is in front, and nothing else. It decides
 * nothing: the engine says what should be on screen, this makes the screen match.
 *
 * The windows are accessibility overlays, the one kind an accessibility service
 * may add without the "draw over other apps" permission.
 */
internal class Stage(
    private val service: AccessibilityService,
    private val art: Art,
    private val onClosed: () -> Unit,
) {
    /** The jokes: which line and which GIF a card gets. */
    var gags: Gags = Gags.NONE

    private val main = Handler(Looper.getMainLooper())
    private val windows = service.getSystemService(WindowManager::class.java)
    private val audio = service.getSystemService(AudioManager::class.java)
    private val themed = ContextThemeWrapper(service, R.style.Theme_Nomoredoom_Overlay)
    private val inflater = LayoutInflater.from(themed)
    private val density = themed.resources.displayMetrics.density

    private var shown = Ui.NONE
    private var area = Box(0, 0, 0, 0)
    private var dim: View? = null
    private var pill: View? = null
    private var card: View? = null
    private var quiet = false
    private var line = ""
    private var artState = NO_ART
    private var stampAt = Stamp.TOP_LEFT

    /** The agent's card: every GIF on the phone, shuffled anew for each visit, and which one is showing. */
    private var reel: List<String> = emptyList()
    private var reelAt = 0
    private var busyCard = false
    private val nextInReel = Runnable { card?.let { showArt(it, reel.getOrNull(++reelAt % reel.size.coerceAtLeast(1))) } }

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
                dim?.let { windows.updateViewLayout(it, params(Kind.BACKDROP)) }
                pill?.let { windows.updateViewLayout(it, params(Kind.PILL)) }
                card?.let {
                    layOut(it)
                    windows.updateViewLayout(it, params(Kind.CARD))
                }
            }
            showDim(ui.dim)
            showPill(ui.pillMinutes)
            showCard(ui.card, replace = ui.card?.id != shown.card?.id)
            showQuiet(ui.quiet)
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

    /** What is drawn right now: dim, pill, card. */
    fun onScreen(): String = listOfNotNull(
        "dim".takeIf { dim != null },
        "pill".takeIf { pill != null },
        "card".takeIf { card != null },
    ).joinToString("+").ifEmpty { "-" }

    /**
     * The card as a test on a computer needs it: its words, its GIF (`name:playing`, `still`,
     * `loading`, `missing`, `broken` or `no-room`), where things are on screen, and whether the X
     * can be pressed yet.
     */
    fun report(): Map<String, String> {
        val view = card ?: return emptyMap()
        val out = linkedMapOf("line" to line, "gif" to artState)
        if (busyCard) out["reel"] = "${if (reel.isEmpty()) 0 else reelAt % reel.size + 1}/${reel.size}"
        boxOf(view.findViewById(R.id.card_holder))?.let { out["card_box"] = it.joinToString(",") }
        boxOf(view.findViewById(R.id.card_gif))?.let { out["gif_box"] = it.joinToString(",") }
        val close = view.findViewById<CloseButton>(R.id.card_close)
        boxOf(close)?.let {
            out["close_at"] = "${(it[0] + it[2]) / 2},${(it[1] + it[3]) / 2}"
            out["close"] = if (close.ready) "ready" else "wait"
        }
        return out
    }

    private fun boxOf(view: View): IntArray? {
        if (view.visibility != View.VISIBLE || view.width == 0) return null
        val spot = IntArray(2)
        view.getLocationOnScreen(spot)
        return intArrayOf(spot[0], spot[1], spot[0] + view.width, spot[1] + view.height)
    }

    private fun showDim(alpha: Float) {
        if (alpha <= 0f) {
            dim = remove(dim)
            return
        }
        val view = dim ?: View(themed).apply {
            setBackgroundColor(Color.BLACK)
            this.alpha = 0f
            windows.addView(this, params(Kind.BACKDROP))
            dim = this
        }
        view.animate().alpha(alpha).setDuration(FADE_MS).start()
    }

    private fun showPill(minutes: Int?) {
        if (minutes == null) {
            pill = remove(pill)
            return
        }
        if (pill != null) return
        val view = inflater.inflate(R.layout.overlay_pill, FrameLayout(themed), false)
        view.findViewById<TextView>(R.id.pill_text).text =
            themed.resources.getQuantityString(R.plurals.pill, minutes, minutes)
        windows.addView(view, params(Kind.PILL))
        view.alpha = 0f
        view.animate().alpha(1f).setDuration(FADE_MS).start()
        pill = view
    }

    private fun showCard(next: Card?, replace: Boolean) {
        if (next == null) {
            card = remove(card)
            return
        }
        if (!replace && card != null) return
        card = remove(card)

        val view = inflater.inflate(R.layout.overlay_card, FrameLayout(themed), false)
        val minutes = themed.resources.getQuantityString(R.plurals.minutes, next.minutes, next.minutes)
        val caption = view.findViewById<TextView>(R.id.card_caption)
        val close = view.findViewById<CloseButton>(R.id.card_close)
        view.findViewById<TextView>(R.id.card_minutes).text = minutes
        view.findViewById<TextView>(R.id.card_stamp).text = themed.getString(R.string.stamp, next.minutes)
        view.findViewById<View>(R.id.card_panel).clipToOutline = true // rounds the picture's corners with the card's

        // The joke: a line of its own, and a GIF if one is on the phone. The agent's card has one
        // line of its own and plays every GIF on the phone in turn.
        busyCard = next is Card.Busy
        val gag = if (busyCard) null else gags.pick(next.over, next.id)
        val words = gag?.text(next.minutes)?.takeIf { it.isNotEmpty() }
        when {
            busyCard -> caption.setText(R.string.card_agent)
            words != null -> caption.text = words
            next is Card.Farewell -> caption.setText(R.string.card_going_home)
        }
        line = caption.text.toString()
        stampAt = if (busyCard) Stamp.OFF else gag?.stamp ?: Stamp.TOP_LEFT
        reel = if (busyCard) {
            gags.all.mapNotNull { it.media }.distinct().filter { art.file(it) != null }.shuffled()
        } else {
            emptyList()
        }
        reelAt = 0

        when (next) {
            is Card.Tap -> close.start(0)
            is Card.Hold -> close.start(next.holdMs)
            is Card.Farewell -> close.visibility = View.GONE // it leaves by itself
            is Card.Busy -> close.visibility = View.GONE // it leaves when the agent is done
        }
        close.onClose = onClosed

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            // What a screen reader says when the popup appears.
            view.accessibilityPaneTitle = if (busyCard) line else "$minutes. $line"
        }
        layOut(view)
        windows.addView(view, params(Kind.CARD))
        view.alpha = 0f
        view.animate().alpha(1f).setDuration(FADE_MS).start()
        card = view
        showArt(view, if (busyCard) reel.firstOrNull() else gag?.media)
    }

    /** Loads the card's GIF. Until it is there, and when it is not on the phone, the card shows the minutes in its place. */
    private fun showArt(view: View, name: String?) {
        val file = name?.let(art::file)
        artState = when {
            name == null -> NO_ART
            file == null -> "$name:missing"
            else -> "$name:loading"
        }
        if (file == null) return
        art.load(file) { picture ->
            if (card !== view) return@load // that card is gone already
            if (busyCard && reel.size > 1) main.postDelayed(nextInReel, if (picture == null) RETRY_REEL_MS else REEL_MS)
            if (picture == null) {
                artState = "$name:broken"
                return@load
            }
            val image = view.findViewById<ImageView>(R.id.card_gif)
            (image.drawable as? Animatable)?.stop()
            image.setImageDrawable(picture)
            if (busyCard && image.visibility == View.VISIBLE) {
                image.alpha = 0.3f
                image.animate().alpha(1f).setDuration(FADE_MS).start()
            }
            if (!layOut(view)) {
                artState = "$name:no-room"
                return@load
            }
            // Someone who switched animations off in Android's settings gets the first frame, not a loop.
            val moving = picture is Animatable && motionAllowed()
            if (moving) (picture as Animatable).start()
            artState = "$name:" + if (moving) "playing" else "still"
            slam(view.findViewById(R.id.card_stamp))
        }
    }

    /**
     * Sizes the card for the space there is: the GIF as big as fits with its line still on screen.
     * Returns false when the card shows no GIF (none loaded, or no room for one).
     */
    private fun layOut(view: View): Boolean {
        val holder = view.findViewById<View>(R.id.card_holder)
        val panel = view.findViewById<View>(R.id.card_panel)
        val image = view.findViewById<ImageView>(R.id.card_gif)
        val minutes = view.findViewById<View>(R.id.card_minutes)
        val stamp = view.findViewById<TextView>(R.id.card_stamp)
        val edge = (EDGE_DP * density).toInt()
        val widest = minOf(area.width - 2 * edge, (MAX_CARD_DP * density).toInt()).coerceAtLeast(1)
        val narrowest = minOf(widest, (MIN_CARD_DP * density).toInt())
        val picture = image.drawable

        var width = widest
        var gif: Size? = null
        if (picture != null) {
            image.visibility = View.GONE
            minutes.visibility = View.GONE
            // Twice, because a tall GIF makes the card narrower and that can wrap the line under it.
            repeat(2) {
                panel.measure(
                    MeasureSpec.makeMeasureSpec(width, MeasureSpec.EXACTLY),
                    MeasureSpec.makeMeasureSpec(0, MeasureSpec.UNSPECIFIED),
                )
                val headroom = if (stampAt == Stamp.TOP_LEFT) (STAMP_HEADROOM_DP * density).toInt() else 0
                val room = minOf(
                    area.height - 2 * edge - headroom - panel.measuredHeight,
                    (area.height * MAX_ART_SHARE).toInt(),
                )
                gif = if (room >= MIN_ART_DP * density) {
                    Fit.inside(picture.intrinsicWidth, picture.intrinsicHeight, widest, room)
                } else {
                    null
                }
                width = gif?.width?.coerceAtLeast(narrowest) ?: widest
            }
        }

        val fitted = gif
        image.visibility = if (fitted != null) View.VISIBLE else View.GONE
        minutes.visibility = if (fitted != null || busyCard) View.GONE else View.VISIBLE
        stamp.visibility = if (fitted != null && stampAt != Stamp.OFF) View.VISIBLE else View.GONE
        if (fitted != null) {
            image.layoutParams = image.layoutParams.apply { height = fitted.height }
            if (stampAt != Stamp.OFF) placeStamp(stamp, fitted, width)
        }
        holder.layoutParams = holder.layoutParams.apply { this.width = width }
        return fitted != null
    }

    /** The stamp is one size on every card, smaller only where the GIF is too small for it, and hangs off its corner. */
    @SuppressLint("RtlHardcoded") // the stamp is part of the picture, which does not mirror
    private fun placeStamp(stamp: TextView, gif: Size, cardWidth: Int) {
        var text = STAMP_TEXT_DP * density
        fun measure() {
            stamp.setTextSize(TypedValue.COMPLEX_UNIT_PX, text)
            stamp.setPadding((text * 0.42f).toInt(), (text * 0.12f).toInt(), (text * 0.42f).toInt(), (text * 0.12f).toInt())
            stamp.measure(MeasureSpec.UNSPECIFIED, MeasureSpec.UNSPECIFIED)
        }
        measure()
        val shrink = minOf(
            cardWidth * STAMP_MAX_WIDTH / stamp.measuredWidth,
            gif.height * STAMP_MAX_HEIGHT / stamp.measuredHeight,
        )
        if (shrink < 1f) {
            text *= shrink
            measure()
        }
        val low = stampAt == Stamp.BOTTOM_LEFT || stampAt == Stamp.BOTTOM_RIGHT
        val right = stampAt == Stamp.BOTTOM_RIGHT
        val inset = (STAMP_INSET_DP * density).toInt()
        stamp.layoutParams = (stamp.layoutParams as FrameLayout.LayoutParams).apply {
            gravity = Gravity.TOP or if (right) Gravity.RIGHT else Gravity.LEFT
            topMargin = if (low) gif.height - stamp.measuredHeight - inset else 0
        }
        stamp.translationX = STAMP_HANG_DP * density * if (right) 1 else -1
        stamp.translationY = if (low) 0f else -stamp.measuredHeight * STAMP_LIFT
        stamp.rotation = if (low == right) STAMP_TILT else -STAMP_TILT
    }

    /** The stamp starts big and see-through, then lands with a buzz. */
    private fun slam(stamp: View) {
        if (stamp.visibility != View.VISIBLE) return
        stamp.scaleX = STAMP_FROM
        stamp.scaleY = STAMP_FROM
        stamp.alpha = 0f
        stamp.animate().scaleX(1f).scaleY(1f).alpha(1f)
            .setStartDelay(STAMP_DELAY_MS).setDuration(STAMP_MS)
            .setInterpolator(OvershootInterpolator(STAMP_BOUNCE))
            .withEndAction {
                val buzz = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) HapticFeedbackConstants.REJECT
                else HapticFeedbackConstants.LONG_PRESS
                stamp.performHapticFeedback(buzz)
            }
            .start()
    }

    private fun motionAllowed(): Boolean =
        Settings.Global.getFloat(service.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) > 0f

    /** Ask for the sound while the card is up; video apps pause when another app takes it. */
    private fun showQuiet(on: Boolean) {
        if (on == quiet) return
        quiet = on
        if (on) audio.requestAudioFocus(focusRequest) else audio.abandonAudioFocusRequest(focusRequest)
    }

    private fun remove(view: View?): View? {
        if (view != null) {
            if (view === card) {
                main.removeCallbacks(nextInReel)
                busyCard = false
                reel = emptyList()
            }
            view.animate().cancel()
            view.findViewById<View>(R.id.card_stamp)?.animate()?.cancel()
            (view.findViewById<ImageView>(R.id.card_gif)?.drawable as? Animatable)?.stop()
            runCatching { windows.removeViewImmediate(view) }
                .onFailure { Log.w(TAG, "window was already gone", it) }
        }
        return null
    }

    private fun forget() {
        dim = remove(dim)
        pill = remove(pill)
        card = remove(card)
        if (quiet) {
            quiet = false
            audio.abandonAudioFocusRequest(focusRequest)
        }
        shown = Ui.NONE
    }

    /**
     * Three kinds of window, and only one of them takes touches:
     *
     *  - BACKDROP: black, darkening the video between cards. Touches fall through it.
     *  - PILL: a strip at the top. Touches fall through it.
     *  - CARD: covers the video and takes touches, so it cannot be swiped.
     *
     * Android stacks these overlays above its own status bar, button bar and call
     * banner, and hands them no insets to avoid those. So each window is placed by
     * hand inside [area], the part of the screen the system is not using; a card
     * lying over the Home gesture would lock the phone.
     *
     * None of them is focusable, so the keyboard, the back gesture and the volume keys keep
     * going to the app underneath. A popup can be annoying; it must never be a trap.
     */
    private enum class Kind { BACKDROP, PILL, CARD }

    @SuppressLint("RtlHardcoded") // x is counted from the left edge of the screen whatever the language
    private fun params(kind: Kind) = WindowManager.LayoutParams(
        area.width,
        if (kind == Kind.PILL) WRAP_CONTENT else area.height,
        area.left,
        area.top,
        WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
            WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
            WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS or
            (if (kind == Kind.CARD) 0 else WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE),
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
        const val FADE_MS = 220L

        /** How long each GIF stays on the agent's card, and how soon one that would not open is skipped. */
        const val REEL_MS = 4_000L
        const val RETRY_REEL_MS = 300L
        const val NO_ART = "-"

        /** The gap between the card and the edge of the space it may use. */
        const val EDGE_DP = 16

        /** On a tablet or a phone on its side the card stays a hand wide instead of edge to edge. */
        const val MAX_CARD_DP = 440
        const val MIN_CARD_DP = 240

        /** The GIF may take this much of the height; under the smaller size it is left out. */
        const val MAX_ART_SHARE = 0.62f
        const val MIN_ART_DP = 96

        /** The stamp's letters, and the most of the card's width and the GIF's height it may take. */
        const val STAMP_TEXT_DP = 30
        const val STAMP_MAX_WIDTH = 0.45f
        const val STAMP_MAX_HEIGHT = 0.3f
        const val STAMP_INSET_DP = 10
        const val STAMP_HANG_DP = 6

        /** How much of the stamp sticks out above the card, and the space kept free for that. */
        const val STAMP_LIFT = 0.5f
        const val STAMP_HEADROOM_DP = 12
        const val STAMP_TILT = 7f

        /** The stamp starts big and see-through, then lands. */
        const val STAMP_FROM = 2.6f
        const val STAMP_DELAY_MS = 260L
        const val STAMP_MS = 240L
        const val STAMP_BOUNCE = 1.6f
    }
}
