package io.github.mendypf.nomoredoom.core

/**
 * Turns the part of [State] worth keeping into one line of text and back:
 *
 *     v1|watched=754000|last=1791763200000,123456,9,0|pill=1|tap=1|hold=780000|home=700000|log=20738:Reels=600000
 *
 * What is on screen right now (a card, the pill) is left out on purpose: after a
 * restart the engine works out again what is due.
 */
public object StateCodec {
    private const val VERSION = "v1"
    private const val MAX_MS = 366L * 86_400_000L

    public fun encode(state: State): String = listOf(
        VERSION,
        "watched=${state.watchedMs}",
        "last=" + state.lastWatch?.let { "${it.wallMs},${it.upMs},${it.boot},${it.utcOffsetMs}" }.orEmpty(),
        "pill=${state.pillDone.bit()}",
        "tap=${state.tapDone.bit()}",
        "hold=${state.nextHoldAtMs}",
        "home=${state.lastHomeAtMs ?: ""}",
        "log=" + state.log.days.entries.joinToString(";") { (day, names) ->
            "$day:" + names.entries.joinToString(",") { (name, ms) -> "$name=$ms" }
        },
    ).joinToString("|")

    /** Null when the text is not something [encode] wrote. */
    public fun decode(text: String): State? = runCatching { parse(text) }.getOrNull()

    private fun parse(text: String): State {
        val parts = text.split('|')
        require(parts.first() == VERSION)
        val fields = parts.drop(1).associate { part ->
            val at = part.indexOf('=')
            require(at > 0)
            part.substring(0, at) to part.substring(at + 1)
        }
        return State(
            watchedMs = fields.getValue("watched").ms(),
            lastWatch = fields.getValue("last").takeIf { it.isNotEmpty() }?.let(::clock),
            pillDone = fields.getValue("pill").bit(),
            tapDone = fields.getValue("tap").bit(),
            nextHoldAtMs = fields.getValue("hold").ms(),
            lastHomeAtMs = fields.getValue("home").takeIf { it.isNotEmpty() }?.ms(),
            log = DayLog(fields.getValue("log").takeIf { it.isNotEmpty() }?.let(::days).orEmpty()),
        )
    }

    private fun clock(text: String): Clock {
        val (wall, up, boot, offset) = text.split(',').also { require(it.size == 4) }
        return Clock(wallMs = wall.toLong(), upMs = up.toLong(), boot = boot.toInt(), utcOffsetMs = offset.toInt())
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

    private fun Boolean.bit(): Int = if (this) 1 else 0

    private fun String.bit(): Boolean = when (this) {
        "1" -> true
        "0" -> false
        else -> throw IllegalArgumentException()
    }

    private fun String.ms(): Long = toLong().also { require(it in 0..MAX_MS) }
}
