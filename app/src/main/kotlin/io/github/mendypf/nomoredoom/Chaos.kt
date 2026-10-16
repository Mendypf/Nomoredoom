package io.github.mendypf.nomoredoom

import android.animation.ObjectAnimator
import android.graphics.Outline
import android.graphics.drawable.Animatable
import android.graphics.drawable.Drawable
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.view.HapticFeedbackConstants
import android.view.View
import android.view.ViewOutlineProvider
import android.view.animation.OvershootInterpolator
import android.widget.FrameLayout
import android.widget.ImageView
import io.github.mendypf.nomoredoom.core.Box
import kotlin.random.Random

/**
 * The agent's card: GIFs that slam onto the screen one after another, anywhere and at any angle,
 * piling on top of each other. The first few come fast, so a phone picked up out of habit gets a
 * jolt. After that a new one lands every second or so and the oldest goes.
 *
 * It only draws inside [root], a window [Stage] owns. [stop] takes every GIF away again.
 */
internal class Chaos(
    private val root: View,
    private val art: Art,
    /** Every GIF on the phone, in the order they come. */
    private val reel: List<String>,
    /** The space the window covers, used until the window has been laid out. */
    private val area: () -> Box,
    /** False when animations are off in Android's settings: then the GIFs land still and without a bounce. */
    private val motion: Boolean,
) {
    private val main = Handler(Looper.getMainLooper())
    private val pile: FrameLayout = root.findViewById(R.id.chaos_pile)
    private val line: View = root.findViewById(R.id.chaos_line)
    private val density = root.resources.displayMetrics.density
    private val landed = ArrayDeque<ImageView>()
    private val next = Runnable { popNext() }
    private val showLine = Runnable { slam(line) }
    private var asked = 0
    private var stopped = false

    /** How many GIFs have landed since the card came up. */
    var popped = 0
        private set

    /** The last GIF asked for: `name:playing`, `still`, `loading`, `missing` or `broken`, or `-` with none on the phone. */
    var state = "-"
        private set

    fun start() {
        if (motion) {
            line.alpha = 0f
            main.postDelayed(showLine, LINE_DELAY_MS)
        }
        if (reel.isNotEmpty()) popNext()
    }

    fun stop() {
        stopped = true
        main.removeCallbacks(next)
        main.removeCallbacks(showLine)
        line.animate().cancel()
        landed.forEach(::release)
        landed.clear()
        pile.removeAllViews()
    }

    /** Where each GIF on screen sits: its middle on the screen and its tilt. */
    fun spots(): List<String> = landed.map { image ->
        val box = area()
        val middleX = box.left + image.translationX + image.layoutParams.width / 2f
        val middleY = box.top + image.translationY + image.layoutParams.height / 2f
        "${middleX.toInt()},${middleY.toInt()},${image.rotation.toInt()}"
    }

    private fun popNext() {
        if (stopped) return
        val index = asked++
        val name = reel[index % reel.size]
        // The first ones are asked for without waiting for each other, so they land as fast as the phone opens
        // them. From the last of those on, each one asks for the next once it is down.
        val chained = index >= BURST - 1
        if (!chained) main.postDelayed(next, BURST_MS)
        val file = art.file(name)
        if (file == null) {
            state = "$name:missing"
            if (chained) main.postDelayed(next, RETRY_MS)
            return
        }
        state = "$name:loading"
        art.load(file, SIDE_PX) { picture ->
            if (stopped) return@load
            if (picture == null) {
                state = "$name:broken"
                if (chained) main.postDelayed(next, RETRY_MS)
                return@load
            }
            val moving = picture is Animatable && motion
            land(picture, moving)
            state = "$name:" + if (moving) "playing" else "still"
            popped++
            if (chained) main.postDelayed(next, STEADY_MS)
        }
    }

    private fun land(picture: Drawable, moving: Boolean) {
        val wide = pile.width.takeIf { it > 0 } ?: area().width
        val high = pile.height.takeIf { it > 0 } ?: area().height
        var width = (wide * between(SMALLEST, BIGGEST)).coerceAtMost(MAX_GIF_DP * density)
        var height = width * picture.intrinsicHeight / picture.intrinsicWidth.coerceAtLeast(1)
        if (height > high * TALLEST) {
            width *= high * TALLEST / height
            height = high * TALLEST
        }
        val image = ImageView(root.context).apply {
            setImageDrawable(picture)
            scaleType = ImageView.ScaleType.FIT_XY // the view already has the GIF's shape
            importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
            layoutParams = FrameLayout.LayoutParams(width.toInt(), height.toInt())
            // Each one may hang a little off the edge, never so far that its caption is lost.
            translationX = between(-HANG * width, (wide - (1 - HANG) * width).coerceAtLeast(-HANG * width + 1))
            translationY = between(-HANG * height, (high - (1 - HANG) * height).coerceAtLeast(-HANG * height + 1))
            rotation = between(-MAX_TILT, MAX_TILT)
            elevation = SHADOW_DP * density
            outlineProvider = object : ViewOutlineProvider() {
                override fun getOutline(view: View, outline: Outline) =
                    outline.setRoundRect(0, 0, view.width, view.height, CORNER_DP * density)
            }
            clipToOutline = true
        }
        pile.addView(image)
        landed.addLast(image)
        if (moving) {
            (picture as Animatable).start()
            image.scaleX = POP_FROM
            image.scaleY = POP_FROM
            image.alpha = 0f
            image.animate().scaleX(1f).scaleY(1f).alpha(1f).setDuration(POP_MS)
                .setInterpolator(OvershootInterpolator(POP_BOUNCE)).start()
            if (popped < BURST) shake()
        }
        if (popped < BURST) buzz(image)
        while (landed.size > MAX_ON_SCREEN) {
            val oldest = landed.removeFirst()
            if (moving) {
                oldest.animate().alpha(0f).scaleX(0.8f).scaleY(0.8f).setDuration(GONE_MS)
                    .withEndAction { release(oldest); pile.removeView(oldest) }.start()
            } else {
                release(oldest)
                pile.removeView(oldest)
            }
        }
    }

    /** The pile jumps with each of the first GIFs, as if they hit it. */
    private fun shake() {
        val kick = SHAKE_DP * density * if (Random.nextBoolean()) 1 else -1
        ObjectAnimator.ofFloat(pile, View.TRANSLATION_X, 0f, kick, -kick * 0.7f, kick * 0.4f, 0f)
            .setDuration(SHAKE_MS).start()
    }

    private fun buzz(view: View) {
        val kind = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) HapticFeedbackConstants.REJECT
        else HapticFeedbackConstants.LONG_PRESS
        view.performHapticFeedback(kind)
    }

    /** The line starts big and see-through, then lands on top of the pile. */
    private fun slam(view: View) {
        view.scaleX = LINE_FROM
        view.scaleY = LINE_FROM
        view.animate().scaleX(1f).scaleY(1f).alpha(1f).setDuration(POP_MS)
            .setInterpolator(OvershootInterpolator(POP_BOUNCE))
            .withEndAction { buzz(view) }
            .start()
    }

    private fun between(low: Float, high: Float): Float = low + Random.nextFloat() * (high - low)

    private fun release(image: ImageView) {
        image.animate().cancel()
        (image.drawable as? Animatable)?.stop()
        image.setImageDrawable(null)
    }

    private companion object {
        /** The first GIFs come this fast, then one lands every [STEADY_MS] and the oldest of [MAX_ON_SCREEN] goes. */
        const val BURST = 7
        const val BURST_MS = 110L
        const val STEADY_MS = 900L
        const val MAX_ON_SCREEN = 9
        const val RETRY_MS = 300L

        /** The line lands once the first GIFs are down. */
        const val LINE_DELAY_MS = 1_100L
        const val LINE_FROM = 2.6f

        /** A GIF's width, as a share of the screen's, and the most of its height one may take. */
        const val SMALLEST = 0.48f
        const val BIGGEST = 0.82f
        const val TALLEST = 0.55f
        const val MAX_GIF_DP = 380

        /** How much of a GIF may hang off the edge of the screen. */
        const val HANG = 0.12f
        const val MAX_TILT = 14f
        const val SHADOW_DP = 10
        const val CORNER_DP = 14

        /** The GIFs are opened at most this big; none is drawn bigger than the screen is wide. */
        const val SIDE_PX = 720

        const val POP_FROM = 0.25f
        const val POP_MS = 280L
        const val POP_BOUNCE = 2.2f
        const val GONE_MS = 180L
        const val SHAKE_DP = 9
        const val SHAKE_MS = 220L
    }
}
