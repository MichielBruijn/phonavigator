package nu.bruijn.phonavigator

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.view.Gravity
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView

class MainActivity : Activity() {

    private lateinit var statusView: TextView
    private lateinit var toggleButton: Button
    private lateinit var batteryButton: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val pad = (24 * resources.displayMetrics.density).toInt()

        statusView = TextView(this).apply { textSize = 18f; gravity = Gravity.CENTER }
        toggleButton = Button(this).apply { setOnClickListener { toggle() } }
        batteryButton = Button(this).apply {
            text = "Accubeperking uitzetten"
            setOnClickListener { requestBatteryExemption() }
        }
        val help = TextView(this).apply {
            textSize = 14f
            text = "Zet de telefoon rechtop op de kant van de laadpoort en kalibreer de " +
                "nulstand in de tray-app. Kantelen en draaien sturen het model; " +
                "plat neerleggen = pauze. Het scherm mag uit."
        }

        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad * 2, pad, pad)
            addView(TextView(context).apply { text = "Phonavigator"; textSize = 28f; gravity = Gravity.CENTER })
            addView(statusView, lp(pad))
            addView(toggleButton, lp(pad))
            addView(batteryButton, lp(pad / 2))
            addView(help, lp(pad))
        })
    }

    private fun lp(top: Int) = LinearLayout.LayoutParams(
        LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT,
    ).apply { topMargin = top }

    override fun onResume() {
        super.onResume()
        NavService.statusListener = { render() }
        render()
    }

    override fun onPause() {
        NavService.statusListener = null
        super.onPause()
    }

    private fun render() {
        statusView.text = NavService.status
        toggleButton.text = if (NavService.running) "Stoppen" else "Starten"
        batteryButton.isEnabled = !isBatteryExempt()
    }

    private fun toggle() {
        if (NavService.running) {
            startService(Intent(this, NavService::class.java).setAction(NavService.ACTION_STOP))
            return
        }
        val missing = requiredPermissions().filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }
        if (missing.isNotEmpty()) {
            requestPermissions(missing.toTypedArray(), 1)
        } else {
            startNav()
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        // Notificaties zijn optioneel; Bluetooth niet.
        val btOk = requiredPermissions()
            .filter { it != Manifest.permission.POST_NOTIFICATIONS }
            .all { checkSelfPermission(it) == PackageManager.PERMISSION_GRANTED }
        if (btOk) startNav() else statusView.text = "Zonder Bluetooth-toestemming gaat het niet"
    }

    private fun startNav() {
        startForegroundService(Intent(this, NavService::class.java))
        statusView.postDelayed({ render() }, 300)
    }

    private fun requiredPermissions(): List<String> = buildList {
        if (Build.VERSION.SDK_INT >= 31) {
            add(Manifest.permission.BLUETOOTH_ADVERTISE)
            add(Manifest.permission.BLUETOOTH_CONNECT)
        }
        if (Build.VERSION.SDK_INT >= 33) add(Manifest.permission.POST_NOTIFICATIONS)
    }

    private fun isBatteryExempt() =
        getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(packageName)

    @SuppressLint("BatteryLife") // sideload-app; Play-beleid niet van toepassing
    private fun requestBatteryExemption() {
        startActivity(
            Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName"))
        )
    }
}
