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

public data class Settings(
    val limitMs: Long = 10 * 60_000L,
    val freshAfterMs: Long = 60 * 60_000L,
    val sendHome: Boolean = true,
    /** Test hook: every watched second counts this many times. */
    val speed: Int = 1,
)

/** A popup that covers the video until the user closes it. */
public sealed interface Card {
    public val id: Int
    public val minutes: Int

    /** The step of the ladder this card is on: whole minutes past the limit at which that step starts. */
    public val over: Int

    public data class Tap(override val id: Int, override val minutes: Int, override val over: Int) : Card

    /** A card that holds the screen for [holdMs] before its X can be pressed. */
    public data class Hold(
        override val id: Int,
        override val minutes: Int,
        override val over: Int,
        val holdMs: Long,
        val quiet: Boolean,
    ) : Card

    public data class Farewell(override val id: Int, override val minutes: Int, override val over: Int) : Card

    /** Covers the video while the person's coding agent is working, and has no X. It goes when the agent is done. */
    public data class Busy(override val id: Int, override val minutes: Int) : Card {
        override val over: Int get() = 0
    }
}

/** Everything that should be on screen right now. The phone side only draws this. */
public data class Ui(
    val pillMinutes: Int? = null,
    val card: Card? = null,
    val dim: Float = 0f,
    val quiet: Boolean = false,
) {
    public companion object {
        public val NONE: Ui = Ui()
    }
}

public sealed interface Event {
    /**
     * Once a second while it matters. [screen] is the short-video screen in front, or null.
     * [busy] is true while the person's coding agent says it is working (see [AgentSignal]).
     */
    public data class Tick(val clock: Clock, val screen: String?, val busy: Boolean = false) : Event

    /** The user pressed the X on the card. (Leaving is not an event: the next tick sees no video.) */
    public data class Closed(val clock: Clock) : Event
}

public data class Outcome(val state: State, val ui: Ui, val goHome: Boolean = false)

public data class State(
    // kept across restarts
    val watchedMs: Long = 0,
    val lastWatch: Clock? = null,
    val pillDone: Boolean = false,
    val tapDone: Boolean = false,
    val nextHoldAtMs: Long = 0,
    val lastHomeAtMs: Long? = null,
    val log: DayLog = DayLog(),
    // only while the service is running
    val card: Card? = null,
    val wasWatching: Boolean = false,
    val lastTick: Clock? = null,
    val pillSinceUp: Long? = null,
    val enteredUp: Long = 0,
    val cardShownUp: Long? = null,
    val cardVisible: Boolean = false,
    val seq: Int = 0,
) {
    /** Short-video time in the sitting as of [clock]: zero once the user has stayed away long enough. */
    public fun sittingMs(clock: Clock, settings: Settings): Long {
        val last = lastWatch ?: return watchedMs
        return if (awayMs(last, clock) >= settings.freshAfterMs) 0 else watchedMs
    }

    /** Test hook: jump to a point in the sitting, with nothing answered yet. */
    public fun skippedTo(watchedMs: Long): State = copy(
        watchedMs = watchedMs.coerceAtLeast(0),
        pillDone = false,
        tapDone = false,
        nextHoldAtMs = 0,
        lastHomeAtMs = null,
        card = null,
        pillSinceUp = null,
        cardShownUp = null,
        cardVisible = false,
    )
}

/** Time between two clock readings. A restart resets [Clock.upMs], so across one the wall clock decides. */
internal fun awayMs(then: Clock, now: Clock): Long =
    if (now.boot == then.boot && now.upMs >= then.upMs) now.upMs - then.upMs
    else (now.wallMs - then.wallMs).coerceAtLeast(0)

/**
 * The whole behaviour as one function: what happened plus the state before gives
 * the state after and what belongs on screen. Nothing in here touches Android,
 * a clock or a file, so every rule below is a plain unit test.
 */
public object Engine {
    /** A tick that arrives late (the phone was busy) counts this much at most. */
    internal const val MAX_GAP_MS = 3_000L

    /** How long a short-video screen has to stay in front before any card may cover it. */
    internal const val SETTLE_MS = 2_000L
    internal const val PILL_MS = 5_000L
    internal const val FAREWELL_MS = 3_000L

    /** Watching allowed after the tap card, and after being sent home, before the next card. */
    internal const val BREATHER_MS = 20_000L

    public fun step(state: State, settings: Settings, event: Event): Outcome = when (event) {
        is Event.Tick ->
            if (event.screen == null) lookedAway(state, event.clock)
            else watching(state, settings, event.clock, event.screen, event.busy)
        is Event.Closed -> closed(state, settings, event.clock)
    }

    private fun lookedAway(state: State, clock: Clock): Outcome = Outcome(
        state.copy(
            wasWatching = false,
            lastTick = clock,
            pillSinceUp = null,
            cardShownUp = null,
            cardVisible = false,
        ),
        Ui.NONE,
    )

    private fun watching(before: State, settings: Settings, clock: Clock, screen: String, busy: Boolean): Outcome {
        var s = before
        if (!s.wasWatching) {
            val last = s.lastWatch
            if (last != null && awayMs(last, clock) >= settings.freshAfterMs) s = State(log = s.log, seq = s.seq)
            s = s.copy(enteredUp = clock.upMs, pillSinceUp = null, cardShownUp = null, cardVisible = false)
        } else if (!s.cardVisible) {
            // A card covering the video means nobody is watching it, so that time is not counted.
            val last = s.lastTick
            if (last != null && last.boot == clock.boot) {
                val gained = (clock.upMs - last.upMs).coerceIn(0, MAX_GAP_MS) * settings.speed.coerceAtLeast(1)
                s = s.copy(watchedMs = s.watchedMs + gained, log = s.log.add(clock.day(), screen, gained))
            }
        }
        s = s.copy(wasWatching = true, lastTick = clock, lastWatch = clock)

        val settled = clock.upMs - s.enteredUp >= SETTLE_MS

        // While the agent works the video stays covered, whatever the count says. When it is done the
        // card goes, and the ladder carries on from where it was.
        if (busy) {
            if (s.card !is Card.Busy) {
                val card = Card.Busy(s.seq + 1, (s.watchedMs / 60_000L).toInt())
                s = s.copy(card = card, seq = card.id, cardShownUp = null)
            }
            s = s.copy(cardVisible = settled, pillSinceUp = null)
            return Outcome(s, draw(s, settings))
        }
        if (s.card is Card.Busy) s = s.copy(card = null, cardVisible = false, cardShownUp = null)

        val rung = rungFor(s, settings)

        if (rung?.kind == Kind.PILL && !s.pillDone) s = s.copy(pillDone = true, pillSinceUp = clock.upMs)
        if (s.pillSinceUp?.let { clock.upMs - it >= PILL_MS } == true) s = s.copy(pillSinceUp = null)

        if (s.card == null && settled && rung != null) {
            dueCard(s, rung)?.let { s = s.copy(card = it, seq = it.id) }
        }

        var goHome = false
        if (s.card is Card.Farewell && settled) {
            val shown = s.cardShownUp
            if (shown == null) {
                s = s.copy(cardShownUp = clock.upMs)
            } else if (clock.upMs - shown >= FAREWELL_MS) {
                goHome = true
                s = s.copy(card = null, cardShownUp = null, lastHomeAtMs = s.watchedMs)
            }
        }

        s = s.copy(cardVisible = s.card != null && settled)
        return Outcome(s, draw(s, settings), goHome)
    }

    private fun closed(state: State, settings: Settings, clock: Clock): Outcome {
        val card = state.card
        if (card == null || card is Card.Farewell || card is Card.Busy || !state.cardVisible) {
            return Outcome(state, draw(state, settings))
        }
        val nextHoldAt = when (card) {
            is Card.Tap -> maxOf(state.nextHoldAtMs, state.watchedMs + BREATHER_MS)
            else -> state.watchedMs + (rungFor(state, settings)?.everyMs?.takeIf { it > 0 } ?: BREATHER_MS)
        }
        val s = state.copy(
            card = null,
            cardVisible = false,
            cardShownUp = null,
            tapDone = state.tapDone || card is Card.Tap,
            nextHoldAtMs = nextHoldAt,
            lastTick = clock,
        )
        return Outcome(s, draw(s, settings))
    }

    private fun rungFor(s: State, settings: Settings): Rung? =
        Ladder.rungAt(s.watchedMs - settings.limitMs, settings.sendHome)

    private fun dueCard(s: State, rung: Rung): Card? {
        val id = s.seq + 1
        val minutes = (s.watchedMs / 60_000L).toInt()
        val over = (rung.overMs / 60_000L).toInt()
        return when (rung.kind) {
            Kind.PILL -> null
            Kind.TAP -> Card.Tap(id, minutes, over).takeIf { !s.tapDone }
            Kind.HOLD -> Card.Hold(id, minutes, over, rung.holdMs, rung.quiet).takeIf { s.watchedMs >= s.nextHoldAtMs }
            Kind.HOME -> Card.Farewell(id, minutes, over).takeIf {
                val last = s.lastHomeAtMs
                last == null || s.watchedMs - last >= BREATHER_MS
            }
        }
    }

    private fun draw(s: State, settings: Settings): Ui {
        if (!s.wasWatching) return Ui.NONE
        val visible = s.card.takeIf { s.cardVisible }
        return Ui(
            pillMinutes = s.pillSinceUp?.let { (settings.limitMs / 60_000L).toInt() },
            card = visible,
            dim = if (s.card is Card.Busy) 0f else rungFor(s, settings)?.dim ?: 0f,
            quiet = (visible as? Card.Hold)?.quiet == true || visible is Card.Busy,
        )
    }
}
