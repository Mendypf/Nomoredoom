package io.github.mendypf.nomoredoom.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class AgentSignalTest {

    @Test
    fun `nothing in the window means the agent is not working`() {
        assertFalse(AgentSignal.busy(""))
    }

    @Test
    fun `the last line of a session decides`() {
        assertTrue(AgentSignal.busy("working a1\n"))
        assertFalse(AgentSignal.busy("working a1\ndone a1\n"))
        assertTrue(AgentSignal.busy("working a1\ndone a1\nworking a1\n"))
    }

    @Test
    fun `one session still working is enough`() {
        assertTrue(AgentSignal.busy("working a1\nworking b2\ndone b2\n"))
        assertFalse(AgentSignal.busy("working a1\nworking b2\ndone b2\ndone a1\n"))
    }

    @Test
    fun `anything else on the topic is ignored`() {
        assertFalse(AgentSignal.busy("hello\nWORKING a1\nworking\nworking a1 extra\nworking a-1\n"))
        assertTrue(AgentSignal.busy("  working a1  \r\n"))
    }

    @Test
    fun `only an https address of a topic is taken`() {
        assertTrue(AgentSignal.validUrl("https://ntfy.sh/nomoredoom-Ab3_x"))
        assertTrue(AgentSignal.validUrl("https://ntfy.example.org:8443/topic"))
        for (bad in listOf(
            "http://ntfy.sh/topic",
            "https://ntfy.sh/",
            "https://ntfy.sh/topic/raw",
            "https://ntfy.sh/topic?x=1",
            "https://user@ntfy.sh/topic",
            "https://ntfy.sh/" + "a".repeat(65),
            "file:///sdcard/topic",
        )) {
            assertFalse(AgentSignal.validUrl(bad), bad)
        }
    }

    @Test
    fun `the phone asks for the last twenty minutes only`() {
        assertEquals("https://ntfy.sh/t1/raw?poll=1&since=20m", AgentSignal.pollUrl("https://ntfy.sh/t1"))
        assertFailsWith<IllegalArgumentException> { AgentSignal.pollUrl("http://ntfy.sh/t1") }
    }
}
