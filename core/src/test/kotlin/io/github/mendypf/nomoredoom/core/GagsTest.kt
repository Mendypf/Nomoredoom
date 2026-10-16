package io.github.mendypf.nomoredoom.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

class GagsTest {

    @Test
    fun `reads one GIF a line and skips blank lines and comments`() {
        val gags = Gags.parse(
            """
            # the memes

            https://giphy.com/gifs/judge-judy-hurry-up-Emg9qPKR5hquI
              https://giphy.com/gifs/unimpressed-chloe-NMkDjJP2ZC29G
            """.trimIndent(),
        )
        assertEquals(listOf("Emg9qPKR5hquI", "NMkDjJP2ZC29G"), gags.media)
    }

    @Test
    fun `a GIF can be a GIPHY page or a GIPHY file or a file of your own`() {
        val gags = Gags.parse(
            "https://giphy.com/gifs/judge-judy-hurry-up-Emg9qPKR5hquI\n" +
                "https://media.giphy.com/media/4bpK2k0Yru5Us/giphy.gif\n" +
                "my-cat.gif\n" +
                "Dog.WEBP\n",
        )
        assertEquals(listOf("Emg9qPKR5hquI", "4bpK2k0Yru5Us", "my-cat", "Dog"), gags.media)
    }

    @Test
    fun `a GIF listed twice is played once`() {
        assertEquals(listOf("cat", "dog"), Gags.parse("cat.gif\ndog.webp\ncat.gif").media)
    }

    @Test
    fun `an empty file has no GIFs`() {
        assertTrue(Gags.parse("# only a comment\n\n").media.isEmpty())
        assertTrue(Gags.NONE.media.isEmpty())
    }

    @Test
    fun `a broken line is refused with its line number`() {
        val cases = mapOf(
            "../../etc/passwd.gif" to "line 1",
            "https://example.com/cat.gif" to "line 1",
            "cat.exe" to "line 1",
            "cat.gif dog.gif" to "line 1",
            "fine.gif\nnot a gif" to "line 2",
            "+1 | the old timer cards had lines | cat.gif" to "line 1",
        )
        for ((text, where) in cases) {
            val error = assertFailsWith<GagError>(text) { Gags.parse(text) }
            assertTrue(where in error.message.orEmpty(), "$text -> ${error.message}")
        }
    }

    @Test
    fun `a file with too many lines is refused`() {
        val many = (1..201).joinToString("\n") { "gif$it.gif" }
        assertFailsWith<GagError> { Gags.parse(many) }
    }

    @Test
    fun `a file saved with an invisible mark in front of its first line still reads`() {
        // Notepad's "UTF-8 with BOM" and PowerShell 5.1 put U+FEFF first, and trim() leaves it there.
        assertEquals(listOf("cat"), Gags.parse("﻿# my memes\ncat.gif").media)
    }
}
