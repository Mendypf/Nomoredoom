package io.github.mendypf.nomoredoom.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class FitTest {
    @Test
    fun `a wide picture fills the width`() {
        assertEquals(Size(900, 506), Fit.inside(width = 480, height = 270, maxWidth = 900, maxHeight = 800))
    }

    @Test
    fun `a tall picture stops at the height it is allowed`() {
        assertEquals(Size(450, 800), Fit.inside(width = 270, height = 480, maxWidth = 900, maxHeight = 800))
    }

    @Test
    fun `a small picture is blown up to fill the space`() {
        assertEquals(Size(912, 657), Fit.inside(width = 190, height = 137, maxWidth = 912, maxHeight = 1_000))
    }

    @Test
    fun `a picture the shape of the space fills it exactly`() {
        assertEquals(Size(900, 900), Fit.inside(width = 400, height = 400, maxWidth = 900, maxHeight = 900))
    }

    @Test
    fun `a sliver of a picture still gets one pixel`() {
        assertEquals(Size(900, 1), Fit.inside(width = 10_000, height = 1, maxWidth = 900, maxHeight = 800))
        assertEquals(Size(1, 800), Fit.inside(width = 1, height = 10_000, maxWidth = 900, maxHeight = 800))
    }

    @Test
    fun `no picture or no space fits nothing`() {
        assertNull(Fit.inside(width = 0, height = 270, maxWidth = 900, maxHeight = 800))
        assertNull(Fit.inside(width = 480, height = -1, maxWidth = 900, maxHeight = 800))
        assertNull(Fit.inside(width = 480, height = 270, maxWidth = 0, maxHeight = 800))
        assertNull(Fit.inside(width = 480, height = 270, maxWidth = 900, maxHeight = -5))
    }
}
