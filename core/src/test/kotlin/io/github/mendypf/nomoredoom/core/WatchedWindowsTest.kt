package io.github.mendypf.nomoredoom.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class WatchedWindowsTest {
    @Test
    fun `a window Android reported a change in is remembered`() {
        val windows = WatchedWindows()
        assertTrue(windows.saw(41))
        assertEquals(setOf(41), windows.ids())
    }

    @Test
    fun `a window seen again is not new`() {
        val windows = WatchedWindows()
        windows.saw(41)
        assertFalse(windows.saw(41))
        assertEquals(setOf(41), windows.ids())
    }

    @Test
    fun `a change with no window is not remembered`() {
        val windows = WatchedWindows()
        assertFalse(windows.saw(-1))
        assertEquals(emptySet(), windows.ids())
    }

    @Test
    fun `only the latest few are kept and one seen again counts as latest`() {
        val windows = WatchedWindows(keep = 3)
        listOf(1, 2, 3, 1, 4).forEach(windows::saw)
        assertEquals(setOf(3, 1, 4), windows.ids())
    }

    @Test
    fun `the list it hands out does not change afterwards`() {
        val windows = WatchedWindows()
        windows.saw(1)
        val before = windows.ids()
        windows.saw(2)
        assertEquals(setOf(1), before)
    }
}
