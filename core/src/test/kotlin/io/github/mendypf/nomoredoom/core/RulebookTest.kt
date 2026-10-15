package io.github.mendypf.nomoredoom.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertNull
import kotlin.test.assertTrue

internal class FakeScreen(
    override val pkg: String,
    override val height: Int = 2400,
    private val views: Map<String, List<Box>> = emptyMap(),
) : Screen {
    val asked = mutableListOf<String>()

    override fun find(viewId: String): List<Box> {
        asked += viewId
        return views[viewId].orEmpty()
    }
}

class RulebookTest {
    private val allowed = setOf(
        "com.instagram.android",
        "com.google.android.youtube",
        "com.zhiliaoapp.musically",
    )

    private val text = """
        # what counts as a short-video screen
        com.zhiliaoapp.musically | TikTok | *

        com.google.android.youtube | Shorts | reel_recycler, reel_player_page_container | min-height=0.5
        com.instagram.android | Reels | clips_viewer_view_pager
    """.trimIndent()

    private val book = Rulebook.parse(text, allowed)
    private val fullScreen = Box(0, 0, 1080, 2400)

    @Test
    fun `reads one rule per line and skips comments and blanks`() {
        assertEquals(listOf("TikTok", "Shorts", "Reels"), book.rules.map { it.name })
        assertEquals(
            listOf("reel_recycler", "reel_player_page_container"),
            book.ruleFor("com.google.android.youtube")?.viewIds,
        )
    }

    @Test
    fun `a star means the whole app counts`() {
        assertTrue(book.ruleFor("com.zhiliaoapp.musically")!!.wholeApp)
    }

    @Test
    fun `min-height can be set per rule and is no bar at all otherwise`() {
        assertEquals(0.5, book.ruleFor("com.google.android.youtube")!!.minHeight)
        assertEquals(0.0, book.ruleFor("com.instagram.android")!!.minHeight)
    }

    @Test
    fun `refuses an app that is not on the built-in list`() {
        val error = assertFailsWith<RuleError> {
            Rulebook.parse("com.whatsapp | Chats | *", allowed)
        }
        assertEquals(1, error.line)
    }

    @Test
    fun `refuses the same app twice`() {
        val twice = "com.zhiliaoapp.musically | TikTok | *\ncom.zhiliaoapp.musically | Again | *"
        assertEquals(2, assertFailsWith<RuleError> { Rulebook.parse(twice, allowed) }.line)
    }

    @Test
    fun `refuses a view id with odd characters`() {
        assertFailsWith<RuleError> {
            Rulebook.parse("com.instagram.android | Reels | clips viewer", allowed)
        }
        assertFailsWith<RuleError> {
            Rulebook.parse("com.instagram.android | Reels | com.instagram.android:id/clips", allowed)
        }
    }

    @Test
    fun `refuses a name with punctuation so it can be saved safely`() {
        for (name in listOf("Reels=1", "a,b", "x;y", "x:y", "")) {
            assertFailsWith<RuleError>("name '$name'") {
                Rulebook.parse("com.instagram.android | $name | clips", allowed)
            }
        }
        assertEquals("YouTube Shorts", Rulebook.parse("com.google.android.youtube | YouTube Shorts | a", allowed).rules[0].name)
    }

    @Test
    fun `refuses a line with too few parts`() {
        assertFailsWith<RuleError> { Rulebook.parse("com.instagram.android | Reels", allowed) }
    }

    @Test
    fun `refuses a min-height outside zero to one`() {
        assertFailsWith<RuleError> {
            Rulebook.parse("com.instagram.android | Reels | clips | min-height=1.5", allowed)
        }
        assertFailsWith<RuleError> {
            Rulebook.parse("com.instagram.android | Reels | clips | min-height=-0.1", allowed)
        }
    }

    @Test
    fun `refuses a file with no rules`() {
        assertFailsWith<RuleError> { Rulebook.parse("# nothing here\n", allowed) }
    }

    @Test
    fun `whole-app rule matches any screen of that app without looking inside it`() {
        val screen = FakeScreen("com.zhiliaoapp.musically")
        assertEquals("TikTok", book.match(screen)?.name)
        assertTrue(screen.asked.isEmpty())
    }

    @Test
    fun `view rule matches when the view fills most of the screen`() {
        val screen = FakeScreen(
            "com.google.android.youtube",
            views = mapOf("reel_player_page_container" to listOf(fullScreen)),
        )
        assertEquals("Shorts", book.match(screen)?.name)
    }

    @Test
    fun `view rule ignores a small view with the same id`() {
        val shelfThumbnail = Box(0, 900, 400, 1500)
        val screen = FakeScreen(
            "com.google.android.youtube",
            views = mapOf("reel_recycler" to listOf(shelfThumbnail)),
        )
        assertNull(book.match(screen))
    }

    @Test
    fun `without a height bar any showing view counts however small`() {
        // Instagram's Reels pager is a 26 pixel strip when a reel is opened from a message
        val strip = FakeScreen(
            "com.instagram.android",
            views = mapOf("clips_viewer_view_pager" to listOf(Box(0, 2153, 1080, 2179))),
        )
        assertEquals("Reels", book.match(strip)?.name)
    }

    @Test
    fun `a view folded away to nothing does not count`() {
        // Instagram keeps the Reels pager in every screen, hidden, with a width of zero or less
        for (hidden in listOf(Box(0, 1758, -1080, 2211), Box(0, 0, 0, 2400), Box(0, 500, 1080, 500))) {
            val screen = FakeScreen("com.instagram.android", views = mapOf("clips_viewer_view_pager" to listOf(hidden)))
            assertNull(book.match(screen), "$hidden")
        }
    }

    @Test
    fun `one showing copy among hidden ones is enough`() {
        val screen = FakeScreen(
            "com.instagram.android",
            views = mapOf("clips_viewer_view_pager" to listOf(Box(0, 1758, -1080, 2211), Box(0, 1396, 1080, 2211))),
        )
        assertEquals("Reels", book.match(screen)?.name)
    }

    @Test
    fun `view rule does not match the same app on another screen`() {
        assertNull(book.match(FakeScreen("com.instagram.android")))
    }

    @Test
    fun `an app without a rule never matches and is never looked into`() {
        val screen = FakeScreen("com.whatsapp", views = mapOf("clips_viewer_view_pager" to listOf(fullScreen)))
        assertNull(book.match(screen))
        assertTrue(screen.asked.isEmpty())
    }

    @Test
    fun `the height bar follows the window not the phone`() {
        // split screen: the app only has the top half, and the reel fills that half
        val halfWindow = FakeScreen(
            "com.google.android.youtube",
            height = 1200,
            views = mapOf("reel_recycler" to listOf(Box(0, 0, 1080, 1200))),
        )
        assertEquals("Shorts", book.match(halfWindow)?.name)
    }


    @Test
    fun `a file saved with an invisible mark in front of its first line still reads`() {
        val book = Rulebook.parse("﻿com.instagram.android | Reels | clips_viewer", allowed)
        assertEquals("Reels", book.ruleFor("com.instagram.android")?.name)
    }
}
