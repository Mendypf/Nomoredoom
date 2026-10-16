package io.github.mendypf.nomoredoom

import android.accessibilityservice.AccessibilityServiceInfo
import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import android.provider.Settings
import android.view.View
import android.view.WindowInsets
import android.view.accessibility.AccessibilityManager
import android.widget.TextView
import io.github.mendypf.nomoredoom.core.Clock
import io.github.mendypf.nomoredoom.core.day
import java.util.TimeZone

/** The app's one screen: is it on, what it covers, and today's minutes. */
class MainActivity : Activity() {

    private lateinit var store: Store
    private lateinit var status: TextView
    private lateinit var statusDetail: TextView
    private lateinit var offBlock: View
    private lateinit var today: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        store = Store(this)

        status = findViewById(R.id.status)
        statusDetail = findViewById(R.id.status_detail)
        offBlock = findViewById(R.id.off_block)
        today = findViewById(R.id.today)

        keepClearOfSystemBars(findViewById(R.id.root))

        findViewById<View>(R.id.turn_on).setOnClickListener { openAccessibilitySettings() }
        findViewById<View>(R.id.open_app_info).setOnClickListener { openAppInfo() }
    }

    override fun onResume() {
        super.onResume()
        Live.onChange = ::render
        render()
    }

    override fun onPause() {
        Live.onChange = null
        super.onPause()
    }

    private fun render() {
        val on = isSwitchedOn()
        status.setText(if (on) R.string.status_on else R.string.status_off)
        statusDetail.setText(
            when {
                !on -> R.string.status_off_detail
                store.agentUrl != null -> R.string.status_on_linked
                else -> R.string.status_on_unlinked
            },
        )
        offBlock.visibility = if (on) View.GONE else View.VISIBLE

        val clock = now()
        val state = Live.service?.snapshot ?: store.loadState()
        val byName = state.log.byName(clock.day()).filterValues { it > 0 }
        today.text = if (byName.isEmpty()) {
            getString(R.string.today_none)
        } else {
            byName.entries.sortedByDescending { it.value }.joinToString(getString(R.string.today_separator)) {
                val minutes = (it.value / 60_000).toInt()
                if (minutes == 0) getString(R.string.today_item_short, it.key)
                else getString(R.string.today_item, it.key, minutes)
            }
        }
    }

    private fun isSwitchedOn(): Boolean =
        getSystemService(AccessibilityManager::class.java)
            .getEnabledAccessibilityServiceList(AccessibilityServiceInfo.FEEDBACK_ALL_MASK)
            .any { it.resolveInfo.serviceInfo.packageName == packageName }

    private fun openAccessibilitySettings() {
        val row = serviceComponent(this).flattenToString()
        val intent = Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
            // Asks the Settings app to scroll to this app's row and flash it.
            .putExtra(":settings:fragment_args_key", row)
            .putExtra(":settings:show_fragment_args", Bundle().apply { putString(":settings:fragment_args_key", row) })
        open(intent)
    }

    private fun openAppInfo() {
        open(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", packageName, null)))
    }

    private fun open(intent: Intent) {
        try {
            startActivity(intent)
        } catch (_: ActivityNotFoundException) {
            open(Intent(Settings.ACTION_SETTINGS))
        }
    }

    private fun now(): Clock {
        val wall = System.currentTimeMillis()
        return Clock(
            wallMs = wall,
            upMs = SystemClock.elapsedRealtime(),
            boot = Settings.Global.getInt(contentResolver, Settings.Global.BOOT_COUNT, 0),
            utcOffsetMs = TimeZone.getDefault().getOffset(wall),
        )
    }

    /** From Android 15 the app draws behind the status and navigation bars, so pad the content out of them. */
    private fun keepClearOfSystemBars(root: View) {
        root.setOnApplyWindowInsetsListener { view, insets ->
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                val bars = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout())
                view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            } else {
                @Suppress("DEPRECATION")
                view.setPadding(
                    insets.systemWindowInsetLeft,
                    insets.systemWindowInsetTop,
                    insets.systemWindowInsetRight,
                    insets.systemWindowInsetBottom,
                )
            }
            insets
        }
    }
}
