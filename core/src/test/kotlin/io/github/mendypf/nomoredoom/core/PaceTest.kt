package io.github.mendypf.nomoredoom.core

import kotlin.test.Test
import kotlin.test.assertEquals

class PaceTest {
    @Test
    fun `the first looks after a change stay quick`() {
        assertEquals(700, Pace.gapMs(misses = 0))
        assertEquals(700, Pace.gapMs(misses = 2))
    }

    @Test
    fun `looks that keep finding nothing come less often`() {
        assertEquals(2_000, Pace.gapMs(misses = 3))
        assertEquals(2_000, Pace.gapMs(misses = 9))
        assertEquals(5_000, Pace.gapMs(misses = 10))
    }

    @Test
    fun `it never waits longer than five seconds`() {
        assertEquals(5_000, Pace.gapMs(misses = Int.MAX_VALUE))
    }
}
