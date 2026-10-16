package io.github.mendypf.nomoredoom.core

/**
 * Turns the part of [State] worth keeping into one line of text and back:
 *
 *     v2|log=20738:Reels=600000,Shorts=154000;20737:TikTok=9000
 *
 * That is the watched time of the last two weeks. Whether a video is in front right now is
 * left out on purpose: after a restart the engine sees that again. A line saved by the
 * version that had the timer cards (v1) still gives its watched time.
 */
public object StateCodec {
    private const val VERSION = "v2"
    private val READS = setOf("v1", VERSION)
    private const val MAX_MS = 366L * 86_400_000L

    public fun encode(state: State): String = listOf(
        VERSION,
        "log=" + state.log.days.entries.joinToString(";") { (day, names) ->
            "$day:" + names.entries.joinToString(",") { (name, ms) -> "$name=$ms" }
        },
    ).joinToString("|")

    /** Null when the text is not something [encode] wrote. */
    public fun decode(text: String): State? = runCatching { parse(text) }.getOrNull()

    private fun parse(text: String): State {
        val parts = text.split('|')
        require(parts.first() in READS)
        val fields = parts.drop(1).associate { part ->
            val at = part.indexOf('=')
            require(at > 0)
            part.substring(0, at) to part.substring(at + 1)
        }
        return State(log = DayLog(fields.getValue("log").takeIf { it.isNotEmpty() }?.let(::days).orEmpty()))
    }

    private fun days(text: String): Map<Int, Map<String, Long>> = text.split(';').associate { entry ->
        val day = entry.substringBefore(':').toInt()
        val names = entry.substringAfter(':').split(',').associate { pair ->
            val name = pair.substringBeforeLast('=')
            require(name.isNotEmpty())
            name to pair.substringAfterLast('=').ms()
        }
        day to names
    }

    private fun String.ms(): Long = toLong().also { require(it in 0..MAX_MS) }
}
