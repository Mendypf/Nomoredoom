package io.github.mendypf.nomoredoom

import android.content.Context
import android.content.SharedPreferences
import io.github.mendypf.nomoredoom.core.Settings
import io.github.mendypf.nomoredoom.core.State
import io.github.mendypf.nomoredoom.core.StateCodec

/** The few things kept on the phone: the settings, the sitting so far, and a rules file if one was pushed. */
internal class Store(context: Context) {
    private val prefs: SharedPreferences =
        context.applicationContext.getSharedPreferences("nomoredoom", Context.MODE_PRIVATE)

    var limitMinutes: Int
        get() = prefs.getInt(LIMIT, 10).coerceIn(LIMIT_RANGE)
        set(value) {
            require(value in LIMIT_RANGE) { "limit must be ${LIMIT_RANGE.first} to ${LIMIT_RANGE.last} minutes" }
            prefs.edit().putInt(LIMIT, value).apply()
        }

    /** Minutes away from short videos after which the count starts again from zero. */
    var freshMinutes: Int
        get() = prefs.getInt(FRESH, 60).coerceIn(FRESH_RANGE)
        set(value) {
            require(value in FRESH_RANGE) { "fresh must be ${FRESH_RANGE.first} to ${FRESH_RANGE.last} minutes" }
            prefs.edit().putInt(FRESH, value).apply()
        }

    var sendHome: Boolean
        get() = prefs.getBoolean(HOME, true)
        set(value) = prefs.edit().putBoolean(HOME, value).apply()

    /** A rules file pushed from a computer; null means the one built into the app. */
    var rulesText: String?
        get() = prefs.getString(RULES, null)
        set(value) = prefs.edit().putString(RULES, value).apply()

    /** A jokes file pushed from a computer; null means the one built into the app. */
    var gagsText: String?
        get() = prefs.getString(GAGS, null)
        set(value) = prefs.edit().putString(GAGS, value).apply()

    /** The ntfy topic the person's coding agent posts to; null means not linked, and the app never goes online. */
    var agentUrl: String?
        get() = prefs.getString(AGENT, null)
        set(value) = prefs.edit().putString(AGENT, value).apply()

    fun settings(speed: Int = 1): Settings = Settings(
        limitMs = limitMinutes * 60_000L,
        freshAfterMs = freshMinutes * 60_000L,
        sendHome = sendHome,
        speed = speed,
    )

    fun loadState(): State = prefs.getString(STATE, null)?.let(StateCodec::decode) ?: State()

    fun saveState(state: State) = prefs.edit().putString(STATE, StateCodec.encode(state)).apply()

    companion object {
        val LIMIT_RANGE = 1..180
        val FRESH_RANGE = 5..720

        /** The steps of the minus and plus buttons. */
        val LIMIT_STEPS = listOf(1, 2, 3, 5, 10, 15, 20, 30, 45, 60, 90, 120, 180)

        private const val LIMIT = "limit_minutes"
        private const val FRESH = "fresh_minutes"
        private const val HOME = "send_home"
        private const val RULES = "rules"
        private const val GAGS = "gags"
        private const val STATE = "state"
        private const val AGENT = "agent_url"
    }
}
