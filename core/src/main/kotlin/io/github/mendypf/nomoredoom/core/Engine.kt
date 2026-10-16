package io.github.mendypf.nomoredoom.core

/**
 * One reading of the phone's clocks.
 *
 * @param wallMs time of day (the user can change it, and it survives a restart)
 * @param upMs milliseconds since the phone started, sleep included (cannot be changed, resets on restart)
 * @param boot how many times the phone has started; tells two restarts apart
 * @param utcOffsetMs the phone's time zone right now
 */
public data class Clock(val wallMs: Long, val upMs: Long, val boot: Int, val utcOffsetMs: Int = 0)

/** Everything that should be on screen right now. The phone side only draws this. */
public data class Ui(
    /**
     * The memes over the video while the person's coding agent works. They have no X, they ask Android
     * to pause the video's sound, and they go when the agent is done.
     */
    val cover: Boolean = false,
) {
    public companion object {
        public val NONE: Ui = Ui()
    }
}

/**
 * Once a second while it matters. [screen] is the short-video screen in front, or null.
 * [busy] is true while the person's coding agent says it is working (see [AgentSignal]).
 */
public data class Tick(val clock: Clock, val screen: String?, val busy: Boolean = false)

public data class Outcome(val state: State, val ui: Ui)

public data class State(
    // kept across restarts
    val log: DayLog = DayLog(),
    // only while the service is running
    val wasWatching: Boolean = false,
    val lastTick: Clock? = null,
    val enteredUp: Long = 0,
    val covered: Boolean = false,
)

/**
 * The whole behaviour as one function: what the phone saw plus the state before gives
 * the state after and what belongs on screen. Nothing in here touches Android,
 * a clock or a file, so every rule below is a plain unit test.
 */
public object Engine {
    /** A tick that arrives late (the phone was busy) counts this much at most. */
    internal const val MAX_GAP_MS = 3_000L

    /** How long a short-video screen has to stay in front before the memes may cover it. */
    internal const val SETTLE_MS = 2_000L

    public fun step(state: State, tick: Tick): Outcome {
        val clock = tick.clock
        val screen = tick.screen
            ?: return Outcome(state.copy(wasWatching = false, lastTick = clock, covered = false), Ui.NONE)
        var s = state
        if (!s.wasWatching) {
            s = s.copy(enteredUp = clock.upMs)
        } else if (!s.covered) {
            // Memes over the video mean nobody is watching it, so that time is not counted.
            val last = s.lastTick
            if (last != null && last.boot == clock.boot) {
                val gained = (clock.upMs - last.upMs).coerceIn(0, MAX_GAP_MS)
                s = s.copy(log = s.log.add(clock.day(), screen, gained))
            }
        }
        val settled = clock.upMs - s.enteredUp >= SETTLE_MS
        s = s.copy(wasWatching = true, lastTick = clock, covered = tick.busy && settled)
        return Outcome(s, Ui(cover = s.covered))
    }
}
