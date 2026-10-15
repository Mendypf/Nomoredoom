package io.github.mendypf.nomoredoom

import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import io.github.mendypf.nomoredoom.core.AgentSignal
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/**
 * The one thing this app asks the internet: is the person's coding agent working?
 *
 * It asks only when a computer has linked it to a ntfy topic (`nomoredoom.py agent link`),
 * and only while a short-video screen is in front. The question is a plain GET of the
 * topic's last lines; nothing about the phone or what is on it goes with it. Any answer
 * it cannot read counts as "not working", so a dead network never locks anyone out.
 */
internal class AgentLink(private val store: Store) {
    private val main = Handler(Looper.getMainLooper())
    private var worker: ExecutorService? = null
    private var asking = false
    private var askedAt = 0L

    /** Goes up when the link changes, so an answer about the old topic is thrown away. */
    private var generation = 0
    private var answeredAt = 0L
    private var answer = false

    /** How the last question went: "ok", "failed", or "-" before the first. */
    var lastCheck = "-"
        private set

    val linked: Boolean get() = store.agentUrl != null

    /** Seconds since the last answer, or null before the first one. */
    fun answeredSecondsAgo(): Long? = answeredAt.takeIf { it > 0 }?.let { (SystemClock.elapsedRealtime() - it) / 1000 }

    /** The agent is working, by an answer young enough to trust. */
    fun busy(): Boolean = linked && answer && SystemClock.elapsedRealtime() - answeredAt <= TRUST_MS

    /**
     * Asks again if the last question is older than [EVERY_MS], or than [FRESH_MS] when [arriving]
     * (a short-video screen just came to the front, and the old answer may be from before the
     * agent started). Call it on every look at a short-video screen; [onChange] runs on the main
     * thread when the answer flips.
     */
    fun refresh(arriving: Boolean, onChange: () -> Unit) {
        val url = store.agentUrl ?: return
        val now = SystemClock.elapsedRealtime()
        if (asking || now - askedAt < if (arriving) FRESH_MS else EVERY_MS) return
        asking = true
        askedAt = now
        val asked = generation
        val thread = worker ?: Executors.newSingleThreadExecutor { Thread(it, "nomoredoom-agent") }.also { worker = it }
        thread.execute {
            val said = runCatching { ask(url) }
                .onFailure { Log.w(TAG, "could not ask whether the agent is working", it) }
                .getOrNull()
            main.post {
                if (asked != generation) return@post // the link changed while this was out
                asking = false
                val before = busy()
                lastCheck = if (said != null) "ok" else "failed"
                answer = said ?: false
                answeredAt = SystemClock.elapsedRealtime()
                if (busy() != before) onChange()
            }
        }
    }

    /** Forget the old answer, for when the link changes. */
    fun forget() {
        generation++
        asking = false
        answer = false
        answeredAt = 0
        askedAt = 0
        lastCheck = "-"
    }

    fun close() {
        worker?.shutdownNow()
        worker = null
        asking = false
    }

    private fun ask(url: String): Boolean {
        val connection = URL(AgentSignal.pollUrl(url)).openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = TIMEOUT_MS
            connection.readTimeout = TIMEOUT_MS
            connection.instanceFollowRedirects = false
            connection.useCaches = false
            connection.setRequestProperty("User-Agent", "nomoredoom")
            check(connection.responseCode == 200) { "the topic answered ${connection.responseCode}" }
            val body = connection.inputStream.use { input ->
                val buffer = ByteArray(MAX_BYTES)
                var read = 0
                while (read < MAX_BYTES) {
                    val n = input.read(buffer, read, MAX_BYTES - read)
                    if (n < 0) break
                    read += n
                }
                String(buffer, 0, read, Charsets.UTF_8)
            }
            return AgentSignal.busy(body)
        } finally {
            connection.disconnect()
        }
    }

    private companion object {
        const val TAG = "Nomoredoom"

        /** While a short-video screen is in front: one question every this often. */
        const val EVERY_MS = 10_000L

        /** On arriving at a short video, an answer younger than this is still used. */
        const val FRESH_MS = 2_000L

        /** An answer older than this is not acted on (the screen was away and the agent may have finished). */
        const val TRUST_MS = 25_000L
        const val TIMEOUT_MS = 5_000
        const val MAX_BYTES = 64 * 1024
    }
}
