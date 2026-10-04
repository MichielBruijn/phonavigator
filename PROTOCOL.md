# Phonavigator BLE-protocol (v1)

De telefoon is GATT-**server** (peripheral); de computer is central en abonneert
zich op notificaties.

Advertentie: **service data** onder de service-UUID (1 byte: protocolversie, nu 1);
de scan response bevat de service-UUID als UUID-lijst. Zoek op beide: BlueZ negeert
geadverteerde UUID-lijsten van een gekoppeld apparaat waarvan de services al bekend
zijn, maar werkt service data altijd bij.

| | UUID |
|---|---|
| Service | `7f3a0001-5c1e-4b8e-9d2a-6e0f1c9b4a10` |
| Oriëntatie (notify, read) | `7f3a0002-5c1e-4b8e-9d2a-6e0f1c9b4a10` |

## Oriëntatiepakket — 19 bytes, little-endian

| Offset | Type | Veld |
|---|---|---|
| 0 | u16 | volgnummer (wrapt) |
| 2 | f32 ×4 | quaternion x, y, z, w |
| 18 | u8 | flags: bit 0 = magnetometer gebruikt |

De quaternion roteert telefooncoördinaten naar wereldcoördinaten (Android
`GAME_ROTATION_VECTOR`-conventie): telefoon-X naar rechts, Y omhoog langs het
scherm, Z uit het scherm; wereld-Z omhoog. Yaw heeft een willekeurige
referentie en mag driften.

Het pakket is een **toestand**, geen event: alleen het nieuwste telt, gemiste
pakketten zijn geen probleem. Zender stuurt ~50 Hz; past in de standaard
ATT-MTU, dus geen MTU-onderhandeling nodig.

Een andere zender (bijv. iOS) hoeft alleen dit te implementeren.
