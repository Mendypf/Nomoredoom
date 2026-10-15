package io.github.mendypf.nomoredoom.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class StateCodecTest {
    private val clock = Clock(wallMs = 1_791_763_200_000L, upMs = 123_456L, boot = 9)

    private val saved = State(
        watchedMs = 754_000,
        lastWatch = clock,
        pillDone = true,
        tapDone = true,
        nextHoldAtMs = 780_000,
        lastHomeAtMs = 700_000,
        log = DayLog().add(20_738, "Reels", 600_000).add(20_738, "Shorts", 154_000).add(20_737, "TikTok", 9_000),
    )

    @Test
    fun `what is saved comes back the same`() {
        assertEquals(saved, StateCodec.decode(StateCodec.encode(saved)))
    }

    @Test
    fun `an empty state comes back the same`() {
        assertEquals(State(), StateCodec.decode(StateCodec.encode(State())))
    }

    @Test
    fun `what is on screen right now is not saved`() {
        val live = saved.copy(
            card = Card.Hold(id = 4, minutes = 12, over = 2, holdMs = 3_000, quiet = false),
            wasWatching = true,
            lastTick = clock,
            pillSinceUp = 5L,
        )
        assertEquals(saved, StateCodec.decode(StateCodec.encode(live)))
    }

    @Test
    fun `garbage reads as nothing saved`() {
        assertNull(StateCodec.decode(""))
        assertNull(StateCodec.decode("hello"))
        assertNull(StateCodec.decode("v1|watched=abc"))
        assertNull(StateCodec.decode("v999|watched=1"))
    }

    @Test
    fun `a negative or absurd number reads as nothing saved`() {
        assertNull(StateCodec.decode(StateCodec.encode(saved).replace("watched=754000", "watched=-5")))
    }
}
