package io.github.mendypf.nomoredoom.core

/** Watched time per day and per screen name, for the last two weeks. */
public data class DayLog(val days: Map<Int, Map<String, Long>> = emptyMap()) {

    public fun add(day: Int, name: String, ms: Long): DayLog {
        if (ms <= 0) return this
        val forDay = days[day].orEmpty()
        val updated = days + (day to (forDay + (name to (forDay[name] ?: 0L) + ms)))
        val newest = updated.keys.max()
        return DayLog(updated.filterKeys { it > newest - KEEP_DAYS })
    }

    public fun byName(day: Int): Map<String, Long> = days[day].orEmpty()

    public fun total(day: Int): Long = byName(day).values.sum()

    public companion object {
        public const val KEEP_DAYS: Int = 14
    }
}

private const val DAY_MS = 86_400_000L
private const val DAY_STARTS_AT_MS = 4 * 3_600_000L

/** The day this moment belongs to. A day runs from 4 in the morning, so a late night stays one day. */
public fun Clock.day(): Int = Math.floorDiv(wallMs + utcOffsetMs - DAY_STARTS_AT_MS, DAY_MS).toInt()
