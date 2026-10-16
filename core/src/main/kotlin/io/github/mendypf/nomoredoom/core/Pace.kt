package io.github.mendypf.nomoredoom.core

/**
 * How long to wait between looks while a watched app is in front and no short video is.
 *
 * Scrolling a feed changes the layout many times a second, and every look asks the app a
 * question on its own main thread. The first looks stay quick, because someone who has just
 * opened the app may tap Reels next. After that, each look that finds nothing makes the next
 * one wait longer, up to [SLOWEST_MS]. The price is that a short video can be noticed a few
 * seconds late.
 */
public object Pace {
    public const val QUICK_MS: Long = 700
    public const val SLOWER_MS: Long = 2_000
    public const val SLOWEST_MS: Long = 5_000

    private const val QUICK_LOOKS = 3
    private const val SLOWER_LOOKS = 10

    /** @param misses how many looks in a row found no short video */
    public fun gapMs(misses: Int): Long = when {
        misses < QUICK_LOOKS -> QUICK_MS
        misses < SLOWER_LOOKS -> SLOWER_MS
        else -> SLOWEST_MS
    }
}
