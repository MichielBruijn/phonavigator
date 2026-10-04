package nu.bruijn.phonavigator

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Restarts the service after an app update if it was running before (updates kill it). */
class UpdateReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_MY_PACKAGE_REPLACED && NavService.wantsRunning(context)) {
            context.startForegroundService(Intent(context, NavService::class.java))
        }
    }
}
