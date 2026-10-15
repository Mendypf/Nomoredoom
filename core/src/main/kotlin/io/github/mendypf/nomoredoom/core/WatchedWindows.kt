package io.github.mendypf.nomoredoom.core

/**
 * The windows Android has reported a change in.
 *
 * Android reports changes from the watched apps and from no other, so a window in this list
 * belongs to one of them. A window that is not in it is never opened.
 *
 * @param keep how many to remember. Window numbers are not reused, so old ones only take up room.
 */
public class WatchedWindows(private val keep: Int = 16) {
    private val latest = LinkedHashSet<Int>()

    /**
     * @param windowId the window a change came from; negative when Android named none
     * @return true when this window was not known yet
     */
    public fun saw(windowId: Int): Boolean {
        if (windowId < 0) return false
        val known = latest.remove(windowId)
        latest.add(windowId)
        if (latest.size > keep) latest.remove(latest.first())
        return !known
    }

    /** A copy, safe to hand to another thread. */
    public fun ids(): Set<Int> = latest.toSet()
}
