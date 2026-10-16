package io.github.mendypf.nomoredoom

import android.content.Context
import android.content.SharedPreferences
import io.github.mendypf.nomoredoom.core.State
import io.github.mendypf.nomoredoom.core.StateCodec

/** The few things kept on the phone: the agent's topic, today's minutes, and a rules or memes file if one was pushed. */
internal class Store(context: Context) {
    private val prefs: SharedPreferences =
        context.applicationContext.getSharedPreferences("nomoredoom", Context.MODE_PRIVATE)

    init {
        // The timer cards and their settings are gone; so is what an older version kept for them.
        if (OLD_KEYS.any(prefs::contains)) prefs.edit().apply { OLD_KEYS.forEach(::remove) }.apply()
    }

    /** A rules file pushed from a computer; null means the one built into the app. */
    var rulesText: String?
        get() = prefs.getString(RULES, null)
        set(value) = prefs.edit().putString(RULES, value).apply()

    /** A memes file pushed from a computer; null means the one built into the app. */
    var gagsText: String?
        get() = prefs.getString(GAGS, null)
        set(value) = prefs.edit().putString(GAGS, value).apply()

    /** The ntfy topic the person's coding agent posts to; null means not linked, and the app never goes online. */
    var agentUrl: String?
        get() = prefs.getString(AGENT, null)
        set(value) = prefs.edit().putString(AGENT, value).apply()

    fun loadState(): State = prefs.getString(STATE, null)?.let(StateCodec::decode) ?: State()

    fun saveState(state: State) = prefs.edit().putString(STATE, StateCodec.encode(state)).apply()

    private companion object {
        val OLD_KEYS = listOf("limit_minutes", "fresh_minutes", "send_home", "popups")
        private const val RULES = "rules"
        private const val GAGS = "gags"
        private const val STATE = "state"
        private const val AGENT = "agent_url"
    }
}
