package io.github.mendypf.nomoredoom

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.os.SystemClock
import android.util.AttributeSet
import android.view.View
import android.widget.Button
import kotlin.math.ceil
import kotlin.math.min

/**
 * The popup's only control: an X in a circle. On the later popups it starts as
 * a countdown and turns into the X when the wait is over.
 *
 * The wait is measured with the clock, not with an animation, because a phone
 * with animations switched off would otherwise finish every wait instantly.
 */
internal class CloseButton @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : View(context, attrs) {

    var onClose: (() -> Unit)? = null

    /** True once the X can be pressed. */
    val ready: Boolean get() = startedAt != 0L && remainingMs() == 0L

    private val density = resources.displayMetrics.density
    private val disc = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = context.getColor(R.color.close_disc) }
    private val track = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = context.getColor(R.color.close_track)
        style = Paint.Style.STROKE
        strokeWidth = RING_DP * density
    }
    private val ring = Paint(track).apply {
        color = context.getColor(R.color.close_ring)
        strokeCap = Paint.Cap.ROUND
    }
    private val cross = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = context.getColor(R.color.close_glyph)
        style = Paint.Style.STROKE
        strokeWidth = CROSS_DP * density
        strokeCap = Paint.Cap.ROUND
    }
    private val number = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = context.getColor(R.color.close_glyph)
        textAlign = Paint.Align.CENTER
        isFakeBoldText = true
    }

    private var waitMs = 0L
    private var startedAt = 0L
    private var closed = false

    private val frame = object : Runnable {
        override fun run() {
            if (startedAt == 0L || closed) return
            invalidate()
            if (remainingMs() > 0) postOnAnimation(this) else describe()
        }
    }

    init {
        isClickable = true
        isFocusable = true
        isEnabled = false
        // A screen reader says "Close" by itself the moment the wait is over.
        accessibilityLiveRegion = ACCESSIBILITY_LIVE_REGION_POLITE
    }

    /** Starts the wait. Zero shows the X at once. */
    fun start(waitMs: Long) {
        this.waitMs = waitMs.coerceAtLeast(0)
        startedAt = SystemClock.uptimeMillis()
        closed = false
        removeCallbacks(frame)
        describe()
        invalidate()
        if (this.waitMs > 0) postOnAnimation(frame)
    }

    override fun performClick(): Boolean {
        if (!ready || closed) return false
        super.performClick()
        closed = true
        onClose?.invoke()
        return true
    }

    override fun getAccessibilityClassName(): CharSequence = Button::class.java.name

    override fun drawableStateChanged() {
        super.drawableStateChanged()
        invalidate()
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        if (startedAt != 0L && !closed && remainingMs() > 0) postOnAnimation(frame)
    }

    override fun onDetachedFromWindow() {
        removeCallbacks(frame)
        super.onDetachedFromWindow()
    }

    override fun onDraw(canvas: Canvas) {
        val cx = width / 2f
        val cy = height / 2f
        val radius = min(width, height) / 2f - INSET_DP * density
        val left = remainingMs()

        disc.alpha = if (isPressed && left == 0L) PRESSED_ALPHA else DISC_ALPHA
        canvas.drawCircle(cx, cy, radius, disc)

        if (left > 0) {
            val edge = radius - track.strokeWidth / 2
            canvas.drawCircle(cx, cy, edge, track)
            canvas.drawArc(cx - edge, cy - edge, cx + edge, cy + edge, -90f, 360f * left / waitMs, false, ring)
            number.textSize = radius * NUMBER_SHARE
            val baseline = cy - (number.descent() + number.ascent()) / 2
            canvas.drawText(ceil(left / 1000.0).toInt().toString(), cx, baseline, number)
        } else {
            val arm = radius * CROSS_SHARE
            canvas.drawLine(cx - arm, cy - arm, cx + arm, cy + arm, cross)
            canvas.drawLine(cx - arm, cy + arm, cx + arm, cy - arm, cross)
        }
    }

    private fun remainingMs(): Long =
        if (startedAt == 0L) waitMs else (waitMs - (SystemClock.uptimeMillis() - startedAt)).coerceAtLeast(0)

    /** What a screen reader says: how long until it works, then just "Close". */
    private fun describe() {
        val seconds = ceil(remainingMs() / 1000.0).toInt()
        isEnabled = seconds == 0
        contentDescription = if (seconds == 0) {
            context.getString(R.string.card_close)
        } else {
            resources.getQuantityString(R.plurals.card_close_wait, seconds, seconds)
        }
    }

    private companion object {
        const val INSET_DP = 4
        const val RING_DP = 3f
        const val CROSS_DP = 2.5f
        const val NUMBER_SHARE = 0.95f
        const val CROSS_SHARE = 0.34f
        const val DISC_ALPHA = 0xE6
        const val PRESSED_ALPHA = 0xFF
    }
}
