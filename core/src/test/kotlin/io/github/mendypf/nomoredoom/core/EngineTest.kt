package io.github.mendypf.nomoredoom.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

private const val SEC = 1_000L
private const val MIN = 60 * SEC

/** Drives the engine the way the phone does: one tick a second. */
private class Sim {
    var state = State()
    var ui = Ui.NONE
    var up = 5_000_000L
    var wall = 1_790_000_000_000L
    var boot = 7

    /** What the agent says on the next ticks. */
    var busy = false

    fun clock() = Clock(wallMs = wall, upMs = up, boot = boot)

    fun pass(ms: Long) {
        up += ms
        wall += ms
    }

    fun tick(screen: String? = "Reels", after: Long = SEC) {
        pass(after)
        val out = Engine.step(state, Tick(clock(), screen, busy))
        state = out.state
        ui = out.ui
    }

    fun watch(seconds: Int, screen: String = "Reels") = repeat(seconds) { tick(screen) }

    fun leave(forMs: Long) {
        tick(screen = null)
        pass(forMs - SEC)
    }

    fun reboot() {
        boot += 1
        up = 40_000L
    }

    fun today(): Long = state.log.total(clock().day())
}

class EngineTest {

    // ---- today's count

    @Test
    fun `counts one second for each second of watching`() {
        val sim = Sim()
        sim.watch(61)
        assertEquals(60 * SEC, sim.today())
    }

    @Test
    fun `time away is not counted`() {
        val sim = Sim()
        sim.watch(31)
        sim.leave(5 * MIN)
        sim.watch(31)
        assertEquals(60 * SEC, sim.today())
    }

    @Test
    fun `a late tick counts three seconds at most`() {
        val sim = Sim()
        sim.watch(2)
        sim.tick(after = 45 * SEC)
        assertEquals(4 * SEC, sim.today())
    }

    @Test
    fun `watched time is logged under the name of the screen`() {
        val sim = Sim()
        sim.watch(31, "Reels")
        sim.watch(20, "Shorts")
        val today = sim.clock().day()
        assertEquals(mapOf("Reels" to 30 * SEC, "Shorts" to 20 * SEC), sim.state.log.byName(today))
    }

    @Test
    fun `the tick after a restart of the phone counts nothing`() {
        val sim = Sim()
        sim.watch(11)
        sim.reboot()
        sim.watch(3)
        assertEquals(12 * SEC, sim.today(), "ten seconds before, two after: the first tick after the restart adds none")
    }

    @Test
    fun `nothing is drawn while the agent is not working, however long the video runs`() {
        val sim = Sim()
        repeat(25 * 60) {
            sim.tick()
            assertEquals(Ui.NONE, sim.ui)
        }
    }

    // ---- while the person's agent works

    @Test
    fun `while the agent works the memes cover the video once the screen has settled`() {
        val sim = Sim()
        sim.busy = true
        sim.watch(1)
        assertFalse(sim.ui.cover, "a screen that just came up is left to settle first")
        sim.watch(1)
        assertFalse(sim.ui.cover)
        sim.watch(1)
        assertTrue(sim.ui.cover)
    }

    @Test
    fun `time under the memes is not counted`() {
        val sim = Sim()
        sim.busy = true
        sim.watch(3)
        val held = sim.today()
        sim.watch(120)
        assertEquals(held, sim.today())
    }

    @Test
    fun `when the agent is done the memes go and the count carries on`() {
        val sim = Sim()
        sim.watch(31)
        sim.busy = true
        sim.watch(10)
        assertTrue(sim.ui.cover)
        sim.busy = false
        sim.watch(1)
        assertEquals(Ui.NONE, sim.ui)
        sim.watch(10)
        assertEquals(41 * SEC, sim.today(), "30 before, 1 on the tick the agent started, 10 after")
    }

    @Test
    fun `the agent starting mid-video covers it on the next tick`() {
        val sim = Sim()
        sim.watch(20)
        sim.busy = true
        sim.watch(1)
        assertTrue(sim.ui.cover)
    }

    @Test
    fun `the memes go with the video and come back with it`() {
        val sim = Sim()
        sim.busy = true
        sim.watch(3)
        sim.tick(screen = null)
        assertEquals(Ui.NONE, sim.ui)
        sim.watch(2)
        assertFalse(sim.ui.cover, "back in the app, the screen settles first again")
        sim.watch(1)
        assertTrue(sim.ui.cover)
    }

    @Test
    fun `leaving the video counts nothing and draws nothing`() {
        val sim = Sim()
        sim.watch(10)
        val before = sim.today()
        sim.busy = true
        repeat(30) { sim.tick(screen = null) }
        assertEquals(Ui.NONE, sim.ui)
        assertEquals(before, sim.today())
    }
}
