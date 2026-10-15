package io.github.mendypf.nomoredoom.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue
import kotlin.test.fail

private const val SEC = 1_000L
private const val MIN = 60 * SEC

/** Drives the engine the way the phone does: one tick a second. */
private class Sim(var settings: Settings = Settings(popups = true)) {
    var state = State()
    var ui = Ui.NONE
    var homes = 0
    var up = 5_000_000L
    var wall = 1_790_000_000_000L
    var boot = 7

    fun clock() = Clock(wallMs = wall, upMs = up, boot = boot)

    fun pass(ms: Long) {
        up += ms
        wall += ms
    }

    /** What the agent says on the next ticks. */
    var busy = false

    fun tick(screen: String? = "Reels", after: Long = SEC) {
        pass(after)
        apply(Event.Tick(clock(), screen, busy))
    }

    fun watch(seconds: Int, screen: String = "Reels") = repeat(seconds) { tick(screen) }

    /** Keep watching until this much short-video time is on the clock. */
    fun watchUntil(watchedMs: Long, screen: String = "Reels") {
        var guard = 0
        while (state.watchedMs < watchedMs) {
            tick(screen)
            if (++guard > 5_000) fail("never reached ${watchedMs / SEC}s watched, stuck at ${state.watchedMs / SEC}s with $ui")
        }
    }

    fun leave(forMs: Long) {
        tick(screen = null)
        pass(forMs - SEC)
    }

    fun close() = apply(Event.Closed(clock()))

    fun reboot() {
        boot += 1
        up = 40_000L
    }

    private fun apply(event: Event) {
        val out = Engine.step(state, settings, event)
        state = out.state
        ui = out.ui
        if (out.goHome) homes++
    }
}

class EngineTest {

    @Test
    fun `counts one second for each second of watching`() {
        val sim = Sim()
        sim.watch(61)
        assertEquals(60 * SEC, sim.state.watchedMs)
    }

    @Test
    fun `time away is not counted`() {
        val sim = Sim()
        sim.watch(31)
        sim.leave(5 * MIN)
        sim.watch(31)
        assertEquals(60 * SEC, sim.state.watchedMs)
    }

    @Test
    fun `a late tick counts three seconds at most`() {
        val sim = Sim()
        sim.watch(2)
        sim.tick(after = 45 * SEC)
        assertEquals(4 * SEC, sim.state.watchedMs)
    }

    @Test
    fun `nothing shows before the limit`() {
        val sim = Sim()
        sim.watchUntil(10 * MIN - SEC)
        assertEquals(Ui.NONE, sim.ui)
    }

    @Test
    fun `the pill shows at the limit then goes away by itself`() {
        val sim = Sim()
        sim.watchUntil(10 * MIN)
        assertEquals(10, sim.ui.pillMinutes)
        assertNull(sim.ui.card)
        sim.watch(6)
        assertNull(sim.ui.pillMinutes)
    }

    @Test
    fun `the pill shows once per sitting`() {
        val sim = Sim()
        sim.watchUntil(10 * MIN)
        sim.leave(2 * MIN)
        sim.watch(10)
        assertNull(sim.ui.pillMinutes)
    }

    @Test
    fun `a minute over a tap card appears`() {
        val sim = Sim()
        sim.watchUntil(11 * MIN)
        val card = assertIs<Card.Tap>(sim.ui.card)
        assertEquals(11, card.minutes)
    }

    @Test
    fun `time stops while a card covers the video`() {
        val sim = Sim()
        sim.watchUntil(11 * MIN)
        val before = sim.state.watchedMs
        sim.watch(30)
        assertEquals(before, sim.state.watchedMs)
        assertIs<Card.Tap>(sim.ui.card)
    }

    @Test
    fun `closing the card lets the time run again`() {
        val sim = Sim()
        sim.watchUntil(11 * MIN)
        sim.close()
        assertNull(sim.ui.card)
        sim.watch(5)
        assertEquals(11 * MIN + 5 * SEC, sim.state.watchedMs)
    }

    @Test
    fun `two minutes over the card has to be held for three seconds`() {
        val sim = Sim()
        sim.watchUntil(11 * MIN)
        sim.close()
        sim.watchUntil(12 * MIN)
        val card = assertIs<Card.Hold>(sim.ui.card)
        assertEquals(3 * SEC, card.holdMs)
        assertEquals(12, card.minutes)
    }

    @Test
    fun `the hold card comes back after a minute of watching`() {
        val sim = Sim()
        sim.skipTo(12 * MIN)
        sim.watch(4)
        assertIs<Card.Hold>(sim.ui.card)
        sim.close()
        val closedAt = sim.state.watchedMs
        sim.watchUntil(closedAt + 59 * SEC)
        assertNull(sim.ui.card)
        sim.watch(1)
        assertIs<Card.Hold>(sim.ui.card)
    }

    @Test
    fun `holds get longer and closer together and the screen gets dimmer`() {
        data class Row(val overMin: Int, val holdSec: Int, val everySec: Int, val quiet: Boolean)
        val rows = listOf(
            Row(2, 3, 60, false),
            Row(4, 5, 45, false),
            Row(6, 8, 30, true),
            Row(8, 12, 20, true),
        )
        var lastDim = 0f
        for (row in rows) {
            val sim = Sim()
            val start = (10 + row.overMin) * MIN
            sim.skipTo(start)
            sim.watch(4)
            val card = assertIs<Card.Hold>(sim.ui.card, "at ${row.overMin} minutes over")
            assertEquals(row.holdSec * SEC, card.holdMs, "hold at ${row.overMin} minutes over")
            assertEquals(row.quiet, sim.ui.quiet, "quiet at ${row.overMin} minutes over")
            assertTrue(sim.ui.dim > lastDim, "dim should grow at ${row.overMin} minutes over")
            lastDim = sim.ui.dim

            sim.close()
            val dismissedAt = sim.state.watchedMs
            sim.watchUntil(dismissedAt + row.everySec * SEC - SEC)
            assertNull(sim.ui.card, "too early at ${row.overMin} minutes over")
            sim.watch(1)
            assertIs<Card.Hold>(sim.ui.card, "due at ${row.overMin} minutes over")
        }
    }

    @Test
    fun `ten minutes over it says goodbye then goes home`() {
        val sim = Sim()
        sim.skipTo(20 * MIN)
        sim.watch(3)
        val card = assertIs<Card.Farewell>(sim.ui.card)
        assertEquals(20, card.minutes)
        assertEquals(0, sim.homes)
        sim.watch(4)
        assertEquals(1, sim.homes)
        assertNull(sim.ui.card)
    }

    @Test
    fun `coming back in the same sitting gets twenty seconds then home again`() {
        val sim = Sim()
        sim.skipTo(20 * MIN)
        sim.watch(6)
        assertEquals(1, sim.homes)
        sim.leave(3 * MIN)
        sim.watch(20) // the first tick of a visit counts nothing, so this is 19 seconds
        assertNull(sim.ui.card)
        sim.watch(1)
        assertIs<Card.Farewell>(sim.ui.card)
        sim.watch(3)
        assertEquals(2, sim.homes)
    }

    @Test
    fun `with send-home off the hold cards keep coming instead`() {
        val sim = Sim(Settings(popups = true, sendHome = false))
        sim.skipTo(25 * MIN)
        sim.watch(4)
        val card = assertIs<Card.Hold>(sim.ui.card)
        assertEquals(12 * SEC, card.holdMs)
        sim.watch(10)
        assertEquals(0, sim.homes)
    }

    @Test
    fun `an hour away starts a fresh sitting`() {
        val sim = Sim()
        sim.watchUntil(11 * MIN)
        sim.close()
        sim.watchUntil(11 * MIN + 30 * SEC)
        sim.leave(60 * MIN)
        sim.watch(5)
        assertEquals(4 * SEC, sim.state.watchedMs)
        assertEquals(Ui.NONE, sim.ui)
        sim.watchUntil(10 * MIN)
        assertEquals(10, sim.ui.pillMinutes)
    }

    @Test
    fun `fifty nine minutes away is still the same sitting`() {
        val sim = Sim()
        sim.watchUntil(5 * MIN)
        sim.leave(59 * MIN)
        sim.watch(5)
        assertEquals(5 * MIN + 4 * SEC, sim.state.watchedMs)
    }

    @Test
    fun `after a restart of the phone time away comes from the wall clock`() {
        val sim = Sim()
        sim.watchUntil(5 * MIN)
        sim.leave(10 * MIN)
        sim.reboot()
        sim.watch(3)
        assertEquals(5 * MIN + 2 * SEC, sim.state.watchedMs, "ten minutes away, same sitting")

        sim.leave(61 * MIN)
        sim.reboot()
        sim.watch(3)
        assertEquals(2 * SEC, sim.state.watchedMs, "an hour away across a restart, fresh sitting")
    }

    @Test
    fun `no card in the first two seconds of a visit`() {
        val sim = Sim()
        sim.skipTo(12 * MIN)
        sim.tick()
        assertNull(sim.ui.card)
        sim.tick()
        assertNull(sim.ui.card)
        sim.tick()
        assertNotNull(sim.ui.card)
    }

    @Test
    fun `a card left unanswered is back when the user returns`() {
        val sim = Sim()
        sim.watchUntil(11 * MIN)
        assertIs<Card.Tap>(sim.ui.card)
        sim.leave(5 * MIN)
        assertEquals(Ui.NONE, sim.ui)
        sim.watch(3)
        assertIs<Card.Tap>(sim.ui.card)
    }

    @Test
    fun `each new card gets a new id and the same card keeps its id`() {
        val sim = Sim()
        sim.watchUntil(11 * MIN)
        val first = sim.ui.card!!.id
        sim.watch(3)
        assertEquals(first, sim.ui.card!!.id)
        sim.close()
        sim.watchUntil(12 * MIN)
        assertTrue(sim.ui.card!!.id != first)
    }

    @Test
    fun `each card says which step of the ladder it is on`() {
        val sim = Sim()
        val firstSeenAt = LinkedHashMap<Int, Int>() // step -> minutes watched when its first card came up
        var guard = 0
        while (sim.homes == 0) {
            sim.tick()
            val card = sim.ui.card
            if (card != null) {
                firstSeenAt.putIfAbsent(card.over, card.minutes)
                if (card !is Card.Farewell) sim.close()
            }
            if (++guard > 5_000) fail("never went home")
        }
        assertEquals(mapOf(1 to 11, 2 to 12, 4 to 14, 6 to 16, 8 to 18, 10 to 20), firstSeenAt)
    }

    @Test
    fun `speed makes test time run faster`() {
        val sim = Sim(Settings(popups = true, speed = 60))
        sim.watch(11)
        assertEquals(10 * MIN, sim.state.watchedMs)
        assertEquals(10, sim.ui.pillMinutes)
    }

    @Test
    fun `a shorter limit moves the whole ladder`() {
        val sim = Sim(Settings(popups = true, limitMs = 2 * MIN))
        sim.watchUntil(2 * MIN)
        assertEquals(2, sim.ui.pillMinutes)
        sim.watchUntil(3 * MIN)
        assertIs<Card.Tap>(sim.ui.card)
    }

    @Test
    fun `watched time is logged under the name of the screen`() {
        val sim = Sim()
        sim.watch(31, "Reels")
        sim.watch(20, "Shorts")
        val today = sim.clock().day()
        assertEquals(mapOf("Reels" to 30 * SEC, "Shorts" to 20 * SEC), sim.state.log.byName(today))
        assertEquals(50 * SEC, sim.state.log.total(today))
    }

    @Test
    fun `sitting time reads zero once an hour has gone by`() {
        val sim = Sim()
        sim.watchUntil(5 * MIN)
        sim.leave(30 * MIN)
        assertEquals(5 * MIN, sim.state.sittingMs(sim.clock(), sim.settings))
        sim.pass(31 * MIN)
        assertEquals(0, sim.state.sittingMs(sim.clock(), sim.settings))
    }

    /** Jump straight to a point in the sitting, the way the test command on the phone does. */
    private fun Sim.skipTo(watchedMs: Long) {
        state = state.skippedTo(watchedMs)
    }

    // ---- while the person's agent works

    @Test
    fun `while the agent works the video is covered at once, long before the limit`() {
        val sim = Sim()
        sim.busy = true
        sim.watch(1)
        assertNull(sim.ui.card, "a screen that just came up is left to settle first")
        sim.watch(2)
        assertIs<Card.Busy>(sim.ui.card)
        assertTrue(sim.ui.quiet, "the video's sound is paused under it")
        assertEquals(0f, sim.ui.dim)
    }

    @Test
    fun `time under the agent's card is not counted`() {
        val sim = Sim()
        sim.busy = true
        sim.watch(3)
        val held = sim.state.watchedMs
        sim.watch(120)
        assertEquals(held, sim.state.watchedMs)
    }

    @Test
    fun `the agent's card has no X that works`() {
        val sim = Sim()
        sim.busy = true
        sim.watch(3)
        sim.close()
        assertIs<Card.Busy>(sim.ui.card)
        sim.watch(1)
        assertIs<Card.Busy>(sim.ui.card)
    }

    @Test
    fun `when the agent is done the card goes and the count carries on`() {
        val sim = Sim()
        sim.watch(31)
        sim.busy = true
        sim.watch(10)
        assertIs<Card.Busy>(sim.ui.card)
        sim.busy = false
        sim.watch(1)
        assertNull(sim.ui.card)
        assertTrue(sim.ui == Ui.NONE)
        sim.watch(10)
        assertEquals(41 * SEC, sim.state.watchedMs)
    }

    @Test
    fun `the agent starting mid-video covers it on the next tick`() {
        val sim = Sim()
        sim.watch(20)
        sim.busy = true
        sim.watch(1)
        assertIs<Card.Busy>(sim.ui.card)
    }

    @Test
    fun `the agent's card takes the place of a countdown card, which comes back once the agent is done`() {
        val sim = Sim()
        sim.skipTo(12 * MIN)
        sim.watch(3)
        assertIs<Card.Hold>(sim.ui.card)
        sim.busy = true
        sim.watch(1)
        assertIs<Card.Busy>(sim.ui.card)
        sim.busy = false
        sim.watch(1)
        assertIs<Card.Hold>(sim.ui.card, "the unanswered card was still owed")
    }

    @Test
    fun `nobody is sent home while the agent's card is up`() {
        val sim = Sim()
        sim.skipTo(20 * MIN)
        sim.busy = true
        sim.watch(30)
        assertEquals(0, sim.homes)
        assertIs<Card.Busy>(sim.ui.card)
    }

    // ---- with the popups off, as the app comes

    @Test
    fun `the popups are off unless switched on`() {
        assertEquals(false, Settings().popups)
    }

    @Test
    fun `with the popups off nothing comes up at any minute and nobody is sent home`() {
        val sim = Sim(Settings())
        sim.watchUntil(25 * MIN)
        sim.watch(60)
        assertEquals(Ui.NONE, sim.ui)
        assertEquals(0, sim.homes)
    }

    @Test
    fun `with the popups off the agent's card still covers the video, and goes when it is done`() {
        val sim = Sim(Settings())
        sim.busy = true
        sim.watch(3)
        assertIs<Card.Busy>(sim.ui.card)
        sim.busy = false
        sim.watch(1)
        assertEquals(Ui.NONE, sim.ui)
    }

    @Test
    fun `switching the popups off takes a card that is up away`() {
        val sim = Sim()
        sim.skipTo(12 * MIN)
        sim.watch(3)
        assertIs<Card.Hold>(sim.ui.card)
        sim.settings = Settings()
        sim.watch(1)
        assertEquals(Ui.NONE, sim.ui)
        sim.close()
        assertEquals(Ui.NONE, sim.ui)
    }

    @Test
    fun `the agent's card goes with the video`() {
        val sim = Sim()
        sim.busy = true
        sim.watch(3)
        sim.tick(screen = null)
        assertEquals(Ui.NONE, sim.ui)
        sim.watch(3)
        assertIs<Card.Busy>(sim.ui.card)
    }
}
