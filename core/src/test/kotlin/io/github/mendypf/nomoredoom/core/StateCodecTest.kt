package io.github.mendypf.nomoredoom.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class StateCodecTest {
    private val clock = Clock(wallMs = 1_791_763_200_000L, upMs = 123_456L, boot = 9)

    private val saved = State(
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
        val live = saved.copy(wasWatching = true, lastTick = clock, enteredUp = 5L, covered = true)
        assertEquals(saved, StateCodec.decode(StateCodec.encode(live)))
    }

    @Test
    fun `a line saved by the version with the timer cards still gives its watched time`() {
        val old = "v1|watched=754000|last=1791763200000,123456,9,0|pill=1|tap=1|hold=780000|home=700000|" +
            "log=20738:Reels=600000,Shorts=154000;20737:TikTok=9000"
        assertEquals(saved, StateCodec.decode(old))
    }

    @Test
    fun `garbage reads as nothing saved`() {
        assertNull(StateCodec.decode(""))
        assertNull(StateCodec.decode("hello"))
        assertNull(StateCodec.decode("v2|log=abc"))
        assertNull(StateCodec.decode("v2|watched=1"))
        assertNull(StateCodec.decode("v999|log=20738:Reels=1"))
    }

    @Test
    fun `a negative or absurd number reads as nothing saved`() {
        assertNull(StateCodec.decode("v2|log=20738:Reels=-5"))
        assertNull(StateCodec.decode("v2|log=20738:Reels=99999999999999"))
    }
}
