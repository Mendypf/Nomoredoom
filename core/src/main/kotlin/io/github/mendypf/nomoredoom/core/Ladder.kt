package io.github.mendypf.nomoredoom.core

internal enum class Kind { PILL, TAP, HOLD, HOME }

/**
 * One step of getting more annoying.
 *
 * @param overMs how far past the limit this step starts
 * @param holdMs how long the card holds the screen before its X can be pressed
 * @param everyMs how much watching is allowed before the card comes back
 * @param dim how dark the video gets between cards, 0 to 1
 * @param quiet whether the card also pauses the video's sound
 */
internal data class Rung(
    val overMs: Long,
    val kind: Kind,
    val holdMs: Long = 0,
    val everyMs: Long = 0,
    val dim: Float = 0f,
    val quiet: Boolean = false,
)

internal object Ladder {
    private const val SEC = 1_000L
    private const val MIN = 60 * SEC

    val rungs: List<Rung> = listOf(
        Rung(overMs = 0, kind = Kind.PILL),
        Rung(overMs = 1 * MIN, kind = Kind.TAP),
        Rung(overMs = 2 * MIN, kind = Kind.HOLD, holdMs = 3 * SEC, everyMs = 60 * SEC, dim = 0.15f),
        Rung(overMs = 4 * MIN, kind = Kind.HOLD, holdMs = 5 * SEC, everyMs = 45 * SEC, dim = 0.30f),
        Rung(overMs = 6 * MIN, kind = Kind.HOLD, holdMs = 8 * SEC, everyMs = 30 * SEC, dim = 0.45f, quiet = true),
        Rung(overMs = 8 * MIN, kind = Kind.HOLD, holdMs = 12 * SEC, everyMs = 20 * SEC, dim = 0.60f, quiet = true),
        Rung(overMs = 10 * MIN, kind = Kind.HOME, dim = 0.60f),
    )

    private val lastHold = rungs.last { it.kind == Kind.HOLD }

    /** The step for this much time past the limit; null while still under it. */
    fun rungAt(overMs: Long, sendHome: Boolean): Rung? {
        val rung = rungs.lastOrNull { overMs >= it.overMs }
        return if (rung?.kind == Kind.HOME && !sendHome) lastHold else rung
    }
}
