package io.github.mendypf.nomoredoom.core

/**
 * One joke for a popup.
 *
 * @param over the step it belongs to, as whole minutes past the limit (the same numbers as the ladder: 1, 2, 4, 6, 8, 10)
 * @param line the words under the GIF; `{minutes}` becomes the minutes watched
 * @param media the name of its GIF without the ending, or null for words only
 */
public data class Gag(val over: Int, val line: String, val media: String?, val stamp: Stamp = Stamp.TOP_LEFT) {
    public fun text(minutes: Int): String = line.replace("{minutes}", minutes.toString())
}

/** Which corner of the GIF the minutes are stamped on. The top right is taken by the X. */
public enum class Stamp { TOP_LEFT, BOTTOM_LEFT, BOTTOM_RIGHT, OFF }

public class GagError(public val line: Int, message: String) : IllegalArgumentException("line $line: $message")

/**
 * What makes the popups funny, kept as a text file so the jokes can be changed
 * without rebuilding the app:
 *
 *     # minutes past the limit | the line | the GIF (optional) | stamp=bottom-left (optional)
 *     +1 | You have been here a while. | https://giphy.com/gifs/judge-judy-hurry-up-Emg9qPKR5hquI
 *     +6 | Put the phone down.         | my-cat.gif | stamp=bottom-left
 *
 * The minutes are stamped on the GIF's top left corner. `stamp=` moves the stamp
 * off a caption: bottom-left, bottom-right, or off.
 *
 * The app never follows the link. It only takes the GIF's name from it and
 * looks for a file of that name on the phone, which the tool on the computer
 * puts there.
 */
public class Gags private constructor(public val all: List<Gag>) {

    /**
     * The gag for a card on the step that starts [over] minutes past the limit.
     * Cards on one step take turns through that step's gags; a step with none
     * borrows from the nearest earlier step.
     */
    public fun pick(over: Int, turn: Int): Gag? {
        val step = all.filter { it.over <= over }.maxOfOrNull { it.over } ?: return null
        val mine = all.filter { it.over == step }
        return mine[Math.floorMod(turn, mine.size)]
    }

    public companion object {
        public val NONE: Gags = Gags(emptyList())

        private const val MAX_GAGS = 200
        private const val MAX_LINE = 120
        private const val MAX_OVER = 600

        /** Some Windows editors put this invisible mark in front of a file, and trim() does not remove it. */
        private const val BYTE_ORDER_MARK = "\uFEFF"

        private val PAGE = Regex("""https://(?:www\.)?giphy\.com/gifs/([A-Za-z0-9-]+)/?""")
        private val FILE_AT_GIPHY = Regex("""https://media\d*\.giphy\.com/media/([A-Za-z0-9]+)/[A-Za-z0-9._-]+""")
        private val OWN_FILE = Regex("""([A-Za-z0-9][A-Za-z0-9_-]{0,63})\.(?:gif|webp)""", RegexOption.IGNORE_CASE)
        private val GIPHY_ID = Regex("""[A-Za-z0-9]{5,40}""")
        private val STAMPS = mapOf(
            "top-left" to Stamp.TOP_LEFT,
            "bottom-left" to Stamp.BOTTOM_LEFT,
            "bottom-right" to Stamp.BOTTOM_RIGHT,
            "off" to Stamp.OFF,
        )

        public fun parse(text: String): Gags {
            val gags = ArrayList<Gag>()
            text.removePrefix(BYTE_ORDER_MARK).lineSequence().forEachIndexed { index, raw ->
                val n = index + 1
                val row = raw.trim()
                if (row.isEmpty() || row.startsWith("#")) return@forEachIndexed
                if (gags.size == MAX_GAGS) throw GagError(n, "more than $MAX_GAGS lines")
                val parts = row.split('|').map { it.trim() }
                if (parts.size !in 2..4) throw GagError(n, "expected: minutes | line | GIF | stamp=bottom-left (the last two are optional)")
                val over = parts[0].removePrefix("+").toIntOrNull()?.takeIf { it in 0..MAX_OVER }
                    ?: throw GagError(n, "'${parts[0]}' should be whole minutes past the limit, 0 to $MAX_OVER")
                val line = parts[1]
                if (line.length > MAX_LINE) throw GagError(n, "the line is longer than $MAX_LINE letters")
                val media = parts.getOrNull(2)?.takeIf { it.isNotEmpty() }?.let { mediaName(it, n) }
                if (line.isEmpty() && media == null) throw GagError(n, "needs a line, a GIF, or both")
                val stamp = parts.getOrNull(3)?.let { option ->
                    STAMPS[option.removePrefix("stamp=").takeIf { it != option }]
                        ?: throw GagError(n, "'$option' should be stamp=bottom-left, stamp=bottom-right or stamp=off")
                } ?: Stamp.TOP_LEFT
                gags += Gag(over, line, media, stamp)
            }
            return Gags(gags)
        }

        private fun mediaName(ref: String, n: Int): String {
            PAGE.matchEntire(ref)?.let { page ->
                val id = page.groupValues[1].substringAfterLast('-')
                if (GIPHY_ID.matches(id)) return id
            }
            FILE_AT_GIPHY.matchEntire(ref)?.let { return it.groupValues[1] }
            OWN_FILE.matchEntire(ref)?.let { return it.groupValues[1] }
            throw GagError(n, "'$ref' should be a giphy.com/gifs/ link or a file name ending in .gif or .webp")
        }
    }
}
