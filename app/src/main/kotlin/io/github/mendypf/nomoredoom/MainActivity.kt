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
import android.widget.Button
import android.widget.ProgressBar
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast
import io.github.mendypf.nomoredoom.core.Clock
import io.github.mendypf.nomoredoom.core.day
import java.util.TimeZone

/** The app's one screen: is it on, how much of the limit is used, and the two settings. */
class MainActivity : Activity() {

    private lateinit var store: Store
    private lateinit var status: TextView
    private lateinit var statusDetail: TextView
    private lateinit var offBlock: View
    private lateinit var sitting: TextView
    private lateinit var sittingBar: ProgressBar
    private lateinit var sittingNote: TextView
    private lateinit var today: TextView
    private lateinit var limit: TextView
    private lateinit var sendHome: Switch
    private lateinit var demo: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        store = Store(this)

        status = findViewById(R.id.status)
        statusDetail = findViewById(R.id.status_detail)
        offBlock = findViewById(R.id.off_block)
        sitting = findViewById(R.id.sitting)
        sittingBar = findViewById(R.id.sitting_bar)
        sittingNote = findViewById(R.id.sitting_note)
        today = findViewById(R.id.today)
        limit = findViewById(R.id.limit)
        sendHome = findViewById(R.id.send_home)
        demo = findViewById(R.id.demo)

        keepClearOfSystemBars(findViewById(R.id.root))

        findViewById<View>(R.id.turn_on).setOnClickListener { openAccessibilitySettings() }
        findViewById<View>(R.id.open_app_info).setOnClickListener { openAppInfo() }
        findViewById<View>(R.id.limit_less).setOnClickListener { stepLimit(-1) }
        findViewById<View>(R.id.limit_more).setOnClickListener { stepLimit(+1) }
        sendHome.setOnCheckedChangeListener { _, checked -> store.sendHome = checked }
        demo.setOnClickListener {
            val service = Live.service
            if (service == null) toast(R.string.demo_needs_on) else service.startDemo()
        }
    }

    override fun onResume() {
        super.onResume()
        Live.onChange = ::render
        render()
    }

    override fun onPause() {
        Live.onChange = null
        Live.service?.endDemo()
        super.onPause()
    }

    private fun render() {
        val on = isSwitchedOn()
        status.setText(if (on) R.string.status_on else R.string.status_off)
        statusDetail.setText(if (on) R.string.status_on_detail else R.string.status_off_detail)
        offBlock.visibility = if (on) View.GONE else View.VISIBLE

        val clock = now()
        val state = Live.service?.snapshot ?: store.loadState()
        val limitMinutes = store.limitMinutes
        val sittingMs = state.sittingMs(clock, store.settings())
        sitting.text = resources.getQuantityString(
            R.plurals.sitting_value, limitMinutes, (sittingMs / 60_000).toInt(), limitMinutes,
        )
        sittingBar.progress = (sittingMs * 1000 / (limitMinutes * 60_000L)).toInt().coerceIn(0, 1000)
        val fresh = store.freshMinutes
        sittingNote.text = if (fresh % 60 == 0) {
            resources.getQuantityString(R.plurals.sitting_note_hours, fresh / 60, fresh / 60)
        } else {
            resources.getQuantityString(R.plurals.sitting_note_minutes, fresh, fresh)
        }

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

        limit.text = resources.getQuantityString(R.plurals.minutes, limitMinutes, limitMinutes)
        if (sendHome.isChecked != store.sendHome) sendHome.isChecked = store.sendHome
    }

    private fun stepLimit(direction: Int) {
        val steps = Store.LIMIT_STEPS
        val current = store.limitMinutes
        val next = if (direction > 0) steps.firstOrNull { it > current } else steps.lastOrNull { it < current }
        if (next != null) store.limitMinutes = next
        render()
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

    private fun toast(text: Int) = Toast.makeText(this, text, Toast.LENGTH_SHORT).show()

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
