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

        if (extras.containsKey("limit")) {
            store.limitMinutes = extras.getInt("limit")
            done += "limit=${store.limitMinutes}"
        }
        if (extras.containsKey("fresh")) {
            store.freshMinutes = extras.getInt("fresh")
            done += "fresh=${store.freshMinutes}"
        }
        if (extras.containsKey("home")) {
            store.sendHome = extras.getBoolean("home")
            done += "home=${store.sendHome}"
        }
        if (extras.containsKey("popups")) {
            store.popups = extras.getBoolean("popups")
            done += "popups=${store.popups}"
        }
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
            val folder = requireNotNull(Art(context).folder) { "GIFs need Android 11 or newer; the lines work without them" }
            done += "gags_dir=${folder.absolutePath}"
        }

        // The rest only makes sense while the service is running.
        val wantsService = TEST_KEYS.any(extras::containsKey)
        val service = Live.service
        if (wantsService) {
            requireNotNull(service) { "the service is off" }
            if (extras.containsKey("speed")) {
                val speed = extras.getInt("speed")
                require(speed in 1..600) { "speed must be 1 to 600" }
                service.speed = speed
                done += "speed=$speed"
            }
            if (extras.containsKey("skip_s")) {
                val seconds = extras.getInt("skip_s")
                require(seconds in 0..86_400) { "skip_s must be 0 to 86400" }
                service.skipTo(seconds)
                done += "skip_s=$seconds"
            }
            if (extras.getBoolean("reset")) {
                service.resetSitting()
                done += "reset"
            }
            if (extras.getBoolean("demo")) {
                require(service.startDemo()) { "the phone is locked or its notification shade is down" }
                done += "demo"
            }
        }
        require(done.isNotEmpty()) {
            "nothing to do: pass limit, fresh, home, popups, rules_b64, gags_b64, gags_dir, agent_url, speed, skip_s, reset or demo"
        }
        return done.joinToString(" ")
    }

    private companion object {
        const val ACTION = "io.github.mendypf.nomoredoom.SET"
        const val OK = 1
        const val REFUSED = 2
        val TEST_KEYS = listOf("speed", "skip_s", "reset", "demo")
    }
}
