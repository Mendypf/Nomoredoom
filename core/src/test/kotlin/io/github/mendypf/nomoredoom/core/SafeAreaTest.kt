package io.github.mendypf.nomoredoom.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

/** Numbers are a 1080 x 2400 phone, the sizes Android reported on the test emulator. */
class SafeAreaTest {
    private val display = Box(0, 0, 1080, 2400)
    private val statusBar = Box(0, 0, 1080, 136)
    private val buttonBar = Box(0, 2274, 1080, 2400)

    @Test
    fun `with no system windows the whole screen is free`() {
        assertEquals(display, SafeArea.within(display, emptyList()))
    }

    @Test
    fun `stays below the status bar and above the button bar`() {
        assertEquals(Box(0, 136, 1080, 2274), SafeArea.within(display, listOf(statusBar, buttonBar)))
    }

    @Test
    fun `stays clear of a button bar on the side when the phone is turned`() {
        val turned = Box(0, 0, 2400, 1080)
        assertEquals(Box(0, 0, 2274, 1080), SafeArea.within(turned, listOf(Box(2274, 0, 2400, 1080))))
        assertEquals(Box(126, 0, 2400, 1080), SafeArea.within(turned, listOf(Box(0, 0, 126, 1080))))
    }

    @Test
    fun `makes room for a call banner that drops down from the top`() {
        // Android draws the banner with a gap at each side, so it is not quite the full width
        val banner = Box(24, 0, 1056, 520)
        assertEquals(Box(0, 520, 1080, 2274), SafeArea.within(display, listOf(statusBar, banner, buttonBar)))
    }

    @Test
    fun `makes room for a sheet that comes up from the bottom`() {
        val sheet = Box(0, 1700, 1080, 2400)
        assertEquals(Box(0, 136, 1080, 1700), SafeArea.within(display, listOf(statusBar, sheet)))
    }

    @Test
    fun `ignores a small system window that is not a bar`() {
        val volumePanel = Box(900, 800, 1080, 1500)
        assertEquals(Box(0, 136, 1080, 2274), SafeArea.within(display, listOf(statusBar, volumePanel, buttonBar)))
    }

    @Test
    fun `ignores a window that is off the screen or has no size`() {
        val offScreen = Box(0, 2400, 1080, 2600)
        val empty = Box(0, 0, 0, 0)
        assertEquals(Box(0, 136, 1080, 2400), SafeArea.within(display, listOf(statusBar, offScreen, empty)))
    }

    @Test
    fun `there is no room when a system window is over half the screen`() {
        // the lock screen and the notification shade fill it; a tall sheet covers more than half
        assertNull(SafeArea.within(display, listOf(statusBar, display)))
        assertNull(SafeArea.within(display, listOf(Box(0, 1100, 1080, 2400))))
    }

    @Test
    fun `there is no room when the bars and banners leave less than half the screen`() {
        val banner = Box(24, 0, 1056, 700)
        val sheet = Box(0, 1800, 1080, 2400)
        assertNull(SafeArea.within(display, listOf(banner, sheet)))
    }
}
