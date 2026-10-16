package io.github.mendypf.nomoredoom

import android.accessibilityservice.AccessibilityService
import android.graphics.Point
import android.graphics.Rect
import android.os.Build
import android.os.SystemClock
import android.view.WindowManager
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import io.github.mendypf.nomoredoom.core.Box
import io.github.mendypf.nomoredoom.core.Rule
import io.github.mendypf.nomoredoom.core.Rulebook
import io.github.mendypf.nomoredoom.core.SafeArea
import io.github.mendypf.nomoredoom.core.Screen
import java.io.PrintWriter

/** What one look at the screen found. */
internal sealed interface Seen {
    /** A short-video screen is showing. */
    data class Short(val rule: Rule) : Seen

    /**
     * The lock screen, the notification shade or a system menu is over the screen.
     * Whatever is under it, nobody is watching it, and the memes must not sit on top.
     */
    data object Covered : Seen

    /** Something else is showing. */
    data object Other : Seen

    /** Android had no answer (a screen was mid-change). Not the same as [Other]. */
    data object Unknown : Seen
}

/** @param safe the part of the screen no system bar or banner is using; the memes stay inside it. Null when [Seen.Covered]. */
internal data class Look(val seen: Seen, val safe: Box?)

/**
 * The only code that looks at anything outside this app.
 *
 * Per look it reads three things and nothing else:
 *  - the list of windows on screen: what kind each is, its outline and its number. Not what is in it.
 *  - for a window that a watched app reported a change in: where a few named views are (never their
 *    text). No other window is opened. Android only reports changes from the watched apps, so the
 *    window of any other app is never in that list.
 *  - the outlines of Android's own windows (status bar, button bar, a call banner), to stay clear of them.
 *
 * [printNamedViews] is the one place that walks a watched app's layout, and it
 * only runs when someone asks for it over adb to repair the rules.
 */
internal object Looker {

    /** @param watched the windows a watched app reported a change in; see [io.github.mendypf.nomoredoom.core.WatchedWindows] */
    fun look(service: AccessibilityService, rulebook: Rulebook, watched: Set<Int>): Look {
        val all = service.windows
        val safe = SafeArea.within(displayBounds(service), outlines(all, AccessibilityWindowInfo.TYPE_SYSTEM))
            ?: return Look(Seen.Covered, null)
        return Look(shortVideo(all, rulebook, watched), safe)
    }

    /** Null when the system is using most of the screen. */
    fun safeArea(service: AccessibilityService): Box? =
        SafeArea.within(displayBounds(service), outlines(service.windows, AccessibilityWindowInfo.TYPE_SYSTEM))

    private fun shortVideo(all: List<AccessibilityWindowInfo>, rulebook: Rulebook, watched: Set<Int>): Seen {
        val appWindows = all.filter { it.type == AccessibilityWindowInfo.TYPE_APPLICATION }
        if (appWindows.isEmpty()) return Seen.Unknown
        var unsure = false
        for (window in appWindows) {
            if (window.id !in watched) continue // another app's window: it stays closed
            val root = window.root
            val pkg = root?.packageName?.toString()
            if (root == null || pkg == null) {
                unsure = true
                continue
            }
            if (rulebook.ruleFor(pkg) == null) continue
            val bounds = Rect().also(window::getBoundsInScreen)
            rulebook.match(NodeScreen(root, pkg, bounds.height()))?.let { return Seen.Short(it) }
        }
        return if (unsure) Seen.Unknown else Seen.Other
    }

    /**
     * For fixing the rules after an app update: every named view showing in the
     * watched app in front. Names, sizes and types only, never what the views say.
     */
    fun printNamedViews(service: AccessibilityService, rulebook: Rulebook, watched: Set<Int>, out: PrintWriter) {
        // This runs on the thread that draws the memes, and a slow app answers each question slowly.
        val giveUpAt = SystemClock.uptimeMillis() + TREE_BUDGET_MS
        for (window in service.windows.filter { it.type == AccessibilityWindowInfo.TYPE_APPLICATION && it.id in watched }) {
            val root = window.root ?: continue
            val pkg = root.packageName?.toString() ?: continue
            if (rulebook.ruleFor(pkg) == null) continue
            val windowHeight = Rect().also(window::getBoundsInScreen).height().coerceAtLeast(1)
            out.println("tree $pkg window_height=$windowHeight")

            val rect = Rect()
            val printed = HashSet<String>()
            val stack = ArrayDeque<Pair<AccessibilityNodeInfo, Int>>().apply { add(root to 0) }
            var walked = 0
            while (stack.isNotEmpty() && walked < MAX_NODES && printed.size < MAX_LINES) {
                if (SystemClock.uptimeMillis() > giveUpAt) {
                    out.println("tree cut short after $walked views: the app was slow to answer")
                    return
                }
                val (node, depth) = stack.removeLast()
                walked++
                node.getBoundsInScreen(rect)
                val id = node.viewIdResourceName?.substringAfter(":id/")
                if (id != null && node.isVisibleToUser && rect.width() > 0 && rect.height() > 0) {
                    val type = node.className?.toString()?.substringAfterLast('.')
                    val line = "  id=$id height=${rect.height() * 100 / windowHeight}% px=${rect.width()}x${rect.height()} class=$type"
                    if (printed.add(line)) out.println(line)
                }
                if (depth < MAX_DEPTH) {
                    for (i in node.childCount - 1 downTo 0) node.getChild(i)?.let { stack.add(it to depth + 1) }
                }
            }
        }
    }

    private fun outlines(all: List<AccessibilityWindowInfo>, type: Int): List<Box> {
        val rect = Rect()
        return all.filter { it.type == type }.map {
            it.getBoundsInScreen(rect)
            Box(rect.left, rect.top, rect.right, rect.bottom)
        }
    }

    private fun displayBounds(service: AccessibilityService): Box {
        val manager = service.getSystemService(WindowManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val bounds = manager.maximumWindowMetrics.bounds
            return Box(bounds.left, bounds.top, bounds.right, bounds.bottom)
        }
        val size = Point()
        @Suppress("DEPRECATION")
        manager.defaultDisplay.getRealSize(size)
        return Box(0, 0, size.x, size.y)
    }

    private const val MAX_NODES = 4_000
    private const val MAX_LINES = 400
    private const val MAX_DEPTH = 80
    private const val TREE_BUDGET_MS = 1_500L
}

private class NodeScreen(
    private val root: AccessibilityNodeInfo,
    override val pkg: String,
    override val height: Int,
) : Screen {
    /** Every copy with this name that Android says is showing. Hidden copies are common; see Rulebook.match. */
    override fun find(viewId: String): List<Box> {
        val rect = Rect()
        return root.findAccessibilityNodeInfosByViewId("$pkg:id/$viewId").orEmpty()
            .filter { it.isVisibleToUser }
            .map {
                it.getBoundsInScreen(rect)
                Box(rect.left, rect.top, rect.right, rect.bottom)
            }
    }
}
