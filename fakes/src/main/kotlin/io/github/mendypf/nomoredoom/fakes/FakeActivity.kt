package io.github.mendypf.nomoredoom.fakes

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.widget.FrameLayout
import android.widget.TextView

/**
 * One screen that can look, to an accessibility service, like three states of a real app:
 *
 *     adb shell am start -n <package>/io.github.mendypf.nomoredoom.fakes.FakeActivity --es screen short
 *
 *  - home:  the app is open, nothing to do with short videos. Like the real Instagram, it still
 *           carries a hidden copy of the player, folded to zero width.
 *  - short: the short-video player is showing, at the size the real app gives it
 *           (YouTube: the whole screen; Instagram: a thin strip, as when a reel is opened from a message)
 *  - small: a view with the player's name is showing but small (a shelf of thumbnails)
 *
 * Add `--ez tappable true` and a touch anywhere on the screen sends it back to
 * home, which is how the test can tell whether a touch got through a popup.
 */
class FakeActivity : Activity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        show(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        show(intent)
    }

    private fun show(intent: Intent) {
        val screen = intent.getStringExtra("screen") ?: "home"
        val short = screen == "short"
        val root = FrameLayout(this).apply {
            setBackgroundColor(if (short) Color.rgb(28, 36, 64) else Color.WHITE)
        }
        root.addView(
            TextView(this).apply {
                text = "${BuildConfig.FLAVOR}\n$screen"
                textSize = 30f
                gravity = Gravity.CENTER
                setTextColor(if (short) Color.WHITE else Color.BLACK)
            },
            FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT),
        )

        val playerId = resources.getIdentifier(BuildConfig.SHORT_VIEW_ID, "id", packageName)
        if (playerId != 0) {
            val player = View(this).apply { id = playerId }
            when (screen) {
                "short" -> root.addView(
                    player,
                    FrameLayout.LayoutParams(MATCH_PARENT, BuildConfig.SHORT_VIEW_HEIGHT, Gravity.BOTTOM).apply {
                        // a thin player sits above the app's own tab bar, clear of the phone's button bar
                        if (height > 0) bottomMargin = 320
                    },
                )
                "small" -> root.addView(player, FrameLayout.LayoutParams(MATCH_PARENT, 400))
                else -> root.addView(
                    player.apply { visibility = View.INVISIBLE },
                    FrameLayout.LayoutParams(0, MATCH_PARENT),
                )
            }
        }
        if (intent.getBooleanExtra("tappable", false)) {
            root.setOnClickListener { show(Intent().putExtra("screen", "home")) }
        }
        setContentView(root)
    }
}
