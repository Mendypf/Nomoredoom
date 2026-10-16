package io.github.mendypf.nomoredoom

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.util.Base64
import io.github.mendypf.nomoredoom.core.AgentSignal
import io.github.mendypf.nomoredoom.core.GagError
import io.github.mendypf.nomoredoom.core.Gags
import io.github.mendypf.nomoredoom.core.RuleError
import io.github.mendypf.nomoredoom.core.Rulebook

/**
 * Settings and test commands from a computer:
 *
 *     adb shell am broadcast -n io.github.mendypf.nomoredoom/.AdbReceiver \
 *         -a io.github.mendypf.nomoredoom.SET --ei limit 15
 *
 * The manifest guards this with the DUMP permission. adb has it; an installed
 * app does not, unless someone hands it over with adb. The reply comes back as
 * the broadcast's result: result=1 worked, result=2 was refused, result=0
 * nothing received it.
 */
class AdbReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != ACTION) return
        val reply = try {
            OK to handle(context, intent.extras ?: Bundle.EMPTY)
        } catch (e: IllegalArgumentException) {
            REFUSED to (e.message ?: "refused")
        }
        if (isOrderedBroadcast) {
            resultCode = reply.first
            resultData = reply.second
        }
    }

    private fun handle(context: Context, extras: Bundle): String {
        val store = Store(context)
        val done = mutableListOf<String>()

        if (extras.containsKey("rules_b64")) {
            val encoded = extras.getString("rules_b64").orEmpty()
            if (encoded.isEmpty()) {
                store.rulesText = null
                done += "rules=built-in"
            } else {
                val text = String(Base64.decode(encoded, Base64.DEFAULT), Charsets.UTF_8)
                try {
                    Rulebook.parse(text, watchedPackages(context))
                } catch (e: RuleError) {
                    throw IllegalArgumentException("rules ${e.message}")
                }
                store.rulesText = text
                done += "rules=pushed"
            }
            Live.service?.reloadRules()
        }

        if (extras.containsKey("gags_b64")) {
            val encoded = extras.getString("gags_b64").orEmpty()
            if (encoded.isEmpty()) {
                store.gagsText = null
                done += "gags=built-in"
            } else {
                val text = String(Base64.decode(encoded, Base64.DEFAULT), Charsets.UTF_8)
                try {
                    Gags.parse(text)
                } catch (e: GagError) {
                    throw IllegalArgumentException("gags ${e.message}")
                }
                store.gagsText = text
                done += "gags=pushed"
            }
            Live.service?.reloadGags()
        }
        if (extras.containsKey("agent_url")) {
            val url = extras.getString("agent_url").orEmpty()
            if (url.isEmpty()) {
                store.agentUrl = null
                done += "agent=-"
            } else {
                require(AgentSignal.validUrl(url)) { "agent_url must be https://server/topic" }
                store.agentUrl = url
                done += "agent=linked"
            }
            Live.service?.agentChanged()
        }
        if (extras.getBoolean("gags_dir")) {
            // Where a computer should copy the GIFs. Asking is what creates the folder, owned by this app.
            val folder = requireNotNull(Art(context).folder) { "GIFs need Android 11 or newer" }
            done += "gags_dir=${folder.absolutePath}"
        }
        require(done.isNotEmpty()) { "nothing to do: pass rules_b64, gags_b64, gags_dir or agent_url" }
        return done.joinToString(" ")
    }

    private companion object {
        const val ACTION = "io.github.mendypf.nomoredoom.SET"
        const val OK = 1
        const val REFUSED = 2
    }
}
