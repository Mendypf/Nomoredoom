package io.github.mendypf.nomoredoom.core

/**
 * Where a popup may be drawn.
 *
 * Android stacks an accessibility service's windows above its own status bar,
 * button bar, incoming-call banner, notification shade and lock screen, and
 * gives them no hint where those are. A popup that simply filled the screen
 * would sit on top of the Home button and hide a ringing call. So the popups
 * only ever use the part of the screen no system window is using, and when the
 * system is using most of it they are not drawn at all.
 */
public object SafeArea {
    /**
     * @param systemWindows the outlines of Android's own windows, in screen pixels
     * @return the free part of [display], or null when a popup does not belong on screen right now
     */
    public fun within(display: Box, systemWindows: List<Box>): Box? {
        val half = display.area / 2
        var left = display.left
        var top = display.top
        var right = display.right
        var bottom = display.bottom
        for (window in systemWindows) {
            val w = Box(
                maxOf(window.left, display.left),
                maxOf(window.top, display.top),
                minOf(window.right, display.right),
                minOf(window.bottom, display.bottom),
            )
            if (w.width <= 0 || w.height <= 0) continue
            // The lock screen, the notification shade pulled down, the power menu.
            if (w.area >= half) return null
            val middleX = (w.left + w.right) / 2
            val middleY = (w.top + w.bottom) / 2
            when {
                // A bar, a banner or a sheet: it owns its edge of the screen.
                w.width * 2 >= display.width ->
                    if (middleY * 2 < display.top + display.bottom) top = maxOf(top, w.bottom) else bottom = minOf(bottom, w.top)
                w.height * 2 >= display.height ->
                    if (middleX * 2 < display.left + display.right) left = maxOf(left, w.right) else right = minOf(right, w.left)
                // Anything smaller (the volume slider, a chat bubble) is left alone.
            }
        }
        val free = Box(left, top, right, bottom)
        return free.takeIf { it.width > 0 && it.height > 0 && it.area >= half }
    }

    private val Box.area: Long get() = width.toLong() * height
}
