package io.github.mendypf.nomoredoom.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertNull
import kotlin.test.assertTrue

class GagsTest {
    private val gags = Gags.parse(
        """
        # minutes past the limit | the line | the GIF
        +1 | You have been here a while. | https://giphy.com/gifs/judge-judy-hurry-up-Emg9qPKR5hquI
        +2 | You said one more. | https://giphy.com/gifs/unimpressed-chloe-NMkDjJP2ZC29G
        +2 | Still scrolling.
        6  | Put the phone down. | my-cat.gif
        10 | Going home. | https://media.giphy.com/media/4bpK2k0Yru5Us/giphy.gif
        """.trimIndent(),
    )

    @Test
    fun `reads the step and the line and the name of the GIF`() {
        val first = gags.all.first()
        assertEquals(1, first.over)
        assertEquals("You have been here a while.", first.line)
        assertEquals("Emg9qPKR5hquI", first.media)
    }

    @Test
    fun `a GIF can be a GIPHY page or a GIPHY file or a file of your own`() {
        assertEquals(listOf("Emg9qPKR5hquI", "NMkDjJP2ZC29G", null, "my-cat", "4bpK2k0Yru5Us"), gags.all.map { it.media })
    }

    @Test
    fun `a line may come without a GIF`() {
        assertNull(gags.all[2].media)
        assertEquals("Still scrolling.", gags.all[2].line)
    }

    @Test
    fun `a card gets a gag written for its own step`() {
        assertEquals("You have been here a while.", gags.pick(over = 1, turn = 7)?.line)
        assertEquals("Going home.", gags.pick(over = 10, turn = 0)?.line)
    }

    @Test
    fun `cards on the same step take turns through its gags`() {
        val seen = (0..3).map { gags.pick(over = 2, turn = it)?.line }
        assertEquals(listOf("You said one more.", "Still scrolling.", "You said one more.", "Still scrolling."), seen)
    }

    @Test
    fun `a step with nothing written for it borrows from the step before`() {
        assertEquals("You said one more.", gags.pick(over = 4, turn = 0)?.line)
        assertEquals("Put the phone down.", gags.pick(over = 8, turn = 3)?.line)
    }

    @Test
    fun `nothing is picked below the first step or from an empty file`() {
        assertNull(gags.pick(over = 0, turn = 0))
        assertNull(Gags.NONE.pick(over = 4, turn = 0))
        assertTrue(Gags.parse("# only a comment\n\n").all.isEmpty())
    }

    @Test
    fun `turns below zero still land on a gag`() {
        assertEquals("Still scrolling.", gags.pick(over = 2, turn = -1)?.line)
    }

    @Test
    fun `the minutes are filled into a line that asks for them`() {
        val gag = Gags.parse("1 | {minutes} minutes of this.").all.single()
        assertEquals("12 minutes of this.", gag.text(minutes = 12))
        assertEquals("Going home.", gags.all.last().text(minutes = 20))
    }

    @Test
    fun `the names of the GIFs it wants are listed once each`() {
        val twice = Gags.parse("1 | a | cat.gif\n2 | b | cat.gif\n3 | c | dog.webp")
        assertEquals(setOf("cat", "dog"), twice.all.mapNotNull { it.media }.toSet())
    }

    @Test
    fun `the stamp sits on the top left corner unless the line says otherwise`() {
        val pack = Gags.parse(
            """
            1 | a | one.gif
            1 | b | two.gif | stamp=bottom-left
            1 | c | three.gif | stamp=bottom-right
            1 | d | four.gif | stamp=off
            1 | e |          | stamp=bottom-left
            """.trimIndent(),
        )
        assertEquals(
            listOf(Stamp.TOP_LEFT, Stamp.BOTTOM_LEFT, Stamp.BOTTOM_RIGHT, Stamp.OFF, Stamp.BOTTOM_LEFT),
            pack.all.map { it.stamp },
        )
        assertNull(pack.all.last().media)
    }

    @Test
    fun `a broken line is refused with its line number`() {
        val cases = mapOf(
            "soon | hello" to "line 1",
            "1 | fine\n2 |  | " to "line 2",
            "1 | hello | ../../etc/passwd.gif" to "line 1",
            "1 | hello | https://example.com/cat.gif" to "line 1",
            "1 | hello | cat.exe" to "line 1",
            "1 | hello | a.gif | extra" to "line 1",
            "1 | hello | a.gif | stamp=top-right" to "line 1",
            "1 | hello | a.gif | stamp=bottom-left | more" to "line 1",
            "700 | hello" to "line 1",
            "1 | ${"x".repeat(200)}" to "line 1",
        )
        for ((text, where) in cases) {
            val error = assertFailsWith<GagError>(text) { Gags.parse(text) }
            assertTrue(where in error.message.orEmpty(), "$text -> ${error.message}")
        }
    }

    @Test
    fun `a file with too many lines is refused`() {
        val many = (1..201).joinToString("\n") { "1 | line $it" }
        assertFailsWith<GagError> { Gags.parse(many) }
    }


    @Test
    fun `a file saved with an invisible mark in front of its first line still reads`() {
        // Notepad's "UTF-8 with BOM" and PowerShell 5.1 put U+FEFF first, and trim() leaves it there.
        val pack = Gags.parse("﻿# my jokes\n+1 | You have been here a while.")
        assertEquals("You have been here a while.", pack.all.single().line)
    }
}
