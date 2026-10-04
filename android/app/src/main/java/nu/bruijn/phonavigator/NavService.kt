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
import kotlin.math.sqrt

/**
 * Leest de oriëntatie (quaternion) en stuurt die als BLE-notify naar de tray-app.
 * Bewust dom: kalibratie, deadzone en mapping gebeuren allemaal op de computer.
 * Zie PROTOCOL.md voor het pakketformaat.
 */
@SuppressLint("MissingPermission") // MainActivity vraagt de permissies vóór het starten
class NavService : Service(), SensorEventListener {

    companion object {
        private const val TAG = "Phonavigator"
        private const val CHANNEL_ID = "nav"
        private const val NOTIF_ID = 1
        const val ACTION_STOP = "nu.bruijn.phonavigator.STOP"
        private const val SAMPLE_US = 20_000 // 50 Hz

        @Volatile var running = false
            private set
        @Volatile var status = "Gestopt"
            private set
        /** Wordt op de main thread aangeroepen; MainActivity hangt hier aan. */
        var statusListener: ((String) -> Unit)? = null

        private val mainHandler = Handler(Looper.getMainLooper())
    }

    private lateinit var sensorManager: SensorManager
    private lateinit var bluetoothManager: BluetoothManager
    private var sensorThread: HandlerThread? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var gattServer: BluetoothGattServer? = null
    private var orientChar: BluetoothGattCharacteristic? = null
    private var advertising = false
    private var usingMagnetometer = false

    // Notify-coalescing: nooit meer dan één notify per verbinding onderweg,
    // alleen het nieuwste pakket telt (het is een toestand, geen event).
    private val lock = Any()
    private val subscribers = mutableSetOf<BluetoothDevice>()
    private val connected = mutableSetOf<BluetoothDevice>()
    private var inFlight = 0
    private var inFlightSince = 0L
    private var pending: ByteArray? = null
    private var lastPacket = ByteArray(Protocol.PACKET_SIZE)
    private var seq = 0

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        if (running) return START_STICKY
        running = true

        startInForeground()
        sensorManager = getSystemService(SENSOR_SERVICE) as SensorManager
        bluetoothManager = getSystemService(BLUETOOTH_SERVICE) as BluetoothManager

        wakeLock = (getSystemService(POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "phonavigator:sensor")
            .apply { acquire() }

        if (!startSensor()) {
            setStatus("Geen oriëntatiesensor gevonden")
            stopSelf()
            return START_NOT_STICKY
        }
        startGattServer()
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
        setStatus("Gestopt")
        super.onDestroy()
    }

    // --- Sensor ---

    private fun startSensor(): Boolean {
        // GAME_ROTATION_VECTOR: gyro + accelerometer, geen magnetometer (bureaulampen, schroeven).
        val sensor = sensorManager.getDefaultSensor(Sensor.TYPE_GAME_ROTATION_VECTOR)
            ?: sensorManager.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR)?.also { usingMagnetometer = true }
            ?: return false
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
            // Vangnet voor een notify-callback die nooit komt.
            if (inFlight > 0 && SystemClock.elapsedRealtime() - inFlightSince > 500) inFlight = 0
            if (inFlight == 0) sendPendingLocked()
        }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}

    // --- BLE ---

    private fun startGattServer() {
        val adapter = bluetoothManager.adapter
        if (adapter == null || !adapter.isEnabled) {
            setStatus("Bluetooth staat uit")
            return
        }
        val server = bluetoothManager.openGattServer(this, gattCallback) ?: run {
            setStatus("GATT-server starten mislukt")
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
        server.addService(svc) // adverteren pas in onServiceAdded
    }

    private fun startAdvertising(withName: Boolean = true) {
        val advertiser = bluetoothManager.adapter?.bluetoothLeAdvertiser ?: run {
            setStatus("Telefoon kan niet adverteren via BLE")
            return
        }
        val settings = AdvertiseSettings.Builder()
            .setAdvertiseMode(AdvertiseSettings.ADVERTISE_MODE_LOW_LATENCY)
            .setTxPowerLevel(AdvertiseSettings.ADVERTISE_TX_POWER_MEDIUM)
            .setConnectable(true)
            .setTimeout(0)
            .build()
        val data = AdvertiseData.Builder()
            .addServiceUuid(ParcelUuid(Protocol.SERVICE_UUID))
            .build()
        val scanResponse = AdvertiseData.Builder().setIncludeDeviceName(withName).build()
        advertiser.startAdvertising(settings, data, scanResponse, advertiseCallback)
    }

    private fun stopAdvertising() {
        if (!advertising) return
        advertising = false
        bluetoothManager.adapter?.bluetoothLeAdvertiser?.stopAdvertising(advertiseCallback)
    }

    private val advertiseCallback = object : AdvertiseCallback() {
        override fun onStartSuccess(settingsInEffect: AdvertiseSettings) {
            advertising = true
            refreshStatus()
        }

        override fun onStartFailure(errorCode: Int) {
            if (errorCode == ADVERTISE_FAILED_DATA_TOO_LARGE) {
                // Lange toestelnaam past niet in de scan response: dan maar zonder naam.
                startAdvertising(withName = false)
            } else {
                setStatus("Adverteren mislukt (code $errorCode)")
            }
        }
    }

    private val gattCallback = object : BluetoothGattServerCallback() {
        override fun onServiceAdded(status: Int, service: BluetoothGattService) {
            if (status == BluetoothGatt.GATT_SUCCESS) startAdvertising()
            else setStatus("GATT-service toevoegen mislukt ($status)")
        }

        override fun onConnectionStateChange(device: BluetoothDevice, status: Int, newState: Int) {
            synchronized(lock) {
                if (newState == BluetoothProfile.STATE_CONNECTED) {
                    connected += device
                } else {
                    connected -= device
                    subscribers -= device
                    inFlight = 0
                }
            }
            if (newState != BluetoothProfile.STATE_CONNECTED && running) {
                // Sommige stacks stoppen met adverteren na een verbinding; herstart voor de zekerheid.
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

    /** Aanroepen met [lock] vast. */
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
            if (ok) sent++ else Log.d(TAG, "notify naar ${dev.address} mislukt")
        }
        inFlight = sent
        inFlightSince = SystemClock.elapsedRealtime()
    }

    // --- Status / notificatie ---

    private fun refreshStatus() {
        val (nConn, nSub) = synchronized(lock) { connected.size to subscribers.size }
        setStatus(
            when {
                nSub > 0 -> "Verbonden, stuurt data"
                nConn > 0 -> "Verbonden, wacht op abonnement"
                advertising -> "Wacht op computer…"
                else -> "Bezig met starten…"
            }
        )
    }

    private fun setStatus(s: String) {
        status = s
        Log.i(TAG, s)
        mainHandler.post {
            statusListener?.invoke(s)
            if (running) {
                getSystemService(NotificationManager::class.java).notify(NOTIF_ID, buildNotification(s))
            }
        }
    }

    private fun startInForeground() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Phonavigator", NotificationManager.IMPORTANCE_LOW)
        )
        val n = buildNotification("Bezig met starten…")
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
    const val PACKET_SIZE = 19 // past in de standaard ATT-MTU (20 bytes payload)
    const val FLAG_MAGNETOMETER = 0x01
}
