package nu.bruijn.phonavigator

import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothGattServer
import android.bluetooth.BluetoothGattServerCallback
import android.bluetooth.BluetoothGattService
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.le.AdvertiseCallback
import android.bluetooth.le.AdvertiseData
import android.bluetooth.le.AdvertiseSettings
import android.content.Intent
import android.content.pm.ServiceInfo
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.Looper
import android.os.ParcelUuid
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.math.sqrt

/**
 * Reads the orientation (quaternion) and sends it as a BLE notification to the tray app.
 * Deliberately dumb: calibration, deadzone and mapping all happen on the computer.
 * See PROTOCOL.md for the packet format.
 */
@SuppressLint("MissingPermission") // MainActivity requests the permissions before starting
class NavService : Service(), SensorEventListener {

    companion object {
        private const val TAG = "Phonavigator"
        private const val CHANNEL_ID = "nav"
        private const val NOTIF_ID = 1
        const val ACTION_STOP = "nu.bruijn.phonavigator.STOP"
        private const val SAMPLE_US = 20_000 // 50 Hz
        private const val LOG_LINES = 12

        @Volatile var running = false
            private set
        /** One-line summary for the notification and the big status label. */
        @Volatile var status = "Stopped"
            private set
        /** Multi-line details: advertising, links, subscribers, sensor rate. */
        @Volatile var details = ""
            private set
        private val log = ArrayDeque<String>()
        val logText: String get() = synchronized(log) { log.joinToString("\n") }

        /** Called on the main thread; MainActivity hooks in here. */
        var statusListener: (() -> Unit)? = null

        private val mainHandler = Handler(Looper.getMainLooper())
        private val timeFmt = SimpleDateFormat("HH:mm:ss", Locale.ROOT)

        fun event(msg: String) {
            Log.i(TAG, msg)
            synchronized(log) {
                log.addFirst("${timeFmt.format(Date())}  $msg")
                while (log.size > LOG_LINES) log.removeLast()
            }
        }
    }

    private lateinit var sensorManager: SensorManager
    private lateinit var bluetoothManager: BluetoothManager
    private var sensorThread: HandlerThread? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var gattServer: BluetoothGattServer? = null
    private var orientChar: BluetoothGattCharacteristic? = null
    private var usingMagnetometer = false

    // "off" | "starting" | "on" | "failed: ..."
    @Volatile private var advState = "off"

    // Notify coalescing: never more than one notification in flight per link,
    // only the newest packet matters (it is a state, not an event).
    private val lock = Any()
    private val subscribers = mutableSetOf<BluetoothDevice>()
    private val links = mutableSetOf<BluetoothDevice>()
    private var inFlight = 0
    private var inFlightSince = 0L
    private var pending: ByteArray? = null
    private var lastPacket = ByteArray(Protocol.PACKET_SIZE)
    private var seq = 0

    private var rateCount = 0
    private var rateSince = 0L
    @Volatile private var sensorHz = 0

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        if (running) return START_STICKY
        running = true
        synchronized(log) { log.clear() }
        event("Service started")

        startInForeground()
        sensorManager = getSystemService(SENSOR_SERVICE) as SensorManager
        bluetoothManager = getSystemService(BLUETOOTH_SERVICE) as BluetoothManager

        wakeLock = (getSystemService(POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "phonavigator:sensor")
            .apply { acquire() }

        if (!startSensor()) {
            event("No orientation sensor found")
            stopSelf()
            return START_NOT_STICKY
        }
        startGattServer()
        refreshStatus()
        return START_STICKY
    }

    override fun onDestroy() {
        running = false
        sensorManager.unregisterListener(this)
        sensorThread?.quitSafely()
        stopAdvertising()
        gattServer?.close()
        gattServer = null
        wakeLock?.let { if (it.isHeld) it.release() }
        event("Service stopped")
        status = "Stopped"
        details = ""
        mainHandler.post { statusListener?.invoke() }
        super.onDestroy()
    }

    // --- Sensor ---

    private fun startSensor(): Boolean {
        // GAME_ROTATION_VECTOR: gyro + accelerometer, no magnetometer (desk lamps, screws).
        val sensor = sensorManager.getDefaultSensor(Sensor.TYPE_GAME_ROTATION_VECTOR)
            ?: sensorManager.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR)?.also { usingMagnetometer = true }
            ?: return false
        event("Sensor: ${sensor.name}")
        val thread = HandlerThread("sensor").apply { start() }
        sensorThread = thread
        sensorManager.registerListener(this, sensor, SAMPLE_US, Handler(thread.looper))
        return true
    }

    override fun onSensorChanged(event: SensorEvent) {
        val v = event.values
        val x = v[0]; val y = v[1]; val z = v[2]
        val w = if (v.size >= 4) v[3] else sqrt((1f - x * x - y * y - z * z).coerceAtLeast(0f))
        var flags = 0
        if (usingMagnetometer) flags = flags or Protocol.FLAG_MAGNETOMETER

        val pkt = ByteBuffer.allocate(Protocol.PACKET_SIZE).order(ByteOrder.LITTLE_ENDIAN)
            .putShort((seq++ and 0xFFFF).toShort())
            .putFloat(x).putFloat(y).putFloat(z).putFloat(w)
            .put(flags.toByte())
            .array()

        synchronized(lock) {
            lastPacket = pkt
            pending = pkt
            // Safety net for a notification callback that never arrives.
            if (inFlight > 0 && SystemClock.elapsedRealtime() - inFlightSince > 500) inFlight = 0
            if (inFlight == 0) sendPendingLocked()
        }

        val now = SystemClock.elapsedRealtime()
        rateCount++
        if (now - rateSince >= 1000) {
            sensorHz = (rateCount * 1000 / (now - rateSince).coerceAtLeast(1)).toInt()
            rateCount = 0
            rateSince = now
            refreshStatus()
        }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}

    // --- BLE ---

    private fun startGattServer() {
        val adapter = bluetoothManager.adapter
        if (adapter == null || !adapter.isEnabled) {
            event("Bluetooth is off")
            advState = "failed: Bluetooth is off"
            return
        }
        val server = bluetoothManager.openGattServer(this, gattCallback) ?: run {
            event("Could not open GATT server")
            advState = "failed: no GATT server"
            return
        }
        gattServer = server

        val ch = BluetoothGattCharacteristic(
            Protocol.ORIENTATION_UUID,
            BluetoothGattCharacteristic.PROPERTY_NOTIFY or BluetoothGattCharacteristic.PROPERTY_READ,
            BluetoothGattCharacteristic.PERMISSION_READ,
        )
        ch.addDescriptor(
            BluetoothGattDescriptor(
                Protocol.CCCD_UUID,
                BluetoothGattDescriptor.PERMISSION_READ or BluetoothGattDescriptor.PERMISSION_WRITE,
            )
        )
        orientChar = ch
        val svc = BluetoothGattService(Protocol.SERVICE_UUID, BluetoothGattService.SERVICE_TYPE_PRIMARY)
        svc.addCharacteristic(ch)
        advState = "starting"
        server.addService(svc) // advertising starts in onServiceAdded
    }

    private fun startAdvertising() {
        val advertiser = bluetoothManager.adapter?.bluetoothLeAdvertiser ?: run {
            event("Phone cannot advertise over BLE")
            advState = "failed: not supported"
            return
        }
        val settings = AdvertiseSettings.Builder()
            .setAdvertiseMode(AdvertiseSettings.ADVERTISE_MODE_LOW_LATENCY)
            .setTxPowerLevel(AdvertiseSettings.ADVERTISE_TX_POWER_MEDIUM)
            .setConnectable(true)
            .setTimeout(0)
            .build()
        // Service data rather than (only) a UUID list: BlueZ ignores advertised UUIDs of a
        // bonded device whose services it already knows, but always updates service data.
        val data = AdvertiseData.Builder()
            .addServiceData(ParcelUuid(Protocol.SERVICE_UUID), byteArrayOf(Protocol.VERSION))
            .build()
        val scanResponse = AdvertiseData.Builder()
            .addServiceUuid(ParcelUuid(Protocol.SERVICE_UUID))
            .build()
        advState = "starting"
        advertiser.startAdvertising(settings, data, scanResponse, advertiseCallback)
    }

    private fun stopAdvertising() {
        if (advState == "on") {
            bluetoothManager.adapter?.bluetoothLeAdvertiser?.stopAdvertising(advertiseCallback)
        }
        advState = "off"
    }

    private val advertiseCallback = object : AdvertiseCallback() {
        override fun onStartSuccess(settingsInEffect: AdvertiseSettings) {
            advState = "on"
            event("Advertising started")
            refreshStatus()
        }

        override fun onStartFailure(errorCode: Int) {
            val reason = when (errorCode) {
                ADVERTISE_FAILED_DATA_TOO_LARGE -> "data too large"
                ADVERTISE_FAILED_TOO_MANY_ADVERTISERS -> "too many advertisers"
                ADVERTISE_FAILED_ALREADY_STARTED -> "already started"
                ADVERTISE_FAILED_INTERNAL_ERROR -> "internal error"
                ADVERTISE_FAILED_FEATURE_UNSUPPORTED -> "not supported"
                else -> "unknown"
            }
            event("Advertising failed: $reason ($errorCode)")
            advState = if (errorCode == ADVERTISE_FAILED_ALREADY_STARTED) "on" else "failed: $reason ($errorCode)"
            refreshStatus()
        }
    }

    private val gattCallback = object : BluetoothGattServerCallback() {
        override fun onServiceAdded(status: Int, service: BluetoothGattService) {
            if (status == BluetoothGatt.GATT_SUCCESS) {
                event("GATT service added")
                startAdvertising()
            } else {
                event("Adding GATT service failed ($status)")
                advState = "failed: GATT service ($status)"
                refreshStatus()
            }
        }

        override fun onConnectionStateChange(device: BluetoothDevice, status: Int, newState: Int) {
            // Android reports every Bluetooth link of the phone here (earbuds, watch, ...),
            // not only links to our service.
            val up = newState == BluetoothProfile.STATE_CONNECTED
            val wasSubscriber: Boolean
            synchronized(lock) {
                wasSubscriber = device in subscribers
                if (up) {
                    links += device
                } else {
                    links -= device
                    subscribers -= device
                    inFlight = 0
                }
            }
            event("Link ${if (up) "up" else "down"}: ${deviceLabel(device)}")
            // Some stacks stop advertising after a connection; restart once our computer leaves.
            if (!up && wasSubscriber && running) {
                stopAdvertising()
                startAdvertising()
            }
            refreshStatus()
        }

        override fun onDescriptorWriteRequest(
            device: BluetoothDevice, requestId: Int, descriptor: BluetoothGattDescriptor,
            preparedWrite: Boolean, responseNeeded: Boolean, offset: Int, value: ByteArray?,
        ) {
            if (descriptor.uuid == Protocol.CCCD_UUID) {
                val enable = value != null && value.isNotEmpty() && (value[0].toInt() and 0x01) != 0
                synchronized(lock) { if (enable) subscribers += device else subscribers -= device }
                event("${if (enable) "Subscribed" else "Unsubscribed"}: ${deviceLabel(device)}")
                refreshStatus()
            }
            if (responseNeeded) {
                gattServer?.sendResponse(device, requestId, BluetoothGatt.GATT_SUCCESS, offset, value)
            }
        }

        override fun onDescriptorReadRequest(
            device: BluetoothDevice, requestId: Int, offset: Int, descriptor: BluetoothGattDescriptor,
        ) {
            val on = synchronized(lock) { device in subscribers }
            val v = if (on) BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
            else BluetoothGattDescriptor.DISABLE_NOTIFICATION_VALUE
            gattServer?.sendResponse(device, requestId, BluetoothGatt.GATT_SUCCESS, 0, v)
        }

        override fun onCharacteristicReadRequest(
            device: BluetoothDevice, requestId: Int, offset: Int, characteristic: BluetoothGattCharacteristic,
        ) {
            val pkt = synchronized(lock) { lastPacket }
            val slice = if (offset < pkt.size) pkt.copyOfRange(offset, pkt.size) else ByteArray(0)
            gattServer?.sendResponse(device, requestId, BluetoothGatt.GATT_SUCCESS, offset, slice)
        }

        override fun onNotificationSent(device: BluetoothDevice, status: Int) {
            synchronized(lock) {
                if (inFlight > 0) inFlight--
                if (inFlight == 0) sendPendingLocked()
            }
        }
    }

    private fun deviceLabel(d: BluetoothDevice): String = try {
        d.name?.let { "$it (${d.address})" } ?: d.address
    } catch (_: SecurityException) {
        d.address
    }

    /** Call with [lock] held. */
    private fun sendPendingLocked() {
        val pkt = pending ?: return
        val server = gattServer ?: return
        val ch = orientChar ?: return
        if (subscribers.isEmpty()) return
        pending = null
        var sent = 0
        for (dev in subscribers) {
            val ok = if (Build.VERSION.SDK_INT >= 33) {
                server.notifyCharacteristicChanged(dev, ch, false, pkt) == BluetoothGatt.GATT_SUCCESS
            } else {
                @Suppress("DEPRECATION")
                ch.value = pkt
                @Suppress("DEPRECATION")
                server.notifyCharacteristicChanged(dev, ch, false)
            }
            if (ok) sent++ else Log.d(TAG, "notify to ${dev.address} failed")
        }
        inFlight = sent
        inFlightSince = SystemClock.elapsedRealtime()
    }

    // --- Status / notification ---

    private fun refreshStatus() {
        val (nLinks, nSubs) = synchronized(lock) { links.size to subscribers.size }
        val adv = advState
        val newStatus = when {
            nSubs > 0 -> "Streaming to computer"
            adv == "on" -> "Waiting for computer…"
            adv.startsWith("failed") -> "Not visible to computer"
            else -> "Starting…"
        }
        details = "Advertising: $adv\n" +
            "Bluetooth links (any device): $nLinks\n" +
            "Subscribed computers: $nSubs\n" +
            "Sensor: $sensorHz Hz" + if (usingMagnetometer) " (with magnetometer)" else ""
        val changed = newStatus != status
        status = newStatus
        mainHandler.post {
            statusListener?.invoke()
            if (changed && running) {
                getSystemService(NotificationManager::class.java).notify(NOTIF_ID, buildNotification(newStatus))
            }
        }
    }

    private fun startInForeground() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Phonavigator", NotificationManager.IMPORTANCE_LOW)
        )
        val n = buildNotification("Starting…")
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIF_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
        } else {
            startForeground(NOTIF_ID, n)
        }
    }

    private fun buildNotification(text: String): Notification {
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE,
        )
        val stop = PendingIntent.getService(
            this, 1, Intent(this, NavService::class.java).setAction(ACTION_STOP), PendingIntent.FLAG_IMMUTABLE,
        )
        return Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notif)
            .setContentTitle("Phonavigator")
            .setContentText(text)
            .setContentIntent(open)
            .setOngoing(true)
            .addAction(Notification.Action.Builder(null, "Stop", stop).build())
            .build()
    }
}

object Protocol {
    val SERVICE_UUID: java.util.UUID = java.util.UUID.fromString("7f3a0001-5c1e-4b8e-9d2a-6e0f1c9b4a10")
    val ORIENTATION_UUID: java.util.UUID = java.util.UUID.fromString("7f3a0002-5c1e-4b8e-9d2a-6e0f1c9b4a10")
    val CCCD_UUID: java.util.UUID = java.util.UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")
    const val PACKET_SIZE = 19 // fits the default ATT MTU (20 bytes payload)
    const val FLAG_MAGNETOMETER = 0x01
    const val VERSION: Byte = 1 // advertised as service data
}
