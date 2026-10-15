package io.github.mendypf.nomoredoom.core

/** A rectangle on screen, in pixels. */
public data class Box(val left: Int, val top: Int, val right: Int, val bottom: Int) {
    val width: Int get() = right - left
    val height: Int get() = bottom - top
}

/** One app window, as much of it as a rule is allowed to ask about. */
public interface Screen {
    public val pkg: String

    /** Height of the app's window in pixels (half the phone in split screen). */
    public val height: Int

    /** Where the views with this id are, counting only the ones on screen right now. */
    public fun find(viewId: String): List<Box>
}

/** What counts as a short-video screen in one app. */
public data class Rule(
    val pkg: String,
    val name: String,
    val viewIds: List<String>,
    val minHeight: Double = DEFAULT_MIN_HEIGHT,
) {
    val wholeApp: Boolean get() = viewIds.isEmpty()

    public companion object {
        /** No height bar: any showing copy of the view counts. */
        public const val DEFAULT_MIN_HEIGHT: Double = 0.0
    }
}

public class RuleError(public val line: Int, message: String) :
    IllegalArgumentException("line $line: $message")

/**
 * The list of short-video screens, read from a small text file so it can be
 * fixed without rebuilding the app when Instagram or YouTube rename a view:
 *
 *     # package | name | view ids, or * for the whole app | min-height=0.5 (optional)
 *     com.zhiliaoapp.musically   | TikTok | *
 *     com.instagram.android      | Reels  | clips_viewer_view_pager
 *     com.google.android.youtube | Shorts | reel_recycler, reel_watch_player | min-height=0.5
 *
 * A screen matches when one of the listed views is showing. With min-height the
 * view must also be at least that share of the app's window tall.
 */
public class Rulebook private constructor(public val rules: List<Rule>) {
    private val byPackage = rules.associateBy { it.pkg }

    public fun ruleFor(pkg: String): Rule? = byPackage[pkg]

    /** The rule this screen satisfies, or null when it is not a short-video screen. */
    public fun match(screen: Screen): Rule? {
        val rule = byPackage[screen.pkg] ?: return null
        if (rule.wholeApp) return rule
        // Apps keep hidden copies of a view around with a width or height of zero or less,
        // so every copy is checked and one that is really showing is enough.
        val tallEnough = screen.height * rule.minHeight
        val found = rule.viewIds.any { id ->
            screen.find(id).any { it.width > 0 && it.height > 0 && it.height >= tallEnough }
        }
        return rule.takeIf { found }
    }

    public companion object {
        private const val MAX_RULES = 16
        private const val MAX_IDS = 16
        private val VIEW_ID = Regex("[A-Za-z][A-Za-z0-9_]{0,99}")

        // Names end up in the saved log, where , ; : = separate things.
        private val NAME = Regex("[A-Za-z0-9](?:[A-Za-z0-9 ]{0,22}[A-Za-z0-9])?")

        /**
         * @param allowedPackages the apps the service is built to hear from. A rule
         *   for any other app is refused, so a rules file can never widen the list.
         * @throws RuleError with the line number of the first problem
         */
        public fun parse(text: String, allowedPackages: Set<String>): Rulebook {
            val rules = mutableListOf<Rule>()
            // Some Windows editors put an invisible mark in front of a file, and trim() does not remove it.
            text.removePrefix("\uFEFF").lineSequence().forEachIndexed { index, raw ->
                val line = raw.substringBefore('#').trim()
                if (line.isNotEmpty()) rules += parseLine(index + 1, line, allowedPackages, rules)
            }
            if (rules.isEmpty()) throw RuleError(1, "no rules in the file")
            return Rulebook(rules)
        }

        private fun parseLine(n: Int, line: String, allowed: Set<String>, soFar: List<Rule>): Rule {
            val parts = line.split('|').map { it.trim() }
            if (parts.size !in 3..4) throw RuleError(n, "expected: package | name | view ids | min-height=0.5")
            val (pkg, name, ids) = parts
            if (pkg !in allowed) throw RuleError(n, "$pkg is not one of the apps this build listens to")
            if (soFar.any { it.pkg == pkg }) throw RuleError(n, "$pkg already has a rule")
            if (soFar.size >= MAX_RULES) throw RuleError(n, "more than $MAX_RULES rules")
            if (!NAME.matches(name)) throw RuleError(n, "the name must be 1 to 24 letters, digits or spaces")

            val viewIds = if (ids == "*") emptyList() else ids.split(',').map { it.trim() }
            if (viewIds.size > MAX_IDS) throw RuleError(n, "more than $MAX_IDS view ids")
            viewIds.firstOrNull { !VIEW_ID.matches(it) }?.let {
                throw RuleError(n, "'$it' is not a view id (letters, digits, underscore; no package prefix)")
            }

            val minHeight = parts.getOrNull(3)?.let { option ->
                option.removePrefix("min-height=").takeIf { it != option }?.toDoubleOrNull()
                    ?.takeIf { it in 0.0..1.0 }
                    ?: throw RuleError(n, "'$option' should look like min-height=0.5 (0 to 1)")
            } ?: Rule.DEFAULT_MIN_HEIGHT

            return Rule(pkg, name, viewIds, minHeight)
        }
    }
}
