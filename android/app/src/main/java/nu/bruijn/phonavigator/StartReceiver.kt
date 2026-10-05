package nu.bruijn.phonavigator

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Starts the service when the phone starts (if enabled), and again after an app update. */
class StartReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val start = when (intent.action) {
            Intent.ACTION_BOOT_COMPLETED -> NavService.startAtBoot(context)
            Intent.ACTION_MY_PACKAGE_REPLACED -> NavService.wantsRunning(context) // updates kill it
            else -> false
        }
        if (start) context.startForegroundService(Intent(context, NavService::class.java))
    }
}
