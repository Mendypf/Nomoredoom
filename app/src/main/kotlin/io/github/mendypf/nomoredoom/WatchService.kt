package io.github.mendypf.nomoredoom

import android.accessibilityservice.AccessibilityService
import android.annotation.SuppressLint
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import io.github.mendypf.nomoredoom.core.Box
import io.github.mendypf.nomoredoom.core.Card
import io.github.mendypf.nomoredoom.core.Clock
import io.github.mendypf.nomoredoom.core.Engine
import io.github.mendypf.nomoredoom.core.Event
import io.github.mendypf.nomoredoom.core.Gags
import io.github.mendypf.nomoredoom.core.Pace
import io.github.mendypf.nomoredoom.core.Rulebook
import io.github.mendypf.nomoredoom.core.State
import io.github.mendypf.nomoredoom.core.StateCodec
import io.github.mendypf.nomoredoom.core.Ui
import io.github.mendypf.nomoredoom.core.WatchedWindows
import io.github.mendypf.nomoredoom.core.day
import java.io.FileDescriptor
import java.io.PrintWriter
import java.util.TimeZone

/**
 * The part Android runs. It does three things and decides nothing itself:
 *
 *  1. Android says "one of the three apps changed its screen" -> take a look, at that app's
 *     window and no other.
 *  2. While a short-video screen is in front, look again once a second and tell the engine.
 *     When none is in front there is no timer: a look happens only when one of the three apps
 *     changes its layout, and less often the longer those looks find nothing (see [Pace]).
 *  3. Draw what the engine says should be on screen.
 *
 * Looking happens on a side thread, because it asks the other app a question
 * and a frozen app must not freeze the popups.
 */
class WatchService : AccessibilityService() {

    private val main = Handler(Looper.getMainLooper())
    private var lookThread: HandlerThread? = null
    private var looker: Handler? = null

    private lateinit var store: Store
    private lateinit var stage: Stage
    private lateinit var art: Art
    private lateinit var agent: AgentLink
    private lateinit var power: PowerManager

    @Volatile
    private var rulebook: Rulebook? = null

    private var state = State()
    private var connected = false
    private var lookPending = false

    /** A look that has been asked for and has not started. Only one of those can still be brought forward. */
    private var lookWaiting = false
    private var lookDueAt = 0L
    private var lastLookAt = 0L

    /** How many looks in a row found no short video. */
    private var misses = 0

    /** A look was asked for while one was running. That one may have seen the screen before it changed. */
    private var lookAgain = false
    private var dropped = 0
    private val watched = WatchedWindows()
    private var lastSaveAt = 0L
    private var lastSaved = ""
    private var unknowns = 0
    private var boot = 0
    private var front: String? = null
    private var covered = false
    private var safe = Box(0, 0, 0, 0)
    private var demo = Demo.OFF

    /** The demo's one clock: every step moves on by itself, so a demo nobody touches still ends. */
    private val demoTimer = Runnable { demoNext() }

    /** Test hook: every watched second counts this many times. Never saved. */
    internal var speed = 1

    internal val snapshot: State get() = state

    private val screenReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            when (intent.action) {
                Intent.ACTION_SCREEN_OFF -> apply(Event.Tick(now(), null))
                Intent.ACTION_USER_PRESENT -> lookIn(LOOK_DELAY_MS)
            }
        }
    }

    private val startLook = Runnable {
        lookWaiting = false
        val book = rulebook
        val side = looker
        if (!connected || book == null || side == null) {
            lookPending = false
            return@Runnable
        }
        lastLookAt = SystemClock.uptimeMillis()
        val windows = watched.ids()
        side.post {
            val look = runCatching {
                if (power.isInteractive) Looker.look(this, book, windows) else null
            }.getOrElse {
                Log.w(TAG, "could not look at the screen", it)
                Look(Seen.Unknown, null)
            }
            main.post { looked(look) }
        }
    }

    override fun onServiceConnected() {
        store = Store(this)
        power = getSystemService(PowerManager::class.java)
        boot = Settings.Global.getInt(contentResolver, Settings.Global.BOOT_COUNT, 0)
        state = store.loadState()
        lastSaved = StateCodec.encode(state)
        rulebook = loadRulebook()
        art = Art(this)
        agent = AgentLink(store)
        stage = Stage(this, art, onClosed = ::onClosed)
        stage.gags = loadGags()
        lookThread = HandlerThread("nomoredoom-look").also {
            it.start()
            looker = Handler(it.looper)
        }
        val filter = IntentFilter(Intent.ACTION_SCREEN_OFF).apply { addAction(Intent.ACTION_USER_PRESENT) }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(screenReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            registerReceiver(screenReceiver, filter)
        }
        connected = true
        Live.attach(this)
        lookIn(LOOK_DELAY_MS)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        if (!connected) return
        // Android sends changes from the watched apps and from no other, so this window is one of theirs.
        val newWindow = watched.saw(event.windowId)
        if (state.wasWatching) return // while watching, the once-a-second look is already running
        val newScreen = newWindow || event.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED
        if (!newScreen && !reshapes(event)) return
        if (newScreen) misses = 0
        val gap = Pace.gapMs(misses)
        val sinceLast = SystemClock.uptimeMillis() - lastLookAt
        lookIn((gap - sinceLast).coerceIn(LOOK_DELAY_MS, gap))
    }

    override fun onInterrupt() = Unit

    override fun onUnbind(intent: Intent?): Boolean {
        shutDown()
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        shutDown()
        super.onDestroy()
    }

    /** `adb shell dumpsys activity service io.github.mendypf.nomoredoom/.WatchService [tree]` */
    override fun dump(fd: FileDescriptor?, writer: PrintWriter, args: Array<out String>?) {
        if (!connected) {
            writer.println("connected=false")
            return
        }
        val clock = now()
        val settings = store.settings(speed)
        writer.println("version=${BuildConfig.VERSION_NAME}")
        writer.println("connected=true")
        writer.println("watching=${front ?: "-"}")
        writer.println("covered=$covered")
        writer.println("safe=${safe.left},${safe.top},${safe.right},${safe.bottom}")
        writer.println("sitting_s=${state.sittingMs(clock, settings) / 1000}")
        writer.println("limit_min=${store.limitMinutes}")
        writer.println("fresh_min=${store.freshMinutes}")
        writer.println("send_home=${store.sendHome}")
        writer.println("popups=${store.popups}")
        writer.println("speed=$speed")
        writer.println("card=" + when (state.card) {
            null -> "-"
            is Card.Tap -> "tap"
            is Card.Hold -> "hold"
            is Card.Farewell -> "farewell"
            is Card.Busy -> "agent"
        })
        writer.println("agent=" + if (agent.linked) "linked" else "-")
        writer.println("agent_busy=" + if (agent.busy()) "yes" else "no")
        writer.println("agent_check=${agent.lastCheck}")
        writer.println("agent_answer_s=${agent.answeredSecondsAgo() ?: "-"}")
        writer.println("drawn=${stage.onScreen()}")
        stage.report().forEach { (name, value) -> writer.println("$name=$value") }
        writer.println("gags=" + if (store.gagsText != null) "pushed" else "built-in")
        writer.println("gifs_on_phone=${art.count()}")
        writer.println("today_s=" + state.log.byName(clock.day()).entries.joinToString(",") { "${it.key}:${it.value / 1000}" })
        writer.println("rules=" + if (store.rulesText != null) "pushed" else "built-in")
        writer.println("packages=" + serviceInfo?.packageNames?.joinToString(",").orEmpty())
        writer.println("look=" + (if (lookWaiting) "waiting" else if (lookPending) "running" else "idle") +
            " misses=$misses dropped=$dropped since_look_ms=${SystemClock.uptimeMillis() - lastLookAt}")
        if (args?.contains("tree") == true) rulebook?.let { Looker.printNamedViews(this, it, watched.ids(), writer) }
    }

    // ---- commands from the app's own screen and from adb ----

    internal fun reloadRules() {
        rulebook = loadRulebook()
    }

    internal fun reloadGags() {
        stage.gags = loadGags()
    }

    /** A computer linked or unlinked the agent's topic. The next look at a short video asks the new one. */
    internal fun agentChanged() {
        agent.forget()
    }

    internal fun skipTo(seconds: Int) {
        endDemo()
        state = state.skippedTo(seconds * 1000L)
        save(force = true)
        draw(Ui.NONE)
        lookIn(LOOK_DELAY_MS)
    }

    internal fun resetSitting() {
        endDemo()
        state = State(log = state.log)
        save(force = true)
        draw(Ui.NONE)
        lookIn(LOOK_DELAY_MS)
    }

    /**
     * Plays the popups once, in order, without counting or changing anything. The real popups
     * wait while it plays. False when the lock screen or the notification shade is up and there
     * is nowhere to draw.
     */
    internal fun startDemo(): Boolean {
        safe = Looker.safeArea(this) ?: return false
        demo = Demo.OFF
        demoNext()
        return true
    }

    // ---- the loop ----

    /** Asks for a look. One that is still waiting its turn moves forward when this one is due clearly sooner. */
    private fun lookIn(delayMs: Long) {
        if (!connected) return
        val due = SystemClock.uptimeMillis() + delayMs
        if (lookPending) {
            if (!lookWaiting) {
                dropped++
                lookAgain = true
                return
            }
            if (due + SOONER_MS > lookDueAt) return
            main.removeCallbacks(startLook)
        }
        lookPending = true
        lookWaiting = true
        lookDueAt = due
        main.postDelayed(startLook, delayMs)
    }

    /** @param look what the side thread saw; null when the screen is off */
    private fun looked(look: Look?) {
        lookPending = false
        val again = lookAgain
        lookAgain = false
        if (!connected) return
        val seen = look?.seen ?: Seen.Other
        if (seen is Seen.Unknown && ++unknowns <= MAX_UNKNOWNS) {
            // Mid-transition Android sometimes has no answer yet. Ask again rather than guess.
            lookIn(RETRY_MS)
            return
        }
        unknowns = 0
        look?.safe?.let { safe = it }
        // The shade or the lock screen came down over a video: nothing is counted or drawn, but
        // keep looking, because Android sends no event when it lifts off the video again.
        covered = seen is Seen.Covered && (front != null || covered)
        front = (seen as? Seen.Short)?.rule?.name
        misses = if (front != null) 0 else minOf(misses + 1, MAX_MISSES)
        // Only with a short video in front does the app ask whether the agent is working.
        if (front != null) agent.refresh(arriving = !state.wasWatching, onChange = ::agentFlipped)
        apply(Event.Tick(now(), front, busy = front != null && agent.busy()))
        if (state.wasWatching || covered) lookIn(BEAT_MS)
        // The screen changed while this look was out, and what it saw may be from before: look once more.
        else if (again) lookIn(LOOK_DELAY_MS)
    }

    private fun draw(ui: Ui) = stage.show(ui, safe)

    /** The agent started or finished while a short video is in front: act on it now, not at the next look. */
    private fun agentFlipped() {
        if (connected && front != null && !covered) apply(Event.Tick(now(), front, busy = agent.busy()))
    }

    private fun onClosed() {
        if (demo != Demo.OFF) return demoNext()
        apply(Event.Closed(now()))
    }

    private fun apply(event: Event) {
        val before = state
        val out = Engine.step(before, store.settings(speed), event)
        state = out.state
        if (demo == Demo.OFF) draw(if (out.goHome) Ui.NONE else out.ui)
        if (out.goHome) performGlobalAction(GLOBAL_ACTION_HOME)
        save(force = before.wasWatching != state.wasWatching || out.goHome)
        Live.changed()
    }

    private fun save(force: Boolean) {
        val now = SystemClock.uptimeMillis()
        if (!force && now - lastSaveAt < SAVE_EVERY_MS) return
        lastSaveAt = now
        val line = StateCodec.encode(state)
        if (line == lastSaved) return
        lastSaved = line
        store.saveState(state)
    }

    /** On to the demo's next step. A card's X asks for it; [demoTimer] does when nobody presses one. */
    private fun demoNext() {
        main.removeCallbacks(demoTimer)
        val limit = store.limitMinutes
        val next = demo.next() ?: return endDemo()
        demo = next
        draw(
            when (next) {
                Demo.NOTE -> Ui(pillMinutes = limit)
                Demo.TAP -> Ui(card = Card.Tap(DEMO_ID, limit + 1, over = 1))
                Demo.HOLD -> Ui(card = Card.Hold(DEMO_ID - 1, limit + 6, over = 6, holdMs = DEMO_HOLD_MS, quiet = false), dim = 0.3f)
                Demo.FAREWELL -> Ui(card = Card.Farewell(DEMO_ID - 2, limit + 10, over = 10), dim = 0.6f)
                Demo.OFF -> Ui.NONE
            },
        )
        main.postDelayed(demoTimer, next.staysMs)
    }

    /** Also called when the app's own screen goes away, so a demo card is never left over another app. */
    internal fun endDemo() {
        if (demo == Demo.OFF) return
        main.removeCallbacks(demoTimer)
        demo = Demo.OFF
        draw(Ui.NONE)
    }

    private fun shutDown() {
        if (!connected) return
        connected = false
        Live.detach(this)
        main.removeCallbacksAndMessages(null)
        lookPending = false
        lookWaiting = false
        lookAgain = false
        demo = Demo.OFF
        unregisterReceiver(screenReceiver)
        lookThread?.quitSafely()
        lookThread = null
        looker = null
        state = Engine.step(state, store.settings(speed), Event.Tick(now(), null)).state
        save(force = true)
        stage.clear()
        art.close()
        agent.close()
    }

    private fun loadRulebook(): Rulebook {
        val allowed = serviceInfo?.packageNames?.toSet().orEmpty()
        store.rulesText?.let { pushed ->
            runCatching { Rulebook.parse(pushed, allowed) }
                .onSuccess { return it }
                .onFailure { Log.w(TAG, "the pushed rules do not parse, using the built-in ones", it) }
        }
        val builtIn = resources.openRawResource(R.raw.rules).bufferedReader().use { it.readText() }
        return Rulebook.parse(builtIn, allowed)
    }

    private fun loadGags(): Gags {
        store.gagsText?.let { pushed ->
            runCatching { Gags.parse(pushed) }
                .onSuccess { return it }
                .onFailure { Log.w(TAG, "the pushed jokes do not parse, using the built-in ones", it) }
        }
        val builtIn = resources.openRawResource(R.raw.gags).bufferedReader().use { it.readText() }
        return Gags.parse(builtIn)
    }

    private fun now(): Clock {
        val wall = System.currentTimeMillis()
        return Clock(
            wallMs = wall,
            upMs = SystemClock.elapsedRealtime(),
            boot = boot,
            utcOffsetMs = TimeZone.getDefault().getOffset(wall),
        )
    }

    /** True when the screen's layout changed, false when only a label did (a progress time, a like count). */
    @SuppressLint("InlinedApi")
    private fun reshapes(event: AccessibilityEvent): Boolean {
        val types = event.contentChangeTypes
        return types == AccessibilityEvent.CONTENT_CHANGE_TYPE_UNDEFINED || types and LAYOUT_CHANGES != 0
    }

    /** The demo's steps in order, and how long each stays up when nobody closes it. */
    private enum class Demo(val staysMs: Long) {
        OFF(0),
        NOTE(DEMO_PILL_MS),
        TAP(DEMO_CARD_MS),
        HOLD(DEMO_HOLD_MS + DEMO_CARD_MS),
        FAREWELL(DEMO_FAREWELL_MS);

        fun next(): Demo? = entries.getOrNull(ordinal + 1)
    }

    internal companion object {
        private const val TAG = "Nomoredoom"

        /** While watching: one look a second. */
        private const val BEAT_MS = 1_000L

        /** After an event: wait for the screen to settle. How long to leave between looks is [Pace]'s call. */
        private const val LOOK_DELAY_MS = 150L
        private const val SOONER_MS = 50L
        private const val MAX_MISSES = 1_000
        private const val RETRY_MS = 300L
        private const val MAX_UNKNOWNS = 3
        private const val SAVE_EVERY_MS = 5_000L

        private const val DEMO_ID = -1
        private const val DEMO_PILL_MS = 3_000L
        private const val DEMO_CARD_MS = 6_000L
        private const val DEMO_HOLD_MS = 3_000L
        private const val DEMO_FAREWELL_MS = 2_500L

        @SuppressLint("InlinedApi")
        private const val LAYOUT_CHANGES = AccessibilityEvent.CONTENT_CHANGE_TYPE_SUBTREE or
            AccessibilityEvent.CONTENT_CHANGE_TYPE_PANE_APPEARED or
            AccessibilityEvent.CONTENT_CHANGE_TYPE_PANE_DISAPPEARED or
            AccessibilityEvent.CONTENT_CHANGE_TYPE_PANE_TITLE
    }
}
