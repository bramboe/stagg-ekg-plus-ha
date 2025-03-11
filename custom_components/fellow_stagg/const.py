DOMAIN = "fellow_stagg"

# Primary service UUID for Fellow Stagg kettle
SERVICE_UUID = "021a9004-0382-4aea-bff4-6b3f1c5adfb4"

# Secondary service UUID (control service)
CONTROL_SERVICE_UUID = "7aebf330-6cb1-46e4-b23b-7cc2262c605e"

# Control service characteristic for notifications
CHAR_CONTROL_UUID = "2291c4b1-5d7f-4477-a88b-b266edb97142"

# Control service characteristic for writing commands
CHAR_WRITE_UUID = "2291c4b2-5d7f-4477-a88b-b266edb97142"

# Initialization sequence based on observed BLE patterns
INIT_SEQUENCE = [
    # First service characteristics
    {
        "uuid": "021aff50-0382-4aea-bff4-6b3f1c5adfb4",
        "data": bytearray([0xF7, 0x0A, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00])
    },
    {
        "uuid": "021aff51-0382-4aea-bff4-6b3f1c5adfb4",
        "data": bytearray([0xF7, 0x0B, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00])
    },
    # Control service characteristics
    {
        "uuid": "2291c4b1-5d7f-4477-a88b-b266edb97142",
        "data": bytearray([0xF7, 0x0C, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00])
    },
    {
        "uuid": "2291c4b2-5d7f-4477-a88b-b266edb97142",
        "data": bytearray([0xF7, 0x0D, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00])
    }
]
