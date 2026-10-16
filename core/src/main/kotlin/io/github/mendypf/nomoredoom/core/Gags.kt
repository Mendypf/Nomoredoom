package io.github.mendypf.nomoredoom.core

public class GagError(public val line: Int, message: String) : IllegalArgumentException("line $line: $message")

/**
 * The memes that land on the video while the person's coding agent works, kept as a text file so
 * they can be changed without rebuilding the app. One GIF a line:
 *
 *     # a GIPHY page, a GIPHY file, or a GIF of your own
 *     https://giphy.com/gifs/judge-judy-hurry-up-Emg9qPKR5hquI
 *     https://media.giphy.com/media/4bpK2k0Yru5Us/giphy.gif
 *     my-cat.gif
 *
 * The app never follows a link. It only takes the GIF's name from it and looks for
 * a file of that name on the phone, which the tool on the computer puts there.
 */
public class Gags private constructor(
    /** The names of the GIFs, without their endings, each once, in the file's order. */
    public val media: List<String>,
) {
    public companion object {
        public val NONE: Gags = Gags(emptyList())

        private const val MAX_GAGS = 200

        /** Some Windows editors put this invisible mark in front of a file, and trim() does not remove it. */
        private const val BYTE_ORDER_MARK = "﻿"

        private val PAGE = Regex("""https://(?:www\.)?giphy\.com/gifs/([A-Za-z0-9-]+)/?""")
        private val FILE_AT_GIPHY = Regex("""https://media\d*\.giphy\.com/media/([A-Za-z0-9]+)/[A-Za-z0-9._-]+""")
        private val OWN_FILE = Regex("""([A-Za-z0-9][A-Za-z0-9_-]{0,63})\.(?:gif|webp)""", RegexOption.IGNORE_CASE)
        private val GIPHY_ID = Regex("""[A-Za-z0-9]{5,40}""")

        public fun parse(text: String): Gags {
            val names = LinkedHashSet<String>()
            var count = 0
            text.removePrefix(BYTE_ORDER_MARK).lineSequence().forEachIndexed { index, raw ->
                val n = index + 1
                val row = raw.trim()
                if (row.isEmpty() || row.startsWith("#")) return@forEachIndexed
                if (++count > MAX_GAGS) throw GagError(n, "more than $MAX_GAGS GIFs")
                names += mediaName(row, n)
            }
            return Gags(names.toList())
        }

        private fun mediaName(ref: String, n: Int): String {
            PAGE.matchEntire(ref)?.let { page ->
                val id = page.groupValues[1].substringAfterLast('-')
                if (GIPHY_ID.matches(id)) return id
            }
            FILE_AT_GIPHY.matchEntire(ref)?.let { return it.groupValues[1] }
            OWN_FILE.matchEntire(ref)?.let { return it.groupValues[1] }
            throw GagError(n, "'$ref' should be one giphy.com/gifs/ link or one file name ending in .gif or .webp")
        }
    }
}
