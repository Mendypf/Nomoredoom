package io.github.mendypf.nomoredoom.core

/**
 * Whether the person's coding agent is working right now, read from a ntfy topic.
 *
 * The computer posts one short line per change, `working <tag>` or `done <tag>`, where the
 * tag tells two sessions of the agent apart. The phone asks the topic for the last
 * [WINDOW_MINUTES] minutes only, so a session that stopped posting (the computer went to
 * sleep, the agent crashed) stops counting by itself. While a session works it posts
 * `working` again every few minutes to stay inside that window.
 */
public object AgentSignal {
    /** How far back the phone looks. The computer repeats `working` well inside this. */
    public const val WINDOW_MINUTES: Int = 20

    /** A ntfy topic on a server reached over https: `https://ntfy.sh/some-topic`. */
    private val URL = Regex("""https://[A-Za-z0-9.-]{1,253}(?::\d{1,5})?/[A-Za-z0-9_-]{1,64}""")
    private val LINE = Regex("""(working|done) ([A-Za-z0-9]{1,32})""")

    public fun validUrl(url: String): Boolean = URL.matches(url)

    /** The address the phone asks: the topic's messages of the last [WINDOW_MINUTES] minutes, one per line. */
    public fun pollUrl(url: String): String {
        require(validUrl(url)) { "not a ntfy topic address" }
        return "$url/raw?poll=1&since=${WINDOW_MINUTES}m"
    }

    /** True while any session's last line is `working`. Lines come oldest first; anything else is ignored. */
    public fun busy(raw: String): Boolean {
        val last = HashMap<String, Boolean>()
        raw.lineSequence().forEach { line ->
            LINE.matchEntire(line.trim())?.let { last[it.groupValues[2]] = it.groupValues[1] == "working" }
        }
        return last.values.any { it }
    }
}
