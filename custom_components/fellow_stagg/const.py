DOMAIN = "fellow_stagg"

# BLE UUIDs for the Fellow Stagg kettle’s “Serial Port Service”
SERVICE_UUID = "B4DF5A1C-3F6B-F4BF-EA4A-82030490-1A02"
CHAR_UUID = "B4DF5A1C-3F6B-F4BF-EA4A-820350FF1A02"

# The authentication sequences (in hex) used to communicate with the EKG-2d-25-b0 kettle
AUTH_SEQUENCES = {
    "proto_ver": bytes.fromhex("455350"),
    "prov_session": bytes.fromhex("10015A25A20122A0A204A1C799EB1238CB23A45930C781AD2B12A8A343DCA08CA89EDF9032E03471F2D"),
    "prov_scan": [
        bytes.fromhex("F1CE12082DC8"),
        bytes.fromhex("24C1F300"),
        bytes.fromhex("97C13A480E67"),
        bytes.fromhex("5C894ABA1CEC1D91")
    ]
}
