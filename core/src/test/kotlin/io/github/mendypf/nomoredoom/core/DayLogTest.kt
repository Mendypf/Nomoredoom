package io.github.mendypf.nomoredoom.core

import kotlin.test.Test
import kotlin.test.assertEquals

class DayLogTest {
    private val hour = 3_600_000L

    // 2026-10-12 00:00 UTC
    private val midnight = 1_791_763_200_000L

    @Test
    fun `adds time under a name for a day`() {
        val log = DayLog().add(100, "Reels", 5_000).add(100, "Reels", 2_000).add(100, "TikTok", 1_000)
        assertEquals(mapOf("Reels" to 7_000L, "TikTok" to 1_000L), log.byName(100))
        assertEquals(8_000, log.total(100))
        assertEquals(0, log.total(99))
    }

    @Test
    fun `keeps the last fourteen days`() {
        var log = DayLog()
        for (day in 1..20) log = log.add(day, "Reels", 1_000)
        assertEquals((7..20).toList(), log.days.keys.sorted())
    }

    @Test
    fun `adding nothing changes nothing`() {
        val log = DayLog().add(5, "Reels", 0)
        assertEquals(DayLog(), log)
    }

    @Test
    fun `the day turns over at four in the morning local time`() {
        val lateNight = Clock(wallMs = midnight + 3 * hour, upMs = 0, boot = 1)
        val earlyMorning = Clock(wallMs = midnight + 5 * hour, upMs = 0, boot = 1)
        assertEquals(lateNight.day() + 1, earlyMorning.day())
        assertEquals(Clock(midnight - hour, 0, 1).day(), lateNight.day())
    }

    @Test
    fun `the day follows the phone's time zone`() {
        // 03:00 UTC is 23:00 the evening before in New York (UTC-4): same day as that evening
        val newYork = Clock(wallMs = midnight + 3 * hour, upMs = 0, boot = 1, utcOffsetMs = (-4 * hour).toInt())
        val evening = Clock(wallMs = midnight - 6 * hour, upMs = 0, boot = 1, utcOffsetMs = (-4 * hour).toInt())
        assertEquals(evening.day(), newYork.day())
    }
}
